from tc_memory.capture import MAX_CONTENT_LEN, CaptureBuffer, sanitize_message
from tc_memory.client import IsolationIds

IDS = IsolationIds(team_id="t", agent_id="a", user_id="u")


class StubClient:
    def __init__(self):
        self.sent = []

    async def add_conversation(self, ids, session_id, messages):
        self.sent.append({"session_id": session_id, "messages": messages})
        return {"added": len(messages)}


def test_sanitize_strips_code_blocks():
    text = "结论如下：\n```python\nprint('hello')\n```\n完毕"
    cleaned = sanitize_message(text)
    assert "print('hello')" not in cleaned
    assert "结论如下" in cleaned
    assert "完毕" in cleaned


def test_sanitize_strips_injection_tags():
    text = "前文 <relevant-memories>\n- [episodic] 旧事\n</relevant-memories> 后文"
    cleaned = sanitize_message(text)
    assert "旧事" not in cleaned
    assert "前文" in cleaned and "后文" in cleaned


def test_sanitize_truncates_long_content():
    cleaned = sanitize_message("x" * 9000)
    assert len(cleaned) == MAX_CONTENT_LEN


async def test_flush_sends_user_then_assistant():
    client = StubClient()
    buf = CaptureBuffer()
    buf.note_user("s1", "你好，记住我喜欢咖啡")

    sent = await buf.flush(client, IDS, "s1", "好的，已记住")

    assert sent is True
    messages = client.sent[0]["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "你好，记住我喜欢咖啡"
    assert messages[1]["content"] == "好的，已记住"


async def test_flush_skips_when_user_text_empty():
    client = StubClient()
    buf = CaptureBuffer()

    sent = await buf.flush(client, IDS, "s1", "答复")

    assert sent is False
    assert client.sent == []


async def test_note_user_overwrites_previous():
    buf = CaptureBuffer()
    buf.note_user("s1", "第一条")
    buf.note_user("s1", "第二条")
    client = StubClient()

    await buf.flush(client, IDS, "s1", "答复")

    assert client.sent[0]["messages"][0]["content"] == "第二条"


async def test_buffer_cleared_after_flush():
    client = StubClient()
    buf = CaptureBuffer()
    buf.note_user("s1", "你好")
    await buf.flush(client, IDS, "s1", "答")
    await buf.flush(client, IDS, "s1", "答2")

    assert len(client.sent) == 1  # 第二次没有 user 文本，不再发送
