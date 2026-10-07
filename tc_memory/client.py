"""MemoryCore Gateway v3 薄客户端（aiohttp，无状态）。

约定（见 MemoryCore/v3-api-memorycore-doc.md）：
- 数据面全部 POST + JSON 信封 {code, message, request_id, data}
- Header: Authorization: Bearer <apiKey> + x-tdai-service-id
- 隔离字段 team_id/agent_id/user_id 放 body
- GET /health 非 v3、无鉴权、无信封
"""

from dataclasses import dataclass
from typing import Any

import aiohttp

from .errors import TDAMAuthError, TDAMError, TDAMUnavailable


@dataclass(frozen=True)
class IsolationIds:
    """v3 数据面强制隔离三元组。"""

    team_id: str
    agent_id: str
    user_id: str

    def as_body(self) -> dict[str, str]:
        return {
            "team_id": self.team_id,
            "agent_id": self.agent_id,
            "user_id": self.user_id,
        }


class TdMemoryClient:
    def __init__(
        self,
        endpoint: str,
        api_key: str,
        service_id: str,
        timeout_sec: float = 5.0,
    ):
        self._endpoint = endpoint.rstrip("/")
        self._api_key = api_key
        self._service_id = service_id
        self._timeout = aiohttp.ClientTimeout(total=timeout_sec)
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        # 懒初始化：构造允许在同步上下文，session 必须在事件循环内创建
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "x-tdai-service-id": self._service_id,
        }

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        session = await self._get_session()
        url = f"{self._endpoint}{path}"
        try:
            async with session.post(url, json=body, headers=self._headers()) as resp:
                if resp.status in (401, 403):
                    raise TDAMAuthError(resp.status, f"鉴权失败 HTTP {resp.status}")
                if resp.status >= 500:
                    raise TDAMUnavailable(f"服务错误 HTTP {resp.status}")
                payload = await resp.json(content_type=None)
        except TDAMError:
            raise
        except (aiohttp.ClientError, TimeoutError) as e:
            raise TDAMUnavailable(f"Gateway 不可达: {type(e).__name__}") from e
        code = payload.get("code", -1)
        if code != 0:
            raise TDAMError(code, payload.get("message", "unknown error"))
        return payload.get("data") or {}

    async def health(self) -> dict[str, Any]:
        session = await self._get_session()
        try:
            async with session.get(f"{self._endpoint}/health") as resp:
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as e:
            raise TDAMUnavailable(f"Gateway 不可达: {type(e).__name__}") from e

    # ── L1 原子记忆 ──────────────────────────────────────────

    async def search_atomic(
        self, ids: IsolationIds, query: str, limit: int = 5
    ) -> list[dict]:
        data = await self._post(
            "/v3/atomic/search",
            {"query": query, "limit": limit, **ids.as_body()},
        )
        return data.get("items") or []

    async def atomic_query(
        self,
        ids: IsolationIds,
        type: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict:
        body: dict[str, Any] = {"limit": limit, "offset": offset, **ids.as_body()}
        if type:
            body["type"] = type
        return await self._post("/v3/atomic/query", body)

    async def atomic_delete(self, ids: IsolationIds, memory_ids: list[str]) -> int:
        data = await self._post(
            "/v3/atomic/delete", {"ids": memory_ids, **ids.as_body()}
        )
        return data.get("deleted_count", 0)

    async def atomic_count(self, ids: IsolationIds) -> int:
        data = await self._post("/v3/atomic/count", ids.as_body())
        return data.get("total", 0)

    # ── L2 场景 / L3 画像 ────────────────────────────────────

    async def list_scenarios(self, ids: IsolationIds) -> list[dict]:
        data = await self._post("/v3/scenario/ls", ids.as_body())
        return data.get("entries") or []

    async def read_core(self, ids: IsolationIds) -> str | None:
        data = await self._post("/v3/core/read", ids.as_body())
        return data.get("content") or None

    async def core_count(self, ids: IsolationIds) -> int:
        data = await self._post("/v3/core/count", ids.as_body())
        return data.get("total", 0)

    # ── L0 会话 ─────────────────────────────────────────────

    async def add_conversation(
        self, ids: IsolationIds, session_id: str, messages: list[dict]
    ) -> dict:
        return await self._post(
            "/v3/conversation/add",
            {"session_id": session_id, "messages": messages, **ids.as_body()},
        )

    async def conversation_search(
        self, ids: IsolationIds, query: str, limit: int = 5
    ) -> list[dict]:
        data = await self._post(
            "/v3/conversation/search",
            {"query": query, "limit": limit, **ids.as_body()},
        )
        return data.get("messages") or []

    async def conversation_query(
        self, ids: IsolationIds, session_id: str, limit: int = 100, offset: int = 0
    ) -> dict:
        return await self._post(
            "/v3/conversation/query",
            {
                "session_id": session_id,
                "limit": limit,
                "offset": offset,
                **ids.as_body(),
            },
        )

    async def conversation_delete(
        self, ids: IsolationIds, message_ids: list[str]
    ) -> int:
        data = await self._post(
            "/v3/conversation/delete",
            {"message_ids": message_ids, **ids.as_body()},
        )
        return data.get("deleted_count", 0)

    async def conversation_count(
        self, ids: IsolationIds, session_id: str | None = None
    ) -> int:
        body: dict[str, Any] = dict(ids.as_body())
        if session_id:
            body["session_id"] = session_id
        data = await self._post("/v3/conversation/count", body)
        return data.get("total", 0)

    # ── Skill ───────────────────────────────────────────────

    async def skill_listing(self, ids: IsolationIds) -> str | None:
        data = await self._post("/v3/skill/listing", ids.as_body())
        return data.get("listing") or None

    async def skill_search(
        self, ids: IsolationIds, query: str, limit: int = 10
    ) -> list[dict]:
        data = await self._post(
            "/v3/skill/search",
            {"query": query, "top_k": limit, **ids.as_body()},
        )
        return data.get("items") or []

    async def skill_get_by_name(self, ids: IsolationIds, name: str) -> dict | None:
        try:
            return await self._post(
                "/v3/skill/get-by-name",
                {"skill_name": name, **ids.as_body()},
            )
        except TDAMError as e:
            if e.code == 40401:  # 找不到 name 的统一错误码，不暴露存在性
                return None
            raise

    # ── Meta / 运维（面板引导用）──────────────────────────────

    async def meta_auth_verify(self, user_key: str) -> bool:
        """校验 user_key 是否合法。注意：非法 key 也是 code=0，须看 data.valid。"""
        data = await self._post("/v3/meta/auth/verify", {"user_key": user_key})
        return bool(data.get("valid"))

    async def meta_user_of_key(self, user_key: str) -> dict | None:
        """取 user_key 对应的用户（无效返回 None）。"""
        data = await self._post("/v3/meta/auth/verify", {"user_key": user_key})
        return data.get("user") if data.get("valid") else None

    async def meta_team_list(self) -> list[dict]:
        data = await self._post("/v3/meta/team/list", {"limit": 20})
        return data.get("items") or []

    async def meta_agent_list(self, team_id: str, limit: int = 20) -> list[dict]:
        data = await self._post(
            "/v3/meta/agent/list", {"team_id": team_id, "limit": limit}
        )
        return data.get("items") or []

    async def internal_init_admin(self, username: str, user_key: str) -> dict:
        """初始化 system_admin（仅 Bearer 鉴权的运维接口；重复初始化报 409）。"""
        return await self._post(
            "/v3/internal/meta/user/init-admin",
            {"username": username, "user_key": user_key},
        )

    async def aclose(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
