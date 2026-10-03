"""服务状态构建（dashboard「服务状态」popover 的数据源）。

running 实时探测（不调缓存），保证用户看到的就是此刻状态；
pid 只在 local 模式且进程由本插件拉起时存在。
"""

from .errors import TDAMError
from .runtime import PluginRuntime


async def build_status(runtime: PluginRuntime) -> dict:
    running = False
    version = None
    try:
        health = await runtime.core.health()
        running = health.get("status") == "ok"
        version = health.get("version")
    except TDAMError:
        pass

    pid = None
    launcher = runtime.launcher
    if launcher is not None:
        proc = launcher._proc
        if proc is not None and proc.poll() is None:
            pid = proc.pid

    panel_launcher = getattr(runtime, "panel_launcher", None)
    panel_running = None
    if panel_launcher is not None:
        panel_running = await panel_launcher._healthy()

    return {
        "user_key": runtime.admin_key,
        "panel_running": panel_running,
        "mode": runtime.cfg.mode,
        "enabled": runtime.enabled,
        "running": running,
        "endpoint": runtime.cfg.core_endpoint,
        "version": version,
        "pid": pid,
    }
