"""服务状态条目测试（dashboard 服务状态 popover 的数据源）。"""

from tc_memory.cache import TTLCache
from tc_memory.capture import CaptureBuffer
from tc_memory.config import config_from_astrbot
from tc_memory.errors import TDAMUnavailable
from tc_memory.runtime import PluginRuntime
from tc_memory.status import build_status


class StubCore:
    def __init__(self, ok=True):
        self.ok = ok

    async def health(self):
        if not self.ok:
            raise TDAMUnavailable("down")
        return {"status": "ok", "version": "2.0.1"}


class FakeProc:
    pid = 26532

    def __init__(self, alive=True):
        self.alive = alive

    def poll(self):
        return None if self.alive else 0


class FakeLauncher:
    def __init__(self, proc=None):
        self._proc = proc


def make_runtime(mode="local", core_ok=True, launcher=None) -> PluginRuntime:
    rt = PluginRuntime(
        cfg=config_from_astrbot({"mode": mode}),
        core=StubCore(ok=core_ok),
        knowledge=None,
        cache=TTLCache(),
        buffer=CaptureBuffer(),
        launcher=launcher,
    )
    rt._enabled = True
    return rt


async def test_status_running_with_pid_when_self_spawned():
    rt = make_runtime(launcher=FakeLauncher(FakeProc(alive=True)))
    s = await build_status(rt)
    assert s["running"] is True
    assert s["version"] == "2.0.1"
    assert s["mode"] == "local"
    assert s["endpoint"] == "http://127.0.0.1:8420"
    assert s["pid"] == 26532
    assert s["enabled"] is True


async def test_status_down_when_health_fails():
    rt = make_runtime(core_ok=False)
    s = await build_status(rt)
    assert s["running"] is False
    assert s["version"] is None


async def test_status_no_pid_without_launcher_or_dead_proc():
    rt = make_runtime(launcher=None)
    assert (await build_status(rt))["pid"] is None

    rt2 = make_runtime(launcher=FakeLauncher(FakeProc(alive=False)))
    assert (await build_status(rt2))["pid"] is None


class FakePanelLauncher:
    def __init__(self, healthy):
        self._healthy_value = healthy

    async def _healthy(self):
        return self._healthy_value


async def test_status_includes_panel_running():
    rt = make_runtime()
    rt.panel_launcher = FakePanelLauncher(True)
    s = await build_status(rt)
    assert s["panel_running"] is True

    rt2 = make_runtime()
    rt2.panel_launcher = None
    assert (await build_status(rt2))["panel_running"] is None
