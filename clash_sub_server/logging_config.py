"""日志配置。

应用只在创建 Flask app 时初始化一次日志格式。各模块使用自己的
`logging.getLogger(__name__)`，请求访问日志使用 `clash_sub_server.access`。
这种方式更接近常见 Python 服务实践，也方便以后接入 systemd/journald 或
JSON 日志处理器。
"""

from __future__ import annotations

import logging


def configure_logging(level: int = logging.INFO) -> None:
    """初始化项目日志格式。

    如果宿主环境已经配置了 handler，就只调整项目 logger 级别，避免重复添加
    handler 导致日志重复输出。
    """

    root_logger = logging.getLogger()
    if not root_logger.handlers:
        logging.basicConfig(
            level=level,
            format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    logging.getLogger("clash_sub_server").setLevel(level)
    logging.getLogger("clash_sub_server.access").setLevel(level)
