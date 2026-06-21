"""内部异常类型。

把可预期错误拆成明确类型，路由层就能根据错误语义返回合适的 HTTP
状态码，同时日志也更容易定位问题。
"""


class ConfigError(Exception):
    """配置文件无效。"""


class SourceError(Exception):
    """单个订阅源加载或解析失败。"""


class AllSourcesFailedError(SourceError):
    """多源配置下所有订阅源都失败。"""


class SubscriptionFormatError(SourceError):
    """订阅源内容格式不符合预期。"""
