"""详细日志工具（verbose_logging 开关统一入口）。

原则：
- 敏感值（api_key 等）任何级别都不打印
- 内容按 limit 截断，防日志洪水
- 开关关闭时零开销（f-string 不预求值）
"""

import logging

SHORT_LIMIT = 120


def short(text: str | None, limit: int = SHORT_LIMIT) -> str:
    if not text:
        return ""
    text = str(text)
    return text if len(text) <= limit else text[:limit] + "…"


def vlog(logger: logging.Logger, cfg, msg: str, *args) -> None:
    """cfg.verbose_logging 开启时才输出 INFO。"""
    if getattr(cfg, "verbose_logging", False):
        logger.info("[verbose] " + msg, *args)


# ── 插件 logger 路由 ─────────────────────────────────────────
# AstrBot 的 WebUI 控制台（LogBroker）只挂在 `astrbot` logger 与
# `astrbot.plugin.<name>` 插件 logger 上；stdlib root 只进控制台+文件。
# 因此插件加载时注入插件 logger，所有模块日志改走插件管线。
# LazyLogger 在每次 emit 时解析目标：模块级 logger 在 import 时创建，
# 而注入发生在插件 __init__，晚于 import——创建时绑定会错过注入。

_plugin_logger = None


def set_plugin_logger(logger) -> None:
    """插件 __init__ 时注入（main.py 调用）；传 None 恢复 stdlib。"""
    global _plugin_logger
    _plugin_logger = logger


class LazyLogger:
    """按名延迟解析：注入插件 logger 后路由到插件管线，否则回退 stdlib。"""

    __slots__ = ("_name",)

    def __init__(self, name: str):
        self._name = name

    def _target(self):
        return (
            _plugin_logger
            if _plugin_logger is not None
            else logging.getLogger(self._name)
        )

    def debug(self, msg, *args, **kwargs):
        self._target().debug(msg, *args, **kwargs)

    def info(self, msg, *args, **kwargs):
        self._target().info(msg, *args, **kwargs)

    def warning(self, msg, *args, **kwargs):
        self._target().warning(msg, *args, **kwargs)

    def error(self, msg, *args, **kwargs):
        self._target().error(msg, *args, **kwargs)


def get_logger(name: str) -> LazyLogger:
    return LazyLogger(name)
