"""launcher 与 runtime 联动测试（local 模式内嵌 gateway 生命周期）。"""

import sys
from pathlib import Path

from tc_memory.cache import TTLCache
from tc_memory.capture import CaptureBuffer
from tc_memory.config import config_from_astrbot
from tc_memory.errors import TDAMUnavailable
from tc_memory.launcher import LocalGatewayLauncher
from tc_memory.runtime import PluginRuntime

FIXTURE = Path(__file__).parent / "fixtures" / "fake_gw.py"


class FlakyHealthCore:
    """前 fail_times 次 health 失败，之后成功；记录调用次数。"""

    def __init__(self, fail_times: int):
        self.fail_times = fail_times
        self.calls = 0

    async def health(self):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise TDAMUnavailable("down")
        return {"status": "ok"}


class StubLauncher:
    def __init__(self, succeed: bool = True):
        self.succeed = succeed
        self.calls = 0

    async def ensure_running(self) -> bool:
        self.calls += 1
        return self.succeed


def make_runtime(core, launcher=None, mode="local"):
    cfg = config_from_astrbot({"mode": mode})
    return PluginRuntime(
        cfg=cfg,
        core=core,
        knowledge=None,
        cache=TTLCache(),
        buffer=CaptureBuffer(),
        launcher=launcher,
    )


async def test_local_mode_spawns_gateway_and_recovers():
    core = FlakyHealthCore(fail_times=1)
    launcher = StubLauncher(succeed=True)
    runtime = make_runtime(core, launcher)

    assert await runtime.probe() is True
    assert launcher.calls == 1
    assert core.calls == 2  # 拉起后重试了一次 health


async def test_server_mode_never_spawns():
    core = FlakyHealthCore(fail_times=99)
    launcher = StubLauncher()
    runtime = make_runtime(core, launcher, mode="server")

    assert await runtime.probe() is False
    assert launcher.calls == 0


async def test_healthy_gateway_not_spawned():
    core = FlakyHealthCore(fail_times=0)
    launcher = StubLauncher()
    runtime = make_runtime(core, launcher)

    assert await runtime.probe() is True
    assert launcher.calls == 0


async def test_spawn_failure_disables_plugin():
    core = FlakyHealthCore(fail_times=99)
    launcher = StubLauncher(succeed=False)
    runtime = make_runtime(core, launcher)

    assert await runtime.probe() is False
    assert launcher.calls == 1


async def test_probe_also_ensures_knowledge_service():
    """knowledge_enabled 时 probe 连带拉起知识库服务；其失败不影响插件启用。"""
    core = FlakyHealthCore(fail_times=0)
    gateway_launcher = StubLauncher()
    knowledge_launcher = StubLauncher(succeed=False)  # 知识库拉起失败
    cfg = config_from_astrbot({"mode": "local", "knowledge_enabled": True})
    runtime = PluginRuntime(
        cfg=cfg,
        core=core,
        knowledge=None,
        cache=TTLCache(),
        buffer=CaptureBuffer(),
        launcher=gateway_launcher,
        knowledge_launcher=knowledge_launcher,
    )

    assert await runtime.probe() is True  # 插件仍启用
    assert knowledge_launcher.calls == 1  # 但确实尝试过拉起知识库


# ── launcher 本体（真实子进程 + 真实 HTTP 探测）────────────────


def make_launcher(port: int, **kw) -> LocalGatewayLauncher:
    return LocalGatewayLauncher(
        command=[sys.executable, str(FIXTURE), str(port)],
        cwd=Path(__file__).parent,
        env={},
        health_url=f"http://127.0.0.1:{port}/health",
        log_path=None,
        startup_timeout_sec=10.0,
        **kw,
    )


async def test_launcher_spawns_and_waits_until_healthy():
    launcher = make_launcher(port=18531)
    assert await launcher.ensure_running() is True
    # 幂等：已健康时再调用不重复 spawn
    assert await launcher.ensure_running() is True
    assert launcher.spawned_count == 1
    await launcher.shutdown()


async def test_launcher_reuses_existing_service():
    import asyncio
    import subprocess

    proc = subprocess.Popen(  # noqa: ASYNC220  # 测试夹具：模拟一个已在运行的外部服务
        [sys.executable, str(FIXTURE), "18532"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        await asyncio.sleep(0.5)  # 等服务起来
        launcher = make_launcher(port=18532)
        assert await launcher.ensure_running() is True
        assert launcher.spawned_count == 0  # 复用，未 spawn
        await launcher.shutdown()  # 不应杀掉别人的进程
        # 别人的进程还活着
        assert proc.poll() is None
    finally:
        proc.terminate()
        proc.wait(timeout=5)


async def test_launcher_spawn_timeout_returns_false(tmp_path):
    launcher = LocalGatewayLauncher(
        command=[sys.executable, "-c", "import time; time.sleep(30)"],  # 不起服务
        cwd=tmp_path,
        env={},
        health_url="http://127.0.0.1:18533/health",
        log_path=None,
        startup_timeout_sec=1.0,
    )
    assert await launcher.ensure_running() is False
    await launcher.shutdown()


async def test_shutdown_kills_self_spawned():
    launcher = make_launcher(port=18534)
    await launcher.ensure_running()
    proc = launcher._proc
    await launcher.shutdown()
    assert proc is not None
    assert proc.returncode is not None or proc.poll() is not None


# ── 引擎参数覆盖（生成 effective yaml）────────────────────────


def test_prepare_config_merges_overrides(tmp_path):
    from tc_memory.launcher import prepare_gateway_config

    template = tmp_path / "template.yaml"
    template.write_text(
        "server:\n  port: 8420\n"
        "llm:\n  apiKey: '${TDAI_LLM_API_KEY}'\n"
        "memory:\n  recall:\n    maxResults: 5\n    scoreThreshold: 0.3\n",
        encoding="utf-8",
    )
    out = tmp_path / "out" / "effective.yaml"

    result = prepare_gateway_config(
        template, {"memory": {"recall": {"maxResults": 10}}}, out
    )

    import yaml

    merged = yaml.safe_load(result.read_text(encoding="utf-8"))
    assert merged["memory"]["recall"]["maxResults"] == 10
    assert merged["memory"]["recall"]["scoreThreshold"] == 0.3  # 未覆盖的保留
    assert merged["server"]["port"] == 8420
    assert merged["llm"]["apiKey"] == "${TDAI_LLM_API_KEY}"  # 环境变量占位符不破坏


async def test_launcher_generates_effective_config(tmp_path):
    from tc_memory.launcher import LocalGatewayLauncher

    template = tmp_path / "tdai-gateway.standalone.yaml"
    template.write_text("server:\n  port: 8420\n", encoding="utf-8")
    out = tmp_path / "effective.yaml"
    launcher = LocalGatewayLauncher(
        command=[sys.executable, str(FIXTURE), "18535"],
        cwd=tmp_path,
        env={},
        health_url="http://127.0.0.1:18535/health",
        log_path=None,
        startup_timeout_sec=10.0,
        config_template=template,
        config_out=out,
        engine_overrides={"memory": {"extraction": {"enabled": False}}},
    )
    assert await launcher.ensure_running() is True
    # 子进程拿到的是生成文件而非模板
    assert launcher._env["TDAI_GATEWAY_CONFIG"] == str(out)
    assert out.exists()
    await launcher.shutdown()
