"""召回流水线：四路并行（L1 搜索 / L3 画像 / L2 场景 / Skill 清单），降级优先。

设计要点：
- 空 query 跳过 L1 搜索（空 query 会被 Gateway 拒绝，且纯图片消息无可搜内容）
- persona / skill listing 变化慢，走 TTLCache（600s），显著降低每轮调用数
- 任一路失败只缺该路内容，整体永不抛异常——聊天可用性高于记忆完整性
"""

import asyncio

from .cache import TTLCache
from .client import TdMemoryClient
from .config import PluginConfig
from .errors import TDAMAuthError
from .identity import ResolvedIdentity
from .inject_format import render_injection, render_memory_block, render_skill_block
from .logutil import get_logger, short, vlog

logger = get_logger(__name__)

PERSONA_TTL_SEC = 600.0
SKILL_LISTING_TTL_SEC = 600.0


async def perform_recall(
    client: TdMemoryClient,
    cache: TTLCache,
    identity: ResolvedIdentity,
    query: str,
    cfg: PluginConfig,
) -> str | None:
    """召回并渲染注入文本；全部失败/为空 → None（本轮不注入）。"""
    ids = identity.ids

    async def _search():
        return await client.search_atomic(
            ids, query=query, limit=cfg.recall_max_results
        )

    vlog(logger, cfg, "召回 query=%r", short(query, 80))

    async def _persona():
        key = f"persona:{ids.team_id}:{ids.user_id}"
        cached = cache.get(key)
        if cached is not None:
            vlog(logger, cfg, "L3 画像缓存命中")
            return cached
        value = await client.read_core(ids)
        if value:
            cache.set(key, value, PERSONA_TTL_SEC)
        return value

    async def _scenes():
        return await client.list_scenarios(ids)

    async def _listing():
        key = f"skills:{ids.team_id}:{ids.agent_id}"
        cached = cache.get(key)
        if cached is not None:
            vlog(logger, cfg, "Skill 清单缓存命中")
            return cached
        value = await client.skill_listing(ids)
        if value:
            cache.set(key, value, SKILL_LISTING_TTL_SEC)
        return value

    # 空 query（纯图片/@消息等）跳过搜索，其余三路照常
    tasks = [_persona(), _scenes(), _listing()]
    if query and query.strip():
        tasks.append(_search())

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # 鉴权失败是配置级故障（四路必然全挂），向上传播让 runtime 进入退避，
    # 而不是按单路降级静默吞掉
    for r in results:
        if isinstance(r, TDAMAuthError):
            raise r

    persona, scenes, listing = results[0], results[1], results[2]
    l1_items = results[3] if len(results) > 3 else []

    for name, r in zip(
        ("persona", "scenes", "listing", "search"), results, strict=False
    ):
        if isinstance(r, BaseException):
            logger.warning("recall 单路降级 [%s]: %s", name, type(r).__name__)

    l1_items = l1_items if isinstance(l1_items, list) else []
    persona = persona if isinstance(persona, str) else None
    scenes = scenes if isinstance(scenes, list) else []
    listing = listing if isinstance(listing, str) else None

    # ── 详细日志：各路面貌 + 注入量 ──────────────────────
    vlog(logger, cfg, "L1 命中 %d 条", len(l1_items))
    for it in l1_items:
        vlog(
            logger,
            cfg,
            "  · %s[%s] %s",
            it.get("id", "?"),
            it.get("type", "-"),
            short(it.get("content", "")),
        )
    vlog(logger, cfg, "L3 画像: %s", short(persona) or "(无)")
    vlog(
        logger,
        cfg,
        "L2 场景 %d 个: %s",
        len(scenes),
        short(", ".join(s.get("path", "?") for s in scenes), 200) or "(无)",
    )
    vlog(logger, cfg, "Skill 清单: %s", short(listing, 200) or "(无)")

    result = render_injection(
        render_memory_block(l1_items, persona, scenes),
        render_skill_block(listing),
    )
    vlog(logger, cfg, "注入文本 %d 字符", len(result) if result else 0)
    return result
