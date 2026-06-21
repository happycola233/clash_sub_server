"""订阅源加载器。

这里负责所有 source 相关 IO：
- 远程 URL 用 requests 拉取，可显式走 Mihomo/Clash 代理。
- 本地路径从配置文件所在目录解析。
- 多源并发拉取，但合并顺序仍保持配置顺序。
"""

from __future__ import annotations

import logging
import os
import time
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
import yaml

from .errors import AllSourcesFailedError, SourceError
from .merger import merge_proxy_lists
from .models import AppConfig, ProxySettings, SourceEntry, SourceRequestSettings
from .parser import parse_source_yaml, validate_proxy_list

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SourceLoadResult:
    source_config: dict[str, Any]
    remote_userinfo: str | None


@dataclass(frozen=True)
class FailedSource:
    ref: str
    tag: str
    error: str


@dataclass(frozen=True)
class MergedSourceResult:
    source_config: dict[str, Any]
    remote_userinfos: list[str]
    successful_sources: list[str]
    failed_sources: list[FailedSource]


def format_source_refs(source_entries: tuple[SourceEntry, ...]) -> str | list[str]:
    """生成日志里的 source 描述。"""

    source_refs = [entry.ref for entry in source_entries]
    if len(source_refs) == 1:
        return source_refs[0]
    return source_refs


def load_merged_source_config(source_entries: tuple[SourceEntry, ...], config: AppConfig) -> MergedSourceResult:
    """加载一个或多个 source，并返回合并后的 proxies。"""

    if len(source_entries) == 1:
        entry = source_entries[0]
        result = _load_single_source_with_deadline(entry, config)
        remote_userinfos = [result.remote_userinfo] if result.remote_userinfo else []
        return MergedSourceResult(result.source_config, remote_userinfos, [entry.ref], [])

    return _load_multi_source_config(source_entries, config)


def _load_single_source_with_deadline(source_entry: SourceEntry, config: AppConfig) -> SourceLoadResult:
    """单源也使用 overall_timeout，避免慢连接无限拖住用户请求。"""

    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="source-fetch")
    future = executor.submit(load_source_config, source_entry, config)
    try:
        done, _ = wait({future}, timeout=config.source_request.overall_timeout)
        if not done:
            future.cancel()
            raise SourceError(f"订阅源加载超过 overall_timeout={config.source_request.overall_timeout}s")
        return future.result()
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def load_source_config(source_entry: SourceEntry, config: AppConfig) -> SourceLoadResult:
    """按 source 类型加载单个订阅源。"""

    source_ref = source_entry.ref.strip()
    if not source_ref:
        raise SourceError("订阅源为空。")

    lower_ref = source_ref.lower()
    is_remote_source = lower_ref.startswith("http://") or lower_ref.startswith("https://")

    try:
        if is_remote_source:
            return _load_remote_source(source_ref, config.source_request, config.proxy)
        return _load_local_source(source_ref, config.base_dir)
    except requests.RequestException as exc:
        raise SourceError(str(exc)) from exc
    except yaml.YAMLError as exc:
        raise SourceError(str(exc)) from exc
    except OSError as exc:
        raise SourceError(str(exc)) from exc


def _load_remote_source(
    source_ref: str,
    request_settings: SourceRequestSettings,
    proxy_settings: ProxySettings,
) -> SourceLoadResult:
    proxies = _build_requests_proxies(source_ref, proxy_settings)
    logger.info(
        "开始拉取远程订阅源: source=%s connect_timeout=%ss read_timeout=%ss proxy=%s",
        source_ref,
        request_settings.connect_timeout,
        request_settings.read_timeout,
        "enabled" if proxies else "disabled",
    )

    headers = {"User-Agent": request_settings.user_agent}
    start_time = time.time()
    source_resp = requests.get(
        source_ref,
        headers=headers,
        timeout=(request_settings.connect_timeout, request_settings.read_timeout),
        proxies=proxies,
    )
    source_resp.raise_for_status()

    source_config, parse_mode = parse_source_yaml(source_resp.text, source_ref)
    remote_userinfo = source_resp.headers.get("Subscription-Userinfo")
    elapsed_ms = int((time.time() - start_time) * 1000)
    proxies = source_config.get("proxies")
    proxy_count = len(proxies) if isinstance(proxies, list) else 0
    logger.info(
        "远程订阅源拉取成功: source=%s 状态码=%s 耗时=%sms 响应字节=%s 包含Subscription-Userinfo=%s 解析模式=%s 节点数量=%s",
        source_ref,
        source_resp.status_code,
        elapsed_ms,
        len(source_resp.content),
        bool(remote_userinfo),
        parse_mode,
        proxy_count,
    )
    return SourceLoadResult(source_config=source_config, remote_userinfo=remote_userinfo)


def _load_local_source(source_ref: str, base_dir: Path) -> SourceLoadResult:
    local_path = os.path.expandvars(os.path.expanduser(source_ref))
    path = Path(local_path)
    if not path.is_absolute():
        path = base_dir / path
    path = path.resolve()

    logger.info("开始读取本地订阅源: source=%s 绝对路径=%s", source_ref, path)
    source_text = path.read_text(encoding="utf-8")
    source_config, parse_mode = parse_source_yaml(source_text, source_ref)
    logger.info("本地订阅源读取成功: 绝对路径=%s 解析模式=%s", path, parse_mode)
    return SourceLoadResult(source_config=source_config, remote_userinfo=None)


def _load_multi_source_config(source_entries: tuple[SourceEntry, ...], config: AppConfig) -> MergedSourceResult:
    max_workers = min(len(source_entries), config.source_request.max_workers)
    executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="source-fetch")
    future_map: dict[Future[SourceLoadResult], tuple[int, SourceEntry]] = {}

    try:
        for index, entry in enumerate(source_entries):
            future = executor.submit(load_source_config, entry, config)
            future_map[future] = (index, entry)

        done, not_done = wait(future_map, timeout=config.source_request.overall_timeout)

        results_by_index: dict[int, SourceLoadResult] = {}
        failed_by_index: dict[int, FailedSource] = {}

        for future in done:
            index, entry = future_map[future]
            try:
                results_by_index[index] = future.result()
            except Exception as exc:  # noqa: BLE001 - 多源需要把任意失败转成失败源记录
                logger.warning("多源订阅源加载失败: source=%s tag=%s error=%s", entry.ref, entry.tag, exc)
                failed_by_index[index] = FailedSource(ref=entry.ref, tag=entry.tag, error=str(exc))

        for future in not_done:
            future.cancel()
            index, entry = future_map[future]
            error = f"订阅源加载超过 overall_timeout={config.source_request.overall_timeout}s"
            logger.warning("多源订阅源加载超时: source=%s tag=%s error=%s", entry.ref, entry.tag, error)
            failed_by_index[index] = FailedSource(ref=entry.ref, tag=entry.tag, error=error)
    finally:
        # wait=False 避免某个慢源拖住当前用户请求；线程会在 requests 超时后自然结束。
        executor.shutdown(wait=False, cancel_futures=True)

    loaded_proxy_lists: list[tuple[str, list[dict[str, Any]]]] = []
    remote_userinfos: list[str] = []
    successful_sources: list[str] = []
    failed_sources: list[FailedSource] = []

    for index, entry in enumerate(source_entries):
        if index in results_by_index:
            result = results_by_index[index]
            proxies = validate_proxy_list(result.source_config.get("proxies", []))
            loaded_proxy_lists.append((entry.tag, proxies))
            successful_sources.append(entry.ref)
            if result.remote_userinfo:
                remote_userinfos.append(result.remote_userinfo)
        elif index in failed_by_index:
            failed_sources.append(failed_by_index[index])

    if not successful_sources:
        failed_refs = [item.ref for item in failed_sources]
        raise AllSourcesFailedError(f"所有订阅源加载失败: {failed_refs}")

    if failed_sources and not config.source_request.allow_partial_failure:
        failed_refs = [item.ref for item in failed_sources]
        raise SourceError(f"部分订阅源加载失败: {failed_refs}")

    merged_proxies = merge_proxy_lists(loaded_proxy_lists)
    return MergedSourceResult(
        source_config={"proxies": merged_proxies},
        remote_userinfos=remote_userinfos,
        successful_sources=successful_sources,
        failed_sources=failed_sources,
    )


def _build_requests_proxies(source_ref: str, proxy_settings: ProxySettings) -> dict[str, str] | None:
    if not proxy_settings.enabled:
        return None
    if _matches_no_proxy(source_ref, proxy_settings.no_proxy):
        return None

    proxies: dict[str, str] = {}
    if proxy_settings.http:
        proxies["http"] = proxy_settings.http
    if proxy_settings.https:
        proxies["https"] = proxy_settings.https
    elif proxy_settings.http:
        proxies["https"] = proxy_settings.http

    return proxies or None


def _matches_no_proxy(source_ref: str, no_proxy: tuple[str, ...]) -> bool:
    hostname = urlparse(source_ref).hostname
    if not hostname:
        return False
    hostname = hostname.lower()

    for raw_pattern in no_proxy:
        pattern = raw_pattern.strip().lower()
        if not pattern:
            continue
        if pattern == "*":
            return True
        if pattern.startswith("*."):
            suffix = pattern[1:]
            if hostname.endswith(suffix):
                return True
        elif pattern.startswith("."):
            if hostname.endswith(pattern):
                return True
        elif hostname == pattern:
            return True
    return False
