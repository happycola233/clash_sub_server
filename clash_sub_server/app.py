"""Flask 应用工厂与 HTTP 路由。"""

from __future__ import annotations

from flask import Flask, Response, abort, request
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

import logging

from .config_loader import ConfigManager
from .errors import AllSourcesFailedError, ConfigError, SourceError, SubscriptionFormatError
from .fetcher import format_source_refs, load_merged_source_config
from .logging_config import configure_logging
from .parser import validate_proxy_list
from .renderer import build_final_config, dump_yaml
from .userinfo import choose_response_userinfo, describe_userinfo_header_mode

logger = logging.getLogger(__name__)
access_logger = logging.getLogger("clash_sub_server.access")


def create_app(config_manager: ConfigManager | None = None) -> Flask:
    """创建 Flask app。

    使用 app factory 后，测试、开发服务器、Waitress 部署都可以复用同一套
    应用构建逻辑。
    """

    manager = config_manager or ConfigManager()
    configure_logging()
    app = Flask(__name__)
    app.config["CONFIG_MANAGER"] = manager

    # 信任一层反向代理，恢复真实客户端 IP、协议和 Host。
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=0)

    @app.after_request
    def log_request(response: Response) -> Response:
        client_ip = request.remote_addr or "-"
        x_real_ip = request.headers.get("X-Real-IP", "-")
        x_forwarded_for = request.headers.get("X-Forwarded-For", "-")
        access_logger.info(
            '%s - X-Real-IP=%s X-Forwarded-For="%s" - "%s %s" %s',
            client_ip,
            x_real_ip,
            x_forwarded_for,
            request.method,
            get_request_url_for_log(),
            response.status_code,
        )
        return response

    @app.errorhandler(HTTPException)
    def log_http_exception(err: HTTPException):
        access_logger.warning(
            'HTTP 异常: 状态码=%s 描述="%s" 来源IP=%s 请求URL="%s"',
            err.code,
            err.description,
            request.remote_addr or "-",
            get_request_url_for_log(),
        )
        return err

    @app.errorhandler(Exception)
    def log_unexpected_exception(err: Exception):
        logger.exception('未捕获异常: 来源IP=%s 请求URL="%s"', request.remote_addr or "-", get_request_url_for_log())
        return Response("服务器内部错误，请查看服务端日志。", status=500)

    @app.route("/", defaults={"path": ""})
    @app.route("/<path:path>")
    def generate_sub(path: str):
        return handle_subscription_request(manager)

    return app


def get_request_url_for_log() -> str:
    """优先使用反向代理透传的原始 URI，避免 proxy_pass 重写后日志失真。"""

    original_uri = (request.headers.get("X-Original-URI") or "").strip()
    if original_uri:
        if original_uri.startswith(("http://", "https://")):
            return original_uri

        if not original_uri.startswith("/"):
            original_uri = f"/{original_uri}"

        forwarded_proto = request.headers.get("X-Forwarded-Proto", "")
        scheme = forwarded_proto.split(",")[0].strip() or request.scheme or "http"
        host = request.headers.get("Host") or request.host
        if host:
            return f"{scheme}://{host}{original_uri}"
        return original_uri

    return request.url


def handle_subscription_request(manager: ConfigManager) -> Response:
    """处理一次订阅请求。"""

    try:
        config = manager.get_config()
    except ConfigError as exc:
        logger.error("配置加载失败: %s", exc)
        return Response(f"Server misconfiguration: {exc}", status=500)

    request_path = _normalize_request_path(request.path)
    if request_path not in config.routes.subscription_paths:
        abort(404, description="Not Found")

    token = request.args.get("token")
    client_ip = request.remote_addr or "-"
    request_url = get_request_url_for_log()
    logger.info('收到订阅请求: 来源IP=%s 请求URL="%s" token=%s', client_ip, request_url, token)

    if token is None:
        logger.warning('拒绝请求: 缺少 token, 来源IP=%s 请求URL="%s"', client_ip, request_url)
        abort(403, description="Forbidden: Invalid or missing token.")

    token_config = config.tokens.get(token)
    if token_config is None:
        logger.warning('拒绝请求: token 无效, token=%s 来源IP=%s 请求URL="%s"', token, client_ip, request_url)
        abort(403, description="Forbidden: Invalid or missing token.")

    source_ref_log = format_source_refs(token_config.sources)
    logger.info(
        "开始加载订阅源: token=%s source=%s userinfo_mode=%s source_count=%s",
        token,
        source_ref_log,
        token_config.userinfo_mode,
        len(token_config.sources),
    )

    try:
        merged = load_merged_source_config(token_config.sources, config)
    except AllSourcesFailedError as exc:
        logger.error("加载订阅源失败: token=%s source=%s error=%s", token, source_ref_log, exc)
        return Response(f"获取订阅源失败: {exc}", status=500)
    except SourceError as exc:
        logger.error("加载订阅源失败: token=%s source=%s error=%s", token, source_ref_log, exc)
        return Response(f"获取订阅源失败: {exc}", status=500)

    if merged.failed_sources:
        logger.warning(
            "多源订阅部分源加载失败: token=%s 成功数量=%s 失败数量=%s 失败源=%s",
            token,
            len(merged.successful_sources),
            len(merged.failed_sources),
            [item.ref for item in merged.failed_sources],
        )

    try:
        proxies = validate_proxy_list(merged.source_config.get("proxies", []))
    except SubscriptionFormatError as exc:
        logger.error("订阅源格式错误: token=%s source=%s error=%s", token, source_ref_log, exc)
        return Response(str(exc), status=500)

    proxy_names = [proxy["name"] for proxy in proxies]
    logger.info(
        "节点列表解析完成: token=%s source=%s 成功源数量=%s 失败源数量=%s 节点数量=%s",
        token,
        source_ref_log,
        len(merged.successful_sources),
        len(merged.failed_sources),
        len(proxy_names),
    )

    final_config = build_final_config(proxies, config.commercial_rules)
    yaml_str = dump_yaml(final_config)
    resp = Response(yaml_str, mimetype=config.response.mimetype)

    response_userinfo = choose_response_userinfo(
        token_config.userinfo_mode,
        token_config.subscription_userinfo,
        token_config.sources,
        merged.remote_userinfos,
    )
    if response_userinfo:
        resp.headers["Subscription-Userinfo"] = response_userinfo
    resp.headers["Profile-Update-Interval"] = config.response.profile_update_interval

    logger.info(
        "订阅配置生成成功: token=%s source=%s 节点数量=%s 订阅头模式=%s",
        token,
        source_ref_log,
        len(proxy_names),
        describe_userinfo_header_mode(token_config.userinfo_mode, token_config.sources, response_userinfo),
    )
    return resp


def _normalize_request_path(path: str) -> str:
    if not path:
        return "/"
    if path != "/" and path.endswith("/"):
        return path.rstrip("/")
    return path
    return path
