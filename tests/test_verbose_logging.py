"""详细日志（verbose_logging）测试：开启后记录召回/写入/工具动作，关闭时静默。"""

import logging
from pathlib import Path

from tc_memory import tools
from tc_memory.cache import TTLCache
from tc_memory.capture import CaptureBuffer
from tc_memory.client import IsolationIds
from tc_memory.config import config_from_astrbot
from tc_memory.identity import ResolvedIdentity
from tc_memory.recall import perform_recall
from tc_memory.runtime import PluginRuntime

FIXTURE = Path(__file__).parent / "fixtures" / "fake_gw.py"
IDENTITY = ResolvedIdentity(
    ids=IsolationIds(team_id="t1", agent_id="a1", user_id="u1"),
    session_id="qq:F:1",
)


class StubClient:
    def __init__(self):
        self.calls = []

    async def search_atomic(self, ids, query, limit=5):
        self.calls.append("search")
        return [{"id": "rec_1", "type": "episodic", "content": "喜欢美式咖啡"}]

    async def read_core(self, ids):
        self.calls.append("core")
        return "用户是后端工程师"

    async def list_scenarios(self, ids):
        return [{"path": "scene_blocks/dev.md"}]

    async def skill_listing(self, ids):
        return "- deploy: 部署流程"

    async def add_conversation(self, ids, session_id, messages):
        self.calls.append("add")
        return {"added": len(messages)}

    async def health(self):
        return {"status": "ok"}


def make_cfg(**kw):
    return config_from_astrbot(kw)


def test_config_default_verbose_off():
    assert config_from_astrbot({}).verbose_logging is False


async def test_recall_logs_details_when_enabled(caplog):
    cfg = make_cfg(verbose_logging=True)
    with caplog.at_level(logging.INFO, logger="tc_memory.recall"):
        text = await perform_recall(
            StubClient(), TTLCache(), IDENTITY, "我喜欢什么咖啡", cfg
        )

    assert text
    joined = "\n".join(r.message for r in caplog.records)
    assert "我喜欢什么咖啡" in joined  # 查询词
    assert "rec_1" in joined and "喜欢美式咖啡" in joined  # 命中的记忆明细
    assert "后端工程师" in joined  # 画像命中
    assert "scene_blocks/dev.md" in joined  # 场景导航
    assert "deploy: 部署流程" in joined  # skill 清单
    assert "注入" in joined  # 注入量汇总


async def test_recall_silent_when_disabled(caplog):
    cfg = make_cfg()  # 默认关
    with caplog.at_level(logging.INFO, logger="tc_memory.recall"):
        await perform_recall(StubClient(), TTLCache(), IDENTITY, "查询词", cfg)

    joined = "\n".join(r.message for r in caplog.records)
    assert "喜欢美式咖啡" not in joined
    assert "注入" not in joined


async def test_recall_logs_cache_hits(caplog):
    cfg = make_cfg(verbose_logging=True)
    cache = TTLCache()
    client = StubClient()
    await perform_recall(client, cache, IDENTITY, "q1", cfg)  # 首次填充缓存
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="tc_memory.recall"):
        await perform_recall(client, cache, IDENTITY, "q2", cfg)
    joined = "\n".join(r.message for r in caplog.records)
    assert "缓存命中" in joined


async def test_capture_logs_written_content(caplog):
    cfg = make_cfg(verbose_logging=True)
    rt = PluginRuntime(
        cfg=cfg,
        core=StubClient(),
        knowledge=None,
        cache=TTLCache(),
        buffer=CaptureBuffer(),
    )
    rt._enabled = True
    rt.note_user_message("qq:F:1", "记住我喝美式不加糖" + "x" * 300)

    with caplog.at_level(logging.INFO, logger="tc_memory.runtime"):
        sent = await rt.capture_after("u1", "qq:F:1", "好的已记住")

    assert sent is True
    joined = "\n".join(r.message for r in caplog.records)
    assert "写入 L0" in joined
    assert "美式不加糖" in joined
    assert "已记住" in joined
    # 长内容截断，防日志洪水
    assert "x" * 200 not in joined


async def test_tool_call_logs_when_enabled(caplog):
    cfg = make_cfg(verbose_logging=True)
    rt = PluginRuntime(
        cfg=cfg,
        core=StubClient(),
        knowledge=None,
        cache=TTLCache(),
        buffer=CaptureBuffer(),
    )
    rt._enabled = True

    with caplog.at_level(logging.INFO, logger="tc_memory.tools"):
        out = await tools.memory_search(rt, "u1", "qq:F:1", "咖啡")

    assert "喜欢美式咖啡" in out
    joined = "\n".join(r.message for r in caplog.records)
    assert "memory_search" in joined and "咖啡" in joined


async def test_tool_call_silent_when_disabled(caplog):
    cfg = make_cfg()
    rt = PluginRuntime(
        cfg=cfg,
        core=StubClient(),
        knowledge=None,
        cache=TTLCache(),
        buffer=CaptureBuffer(),
    )
    rt._enabled = True

    with caplog.at_level(logging.INFO, logger="tc_memory.tools"):
        await tools.memory_search(rt, "u1", "qq:F:1", "咖啡")

    assert "memory_search" not in "\n".join(r.message for r in caplog.records)
