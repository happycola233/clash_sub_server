"""配置加载、校验与热重载。

设计原则：
1. 配置文件出错时，不覆盖上一份已经生效的配置。
2. 热重载只在请求进入时做轻量 mtime 检查，不额外起后台线程。
3. 规则文件作为配置的一部分一起纳入热重载。
"""

from __future__ import annotations

import os
import threading
import logging
from pathlib import Path
from typing import Any, Mapping

import yaml

from .errors import ConfigError
from .models import (
    AppConfig,
    HotReloadSettings,
    ProxySettings,
    ResponseSettings,
    RouteSettings,
    RulesSettings,
    ServerSettings,
    SourceEntry,
    SourceRequestSettings,
    TokenConfig,
)

logger = logging.getLogger(__name__)


def default_config_path() -> Path:
    """返回默认配置文件路径。

    可以通过环境变量 CLASH_SUB_CONFIG 指定配置文件；否则使用项目根目录的
    config.yaml。
    """

    configured = os.environ.get("CLASH_SUB_CONFIG")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[1] / "config.yaml"


class ConfigManager:
    """带热重载能力的配置管理器。"""

    def __init__(self, config_path: Path | str | None = None):
        self.config_path = Path(config_path or default_config_path()).expanduser().resolve()
        self._lock = threading.RLock()
        self._config: AppConfig | None = None
        self._signature: tuple[tuple[str, int, int], ...] | None = None
        self._last_error: Exception | None = None

    @property
    def last_error(self) -> Exception | None:
        return self._last_error

    def get_config(self) -> AppConfig:
        """获取当前有效配置，必要时自动热重载。"""

        with self._lock:
            if self._config is None:
                return self._load_or_raise()

            if not self._config.hot_reload.enabled:
                return self._config

            current_signature = self._build_signature(self._config)
            if current_signature == self._signature:
                return self._config

            try:
                return self._load_or_raise()
            except ConfigError as exc:
                self._last_error = exc
                logger.exception("配置热重载失败，继续使用上一份有效配置: %s", exc)
                # 热重载失败时保持当前有效配置继续服务。
                return self._config

    def reload_now(self) -> AppConfig:
        """强制重新加载配置，主要供测试或管理脚本使用。"""

        with self._lock:
            return self._load_or_raise()

    def _load_or_raise(self) -> AppConfig:
        try:
            config = load_config(self.config_path)
        except Exception as exc:  # noqa: BLE001 - 这里统一转成配置错误
            error = exc if isinstance(exc, ConfigError) else ConfigError(str(exc))
            self._last_error = error
            if self._config is None:
                raise error
            raise error

        self._config = config
        self._signature = self._build_signature(config)
        self._last_error = None
        return config

    def _build_signature(self, config: AppConfig) -> tuple[tuple[str, int, int], ...]:
        paths = [self.config_path, config.rules.commercial_rules_path]
        return tuple(_file_signature(path) for path in paths)


def load_config(config_path: Path | str) -> AppConfig:
    """从 YAML 文件加载完整配置。"""

    path = Path(config_path).expanduser().resolve()
    if not path.exists():
        raise ConfigError(f"配置文件不存在: {path}")

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"配置文件 YAML 解析失败: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"读取配置文件失败: {exc}") from exc

    root = _as_mapping(raw, "配置根节点")
    base_dir = path.parent

    rules_settings = _parse_rules(root.get("rules"), base_dir)
    commercial_rules = _load_commercial_rules(rules_settings.commercial_rules_path)

    return AppConfig(
        base_dir=base_dir,
        server=_parse_server(root.get("server")),
        routes=_parse_routes(root.get("routes")),
        hot_reload=_parse_hot_reload(root.get("hot_reload")),
        source_request=_parse_source_request(root.get("source_request")),
        proxy=_parse_proxy(root.get("proxy")),
        response=_parse_response(root.get("response")),
        rules=rules_settings,
        tokens=_parse_tokens(root.get("tokens")),
        commercial_rules=commercial_rules,
    )


def _parse_server(value: Any) -> ServerSettings:
    data = _optional_mapping(value, "server")
    headers = data.get("trusted_proxy_headers", ServerSettings.trusted_proxy_headers)
    return ServerSettings(
        host=_as_str(data.get("host", "127.0.0.1"), "server.host"),
        port=_as_int(data.get("port", 2233), "server.port"),
        trusted_proxy=_as_str(data.get("trusted_proxy", "127.0.0.1"), "server.trusted_proxy"),
        trusted_proxy_count=_as_int(data.get("trusted_proxy_count", 1), "server.trusted_proxy_count"),
        trusted_proxy_headers=tuple(_as_str_list(headers, "server.trusted_proxy_headers")),
    )


def _parse_routes(value: Any) -> RouteSettings:
    data = _optional_mapping(value, "routes")
    paths = data.get("subscription_paths", ["/", "/c-sub"])
    normalized = []
    for item in _as_str_list(paths, "routes.subscription_paths"):
        path = item.strip()
        if not path:
            raise ConfigError("routes.subscription_paths 不能包含空路径")
        if not path.startswith("/"):
            path = f"/{path}"
        if path != "/" and path.endswith("/"):
            path = path.rstrip("/")
        if path not in normalized:
            normalized.append(path)
    if not normalized:
        raise ConfigError("routes.subscription_paths 至少需要一个路径")
    return RouteSettings(subscription_paths=tuple(normalized))


def _parse_hot_reload(value: Any) -> HotReloadSettings:
    data = _optional_mapping(value, "hot_reload")
    return HotReloadSettings(enabled=_as_bool(data.get("enabled", True), "hot_reload.enabled"))


def _parse_source_request(value: Any) -> SourceRequestSettings:
    data = _optional_mapping(value, "source_request")
    return SourceRequestSettings(
        connect_timeout=_as_float(data.get("connect_timeout", 5), "source_request.connect_timeout"),
        read_timeout=_as_float(data.get("read_timeout", 25), "source_request.read_timeout"),
        overall_timeout=_as_float(data.get("overall_timeout", 30), "source_request.overall_timeout"),
        max_workers=max(1, _as_int(data.get("max_workers", 4), "source_request.max_workers")),
        user_agent=_as_str(data.get("user_agent", "clash-verge/v2.5.1"), "source_request.user_agent"),
        allow_partial_failure=_as_bool(data.get("allow_partial_failure", False), "source_request.allow_partial_failure"),
    )


def _parse_proxy(value: Any) -> ProxySettings:
    data = _optional_mapping(value, "proxy")
    return ProxySettings(
        enabled=_as_bool(data.get("enabled", False), "proxy.enabled"),
        http=_optional_str(data.get("http"), "proxy.http"),
        https=_optional_str(data.get("https"), "proxy.https"),
        no_proxy=tuple(_as_str_list(data.get("no_proxy", ["localhost", "127.0.0.1", "::1"]), "proxy.no_proxy")),
    )


def _parse_response(value: Any) -> ResponseSettings:
    data = _optional_mapping(value, "response")
    return ResponseSettings(
        profile_update_interval=_as_str(data.get("profile_update_interval", "1"), "response.profile_update_interval"),
        mimetype=_as_str(data.get("mimetype", "text/yaml; charset=utf-8"), "response.mimetype"),
    )


def _parse_rules(value: Any, base_dir: Path) -> RulesSettings:
    data = _optional_mapping(value, "rules")
    raw_path = _as_str(data.get("commercial_rules_path", "rules/commercial_rules.list"), "rules.commercial_rules_path")
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return RulesSettings(commercial_rules_path=path.resolve())


def _parse_tokens(value: Any) -> Mapping[str, TokenConfig]:
    data = _as_mapping(value, "tokens")
    if not data:
        raise ConfigError("tokens 至少需要配置一个 token")

    parsed: dict[str, TokenConfig] = {}
    for token, token_value in data.items():
        token_name = _as_str(token, "tokens.<token>").strip()
        if not token_name:
            raise ConfigError("tokens 不能包含空 token")
        token_data = _as_mapping(token_value, f"tokens.{token_name}")
        userinfo_mode = _as_str(token_data.get("userinfo_mode"), f"tokens.{token_name}.userinfo_mode")
        if userinfo_mode not in ("source", "custom"):
            raise ConfigError(f"tokens.{token_name}.userinfo_mode 必须是 source 或 custom")

        subscription_userinfo = _optional_str(
            token_data.get("subscription_userinfo"),
            f"tokens.{token_name}.subscription_userinfo",
        )
        if userinfo_mode == "custom" and not (subscription_userinfo and subscription_userinfo.strip()):
            raise ConfigError(f"tokens.{token_name}.subscription_userinfo 在 custom 模式下不能为空")

        parsed[token_name] = TokenConfig(
            sources=tuple(_parse_source_entries(token_data.get("source"), f"tokens.{token_name}.source")),
            userinfo_mode=userinfo_mode,
            subscription_userinfo=subscription_userinfo.strip() if subscription_userinfo else None,
        )
    return parsed


def _parse_source_entries(value: Any, field_name: str) -> list[SourceEntry]:
    if isinstance(value, str):
        ref = value.strip()
        if not ref:
            raise ConfigError(f"{field_name} 不能为空")
        return [SourceEntry(ref=ref, tag="source1")]

    if not isinstance(value, list) or not value:
        raise ConfigError(f"{field_name} 必须是非空字符串或非空列表")

    entries: list[SourceEntry] = []
    for index, item in enumerate(value, start=1):
        default_tag = f"source{index}"
        if isinstance(item, str):
            ref = item.strip()
            tag = default_tag
        else:
            item_data = _as_mapping(item, f"{field_name}[{index}]")
            ref = _as_str(item_data.get("ref"), f"{field_name}[{index}].ref").strip()
            tag = _as_str(item_data.get("tag", default_tag), f"{field_name}[{index}].tag").strip()

        if not ref:
            raise ConfigError(f"{field_name}[{index}].ref 不能为空")
        if not tag:
            raise ConfigError(f"{field_name}[{index}].tag 不能为空")
        entries.append(SourceEntry(ref=ref, tag=tag))
    return entries


def _load_commercial_rules(path: Path) -> tuple[str, ...]:
    if not path.exists():
        raise ConfigError(f"商业规则文件不存在: {path}")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ConfigError(f"读取商业规则文件失败: {exc}") from exc
    rules = tuple(line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#"))
    if not rules:
        raise ConfigError(f"商业规则文件为空: {path}")
    return rules


def _file_signature(path: Path) -> tuple[str, int, int]:
    try:
        stat = path.stat()
    except OSError:
        return (str(path), -1, -1)
    return (str(path), stat.st_mtime_ns, stat.st_size)


def _optional_mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    return _as_mapping(value, field_name)


def _as_mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigError(f"{field_name} 必须是对象")
    return value


def _as_str(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{field_name} 必须是字符串")
    return value


def _optional_str(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    return _as_str(value, field_name)


def _as_bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{field_name} 必须是布尔值")
    return value


def _as_int(value: Any, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ConfigError(f"{field_name} 必须是整数")
    return value


def _as_float(value: Any, field_name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ConfigError(f"{field_name} 必须是数字")
    if value <= 0:
        raise ConfigError(f"{field_name} 必须大于 0")
    return float(value)


def _as_str_list(value: Any, field_name: str) -> list[str]:
    if isinstance(value, str):
        return [value]
    if not isinstance(value, (list, tuple)):
        raise ConfigError(f"{field_name} 必须是字符串列表")
    result = []
    for index, item in enumerate(value, start=1):
        result.append(_as_str(item, f"{field_name}[{index}]"))
    return result
