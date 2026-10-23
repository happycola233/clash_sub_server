"""从选定的 MetaCubeX classical 名单生成商业规则；失败时保留原文件。"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import ipaddress
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import quote

import requests
import yaml

from .renderer import build_final_config
from .fetcher import _build_requests_proxies
from .models import ProxySettings


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DOMAIN_TYPES = {"DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD", "DOMAIN-REGEX"}
IP_TYPES = {"IP-CIDR", "IP-CIDR6"}


class UpdateError(ValueError):
    """名单、来源或下载不符合要求，不能替换现有文件。"""


@dataclass(frozen=True)
class RuleSet:
    name: str
    policy: str
    files: tuple[str, ...]
    local_files: tuple[str, ...]


@dataclass(frozen=True)
class Manifest:
    repository: str
    ref: str
    directory: Path
    rulesets: tuple[RuleSet, ...]


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or any(c in value for c in "\r\n,"):
        raise UpdateError(f"{field} 必须是非空的单行字符串，不能包含逗号")
    return value.strip()


def _paths(value: object, field: str, *, local: bool) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise UpdateError(f"{field} 必须是文件列表")
    result = []
    for item in value:
        path = _text(item, field)
        if local:
            valid = re.fullmatch(r"[\w.-]+\.list", path) is not None
        else:
            valid = re.fullmatch(r"geo/(geosite|geoip)/classical/[\w@!+.-]+\.list", path) is not None
        if not valid:
            raise UpdateError(f"{field} 中的名单路径不正确: {path}")
        result.append(path)
    return tuple(result)


def load_manifest(path: Path) -> Manifest:
    data = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or not isinstance(data.get("upstream"), dict):
        raise UpdateError("来源配置缺少 upstream")
    repository = _text(data["upstream"].get("repository"), "upstream.repository")
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repository):
        raise UpdateError("upstream.repository 必须是 GitHub 的 owner/repo")
    ref = _text(data["upstream"].get("ref"), "upstream.ref")
    entries = data.get("rulesets")
    if not isinstance(entries, list) or not entries:
        raise UpdateError("rulesets 必须是非空列表")
    policies = {group["name"] for group in build_final_config([], ())["proxy-groups"]} | {"DIRECT", "REJECT"}
    rulesets = []
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise UpdateError(f"rulesets[{index}] 必须是对象")
        policy = _text(entry.get("policy"), f"rulesets[{index}].policy")
        if policy not in policies:
            raise UpdateError(f"策略组不存在: {policy}")
        name = _text(entry.get("name", policy), f"rulesets[{index}].name")
        files = _paths(entry.get("files", []), "files", local=False)
        local_files = _paths(entry.get("local_files", []), "local_files", local=True)
        if not files and not local_files:
            raise UpdateError(f"{name} 没有选择任何名单")
        rulesets.append(RuleSet(name, policy, files, local_files))
    return Manifest(repository, ref, path.resolve().parent, tuple(rulesets))


class Upstream:
    def __init__(
        self, repository: str, *, local_repo: Path | None = None, proxy: str | None = None,
        proxy_settings: ProxySettings | None = None,
    ):
        self.repository = repository
        self.local_repo = local_repo
        self.proxies = {"http": proxy, "https": proxy} if proxy else None
        self.proxy_settings = proxy_settings

    def _get(self, url: str) -> requests.Response:
        proxies = _build_requests_proxies(url, self.proxy_settings) if self.proxy_settings is not None else self.proxies
        response = requests.get(
            url,
            timeout=(5, 30),
            proxies=proxies,
            headers={"User-Agent": "clash-sub-server-rule-updater"},
        )
        response.raise_for_status()
        return response

    def _git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(self.local_repo), *args],
            capture_output=True, text=True, encoding="utf-8", check=True,
        )
        return result.stdout.strip()

    def resolve(self, ref: str) -> str:
        if self.local_repo is not None:
            for candidate in (ref, f"origin/{ref}"):
                try:
                    return self._git("rev-parse", "--verify", "--end-of-options", f"{candidate}^{{commit}}")
                except subprocess.CalledProcessError:
                    continue
            raise UpdateError(f"本地上游仓库中找不到版本: {ref}")
        if re.fullmatch(r"[0-9a-f]{40}", ref):
            return ref
        data = self._get(f"https://api.github.com/repos/{self.repository}/commits/{quote(ref, safe='')}").json()
        revision = data.get("sha", "")
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise UpdateError("GitHub 没有返回有效的上游版本")
        return revision

    def read(self, revision: str, path: str) -> str:
        if self.local_repo is not None:
            return self._git("show", f"{revision}:{path}")
        url = f"https://raw.githubusercontent.com/{self.repository}/{revision}/{quote(path, safe='/')}"
        return self._get(url).content.decode("utf-8-sig")


def parse_rules(text: str, source: str) -> list[str]:
    """只接受无策略的 classical 条件，避免错误页或带策略的文件混入结果。"""
    rules = []
    for number, raw in enumerate(text.lstrip("\ufeff").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        rule_type, separator, value = line.partition(",")
        value = value.strip()
        prefix = f"{source}:{number}"
        if not separator or not value or rule_type not in DOMAIN_TYPES | IP_TYPES:
            raise UpdateError(f"{prefix}: 不支持的 classical 规则")
        if rule_type in IP_TYPES:
            parts = value.split(",")
            if len(parts) > 2 or (len(parts) == 2 and parts[1] != "no-resolve"):
                raise UpdateError(f"{prefix}: IP 规则不能带策略或未知选项")
            try:
                network = ipaddress.ip_network(parts[0], strict=False)
            except ValueError as exc:
                raise UpdateError(f"{prefix}: IP 网段无效") from exc
            if rule_type == "IP-CIDR6" and network.version != 6:
                raise UpdateError(f"{prefix}: IP-CIDR6 必须使用 IPv6")
            rule_type = "IP-CIDR6" if network.version == 6 else "IP-CIDR"
            value = str(network)
        elif "," in value or any(c.isspace() for c in value):
            raise UpdateError(f"{prefix}: 域名规则包含多余字段或空白")
        rules.append(f"{rule_type},{value}")
    if not rules:
        raise UpdateError(f"名单为空: {source}")
    return rules


def generate(manifest: Manifest, upstream: Upstream, revision: str) -> tuple[str, int, int]:
    paths = list(dict.fromkeys(path for group in manifest.rulesets for path in group.files))
    # 固定同一个 commit 下载全部文件，避免更新途中混入不同版本。
    with ThreadPoolExecutor(max_workers=6) as pool:
        contents = dict(zip(paths, pool.map(lambda path: upstream.read(revision, path), paths)))
    local_paths = {path for group in manifest.rulesets for path in group.local_files}
    for path in local_paths:
        contents[f"local:{path}"] = (manifest.directory / path).read_text(encoding="utf-8-sig")

    lines = [
        "# 自动生成：来源配置见 rules/sources.yaml；订阅服务负责自动更新。",
        f"# 上游：https://github.com/{manifest.repository}/tree/{revision}",
        "# 按来源清单顺序匹配；相同条件保留首次出现的策略；IP 规则不额外触发 DNS 解析。",
    ]
    seen: set[str] = set()
    duplicates = 0
    for group in manifest.rulesets:
        lines.extend(["", f"# {group.name}"])
        for source in [*group.files, *(f"local:{path}" for path in group.local_files)]:
            lines.append(f"# 来源：{source}")
            for rule in parse_rules(contents[source], source):
                if rule in seen:
                    duplicates += 1
                    continue
                seen.add(rule)
                suffix = ",no-resolve" if rule.split(",", 1)[0] in IP_TYPES else ""
                lines.append(f"{rule},{group.policy}{suffix}")
    return "\n".join(lines) + "\n", len(seen), duplicates


def replace_output(path: Path, text: str) -> bool:
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        # 定时任务可能以另一用户运行，不能让替换后的名单失去原来的读取权限。
        os.chmod(temporary, path.stat().st_mode & 0o777 if path.exists() else 0o644)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=PROJECT_ROOT / "rules" / "sources.yaml")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "rules" / "commercial_rules.list")
    parser.add_argument("--ref", help="指定上游分支或 commit，默认使用 sources.yaml 中的 ref")
    parser.add_argument("--local-repo", type=Path, help="从本地上游 Git 仓库读取，不修改其分支或工作区")
    parser.add_argument("--proxy", help="下载代理，例如 http://127.0.0.1:7890；也支持 HTTPS_PROXY 环境变量")
    parser.add_argument("--check", action="store_true", help="只检查；有更新时返回 1，不写文件")
    args = parser.parse_args(argv)
    try:
        manifest = load_manifest(args.manifest)
        upstream = Upstream(manifest.repository, local_repo=args.local_repo, proxy=args.proxy)
        revision = upstream.resolve(args.ref or manifest.ref)
        text, count, duplicates = generate(manifest, upstream, revision)
        if args.check:
            current = args.output.read_text(encoding="utf-8") if args.output.exists() else ""
            print(f"{'UP TO DATE' if current == text else 'UPDATE AVAILABLE'}: {count} rules, {duplicates} duplicates")
            return 0 if current == text else 1
        changed = replace_output(args.output, text)
        print(f"{'UPDATED' if changed else 'UNCHANGED'}: {count} rules, {duplicates} duplicates; upstream {revision}")
        return 0
    except (OSError, ValueError, yaml.YAMLError, requests.RequestException, subprocess.CalledProcessError) as exc:
        print(f"UPDATE FAILED; existing rules kept: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
