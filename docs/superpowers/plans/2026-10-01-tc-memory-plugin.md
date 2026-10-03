# astrbot_plugin_tc_memory 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 AstrBot 插件 `astrbot_plugin_tc_memory`，接入 TencentDB Agent Memory（召回注入 + 对话捕获 + 8 个 LLM 工具 + /memory 指令组），纯客户端无状态架构。

**Architecture:** 仿官方 openclaw-plugin 的纯客户端分层：`tc_memory/` 包内 client（aiohttp 薄客户端）/ identity / inject_format（纯函数）/ recall / capture / cache，全部不依赖 AstrBot 可独立单测；`main.py` 是唯一的 AstrBot 适配薄层（hooks、tools、commands 注册与装配）。

**Tech Stack:** Python ≥3.10、aiohttp（AstrBot 内置）、pytest + pytest-asyncio + aioresponses（dev）、ruff

**Spec:** `docs/superpowers/specs/2026-10-01-tc-memory-plugin-design.md`

## Global Constraints

- 目标 AstrBot 版本：`>=4.23.1,<5`（`on_agent_done` 需要 4.23.1+），写入 `metadata.yaml` 的 `astrbot_version`
- 插件名固定 `astrbot_plugin_tc_memory`；入口 `main.py`；`@register` 四参数齐全
- 异步 IO 一律 aiohttp，禁止 requests；生产依赖零新增（aiohttp 由 AstrBot 提供）
- 每轮变化的内容**禁止**写入 `req.system_prompt`，只走 `req.extra_user_content_parts`
- 注入块默认 `mark_as_temp()`（不落盘）；仅当 `persist_injected_memory=true` 时不标记
- Gateway 任何故障不得阻塞/打断用户聊天；日志不打印 apiKey、消息全文（截断 80 字）
- v3 API 信封 `{code, message, request_id, data}`；Header：`Authorization: Bearer <apiKey>` + `x-tdai-service-id: <service_id>`；隔离字段 team/agent/user 放 body
- MemoryCore :8420（v3 全 POST + `GET /health`）；MemoryKnowledge :8421/v3
- 代码用 ruff 格式化；每个任务完成即 commit

## Review Focus

1. **空 query 召回**：用户发纯图片/@消息时 `req.prompt` 为空——recall 必须跳过 search_atomic（空 query 会 400），只注入画像/场景/Skill 块（Task 6 测试）
2. **超长消息写入**：capture 内容 >8192 字会 400——`sanitize_message` 必须截断到 8000 字（Task 7 测试）
3. **同 session 连续两条消息**：pending buffer 串话——`note_user` 覆盖式保留最新文本，flush 后清除（Task 7 测试）
4. **401 后退避恢复**：auth 失败进入 60s 退避，期间所有调用短路；退避过期自动恢复（Task 8 测试）
5. **旧版 AstrBot 无 `on_agent_done`**：版本 <4.23.1 时插件加载即 warn 并自我禁用，而不是静默丢捕获（Task 8 测试）

---

### Task 1: 项目骨架与开发环境

**Files:**
- Create: `metadata.yaml`、`_conf_schema.json`、`main.py`（占位）、`requirements-dev.txt`、`pytest.ini`、`tc_memory/__init__.py`、`tests/__init__.py`

**Interfaces:**
- Produces: `_conf_schema.json` 的字段名（后续任务全部引用）：`mode, core_endpoint, core_api_key, service_id, team_id, agent_id, knowledge_enabled, knowledge_endpoint, recall_enabled, capture_enabled, persist_injected_memory, recall_max_results, recall_timeout_sec, user_id_map`，默认值与 spec §5.1 一致

- [ ] **Step 1: 写骨架文件**

`metadata.yaml`：`name: astrbot_plugin_tc_memory`、`author: elecvoid243`、`version: 0.1.0`、`desc` 一句话、`astrbot_version: ">=4.23.1,<5"`。
`_conf_schema.json` 按 spec §5.1 全字段（每项带 description；`persist_injected_memory` 的 description 写清「公网 API 建议关 / 自建推理服务建议开」）。
`pytest.ini`：`asyncio_mode = auto`，`testpaths = ["tests"]`。
`requirements-dev.txt`：`pytest`、`pytest-asyncio`、`aioresponses`、`ruff`。
`main.py` 先放最小可加载插件（`@register` + 空类）。

- [ ] **Step 2: 验证环境**

Run: `pip install -r requirements-dev.txt && pytest tests/ -v`
Expected: 收集 0 个测试，无报错退出

- [ ] **Step 3: Commit**

```bash
git add -A && git commit -m "chore: 插件骨架与测试环境"
```

---

### Task 2: TdMemoryClient —— Core 数据面客户端

**Files:**
- Create: `tc_memory/errors.py`、`tc_memory/client.py`
- Test: `tests/test_client.py`

**Interfaces:**
- Produces（后续所有任务依赖）:

```python
# tc_memory/errors.py
class TDAMError(Exception):
    def __init__(self, code: int, message: str): ...


class TDAMUnavailable(TDAMError): ...  # 网络错误 / 超时 / 5xx，code=-1


class TDAMAuthError(TDAMError): ...  # 401/403


# tc_memory/client.py
@dataclass(frozen=True)
class IsolationIds:
    team_id: str
    agent_id: str
    user_id: str


class TdMemoryClient:
    def __init__(
        self, endpoint: str, api_key: str, service_id: str, timeout_sec: float = 5.0
    ): ...
    async def health(self) -> dict: ...  # GET /health，无鉴权
    async def search_atomic(
        self, ids: IsolationIds, query: str, limit: int = 5
    ) -> list[dict]: ...
    async def read_core(self, ids: IsolationIds) -> str | None: ...  # 无画像返回 None
    async def list_scenarios(self, ids: IsolationIds) -> list[dict]: ...
    async def skill_listing(
        self, ids: IsolationIds
    ) -> str | None: ...  # 预渲染 <available_skills> 文本
    async def skill_search(
        self, ids: IsolationIds, query: str, limit: int = 10
    ) -> list[dict]: ...
    async def skill_get_by_name(self, ids: IsolationIds, name: str) -> dict | None: ...
    async def atomic_query(
        self,
        ids: IsolationIds,
        type: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict: ...
    async def atomic_delete(
        self, ids: IsolationIds, memory_ids: list[str]
    ) -> int: ...  # 返回 deleted_count
    async def atomic_count(self, ids: IsolationIds) -> int: ...
    async def core_count(self, ids: IsolationIds) -> int: ...
    async def add_conversation(
        self, ids: IsolationIds, session_id: str, messages: list[dict]
    ) -> dict: ...
    async def conversation_search(
        self, ids: IsolationIds, query: str, limit: int = 5
    ) -> list[dict]: ...
    async def conversation_query(
        self, ids: IsolationIds, session_id: str, limit: int = 100, offset: int = 0
    ) -> dict: ...
    async def conversation_delete(
        self, ids: IsolationIds, message_ids: list[str]
    ) -> int: ...
    async def aclose(self) -> None: ...
```

所有数据面方法：POST `<endpoint>/v3/<path>`，body = 业务字段 + `team_id/agent_id/user_id`（来自 ids）；`code != 0` 抛 `TDAMError(code, message)`；HTTP 401/403 → `TDAMAuthError`；`aiohttp.ClientError` / `asyncio.TimeoutError` / HTTP 5xx → `TDAMUnavailable`。endpoint 尾部斜杠需剔除。

- [ ] **Step 1: 写失败测试**

`tests/test_client.py` 用 aioresponses，至少覆盖：

```python
async def test_search_atomic_success(): ...  # 信封 code=0 → 返回 data["items"]，断言请求头含 Bearer 与 x-tdai-service-id，body 含三元组
async def test_envelope_error_raises_tdam_error(): ...  # code=40401 → TDAMError，code/message 保留
async def test_http_401_raises_auth_error(): ...
async def test_timeout_raises_unavailable(): ...
async def test_read_core_empty_returns_none(): ...  # data 无 content → None
async def test_add_conversation_posts_session_and_messages(): ...
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_client.py -v`
Expected: FAIL（`ModuleNotFoundError: tc_memory.client`）

- [ ] **Step 3: 实现 client.py**

内部一个 `_post(path, body) -> dict` 私有方法统一处理：建/复用 `aiohttp.ClientSession`（懒初始化）、`ClientTimeout(total=timeout_sec)`、headers、信封解析、异常映射。公开方法均为薄封装。`aclose` 关闭 session。

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_client.py -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add tc_memory/ tests/test_client.py && git commit -m "feat: TdMemoryClient core 数据面客户端"
```

---

### Task 3: KnowledgeClient —— 知识库客户端

**Files:**
- Create: `tc_memory/knowledge_client.py`
- Test: `tests/test_knowledge_client.py`

**Interfaces:**
- Produces:

```python
class KnowledgeClient:
    def __init__(self, endpoint: str, timeout_sec: float = 5.0): ...
    async def wiki_search(
        self, query: str, limit: int = 5
    ) -> list[dict]: ...  # POST {endpoint}/v3/wiki/search
    async def wiki_read(self, path: str) -> str | None: ...  # POST /v3/wiki/page/read
    async def codegraph_search(self, query: str, limit: int = 5) -> list[dict]: ...
    async def codegraph_explore(self, symbol: str) -> dict | None: ...
    async def aclose(self) -> None: ...
```

异常模型复用 Task 2 的 `TDAMError/TDAMUnavailable`；Knowledge 的响应结构若不是 code 信封（读 `MemoryKnowledge/openapi.yaml` 对应端点的 response schema 确认），按实际结构解析并在测试里钉死。

- [ ] **Step 1: 写失败测试**（4 个方法各一：成功路径 + 一个超时映射 TDAMUnavailable）
- [ ] **Step 2: 跑测试确认失败** — `pytest tests/test_knowledge_client.py -v`，FAIL
- [ ] **Step 3: 实现**（结构与 TdMemoryClient 相同，独立 session）
- [ ] **Step 4: 跑测试确认通过** — 全 PASS
- [ ] **Step 5: Commit** — `git commit -m "feat: KnowledgeClient 知识库客户端"`

---

### Task 4: identity —— 身份映射

**Files:**
- Create: `tc_memory/config.py`、`tc_memory/identity.py`
- Test: `tests/test_identity.py`

**Interfaces:**
- Produces:

```python
# tc_memory/config.py
@dataclass(frozen=True)
class PluginConfig:
    mode: str
    core_endpoint: str
    core_api_key: str
    service_id: str
    team_id: str
    agent_id: str
    knowledge_enabled: bool
    knowledge_endpoint: str
    recall_enabled: bool
    capture_enabled: bool
    persist_injected_memory: bool
    recall_max_results: int
    recall_timeout_sec: int
    user_id_map: dict


def config_from_astrbot(cfg: dict) -> PluginConfig: ...  # 缺省值按 spec §5.1


# tc_memory/identity.py
@dataclass(frozen=True)
class ResolvedIdentity:
    ids: IsolationIds  # 来自 Task 2
    session_id: str


def resolve_identity(
    sender_id: str, unified_msg_origin: str, cfg: PluginConfig
) -> ResolvedIdentity: ...


# user_id 规则：cfg.user_id_map 命中 → 映射值；否则 "u_" + sender_id；sender_id 为空 → "default"
```

保持纯函数：不 import AstrBot，event 字段提取留给 main.py。

- [ ] **Step 1: 写失败测试**：默认规则（`u_<sender>`）、user_id_map 命中、空 sender 回落 default、session_id 原样透传、config 缺省值填充
- [ ] **Step 2: 确认失败** — `pytest tests/test_identity.py -v`，FAIL
- [ ] **Step 3: 实现**
- [ ] **Step 4: 确认通过**
- [ ] **Step 5: Commit** — `git commit -m "feat: 配置模型与身份映射"`

---

### Task 5: inject_format —— 注入文本渲染（纯函数）

**Files:**
- Create: `tc_memory/inject_format.py`
- Test: `tests/test_inject_format.py`

**Interfaces:**
- Produces:

```python
def render_memory_block(
    l1_items: list[dict], persona: str | None, scenes: list[dict]
) -> str | None:
    """三路全空 → None；否则渲染 <relevant-memories> 块（persona → L1 列表 → 场景导航）"""


def render_skill_block(listing_text: str | None) -> str | None:
    """listing 为空 → None；否则原样包进 <available_skills> 块"""


def render_injection(memory_block: str | None, skill_block: str | None) -> str | None:
    """组合最终注入文本；两块都空 → None"""
```

渲染格式参照 `MemoryCore/openclaw-plugin/src/format.ts` 的 `<relevant-memories>` 结构。

- [ ] **Step 1: 写失败测试**：全空→None、仅 persona、仅 L1（含 content/background 排版）、仅场景、skill 块透传、组合顺序（skill 块在 memory 块之前，便于模型先看到工具清单）
- [ ] **Step 2: 确认失败**
- [ ] **Step 3: 实现**
- [ ] **Step 4: 确认通过**
- [ ] **Step 5: Commit** — `git commit -m "feat: 注入文本渲染"`

---

### Task 6: recall —— 召回流水线 + TTL 缓存

**Files:**
- Create: `tc_memory/cache.py`、`tc_memory/recall.py`
- Test: `tests/test_recall.py`

**Interfaces:**
- Consumes: Task 2 `TdMemoryClient`、Task 4 `ResolvedIdentity/PluginConfig`、Task 5 `render_*`
- Produces:

```python
# tc_memory/cache.py
class TTLCache:
    def get(self, key: str) -> Any | None: ...
    def set(self, key: str, value: Any, ttl_sec: float) -> None: ...


# tc_memory/recall.py
PERSONA_TTL_SEC = 600


async def perform_recall(
    client: TdMemoryClient,
    cache: TTLCache,
    identity: ResolvedIdentity,
    query: str,
    cfg: PluginConfig,
) -> str | None:
    """并行四路；query 为空跳过 search_atomic（Review Focus #1）；
    persona/skill_listing 走 cache（persona TTL 600s，listing 同）；任一路失败仅缺该路；
    返回 render_injection 的结果"""
```

- [ ] **Step 1: 写失败测试**（mock client 的 4 个方法）：正常全量注入、空 query 不调用 search_atomic、单路异常不影响其他路、persona 命中缓存时不再请求、全空返回 None
- [ ] **Step 2: 确认失败**
- [ ] **Step 3: 实现**（`asyncio.gather(*, return_exceptions=True)`；cache key 含 user_id）
- [ ] **Step 4: 确认通过**
- [ ] **Step 5: Commit** — `git commit -m "feat: 召回流水线与 TTL 缓存"`

---

### Task 7: capture —— 消息清洗与写回

**Files:**
- Create: `tc_memory/capture.py`
- Test: `tests/test_capture.py`

**Interfaces:**
- Consumes: Task 2 `TdMemoryClient`
- Produces:

```python
MAX_CONTENT_LEN = 8000


def sanitize_message(text: str) -> str:
    """去 fenced code block、去 <relevant-memories>/<available_skills> 标签段（防御）、strip；>8000 字截断"""


class CaptureBuffer:
    def note_user(self, session_id: str, text: str) -> None:
        """覆盖式保留该 session 最新 user 文本（Review Focus #3）"""

    async def flush(
        self,
        client: TdMemoryClient,
        ids: IsolationIds,
        session_id: str,
        assistant_text: str,
    ) -> bool:
        """清洗后 add_conversation([user, assistant])；任一侧清洗后为空则不发；返回是否实际写入"""
```

- [ ] **Step 1: 写失败测试**：sanitize 去代码块/标签/截断 8000；flush 正常写入两条（断言 messages 顺序与 role）；user 为空不发；覆盖语义（note 两次只留后者）；flush 后 buffer 清空
- [ ] **Step 2: 确认失败**
- [ ] **Step 3: 实现**
- [ ] **Step 4: 确认通过**
- [ ] **Step 5: Commit** — `git commit -m "feat: 对话捕获与清洗"`

---

### Task 8: main.py —— AstrBot 装配层（hooks + 降级 + 退避）

**Files:**
- Modify: `main.py`
- Test: `tests/test_main_hooks.py`

**Interfaces:**
- Consumes: Task 2–7 全部
- Produces: 插件类 `TcMemoryPlugin(Star)`，公开方法名即 hook 名：`on_llm_request_hook`、`on_agent_done_hook`

装配逻辑：

```text
__init__: 读配置 → 构建 TdMemoryClient/KnowledgeClient/TTLCache/CaptureBuffer；self._enabled=False
@filter.on_plugin_loaded(): health() 成功 → _enabled=True；
  失败 → warn（指出 core_endpoint 与启动指引），保持禁用
  检测 on_agent_done 钩子是否可用（getattr 检查），不可用 → error 并 _enabled=False（Review Focus #5）
@filter.on_llm_request(): _enabled 且 recall_enabled →
  identity = resolve_identity(event.get_sender_id(), event.unified_msg_origin, cfg)
  text = await perform_recall(...)  # asyncio.wait_for 包 recall_timeout_sec
  text 非空 → part = TextPart(text)；cfg.persist_injected_memory 为 False 时 part.mark_as_temp()；
  req.extra_user_content_parts.append(part)；capture_buffer.note_user(session_id, req.prompt)
@filter.on_agent_done(): _enabled 且 capture_enabled →
  assistant_text = llm_response.result_chain 提取纯文本
  await capture_buffer.flush(...)  # try/except TDAMError → warn 丢弃，不重试
```

401/403 退避：hook 内捕获 `TDAMAuthError` → error 日志 + `self._auth_backoff_until = now + 60s`；退避期内 hook 直接返回（Review Focus #4），过期自动恢复。

- [ ] **Step 1: 写失败测试**

astrbot 包可导入时跑（`pytest.importorskip("astrbot")`），mock event/req/llm_response（SimpleNamespace 即可，hook 只用 `get_sender_id()/unified_msg_origin/prompt/extra_user_content_parts/result_chain`）：

```python
async def test_injects_part_marked_temp_by_default(): ...  # part 带 _no_save
async def test_persist_config_disables_mark_temp(): ...  # persist_injected_memory=True → 不带
async def test_disabled_plugin_does_nothing(): ...
async def test_recall_failure_leaves_request_untouched(): ...
async def test_auth_error_triggers_60s_backoff(): ...  # 第二次调用不再触达 client
async def test_agent_done_flushes_capture(): ...
```

- [ ] **Step 2: 确认失败**
- [ ] **Step 3: 实现**（`from astrbot.core.agent.message import TextPart`）
- [ ] **Step 4: 确认通过**
- [ ] **Step 5: Commit** — `git commit -m "feat: 插件装配层 hooks 与降级退避"`

---

### Task 9: tools —— 8 个 LLM 工具

**Files:**
- Create: `tc_memory/tools.py`；Modify: `main.py`（注册）
- Test: `tests/test_tools.py`

**Interfaces:**
- Consumes: Task 2/3 client、Task 4 identity
- Produces: 8 个工具方法（`@filter.llm_tool`），docstring 按 AstrBot 规范写 `Args:`

| 工具 | 调用 | 返回格式 |
|---|---|---|
| `memory_search(query)` | `search_atomic` | 编号列表文本 |
| `conversation_search(query)` | `conversation_search` | 编号列表（含时间） |
| `skill_search(query)` | `skill_search` | 名称+描述列表 |
| `skill_view(name)` | `skill_get_by_name` | skill 正文全文 |
| `wiki_search(query)` | `KnowledgeClient.wiki_search` | 标题+路径列表 |
| `wiki_read(path)` | `wiki_read` | 页面正文 |
| `codegraph_search(query)` | `codegraph_search` | 符号列表 |
| `codegraph_explore(symbol)` | `codegraph_explore` | 调用关系文本 |

knowledge 系 4 个仅在 `knowledge_enabled=True` 时注册（main.py 条件注册；`@filter.llm_tool` 写在类上的则改为在 `__init__` 用 `context.add_llm_tools()` 注册——实现时二选一，保持 main.py 单一注册点）。
工具内身份解析同 hook；异常 → 返回友好错误文本（不抛出，避免打断工具循环）。

- [ ] **Step 1: 写失败测试**：每工具 mock client 断言调用参数与返回文本格式；client 抛 TDAMUnavailable → 返回错误提示文本；knowledge 关闭时 4 个工具未注册
- [ ] **Step 2: 确认失败**
- [ ] **Step 3: 实现 + main.py 注册**
- [ ] **Step 4: 确认通过**
- [ ] **Step 5: Commit** — `git commit -m "feat: 8 个 LLM 记忆/技能/知识工具"`

---

### Task 10: commands —— /memory 指令组

**Files:**
- Create: `tc_memory/commands.py`；Modify: `main.py`（注册 command_group）
- Test: `tests/test_commands.py`

**Interfaces:**
- Consumes: Task 2 client、Task 4 identity

| 指令 | 实现 |
|---|---|
| `/memory search <词>` | `search_atomic` → 编号列表 |
| `/memory list` | `atomic_query(limit=10)` → 带 id 列表（供 forget 引用） |
| `/memory remember <内容>` | `add_conversation(session_id, [{"role":"user","content":"请记住：..."}])` → 回复「已提交，记忆稍后生效」 |
| `/memory forget <id>` | `atomic_delete([id])` → 回复删除结果 |
| `/memory status` | `health()` + `atomic_count` + `core_count`；异常 → 输出服务不可达 + 启动指引文案 |
| `/memory clear` | `@filter.permission_type(PermissionType.ADMIN)`；`conversation_query` 取 id → `conversation_delete`；执行前回复确认提示，用户回复「确认」才执行（用 session 简单挂起态或 AstrBot 会话控制） |

- [ ] **Step 1: 写失败测试**：每个指令 mock client 断言调用与输出；forget 不存在 id 的错误透传；clear 非管理员被拒
- [ ] **Step 2: 确认失败**
- [ ] **Step 3: 实现 + main.py 注册**
- [ ] **Step 4: 确认通过**
- [ ] **Step 5: Commit** — `git commit -m "feat: /memory 指令组"`

---

### Task 11: README 与手动 E2E 清单

**Files:**
- Create: `README.md`

内容：功能简介、架构图（spec §2）、安装（插件市场/git clone 到 `AstrBot/data/plugins/`）、**服务启动前置**（`start-all.sh` 或 standalone，含 LLM Key 说明）、配置项表（含 `persist_injected_memory` 权衡表）、指令与工具列表、local/server 双模式说明。

- [ ] **Step 1: 写 README**
- [ ] **Step 2: 全量测试 + lint**

Run: `pytest tests/ -v && ruff check . && ruff format --check .`
Expected: 全 PASS，无 lint 错误

- [ ] **Step 3: 手动 E2E 清单**（写进 README「验证」一节，人工执行）：

```text
1. 启动 standalone Gateway → AstrBot 加载插件 → /memory status 显示 ok
2. /memory remember 我叫小明 → 聊几轮别的 → 问「我叫什么」→ 命中记忆
3. 查看 AstrBot 会话历史 DB：注入块默认未落盘；开 persist_injected_memory 后落盘
4. 停掉 Gateway → 聊天不受影响；/memory status 给出启动指引
```

- [ ] **Step 4: Commit** — `git commit -m "docs: README 与 E2E 验证清单"`

---

## Self-Review 结论

- **Spec 覆盖**：spec §2–§8 每节均有对应任务（§3→Task 1-8 模块边界；§4→Task 6/7/8；§5→Task 1/4；§6.1→Task 9；§6.2→Task 10；§7→Task 8 降级退避 + 各 client 异常映射；§8→各任务测试 + Task 11）
- **类型一致性**：`IsolationIds/ResolvedIdentity/PluginConfig/TDAMError` 系列在 Task 2/4 定义，后续任务引用一致
- **Review Focus**：5 项均已钉入所属任务的测试步骤
- **比例**：11 个任务，每任务 5 步，计划长度约为 spec 的 1.5 倍，未转录实现代码
