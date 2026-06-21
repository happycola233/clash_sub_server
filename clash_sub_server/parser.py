"""上游订阅 YAML 解析。

优先只解析顶层 proxies，而不是把整个上游 YAML 全量 safe_load。
这里保留这个策略；如果遇到不兼容写法，再回退到全量 YAML 解析。
"""

from __future__ import annotations

import logging
from typing import Any

import yaml

from .errors import SubscriptionFormatError

logger = logging.getLogger(__name__)


def extract_top_level_proxies(yaml_text: str) -> list[dict[str, Any]]:
    """从 YAML 文本中仅提取顶层 proxies 列表。"""

    lines = yaml_text.splitlines()

    for idx, raw_line in enumerate(lines):
        line = raw_line.lstrip("\ufeff")
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if ":" not in stripped:
            continue

        indent = len(line) - len(line.lstrip(" "))
        if indent != 0:
            continue

        key, value_part = stripped.split(":", 1)
        if key.strip() != "proxies":
            continue

        value_part = value_part.strip()
        if value_part and not value_part.startswith("#"):
            parsed = yaml.safe_load(f"proxies: {value_part}")
        else:
            block_lines = ["proxies:"]
            for next_raw_line in lines[idx + 1 :]:
                next_stripped = next_raw_line.strip()
                if not next_stripped:
                    block_lines.append(next_raw_line)
                    continue

                next_lstrip = next_raw_line.lstrip(" ")
                next_indent = len(next_raw_line) - len(next_lstrip)

                if next_lstrip.startswith("#"):
                    block_lines.append(next_raw_line)
                    continue

                if next_indent < indent:
                    break

                # 支持 "proxies:\n- name: xxx" 这种顶格序列。
                if next_indent == indent and not next_lstrip.startswith("-"):
                    break

                block_lines.append(next_raw_line)

            parsed = yaml.safe_load("\n".join(block_lines))

        if not isinstance(parsed, dict):
            raise SubscriptionFormatError("顶层 proxies 解析结果不是对象。")

        proxies = parsed.get("proxies")
        if not isinstance(proxies, list):
            raise SubscriptionFormatError("顶层 proxies 不是列表。")
        return proxies

    raise SubscriptionFormatError("未找到顶层 proxies。")


def parse_source_yaml(source_text: str, source_ref: str) -> tuple[dict[str, Any], str]:
    """解析订阅源 YAML，返回配置对象和解析模式。"""

    try:
        proxies = extract_top_level_proxies(source_text)
        logger.info("顶层 proxies 解析成功: source_ref=%s 节点数量=%s", source_ref, len(proxies))
        return {"proxies": proxies}, "top_level_proxies_only"
    except Exception as top_level_err:  # noqa: BLE001 - 回退解析需要捕获所有解析异常
        logger.info("顶层 proxies 解析失败，回退到全量解析: source_ref=%s 原因=%s", source_ref, top_level_err)
        parsed = yaml.safe_load(source_text)
        if not isinstance(parsed, dict):
            raise SubscriptionFormatError("订阅源格式错误：YAML 根节点必须是对象。")
        return parsed, "full_yaml_fallback"


def validate_proxy_list(proxies: Any) -> list[dict[str, Any]]:
    """校验 proxies 是否为带 name 字段的节点列表。"""

    if not isinstance(proxies, list):
        raise SubscriptionFormatError("订阅源格式错误: proxies 必须是列表。")
    for proxy in proxies:
        if not isinstance(proxy, dict) or "name" not in proxy:
            raise SubscriptionFormatError("订阅源格式错误: proxies 节点缺少 name 字段。")
    return proxies
