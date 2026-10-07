"""team_ 前缀统一命名 + 注入文案消毒（区别于 AstrBot 原生 SKILL.md 技能）测试。"""

from tc_memory.inject_format import render_skill_block, sanitize_listing

# 与 MemoryCore 服务端真实文案同构的样本（skill-listing-prompt.ts）
SERVER_SAMPLE = (
    "## Skills (mandatory)\n"
    "Before replying, scan the skills below. If a skill matches or is even partially relevant "
    "to your task, you MUST load it with skill_view(name) and follow its instructions.\n"
    "If a skill has issues, fix it with skill_manage(action='patch').\n"
    "After difficult/iterative tasks, offer to save as a skill.\n"
    "<available_skills>\n"
    "- deploy: 部署流程\n"
    "</available_skills>\n"
    "Only proceed without loading a skill if genuinely none are relevant to the task."
)


def test_sanitize_listing_renames_and_drops_dangling_refs():
    out = sanitize_listing(SERVER_SAMPLE)
    # 标题与 AstrBot 原生 "## Skills" 区分
    assert "## Team Skills" in out
    assert "## Skills (mandatory)" not in out
    # 工具引用改名
    assert "team_skill_view(name)" in out
    assert "skill_view(name)" not in out.replace("team_skill_view(name)", "")
    # 悬空工具 skill_manage 整句删除
    assert "skill_manage" not in out
    # 剥掉服务端内层 <available_skills> 标签（M1：避免双重嵌套）
    assert "<available_skills>" not in out
    assert "</available_skills>" not in out
    # 实际条目保留
    assert "- deploy: 部署流程" in out


def test_sanitize_listing_tolerates_unknown_server_text():
    # 服务端改版（没有可替换的模式）时不崩、原样返回
    assert sanitize_listing("随便什么内容") == "随便什么内容"
    assert sanitize_listing("") == ""


def test_render_skill_block_uses_team_skills_tag_and_guidance():
    block = render_skill_block(SERVER_SAMPLE)
    assert block.startswith("<team_skills>")
    assert block.endswith("</team_skills>")
    assert "<available_skills>" not in block
    # 引导语明确区分体系并指向正确的工具名
    assert "team_skill_view" in block
    assert "AstrBot 内置" in block  # 明确与 AstrBot 原生技能无关


def test_render_skill_block_empty_returns_none():
    assert render_skill_block(None) is None
    assert render_skill_block("") is None
