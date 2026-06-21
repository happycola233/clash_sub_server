"""多源节点合并逻辑。"""

from __future__ import annotations

import copy
from typing import Any

from .parser import validate_proxy_list


def build_unique_proxy_name(original_name: str, source_tag: str, existing_names: set[str]) -> str:
    """遇到重名节点时追加稳定来源标签。"""

    if original_name not in existing_names:
        return original_name

    candidate = f"{original_name} [{source_tag}]"
    if candidate not in existing_names:
        return candidate

    index = 2
    while True:
        candidate = f"{original_name} [{source_tag} #{index}]"
        if candidate not in existing_names:
            return candidate
        index += 1


def merge_proxy_lists(source_proxy_lists: list[tuple[str, list[dict[str, Any]]]]) -> list[dict[str, Any]]:
    """按配置顺序合并多个订阅源的节点。"""

    merged_proxies: list[dict[str, Any]] = []
    existing_names: set[str] = set()

    for source_tag, proxies in source_proxy_lists:
        validate_proxy_list(proxies)
        for proxy in proxies:
            merged_proxy = copy.deepcopy(proxy)
            original_name = str(merged_proxy["name"])
            unique_name = build_unique_proxy_name(original_name, source_tag, existing_names)
            merged_proxy["name"] = unique_name
            existing_names.add(unique_name)
            merged_proxies.append(merged_proxy)

    return merged_proxies
