from tc_memory import tools
from tc_memory.cache import TTLCache
from tc_memory.capture import CaptureBuffer
from tc_memory.config import config_from_astrbot
from tc_memory.errors import TDAMUnavailable
from tc_memory.runtime import PluginRuntime


class StubCore:
    def __init__(self):
        self.calls = []
        self.fail = False

    def _maybe_fail(self):
        if self.fail:
            raise TDAMUnavailable("down")

    async def search_atomic(self, ids, query, limit=5):
        self.calls.append(("search_atomic", query))
        self._maybe_fail()
        return [{"id": "r1", "content": "喜欢咖啡", "type": "persona", "score": 0.9}]

    async def conversation_search(self, ids, query, limit=5):
        self.calls.append(("conversation_search", query))
        return [
            {"id": "m1", "role": "user", "content": "原文", "created_at": "2026-09-01"}
        ]

    async def skill_search(self, ids, query, limit=10):
        return [{"name": "deploy", "description": "部署流程", "score": 0.8}]

    async def skill_get_by_name(self, ids, name):
        if name == "missing":
            return None
        return {"name": name, "content": "# 部署步骤\n1. ..."}

    async def health(self):
        return {"status": "ok"}


class StubKnowledge:
    def __init__(self):
        self.calls = []

    async def wiki_search(self, wiki_id, query, limit=5):
        self.calls.append(("wiki_search", wiki_id, query))
        return [{"title": "部署手册", "path": "ops/deploy.md"}]

    async def wiki_read(self, wiki_id, refs):
        return [{"ref": refs[0], "content": "页面正文"}]

    async def codegraph_search(self, gid, query, limit=10):
        return "找到符号 login（auth.ts:12）"

    async def codegraph_explore(self, gid, query):
        return "login 被 auth.ts 调用"


def make_runtime(knowledge=True, wiki_id="w1", graph_id="g1") -> PluginRuntime:
    cfg = config_from_astrbot(
        {
            "knowledge_enabled": knowledge,
            "knowledge_wiki_id": wiki_id,
            "knowledge_codegraph_id": graph_id,
        }
    )
    rt = PluginRuntime(
        cfg=cfg,
        core=StubCore(),
        knowledge=StubKnowledge() if knowledge else None,
        cache=TTLCache(),
        buffer=CaptureBuffer(),
    )
    rt._enabled = True
    return rt


async def test_memory_search_formats_numbered_list():
    rt = make_runtime()
    text = await tools.memory_search(rt, "u1", "qq:F:1", "咖啡")
    assert "喜欢咖啡" in text
    assert "[persona]" in text


async def test_memory_search_failure_returns_friendly_text():
    rt = make_runtime()
    rt.core.fail = True
    text = await tools.memory_search(rt, "u1", "qq:F:1", "x")
    assert "不可用" in text


async def test_conversation_search_lists_originals():
    rt = make_runtime()
    text = await tools.conversation_search(rt, "u1", "qq:F:1", "原文")
    assert "原文" in text


async def test_skill_search_and_view():
    rt = make_runtime()
    assert "deploy" in await tools.skill_search(rt, "u1", "qq:F:1", "部署")
    assert "# 部署步骤" in await tools.skill_view(rt, "u1", "qq:F:1", "deploy")
    assert "未找到" in await tools.skill_view(rt, "u1", "qq:F:1", "missing")


async def test_wiki_tools_use_configured_instance_id():
    rt = make_runtime()
    text = await tools.wiki_search(rt, "u1", "qq:F:1", "部署")
    assert "部署手册" in text
    assert ("wiki_search", "w1", "部署") in rt.knowledge.calls
    assert "页面正文" in await tools.wiki_read(rt, "u1", "qq:F:1", "ops/deploy.md")


async def test_wiki_tool_without_instance_id_returns_hint():
    rt = make_runtime(wiki_id="")
    text = await tools.wiki_search(rt, "u1", "qq:F:1", "x")
    assert "knowledge_wiki_id" in text


async def test_knowledge_disabled_returns_hint():
    rt = make_runtime(knowledge=False)
    text = await tools.wiki_search(rt, "u1", "qq:F:1", "x")
    assert "未启用" in text


async def test_codegraph_tools():
    rt = make_runtime()
    assert "login" in await tools.codegraph_search(rt, "u1", "qq:F:1", "登录")
    text = await tools.codegraph_explore(rt, "u1", "qq:F:1", "login")
    assert "auth.ts" in text
