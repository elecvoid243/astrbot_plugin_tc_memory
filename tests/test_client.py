import pytest

from tc_memory.client import IsolationIds, TdMemoryClient
from tc_memory.errors import TDAMAuthError, TDAMError, TDAMUnavailable

IDS = IsolationIds(team_id="t1", agent_id="a1", user_id="u1")


def make_client(endpoint: str, timeout_sec: float = 5.0) -> TdMemoryClient:
    return TdMemoryClient(
        endpoint + "/", api_key="k", service_id="svc", timeout_sec=timeout_sec
    )


async def test_search_atomic_success(gateway):
    fake, endpoint = gateway
    fake.ok("/v3/atomic/search", {"items": [{"id": "rec_1", "content": "下周一发版"}]})
    client = make_client(endpoint)

    items = await client.search_atomic(IDS, query="发版", limit=3)

    assert items == [{"id": "rec_1", "content": "下周一发版"}]
    req = fake.requests[0]
    assert req.headers["Authorization"] == "Bearer k"
    assert req.headers["x-tdai-service-id"] == "svc"
    assert req.body["team_id"] == "t1"
    assert req.body["agent_id"] == "a1"
    assert req.body["user_id"] == "u1"
    assert req.body["query"] == "发版"
    assert req.body["limit"] == 3
    await client.aclose()


async def test_envelope_error_raises_tdam_error(gateway):
    fake, endpoint = gateway
    fake.add(
        "POST",
        "/v3/atomic/search",
        {"code": 40401, "message": "not found", "request_id": "r2"},
    )
    client = make_client(endpoint)

    with pytest.raises(TDAMError) as exc_info:
        await client.search_atomic(IDS, query="x")
    assert exc_info.value.code == 40401
    assert "not found" in str(exc_info.value)
    await client.aclose()


async def test_http_401_raises_auth_error(gateway):
    fake, endpoint = gateway
    fake.add(
        "POST",
        "/v3/atomic/search",
        {"code": 401, "message": "unauthorized"},
        status=401,
    )
    client = make_client(endpoint)

    with pytest.raises(TDAMAuthError):
        await client.search_atomic(IDS, query="x")
    await client.aclose()


async def test_timeout_raises_unavailable(gateway):
    fake, endpoint = gateway
    fake.add("POST", "/v3/atomic/search", {"__sleep__": 1, "code": 0, "data": {}})
    client = make_client(endpoint, timeout_sec=0.05)

    with pytest.raises(TDAMUnavailable):
        await client.search_atomic(IDS, query="x")
    await client.aclose()


async def test_read_core_empty_returns_none(gateway):
    fake, endpoint = gateway
    fake.ok("/v3/core/read", {})
    client = make_client(endpoint)

    assert await client.read_core(IDS) is None
    await client.aclose()


async def test_add_conversation_posts_session_and_messages(gateway):
    fake, endpoint = gateway
    fake.ok("/v3/conversation/add", {"added": 2})
    client = make_client(endpoint)
    messages = [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好呀"},
    ]

    result = await client.add_conversation(IDS, session_id="s1", messages=messages)

    assert result == {"added": 2}
    assert fake.requests[0].body["session_id"] == "s1"
    assert fake.requests[0].body["messages"] == messages
    await client.aclose()


async def test_skill_listing_returns_listing_text(gateway):
    fake, endpoint = gateway
    fake.ok(
        "/v3/skill/listing", {"mode": "full", "listing": "- skill-a: 描述", "hits": []}
    )
    client = make_client(endpoint)

    assert await client.skill_listing(IDS) == "- skill-a: 描述"
    await client.aclose()


async def test_health_uses_get_without_envelope(gateway):
    fake, endpoint = gateway
    fake.add("GET", "/health", {"status": "ok", "version": "2.0.0"})
    client = make_client(endpoint)

    assert await client.health() == {"status": "ok", "version": "2.0.0"}
    await client.aclose()


async def test_conversation_count(gateway):
    fake, endpoint = gateway
    fake.ok("/v3/conversation/count", {"total": 42})
    client = make_client(endpoint)

    assert await client.conversation_count(IDS, session_id="s1") == 42
    assert fake.requests[0].body["session_id"] == "s1"
    await client.aclose()


async def test_meta_team_list_sends_user_key_header_and_user_id_body(gateway):
    fake, endpoint = gateway
    fake.ok("/v3/meta/team/list", {"items": [{"team_id": "t1"}]})
    client = make_client(endpoint)

    teams = await client.meta_team_list("usr-1", "sk-mem-x")

    assert teams == [{"team_id": "t1"}]
    req = fake.requests[0]
    assert req.headers["x-tdai-user-key"] == "sk-mem-x"
    assert req.body["user_id"] == "usr-1"
    await client.aclose()


async def test_meta_agent_list_sends_user_key_header(gateway):
    fake, endpoint = gateway
    fake.ok("/v3/meta/agent/list", {"items": [{"agent_id": "a1"}]})
    client = make_client(endpoint)

    agents = await client.meta_agent_list("t1", "sk-mem-x")

    assert agents == [{"agent_id": "a1"}]
    req = fake.requests[0]
    assert req.headers["x-tdai-user-key"] == "sk-mem-x"
    assert req.body["team_id"] == "t1"
    await client.aclose()
