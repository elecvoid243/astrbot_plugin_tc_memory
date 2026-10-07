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


class _FakeLauncher:
    """launcher 替身：真 launcher 需要 external_tools 打包产物，测试环境没有。"""

    def __init__(self):
        self.env: dict[str, str] = {}
        self.shutdown_called = False

    def set_env(self, updates: dict) -> None:
        self.env.update(updates)

    async def shutdown(self) -> None:
        self.shutdown_called = True


def _fake_provider():
    class FakeProvider:
        provider_config = {  # noqa: RUF012  # 测试替身，类属性即可
            "id": "deepseek-responses/deepseek-flash",
            "api_base": "https://api.deepseek.com/v1",
            "key": ["sk-test"],
            "model": "deepseek-flash",
        }

    return FakeProvider()


async def test_cold_start_defers_services_until_providers_ready(gateway):
    """回归：冷启动 plugin.reload() 早于 provider.initialize()，
    装插件时解析不出 LLM 三元组——必须推迟 spawn，否则子进程带着空
    LLM 配置落地（spawn 之后 set_env 不再生效）。"""
    fake, endpoint = gateway
    fake.add("GET", "/health", {"status": "ok"})
    plugin = _make_plugin(
        endpoint, local_llm_provider_id="deepseek-responses/deepseek-flash"
    )
    launcher = _FakeLauncher()
    plugin.runtime.launcher = launcher
    # 冷启动窗口特征：provider 配置已在，运行时实例还没建
    plugin.context.provider_manager.providers_config = [{"id": "p1"}]
    plugin.context.get_all_providers.return_value = []

    await plugin._on_loaded(SimpleNamespace())
    assert launcher.env == {}
    assert plugin.runtime.enabled is False

    # provider 就绪后的补齐路径：注入 env 并拉起服务
    plugin.context.get_all_providers.return_value = [_fake_provider()]
    await plugin._on_astrbot_loaded()
    assert launcher.env["TDAI_LLM_BASE_URL"] == "https://api.deepseek.com/v1"
    assert launcher.env["TDAI_LLM_MODEL"] == "deepseek-flash"
    assert plugin.runtime.enabled is True

    await plugin.terminate()
    assert launcher.shutdown_called is True


async def test_no_provider_configured_starts_degraded(gateway):
    """用户一个 provider 都没配：照常启动（召回/记录可用），只是提炼不可用——
    不能因为等不到 provider 就永远不启动本地服务。"""
    fake, endpoint = gateway
    fake.add("GET", "/health", {"status": "ok"})
    plugin = _make_plugin(
        endpoint, local_llm_provider_id="deepseek-responses/deepseek-flash"
    )
    launcher = _FakeLauncher()
    plugin.runtime.launcher = launcher
    plugin.context.provider_manager.providers_config = []
    plugin.context.get_all_providers.return_value = []

    await plugin._on_loaded(SimpleNamespace())
    assert plugin.runtime.enabled is True
    assert launcher.env == {}
    await plugin.terminate()


async def test_hot_reload_probes_once_and_injects_provider_env(gateway):
    """运行期重载插件：provider 已就绪 → 立即注入 env；且每个插件加载都会
    触发 on_plugin_loaded，本地服务只应启动一次。"""
    fake, endpoint = gateway
    fake.add("GET", "/health", {"status": "ok"})
    plugin = _make_plugin(
        endpoint, local_llm_provider_id="deepseek-responses/deepseek-flash"
    )
    launcher = _FakeLauncher()
    plugin.runtime.launcher = launcher
    plugin.context.provider_manager.providers_config = [{"id": "p1"}]
    plugin.context.get_all_providers.return_value = [_fake_provider()]

    await plugin._on_loaded(SimpleNamespace())
    await plugin._on_loaded(SimpleNamespace())  # 其它插件加载 → 本 hook 再次触发

    assert plugin.runtime.enabled is True
    assert launcher.env["TDAI_LLM_API_KEY"] == "sk-test"
    # 只探测一次：第二次 on_plugin_loaded 不应重新 probe
    assert len([r for r in fake.requests if r.path == "/health"]) == 1
    await plugin.terminate()


async def test_server_mode_probes_immediately_without_local_services(gateway):
    """server 模式没有内嵌服务要拉，不受 provider 就绪时机影响。"""
    fake, endpoint = gateway
    fake.add("GET", "/health", {"status": "ok"})
    plugin = _make_plugin(endpoint, mode="server", core_api_key="real-key")

    await plugin._on_loaded(SimpleNamespace())
    assert plugin.runtime.enabled is True
    await plugin.terminate()


async def test_plugin_logger_injected_on_init(gateway):
    """插件 __init__ 应把 tc_memory 模块日志路由到插件专属 logger
    （否则 WebUI 控制台看不到——LogBroker 只挂在 astrbot.plugin.* 管线上）。"""
    fake, endpoint = gateway
    fake.add("GET", "/health", {"status": "ok"})
    plugin = _make_plugin(endpoint)

    # 注意：测试进程里 tc_memory 有两份拷贝（顶层 tc_memory.* 与包路径
    # astrbot_plugin_tc_memory.tc_memory.*），插件用的是后者
    import sys

    logutil_mod = sys.modules["astrbot_plugin_tc_memory.tc_memory.logutil"]
    assert logutil_mod._plugin_logger is plugin.logger
    assert logutil_mod._plugin_logger.name == "astrbot.plugin.astrbot_plugin_tc_memory"


async def test_knowledge_tools_pruned_when_disabled(gateway):
    """knowledge_enabled=false：4 个知识工具从注册表移除，不注入 LLM。"""
    import importlib

    from astrbot.core.provider.register import llm_tools

    main_mod = import_plugin_main()
    importlib.reload(main_mod)  # 重新执行装饰器，恢复全部 8 个工具
    fake, endpoint = gateway
    fake.add("GET", "/health", {"status": "ok"})

    ctx = MagicMock()
    ctx.get_llm_tool_manager = lambda: llm_tools  # 接回真实注册表，让 prune 生效
    plugin = main_mod.TcMemoryPlugin(
        context=ctx, config={"core_endpoint": endpoint, "knowledge_enabled": False}
    )

    names = {t.name for t in llm_tools.func_list}
    assert "wiki_search" not in names
    assert "wiki_read" not in names
    assert "codegraph_kb_search" not in names
    assert "codegraph_kb_explore" not in names
    # 基础记忆工具保留
    assert {
        "memory_search",
        "conversation_search",
        "skill_search",
        "skill_view",
    } <= names
    await plugin.terminate()


async def test_knowledge_tools_kept_when_enabled(gateway):
    """knowledge_enabled=true：4 个知识工具保留注册。"""
    import importlib

    from astrbot.core.provider.register import llm_tools

    main_mod = import_plugin_main()
    importlib.reload(main_mod)
    fake, endpoint = gateway
    fake.add("GET", "/health", {"status": "ok"})

    ctx = MagicMock()
    ctx.get_llm_tool_manager = lambda: llm_tools
    plugin = main_mod.TcMemoryPlugin(
        context=ctx,
        config={"core_endpoint": endpoint, "knowledge_enabled": True},
    )

    names = {t.name for t in llm_tools.func_list}
    assert {
        "wiki_search",
        "wiki_read",
        "codegraph_kb_search",
        "codegraph_kb_explore",
    } <= names
    await plugin.terminate()
