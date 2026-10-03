"""对话捕获：本轮 user/assistant 消息清洗后写回 Gateway（触发 L1 异步抽取）。

对齐 openclaw-plugin sanitize.ts 的思路：
- 去 fenced code block（代码原文对长期记忆是噪声且占配额）
- 去注入标签段（防御：persist_injected_memory=true 时历史里可能含注入块，
  若上游把整段历史传进来，录进 L0 会造成自我引用）
- 截断：Gateway 单条 content 上限 8192 字，留余量取 8000
"""

import logging
import re

from .client import IsolationIds, TdMemoryClient

logger = logging.getLogger(__name__)

MAX_CONTENT_LEN = 8000

_CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```", re.MULTILINE)
_INJECTION_TAG_RE = re.compile(
    r"<(relevant-memories|available_skills|user-persona|memory-tools-guide)>"
    r"[\s\S]*?</\1>",
    re.MULTILINE,
)


def sanitize_message(text: str) -> str:
    cleaned = _CODE_BLOCK_RE.sub("[代码块已省略]", text)
    cleaned = _INJECTION_TAG_RE.sub("", cleaned)
    cleaned = cleaned.strip()
    if len(cleaned) > MAX_CONTENT_LEN:
        cleaned = cleaned[:MAX_CONTENT_LEN]
    return cleaned


class CaptureBuffer:
    """按 session 暂存本轮 user 文本，flush 时与 assistant 答复配对写回。

    note_user 覆盖式保留最新文本：同一 session 连续两条消息时，
    只捕获最后一轮（中间轮次大概率已被新消息取代上下文）。
    """

    def __init__(self):
        self._pending: dict[str, str] = {}

    def note_user(self, session_id: str, text: str) -> None:
        if text and text.strip():
            self._pending[session_id] = text.strip()

    async def flush(
        self,
        client: TdMemoryClient,
        ids: IsolationIds,
        session_id: str,
        assistant_text: str,
    ) -> bool:
        """配对写回；任一侧清洗后为空则不发。返回是否实际写入。"""
        user_text = self._pending.pop(session_id, None)
        if not user_text:
            return False
        user_clean = sanitize_message(user_text)
        assistant_clean = sanitize_message(assistant_text or "")
        if not user_clean or not assistant_clean:
            return False
        await client.add_conversation(
            ids,
            session_id,
            [
                {"role": "user", "content": user_clean},
                {"role": "assistant", "content": assistant_clean},
            ],
        )
        return True
