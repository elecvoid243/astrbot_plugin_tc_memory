from tc_memory.inject_format import (
    render_injection,
    render_memory_block,
    render_skill_block,
)


def test_memory_block_all_empty_returns_none():
    assert render_memory_block([], None, []) is None


def test_memory_block_persona_only():
    block = render_memory_block([], "用户是后端工程师", [])
    assert "<user-persona>" in block
    assert "用户是后端工程师" in block
    assert "<relevant-memories>" not in block


def test_memory_block_l1_items_with_type_tag():
    items = [
        {"id": "r1", "content": "下周一发版", "type": "episodic", "score": 0.9},
        {"id": "r2", "content": "偏好简洁回复", "type": "persona"},
    ]
    block = render_memory_block(items, None, [])
    assert "<relevant-memories>" in block
    assert "- [episodic] 下周一发版" in block
    assert "- [persona] 偏好简洁回复" in block


def test_memory_block_scene_navigation():
    scenes = [{"path": "scene_blocks/payment.md"}, {"path": "scene_blocks/auth.md"}]
    block = render_memory_block([], None, scenes)
    assert "scene_blocks/payment.md" in block
    assert "scene_blocks/auth.md" in block


def test_memory_block_includes_tools_guide_with_our_tool_names():
    block = render_memory_block([], None, [])
    # 全空时为 None；有任一路内容时才带工具指南
    assert block is None
    block = render_memory_block([], "画像", [])
    assert "memory_search" in block
    assert "conversation_search" in block


def test_skill_block_empty_returns_none():
    assert render_skill_block(None) is None
    assert render_skill_block("") is None


def test_skill_block_wraps_listing():
    block = render_skill_block("- skill-a: 部署流程")
    assert "<team_skills>" in block
    assert "</team_skills>" in block
    assert "- skill-a: 部署流程" in block
    assert "team_skill_view" in block  # 引导 LLM 用我们的工具加载 skill


def test_render_injection_skill_before_memory():
    result = render_injection("MEMORY_BLOCK", "SKILL_BLOCK")
    assert result.index("SKILL_BLOCK") < result.index("MEMORY_BLOCK")


def test_render_injection_both_empty_returns_none():
    assert render_injection(None, None) is None
