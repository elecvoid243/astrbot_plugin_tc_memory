"""MemoryKnowledge 服务薄客户端（:8421/v3，与 Core 同为 code 信封）。

注意：wiki / code-graph 是「知识实例」粒度——search/read 必须携带
wiki_id / code_graph_id（在面板创建知识库后获得，插件配置注入）。
"""

from typing import Any

import aiohttp

from .errors import TDAMError, TDAMUnavailable


class KnowledgeClient:
    def __init__(self, endpoint: str, service_id: str, timeout_sec: float = 5.0):
        self._endpoint = endpoint.rstrip("/")
        # MemoryKnowledge 所有 /v3/* 路由强制校验 x-tdai-service-id（缺则 400）
        self._service_id = service_id
        self._timeout = aiohttp.ClientTimeout(total=timeout_sec)
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        session = await self._get_session()
        try:
            async with session.post(
                f"{self._endpoint}{path}",
                json=body,
                headers={"x-tdai-service-id": self._service_id},
            ) as resp:
                if resp.status >= 500:
                    raise TDAMUnavailable(f"知识服务错误 HTTP {resp.status}")
                payload = await resp.json(content_type=None)
        except TDAMError:
            raise
        except (aiohttp.ClientError, TimeoutError) as e:
            raise TDAMUnavailable(f"知识服务不可达: {type(e).__name__}") from e
        code = payload.get("code", -1)
        if code != 0:
            raise TDAMError(code, payload.get("message", "unknown error"))
        return payload.get("data") or {}

    async def wiki_search(self, wiki_id: str, query: str, limit: int = 5) -> list[dict]:
        data = await self._post(
            "/v3/wiki/search",
            {"wiki_id": wiki_id, "query": query, "limit": limit},
        )
        return data.get("results") or []

    async def wiki_read(self, wiki_id: str, refs: str | list[str]) -> list[dict]:
        if isinstance(refs, str):
            refs = [refs]
        data = await self._post(
            "/v3/wiki/page/read", {"wiki_id": wiki_id, "refs": refs}
        )
        # 真实契约：data.items = [{ref, content} | {ref, not_found: true}]
        return [item for item in (data.get("items") or []) if not item.get("not_found")]

    async def codegraph_search(
        self, code_graph_id: str, query: str, limit: int = 10
    ) -> str:
        data = await self._post(
            "/v3/code-graph/search",
            {"code_graph_id": code_graph_id, "query": query, "limit": limit},
        )
        return self._query_text(data)

    async def codegraph_explore(self, code_graph_id: str, query: str) -> str:
        data = await self._post(
            "/v3/code-graph/explore",
            {"code_graph_id": code_graph_id, "query": query},
        )
        return self._query_text(data)

    @staticmethod
    def _query_text(data: dict) -> str:
        """code-graph 查询接口返回预渲染文本：{text, isError}。"""
        if data.get("isError"):
            raise TDAMError(422, data.get("text") or "code-graph 查询失败")
        return data.get("text") or ""

    async def aclose(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
