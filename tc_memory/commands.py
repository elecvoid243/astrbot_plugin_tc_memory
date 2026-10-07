"""/memory 指令组业务逻辑（不依赖 AstrBot；main.py 注册指令薄封装）。

服务不可达时一律返回含启动指引的提示，而不是抛出。
"""

import time

from .errors import TDAMError
from .runtime import PluginRuntime

_UNAVAILABLE = (
    "⚠️ 记忆服务不可达。请先启动服务：\n"
    "· Docker 一键：deploy/global-images/start-all.sh\n"
    "· 或 standalone：MemoryCore/tdai-gateway.standalone.yaml\n"
    "然后在插件配置中确认 core_endpoint / core_api_key。"
)


def _identity(runtime: PluginRuntime, sender_id: str, umo: str):
    return runtime.identity_for(sender_id, umo)


async def cmd_search(
    runtime: PluginRuntime, sender_id: str, umo: str, query: str
) -> str:
    identity = _identity(runtime, sender_id, umo)
    try:
        items = await runtime.core.search_atomic(identity.ids, query, limit=10)
    except TDAMError:
        return _UNAVAILABLE
    if not items:
        return "没有找到相关记忆。"
    lines = [f"找到 {len(items)} 条相关记忆："]
    for it in items:
        tag = f"[{it['type']}]" if it.get("type") else ""
        lines.append(f"- `{it['id']}` {tag} {it.get('content', '')}".replace("  ", " "))
    return "\n".join(lines)


async def cmd_list(runtime: PluginRuntime, sender_id: str, umo: str) -> str:
    identity = _identity(runtime, sender_id, umo)
    try:
        data = await runtime.core.atomic_query(identity.ids, limit=10)
    except TDAMError:
        return _UNAVAILABLE
    items = data.get("items") or []
    if not items:
        return "你还没有记忆。聊几句或 /memory remember <内容> 试试。"
    lines = [f"最近 {len(items)} 条记忆（共 {data.get('total', 0)} 条）："]
    for it in items:
        tag = f"[{it['type']}]" if it.get("type") else ""
        lines.append(f"- `{it['id']}` {tag} {it.get('content', '')}".replace("  ", " "))
    lines.append("\n删除：/memory forget <id>")
    return "\n".join(lines)


async def cmd_remember(
    runtime: PluginRuntime, sender_id: str, umo: str, content: str
) -> str:
    """v3 无 atomic/create：写一条 L0 消息，由服务端管线异步抽取成记忆。"""
    identity = _identity(runtime, sender_id, umo)
    try:
        await runtime.core.add_conversation(
            identity.ids,
            identity.session_id,
            [{"role": "user", "content": f"请记住：{content}"}],
        )
    except TDAMError:
        return _UNAVAILABLE
    return "已提交，记忆稍后生效（由服务端自动提炼）。"


async def cmd_forget(
    runtime: PluginRuntime, sender_id: str, umo: str, memory_id: str
) -> str:
    identity = _identity(runtime, sender_id, umo)
    try:
        deleted = await runtime.core.atomic_delete(identity.ids, [memory_id])
    except TDAMError:
        return _UNAVAILABLE
    if deleted > 0:
        return f"已删除记忆 {memory_id}。"
    return f"未找到记忆 {memory_id}（可用 /memory list 查看现有记忆 id）。"


async def cmd_status(runtime: PluginRuntime, sender_id: str, umo: str) -> str:
    identity = _identity(runtime, sender_id, umo)
    try:
        health = await runtime.core.health()
        atomic = await runtime.core.atomic_count(identity.ids)
        core = await runtime.core.core_count(identity.ids)
        conv = await runtime.core.conversation_count(identity.ids)
    except TDAMError:
        return _UNAVAILABLE
    return (
        f"记忆服务状态：{health.get('status', 'unknown')}"
        f"（版本 {health.get('version', '?')}）\n"
        f"你的记忆统计：L0 对话 {conv} 条 · L1 记忆 {atomic} 条 · L3 画像 {core} 份"
    )


class ClearConfirmer:
    """/memory clear 两步确认：先 arm（返回警示），confirm 时须已 arm。

    挂起态按 session 记录，60s 过期。
    """

    TTL_SEC = 60.0

    def __init__(self):
        self._armed: dict[str, float] = {}

    def arm(self, session_id: str) -> None:
        self._armed[session_id] = time.monotonic()

    def consume(self, session_id: str) -> bool:
        armed_at = self._armed.pop(session_id, None)
        return armed_at is not None and time.monotonic() - armed_at < self.TTL_SEC


async def cmd_clear(
    runtime: PluginRuntime,
    sender_id: str,
    umo: str,
    confirmer: ClearConfirmer,
    confirm: bool,
) -> str:
    identity = _identity(runtime, sender_id, umo)
    if not confirm:
        confirmer.arm(identity.session_id)
        return (
            "⚠️ 将清空当前会话的全部对话记录（L0），记忆条目不受影响。\n"
            "确认请发送：/memory clear confirm"
        )
    if not confirmer.consume(identity.session_id):
        return "请先发送 /memory clear 查看确认提示。"
    try:
        data = await runtime.core.conversation_query(
            identity.ids, identity.session_id, limit=100
        )
        msg_ids = [m["id"] for m in data.get("messages") or [] if m.get("id")]
        if not msg_ids:
            return "当前会话没有可清空的对话记录。"
        deleted = await runtime.core.conversation_delete(identity.ids, msg_ids)
    except TDAMError:
        return _UNAVAILABLE
    return f"已清空当前会话的 {deleted} 条对话记录。"
