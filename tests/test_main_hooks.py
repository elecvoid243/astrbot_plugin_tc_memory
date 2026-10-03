"""main.py AstrBot 适配薄层测试（需要 AstrBot 环境，无则跳过）。"""

from types import SimpleNamespace

import pytest

pytest.importorskip("astrbot.core.agent.message")

from astrbot.core.agent.message import TextPart

from tests.conftest import import_plugin_main

_main = import_plugin_main()
build_injection_part = _main.build_injection_part
extract_assistant_text = _main.extract_assistant_text


def test_build_injection_part_marks_temp_by_default():
    part = build_injection_part("记忆内容", persist=False)
    assert isinstance(part, TextPart)
    assert part.text == "记忆内容"
    assert part._no_save is True


def test_build_injection_part_persist_mode_not_marked():
    part = build_injection_part("记忆内容", persist=True)
    assert part._no_save is False


def test_extract_assistant_text_from_result_chain():
    chain = SimpleNamespace(get_plain_text=lambda: "最终答复")
    resp = SimpleNamespace(result_chain=chain, completion_text="")
    assert extract_assistant_text(resp) == "最终答复"


def test_extract_assistant_text_falls_back_to_completion_text():
    resp = SimpleNamespace(result_chain=None, completion_text="兜底文本")
    assert extract_assistant_text(resp) == "兜底文本"
