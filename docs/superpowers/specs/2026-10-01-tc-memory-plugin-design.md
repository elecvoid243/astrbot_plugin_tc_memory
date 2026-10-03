# astrbot_plugin_tc_memory 设计文档

> 创建：2026-10-01 · 作者：elecvoid243 · 状态：待审阅
> 目标仓库：`G:\github\astrbot_plugin_tc_memory`
> 对接服务：TencentDB Agent Memory v2.0.1（MemoryCore Gateway :8420 + MemoryKnowledge :8421）

## 1. 背景与目标

为 AstrBot（v4.x）开发一个插件，接入 TencentDB Agent Memory 服务，让 AstrBot 驱动的聊天机器人获得跨会话长期记忆能力：

- 每轮对话前自动从记忆服务**召回**相关记忆注入上下文（recall）
- 每轮对话后把本轮内容**写回**记忆服务，服务端异步蒸馏 L1–L3（capture）
- 给 LLM 提供主动检索记忆 / Skill / 知识的工具
- 给用户提供 `/memory` 指令组管理自己的记忆

设计蓝本是官方 `MemoryCore/openclaw-plugin`（纯客户端架构：不做抽取/索引/存储，只做框架适配 + HTTP 调用），映射到 AstrBot 的插件机制上。

### 已确认的关键决策

| 决策点 | 结论 |
|---|---|
| 部署形态 | 双模式 `local` / `server`（对齐 openclaw-plugin dual-mode） |
| 功能范围 | 完整版：召回 + 捕获 + 8 个 LLM 工具 + /memory 指令组 |
| 客户端实现 | 自写 aiohttp 薄客户端（官方 Python SDK 未发布 PyPI，不 vendor） |
| 运行时架构 | Hook 直通式：`on_llm_request` 实时召回、`on_agent_done` 写回 |
| 注入落盘 | 可配置 `persist_injected_memory`，默认 `false`（不落盘，走 `_no_save`） |

### 非目标（本期不做）

- **不管理记忆服务的生命周期**：插件不启动/停止/守护 Gateway 进程。服务由用户通过官方途径先行启动——Docker 一键 `deploy/global-images/start-all.sh`，或 Node 版 standalone（`tdai-gateway.standalone.yaml`，零外部依赖但需 Node ≥22 + 一份供 L1 抽取用的 LLM API Key）。插件加载时健康探测失败则自我禁用，`/memory status` 输出服务状态与启动指引文案
- 不做本地抽取/索引/向量库（全部委托服务端管线）
- 不写本地数据库；唯一缓存是进程内存 TTL 缓存
- 不接 MemoryProxy 流量层路线（AstrBot 有原生钩子，无需协议级代理）
- 不做记忆提取预览（generation-log）面板

## 2. 总体架构

```text
┌─ AstrBot ─────────────────────────────────────────────────┐
│  astrbot_plugin_tc_memory（纯客户端插件，无状态）            │
│                                                            │
│  on_llm_request ──▶ Recall 模块 ──┐                        │
│  on_agent_done   ──▶ Capture 模块 ─┤                        │
│  @llm_tool × 8   ──▶ Tools 模块 ──┼─▶ TdMemoryClient       │
│  /memory 指令组  ──▶ Commands 模块─┘   (aiohttp 薄客户端)    │
└────────────────────────────────────┼───────────────────────┘
                                     ▼
              ┌── MemoryCore Gateway :8420 /v3/* ──┐
              │  conversation/add · atomic/search   │
              │  core/read · scenario/ls · skill/*  │
              └─────────────────────────────────────┘
              ┌── MemoryKnowledge :8421 /v3/* ──────┐
              │  wiki/search · wiki/page/read       │
              │  code-graph/search · explore        │
              └─────────────────────────────────────┘
```

### 三条铁律

1. **永不阻塞聊天**：Gateway 调用并行 + 短超时（召回 3s、捕获 5s），任何失败记 warn 日志后静默降级；召回失败 = 本轮不注入任何内容。
2. **不动 system_prompt**：每轮变化的内容只走 `req.extra_user_content_parts`。
3. **插件无状态**：不创建本地存储；对 AstrBot 框架的会话历史 DB，默认通过 `_no_save` 保证注入物零落盘（可由 `persist_injected_memory` 改为落盘）。

## 3. 模块拆分

```text
astrbot_plugin_tc_memory/
├── metadata.yaml            # name: astrbot_plugin_tc_memory
├── _conf_schema.json        # 配置 schema（AstrBot WebUI 可视化编辑）
├── main.py                  # Star 入口：装配各模块，注册 hooks/tools/commands
└── tc_memory/
    ├── client.py            # TdMemoryClient：aiohttp 薄客户端
    ├── identity.py          # 身份映射：event → (team_id, agent_id, user_id, session_id)
    ├── recall.py            # 召回：三路并行 + 格式化注入
    ├── capture.py           # 捕获：本轮消息清洗 → add_conversation
    ├── inject_format.py     # <relevant-memories> / <available_skills> 渲染（纯函数）
    ├── tools.py             # 8 个 @llm_tool
    ├── commands.py          # /memory 指令组
    └── cache.py             # TTL 缓存（persona、skill listing）
```

单元职责：client 只管 HTTP 与信封解析；identity 只做 event→ID 映射；recall/capture 是两条业务流水线；inject_format 是纯函数便于单测；tools/commands 是 client 的薄封装。

### 插件入口约束（AstrBot 侧）

- 入口文件 `main.py`，类继承 `Star`，`@register(name, desc, author, version)` 四参数齐全
- 异步 IO 一律 aiohttp，不用 requests
- 用 ruff 格式化

## 4. 数据流

### 4.1 召回（每轮 LLM 请求前）

```text
on_llm_request(event, req)
  identity.resolve(event) → (team_id, agent_id, user_id, session_id)
  并行（asyncio.gather(return_exceptions=True)，总超时 recall_timeout_sec）:
    ├─ client.search_atomic(query=req.prompt, limit=recall_max_results)  → L1
    ├─ cache.read_core()        (TTL 10min)                              → L3 画像
    ├─ client.list_scenarios()                                           → L2 场景导航
    └─ cache.skill_listing()    (会话级 TTL)                              → <available_skills>
  inject_format.render(...) → TextPart
  若 persist_injected_memory=false（默认）→ part.mark_as_temp()
  req.extra_user_content_parts.append(part)
  同时：把本轮 user 消息文本暂存进内存 pending buffer（key=session_id）
```

- 召回查询词直接用 `req.prompt`（本轮用户原文），不拼接历史——历史已在 contexts 中，拼接反而稀释检索精度。
- 任一路失败只影响该路内容，不影响其他路注入。

### 4.2 注入落盘语义（重要）

AstrBot 会把 `extra_user_content_parts` 合并进当轮 user 消息并**默认持久化到会话历史 DB**（`ProviderRequest.assemble_context()` → `dump_messages_with_checkpoints()`）。

框架提供 part 级豁免：`ContentPart.mark_as_temp()` 置 `_no_save=True`：

- 持久化时 part 级过滤（`dump_messages_with_checkpoints` 跳过）
- 发往 LLM 前 OpenAI / Anthropic / Gemini source 各自剥掉 `_no_save` 键
- 框架 persona 预设对话（persona_mgr）使用同一机制，属官方用法

**权衡与配置项 `persist_injected_memory`（bool，默认 `false`）：**

| | 不落盘（默认） | 落盘 |
|---|---|---|
| 上下文洁净度 | 每轮只有最新召回，无累积 | 历史逐轮堆积记忆块，过期记忆滞留 |
| 前缀 KV 缓存 | 注入点之后缓存全失效（历史中间消息被改写） | 历史 append-only，缓存命中可延伸到上轮回复末尾 |
| 遗忘生效速度 | 下一轮立即生效 | 已落盘旧块仍在历史里（可配合会话重置清除） |
| 适用场景 | 公网 API（按 token 计费） | 自建推理服务（vLLM/SGLang，KV cache 省算力） |

捕获模块从 `event` + `LLMResponse.result_chain` 取干净文本，两种模式下都不会把注入块录进 L0——因此不需要 openclaw 版的 sanitize 钩子。

### 4.3 捕获（Agent 完成后）

```text
on_agent_done(event, run_context, llm_response)
  capture.flush(session_id):
    从 pending buffer 取本轮 user 文本 + llm_response 最终答复文本
    清洗（去代码块/噪声过滤，对齐 openclaw sanitize 逻辑）
    client.add_conversation(session_id, [user_msg, assistant_msg])
    → Gateway 异步触发 L1 抽取管线
```

选 `on_agent_done` 而非 `on_llm_response`：工具循环中后者每轮触发，会录进中间态碎片；前者拿到最终答复，一轮对话只写一次。`on_agent_done` 需要 AstrBot ≥ v4.23.1，`metadata.yaml` 中声明 `astrbot_version: ">=4.23.1,<5"`。

## 5. 配置 Schema 与身份映射

### 5.1 `_conf_schema.json`

```jsonc
{
  "mode":            { "type": "string", "default": "local", "options": ["local", "server"] },
  "core_endpoint":   { "type": "string", "default": "http://127.0.0.1:8420" },
  "core_api_key":    { "type": "string", "default": "local" },
  "service_id":      { "type": "string", "default": "default" },
  "team_id":         { "type": "string", "default": "default" },
  "agent_id":        { "type": "string", "default": "default" },
  "knowledge_enabled":  { "type": "bool", "default": false },
  "knowledge_endpoint": { "type": "string", "default": "http://127.0.0.1:8421" },
  "recall_enabled":  { "type": "bool", "default": true },
  "capture_enabled": { "type": "bool", "default": true },
  "persist_injected_memory": { "type": "bool", "default": false },
  "recall_max_results": { "type": "int", "default": 5 },
  "recall_timeout_sec": { "type": "int", "default": 3 },
  "user_id_map":     { "type": "object", "default": {} }
}
```

local 模式零配置开箱即用；server 模式填面板创建的 team/agent 与真实 apiKey/service_id。
`persist_injected_memory` 的描述文案写清第 4.2 节权衡表的两个适用场景。

### 5.2 身份映射（identity.py）

```text
team_id    ← 配置（local 默认 "default"）
agent_id   ← 配置（代表这个 AstrBot 实例本身）
user_id    ← user_id_map 查表；未命中则 "u_" + event.get_sender_id()
session_id ← event.unified_msg_origin   # 平台:消息类型:会话号，稳定且唯一
```

私聊中每个好友是独立 user + 独立 session；群聊中**群是 session、发言者是 user**——同群成员共享群会话上下文，各自积累个人记忆。server 模式下用 `user_id_map`（`{"astrbot_sender_id": "面板user_id"}`）映射到面板已有用户。

### 5.3 Gateway 调用约定

- 全部 POST + JSON，信封 `{code, message, request_id, data}`，`code != 0` 抛 `TDAMError`
- Header：`Authorization: Bearer <core_api_key>` + `x-tdai-service-id: <service_id>`
- 隔离字段 team/agent/user 放 body
- 健康检查：`GET /health`（非 v3，无鉴权）

## 6. LLM 工具与用户指令

### 6.1 LLM 工具（@filter.llm_tool，docstring 生成 schema）

| 工具 | 端点 | 注册条件 |
|---|---|---|
| `memory_search(query)` | `/v3/atomic/search` | 总是 |
| `conversation_search(query)` | `/v3/conversation/search` | 总是 |
| `skill_search(query)` | `/v3/skill/search` | 总是 |
| `skill_view(name)` | `/v3/skill/get-by-name` | 总是 |
| `wiki_search(query)` | Knowledge `/v3/wiki/search` | `knowledge_enabled` |
| `wiki_read(path)` | Knowledge `/v3/wiki/page/read` | `knowledge_enabled` |
| `codegraph_search(query)` | Knowledge `/v3/code-graph/search` | `knowledge_enabled` |
| `codegraph_explore(symbol)` | Knowledge `/v3/code-graph/explore` | `knowledge_enabled` |

`skill_view` 配合召回注入的 `<available_skills>` 清单使用（清单文本引导 LLM 主动加载相关 Skill）。

### 6.2 `/memory` 指令组

| 指令 | 行为 |
|---|---|
| `/memory search <词>` | 检索我的记忆并格式化返回 |
| `/memory list [type]` | 最近记忆列表（带 id，供 forget 使用） |
| `/memory remember <内容>` | 写一条 `请记住：...` 的 conversation 消息，依赖服务端管线异步抽取（v3 无 atomic/create，无法同步成条），回复"已提交，稍后生效" |
| `/memory forget <id>` | `atomic/delete` 删除指定记忆 |
| `/memory status` | `/health` + 各层计数（atomic/core/conversation count）；服务不可达时输出启动指引（指向官方 start-all.sh / standalone 文档） |
| `/memory clear` | 清空当前 session 记忆：`conversation/query` 取本会话消息 id → `conversation/delete` 批量删除（L0 删除后对应 L1 由服务端级联处理）；管理员限定 + 二次确认 |

## 7. 错误处理

| 场景 | 行为 |
|---|---|
| 加载时健康探测失败（`on_plugin_loaded`） | 整体功能禁用 + warn 指出配置项，不影响 AstrBot 聊天 |
| 召回超时/失败 | 本轮不注入任何内容 |
| 捕获失败 | warn 后丢弃，不重试（避免故障期重试雪崩） |
| 429 / 配额超限 | warn + 跳过本轮 |
| 401 / 403 | error 提示检查 apiKey/三元组，进入 60s 退避不再调用 |
| 日志纪律 | 永不打印 apiKey、user-key、消息全文（截断 80 字） |

## 8. 测试策略

- 框架：`pytest + pytest-asyncio`（AstrBot 插件惯例，不进 AstrBot 主仓测试体系）
- 纯函数单测：`inject_format` 渲染、`identity` 映射、消息清洗
- client 单测：`aioresponses` mock HTTP——信封解析、错误码映射、超时
- hook 集成测试：mock event/req/resp，断言：
  - 注入块追加到 `extra_user_content_parts`
  - 默认带 `_no_save`；`persist_injected_memory=true` 时不带
  - Gateway 失败时 req 不被修改
- 指令测试：mock client 断言调用参数与输出格式
- 手动 E2E：standalone Gateway 全流程（召回→注入→捕获→/memory status）

## 9. 参考依据

- 官方客户端插件蓝本：`MemoryCore/openclaw-plugin`（recall/capture/tools/sanitize 分层）
- v3 API 契约：`MemoryCore/v3-api-memorycore-doc.md`（108 接口、信封、鉴权分层、隔离三元组）
- Knowledge API：`MemoryKnowledge/openapi.yaml`（:8421/v3）
- Skill 注入语义：`MemoryProxy/src/injection/injectors/skill-injector.ts`（`<available_skills>` 清单 + skill_view 加载）
- AstrBot 钩子与 `_no_save` 机制：`astrbot/core/provider/entities.py`、`astrbot/core/agent/message.py`、`astrbot/core/pipeline/process_stage/method/agent_sub_stages/internal.py`
