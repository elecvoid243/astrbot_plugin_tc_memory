from tc_memory import commands
from tc_memory.cache import TTLCache
from tc_memory.capture import CaptureBuffer
from tc_memory.config import config_from_astrbot
from tc_memory.errors import TDAMUnavailable
from tc_memory.runtime import PluginRuntime


class StubCore:
    def __init__(self):
        self.health_ok = True
        self.added = []
        self.deleted_ids = []
        self.deleted_conv_ids = []

    async def health(self):
        if not self.health_ok:
            raise TDAMUnavailable("down")
        return {"status": "ok", "version": "2.0.0"}

    async def search_atomic(self, ids, query, limit=5):
        return [{"id": "rec_1", "content": "喜欢咖啡", "type": "persona"}]

    async def atomic_query(self, ids, type=None, limit=20, offset=0):
        return {
            "items": [
                {"id": "rec_1", "content": "喜欢咖啡", "type": "persona"},
                {"id": "rec_2", "content": "下周一发版", "type": "episodic"},
            ],
            "total": 2,
        }

    async def atomic_delete(self, ids, memory_ids):
        self.deleted_ids.extend(memory_ids)
        return 1 if memory_ids == ["rec_1"] else 0

    async def atomic_count(self, ids):
        return 12

    async def core_count(self, ids):
        return 1

    async def conversation_count(self, ids, session_id=None):
        return 34

    async def add_conversation(self, ids, session_id, messages):
        self.added.append(messages)
        return {"added": len(messages)}

    async def conversation_query(self, ids, session_id, limit=100, offset=0):
        return {
            "messages": [{"id": "m1"}, {"id": "m2"}],
            "total": 2,
        }

    async def conversation_delete(self, ids, message_ids):
        self.deleted_conv_ids.extend(message_ids)
        return len(message_ids)


def make_runtime() -> tuple[PluginRuntime, StubCore]:
    core = StubCore()
    rt = PluginRuntime(
        cfg=config_from_astrbot({}),
        core=core,
        knowledge=None,
        cache=TTLCache(),
        buffer=CaptureBuffer(),
    )
    rt._enabled = True
    return rt, core


async def test_search_command():
    rt, _ = make_runtime()
    text = await commands.cmd_search(rt, "u1", "qq:F:1", "咖啡")
    assert "喜欢咖啡" in text
    assert "rec_1" in text  # 带 id 供 forget 引用


async def test_list_command():
    rt, _ = make_runtime()
    text = await commands.cmd_list(rt, "u1", "qq:F:1")
    assert "rec_1" in text and "rec_2" in text
    assert "喜欢咖啡" in text


async def test_remember_command_writes_conversation():
    rt, core = make_runtime()
    text = await commands.cmd_remember(rt, "u1", "qq:F:1", "我喜欢美式咖啡")
    assert "请记住：我喜欢美式咖啡" == core.added[0][0]["content"]
    assert "稍后生效" in text


async def test_forget_command():
    rt, _ = make_runtime()
    assert "已删除" in await commands.cmd_forget(rt, "u1", "qq:F:1", "rec_1")
    assert "未找到" in await commands.cmd_forget(rt, "u1", "qq:F:1", "rec_x")


async def test_status_command_healthy():
    rt, _ = make_runtime()
    text = await commands.cmd_status(rt, "u1", "qq:F:1")
    assert "ok" in text
    assert "12" in text  # atomic 计数
    assert "34" in text  # conversation 计数


async def test_status_command_service_down_shows_guide():
    rt, core = make_runtime()
    core.health_ok = False
    text = await commands.cmd_status(rt, "u1", "qq:F:1")
    assert "不可达" in text
    assert "start-all.sh" in text  # 启动指引


async def test_clear_two_step_confirmation():
    rt, core = make_runtime()
    confirmer = commands.ClearConfirmer()

    first = await commands.cmd_clear(rt, "u1", "qq:F:1", confirmer, confirm=False)
    assert "确认" in first
    assert core.deleted_conv_ids == []  # 未执行

    second = await commands.cmd_clear(rt, "u1", "qq:F:1", confirmer, confirm=True)
    assert "已清空" in second
    assert core.deleted_conv_ids == ["m1", "m2"]


async def test_clear_confirm_without_arm_does_nothing():
    rt, core = make_runtime()
    confirmer = commands.ClearConfirmer()
    text = await commands.cmd_clear(rt, "u1", "qq:F:1", confirmer, confirm=True)
    assert core.deleted_conv_ids == []
    assert "确认" in text  # 提示先执行 /memory clear
