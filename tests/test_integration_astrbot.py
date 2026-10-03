"""装配层集成测试：真实 AstrBot 环境 + 真实 ProviderRequest + 假 Gateway。

覆盖评审 C1（schema 可加载）、C2（on_plugin_loaded 签名）与 I3（hook 真链路）。
仅在 AstrBot 环境下运行（插件 venv 中跳过）。
"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("astrbot")

from astrbot.core.provider.entities import ProviderRequest

from tests.conftest import import_plugin_main

TcMemoryPlugin = import_plugin_main().TcMemoryPlugin

PLUGIN_DIR = Path(__file__).resolve().parent.parent


def test_plugin_importable_via_dotted_package_path(tmp_path):
    """回归：AstrBot 以 data.plugins.<name>.main 包路径加载插件，
    插件目录不在 sys.path —— 绝对导入 tc_memory 会 ModuleNotFoundError。

    用干净子进程仿真加载器环境（本进程 sys.path 已被 pytest 污染）。
    """
    import subprocess
    import sys

    import astrbot

    astrbot_root = Path(astrbot.__file__).resolve().parent.parent
    code = (
        "import sys;"
        f"sys.path.insert(0, {str(astrbot_root)!r});"
        f"sys.path.insert(0, {str(PLUGIN_DIR.parent)!r});"
        f"import {PLUGIN_DIR.name}.main as m;"
        "print('IMPORT_OK', m.TcMemoryPlugin.__name__)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,  # 我们正是要断言失败场景，不能让 non-zero 退出码抛出
        cwd=tmp_path,  # 干净工作目录：插件目录不在 sys.path
    )
    assert "IMPORT_OK" in result.stdout, result.stderr


def test_conf_schema_loads_in_astrbot_config(tmp_path):
    """C1：_conf_schema.json 必须能被 AstrBotConfig 解析（否则插件装不上）。"""
    from astrbot.core.config.astrbot_config import AstrBotConfig

    schema = json.loads((PLUGIN_DIR / "_conf_schema.json").read_text(encoding="utf-8"))
    cfg = AstrBotConfig(
        config_path=str(tmp_path / "plugin_conf.json"),
        default_config={},
        schema=schema,
    )
    assert cfg["core_endpoint"] == "http://127.0.0.1:8420"
    assert cfg["user_id_map"] == {}


def _make_plugin(endpoint: str, **overrides) -> TcMemoryPlugin:
    config = {"core_endpoint": endpoint, **overrides}
    return TcMemoryPlugin(context=MagicMock(), config=config)


async def test_on_plugin_loaded_accepts_metadata_and_enables(gateway):
    """C2：框架以 handler(metadata) 形式调用 on_plugin_loaded。"""
    fake, endpoint = gateway
    fake.add("GET", "/health", {"status": "ok", "version": "2.0.0"})
    plugin = _make_plugin(endpoint)

    metadata = SimpleNamespace(name="astrbot_plugin_tc_memory")
    await plugin._on_loaded(metadata)  # 框架调用形式：位置参数 metadata

    assert plugin.runtime.enabled is True
    await plugin.terminate()


async def test_llm_request_hook_injects_temp_part(gateway):
    """I3：真 ProviderRequest 走完整召回注入链路，默认不落盘。"""
    fake, endpoint = gateway
    fake.add("GET", "/health", {"status": "ok"})
    fake.ok(
        "/v3/atomic/search",
        {"items": [{"id": "r1", "content": "喜欢咖啡", "type": "persona"}]},
    )
    fake.ok("/v3/core/read", {"content": "画像"})
    fake.ok("/v3/scenario/ls", {"entries": []})
    fake.ok("/v3/skill/listing", {"mode": "full", "listing": "", "hits": []})
    plugin = _make_plugin(endpoint)
    await plugin._on_loaded(SimpleNamespace())

    event = SimpleNamespace(get_sender_id=lambda: "qq_1", unified_msg_origin="qq:F:1")
    req = ProviderRequest(prompt="我喜欢什么")
    await plugin.on_llm_request_hook(event, req)

    assert len(req.extra_user_content_parts) == 1
    part = req.extra_user_content_parts[0]
    assert "喜欢咖啡" in part.text
    assert part._no_save is True  # 默认不落盘
    await plugin.terminate()


async def test_llm_request_hook_persist_mode_unmarked(gateway):
    """persist_injected_memory=true 时不标记 _no_save。"""
    fake, endpoint = gateway
    fake.add("GET", "/health", {"status": "ok"})
    fake.ok(
        "/v3/atomic/search",
        {"items": [{"id": "r1", "content": "m", "type": "episodic"}]},
    )
    fake.ok("/v3/core/read", {})
    fake.ok("/v3/scenario/ls", {"entries": []})
    fake.ok("/v3/skill/listing", {"listing": ""})
    plugin = _make_plugin(endpoint, persist_injected_memory=True)
    await plugin._on_loaded(SimpleNamespace())

    event = SimpleNamespace(get_sender_id=lambda: "qq_1", unified_msg_origin="qq:F:1")
    req = ProviderRequest(prompt="q")
    await plugin.on_llm_request_hook(event, req)

    assert req.extra_user_content_parts[0]._no_save is False
    await plugin.terminate()


async def test_remember_command_accepts_multi_word_content(gateway):
    """I1：/memory remember 的参数必须能容纳多词内容。"""
    fake, endpoint = gateway
    fake.add("GET", "/health", {"status": "ok"})
    fake.ok("/v3/conversation/add", {"added": 1})
    plugin = _make_plugin(endpoint)
    await plugin._on_loaded(SimpleNamespace())

    sent = []
    event = SimpleNamespace(
        get_sender_id=lambda: "qq_1",
        unified_msg_origin="qq:F:1",
        plain_result=lambda s: sent.append(s) or s,
    )
    async for _ in plugin.memory_cmd_remember(event, "我喜欢美式咖啡 不加糖"):
        pass

    assert (
        fake.requests[-1].body["messages"][0]["content"]
        == "请记住：我喜欢美式咖啡 不加糖"
    )
    await plugin.terminate()
