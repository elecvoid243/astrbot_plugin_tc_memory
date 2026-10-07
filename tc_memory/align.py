"""local 模式身份对齐：采用面板（meta 面）的 team/agent/user 三元组。

原因：面板按 asset_id 反解 (team_id, agent_id) 并用 asset.owner_user_id
作为数据面 user_id 查询；插件若用自编的 default/default 桶，面板永远读不到。

对齐规则：
- team：meta team/list 的第一条（面板自举创建的默认团队）
- agent：该团队下 agent/list 的第一条
- user：admin key 对应的 user_id（= chat_memory asset 的 owner）
任何一步缺失则放弃对齐（回退插件配置的三元组），并打印原因由上层决定。
"""

import logging

from .client import IsolationIds, TdMemoryClient

logger = logging.getLogger(__name__)


async def resolve_panel_identities(
    client: TdMemoryClient, admin_user_key: str
) -> IsolationIds | None:
    # 顺序有讲究：auth/verify 免 user-key 头，先拿 user_id；team/list 需要
    # body.user_id + header x-tdai-user-key 两者齐备（缺任一 → 400/401）
    user = await client.meta_user_of_key(admin_user_key)
    if not user:
        return None
    user_id = user.get("user_id") or ""
    if not user_id:
        return None

    teams = await client.meta_team_list(user_id, admin_user_key)
    if not teams:
        return None
    team_id = teams[0].get("team_id") or ""

    agents = await client.meta_agent_list(team_id, admin_user_key)
    if not agents:
        return None
    agent_id = agents[0].get("agent_id") or ""

    if not (team_id and agent_id):
        return None
    return IsolationIds(team_id=team_id, agent_id=agent_id, user_id=user_id)
