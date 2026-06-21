"""Subscription-Userinfo 响应头处理。"""

from __future__ import annotations

from .models import SourceEntry


def parse_subscription_userinfo(userinfo_value: str) -> dict[str, int]:
    """解析机场常见的 Subscription-Userinfo 头。"""

    result: dict[str, int] = {}
    for item in userinfo_value.split(";"):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key not in ("upload", "download", "total", "expire"):
            continue
        try:
            result[key] = int(value)
        except ValueError:
            continue

    required_keys = ("upload", "download", "total", "expire")
    for key in required_keys:
        if key not in result:
            raise ValueError(f"Subscription-Userinfo 缺少字段: {key}")
    return result


def aggregate_subscription_userinfos(userinfo_values: list[str]) -> str | None:
    """多源 source 模式下聚合多个源站返回的流量头。"""

    if not userinfo_values:
        return None

    upload = 0
    download = 0
    total = 0
    expire_values = []

    for userinfo_value in userinfo_values:
        parsed = parse_subscription_userinfo(userinfo_value)
        upload += parsed["upload"]
        download += parsed["download"]
        total += parsed["total"]
        expire_values.append(parsed["expire"])

    expire = min(expire_values) if expire_values else 0
    return f"upload={upload}; download={download}; total={total}; expire={expire}"


def choose_response_userinfo(
    userinfo_mode: str,
    custom_userinfo_value: str | None,
    source_entries: tuple[SourceEntry, ...],
    remote_userinfos: list[str],
) -> str | None:
    """根据 token 配置选择最终响应头。"""

    if userinfo_mode == "custom":
        return custom_userinfo_value

    if len(source_entries) == 1:
        return remote_userinfos[0] if remote_userinfos else None

    return aggregate_subscription_userinfos(remote_userinfos)


def describe_userinfo_header_mode(
    userinfo_mode: str,
    source_entries: tuple[SourceEntry, ...],
    response_userinfo: str | None,
) -> str:
    """生成日志里使用的流量头模式描述。"""

    if userinfo_mode == "custom":
        return "custom"
    if not response_userinfo:
        return "none"
    if len(source_entries) == 1:
        return "source"
    return "source-merged"
