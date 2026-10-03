"""共享测试设施：本地假 HTTP 服务器，走真实 HTTP 栈（比 mock 保真）。"""

import asyncio
import importlib
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

PLUGIN_DIR = Path(__file__).resolve().parent.parent


def import_plugin_main():
    """以包路径导入插件入口（与 AstrBot 加载器的包语义一致，
    支持 main.py 内的相对导入）。"""
    pkg_parent = str(PLUGIN_DIR.parent)
    if pkg_parent not in sys.path:
        sys.path.insert(0, pkg_parent)
    return importlib.import_module(f"{PLUGIN_DIR.name}.main")


@dataclass
class RecordedRequest:
    method: str
    path: str
    headers: dict
    body: dict | None


@dataclass
class FakeGateway:
    """按 (method, path) 返回预置响应，并记录请求供断言。

    require_service_header_paths：仿真真实服务对 x-tdai-service-id 的强制校验，
    缺该头的请求返回 400（防契约漂移——见评审 C3）。
    """

    routes: dict = field(default_factory=dict)
    requests: list = field(default_factory=list)
    require_service_header_paths: frozenset = frozenset()

    def add(self, method: str, path: str, payload: dict, status: int = 200):
        self.routes[(method, path)] = (status, payload)

    def ok(self, path: str, data: dict):
        self.add(
            "POST",
            path,
            {"code": 0, "message": "ok", "request_id": "r1", "data": data},
        )


@pytest.fixture
async def gateway():
    fake = FakeGateway()

    async def handler(request: web.Request) -> web.Response:
        body = await request.json() if request.can_read_body else None
        fake.requests.append(
            RecordedRequest(request.method, request.path, dict(request.headers), body)
        )
        if (
            request.path in fake.require_service_header_paths
            and "x-tdai-service-id" not in request.headers
        ):
            return web.json_response(
                {"code": 400, "message": "missing x-tdai-service-id"}, status=400
            )
        route = fake.routes.get((request.method, request.path))
        if route is None:
            return web.json_response(
                {"code": 40404, "message": "no fake route"}, status=404
            )
        status, payload = route
        if payload.get("__sleep__"):
            await asyncio.sleep(payload["__sleep__"])
        return web.json_response(payload, status=status)

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    server = TestServer(app)
    await server.start_server()
    yield fake, str(server.make_url("")).rstrip("/")
    await server.close()
