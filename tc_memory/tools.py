"""8 个 LLM 工具的业务逻辑（不依赖 AstrBot；main.py 里做 @filter.llm_tool 薄封装）。

约定：工具永不抛异常——失败返回友好文本，避免打断 Agent 工具循环。
"""

import logging

from .errors import TDAMError
from .identity import resolve_identity
from .logutil import short, vlog
from .runtime import PluginRuntime

logger = logging.getLogger(__name__)

_UNAVAILABLE = "记忆服务暂时不可用，请直接根据已有信息回答，稍后可重试。"
_KNOWLEDGE_DISABLED = "知识库功能未启用（knowledge_enabled=false）。"


def _ids(runtime: PluginRuntime, sender_id: str, umo: str):
    return resolve_identity(sender_id, umo, runtime.cfg).ids


async def memory_search(
    runtime: PluginRuntime, sender_id: str, umo: str, query: str
) -> str:
    vlog(logger, runtime.cfg, "memory_search query=%r", short(query, 80))
    try:
        items = await runtime.core.search_atomic(_ids(runtime, sender_id, umo), query)
    except TDAMError:
        return _UNAVAILABLE
    if not items:
        return "没有找到相关记忆。"
    lines = ["找到以下相关记忆："]
    for i, it in enumerate(items, 1):
        tag = f"[{it['type']}]" if it.get("type") else ""
        lines.append(f"{i}. {tag} {it.get('content', '')}".replace("  ", " "))
    return "\n".join(lines)


async def conversation_search(
    runtime: PluginRuntime, sender_id: str, umo: str, query: str
) -> str:
    vlog(logger, runtime.cfg, "conversation_search query=%r", short(query, 80))
    try:
        items = await runtime.core.conversation_search(
            _ids(runtime, sender_id, umo), query
        )
    except TDAMError:
        return _UNAVAILABLE
    if not items:
        return "没有找到相关对话记录。"
    lines = ["找到以下对话记录："]
    for i, it in enumerate(items, 1):
        ts = it.get("created_at") or it.get("recorded_at") or ""
        lines.append(f"{i}. [{it.get('role', '?')}] {it.get('content', '')} ({ts})")
    return "\n".join(lines)


async def skill_search(
    runtime: PluginRuntime, sender_id: str, umo: str, query: str
) -> str:
    try:
        items = await runtime.core.skill_search(_ids(runtime, sender_id, umo), query)
    except TDAMError:
        return _UNAVAILABLE
    if not items:
        return "没有找到相关技能。"
    lines = ["找到以下技能（用 skill_view 加载详情）："]
    lines.extend(
        f"- {it.get('name', '?')}: {it.get('description', '')}" for it in items
    )
    return "\n".join(lines)


async def skill_view(
    runtime: PluginRuntime, sender_id: str, umo: str, name: str
) -> str:
    try:
        skill = await runtime.core.skill_get_by_name(
            _ids(runtime, sender_id, umo), name
        )
    except TDAMError:
        return _UNAVAILABLE
    if skill is None:
        return f"未找到名为 {name} 的技能。"
    return skill.get("content") or f"技能 {name} 没有正文内容。"


def _knowledge_guard(
    runtime: PluginRuntime, instance_id: str, field: str
) -> str | None:
    """返回 None 表示可用；否则返回给模型的提示文本。"""
    if not runtime.cfg.knowledge_enabled or runtime.knowledge is None:
        return _KNOWLEDGE_DISABLED
    if not instance_id:
        return f"未配置知识实例 ID（请在插件配置中填写 {field}）。"
    return None


async def wiki_search(
    runtime: PluginRuntime, sender_id: str, umo: str, query: str
) -> str:
    if hint := _knowledge_guard(
        runtime, runtime.cfg.knowledge_wiki_id, "knowledge_wiki_id"
    ):
        return hint
    vlog(logger, runtime.cfg, "wiki_search query=%r", short(query, 80))
    try:
        results = await runtime.knowledge.wiki_search(
            runtime.cfg.knowledge_wiki_id, query
        )
    except TDAMError:
        return _UNAVAILABLE
    if not results:
        return "知识库中没有找到相关内容。"
    lines = ["找到以下知识页面（用 wiki_read 读取全文）："]
    lines.extend(f"- {r.get('title', '?')}（{r.get('path', '')}）" for r in results)
    return "\n".join(lines)


async def wiki_read(runtime: PluginRuntime, sender_id: str, umo: str, path: str) -> str:
    if hint := _knowledge_guard(
        runtime, runtime.cfg.knowledge_wiki_id, "knowledge_wiki_id"
    ):
        return hint
    vlog(logger, runtime.cfg, "wiki_read path=%r", short(path, 80))
    try:
        pages = await runtime.knowledge.wiki_read(runtime.cfg.knowledge_wiki_id, path)
    except TDAMError:
        return _UNAVAILABLE
    if not pages:
        return f"页面 {path} 不存在或为空。"
    return "\n\n".join(p.get("content", "") for p in pages)


async def codegraph_search(
    runtime: PluginRuntime, sender_id: str, umo: str, query: str
) -> str:
    if hint := _knowledge_guard(
        runtime, runtime.cfg.knowledge_codegraph_id, "knowledge_codegraph_id"
    ):
        return hint
    vlog(logger, runtime.cfg, "codegraph_search query=%r", short(query, 80))
    try:
        # code-graph 查询接口返回预渲染文本，直接透传给模型
        text = await runtime.knowledge.codegraph_search(
            runtime.cfg.knowledge_codegraph_id, query
        )
    except TDAMError:
        return _UNAVAILABLE
    return text or "代码图谱中没有找到相关符号。"


async def codegraph_explore(
    runtime: PluginRuntime, sender_id: str, umo: str, symbol: str
) -> str:
    if hint := _knowledge_guard(
        runtime, runtime.cfg.knowledge_codegraph_id, "knowledge_codegraph_id"
    ):
        return hint
    vlog(logger, runtime.cfg, "codegraph_explore symbol=%r", short(symbol, 80))
    try:
        text = await runtime.knowledge.codegraph_explore(
            runtime.cfg.knowledge_codegraph_id, symbol
        )
    except TDAMError:
        return _UNAVAILABLE
    return text or f"没有探索到与 {symbol} 相关的内容。"
