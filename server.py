"""服务入口文件。

业务逻辑放在 clash_sub_server 包内，入口文件只负责创建 app 并启动
Waitress。
"""

from __future__ import annotations

from waitress import serve

from clash_sub_server.app import create_app
from clash_sub_server.config_loader import ConfigManager

config_manager = ConfigManager()
app = create_app(config_manager)


if __name__ == "__main__":
    config = config_manager.get_config()
    serve(
        app,
        host=config.server.host,
        port=config.server.port,
        trusted_proxy=config.server.trusted_proxy,
        trusted_proxy_count=config.server.trusted_proxy_count,
        trusted_proxy_headers=set(config.server.trusted_proxy_headers),
    )
