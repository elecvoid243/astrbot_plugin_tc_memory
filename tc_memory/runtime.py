"""插件运行时：hooks 的全部判断逻辑（不依赖 AstrBot，可独立单测）。

main.py 只负责 AstrBot 对象 ↔ 纯数据的转换，所有守卫/降级/退避都在这里。
"""

import asyncio
import time

from .admin_key import ensure_admin_key
from .align import resolve_panel_identities
from .cache import TTLCache
from .capture import CaptureBuffer
from .client import TdMemoryClient
from .config import PluginConfig
from .errors import TDAMAuthError, TDAMError
from .identity import ResolvedIdentity, resolve_identity
from .knowledge_client import KnowledgeClient
from .logutil import get_logger, short, vlog
from .recall import perform_recall

logger = get_logger(__name__)

AUTH_BACKOFF_SEC = 60.0


class PluginRuntime:
    def __init__(
        self,
        cfg: PluginConfig,
        core: TdMemoryClient,
        knowledge: KnowledgeClient | None,
        cache: TTLCache,
        buffer: CaptureBuffer,
        agent_done_supported: bool = True,
        launcher=None,
        knowledge_launcher=None,
        panel_launcher=None,
        admin_key_file=None,
    ):
        self.cfg = cfg
        self.core = core
        self.knowledge = knowledge
        self.cache = cache
        self.buffer = buffer
        # 旧版 AstrBot 无 on_agent_done：仅降级捕获，召回仍可用
        self.capture_supported = agent_done_supported
        # local 模式的内嵌 gateway 启动器（server 模式为 None）
        self.launcher = launcher
        # 知识库服务启动器（knowledge_enabled 且能找到打包产物时为非 None）
        self.knowledge_launcher = knowledge_launcher
        # 管理面板启动器（panel_enabled 时为非 None）
        self.panel_launcher = panel_launcher
        # local 模式 admin key 持久化文件（None 则不引导）
        self.admin_key_file = admin_key_file
        self.admin_key: str | None = None
        # local 模式对齐面板后的隔离三元组（None=未对齐，用配置值）
        self.adopted_ids = None
        self._enabled = False
        self._auth_backoff_until = 0.0

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def probe(self) -> bool:
        """启动健康探测：失败则整体禁用（不影响聊天），成功则启用。

        local 模式下首次探测失败会尝试拉起内嵌 standalone gateway 后重试一次。
        """
        err: TDAMError | None = None
        try:
            await self.core.health()
        except TDAMError as first_err:
            err = first_err
            if self.cfg.mode == "local" and self.launcher is not None:
                logger.info("tc_memory: Gateway 未运行，尝试启动内嵌 standalone 服务…")
                if await self.launcher.ensure_running():
                    try:
                        await self.core.health()
                        await self._bootstrap_admin_key()
                        await self._ensure_knowledge()
                        return self._mark_enabled()
                    except TDAMError as second_err:
                        err = second_err
            logger.warning(
                "tc_memory: Gateway 健康探测失败（%s），插件已禁用。"
                "请确认服务已启动（deploy/global-images/start-all.sh 或 standalone），"
                "并检查 core_endpoint/core_api_key 配置",
                err.message[:80],
            )
            self._enabled = False
            return False
        await self._bootstrap_admin_key()
        await self._ensure_knowledge()
        return self._mark_enabled()

    def identity_for(self, sender_id: str, unified_msg_origin: str) -> ResolvedIdentity:
        """对齐面板后统一用面板桶（team/agent/admin user）；否则按配置+发送者映射。"""
        if self.adopted_ids is not None:
            return ResolvedIdentity(ids=self.adopted_ids, session_id=unified_msg_origin)
        return resolve_identity(sender_id, unified_msg_origin, self.cfg)

    async def _adopt_panel_identities(self) -> None:
        """local 模式：读取面板默认 team/agent/admin user 作为自己的隔离三元组。

        面板按 (team, agent, owner_user) 查数据面；不对齐则面板永远显示 0。
        """
        if self.admin_key is None:
            return
        try:
            ids = await resolve_panel_identities(self.core, self.admin_key)
        except TDAMError as e:
            logger.warning("tc_memory: 面板身份对齐失败: %s", e.message[:80])
            return
        if ids is None:
            vlog(
                logger,
                self.cfg,
                "面板身份未就绪（无 team/agent），继续使用配置三元组 team=%s agent=%s",
                self.cfg.team_id,
                self.cfg.agent_id,
            )
            return
        self.adopted_ids = ids
        logger.info(
            "tc_memory: 已对齐面板身份 team=%s agent=%s user=%s",
            ids.team_id,
            ids.agent_id,
            ids.user_id,
        )

    async def _bootstrap_admin_key(self) -> None:
        """local 模式：确保本地实例有可用 admin key（面板登录用）。失败不阻塞。"""
        if self.cfg.mode != "local" or self.admin_key_file is None:
            return
        try:
            self.admin_key = await ensure_admin_key(self.core, self.admin_key_file)
            if self.admin_key is None:
                logger.warning(
                    "tc_memory: 本地实例已有其他 admin key 且与本插件记录不符，"
                    "无法自动恢复。如需重置：删除 %s 对应的数据目录后重启",
                    self.admin_key_file,
                )
        except TDAMError as e:
            logger.warning("tc_memory: admin key 引导失败: %s", e.message[:80])
        await self._adopt_panel_identities()

    async def _ensure_knowledge(self) -> None:
        if self.knowledge_launcher is not None and not (
            await self.knowledge_launcher.ensure_running()
        ):
            logger.warning(
                "tc_memory: 知识库服务启动失败，wiki/codegraph 工具不可用"
                "（记忆功能不受影响）"
            )
        # 面板与知识库相互独立，各自 fail-soft
        if self.panel_launcher is not None and not (
            await self.panel_launcher.ensure_running()
        ):
            logger.warning("tc_memory: 管理面板启动失败（记忆功能不受影响）")

    def _mark_enabled(self) -> bool:
        self._enabled = True
        vlog(
            logger,
            self.cfg,
            "服务已启用 mode=%s 召回=%s 捕获=%s 面板=%s",
            self.cfg.mode,
            self.cfg.recall_enabled,
            self.cfg.capture_enabled,
            self.panel_launcher is not None,
        )
        if not self.capture_supported:
            logger.warning(
                "tc_memory: 当前 AstrBot 版本无 on_agent_done 钩子（需 >=4.23.1），"
                "对话捕获已禁用，记忆召回不受影响"
            )
        return True

    def _blocked(self) -> bool:
        return (not self._enabled) or time.monotonic() < self._auth_backoff_until

    def _note_auth_error(self, e: TDAMAuthError) -> None:
        self._auth_backoff_until = time.monotonic() + AUTH_BACKOFF_SEC
        logger.error(
            "tc_memory: 鉴权失败（HTTP %s），%ds 内暂停记忆调用。请检查 core_api_key / 三元组配置",
            e.code,
            int(AUTH_BACKOFF_SEC),
        )

    async def recall_for(
        self, sender_id: str, unified_msg_origin: str, prompt: str
    ) -> str | None:
        """召回守卫 + 执行；任何失败返回 None（本轮不注入）。"""
        if self._blocked() or not self.cfg.recall_enabled:
            return None
        identity = self.identity_for(sender_id, unified_msg_origin)
        try:
            return await asyncio.wait_for(
                perform_recall(self.core, self.cache, identity, prompt, self.cfg),
                timeout=self.cfg.recall_timeout_sec,
            )
        except TDAMAuthError as e:
            self._note_auth_error(e)
        except (TDAMError, TimeoutError) as e:
            logger.warning("tc_memory: 召回降级: %s", type(e).__name__)
        return None

    def note_user_message(self, unified_msg_origin: str, text: str) -> None:
        if self._enabled and self.cfg.capture_enabled:
            self.buffer.note_user(unified_msg_origin, text)

    async def capture_after(
        self, sender_id: str, unified_msg_origin: str, assistant_text: str
    ) -> bool:
        """写回本轮对话；失败丢弃不重试（避免故障期重试雪崩）。"""
        if (
            self._blocked()
            or not self.cfg.capture_enabled
            or not self.capture_supported
        ):
            return False
        identity = self.identity_for(sender_id, unified_msg_origin)
        try:
            vlog(
                logger,
                self.cfg,
                "写入 L0 session=%s user=%r assistant=%r",
                identity.session_id,
                short(self.buffer._pending.get(identity.session_id) or ""),
                short(assistant_text),
            )
            return await self.buffer.flush(
                self.core, identity.ids, identity.session_id, assistant_text
            )
        except TDAMAuthError as e:
            self._note_auth_error(e)
        except TDAMError as e:
            logger.warning("tc_memory: 捕获丢弃: %s", e.message[:80])
        return False
