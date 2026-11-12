# Banyan 设计文档：KB 优先的个人 AI 学习系统

> 状态：设计稿 v0.3（2026-11-12）。代码由 Claude Code 按 §12 的阶段实现。
>
> v0.2 变更（设计 review，对照 steward `sdk/python/openapi.json` 与 `docs/llm-steward-design.md`）：§4 messages 增加
> `steward_retrieval_ids`、拆分引用与提议的条目 id，jobs 增加租约字段，exports 增加状态；§6 重写步骤与路径表，补
> `allow_web=false`、部分降级、讲解模式；§7.3 增加出站防护；§8 按 steward 真实 schema 改写（entry 没有 metadata /
> valid_to，用 tags；反馈按 `retrieval_id`；deprecate 需要 review scope；记忆默认自动写入）；§9 主题创建改为同步，定义
> SSE 中断语义与分层 health；§10 明确渲染栈与确定性定义；§12 B0 增加 steward contract spike，B1 搭 pipeline 骨架；
> §13 更新。
>
> v0.2 增补（同日）：§5.1 改为对话式入口，大纲确认后选择讲解生成方式（预读下一节 / 全部生成 / 只要大纲），全部生成走
> `generation_runs` + `node_explain` 任务，先估算再确认、有整次预算；§5.3 classify 可建议新节点；§2 记录不用 Temporal、
> 任务运行时不单独开 repo 的理由；§6.0 规定步骤形状以保留迁移 Temporal 的退路；§12 B2 承接这些功能。
>
> v0.3（2026-11-12，对照 steward `52a8d07`、lite-rag `20981c3`、lite-mem `7491e36`）：steward 默认记忆引擎由 mem0 换成
> lite-mem（mem0 仍可选），Banyan 接口不变，各处改为"steward 配置的记忆引擎"，§13 第 3 条关闭；§6.2 增加 prompt 注入防护
> （照搬 lite-rag 的 nonce 标签）；§8.1 增加 namespace 配置文件 `deploy/steward/banyan.yaml` 与模型生成条目自动生效的
> 例外说明（owner 选定，作者类型提为 steward 变更需求）；§8.3 `HttpStewardClient` 基于 steward Python SDK，契约测试
> 可用 SDK 的嵌入模式；§8.6 steward MCP 已可用；§12 B0 / B5 验收相应调整。

## 1. 概述

用 Claude 或 GPT 学习时，通常先列大纲，再一次学一两点，中途不断追问，形成很多支线。线性对话的问题是：支线把主线冲乱，长对话后期上下文变差，学过的内容聊完就沉底，最后还要手动整理笔记。

**Banyan**（榕树：主干之外不断长出气根，气根落地又成为新的支撑）把学习组织成一棵持久的知识树：

- 输入主题 → 生成**可编辑的大纲**（左侧）
- 每个节点先有一份**主讲解**，节点下面可以开多条**支线**提问、追问
- 每个问题**先查知识库**，由便宜的模型判断够不够，不够才交给强模型，必要时联网搜索
- 新答案经 llm-steward 治理后进入知识库，以后跨主题复用
- 每个节点维护一份**节点笔记**，最后导出为一份**自包含的 HTML 学习成果**，结构与大纲一致

### 目标

- 支线不打乱主线；每次调用只带相关上下文
- KB 优先，降低 token 消耗，并能量化节省了多少
- 支持 Claude、GPT、Gemini、DeepSeek、Kimi，按 role 配置模型
- 联网搜索在所有模型上行为一致
- 项目记忆：学习者的水平、偏好、薄弱点，长期生效且可审阅、删除（v1 为自动生效，见 §8.5 与 §13 第 8 条）
- 手机、iPad、电脑都能用（响应式 PWA）
- 起步零成本：本机运行 + Tailscale，开发时可走订阅额度

### 非目标（v1 不做）

- 多用户、团队、计费
- 自己实现 RAG 或记忆引擎（由 llm-steward 及其引擎 lite-rag、lite-mem 负责）
- 离线使用
- 导入 Claude / ChatGPT 历史对话（v2）
- Banyan 自己的 MCP server（共享记忆依赖 steward 的 MCP，见 §8.6）
- Claude Agent SDK 模式（v2 可选，见 §13）

### 核心术语

| 术语 | 含义 |
|---|---|
| Topic（主题） | 一次学习项目，例如“Kubernetes 网络” |
| Node（节点） | 大纲中的一点，树形，最多 3 层 |
| Explanation（讲解） | 节点的主讲解，节点的第一条 assistant 消息，位于 `main` 分支 |
| Branch（支线） | 节点下的一条问答线程；`main` 分支承载讲解，其余为提问支线 |
| Path（路径） | 一次提问在 ask pipeline 里走过的路线：`kb` / `llm` / `web` / `mixed` |
| Node notes（节点笔记） | 节点的结构化笔记，HTML 导出的唯一来源 |
| Instructions（主题指令） | 用户手写的主题级设置：目标、水平、偏好（“用 Java 类比”） |
| Memory（记忆） | 关于学习者的事实，由 steward 的记忆引擎抽取（默认 lite-mem），v1 自动生效、可删除（§8.5） |
| KB entry（知识条目） | steward 治理下的一条知识，内容存储在 lite-rag |

## 2. 关键决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 知识与记忆 | 全部经 llm-steward | steward 已提供治理（候选 → 确认 → 生效、失效不删除）、联合检索、反馈、MCP；Banyan 不重复造 |
| KB 引擎 | lite-rag，作为 steward 的 `LiteRagProvider`，进程内模式 | 混合检索（BM25 + 向量 + RRF）、多 kb 联合检索；rerank 由 steward 自己做（§8.1）；单机少一个服务 |
| 记忆引擎 | steward 配置的记忆引擎，默认 lite-mem（2026-11-12 起；mem0 仍可选） | Banyan 不感知引擎：只经 steward 的 REST。lite-mem 自己抽取并消解冲突（新偏好取代旧偏好），向量用 bge-m3，与 lite-rag 一致 |
| 数据库 | PostgreSQL 18 + pgvector（与本机共享实例、CI 镜像一致），一个实例多个 schema | steward 及其引擎（lite-mem、lite-rag）共用；Banyan 用 `banyan` schema |
| 编排 | 确定性 pipeline + 单步 agent loop | 路由可预测、成本可控；只有作答一步需要工具循环 |
| 模型层 | Pydantic AI，包在 role 层后面 | 原生多 provider、结构化输出校验、工具调用；与 steward/lite-rag 的 role 约定一致 |
| 联网 | 自有 `web_search` / `web_fetch` 工具 | 各模型内置搜索不统一，DeepSeek 没有；自有工具保证一致 |
| 长流程 | 不用 Temporal | 每次请求秒级到分钟级；批量生成讲解（§5.1）是 N 个互相独立的短任务，不是长 agent：顺序由大纲决定，不等人，崩溃只损失一个节点。PG 任务表 + 租约 worker 覆盖进度、暂停、取消、重试、并发与整次 token 上限；整次上限在自有任务表里比在 Temporal 的逐 activity 重试里更容易卡住。引入条件见 §6.0 |
| 任务运行时 | 仓库内独立模块 `banyan/jobs/`，不单独开 repo | 通用部分很小（领取、租约、退避、死信），难点在业务；还没有第二个使用者，且其他项目技术栈不同（cairn 用 Temporal，CampaignOps 是 TypeScript）。import-linter 禁止它导入 Banyan 业务代码，将来第二个 Python 项目需要时可原样搬出 |
| 前端 | React + Vite PWA | 单用户 SPA，与 steward Admin 同栈；可安装到手机/iPad 主屏 |
| 部署 | 阶段一本机 + Tailscale；阶段二 Oracle Always Free | 零成本；本机可用订阅额度与 Ollama |

## 3. 总体架构

```mermaid
flowchart LR
  subgraph Clients
    P[手机 / iPad / 电脑 PWA]
  end
  subgraph Banyan
    API[FastAPI /v1 + SSE]
    SVC[Services]
    PIPE[Ask pipeline]
    LLM[LLM role layer<br/>Pydantic AI]
    TOOLS[Tools: kb_search / web_search / web_fetch]
    W[Worker: jobs]
    EXP[HTML export]
  end
  subgraph Steward[llm-steward]
    GOV[治理 + 检索 + MCP]
    LR[(lite-rag 进程内)]
    M0[(lite-mem 进程内<br/>默认记忆引擎)]
  end
  PG[(PostgreSQL + pgvector<br/>schemas: banyan / steward / lite_mem / lite_rag)]
  OL[Ollama 本机<br/>bge-m3 / qwen3]
  EXT[模型 API / 订阅 gateway]
  SR[搜索 API]

  P --> API --> SVC --> PIPE --> LLM --> EXT
  PIPE --> TOOLS --> SR
  TOOLS --> GOV
  PIPE --> GOV
  SVC --> W --> GOV
  SVC --> EXP
  GOV --> LR --> PG
  GOV --> M0 --> PG
  M0 --> OL
  SVC --> PG
  LR --> OL
  GOV --> OL
```

### 职责边界

| 组件 | 负责 | 不负责 |
|---|---|---|
| Banyan | 学习树、讲解、支线问答、ask 编排、模型路由、联网、节点笔记、HTML 导出、用量统计、UI | 存储与治理知识、向量检索、记忆抽取 |
| llm-steward | KB 条目治理、记忆治理、联合检索、反馈与命中统计、MCP | 聊天、agent、学习业务 |
| lite-rag | 分块、embedding、BM25 + 向量混合检索 | 被 Banyan 直接调用（禁止） |
| lite-mem | 记忆抽取、冲突消解、记忆检索 | 被 Banyan 直接调用（禁止） |

## 4. 数据模型（schema `banyan`）

所有表有 `id`（UUIDv7）、`created_at`、`updated_at`。

```
topics
  title, goal, level (beginner|intermediate|advanced), language (zh|en|mixed)
  instructions        text          -- 用户手写的主题指令
  steward_collection_id  text       -- 每个主题一个 steward collection
  status (active|archived)
  outline_status (draft|confirmed)  -- 用户确认大纲前不生成任何讲解（§5.1）
  explain_mode (all|prefetch|manual) -- 讲解生成方式（§5.1），可随时修改
  research_sources jsonb            -- 生成大纲时联网检索到的资料 [{url, title, fetched_at, summary}]，供所有节点讲解共享（B4）

nodes
  topic_id, parent_id (nullable), position (int), depth (0..2)
  title, summary
  status (not_started|learning|done)
  origin (outline|user|prerequisite|question)   -- 大纲生成 / 手动添加 / 插入的前置知识 / 由提问建议新增（§5.3）

branches
  node_id, kind (main|question), title
  closed_at, episode_job_id (nullable)
  -- 唯一约束：每个节点至多一个 kind=main 的分支

generation_runs                      -- 一次"全部生成"（§5.1）
  topic_id, status (running|paused|budget_exhausted|cancelled|done)
  node_ids uuid[]                    -- 本次要生成的节点，按大纲深度优先顺序
  token_budget int, tokens_used int  -- tokens_used 由 usage_log 按 run 汇总
  estimate jsonb                     -- 开始前给用户看的估算 {nodes, tokens_per_node, total, basis}

messages
  branch_id, role (user|assistant)
  status (streaming|done|aborted|failed)   -- user 消息恒为 done；见 §9 提交点与中断语义
  content_md
  source (kb|llm|web|mixed|null)       -- user 消息为 null
  path jsonb          -- 走过的 pipeline 步骤与判定结果（§6.4）
  provenance text     -- role:provider:model，多个用逗号分隔
  citations jsonb     -- [{kind: kb, entry_id, item_id, retrieval_id}, {kind: web, url, title, fetched_at}]
  steward_retrieval_ids text[]   -- 本次 ask 的每次 steward 检索返回的 retrieval_id（§6.2 按范围可能多次）；反馈必须带它
  cited_entry_ids text[]         -- 答案引用的 KB 条目（检索结果中 kind=entry 的 id；memory 不算引用）
  proposed_entry_ids text[]      -- 由本答案的 kb_propose 任务创建的条目 id，任务完成后由 worker 回写（写入前为空）
  feedback (none|good|wrong|incomplete), feedback_note
  superseded_by (nullable)   -- 反馈后重答，旧消息指向新消息

node_notes
  node_id (pk), content jsonb, version int
  -- content: {summary, key_points[], examples[], pitfalls[], branch_highlights[{branch_id, question, answer_md, message_id}]}

usage_log
  topic_id, message_id (nullable), generation_run_id (nullable), role, provider, model
  attempt int                 -- 同一 role 调用的第几次尝试（结构化输出校验失败重试 = 新的一行）
  input_tokens, output_tokens -- provider 未返回时为 null
  path_kind, ok bool, error_kind (nullable: timeout|provider|validation|budget), latency_ms

jobs
  kind (node_explain|collection_ensure|kb_propose|kb_feedback|kb_deprecate|notes_update|episode_submit|export_render)
  payload jsonb, idempotency_key (unique), status (pending|running|done|failed|dead)
  attempts, run_after, last_error
  locked_at, locked_by (nullable)   -- 租约：worker 领取时写入；超过 BANYAN_JOB_LEASE 未完成则视为可重领
  dedupe_key (nullable)             -- 同 key 的 pending 任务只保留一个（notes_update 按 node_id）

exports
  topic_id, notes_versions jsonb, status (pending|done|failed), error, html (text, nullable), created_at
```

一致性规则：

- 一条 assistant 消息、它的 `usage_log` 行和后续 `jobs` 在同一事务写入。消息在 LLM 调用**完成后**提交（§9 SSE 语义）。
- 每次 LLM provider 尝试一行 `usage_log`，包括失败和校验重试；`ok=false` 时 token 可为 null。
- 删除节点时级联删除其支线与消息。已提议到 steward 的条目**不自动失效**（§13 第 4 条，保守选项）：删除响应返回受影响的
  `proposed_entry_ids`，UI 提示用户可选择"同时失效这些知识"，用户确认后入队 `kb_deprecate`。
- `node_notes.version` 每次更新 +1，更新用乐观锁（`WHERE version = :expected`）；同一节点的 `notes_update` 任务按
  `dedupe_key` 合并，worker 一次只处理一个节点的笔记。导出记录所用版本，便于判断是否需要重新导出。
- worker 领取任务用 `SELECT … FOR UPDATE SKIP LOCKED`，并写 `locked_at/locked_by`；进程崩溃后租约过期的 `running`
  任务由其他 worker 重领。重试指数退避，`attempts ≥ BANYAN_JOB_MAX_ATTEMPTS` 进 `dead`。**所有重试必须在首次尝试后
  24 小时内结束**（steward 的 `Idempotency-Key` 只保留 24 小时，§8.4），所以退避上限与最大次数的乘积受此约束。
- `idempotency_key` 由业务来源派生（如 `kb_propose:<message_id>`），不是随机值，重试复用同一个。
- **steward 的 4xx 是终态**：steward 把 4xx 答复存在该幂等键下并在 24 小时内原样重放（只有 5xx 释放键，B0 spike 实测）。所以
  `StewardRejected`（4xx）让任务直接进 `dead`，不重试；修好原因（如 key 缺 scope）后由人重新入队，新任务带新的键
  （原键加 `:<n>` 后缀）。`StewardUnavailable`（网络、超时、5xx）照常退避重试。

## 5. 学习流程

### 5.1 创建主题与大纲（对话式入口）

入口是一个对话框，但流程由代码驱动，不是自由对话的 agent（规则 3）：

1. **输入课题**：用户在对话框里输入要学什么，例如“我想学 Java”。
2. **固定的几个澄清问题**：目标、当前水平、语言。问题由前端按固定顺序以对话气泡呈现，有默认值、可跳过，不调用 LLM；答案写入 `topics.goal / level / language`。
3. **生成大纲**：`outline.generate`（强模型）返回结构化大纲：2–3 层、建议 6–15 个顶层节点，每个节点带一句 summary。`allow_web=true` 时（B4 起）先联网**一次**检索课程大纲与权威资料，结果存入 `topics.research_sources`，后面所有节点的讲解共享这份资料，不再逐节点重复检索同类内容。
4. **编辑并确认大纲**：增删、重命名、拖动排序、插入前置节点（`origin=prerequisite`）。用户点“确认大纲”后 `outline_status=confirmed`。**确认之前不生成任何讲解**，避免为将被删改的节点花钱。
5. **选择讲解生成方式**（`topics.explain_mode`，之后可随时修改）：

| 方式 | 行为 | 适合 |
|---|---|---|
| `prefetch` 预读下一节（**默认**） | 用户打开第 N 个节点的讲解时，后台生成大纲顺序中的下一个尚无讲解的节点 | 大多数情况：体验接近全部生成，成本跟着学习进度走 |
| `all` 全部生成 | 创建一个 `generation_run`，后台为全部节点生成讲解（§5.1.1） | 想先拿到一份完整资料 |
| `manual` 只要大纲 | 只在用户打开节点时生成讲解（§5.2） | 只想按需学习、最省 |

6. Banyan 入队 `collection_ensure`，由 worker 在 steward 创建该主题的 collection 并回写 `steward_collection_id`（失败重试，不阻塞学习；collection 就绪前 retrieve 视为 §6.4 第 8 行）。

三种方式产生的数据完全相同：讲解都是节点 `main` 分支上的第一条 assistant 消息，都走 ask pipeline（§6，`mode=explain`），都有 provenance、KB 回写与 `notes_update`。之后的追问、笔记、导出没有区别。每个节点是否联网仍由 `judge.freshness` 决定，不因“全部生成”而逐节点强制联网；KB 中已有的内容照常命中路径 1。

#### 5.1.1 全部生成

- **先估算再确认**：`estimate = 节点数 × 每节点 token`。每节点 token 取本机 `usage_log` 中 `node.explain` 最近 50 次的中位数；没有历史时用 `BANYAN_EXPLAIN_TOKENS_ESTIMATE`（默认 20,000）。前端展示节点数、估算总 token 与依据，用户确认后才开始，可调整 `token_budget`（默认 `BANYAN_BULK_MAX_TOKENS`）。
- **执行**：为每个节点入队一个 `node_explain` 任务（`idempotency_key=explain:<node_id>`，`dedupe_key` 相同），由 worker 执行与交互式讲解相同的 pipeline。同时运行的 `node_explain` 不超过 `BANYAN_BULK_CONCURRENCY`（默认 2），避免撞到 provider 限流或订阅额度。
- **预算**：worker 领取下一个节点前汇总 `usage_log` 中该 run 的 token；若 `tokens_used + 每节点估算 > token_budget`，run 转为 `budget_exhausted` 并停止领取，前端提示“已用 X，继续需要追加预算”。已在运行的节点跑完不中断。
- **暂停 / 取消**：`paused` 时 worker 不再领取该 run 的任务，`running` 恢复；`cancelled` 把未领取的任务标为 `dead`（`last_error=cancelled`）。
- **失败**：单个节点按任务重试规则重试，进 `dead` 后在进度里标红，可单独重试；其余节点不受影响。全部节点 `done` 或 `dead` 时 run 为 `done`。
- **与交互式讲解的互斥**：同一节点的讲解只能有一个 `streaming` 消息（§9）。用户打开一个正在后台生成的节点时，看到“生成中”并轮询；节点已有 `done` 的讲解时，`node_explain` 任务直接成功返回（no-op）。
- **预读**（`prefetch`）复用同一个 `node_explain` 任务，不建 run，不设整体预算，每次只预读一个节点。

### 5.2 节点讲解

打开节点时若 `main` 分支为空，触发讲解：同样走 ask pipeline（§6），`mode=explain`，问题为固定模板“讲解节点 X（在主题 Y 的大纲上下文中）”。`mode=explain` 对 pipeline 的影响只有两点：(1) 作答一步的 role 由 `answer.compose` 换成 `node.explain`（同一个 agent loop、同一组工具，不同 prompt 模板与模型配置）；(2) judge 用 `BANYAN_JUDGE_THRESHOLD_EXPLAIN`（默认 0.85，高于提问的 0.7），且 `judge.sufficiency` 的 prompt 要求检索内容覆盖该节点 summary 列出的要点。命中时仍由 `answer.from_kb` 组织讲解。

### 5.3 支线提问

- 节点下“新建支线”或直接在讲解下提问（自动建支线）。
- **快速提问**：不选节点直接问，`node.classify`（便宜模型）把问题归到最相关的节点，用户可事后移动支线。
- **大纲随提问生长**：`node.classify` 的结构化输出为 `{node_id | null, confidence, suggested_node?: {title, summary, parent_id, position}}`。`node_id=null`（大纲中没有合适节点）或 `confidence < BANYAN_CLASSIFY_THRESHOLD` 时，前端提示“大纲里没有这部分，是否新增节点「X」（挂在「Y」下）？”。用户确认 → 插入 `origin=question` 的节点，问题成为该节点下的第一条支线；用户拒绝 → 归到用户选的节点。是否新增节点由用户决定，模型只给建议。
- 支线内可连续追问，上下文为：主题指令 + 节点 summary + 节点讲解摘要 + 本支线历史（超预算时由 `branch.compress` 压缩早期轮次）。

### 5.4 节点笔记

每次讲解完成、或支线中一条答案被用户标 good / 关闭支线时，入队 `notes_update`。`notes.update` 角色读取当前笔记与新内容，输出新的结构化笔记（JSON，经 Pydantic 校验）。笔记是 HTML 导出的唯一来源。

### 5.5 学习状态

完全由数据计算，不调用 LLM：节点状态（打开过讲解 → learning，用户标记完成 → done）、各节点提问次数、被标 wrong/incomplete 的次数（薄弱点信号）。这些信号写入记忆 episode 的上下文，供 steward 的记忆引擎抽取。

## 6. Ask pipeline

所有“需要一个答案”的场景（讲解、批量或预读讲解、提问、快速提问）都走同一个 pipeline。外层是确定性代码，只有作答一步是 agent loop。请求内（SSE）与 worker 内（`node_explain`）调用的是同一个 pipeline 函数，区别只在输出：SSE 把事件推给客户端，worker 丢弃 token 事件、只保留结果。

### 6.0 步骤的形状（为将来迁移 Temporal 保留退路）

- 每一步是 `async def step(inp: StepInput, deps: Deps) -> StepOutput`：输入输出都是可序列化的 Pydantic 模型，不读写共享可变状态，所有 I/O（LLM、steward、搜索、DB）只经 `deps` 注入的接口。
- 编排函数只串联步骤并根据输出分支，自身不做 I/O。
- 这正是 Temporal activity / workflow 的形状，而 Pydantic AI 原生支持 Temporal 持久化执行。将来若需要，可以把步骤包成 activity、编排函数包成 workflow，不必重写。
- **引入 Temporal 的条件**（满足其一再考虑）：单个节点的生成变成多步链条（调研 → 起草 → 审校 → 修订），每步都贵，崩溃后从头重跑代价不可接受；或者出现跨天等待人或定时器的流程。届时整次运行的 token 上限仍在自己的 activity 里计数（KB `global/llm-app-architecture.md` §7.2）。

### 6.1 输入

```
AskRequest:
  topic_id, node_id, branch_id
  question
  mode: explain | ask
  force_llm: bool = false      -- 反馈“不对/不完整”后重答时为 true
  allow_web: bool = true       -- false 时 compose 不挂 web_search / web_fetch 工具
```

### 6.2 步骤

每一步在 `path` 中记录 `{step, status, detail}`；下面写明每种情况是否执行该步。

1. **scope**：组装检索范围与上下文骨架。范围依次为：当前节点（`collections=[本主题]`，`filters.tags=["node:<node_id>"]`）→ 本主题（`collections=[本主题]`，无 tag 过滤）→ 全部主题（`collections=[全部活跃主题]`）。每个范围是一次独立的 steward `retrieve` 调用（steward 不支持一次请求内的分层范围）。
2. **freshness**（`judge.freshness`，便宜模型）：问题是否依赖时效信息（版本、价格、近期动态、“最新”）？是则标记 `needs_web`。`force_llm=true` 时跳过（重答不改变问题的时效性，沿用原消息 `path` 中的判定）。
3. **retrieve**：**所有情况都执行**（包括 `needs_web` 与 `force_llm`，检索结果仍作为 compose 的初始上下文）。按范围由近到远依次调用 steward，`include_memory=true`，`format=context`，直到累计 `top_k` 条或达到 `BANYAN_CONTEXT_BUDGET`；记录每次的 `retrieval_id`。结果中 `kind=memory` 的条目只进上下文，不计入"检索到的知识"，也不作为引用。
   降级规则：steward 连接失败、超时、非 2xx，或响应 `degraded` 包含 `"kb"` → 本次 ask 视为 `retrieve: unavailable`，跳过 judge，直接进入 compose，上下文中只保留已拿到的 memory（若有）。`degraded` 只含 `"memory"` 不算降级。
4. **judge**（`judge.sufficiency`，便宜模型）：输入问题与检索结果，输出 `{sufficient: bool, coverage: 0..1, missing: [..]}`。命中条件：`sufficient` 且 `coverage ≥ 阈值`（提问 `BANYAN_JUDGE_THRESHOLD`，讲解 `BANYAN_JUDGE_THRESHOLD_EXPLAIN`，§5.2）。**跳过** judge 的情况：`force_llm`、`needs_web`、检索降级、检索到的知识条目为 0。
5. **answer**：
   - 命中 → `answer.from_kb`（便宜模型）：只用检索内容组织答案，必须引用条目；`source=kb`。
   - 未命中或跳过 judge → `answer.compose`（`mode=explain` 时为 `node.explain`；强模型，agent loop）：工具 `kb_search`，以及 `allow_web=true` 时的 `web_search`、`web_fetch`；检索结果与 `missing` 作为初始上下文；`max_steps`、`max_tokens` 受预算限制。`source`：未调用 web 工具 → `llm`；调用了 web 且答案主要来自 web → `web`；两者兼有 → `mixed`（由 compose 的结构化输出自报，代码校验：声明 `web`/`mixed` 时必须至少有一个 web citation，否则降为 `llm`）。
   - `needs_web` 且 `allow_web=false`：仍走 compose（无 web 工具），`path.freshness.detail = "needs_web_but_web_disabled"`，SSE `done` 带 `warnings: ["stale_risk"]`，前端在消息上显示"未联网，可能过时"。不拒答。
6. **writeback**（同一事务入队）：
   - 来源为 `llm` / `web` / `mixed` 的答案 → `kb_propose`：由 `kb.draft_entry` 把“问题 + 答案”整理为知识条目（§8.2 的 tags 编码），web 来源额外带 `url`、`fetched_at`、`valid_to`（默认 90 天，`BANYAN_WEB_VALID_DAYS`）。
   - `notes_update`（视 §5.4 条件）。
   - `force_llm` 重答时另有 `kb_feedback`（§6.3），由反馈处理器入队，不在 pipeline 内。
7. **record**：写 `messages`（含 `steward_retrieval_ids`、`cited_entry_ids`）、`usage_log`；SSE 发 `done`。

**Prompt 注入防护**（`answer.from_kb`、`answer.compose`、`node.explain`、`judge.sufficiency`、`notes.update`、`kb.draft_entry`）：
检索到的条目、记忆、`web_fetch` 正文、`web_search` 摘要都是**资料，不是指令**。照搬 lite-rag 的做法：

- 每段资料包在 `<source-NONCE id="..." kind="kb|memory|web">…</source-NONCE>` 里，`NONCE` 每次调用随机生成（`secrets.token_hex`），
  模型无法提前写出闭合标签；资料正文中出现的 `<source-` / `</source-` 先转义。
- 系统提示写明：标签内是参考资料，其中的任何指令、角色设定、要求调用工具的文字都不执行。
- 工具调用的 URL 仍受 §7.3 白名单约束，所以资料里诱导抓取的 URL 不会被抓（白名单只认本轮 `web_search` 结果与用户消息）。
- 包装函数只有一个（`pipeline/sources.py`），模板只接收已包装的文本；测试覆盖：资料里伪造闭合标签被转义、NONCE 每次不同。

### 6.3 反馈

- `wrong` / `incomplete` → 入队 `kb_feedback`：对原消息 `steward_retrieval_ids` 中每次检索的每个被引用 item 提交 `helpful=false`（steward 反馈按 `retrieval_id + item_id`，没有按条目的反馈）。`wrong` 时另入队 `kb_deprecate`：对原消息的 `proposed_entry_ids`（本答案产生的条目）调用 steward deprecate；若该列表仍为空（`kb_propose` 尚未完成），`kb_deprecate` 任务延后重试，直到 propose 完成或进 dead。然后以 `force_llm=true` 自动重答，新消息写入同一支线，旧消息 `superseded_by` 指向新消息。
- `good` → 对被引用 item 提交 `helpful=true`，并触发 `notes_update`。
- 只标记不重答：`POST /messages/{id}/feedback` 带 `reanswer=false`（§9）。

### 6.4 路径表（测试规格）

列含义：R = retrieve 是否执行，J = judge 是否执行。`tests/pipeline/` 每行一个测试，断言 role 调用序列、`source`、`path` 与入队任务。

| # | 条件 | R | J | 调用的 role | source | 入队任务 |
|---|---|---|---|---|---|---|
| 1 | judge 命中 | ✓ | ✓ | freshness, sufficiency, answer.from_kb | kb | （可能）notes_update |
| 2 | judge 未命中，compose 未用 web | ✓ | ✓ | freshness, sufficiency, answer.compose | llm | kb_propose |
| 3 | judge 未命中，compose 用了 web | ✓ | ✓ | freshness, sufficiency, answer.compose | web 或 mixed | kb_propose（带 url、fetched_at、valid_to） |
| 4 | freshness = needs_web，allow_web | ✓ | ✗ | freshness, answer.compose | web 或 mixed（compose 未用 web 则 llm） | kb_propose |
| 4b | needs_web，allow_web=false | ✓ | ✗ | freshness, answer.compose（无 web 工具） | llm，`warnings=[stale_risk]` | kb_propose |
| 5 | force_llm（重答） | ✓ | ✗ | answer.compose | llm / web / mixed | kb_propose（kb_feedback、kb_deprecate 由反馈处理器入队） |
| 6 | 检索成功但知识条目为 0 | ✓ | ✗ | freshness, answer.compose | llm / web / mixed | kb_propose |
| 7 | 任一 role 失败（含校验重试后仍失败） | 到失败为止 | | 到失败为止 | — | 无；SSE `error`，usage 记录 ok=false，不写 assistant 消息 |
| 8 | steward 不可用或 `degraded` 含 kb | 尝试 | ✗ | freshness, answer.compose | llm / web / mixed | kb_propose（worker 重试） |
| 9 | mode=explain，judge 命中（阈值 0.85） | ✓ | ✓ | freshness, sufficiency, answer.from_kb | kb | notes_update |
| 10 | mode=explain，未命中 | ✓ | ✓ | freshness, sufficiency, node.explain | llm / web / mixed | kb_propose, notes_update |

第 8 行是唯一的降级：检索失败时不阻塞学习，直接作答并在 `path` 中记录 `retrieve: unavailable`。这不是模型 fallback，规则 4 不受影响。第 7 行中 `judge.*` 的失败同样是失败（不"当作未命中"继续），因为那等于静默换路径。

### 6.5 Token 预算

| 项 | 默认 | 配置 |
|---|---|---|
| 检索上下文 | 3,000 tokens | `BANYAN_CONTEXT_BUDGET` |
| 支线历史 | 2,000 tokens，超出压缩 | `BANYAN_HISTORY_BUDGET` |
| compose 最大步数 | 6 | `BANYAN_ANSWER_MAX_STEPS` |
| compose 最大输出 | 2,000 tokens | `BANYAN_ANSWER_MAX_TOKENS` |
| web_fetch 单页截断 | 4,000 tokens | `BANYAN_FETCH_MAX_TOKENS` |
| judge 命中阈值（提问 / 讲解） | 0.7 / 0.85 | `BANYAN_JUDGE_THRESHOLD` / `BANYAN_JUDGE_THRESHOLD_EXPLAIN` |
| role 调用超时 | 60 s | `BANYAN_ROLE_TIMEOUT_S`（可按 role 覆盖） |
| web 来源条目有效期 | 90 天 | `BANYAN_WEB_VALID_DAYS` |
| 任务租约 / 最大尝试 | 5 min / 6 | `BANYAN_JOB_LEASE` / `BANYAN_JOB_MAX_ATTEMPTS`（退避总和须 < 24 h，§4） |
| 全部生成：整次预算 / 并发 | 500,000 tokens / 2 | `BANYAN_BULK_MAX_TOKENS` / `BANYAN_BULK_CONCURRENCY` |
| 无历史时每节点讲解估算 | 20,000 tokens | `BANYAN_EXPLAIN_TOKENS_ESTIMATE` |
| classify 建议新节点阈值 | 0.6 | `BANYAN_CLASSIFY_THRESHOLD` |

## 7. LLM role 层与模型

### 7.1 Roles

| Role | 用途 | 建议档位 |
|---|---|---|
| `outline.generate` | 生成大纲（结构化） | 强 |
| `node.explain` | 节点讲解（compose 的讲解模式） | 强 |
| `answer.compose` | 未命中时作答，agent loop | 强 |
| `answer.from_kb` | 仅用检索内容作答 | 便宜 |
| `judge.freshness` | 是否需要时效信息 | 便宜 |
| `judge.sufficiency` | 检索结果是否足够 | 便宜 |
| `node.classify` | 快速提问归类；无合适节点时建议新节点（§5.3） | 便宜 |
| `branch.compress` | 压缩支线早期历史 | 便宜 |
| `notes.update` | 更新节点笔记（结构化） | 中 |
| `kb.draft_entry` | 把问答整理为知识条目 | 便宜 |

配置示例（`BANYAN_ROLES` 或 `roles.yaml`）：

```yaml
providers:
  anthropic: {kind: anthropic, api_key_env: ANTHROPIC_API_KEY}
  openai:    {kind: openai, api_key_env: OPENAI_API_KEY}
  gemini:    {kind: google, api_key_env: GEMINI_API_KEY}
  deepseek:  {kind: deepseek, api_key_env: DEEPSEEK_API_KEY}
  kimi:      {kind: openai_compatible, base_url: https://api.moonshot.ai/v1, api_key_env: MOONSHOT_API_KEY}
  ollama:    {kind: openai_compatible, base_url: http://127.0.0.1:11434/v1}
  gateway:   {kind: openai_compatible, base_url: http://127.0.0.1:<port>/v1}   # llm-gateway，仅本机开发
roles:
  outline.generate:   anthropic/<model>
  answer.compose:     anthropic/<model>
  answer.from_kb:     deepseek/<model>
  judge.sufficiency:  deepseek/<model>
  judge.freshness:    ollama/qwen3:8b
  notes.update:       openai/<model>
```

模型名一律来自配置，代码中不出现。provider `kind` 与 Pydantic AI 的对应关系以锁定版本的文档为准（context7）。

### 7.2 规则

- 没有 fallback 链；失败即抛错（规则 4）。
- 结构化输出（大纲、judge、笔记、条目）用 Pydantic 模型校验；校验失败重试一次（同一模型），再失败抛错。
- 每次 provider 尝试记录一行 usage（含失败、超时、校验重试；§4）。
- 能力表：每个 provider/model 登记是否支持工具调用、结构化输出、上下文长度。`answer.compose` 与 `node.explain` 只能配置为支持工具调用的模型，启动时校验。
- 启动校验只检查**配置**（每个启用功能所需的 role 都映射到了已登记的模型），不探测模型是否在线；模型在线状态由 `/health` 的依赖项报告（§9）。所需 role 按功能阶段确定：B1 只要求 `outline.generate`、`answer.compose`、`node.explain`；其余 role 在对应阶段启用后成为必需。
- 每个 role 有超时（`BANYAN_ROLE_TIMEOUT_S`，可按 role 覆盖）；超时是失败（§6.4 第 7 行），不重试到别的模型。只有结构化输出校验失败才重试一次（同一模型）。

### 7.3 搜索工具

```
SearchProvider (Protocol)
  search(query, *, max_results, recency_days | None) -> list[SearchResult{url, title, snippet, published_at?}]
Fetcher
  fetch(url) -> FetchedPage{url, title, text_md, fetched_at}   -- 正文抽取后转 Markdown，截断到预算
```

默认 provider 由配置决定（Tavily / Exa / Brave 之一，见 §13）。所有模型通过同一组工具联网，结果格式统一，KB 入库格式统一。

`web_fetch` 的两层限制：

1. **白名单**：只允许抓取本轮 `web_search` 返回的 URL 或用户消息中出现的 URL，防止模型构造任意请求。
2. **出站防护**（用户消息里的 URL 不可信，可能指向 `127.0.0.1:8600` 的 steward admin、Ollama 或云元数据地址）：只允许 `https`；解析主机名后所有地址必须是公网地址（拒绝私网、回环、链路本地、CGNAT、组播、保留地址，以及写成 IPv6 的 IPv4）；连接发往校验过的 IP，主机名只用于 SNI 与 `Host`（防 DNS rebinding）；重定向最多 3 跳且每跳重新校验；响应体有大小上限。实现照搬 steward 的 `outbound.py`（steward 设计 §11 P5c）。`BANYAN_ALLOW_PRIVATE_URLS=true` 仅供本地测试。

## 8. llm-steward 集成

> 所有字段名、端点、请求体以 steward 的 OpenAPI 与 `docs/llm-steward-design.md` §5–§7 为准，本节只定义 Banyan 侧的语义与映射。

### 8.1 Steward 侧配置

- Namespace：`banyan`。
- KB provider：`literag`，`STEWARD_LITERAG_MODE=inprocess`。
- Memory provider：steward 默认的 `litemem`（`STEWARD_MEMORY_PROVIDER`，进程内，表在同一 PG 的 `lite_mem` schema）。Banyan 不依赖引擎：
  换成 mem0 只改 steward 配置并 `steward resync`，Banyan 的契约测试不能依赖某个引擎特有的行为（例如 mem0 只会新增、lite-mem 会取代）。
- 模型：lite-mem 的向量用宿主机 Ollama 的 `bge-m3:567m`；抽取与冲突消解用 `STEWARD_LLM_*`，不设时用宿主机 Ollama 的 `qwen3:8b`。
  steward 记录（2026-11-12）：本地 `qwen3:8b` 下每次抽取都超过 worker 的 30 秒、靠重试完成，且有一次把"搬到 Auckland"抽成"Moved last week"。
  记忆是异步的，延迟可以接受；抽取质量不够时给 `STEWARD_LLM_*` 配更强的模型（llm-gateway 只监听宿主机回环，容器内用不了）。
  steward 自己的其他 roles（`kb.risk`、`kb.worthiness`、`kb.draft`）同样指向本机 Ollama，质量不够时改为 DeepSeek。
- **Namespace 配置即代码**：`deploy/steward/banyan.yaml`，`steward ns apply -f` 应用（整份替换）。起始值：
  ```yaml
  slug: banyan
  name: Banyan
  retrieval:
    top_k: 8
    token_budget: 3000        # 与 BANYAN_CONTEXT_BUDGET 一致
    rerank: true              # steward 自己的 cross-encoder（不是 lite-rag 的 rerank；steward 调 lite-rag 时固定 hybrid、rerank 关）
    min_relevance: null       # B3 用真实问答校准后再开；只在 rerank 跑过时生效，全部低于下限 = 缺口
    context_language: auto
  kb:
    high_risk_keywords: []    # B0 核实：是否与内置规则叠加，以及学习内容里"价格""安全"一类词是否会把条目送进审核
  ```
  开了 `min_relevance` 后，"检索到 0 条知识"直接走路径 6，省一次 `judge.sufficiency`；`score` 是否就是 0–1 的 rerank 分在 B0 spike 里确认。
- **模型生成的条目自动生效（owner 决定，2026-11-12）**：steward 的原则是"模型起草的文本先经人看"，而 Banyan 提议的条目全是模型答案，
  `review_policy` 又让 low / medium 自动生效。单用户系统里用户本人就是审核者，事后纠错靠来源徽章、`wrong` 反馈、`kb_deprecate`
  与 steward 的命中 / 无用率扫描，所以接受这个例外。另一个问题：`EntryCreate` 没有作者类型，API 写入默认记为 `human`，作者记录不真实。
  v1 用 tag `author:ai` 标明，并向 steward 提 `EntryCreate.author_type` 的变更需求（§13）。
- KB 审核策略：steward 的 `POST /v1/kb/collections` 接受 `review_policy`（`{"low": "auto"|"user"|"review", "medium": …}`，high 永远进审核）。Banyan 建 collection 时传 `{"low": "auto", "medium": "auto"}`，提议条目时**不传 `user_id`**（steward 对 medium 的规则是"问 user_id，没有则直接生效"）。steward 仍会用规则 + 自己的 LLM 重新定级并取较高者，所以被判 high 的内容（价格、法务、医疗、安全类关键词或 PII）会进审核队列、暂不可检索。这是预期行为（§13 第 2 条已由此解决）。
- **两把 key**（B0 spike 修正 v0.3 的单 key 方案）：`BANYAN_STEWARD_API_KEY`（`read`、`write`）用于检索、提议条目、反馈、episode；
  `BANYAN_STEWARD_REVIEW_KEY`（`review`）只用于建 collection 与 deprecate（这两个端点要求 review scope）。原因：steward 把带 review
  scope 的 key 写入的条目直接记为 `verified`（"reviewer's writes go live verified"），单 key 会让 Banyan 的模型答案全部冒充审核过的知识，
  检索时还按信任度加权。
- steward 用 `Idempotency-Key` 请求头做写入幂等，保留 24 小时；`/v1/retrieve` 忽略它。

### 8.2 映射

> steward 的 `EntryCreate` 只有 `collection_id, title, content, risk_level, tags, user_id`，**没有 metadata、valid_to、url、source_refs**（已核对 `sdk/python/openapi.json`，2026-11-11）。检索过滤 `filters.metadata` 对 entry 无效，只有 `filters.tags`（AND）。因此 Banyan 的所有结构化来源信息都编码在 tags 里，格式 `<key>:<value>`，解析时以第一个冒号分隔。

| Banyan | Steward |
|---|---|
| Topic | collection（每个主题一个，`review_policy` 见 §8.1） |
| 节点问答、讲解 | KB entry。tags：`app:banyan`、`topic:<topic_id>`、`node:<node_id>`、`source:<kb\|llm\|web\|mixed>`、`author:ai`、`origin_message:<message_id>`；web 来源另加 `url:<url>`（每个 URL 一个 tag）、`fetched_at:<ISO date>`、`valid_to:<ISO date>`。`content` 正文末尾附一行 Markdown 来源列表，供人阅读与 MCP 消费者（§8.6） |
| web 来源的有效期 | **v1 由 Banyan 自己过滤**：检索结果中 `valid_to:` tag 已过期的条目不作为知识计入 judge，并入队 `kb_deprecate`。真正的有效期窗口需要 steward 在 `EntryCreate` 增加 `valid_to`（steward 变更需求，§13） |
| 支线对话 | episode（`POST /v1/episodes`，`user_id=BANYAN_USER_ID`、`agent_id="banyan"`、`session_id=<branch_id>`）；学习状态信号（§5.5）作为最后一条 `role=system` 消息附在 `messages` 里，因为 `EpisodeCreate` 没有 context 字段 |
| 学习者记忆 | memory（steward 的记忆引擎抽取，**默认直接生效**，见 §8.5） |
| 反馈 | `POST /v1/retrieve/{retrieval_id}/feedback {item_id, helpful, note}`；条目失效用 `POST /v1/kb/entries/{id}/deprecate` |
| 用户 | 常量 `BANYAN_USER_ID` |

### 8.3 StewardClient（Banyan 侧接口）

```
class StewardClient(Protocol):
    async def ensure_collection(name, *, review_policy, idempotency_key) -> str
                                                                    # GET 按 name 查，没有则 POST（review key；键由调用方派生，如 collection_ensure:<topic_id>）
    async def retrieve(query, *, user_id, collections, tags, include_memory,
                       top_k, token_budget, as_context) -> RetrievalResult
                                                                    # POST /v1/retrieve；collections 是 collection **名字**；
                                                                    # include_memory → sources=[memory, kb] 或 [kb]
    async def create_entry(collection_id, *, title, content, tags, risk_level,
                           idempotency_key) -> ProposedEntry        # POST /v1/kb/entries → entry_id（生效）或 candidate_id（待审）
    async def feedback(retrieval_id, item_id, *, helpful, note, idempotency_key) -> None
    async def deprecate_entry(entry_id, *, idempotency_key) -> None # review key；steward 不收理由，理由记在 Banyan 的任务里
    async def submit_episode(user_id, agent_id, session_id, messages,
                             *, idempotency_key) -> str            # POST /v1/episodes mode=async → episode_id
    async def list_memories(user_id) -> list[Memory]               # GET /v1/memories?user_id=
    async def delete_memory(memory_id) -> None
    async def list_candidates(user_id) -> list[Candidate]          # GET /v1/candidates?stage=awaiting_user
    async def decide_candidate(candidate_id, accept: bool, note) -> None
    async def health() -> Health
```

`RetrievalResult.items` 保留 steward 的 `id`（即反馈用的 `item_id`；条目的 item id 就是 entry id）、`kind`（memory | entry | document）、`status`、`score`、
`title`、`content`。**检索结果不带 tags**（`RetrievedItemOut` 没有这个字段），所以 §8.2 里按 `valid_to:` tag 过滤过期条目要另想办法：
B3 决定是把有效期写进 `content` 末尾的来源行由 Banyan 解析，还是按 entry id 回查（多一次请求）。B0 前三个方法以外的接口（episode、记忆、候选）
在 B3 / B5 加入。实现：

- `HttpStewardClient` 用 httpx 直接调 steward 的 REST，响应用 SDK 生成的模型 `llm_steward_client.models` 解析（steward 改了契约这里就会报错）。
  **不用 SDK 的客户端发请求**：B0 核实 SDK 每次写入自己生成随机 UUID 作为 `Idempotency-Key`，调用方没法传入（v0.3 写"可由调用方传入"是错的）；
  Banyan 需要按来源派生的键，worker 几小时后的重试才能重放第一次的结果。已向 steward 提变更需求（§13）。SDK 不在 PyPI，用 git 依赖锁定到一个
  commit（`backend/pyproject.toml`），升级时重跑契约测试。业务代码只见 `StewardClient` Protocol，import-linter 禁止其他包导入 SDK。
- **没有客户端重试**：请求内的检索失败就降级（§6.2），worker 任务有自己的重试与退避（§4），只有这一层。错误分两类：`StewardUnavailable`
  （网络、超时、5xx）与 `StewardRejected`（4xx，§4 规定为终态）。`/health` 探测 steward 的 `GET /health`（SDK 没有这个调用），上限 3 秒。
- `FakeSteward`：同样实现 tags AND 过滤、`review_policy`、24 小时幂等。日常测试只用它。
- 契约测试两个目标：(1) `-m steward`：SDK 的嵌入模式 `await AsyncSteward.embedded("sqlite+aiosqlite:///...", app_overrides={"embedder": …})`
  （`uv sync --group contract`），测试进程里跑真 steward（native 引擎、假 embedder、不下载模型），CI 的 `contract` job 每次都跑；
  嵌入模式只有一个调用方身份，且忽略 `Authorization`，所以 review key 的路由由单元测试覆盖。(2) `-m steward_live`：本机运行中的 steward
  （lite-rag + lite-mem，应用 `deploy/steward/banyan.yaml`），验证同步延迟、rerank 分数、定级等引擎相关行为；缺环境变量时失败而不是跳过。

### 8.4 写入时序

steward 的规则是“请求处理中不直接写 KB provider，由 outbox 异步同步”。因此 Banyan 提议的条目从 `create_entry` 返回到可被检索之间有秒级延迟，这是预期行为。同一支线内紧接着的追问依靠支线历史，而不是 KB。`kb_propose` 完成后 worker 把返回的 entry id 回写到 `messages.proposed_entry_ids`（§4）。

### 8.5 记忆

- 支线关闭或每 `BANYAN_EPISODE_EVERY` 轮，提交 episode：支线消息 + 学习状态信号（§5.5）。
- **steward 现状**：记忆引擎抽取的记忆直接生效（steward 设计 §5 "Memory 默认自动写入"）；`/v1/candidates` 里 `awaiting_user` 的候选是 steward 认为值得存入 KB 的建议，不是记忆确认。因此 §1 目标中“经确认后长期生效”在 v1 退为：**记忆自动生效，Banyan 的记忆页列出全部记忆（`list_memories`）并可删除**；KB 候选卡片照常展示并 accept / reject。让记忆先确认再生效需要 steward 增加 namespace 级策略，作为变更需求提出（§13）。
- 检索时 `include_memory=true`，关于学习者的记忆与主题指令一起进入上下文。
- 主题指令（用户手写）存在 Banyan，不经过 steward。
- 默认引擎 lite-mem 会消解冲突：用户改了偏好（"别再用 Java 类比了"），旧记忆被取代（`superseded`），检索只返回新的；steward 合并结果时
  丢掉已失效的记忆，即使引擎还没同步完。记忆页只列生效的记忆。lite-mem 默认丢弃看起来像密码、密钥、证件号的内容。

### 8.6 与 Claude / ChatGPT 共享

steward 的 MCP 已可用（工具 `remember`、`recall`、`forget`、`suggest_kb`、`search_kb`、`get_profile`）：在 Claude 或 ChatGPT 中添加 steward 的 MCP connector 后，可检索 Banyan 积累的知识；外部的写入只能作为候选，需在 Banyan / steward 中确认。Banyan v1 不做额外工作，只需保证条目 metadata 足够自描述（主题、节点标题、来源）。

## 9. API 与 SSE

前缀 `/v1`，鉴权：`Authorization: Bearer <BANYAN_API_TOKEN>`。错误为 RFC 7807。

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/topics` | 创建主题（同步，返回 topic；同时入队 steward collection 创建，失败不阻塞） |
| POST | `/topics/{id}/outline` | 生成或重新生成大纲（SSE：`outline` 事件后 `done`）；主题已有节点时需 `replace=true`；`outline_status=confirmed` 后拒绝（409） |
| POST | `/topics/{id}/outline/confirm` | 确认大纲，带 `explain_mode`；`all` 时同时返回估算，不立即开始 |
| GET | `/topics/{id}/generation/estimate` | 全部生成的估算：待生成节点数、每节点 token 与依据、总量 |
| POST | `/topics/{id}/generation-runs` | 开始全部生成，带 `token_budget`；同一主题同时只能有一个未结束的 run（409） |
| GET | `/generation-runs/{id}` | 进度：各节点状态、`tokens_used`、run 状态（前端轮询） |
| PATCH | `/generation-runs/{id}` | `pause` / `resume` / `cancel` / 追加 `token_budget` |
| GET | `/topics` | 主题列表（含进度） |
| GET | `/topics/{id}` | 主题详情 + 大纲树 + 节点状态 |
| PATCH | `/topics/{id}` | 修改标题、目标、水平、指令、状态 |
| POST | `/topics/{id}/nodes` | 新增节点（可指定 parent、position、origin） |
| PATCH | `/nodes/{id}` | 重命名、移动、改状态 |
| DELETE | `/nodes/{id}` | 删除节点（§4 一致性规则） |
| POST | `/nodes/{id}/explain` | 生成讲解（SSE） |
| GET | `/nodes/{id}` | 节点详情：讲解、支线列表、笔记版本 |
| POST | `/nodes/{id}/branches` | 新建支线 |
| GET | `/branches/{id}` | 支线消息 |
| PATCH | `/branches/{id}` | 重命名、移动到其他节点、关闭 |
| POST | `/branches/{id}/ask` | 提问（SSE） |
| POST | `/topics/{id}/quick-ask` | 快速提问：归类节点、建支线、作答（SSE） |
| POST | `/messages/{id}/feedback` | `good` / `wrong` / `incomplete` + note + `reanswer`（默认 true）；后两者且 `reanswer=true` 时触发重答（SSE），否则同步返回 |
| GET | `/branches/{id}/messages/latest` | 流中断后取回最新一条 assistant 消息（见下方 SSE 语义） |
| GET | `/memory` | 学习者记忆列表（steward memories，§8.5） |
| DELETE | `/memory/{id}` | 删除一条记忆 |
| GET | `/nodes/{id}/notes` | 节点笔记 |
| POST | `/topics/{id}/export` | 生成 HTML，返回 export id |
| GET | `/exports/{id}` | 下载 HTML（`text/html`，附件或内联） |
| GET | `/memory/candidates` | 待确认的 KB 候选卡片（steward `awaiting_user`，§8.5） |
| POST | `/memory/candidates/{id}` | `accept` / `reject` |
| GET | `/usage` | 按主题 / role / 路径统计 token 与命中率 |
| GET | `/health` | 分层健康检查（见下） |

`/health` 返回 `{status: ok|degraded|down, checks: {db, steward, search, roles: {<role>: ok|unreachable}}}`。`down` 只在 DB 不可用或 role 配置不合法时出现（这两者也会阻止启动）；steward、搜索或某个模型不可达时为 `degraded`，服务仍接受请求（§6.4 第 8 行、第 7 行）。响应里不含任何密钥或 URL 中的凭据。

### SSE 事件（`api/sse.py` 唯一定义）

```
path      {step: scope|freshness|retrieve|judge|answer|writeback, status, detail}
tool      {name: kb_search|web_search|web_fetch, query|url}
token     {text}
citation  {kind: kb|web, entry_id? , url?, title?}
outline   {nodes: [...]}                       -- 仅生成大纲
done      {message_id, source, usage: {input, output}, path, warnings: [..]}
error     {code, message}
```

每个事件带 SSE `id`（同一流内递增序号），仅用于客户端去重，**不支持 `Last-Event-ID` 续传**（v1 决定）。

**提交点与中断语义**

- 开始流式输出前先在同一事务写入 user 消息和一条 `status=streaming` 的 assistant 消息占位（无内容），返回其 `message_id` 作为流的第一个 `path` 事件的 `detail.message_id`。
- 作答完成后，在**一个事务**里写入 assistant 消息内容、`usage_log`、`jobs`，然后发 `done`。`done` 之前客户端看到的 token 都是未提交内容。
- 客户端断开（含用户点"停止"）→ 服务端取消进行中的 LLM 调用，占位消息改为 `status=aborted`，已发生的 provider 调用照常记 `usage_log`（`ok=false, error_kind=aborted`），不入队任何 job。
- 客户端断开但服务端在断开前已到达提交点 → 消息照常提交；客户端重连后用 `GET /branches/{id}/messages/latest` 取回。
- 同一支线同一时刻只允许一个 `streaming` 消息；重复 POST 返回 409。
- role 失败 → `error` 事件后关闭流，占位消息改为 `status=failed`（前端显示"重试"）。因此 `messages` 增加 `status (streaming|done|aborted|failed)`，列表与导出只取 `done`。

## 10. 节点笔记与 HTML 导出

### 10.1 笔记结构

```json
{
  "summary": "…",
  "key_points": ["…"],
  "examples": [{"title": "…", "body_md": "…"}],
  "pitfalls": ["…"],
  "branch_highlights": [{"branch_id": "…", "question": "…", "answer_md": "…", "message_id": "…"}]
}
```

`notes.update` 只做增量合并：保留已有要点，合并新内容，去重；被标 wrong 并被替换的答案从 highlights 中替换为新答案。

### 10.2 导出

- Jinja2 模板渲染全部节点笔记，结构与大纲一致：目录、各节点小结、要点、示例、易错点、支线精华（可折叠）。
- 渲染栈（全部 Python 端，无 JS 运行时）：Markdown → HTML 用 `markdown-it-py`（GFM 表格、任务列表）；代码高亮用 **Pygments** 服务端渲染，内联其 CSS（Shiki 是 JS 库，后端跑不了，只在前端预览里用）；数学公式保留 `$…$` 原文，由内联的 KaTeX `auto-render` JS 在打开时渲染，KaTeX CSS 与 woff2 字体 base64 内联（约 1.5 MB，可接受）。HTML 输出经 `nh3` 清洗（白名单标签 / 属性），防止笔记内容注入脚本。
- 单文件：CSS、JS、字体全部内联；不发起任何网络请求（CI 用无头浏览器断言零外部请求）。
- 亮色 / 暗色：`:root` CSS 变量，`prefers-color-scheme: dark` 覆盖。
- 页脚：生成时间、各节点笔记版本、来源统计（kb / llm / web 占比）、模板版本。
- **确定性的定义**：同一组 `node_notes` 版本 + 同一模板版本 + 同一资产包，生成的 HTML 除页脚生成时间外逐字节相同；测试用固定时钟断言。渲染失败（模板错误、笔记 JSON 不合法）→ `exports.status=failed` 并记录 `error`，不留半成品。
- 前端在 iframe 中预览，提供下载与系统分享。也可导入 Obsidian（另存为 `.html` 或后续提供 Markdown 导出，§13）。

## 11. 前端

### 11.1 布局

| 宽度 | 布局 |
|---|---|
| ≥1200px（电脑、iPad 横屏） | 三栏：大纲树 / 节点讲解 + 支线 / 节点笔记预览 |
| 768–1199px（iPad 竖屏） | 两栏：大纲收进左侧抽屉 |
| <768px（手机） | 单栏逐层进入：主题 → 节点 → 支线；大纲用底部抽屉随时呼出 |

### 11.2 关键交互

- 大纲树：进度标记（未开始 / 学习中 / 完成）、拖动排序、插入前置节点。
- 节点页：讲解在上，支线列表在下；“在此提问”直接建支线。
- 消息：来源徽章（KB / 模型 / 联网 / 混合）、引用列表、反馈按钮；流式输出时状态行显示当前步骤。
- 新主题：对话框式入口（课题 → 固定澄清问题 → 大纲 → 编辑确认 → 选择讲解生成方式）；“全部生成”先显示估算再确认。
- 全部生成进度：主题页顶部进度条（已完成 / 总数、已用 token / 预算），暂停、继续、取消、追加预算；大纲树中节点显示“生成中 / 失败（可重试）”。
- 快速提问：全局输入框，提交后显示归到哪个节点，可修改；无合适节点时提示新增节点（§5.3）。
- 记忆候选：卡片式确认 / 拒绝，入口在顶栏徽标。
- 用量页：每个主题的 KB 命中率、各 role token、节省估算（KB 命中次数 × 该主题 compose 平均 token）。
- PWA：可安装，全屏；token 首次输入后保存在本地。

## 12. 实施计划（给 Claude Code）

每个阶段：plan mode 起步 → 测试先行 → 实现 → 全部检查通过 → **收尾**：`/simplify` 并应用修改、重跑检查 → Codex review →
逐条判断成立与否 → 修改成立的（bug 先写失败的测试；改动大或涉及 §4、§6、§8 时对修改再跑一次 Codex）→ 重跑全部检查与验收 →
勾选本节清单并写收尾记录 → 写 KB → 提议提交、等 owner 同意。命令与细节见 `CLAUDE.md` § Stage close-out。

### B0 — 脚手架

- [x] monorepo：`backend/`（uv、ruff、mypy strict、import-linter、pytest、pre-commit）
- [ ] `frontend/`（pnpm、Vite、TS strict、ESLint、Vitest、Playwright）：由另一个会话搭建（owner 2026-11-12 决定），B0 后端会话不碰
- [x] `docker-compose.yml`：`db`（pgvector 镜像，固定版本）；steward 以文档说明的方式在本机运行（宿主机 Ollama 需 `bge-m3:567m`，不配外部 LLM 时还需 `qwen3:8b`）
- [x] Settings、分层 `/health`、Alembic 基线（schema `banyan`）、CLI（serve / migrate / openapi）
- [x] **steward contract spike**（`tests/contract/`，`-m steward`；嵌入模式已跑通，对运行中 steward 的 `-m steward_live` 已写、待跑）：建 collection 带 `review_policy`
      → `create_entry` 带 tags（低风险样本）→ 等 outbox 同步后 `retrieve` 带 `filters.tags` 与 `format=context` 命中
      → `feedback(retrieval_id, item_id)` → `deprecate` 成功（key 带 review scope）→ 同 `Idempotency-Key` 重放返回同一 id。
      → 应用 `deploy/steward/banyan.yaml` 后检查 rerank 的 `score` 范围与 `high_risk_keywords` 的叠加方式。
      spike 先在 SDK 嵌入模式跑通，再对本机 steward 跑一遍；结论（字段名、延迟、定级行为）写进 §8 与 KB，再开始 B1
- [ ] CI：两端检查与测试（`.github/workflows/ci.yml` 已写：backend、contract、frontend 三个 job；要 push 后看结果才算通过）
- 验收：`banyan serve` + `/health` 绿；前端空壳在三个视口渲染；contract spike 全绿；CI 通过
- **收尾记录（2026-11-12）**：
  - 验收：`banyan serve` 后 `/health` 为 `ok`（db ok、steward / search not_configured）；后端 49 个测试与嵌入模式 contract spike 全绿，
    关键断言都做过变异；`-m steward_live` 待 steward 运行；前端空壳曾在本会话三个视口通过 Playwright，之后交给另一个会话；CI 待 push。
  - spike 改了设计：`retrieve.collections` 用名字（传 id 静默返回空）；review key 写入即 `verified`，改为两把 key（§8.1）；SDK 不收调用方的
    幂等键，改为 httpx 直发 + SDK 模型（§8.3）；steward 对 4xx 按键重放 24 小时，4xx 是终态（§4）；检索结果不带 tags（§8.3，B3 待定）。
  - /simplify：`_send` 拆成传输与状态映射两层，去掉只为一处服务的参数；review key 改为独立连接；steward 三项配置要么全有要么全无；
    `build_app()` 与 `create_app()` 分开，`openapi` 不再伪造数据库 URL；`/health` 返回模型而非手写 JSONResponse；URL 路径里的 id 校验为 UUID；
    测试共用 `app_client`；CI 的 uv 缓存按 job 分开。没采纳：用 SDK 私有的 `_api` 构造请求（不想依赖私有模块，契约测试兜底）；
    测试设置走校验路径（`model_copy` 只绕过 `_test` 一条规则，steward 同样做法）；合并两个契约测试的轮询（期限不同是有意的）。
  - Codex：三条成立并修复（先写失败的测试）——collection 创建用固定幂等键，4xx 后修好也会被重放（改为调用方传键）；空的
    `BANYAN_STEWARD_URL=` 被当成已配置、空 key 能通过（空值视为未设，URL 校验）；嵌入模式的契约测试证明不了两把 key 的分工（收窄说明，
    live 测试加 `active` 而非 `verified` 的断言）。修复后再跑 Codex：URL 只校验前缀（改为解析并要求主机），已修。
  - 等价变异记录：去掉 `version_table_schema` 测试仍绿，因为 `search_path` 首位就是该 schema；保留作防护。

### B1 — 学习树（pipeline 骨架，无 RAG）

- [ ] 数据模型 topics / nodes / branches / messages（含 status、retrieval / entry id 列）/ usage_log / jobs（含租约）
- [ ] role 层：Role 枚举、配置解析、Pydantic AI 封装、FakeLLM、usage 记录（每次尝试一行）、能力表启动校验、按阶段的必需 role
- [ ] 对话式入口：创建主题（同步）、固定澄清问题、`POST /topics/{id}/outline`（`outline.generate`，SSE）、大纲 CRUD 与排序、确认大纲；本阶段只开放 `explain_mode=manual`
- [ ] **ask pipeline 骨架**：scope / freshness / retrieve / judge / answer / writeback / record 七步都存在，
      `StewardClient` 用 `FakeSteward` 且返回空（路径表第 6 行），judge 因而被跳过；compose 暂无工具。
      这样 B3 只替换 `FakeSteward` 与补 judge 测试，不重写流程
- [ ] SSE：占位消息、提交点、中断与 409 语义（§9）；`messages/latest`
- [ ] 前端：主题列表、大纲树、节点页、支线页，三种布局；SSE 客户端（去重、中断后取回）
- 验收：手机与 iPad 上可完成“建主题 → 讲解 → 支线追问”；所有调用有 usage 记录；路径表第 6、7 行测试通过

### B2 — 任务运行时、讲解生成方式、笔记与导出

- [ ] `collection_ensure` 任务：`topics.steward_collection_id` 已有值就跳过（steward 的 collection 列表不分页、不按名字过滤，`ensure_collection` 每次都列全部，只该在首次和崩溃恢复时跑）
- [ ] `banyan/jobs/` 通用任务运行时（领取 `SKIP LOCKED`、租约回收、重试、退避、死信、幂等键、`dedupe_key`）；import-linter 契约禁止它导入 Banyan 其他包；业务处理器在 `worker/handlers/` 注册
- [ ] `node_explain` 任务（复用 pipeline，§6.0）；`explain_mode=prefetch` 与 `all`；`generation_runs`、估算、整次预算、并发上限、暂停 / 继续 / 取消、单节点重试；与交互式讲解的互斥
- [ ] 前端：讲解生成方式选择、估算确认、进度条与节点状态
- [ ] `notes.update` 与 node_notes；触发条件（§5.4）
- [ ] HTML 导出（单文件、亮暗、目录、折叠）；前端预览、下载、分享
- 验收：
  - 10 节点主题用 FakeLLM “全部生成”完成，每个节点恰好一条讲解
  - 运行中杀掉 worker，重启后租约过期的任务被重领，没有重复讲解
  - 预算设为约 3 个节点的量时 run 停在 `budget_exhausted`，追加预算后继续完成
  - `prefetch` 下打开第 N 节后第 N+1 节自动生成
  - 一个 5 节点主题导出的 HTML 离线可用，结构与大纲一致

### B3 — steward 集成（KB 优先）

- [ ] StewardClient：HTTP 实现（基于 B0 spike）、FakeSteward 补齐 tags 过滤 / review_policy / 幂等、契约测试 `-m steward` 覆盖 §8.3 每个方法
- [ ] prompt 注入防护（§6.2，`pipeline/sources.py`）
- [ ] 主题 ↔ collection（建主题入队、失败重试）；pipeline 的 scope（三级范围）/ retrieve（降级规则）/ judge / answer.from_kb / writeback；`proposed_entry_ids` 回写
- [ ] 路径表 §6.4 第 1、2、5、6、7、8、9、10 行的测试
- [ ] 反馈（按 retrieval_id + item_id）、`kb_deprecate`、重答、`reanswer=false`；来源徽章与引用；删除节点时的"同时失效"提示
- [ ] 用量页：命中率与节省估算
- 验收：**低风险**样本问题第二次提问走路径 1（high 风险内容进 steward 审核队列，不在验收内）；标 wrong 后重答并使 `proposed_entry_ids` 失效；用量页显示命中率

### B4 — 联网

- [ ] SearchProvider Protocol + 默认 adapter + FakeSearch；Fetcher（正文抽取、截断、URL 白名单、出站防护 §7.3）
- [ ] `answer.compose` / `node.explain` 变为 agent loop（kb_search / web_search / web_fetch，预算；`allow_web=false` 不挂 web 工具）
- [ ] `judge.freshness`；web 来源条目的 `url:` / `fetched_at:` / `valid_to:` tags；检索时过滤过期条目并入队 `kb_deprecate`
- [ ] 生成大纲时联网一次，写入 `topics.research_sources`；节点讲解把这份资料放进初始上下文，`web_fetch` 白名单包含这些 URL
- [ ] 路径表第 3、4、4b 行测试；SSE `tool` 事件、`warnings` 与前端显示
- 验收：时效类问题跳过 KB 并联网；引用可点击；所有模型（至少 Claude、GPT、DeepSeek）行为一致

### B5 — 记忆与主题指令

- [ ] 主题指令编辑与注入
- [ ] 学习状态计算；episode 提交任务（信号作为 system 消息附带，§8.2）
- [ ] 记忆页：记忆列表与删除（steward 自动生效，§8.5）；KB 候选卡片（确认 / 拒绝）；检索包含记忆
- 验收（真 steward，`-m steward`；记忆异步生成，测试轮询 `list_memories` 直到出现或超时，不假设立即可见）：
  - 抽取出的偏好（如“用 Java 类比”）在新主题的讲解中生效；删除后不再生效
  - 改口（“别用 Java 类比了”）后旧偏好不再出现在检索里（依赖引擎消解冲突，默认 lite-mem；换成 mem0 时由 steward 补偿）

### B6 — 打磨与部署

- [ ] 快速提问（`node.classify`）与支线移动；无合适节点时建议新节点（`origin=question`），用户确认后插入
- [ ] `branch.compress`；预算调优
- [ ] PWA（manifest、图标、app shell 缓存）
- [ ] 部署文档：本机 docker compose + Tailscale；Ollama 模型准备（`bge-m3:567m`、`qwen3:8b`）；steward 配置清单与 `banyan.yaml`
- 验收：手机经 Tailscale 安装 PWA 并完成完整学习流程

### 之后（v2 候选）

- 导入 Claude / ChatGPT 导出的历史对话
- Claude Agent SDK 模式（`answer.compose` 的可选后端）
- Markdown / Obsidian 导出
- 迁移 Oracle Cloud Always Free（API key 模式）

## 13. 待定问题

不要静默决定；询问，或在配置后面实现保守选项。

1. **默认搜索 provider**：Tavily / Exa / Brave。保守选项：Protocol + 一个 adapter，由配置选择。
2. ~~steward 审核策略~~ **已解决（2026-11-11）**：`CollectionCreate.review_policy` 支持 `low/medium → auto`，见 §8.1。遗留：steward 自己定级为 high 的内容进审核队列，Banyan 不绕过。
3. ~~embedding 一致性~~ **已解决（2026-11-12）**：steward 默认记忆引擎改为 lite-mem，向量用 bge-m3，与 lite-rag 一致（mem0 的 MiniLM 只在切回 mem0 时出现）。
4. **删除节点**时的 KB 条目：**v1 采用保守选项**（不自动失效，UI 提示用户选择，§4），用户可改。
5. **KB 直出**时是否完全不调用模型（直接展示检索到的历史答案）以进一步省 token？**v1 采用保守选项**（`answer.from_kb` 便宜模型改写）；若要切换，加 `BANYAN_KB_VERBATIM=true`，`path` 中记 `answer: verbatim`，provenance 为空。
6. **Claude Agent SDK 模式**是否需要，以及它与自有搜索工具的结果如何统一入库。
7. **License**（建议 Apache-2.0，与 lite-rag 一致）。
8. **记忆是否先确认再生效**（§8.5）：steward 默认自动写入。v1 接受自动写入 + 可删除。若要"确认后生效"，需向 steward 提 namespace 级 `memory_policy: confirm` 的变更需求。**需要用户决定**是否接受 v1 的降级。
9. **判定阈值校准**：`judge.sufficiency` 的 `coverage` 由便宜模型自报，未校准。B3 验收后用真实问答样本抽查命中答案的反馈率，若 `wrong` 率明显高于路径 2，上调阈值或改用两段判定。
10. **讲解生成方式的默认值**：v1 默认 `prefetch`（2026-11-11 建议，用户可在新主题流程中改选，也可改配置 `BANYAN_DEFAULT_EXPLAIN_MODE`）。

### 向 steward 提出的变更需求（不在 Banyan 侧绕过）

| 需求 | 用途 | 临时方案 |
|---|---|---|
| `EntryCreate.valid_to` 与 `source_refs` / `metadata` | web 来源条目的有效期与结构化来源 | tags 编码 + Banyan 检索时过滤过期条目（§8.2） |
| `EpisodeCreate.context` 或 metadata | 学习状态信号 | 附加 system 消息 |
| namespace 级 `memory_policy: confirm` | 记忆先确认再生效 | 自动生效 + 可删除（§8.5） |
| 按 entry 的反馈端点 | 不经 retrieval 的条目评价 | 只对本次检索的 item 反馈（§6.3） |
| SDK 写入方法接受调用方的 `idempotency_key` | 用 SDK 客户端发请求，同时保留按来源派生的键 | httpx 直接调 REST，只用 SDK 的模型（§8.3） |
| `EntryCreate.author_type`（`ai`） | 模型生成的条目如实记录作者，steward 的审核与统计能区分 | tag `author:ai`（§8.1、§8.2） |
