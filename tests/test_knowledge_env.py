"""知识库服务启动环境测试：TMC_CALLBACK_URL 决定「索引完成→meta 资产自动登记」闭环。

缺该变量时 KS 的 ready 回调静默跳过（callback.ts no-op），
图谱建好但 meta 面无资产 → 面板搜索报「知识库不存在或已被删除」。

需要 AstrBot 环境（main.py 导入 astrbot）；插件 venv 下跳过。
"""

from pathlib import Path

import pytest

pytest.importorskip("astrbot")

from tc_memory.config import config_from_astrbot
from tests.conftest import import_plugin_main

_main = import_plugin_main()


def test_build_knowledge_env_includes_callback_when_panel_enabled():
    cfg = config_from_astrbot({"knowledge_enabled": True, "panel_enabled": True})
    env = _main.build_knowledge_env(cfg, Path("C:/data"))

    assert env["TMC_CALLBACK_URL"] == f"http://127.0.0.1:{_main.PANEL_PORT}"
    assert env["LLM_MODE"] == "custom"
    assert "KNOWLEDGE_DATA_DIR" in env


def test_build_knowledge_env_no_callback_when_panel_disabled():
    cfg = config_from_astrbot({"knowledge_enabled": True, "panel_enabled": False})
    env = _main.build_knowledge_env(cfg, Path("C:/data"))

    assert "TMC_CALLBACK_URL" not in env
