import time

from tc_memory.cache import TTLCache
from tc_memory.client import IsolationIds
from tc_memory.config import config_from_astrbot
from tc_memory.errors import TDAMUnavailable
from tc_memory.identity import ResolvedIdentity
from tc_memory.recall import perform_recall

IDENTITY = ResolvedIdentity(
    ids=IsolationIds(team_id="t1", agent_id="a1", user_id="u1"),
    session_id="qq:FriendMessage:1",
)
CFG = config_from_astrbot({})


class StubClient:
    """记录调用次数的 client 桩，可预设各路返回或异常。"""

    def __init__(self):
        self.calls = {"search": 0, "core": 0, "scenes": 0, "listing": 0}
        self.l1_items = [{"id": "r1", "content": "下周一发版", "type": "episodic"}]
        self.persona = "用户是后端工程师"
        self.scenes = [{"path": "scene_blocks/payment.md"}]
        self.listing = "- skill-a: 部署流程"
        self.fail: set[str] = set()

    async def search_atomic(self, ids, query, limit=5):
        self.calls["search"] += 1
        if "search" in self.fail:
            raise TDAMUnavailable("down")
        return self.l1_items

    async def read_core(self, ids):
        self.calls["core"] += 1
        if "core" in self.fail:
            raise TDAMUnavailable("down")
        return self.persona

    async def list_scenarios(self, ids):
        self.calls["scenes"] += 1
        if "scenes" in self.fail:
            raise TDAMUnavailable("down")
        return self.scenes

    async def skill_listing(self, ids):
        self.calls["listing"] += 1
        if "listing" in self.fail:
            raise TDAMUnavailable("down")
        return self.listing


def test_ttl_cache_expires():
    cache = TTLCache()
    cache.set("k", "v", ttl_sec=0.05)
    assert cache.get("k") == "v"
    time.sleep(0.06)
    assert cache.get("k") is None


async def test_perform_recall_full_injection():
    client = StubClient()
    text = await perform_recall(client, TTLCache(), IDENTITY, "发版时间？", CFG)
    assert "<relevant-memories>" in text
    assert "下周一发版" in text
    assert "<user-persona>" in text
    assert "scene_blocks/payment.md" in text
    assert "<team_skills>" in text


async def test_empty_query_skips_search():
    client = StubClient()
    text = await perform_recall(client, TTLCache(), IDENTITY, "", CFG)
    assert client.calls["search"] == 0
    # 其余三路照常：画像/场景/skill 仍注入
    assert "<user-persona>" in text
    assert "<team_skills>" in text


async def test_single_failure_degrades_to_remaining():
    client = StubClient()
    client.fail.add("search")
    text = await perform_recall(client, TTLCache(), IDENTITY, "发版？", CFG)
    assert "<relevant-memories>" not in text  # 搜索挂了 → 没有 L1 块
    assert "<user-persona>" in text  # 其他路不受影响


async def test_persona_and_listing_cached():
    client = StubClient()
    cache = TTLCache()
    await perform_recall(client, cache, IDENTITY, "q1", CFG)
    await perform_recall(client, cache, IDENTITY, "q2", CFG)
    assert client.calls["core"] == 1  # 第二次命中缓存
    assert client.calls["listing"] == 1
    assert client.calls["search"] == 2  # 搜索不缓存


async def test_all_empty_returns_none():
    client = StubClient()
    client.l1_items = []
    client.persona = None
    client.scenes = []
    client.listing = None
    text = await perform_recall(client, TTLCache(), IDENTITY, "q", CFG)
    assert text is None
