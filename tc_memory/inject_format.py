"""注入文本渲染（纯函数，不依赖 AstrBot / 网络）。

结构对齐官方 openclaw-plugin 的 format.ts：
- openclaw 把 L1 放用户消息前、persona/scene 拼进 system prompt
- AstrBot 侧按设计铁律不动 system_prompt，全部合并为一个 user-side extra part，
  由 main.py 决定 mark_as_temp（不落盘）或落盘

工具指南中的工具名使用本插件注册的 llm_tool 名（memory_search 等），
与 openclaw 版（tdai_*）不同——模型看到的是真实可调用的工具。
"""

import re

# 工具调用预算提示：防止模型在记忆工具上无限重试（对齐官方插件的 3 次限制）
_TOOLS_GUIDE = """<memory-tools-guide>
当上方注入的记忆不足以回答时，可主动调用工具获取更多信息：
- memory_search：搜索结构化记忆（用户偏好、历史事件、规则）
- conversation_search：搜索原始对话（具体消息原文、时间线）
每轮对话中两者合计最多调用 3 次；仍无结果则说明信息不在记忆中，直接根据已有信息回复。
</memory-tools-guide>"""


def render_memory_block(
    l1_items: list[dict], persona: str | None, scenes: list[dict]
) -> str | None:
    """persona(L3) + L1 列表 + 场景导航(L2) + 工具指南。三路全空 → None。"""
    if not l1_items and not persona and not scenes:
        return None

    parts: list[str] = []

    if persona:
        parts.append(f"<user-persona>\n{persona}\n</user-persona>")

    if l1_items:
        lines = ["<relevant-memories>", ""]
        for item in l1_items:
            type_tag = f"[{item['type']}]" if item.get("type") else ""
            lines.append(f"- {type_tag} {item['content']}".replace("  ", " "))
        lines.append("")
        lines.append("</relevant-memories>")
        parts.append("\n".join(lines))

    if scenes:
        lines = ["## 场景记忆索引（可深入探索的相关经历）", ""]
        lines.extend(f"- `{s['path']}`" for s in scenes)
        parts.append("\n".join(lines))

    parts.append(_TOOLS_GUIDE)
    return "\n\n".join(parts)


def sanitize_listing(text: str) -> str:
    """对服务端预渲染的技能清单做消毒（幂等、容忍改版）：

    1. 标题 `## Skills` → `## Team Skills`：与 AstrBot 原生「## Skills」段区分
    2. `skill_view(` → `team_skill_view(`：防止指向未注册的旧工具名
    3. 删除 `skill_manage(...)` 悬空引用（本插件从未注册该工具）
    4. 剥掉服务端内层 `<available_skills>` 标签（避免与本插件外层双重嵌套）
    """
    if not text:
        return text
    text = text.replace("## Skills (mandatory)", "## Team Skills (mandatory)")
    text = text.replace("skill_view(", "team_skill_view(")
    text = re.sub(r"[^\n]*skill_manage[^\n]*\n?", "", text)
    text = text.replace("<available_skills>", "").replace("</available_skills>", "")
    return text


def render_skill_block(listing_text: str | None) -> str | None:
    """包装服务端技能清单为 <team_skills> 块；为空 → None。

    文案明确与 AstrBot 内置 SKILL.md 技能体系区分（那是文件读取，
    这里是记忆系统的内核资产，用 team_skill_view 加载全文）。
    """
    if not listing_text or not listing_text.strip():
        return None
    cleaned = sanitize_listing(listing_text).strip()
    if not cleaned:
        return None
    return (
        "<team_skills>\n"
        "以下是团队记忆系统装备的技能（独立于 AstrBot 内置 SKILL.md 技能）。\n"
        "相关时用 team_skill_view 加载全文；也可用 team_skill_search 检索团队技能。\n\n"
        f"{cleaned}\n"
        "</team_skills>"
    )


def render_injection(memory_block: str | None, skill_block: str | None) -> str | None:
    """组合最终注入文本：skill 块在前（先看到可用技能），memory 块在后。全空 → None。"""
    parts = [b for b in (skill_block, memory_block) if b]
    return "\n\n".join(parts) if parts else None
