"""内嵌 standalone gateway 的进程启动器（local 模式专用）。

职责边界：
- 只管理自己拉起的子进程（shutdown 只杀自己 spawn 的，复用的不碰）
- ensure_running 幂等：先探测健康，已健康直接复用
- 健康等待用轮询（条件等待，非盲等）

路径约定（打包产物）：
  external_tools/
  ├── codegraph-win32-x64/node.exe     # Node 运行时（复用）
  └── tc-memory-gateway/
      ├── dist/server.js               # gateway bundle
      ├── node_modules/                # 原生依赖（jieba / sqlite-vec）
      └── tdai-gateway.standalone.yaml
"""

import asyncio
import logging
import os
import subprocess
from pathlib import Path

import aiohttp

logger = logging.getLogger(__name__)

NODE_REL = Path("external_tools") / "codegraph-win32-x64" / "node.exe"
GATEWAY_REL = Path("external_tools") / "tc-memory-gateway"
KNOWLEDGE_REL = Path("external_tools") / "tc-memory-knowledge"
PANEL_REL = Path("external_tools") / "tc-memory-panel"


def resolve_external_tools_root(start_from: Path) -> Path | None:
    """从插件文件向上找包含 codegraph node 运行时的 external_tools 根目录。"""
    for parent in [start_from, *start_from.parents]:
        if (parent / NODE_REL).is_file():
            return parent / "external_tools"
    return None


def resolve_gateway_paths(start_from: Path) -> tuple[Path, Path] | None:
    """从插件文件向上找 external_tools，返回 (node_exe, gateway_dir)。找不到返回 None。"""
    root = resolve_external_tools_root(start_from)
    if root is None:
        return None
    node = root / "codegraph-win32-x64" / "node.exe"
    gateway = root / "tc-memory-gateway"
    if node.is_file() and (gateway / "dist" / "server.js").is_file():
        return node, gateway
    return None


def resolve_panel_paths(start_from: Path) -> tuple[Path, Path] | None:
    """返回 (node_exe, panel_dir)。找不到打包产物返回 None。"""
    root = resolve_external_tools_root(start_from)
    if root is None:
        return None
    node = root / "codegraph-win32-x64" / "node.exe"
    panel = root / "tc-memory-panel"
    if node.is_file() and (panel / "dist" / "index.js").is_file():
        return node, panel
    return None


def write_panel_instances(out_path: Path, gateway_endpoint: str, api_key: str) -> None:
    """生成面板实例配置（单一真源：插件的 core_endpoint/core_api_key）。"""
    import json

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "instances": [
                    {
                        "id": "default",
                        "name": "本地记忆",
                        "gateway_endpoint": gateway_endpoint,
                        "api_key": api_key,
                    }
                ]
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def resolve_knowledge_paths(start_from: Path) -> tuple[Path, Path] | None:
    """返回 (node_exe, knowledge_dir)。找不到打包产物返回 None。"""
    root = resolve_external_tools_root(start_from)
    if root is None:
        return None
    node = root / "codegraph-win32-x64" / "node.exe"
    knowledge = root / "tc-memory-knowledge"
    if node.is_file() and (knowledge / "start.mjs").is_file():
        return node, knowledge
    return None


def build_launch_command(node_exe: Path, gateway_dir: Path) -> list[str]:
    return [str(node_exe), str(gateway_dir / "dist" / "server.js")]


def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并：override 的叶子覆盖 base，未提及的键保留 base。"""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def prepare_gateway_config(
    template_path: Path, overrides: dict, out_path: Path
) -> Path:
    """把引擎参数覆盖项合并进包内模板，生成 effective yaml（包内文件保持只读）。

    模板里的 ${ENV} 占位符是纯文本，yaml 往返不破坏——gateway 加载时才做插值。
    """
    import yaml

    base = yaml.safe_load(template_path.read_text(encoding="utf-8")) or {}
    merged = _deep_merge(base, overrides)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        yaml.safe_dump(merged, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return out_path


class LocalGatewayLauncher:
    """拉起并看守一个 gateway 子进程。command 显式传入（测试可替换为夹具）。"""

    def __init__(
        self,
        *,
        command: list[str],
        cwd: Path,
        env: dict[str, str],
        health_url: str,
        log_path: Path | None,
        startup_timeout_sec: float = 30.0,
        config_template: Path | None = None,
        config_out: Path | None = None,
        engine_overrides: dict | None = None,
    ):
        self._command = command
        self._cwd = cwd
        self._env = dict(env)
        self._health_url = health_url
        self._log_path = log_path
        self._startup_timeout = startup_timeout_sec
        self._config_template = config_template
        self._config_out = config_out
        self._engine_overrides = engine_overrides or {}
        self._proc: subprocess.Popen | None = None
        self._log_file = None
        self.spawned_count = 0

    def set_env(self, updates: dict[str, str]) -> None:
        """spawn 前更新环境变量（如 provider 解析出的 LLM 三元组）。"""
        if self._proc is not None:
            logger.warning("tc_memory: gateway 已启动，env 更新不会生效")
        self._env.update(updates)

    async def ensure_running(self) -> bool:
        """已健康 → 复用；否则拉起并轮询等待就绪。返回服务最终是否可用。"""
        if await self._healthy():
            return True
        if self._config_template and self._config_out:
            # 引擎参数覆盖 → 生成 effective 配置，子进程指向生成文件（模板只读）
            prepare_gateway_config(
                self._config_template, self._engine_overrides, self._config_out
            )
            self._env["TDAI_GATEWAY_CONFIG"] = str(self._config_out)
        self._spawn()
        deadline = asyncio.get_event_loop().time() + self._startup_timeout
        while asyncio.get_event_loop().time() < deadline:
            if self._proc is not None and self._proc.poll() is not None:
                logger.error(
                    "tc_memory: gateway 子进程提前退出（code=%s），详见日志文件",
                    self._proc.returncode,
                )
                return False
            if await self._healthy():
                return True
            await asyncio.sleep(0.5)
        logger.error("tc_memory: gateway 启动超时（%.0fs）", self._startup_timeout)
        return False

    def _spawn(self) -> None:
        # Windows 下 CREATE_NO_WINDOW 避免弹出控制台黑窗
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if self._log_path:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            # 句柄存活期 = 子进程存活期，在 shutdown() 中关闭
            self._log_file = open(self._log_path, "ab", buffering=0)  # noqa: SIM115
            stdout = stderr = self._log_file
        else:
            stdout = stderr = subprocess.DEVNULL
        self._proc = subprocess.Popen(
            self._command,
            cwd=self._cwd,
            env={**os.environ, **self._env},
            stdout=stdout,
            stderr=stderr,
            creationflags=creationflags,
        )
        self.spawned_count += 1
        logger.info(
            "tc_memory: 已启动内嵌服务（pid=%s）: %s",
            self._proc.pid,
            self._command[0],
        )

    async def _healthy(self) -> bool:
        try:
            async with (
                aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=2)
                ) as session,
                session.get(self._health_url) as resp,
            ):
                return resp.status == 200
        except (aiohttp.ClientError, TimeoutError, OSError):
            return False

    async def shutdown(self) -> None:
        """只清理自己拉起的进程；复用的服务不动。"""
        if self._proc is None:
            return
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                await asyncio.get_event_loop().run_in_executor(
                    None, lambda: self._proc.wait(timeout=10)
                )
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None
        if self._log_file is not None:
            self._log_file.close()
            self._log_file = None
