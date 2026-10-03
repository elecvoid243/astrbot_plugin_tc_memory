"""KnowledgeClient 契约测试：响应结构按 MemoryKnowledge 真实 API 口径
（wiki/page/read → data.items[{ref, content|not_found}]；
code-graph search/explore → data{text, isError}），
且所有请求必须携带 x-tdai-service-id 头（假网关强制校验，缺则 400）。
"""

import pytest

from tc_memory.errors import TDAMError, TDAMUnavailable
from tc_memory.knowledge_client import KnowledgeClient

KNOWLEDGE_PATHS = frozenset(
    {
        "/v3/wiki/search",
        "/v3/wiki/page/read",
        "/v3/code-graph/search",
        "/v3/code-graph/explore",
    }
)


@pytest.fixture
async def kgateway(gateway):
    fake, endpoint = gateway
    fake.require_service_header_paths = KNOWLEDGE_PATHS
    return fake, endpoint


def make_client(endpoint: str, timeout_sec: float = 5.0) -> KnowledgeClient:
    return KnowledgeClient(endpoint + "/", service_id="svc", timeout_sec=timeout_sec)


async def test_wiki_search_sends_service_header_and_wiki_id(kgateway):
    fake, endpoint = kgateway
    fake.ok(
        "/v3/wiki/search",
        {"results": [{"title": "部署手册", "path": "ops/deploy.md", "score": 0.9}]},
    )
    client = make_client(endpoint)

    results = await client.wiki_search("w1", "部署", limit=3)

    assert results == [{"title": "部署手册", "path": "ops/deploy.md", "score": 0.9}]
    req = fake.requests[0]
    assert req.headers["x-tdai-service-id"] == "svc"
    assert req.body == {"wiki_id": "w1", "query": "部署", "limit": 3}
    await client.aclose()


async def test_wiki_read_parses_items_and_filters_not_found(kgateway):
    fake, endpoint = kgateway
    fake.ok(
        "/v3/wiki/page/read",
        {
            "items": [
                {"ref": "a.md", "content": "正文A"},
                {"ref": "gone.md", "not_found": True},
            ]
        },
    )
    client = make_client(endpoint)

    pages = await client.wiki_read("w1", ["a.md", "gone.md"])

    assert fake.requests[0].body == {"wiki_id": "w1", "refs": ["a.md", "gone.md"]}
    assert pages == [{"ref": "a.md", "content": "正文A"}]
    await client.aclose()


async def test_codegraph_search_returns_text(kgateway):
    fake, endpoint = kgateway
    fake.ok(
        "/v3/code-graph/search",
        {"text": "找到符号 login（auth.ts:12）", "isError": False},
    )
    client = make_client(endpoint)

    text = await client.codegraph_search("g1", "login")

    assert text == "找到符号 login（auth.ts:12）"
    assert fake.requests[0].body["code_graph_id"] == "g1"
    await client.aclose()


async def test_codegraph_explore_iserror_raises(kgateway):
    fake, endpoint = kgateway
    fake.ok("/v3/code-graph/explore", {"text": "symbol 不存在", "isError": True})
    client = make_client(endpoint)

    with pytest.raises(TDAMError):
        await client.codegraph_explore("g1", "missing")
    await client.aclose()


async def test_envelope_error_raises_tdam_error(kgateway):
    fake, endpoint = kgateway
    fake.add("POST", "/v3/wiki/search", {"code": 404, "message": "Wiki not found"})
    client = make_client(endpoint)

    with pytest.raises(TDAMError) as exc_info:
        await client.wiki_search("w-missing", "x")
    assert exc_info.value.code == 404
    await client.aclose()


async def test_timeout_raises_unavailable(kgateway):
    fake, endpoint = kgateway
    fake.add("POST", "/v3/wiki/search", {"__sleep__": 1, "code": 0, "data": {}})
    client = make_client(endpoint, timeout_sec=0.05)

    with pytest.raises(TDAMUnavailable):
        await client.wiki_search("w1", "x")
    await client.aclose()
