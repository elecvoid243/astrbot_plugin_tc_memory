"""Task 8 运行时装配逻辑测试（不依赖 AstrBot）。"""

import time

from tc_memory.cache import TTLCache
from tc_memory.capture import CaptureBuffer
from tc_memory.client import IsolationIds
from tc_memory.config import config_from_astrbot
from tc_memory.errors import TDAMAuthError, TDAMUnavailable
from tc_memory.runtime import PluginRuntime

IDS = IsolationIds(team_id="default", agent_id="default", user_id="u_qq_1")


class StubCore:
    def __init__(self):
        self.health_ok = True
        self.calls = {
            "health": 0,
            "search": 0,
            "core": 0,
            "scenes": 0,
            "listing": 0,
            "add": 0,
        }
        self.auth_fail = False
        self.net_fail = False

    async def health(self):
        self.calls["health"] += 1
        if not self.health_ok:
            raise TDAMUnavailable("down")
        return {"status": "ok"}

    async def search_atomic(self, ids, query, limit=5):
        self.calls["search"] += 1
        if self.auth_fail:
            raise TDAMAuthError(401, "bad key")
        return [{"id": "r1", "content": "记忆", "type": "episodic"}]

    async def read_core(self, ids):
        self.calls["core"] += 1
        return "画像"

    async def list_scenarios(self, ids):
        self.calls["scenes"] += 1
        return []

    async def skill_listing(self, ids):
        self.calls["listing"] += 1

    async def add_conversation(self, ids, session_id, messages):
        self.calls["add"] += 1
        return {"added": len(messages)}


def make_runtime(**cfg_overrides) -> tuple[PluginRuntime, StubCore]:
    core = StubCore()
    cfg = config_from_astrbot(cfg_overrides)
    runtime = PluginRuntime(
        cfg=cfg,
        core=core,
        knowledge=None,
        cache=TTLCache(),
        buffer=CaptureBuffer(),
    )
    return runtime, core


async def test_probe_success_enables():
    runtime, _core = make_runtime()
    assert await runtime.probe() is True
    assert runtime.enabled is True


async def test_probe_failure_disables():
    runtime, core = make_runtime()
    core.health_ok = False
    assert await runtime.probe() is False
    assert runtime.enabled is False


async def test_recall_returns_none_when_disabled():
    runtime, core = make_runtime()
    # 未 probe → 禁用
    text = await runtime.recall_for("qq_1", "qq:FriendMessage:1", "你好")
    assert text is None
    assert core.calls["search"] == 0


async def test_recall_returns_none_when_config_disabled():
    runtime, core = make_runtime(recall_enabled=False)
    await runtime.probe()
    text = await runtime.recall_for("qq_1", "qq:FriendMessage:1", "你好")
    assert text is None
    assert core.calls["search"] == 0


async def test_auth_error_triggers_backoff_and_short_circuits():
    runtime, core = make_runtime()
    await runtime.probe()
    core.auth_fail = True

    assert await runtime.recall_for("qq_1", "qq:F:1", "你好") is None
    assert core.calls["search"] == 1  # 第一次真实调用后失败
    assert await runtime.recall_for("qq_1", "qq:F:1", "再问") is None
    assert core.calls["search"] == 1  # 退避期内短路，不再触达

    runtime._auth_backoff_until = time.monotonic() - 1  # 模拟退避过期
    core.auth_fail = False
    text = await runtime.recall_for("qq_1", "qq:F:1", "恢复")
    assert text is not None  # 自动恢复


async def test_capture_after_flush():
    runtime, core = make_runtime()
    await runtime.probe()
    runtime.note_user_message("qq:F:1", "我喜欢咖啡")

    sent = await runtime.capture_after("qq_1", "qq:F:1", "已记住")

    assert sent is True
    assert core.calls["add"] == 1


async def test_capture_disabled_in_config():
    runtime, core = make_runtime(capture_enabled=False)
    await runtime.probe()
    runtime.note_user_message("qq:F:1", "你好")

    sent = await runtime.capture_after("qq_1", "qq:F:1", "答")

    assert sent is False
    assert core.calls["add"] == 0


async def test_agent_done_unsupported_disables_capture_only():
    core = StubCore()
    runtime = PluginRuntime(
        cfg=config_from_astrbot({}),
        core=core,
        knowledge=None,
        cache=TTLCache(),
        buffer=CaptureBuffer(),
        agent_done_supported=False,
    )
    await runtime.probe()
    runtime.note_user_message("qq:F:1", "你好")

    assert await runtime.capture_after("qq_1", "qq:F:1", "答") is False
    assert core.calls["add"] == 0
    # 召回不受影响
    assert await runtime.recall_for("qq_1", "qq:F:1", "你好") is not None
