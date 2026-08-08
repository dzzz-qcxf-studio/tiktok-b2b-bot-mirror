# 03 — Core 层详解

> 关联: [索引](00-索引.md) | [架构设计](02-架构设计.md) | [Plugin层](04-Plugin层.md)
> 最后更新: 2026-08-08

Core 层是所有业务逻辑的所在，被 CLI/API/UI 三层共享。

## 3.1 数据模型 (`models/entities.py`)

21 个 SQLAlchemy ORM 实体（单文件维护）：

| 实体 | 表名 | 用途 |
| --- | --- | --- |
| `User` | `users` | TikTok 用户信息 + 状态跟踪 |
| `Strategy` | `strategies` | 触达策略（评论/私信模板） |
| `Message` | `messages` | 评论/私信发送记录 |
| `Reply` | `replies` | 用户回复 + 情感分析 |
| `DailyReport` | `daily_reports` | 每日统计快照 |
| `ExperienceRule` | `experience_rules` | 闭环迭代沉淀的规则 |
| `Account` | `accounts` | 系统登录账号 |
| `TikTokAccount` | `tiktok_accounts` | TikTok / 抖音账号与登录态 |
| `ConfigRecord` | `config_records` | 可通过 Web UI 修改的配置 |
| `PipelineJob` | `pipeline_jobs` | 统一持久化任务 |
| `PipelineJobStage` | `pipeline_job_stages` | 任务阶段状态 |
| `PipelineSchedule` | `pipeline_schedules` | 统一定时计划 |
| `PipelineJobUser` | `pipeline_job_users` | Job 用户隔离快照 |
| `LLMProvider` | `llm_providers` | OpenAI-compatible 上游配置 |
| `LLMRoute` | `llm_routes` | 五类业务 Route 的 Provider 链 |
| `LLMRequestLog` | `llm_request_logs` | 不含内容与密钥的用量日志 |
| `AcquisitionCampaign` | `acquisition_campaigns` | Job 创建时冻结的目标画像与搜索预算 |
| `AcquisitionKeyword` | `acquisition_keywords` | Job 关键词及使用/视频/候选效果 |
| `DiscoveryEvidence` | `discovery_evidence` | 候选的关键词→视频→评论/作者来源证据 |
| `CandidateAssessment` | `candidate_assessments` | 不可覆盖的 AI 资格评估快照 |
| `CandidateReviewAudit` | `candidate_review_audits` | 人工通过/淘汰/补资料/改标签审计 |

**User 状态机：**

```text
pending → qualified → contacted → replied
         ↘ rejected
```

每次状态变更自动记录 `updated_at`。

## 3.2 存储层 (`storage/`)

**双数据库架构：**

```python
# SQLite — 给人看的
db = get_db()           # 全局 Database 单例
store = SqliteStore()   # CRUD 仓库

# ChromaDB — 给 AI 用的
vector = VectorStore()  # 3 collection: user_profiles / strategies / experience
```

**SqliteStore** 提供:
- User CRUD + 状态更新
- Strategy/Message/Reply CRUD
- DailyReport UPSERT + 列表
- ExperienceRule CRUD
- ConfigRecord 读写
- 聚合统计: `get_keyword_effectiveness()`, `get_category_distribution()`

**LLMStore** 提供:

- Provider 创建、更新、启停、删除与环境变量名校验
- 五类 Route 的完整链条原子替换
- 请求状态、Token、延迟与 fallback 元数据记录
- 聚合用量统计；不保存 prompt、response、API Key 或上游错误正文
- SQLite 写操作使用 `BEGIN IMMEDIATE` 串行化关键检查；布尔、整数、超时与延迟采用
  严格类型和有限范围校验，不把字符串或浮点数静默强制转换
- SQLAlchemy 仅因先行 SELECT 产生逻辑 autobegin 时仍会取得 IMMEDIATE 锁；如果调用方
  已开启未受管理的真实 SQLite 事务，则 fail-fast，避免假装拥有可证明的写边界

**VectorStore** 提供:
- `add_user_profile()` — bio → embedding
- `search_similar_users()` — "找一个类似 @alice 的用户"
- `search_similar_strategies()` — "针对这类用户什么话术最有效"
- `search_experience()` — "以前遇到过类似情况吗"

## 3.3 事件总线 (`events/bus.py`)

异步事件总线，解耦 Pipeline 阶段：

```python
bus = get_event_bus()
bus.subscribe(EventType.USER_QUALIFIED, my_handler)
await bus.publish(Event(EventType.USER_QUALIFIED, {"user_id": 42}))
```

**10 种事件类型：**

| 事件 | 触发时机 |
| --- | --- |
| `COLLECT_DONE` → `ITERATE_DONE` | 每个 Pipeline 阶段完成 |
| `USER_DISCOVERED / QUALIFIED / REJECTED / CONTACTED / REPLIED` | 用户状态变更 |
| `PIPELINE_START / PIPELINE_END` | Pipeline 生命周期 |
| `ERROR_OCCURRED` | 错误捕获 |

**特性：** 并发订阅、错误隔离、最近 1000 条历史。

## 3.4 扩展注册器 (`extensions/registry.py`)

替代 ChopperBot META-INF 机制：

```python
reg = get_registry()
reg.register_collector(MyCollector())
reg.register_channel(MyChannel())
reg.get_collector("keyword")      # 按名获取
reg.list_plugins()                # {"collectors": [...], ...}
```

三类插件基类 (ABC)：
- `CollectorPlugin` — `async collect(config) → list[dict]`
- `ChannelPlugin` — `async execute(target, content) → bool`
- `FilterPlugin` — `async evaluate(user) → dict`

## 3.5 阶段 01 获客 Agent 边界

`services/acquisition_agents.py` 定义版本化、`extra=forbid` 的
`DiscoveryPlan / EvidenceObservation / CandidateObservation`。Agent 只能提交公开页面
观察，不能写 `qualified`、人工标签或触达消息。`DiscoveryPlannerAgent` 按默认
70% effective + 30% new 选词，明确排除 disabled、cooling 和 low_yield。

`ExplorationBudgetTracker` 在每次结构化 extract 时原子消费关键词、视频、评论、主页、
作者主页作品和总观察预算；拒绝项不会部分污染计数，截断原因按受影响用户保存。
`Stage01CandidateAgent` 在 SQLite `BEGIN IMMEDIATE` 下批量 upsert 用户、Job 关联和状态，
只查询本批用户已有证据，以稳定来源指纹保证同 Job 重试不复制证据，同时保留不同视频、
评论和来源路径。

### 阶段 02—04 资格与触达边界

`EnrichmentResult / QualificationResult / StrategyResult` 都是版本化、`extra=forbid` 的
严格契约。Agent 输入只投影公开字段，并限制单字段长度、证据条数和总提示规模；Cookie、
Secret、浏览器 Profile 路径和未知 Campaign 字段不会进入外部模型提示。

- `EnrichmentAgent` 只整理公开主页、代表内容和来源证据，未知信息写入 `missing_fields`；
- `QualificationAgent` 分开输出匹配度与可信度，保留多标签、正负证据和判断理由；采购需求
  评论属于强正面证据，未知员工数、注册资本等不能作为负面证据；
- AI 建议 `qualified` 时只落为 `manual_review`；只有人工 `approve` 能写入 `qualified`；
- AI 更新使用 `review_version + 起始资格状态 + manually_confirmed_at` 条件更新。模型运行
  期间发生的人工通过或淘汰具有最终优先权，过期 AI 结果不能覆盖当前状态、标签或双评分；
- AI 可以保留“建议淘汰”和硬排除元数据，但不能写终态 `rejected`；所有淘汰都回到
  `manual_review` 由人工确认；
- `CampaignStrategyAgent` 把公开资料视为不可信数据，输出必须通过严格策略契约。模型自由
  话术只作为不可信建议，落库和触达使用项目内确定性中性模板；非法 Schema、累计提示超限、
  URL、联系方式或控制字符不会落策略，也不会进入触达阶段。

### Hermes H1 建单、重试与业务投影

- `services/acquisition_jobs.py` 的 `AcquisitionJobService` 是 AI 获客建单边界：先完成账号/
  Provider 预检，再复用 `PipelineJobService` 的外部 Session 能力，把 Job、Campaign 和
  1—100 个初始关键词作为一个事务提交。快照自动标记 `businessMode=ai_acquisition` 和
  `acquisitionSchemaVersion=1.0`，但运行分支仍以 Campaign 是否存在为权威。
- `PipelineJobService.retry_job()` 对 AI Job 固定从 `collect` 开始，并在同一事务复制 Campaign
  与关键词文本、语言、类型、来源、状态；不复制 Evidence、Assessment、人工审计或上次
  计数，因此新任务的 usage/video/candidate 等计数为 0。legacy Job 仍从首个失败阶段重试。
- `services/acquisition_jobs.py` 的共享校验器同时供 API 与 `AcquisitionJobService` 使用：阶段
  必须是 `PIPELINE_STAGES` 的严格有序子序列并包含 `collect`；配置快照会递归拒绝凭据类键、
  `authHeader/authValue`、标量 Bearer 值及结构化认证容器，且验证错误不包含原始敏感键名或值。
- `services/business_read_model.py` 提供只读 `BusinessReadModel`。它按 Job 创建时间与 Job ID
  选择用户最新的 Campaign 关系；展示状态依次优先 Reply、已发送 Message、最新资格终态、
  AI 待处理状态和 legacy User。分类、双评分、标签与来源 Job 同样来自该最新关系。
- `User` 不接收 AI 资格或分类反写。Users/Stats、Dashboard、Wordcloud、Lead 和 Reports
  Overview 均消费同一投影，避免某个页面有 AI 数据而其他图表仍为空。
- 关键词效果将 AI Evidence 与 legacy `keyword_search` 按同名词、不同用户实时合并；AI
  合格数只看同 Job 的 `qualification_status=qualified`，不读取可能过期的持久化计数器。
- “今日”统计以 UTC 00:00 为边界；报告漏斗、地区、情感和商业意向均来自真实 User、
  Message 与 Reply，其中商业意向统计已发送消息对应的不同回复用户。

## 3.6 配置管理 (`settings.py`)

Pydantic Settings 的 legacy 字段仍从 `.env` 读取；首次迁移只把环境变量名
`LLM_API_KEY` 写入 Provider 表，不复制密钥：

```python
s = get_settings()
s.llm_api_key       # ${LLM_API_KEY}，仅迁移期兼容读取
s.tiktok_keywords   # ["wholesale", "importer"]
s.daily_dm_limit    # 12
```

## 3.7 LLM 数据与执行边界

`LLMProvider / LLMRoute / LLMRequestLog` 与 `LLMStore` 让 SQLite 成为配置和用量
唯一权威源。`LLMRouter` 每次调用读取最新 Route/Provider 快照，使用
`OpenAICompatibleProvider` 执行请求，并统一完成：

- Route 优先级与最多三家 Provider 的有界 fallback；
- 关闭 SDK 隐式重试，保证“一家一次、全链最多三次”对应真实 HTTP 调用；
- HTTP/网络/超时失败分类，鉴权、参数、配置与无效 JSON 快速失败；
- generation lease 隔离并发结果的三次失败 Open、60 秒后单探针 Half Open 熔断；
- 成功、失败、Token、延迟、错误分类和 fallback 元数据记录；
- 在线程中读取 SQLite；遥测写入有界后台队列，异常或队列满不改变结果、fallback 或熔断；
- 从 `api_key_env` 动态读取密钥，配置或密钥变化时重建 SDK Client；
- 轮换、Provider 删除和 Router shutdown 使用可重试关闭任务，只在真实关闭成功后释放引用；
- 删除 Provider 的后台关闭不阻塞其他 Route，请求路径只负责启动并登记关闭任务；
- 不在异常、日志或数据表中保存 prompt、response、API Key 或完整上游正文。

`llm/client.py` 仅保留 `get_llm_client()`、旧构造器和 JSON 提取兼容入口，默认返回同一个
Router，不形成第二套执行系统。Task 9 再把现有业务调用点映射到明确 Route。

## 3.8 浏览器封装 (`browser/client.py`)

异步 Playwright，单例管理：

```python
async with BrowserClient() as browser:
    await browser.navigate("https://tiktok.com/@alice")
    await browser.fill("input", "Hello")
    await browser.click("button")
```
