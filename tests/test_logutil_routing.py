"""logutil 路由测试：插件 logger 注入后，模块日志走插件管线（WebUI 控制台可见）。"""

import logging

from tc_memory.logutil import get_logger, set_plugin_logger


class CollectLogger:
    """模拟 AstrBot 插件 logger：收集 record 供断言。"""

    def __init__(self):
        self.records = []

    def _mk(self, level, msg, args):
        self.records.append((level, msg % args if args else msg))

    def info(self, msg, *args):
        self._mk("INFO", msg, args)

    def warning(self, msg, *args):
        self._mk("WARNING", msg, args)

    def error(self, msg, *args):
        self._mk("ERROR", msg, args)


def test_lazy_logger_defaults_to_stdlib(caplog):
    set_plugin_logger(None)
    logger = get_logger("tc_memory.test_default")
    with caplog.at_level(logging.INFO):
        logger.info("stdlib 路径 %s", "ok")
    assert "stdlib 路径 ok" in caplog.text


def test_lazy_logger_routes_to_plugin_logger_after_injection():
    plugin_logger = CollectLogger()
    set_plugin_logger(plugin_logger)
    try:
        logger = get_logger("tc_memory.test_routed")
        logger.info("插件路径 %s", "ok")
        assert ("INFO", "插件路径 ok") in plugin_logger.records
    finally:
        set_plugin_logger(None)


def test_lazy_resolution_per_call():
    """同一 logger 对象在注入前后自动切换目标（模块级 logger 在 import 时创建，
    注入发生在插件 __init__——必须每次 emit 时解析，而非创建时绑定）。"""
    logger = get_logger("tc_memory.test_lazy")
    plugin_logger = CollectLogger()
    set_plugin_logger(plugin_logger)
    try:
        logger.info("切换后")
    finally:
        set_plugin_logger(None)
    assert ("INFO", "切换后") in plugin_logger.records
