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
