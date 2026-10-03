"""身份映射：AstrBot 消息上下文 → 记忆系统隔离三元组 + session。

规则（spec §5.2）：
- team_id / agent_id 来自配置（agent_id 代表这个 AstrBot 实例本身）
- user_id：user_id_map 命中用映射值；否则 "u_" + sender_id；sender 为空回落 "default"
- session_id 直接用 unified_msg_origin（平台:消息类型:会话号，稳定且唯一）
- 群聊语义：群是 session、发言者是 user —— 群成员共享群会话，各自积累个人记忆
"""

from dataclasses import dataclass

from .client import IsolationIds
from .config import PluginConfig


@dataclass(frozen=True)
class ResolvedIdentity:
    ids: IsolationIds
    session_id: str


def resolve_identity(
    sender_id: str, unified_msg_origin: str, cfg: PluginConfig
) -> ResolvedIdentity:
    if sender_id and sender_id in cfg.user_id_map:
        user_id = cfg.user_id_map[sender_id]
    elif sender_id:
        user_id = f"u_{sender_id}"
    else:
        user_id = "default"
    return ResolvedIdentity(
        ids=IsolationIds(team_id=cfg.team_id, agent_id=cfg.agent_id, user_id=user_id),
        session_id=unified_msg_origin,
    )
