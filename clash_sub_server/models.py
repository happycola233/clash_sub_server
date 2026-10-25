"""配置与运行时数据模型。

项目刻意不引入 Pydantic 等额外依赖，部署时只需要 Flask、Waitress、
Requests、PyYAML。这里用 dataclass 承接 YAML 配置，既清晰，也方便
后续做类型检查和单元测试。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class ServerSettings:
    """HTTP 服务监听与反代信任配置。

    注意：host/port 是 Waitress 启动参数，运行中修改配置文件不会改变已
    绑定的端口，需要重启服务才会生效。
    """

    host: str = "127.0.0.1"
    port: int = 2233
    trusted_proxy: str = "127.0.0.1"
    trusted_proxy_count: int = 1
    trusted_proxy_headers: tuple[str, ...] = (
        "x-forwarded-for",
        "x-forwarded-host",
        "x-forwarded-port",
        "x-forwarded-proto",
    )


@dataclass(frozen=True)
class RouteSettings:
    """可访问订阅的路径列表。"""

    subscription_paths: tuple[str, ...] = ("/", "/c-sub")


@dataclass(frozen=True)
class HotReloadSettings:
    """配置和规则文件热重载设置。"""

    enabled: bool = True


@dataclass(frozen=True)
class SourceRequestSettings:
    """远程订阅源请求策略。"""

    connect_timeout: float = 5.0
    read_timeout: float = 25.0
    overall_timeout: float = 30.0
    max_workers: int = 4
    user_agent: str = "clash-verge/v2.5.1"
    allow_partial_failure: bool = False


@dataclass(frozen=True)
class ProxySettings:
    """远程订阅源下载的代理设置。

    本地文件读取不走代理。
    """

    enabled: bool = False
    http: str | None = None
    https: str | None = None
    no_proxy: tuple[str, ...] = ("localhost", "127.0.0.1", "::1")


@dataclass(frozen=True)
class ResponseSettings:
    """订阅响应头和 MIME 类型设置。"""

    profile_update_interval: str = "24"
    mimetype: str = "text/yaml; charset=utf-8"


@dataclass(frozen=True)
class RulesSettings:
    """商业分流规则文件设置。"""

    commercial_rules_path: Path


@dataclass(frozen=True)
class SourceEntry:
    """单个订阅源配置。"""

    ref: str
    tag: str


@dataclass(frozen=True)
class TokenConfig:
    """一个 token 对应的一组订阅源与流量信息策略。"""

    sources: tuple[SourceEntry, ...]
    userinfo_mode: str
    subscription_userinfo: str | None = None


@dataclass(frozen=True)
class AppConfig:
    """完整应用配置。"""

    base_dir: Path
    server: ServerSettings
    routes: RouteSettings
    hot_reload: HotReloadSettings
    source_request: SourceRequestSettings
    proxy: ProxySettings
    response: ResponseSettings
    rules: RulesSettings
    tokens: Mapping[str, TokenConfig] = field(default_factory=dict)
    commercial_rules: tuple[str, ...] = field(default_factory=tuple)
