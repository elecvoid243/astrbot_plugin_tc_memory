"""astrbot_plugin_tc_memory — 接入 TencentDB Agent Memory 的 AstrBot 插件入口。

本文件是唯一的 AstrBot 适配薄层：只做 AstrBot 对象 ↔ 纯数据的转换与注册，
全部业务逻辑在 tc_memory/ 包内（可独立单测）。
"""

from pathlib import Path
from urllib.parse import urlparse

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.event.filter import PermissionType
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star, register
from astrbot.core.agent.message import TextPart
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.star.filter.command import GreedyStr

from .tc_memory import commands as cmd_impl
from .tc_memory import tools as tool_impl
from .tc_memory.cache import TTLCache
from .tc_memory.capture import CaptureBuffer
from .tc_memory.client import TdMemoryClient
from .tc_memory.commands import ClearConfirmer
from .tc_memory.config import config_from_astrbot
from .tc_memory.knowledge_client import KnowledgeClient
from .tc_memory.launcher import (
    LocalGatewayLauncher,
    build_launch_command,
    resolve_gateway_paths,
    resolve_knowledge_paths,
    resolve_panel_paths,
    write_panel_instances,
)
from .tc_memory.llm_resolve import (
    provider_to_dict,
    providers_pending_initialization,
    resolve_llm_from_providers,
)
from .tc_memory.logutil import set_plugin_logger
from .tc_memory.runtime import PluginRuntime
from .tc_memory.status import build_status

# 版本兼容：on_agent_done 需要 AstrBot >= 4.23.1
AGENT_DONE_SUPPORTED = hasattr(filter, "on_agent_done")


def build_injection_part(text: str, persist: bool) -> TextPart:
    """构造注入内容块。persist=False（默认）时标记不落盘，
    避免注入物进入 AstrBot 会话历史 DB（详见 spec §4.2）。"""
    part = TextPart(text=text)
    if not persist:
        part.mark_as_temp()
    return part


def extract_assistant_text(resp: LLMResponse) -> str:
    """从 LLMResponse 提取纯文本答复（优先 result_chain，兜底 completion_text）。"""
    chain = getattr(resp, "result_chain", None)
    if chain is not None and hasattr(chain, "get_plain_text"):
        return chain.get_plain_text()
    return getattr(resp, "completion_text", "") or ""


@register(
    name="astrbot_plugin_tc_memory",
    desc="接入 TencentDB Agent Memory，为 AstrBot 提供跨会话长期记忆",
    author="elecvoid243",
    version="0.1.0",
)
class TcMemoryPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        # 把 tc_memory 各模块的日志路由到插件专属 logger：
        # WebUI 控制台只消费 astrbot.plugin.<name> 管线，stdlib root 只进控制台/文件
        set_plugin_logger(self.logger)
        self.cfg = config_from_astrbot(config or {})
        core = TdMemoryClient(
            self.cfg.core_endpoint,
            self.cfg.core_api_key,
            self.cfg.service_id,
            timeout_sec=float(self.cfg.recall_timeout_sec) + 2,
        )
        knowledge = (
            KnowledgeClient(self.cfg.knowledge_endpoint, service_id=self.cfg.service_id)
            if self.cfg.knowledge_enabled
            else None
        )
        self.runtime = PluginRuntime(
            cfg=self.cfg,
            core=core,
            knowledge=knowledge,
            cache=TTLCache(),
            buffer=CaptureBuffer(),
            agent_done_supported=AGENT_DONE_SUPPORTED,
            launcher=self._build_launcher(),
            knowledge_launcher=self._build_knowledge_launcher(),
            panel_launcher=self._build_panel_launcher(),
            admin_key_file=(
                Path(__file__).resolve().parents[2]
                / "plugin_data"
                / "astrbot_plugin_tc_memory"
                / "admin-key"
            ),
        )
        self._clear_confirmer = ClearConfirmer()
        # 本地服务只启动一次：on_plugin_loaded 会随每个插件加载重复触发
        self._local_services_started = False

        # Dashboard「服务状态」popover 数据源：
        # GET /api/v1/plugins/extensions/astrbot_plugin_tc_memory/status
        context.register_web_api(
            "/astrbot_plugin_tc_memory/status",
            self.web_status,
            ["GET"],
            "Agent Memory 服务状态",
        )

    async def web_status(self):
        return {"status": "ok", "data": await build_status(self.runtime)}

    def _build_launcher(self) -> LocalGatewayLauncher | None:
        """local 模式：定位 external_tools 里的内嵌 gateway 与 Node 运行时。

        找不到打包产物时返回 None——probe 会退化为直连 core_endpoint
        （兼容用户手动启动 standalone 的场景）。
        """
        if self.cfg.mode != "local":
            return None
        paths = resolve_gateway_paths(Path(__file__).resolve())
        if paths is None:
            logger.warning(
                "tc_memory: 未找到 external_tools/tc-memory-gateway 打包产物，"
                "local 模式将直接连接 %s（如已手动启动服务可忽略）",
                self.cfg.core_endpoint,
            )
            return None
        node_exe, gateway_dir = paths
        endpoint = urlparse(self.cfg.core_endpoint)
        # 数据与日志落在 AstrBot data/plugin_data 下（插件规范：不写插件目录）
        data_dir = (
            Path(__file__).resolve().parents[2]
            / "plugin_data"
            / "astrbot_plugin_tc_memory"
        )
        env = {
            "TDAI_GATEWAY_CONFIG": str(gateway_dir / "tdai-gateway.standalone.yaml"),
            "TDAI_DATA_DIR": str(data_dir / "gateway-data"),
        }
        if endpoint.port:
            env["TDAI_GATEWAY_PORT"] = str(endpoint.port)
        return LocalGatewayLauncher(
            command=build_launch_command(node_exe, gateway_dir),
            cwd=gateway_dir,
            env=env,
            health_url=f"{self.cfg.core_endpoint.rstrip('/')}/health",
            log_path=data_dir / "gateway.log",
            config_template=gateway_dir / "tdai-gateway.standalone.yaml",
            config_out=data_dir / "gateway-config.effective.yaml",
            engine_overrides={
                "memory": {
                    "extraction": {"enabled": self.cfg.engine_extraction_enabled},
                    "pipeline": {
                        "everyNConversations": self.cfg.engine_pipeline_every_n
                    },
                    "persona": {
                        "triggerEveryN": self.cfg.engine_persona_trigger_every_n
                    },
                    "recall": {
                        "maxResults": self.cfg.engine_recall_max_results,
                        "scoreThreshold": self.cfg.engine_recall_score_threshold,
                    },
                }
            },
        )

    def _build_knowledge_launcher(self) -> LocalGatewayLauncher | None:
        """knowledge_enabled 且 local 模式：内嵌知识库服务（Wiki/CodeGraph）。

        LLM 三元组在 _resolve_llm_from_provider 阶段经 set_env 注入。
        """
        if not self.cfg.knowledge_enabled or self.cfg.mode != "local":
            return None
        paths = resolve_knowledge_paths(Path(__file__).resolve())
        if paths is None:
            logger.warning(
                "tc_memory: 未找到 external_tools/tc-memory-knowledge 打包产物，"
                "知识库工具将直连 %s",
                self.cfg.knowledge_endpoint,
            )
            return None
        node_exe, kn_dir = paths
        endpoint = urlparse(self.cfg.knowledge_endpoint)
        data_dir = (
            Path(__file__).resolve().parents[2]
            / "plugin_data"
            / "astrbot_plugin_tc_memory"
        )
        env = {
            "KNOWLEDGE_DATA_DIR": str(data_dir / "knowledge-data"),
            "KNOWLEDGE_DB_PATH": str(data_dir / "knowledge-data" / "knowledge.db"),
            "LLM_MODE": "custom",
        }
        if endpoint.port:
            env["PORT"] = str(endpoint.port)
        return LocalGatewayLauncher(
            command=[str(node_exe), str(kn_dir / "start.mjs")],
            cwd=kn_dir,
            env=env,
            health_url=f"{self.cfg.knowledge_endpoint.rstrip('/')}/health",
            log_path=data_dir / "knowledge.log",
        )

    PANEL_PORT = 8125

    def _build_panel_launcher(self) -> LocalGatewayLauncher | None:
        """local 模式 + panel_enabled：内嵌管理面板（WebUI）。

        实例配置从插件的 core_endpoint/core_api_key 生成（单一真源）。
        """
        if self.cfg.mode != "local" or not self.cfg.panel_enabled:
            return None
        paths = resolve_panel_paths(Path(__file__).resolve())
        if paths is None:
            logger.warning("tc_memory: 未找到 external_tools/tc-memory-panel 打包产物")
            return None
        node_exe, panel_dir = paths
        data_dir = (
            Path(__file__).resolve().parents[2]
            / "plugin_data"
            / "astrbot_plugin_tc_memory"
        )
        instances_file = data_dir / "panel-instances.json"
        write_panel_instances(
            instances_file, self.cfg.core_endpoint, self.cfg.core_api_key
        )
        return LocalGatewayLauncher(
            command=[str(node_exe), str(panel_dir / "dist" / "index.js")],
            cwd=panel_dir,
            env={
                "PORT": str(self.PANEL_PORT),
                "UI_DIST_DIR": str(panel_dir / "web-dist"),
                "METADATA_INSTANCES_CONFIG": str(instances_file),
                "KNOWLEDGE_SERVICE_URL": self.cfg.knowledge_endpoint,
                # 本地无 proxy，跳过 LLM binding 同步（best-effort 逻辑直接关掉）
                "KNOWLEDGE_LLM_BINDING_SYNC": "false",
                "LOG_FORMAT": "pretty",
            },
            health_url=f"http://127.0.0.1:{self.PANEL_PORT}/",
            log_path=data_dir / "panel.log",
        )

    @filter.on_plugin_loaded()
    async def _on_loaded(self, metadata=None):
        # 框架以 handler(metadata) 形式调用，必须接收该位置参数
        if self._local_services_started:
            return  # 每个插件加载都会触发本 hook，本地服务只启动一次
        if not self._apply_llm_env():
            # 冷启动顺序是 plugin_manager.reload() → provider_manager.initialize()，
            # 插件加载阶段拿不到 provider 实例；此处若照常启动，子进程会带着空
            # LLM 配置落地（spawn 之后再 set_env 不生效）→ 推迟到 astrbot_loaded。
            logger.debug(
                "tc_memory: provider 尚未实例化，本地服务推迟到 astrbot_loaded"
            )
            return
        self._local_services_started = True
        await self.runtime.probe()

    @filter.on_astrbot_loaded()
    async def _on_astrbot_loaded(self):
        """冷启动补齐：provider 实例化完成后再解析 LLM 并拉起内嵌服务。"""
        if self._local_services_started:
            return
        self._local_services_started = True
        self._apply_llm_env(force=True)
        await self.runtime.probe()

    def _apply_llm_env(self, *, force: bool = False) -> bool:
        """把所选 provider 的 base_url/api_key/model 注入 launcher env。

        返回 False 只表示「provider 还没实例化，本次无法判定」，调用方须推迟启动
        本地服务；force=True 时不再推迟，解析不出也照常启动（降级运行，召回/记录
        不受影响，仅服务端提炼不可用）。
        """
        launcher = self.runtime.launcher
        kn_launcher = self.runtime.knowledge_launcher
        if launcher is None and kn_launcher is None:
            return True
        providers = [provider_to_dict(p) for p in self.context.get_all_providers()]
        if not force and providers_pending_initialization(self.context, providers):
            return False
        provider_id = self.cfg.local_llm_provider_id
        if not provider_id:
            logger.warning(
                "tc_memory: local 模式未选择提炼 LLM provider，"
                "服务端记忆提炼将不可用（召回/记录不受影响）。"
                "请在插件配置中选择「提炼 LLM」"
            )
            return True
        resolved = resolve_llm_from_providers(providers, provider_id)
        if resolved is None:
            logger.warning(
                "tc_memory: provider %s 解析不出 LLM 连接参数"
                "（api_base / key / model 需齐全且该 provider 已启用），"
                "服务端记忆提炼将不可用（召回/记录不受影响）",
                provider_id,
            )
            return True
        if launcher is not None:
            launcher.set_env(
                {
                    "TDAI_LLM_BASE_URL": resolved.base_url,
                    "TDAI_LLM_API_KEY": resolved.api_key,
                    "TDAI_LLM_MODEL": resolved.model,
                }
            )
        if kn_launcher is not None:
            kn_launcher.set_env(
                {
                    "LLM_BASE_URL": resolved.base_url,
                    "LLM_API_KEY": resolved.api_key,
                    "LLM_MODEL": resolved.model,
                }
            )
        logger.info(
            "tc_memory: 提炼 LLM 使用 provider %s（%s）", provider_id, resolved.model
        )
        return True

    @filter.on_llm_request()
    async def on_llm_request_hook(self, event: AstrMessageEvent, req: ProviderRequest):
        """LLM 请求前：召回记忆注入（不动 system_prompt，走 extra_user_content_parts）。"""
        sender = event.get_sender_id()
        umo = event.unified_msg_origin
        self.runtime.note_user_message(umo, req.prompt or "")
        text = await self.runtime.recall_for(sender, umo, req.prompt or "")
        if text:
            req.extra_user_content_parts.append(
                build_injection_part(text, persist=self.cfg.persist_injected_memory)
            )

    async def on_agent_done_hook(
        self,
        event: AstrMessageEvent,
        run_context: ContextWrapper[AstrAgentContext],
        llm_response: LLMResponse,
    ):
        """Agent 完成后：把本轮 user/assistant 消息写回记忆服务。"""
        await self.runtime.capture_after(
            event.get_sender_id(),
            event.unified_msg_origin,
            extract_assistant_text(llm_response),
        )

    async def terminate(self):
        """插件卸载：释放 HTTP 连接；关闭自己拉起的内嵌 gateway（复用的不动）。"""
        if self.runtime.launcher:
            await self.runtime.launcher.shutdown()
        if self.runtime.knowledge_launcher:
            await self.runtime.knowledge_launcher.shutdown()
        if self.runtime.panel_launcher:
            await self.runtime.panel_launcher.shutdown()
        await self.runtime.core.aclose()
        if self.runtime.knowledge:
            await self.runtime.knowledge.aclose()

    # ── LLM 工具（薄封装，业务逻辑在 tc_memory/tools.py）──────────────
    # 知识系 4 个工具始终注册；knowledge_enabled=false 时返回提示文本。
    # （条件注册会让 schema 随配置闪变，破坏 provider 侧前缀缓存）

    def _ctx(self, event: AstrMessageEvent) -> tuple[str, str]:
        return event.get_sender_id(), event.unified_msg_origin

    @filter.llm_tool()
    async def memory_search(self, event: AstrMessageEvent, query: str):
        """搜索长期记忆中的结构化记忆（用户偏好、历史事件、约定规则）。

        Args:
            query(string): 搜索关键词
        """
        sender, umo = self._ctx(event)
        return await tool_impl.memory_search(self.runtime, sender, umo, query)

    @filter.llm_tool()
    async def conversation_search(self, event: AstrMessageEvent, query: str):
        """搜索原始历史对话（具体消息原文、时间线细节）。

        Args:
            query(string): 搜索关键词
        """
        sender, umo = self._ctx(event)
        return await tool_impl.conversation_search(self.runtime, sender, umo, query)

    @filter.llm_tool()
    async def skill_search(self, event: AstrMessageEvent, query: str):
        """搜索可用的技能（团队共享的工作方法与流程）。

        Args:
            query(string): 搜索关键词
        """
        sender, umo = self._ctx(event)
        return await tool_impl.skill_search(self.runtime, sender, umo, query)

    @filter.llm_tool()
    async def skill_view(self, event: AstrMessageEvent, name: str):
        """加载指定技能的完整内容（先 skill_search 或直接按名称加载）。

        Args:
            name(string): 技能名称
        """
        sender, umo = self._ctx(event)
        return await tool_impl.skill_view(self.runtime, sender, umo, name)

    @filter.llm_tool()
    async def wiki_search(self, event: AstrMessageEvent, query: str):
        """搜索知识库 Wiki 页面（产品文档、设计规范等结构化知识）。

        Args:
            query(string): 搜索关键词
        """
        sender, umo = self._ctx(event)
        return await tool_impl.wiki_search(self.runtime, sender, umo, query)

    @filter.llm_tool()
    async def wiki_read(self, event: AstrMessageEvent, path: str):
        """读取 Wiki 页面全文。

        Args:
            path(string): 页面路径（由 wiki_search 结果获得）
        """
        sender, umo = self._ctx(event)
        return await tool_impl.wiki_read(self.runtime, sender, umo, path)

    @filter.llm_tool()
    async def codegraph_kb_search(self, event: AstrMessageEvent, query: str):
        """在知识库的代码图谱中搜索符号/文件（函数、类、模块的位置与定义）。

        数据源：知识库中【已注册的仓库】（服务端索引快照），不是本机工作区。
        若要查询本机正在开发的代码，请使用本地的 codegraph_search（需传 projectPath）。

        Args:
            query(string): 搜索关键词或符号名
        """
        sender, umo = self._ctx(event)
        return await tool_impl.codegraph_search(self.runtime, sender, umo, query)

    @filter.llm_tool()
    async def codegraph_kb_explore(self, event: AstrMessageEvent, symbol: str):
        """探索知识库代码图谱中符号的调用关系与影响面（改了它会波及哪些文件）。

        数据源：知识库中【已注册的仓库】（服务端索引快照），不是本机工作区。
        若要分析本机正在开发的代码，请使用本地的 codegraph_explore（需传 projectPath）。

        Args:
            symbol(string): 符号名
        """
        sender, umo = self._ctx(event)
        return await tool_impl.codegraph_explore(self.runtime, sender, umo, symbol)

    # ── /memory 指令组 ───────────────────────────────────────

    @filter.command_group("memory")
    def memory_group(self):
        """长期记忆管理"""

    @memory_group.command("search")
    async def memory_cmd_search(self, event: AstrMessageEvent, query: GreedyStr):
        """搜索我的记忆：/memory search <关键词>"""
        if not query.strip():
            yield event.plain_result("用法：/memory search <关键词>")
            return
        sender, umo = self._ctx(event)
        yield event.plain_result(
            await cmd_impl.cmd_search(self.runtime, sender, umo, query)
        )

    @memory_group.command("list")
    async def memory_cmd_list(self, event: AstrMessageEvent):
        """列出我最近的记忆（带 id）"""
        sender, umo = self._ctx(event)
        yield event.plain_result(await cmd_impl.cmd_list(self.runtime, sender, umo))

    @memory_group.command("remember")
    async def memory_cmd_remember(self, event: AstrMessageEvent, content: GreedyStr):
        """手动记一条：/memory remember <内容>（服务端异步提炼，稍后生效）"""
        if not content.strip():
            yield event.plain_result("用法：/memory remember <要记住的内容>")
            return
        sender, umo = self._ctx(event)
        yield event.plain_result(
            await cmd_impl.cmd_remember(self.runtime, sender, umo, content)
        )

    @memory_group.command("forget")
    async def memory_cmd_forget(self, event: AstrMessageEvent, memory_id: str = ""):
        """删除一条记忆：/memory forget <id>（id 从 /memory list 获取）"""
        if not memory_id.strip():
            yield event.plain_result("用法：/memory forget <记忆id>")
            return
        sender, umo = self._ctx(event)
        yield event.plain_result(
            await cmd_impl.cmd_forget(self.runtime, sender, umo, memory_id.strip())
        )

    @memory_group.command("status")
    async def memory_cmd_status(self, event: AstrMessageEvent):
        """记忆服务健康状态与我的记忆统计"""
        sender, umo = self._ctx(event)
        yield event.plain_result(await cmd_impl.cmd_status(self.runtime, sender, umo))

    @memory_group.command("clear")
    @filter.permission_type(PermissionType.ADMIN)
    async def memory_cmd_clear(self, event: AstrMessageEvent, action: str = ""):
        """清空当前会话的对话记录（管理员限定，需二次确认）"""
        sender, umo = self._ctx(event)
        yield event.plain_result(
            await cmd_impl.cmd_clear(
                self.runtime,
                sender,
                umo,
                self._clear_confirmer,
                confirm=(action.strip() == "confirm"),
            )
        )


# 条件注册：旧版 AstrBot 无此钩子时保持方法存在但不注册（runtime 内已降级捕获）
if AGENT_DONE_SUPPORTED:
    TcMemoryPlugin.on_agent_done_hook = filter.on_agent_done()(
        TcMemoryPlugin.on_agent_done_hook
    )
else:
    logger.warning(
        "tc_memory: 当前 AstrBot 无 on_agent_done 钩子（需 >=4.23.1），对话捕获不可用"
    )
