# Changelog

## 0.2.0 — 2026-10-07

### ✨ 新增

- **内嵌服务全家桶（local 模式）**：插件自动拉起并看守三个本地服务——记忆内核（gateway）、知识库（MemoryKnowledge）、管理面板（MemoryPanel），全部免安装打包于 `external_tools/`
- **管理面板集成**：面板嵌入插件 WebUI 页面（iframe），含 user_key 自动引导（`sk-mem-*` 生成/持久化/自愈）与一键复制
- **提炼 LLM 从 provider 选择**：地址/Key/模型自动从所选 AstrBot provider 读取（含面板实例 ID 自动对齐）
- **详细日志开关**（`verbose_logging`）：召回明细、写入内容、工具调用，内容截断防洪水
- **知识库工具**：`team_wiki_search` / `team_wiki_read` / `team_codegraph_search` / `team_codegraph_explore`（`knowledge_enabled` 控制注册与否）
- **引擎参数常用子集**可配置（提炼开关/频率、画像间隔、召回条数与阈值），启动时合并生成 effective 配置，包内模板保持只读
- **旧数据迁移脚本** `scripts/migrate_isolation.py`（含备份、FTS 同步、dry-run）

### 🔧 变更

- 团队资产工具统一 `team_` 前缀（skill / wiki / codegraph 六个），与 spcode 等插件的本机 codegraph 工具及 AstrBot 内置 SKILL.md 技能体系显式区分
- 注入块改 `<team_skills>`，并对服务端文案消毒（标题改名、悬空 `skill_manage` 引用移除、双重标签剥离）
- `knowledge_enabled=false` 时不注册知识工具（不再注入空壳提示工具）
- 面板身份自动对齐：local 模式采用面板的 team/agent/owner 三元组，数据与面板同桶可见

### 🐛 修复

- gateway 打包补 `metadata_config_params.json`、`data/`（BM25 语料），修 init-admin 500 与 stopwords 缺失
- knowledge 打包补 `TMC_CALLBACK_URL`，修索引完成后 meta 资产不登记（面板搜索报「知识库不存在」）
- skill 模块补 `skill: {enabled: true}` 配置段，修面板「Skill module not enabled」
- 面板身份对齐的 meta 调用补齐契约（`team/list` 需 body.user_id + `x-tdai-user-key` 头）
- 模块日志路由到插件专属 logger（WebUI 控制台可见）
- 插件页沙箱适配（bridge/localStorage 双通道取 key）

---

## 0.1.0 — 2026-10-01

- 首个版本：召回注入（四路并行 + TTL 缓存）、对话捕获、8 个 LLM 工具、`/memory` 指令组、local/server 双模式、健康探测降级与 401 退避
