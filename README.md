# astrbot_plugin_tc_memory

为 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 接入 [TencentDB Agent Memory](https://github.com/Tencent/TencentDB-Agent-Memory)，让机器人获得**跨会话长期记忆**。

- 🧠 每轮对话前自动召回相关记忆注入上下文（L1 记忆 + L3 画像 + 场景导航 + 已装备技能）
- 💾 每轮对话后自动写回，服务端异步提炼为长期记忆
- 🛠️ 8 个 LLM 工具：记忆/对话/技能/Wiki/代码图谱检索
- ⌨️ `/memory` 指令组：查看、搜索、手动记忆、遗忘、清空

## 架构

纯客户端插件：不做抽取/索引/存储，全部通过 HTTP 调用记忆服务。

```text
AstrBot ── 本插件 ──▶ MemoryCore Gateway :8420（记忆读写）
                  └─▶ MemoryKnowledge :8421（Wiki/CodeGraph，可选）
```

## 前置：记忆服务从哪来

插件支持两种部署形态（`mode` 配置）：

**`local`（默认）· 内嵌 standalone**：gateway（记忆内核）自动拉起；若开启 `knowledge_enabled`，知识库服务（Wiki/CodeGraph）也一并自动拉起（`external_tools/tc-memory-knowledge`），其 LLM 同样复用所选 provider。插件启动时自动拉起 `external_tools/tc-memory-gateway` 里的打包服务（Node 运行时复用 `external_tools/codegraph-win32-x64/node.exe`），数据落在 `data/plugin_data/astrbot_plugin_tc_memory/`。**仅需填 `local_llm_*` 三项**（记忆提炼要一份 LLM，用便宜的模型即可；Key 只以环境变量传给子进程，不写入文件）。若 `core_endpoint` 上已有手动启动的服务在运行，插件会直接复用而不是重复拉起。

**`server` · 远端服务**：连接服务器上的 Docker 部署（`deploy/global-images/start-all.sh` 启动的那套），填 `core_endpoint` / `core_api_key` / `service_id` / `team_id` / `agent_id`。

验证服务活着：浏览器打开 `http://127.0.0.1:8420/health` 应返回 `{"status":"ok"...}`。

## 安装

```bash
cd AstrBot/data/plugins
git clone <本仓库地址> astrbot_plugin_tc_memory
```

在 AstrBot WebUI 重启/重载插件。local 模式零配置即用。

## 配置

| 配置项 | 默认 | 说明 |
|---|---|---|
| `mode` | `local` | `local` 本地单机 / `server` 团队服务 |
| `core_endpoint` | `http://127.0.0.1:8420` | MemoryCore Gateway 地址 |
| `core_api_key` | `local` | server 模式填真实 Key |
| `team_id` / `agent_id` | `default` | server 模式填面板创建的 ID |
| `recall_enabled` / `capture_enabled` | `true` | 召回注入 / 对话写回开关 |
| `persist_injected_memory` | `false` | 注入的记忆是否写入会话历史，见下表 |
| `knowledge_enabled` | `false` | 启用 Wiki/CodeGraph 工具 |
| `knowledge_wiki_id` / `knowledge_codegraph_id` | 空 | 知识实例 ID（面板创建后获得） |
| `user_id_map` | `{}` | AstrBot 用户 → 记忆用户的映射（可选） |

### `persist_injected_memory` 怎么选

| | 关闭（默认） | 开启 |
|---|---|---|
| 上下文 | 每轮只有最新召回，干净 | 历史逐轮堆积记忆块 |
| 前缀 KV 缓存 | 注入点后缓存失效 | append-only，命中率最大 |
| 适用 | 公网 API（按 token 计费） | 自建推理（vLLM/SGLang） |

### 身份与隔离

- 每个聊天用户自动获得独立记忆空间（`u_<发送者ID>`），群聊中群=会话、发言者=用户
- server 模式用 `user_id_map` 把 AstrBot 用户映射到面板用户

## /memory 指令

| 指令 | 说明 |
|---|---|
| `/memory search <词>` | 搜索我的记忆 |
| `/memory list` | 最近记忆（带 id） |
| `/memory remember <内容>` | 手动记一条（服务端异步提炼，稍后生效） |
| `/memory forget <id>` | 删除一条记忆 |
| `/memory status` | 服务状态与我的记忆统计 |
| `/memory clear` | 清空当前会话对话记录（管理员，需 `/memory clear confirm` 二次确认） |

## 验证

1. `/memory status` 显示服务 ok
2. `/memory remember 我叫小明` → 聊几句别的 → 问「我叫什么」→ 命中
3. 查 AstrBot 会话历史：默认注入块未落盘；开 `persist_injected_memory` 后落盘
4. 停掉记忆服务 → 聊天不受影响；`/memory status` 给出启动指引

## 开发

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt
.venv/Scripts/python -m pytest tests/     # 单测（不依赖 AstrBot）
ruff check . && ruff format .
```
