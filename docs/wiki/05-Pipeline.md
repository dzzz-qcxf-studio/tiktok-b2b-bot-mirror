# 05 — Pipeline 编排

> 关联: [索引](00-索引.md) | [Plugin层](04-Plugin层.md) | [CLI-API-UI](06-CLI-API-UI.md)
> 最后更新: 2026-08-11（v0.7.3，Hermes 获客作战中心集成）

## 单一任务入口

```text
Web UI / REST API / CLI / Scheduler
                  │
                  ▼
        PipelineJobService
                  │
                  ▼
 SQLite pipeline_jobs + pipeline_job_stages
                  │
                  ▼
       Dispatcher → Runner
                  │
                  ▼
 BrowserProvider → PipelineService → Plugins
```

所有入口只创建持久化 Job。旧 `/api/pipeline/run` 也是返回 HTTP 202 的兼容包装，
不会在请求线程里直接运行 `PipelineService`。手动任务与定时任务因此共用同一队列、
状态机、取消/重试规则和 UI 历史。

任务必须指定：

- `platform`: `tiktok` 或 `douyin`
- `accountMode`: `auto` 或 `specified`
- `accountId`: `specified` 时必填，且账号必须属于同一平台
- `stages`: `collect/filter/strategy/outreach/report/iterate` 的非空、无重复子集

## 运行组件

| 组件 | 职责 |
| --- | --- |
| `PipelineJobService` | 校验平台、账号、Provider，创建/查询/取消/重试 Job |
| `AcquisitionJobService` | 原子创建 AI 获客 Job、Campaign 和初始关键词 |
| `PipelineScheduler` | 解析标准五段 cron 和时区，通过同一 Service 创建 `trigger_type=schedule` Job |
| `PipelineDispatcher` | 只认领 SQLite 中 queued Job；账号繁忙时不丢任务 |
| `PipelineJobRunner` | 获取账号租约和 Browser Session，逐阶段落库 |
| `PipelineRuntime` | 服务启动时恢复中断任务，统一启停 Scheduler/Dispatcher |
| `PipelineService` | 在强制的 `PipelineRunContext` 内执行六个业务阶段 |
| `BusinessReadModel` | 把 Job 级 AI 结论投影给现有用户、Lead、仪表盘和报告接口 |
| `PipelineLiveStore` | 持久化按 Job 隔离的实时事件和决策关卡；提供增量序号与 CAS 终态 |
| `PipelineLiveEventRecorder` | 通过命名事件和严格白名单写入安全遥测；SQLite 锁冲突时快速 fail-open |

`PipelineRunContext` 固定携带 `job_id/platform/account_id/account_username/
browser_session`。Job 执行路径没有全局浏览器回退；上下文缺失、平台/账号不匹配或
Session 已释放都会 fail closed。

### AI 获客原子入口

AI 获客任务必须通过受认证的 `POST /api/acquisition/jobs` 创建。该入口要求 `stages` 包含
`collect` 且是六阶段定义的严格有序子序列，并递归拒绝 `configSnapshot` 中的凭据类键；API
与 Service 复用同一校验。通过输入闸门后，服务在事务外完成账号与 Provider 预检，再以单事务写入 Job、阶段、一个 Campaign
和 1—100 个关键词。事务提交前 Dispatcher 看不到 queued Job；创建失败不会留下缺少画像
或关键词的半成品任务。旧的 Job 后补 Campaign 接口只为兼容和诊断保留，H2 UI 不应继续
使用两次请求建单。

### H2 四步 AI 获客创建器

`/pipeline` 只保留一套任务控制台。页面顶部的 `AcquisitionJobCreator` 负责组装获客定义，
父页面继续负责统一任务历史、详情轮询、取消和重试：

1. **执行配置**：选择 TikTok/抖音、自动/指定账号和本次阶段；实时读取 Browser Provider
   与已登录账号能力。`collect` 始终选中且不可取消，阶段顺序按六阶段定义规范化。
2. **目标画像**：TikTok 至少一个目标国家；抖音固定 `CN/zh-CN`。行业和客户角色必填，
   产品、语言和排除对象可选，所有标签去空白并拒绝重复。
3. **探索策略**：硬性排除/必要信号与阶段 02 偏好分区保存。员工数、注册资本、上市状态、
   公司体量和成立年限只用于阶段 02 按需核验、排序与人工复核，不是阶段 01 强制淘汰条件。
   至少一个初始关键词；七项预算与 effective/new 比例在前端复用后端边界，默认 70/30。
4. **确认创建**：确认页从同一份规范化 draft 生成，不维护第二份手工 payload。提交期间锁定
   步骤和按钮，仅调用一次 `POST /api/acquisition/jobs`。成功摘要深拷贝服务端返回的
   `job/campaign/keywords`，继续编辑表单不会改变已创建任务的摘要。

表单错误保留输入并允许重试；FastAPI 数组型 422 detail 会转换为字段路径与消息，不显示
`[object Object]`。桌面保持宽卡布局；`<=900px` 条件/摘要单列，390px 操作目标至少 44px。
应用壳在移动端使用底部横向导航，不再由固定侧栏压缩主内容；主导航与系统导航共用独立
横向滚动区，账户/退出区位于滚动区之外，不能覆盖系统入口。页面级样式不得通过
`:global(.sidebar)` 改写应用壳，账户区始终可触达，用户仍可主动结束会话。

### H4-A 持久化实时事件底座

Pipeline 实时作战窗口首先建立了两个持久化基础表：

- `pipeline_job_events` 使用 SQLite `AUTOINCREMENT` 的全局单调 sequence，并按
  `job_id + sequence` 增量读取；删除最大序号后也不会复用。事件保持 append-only，
  已提交 sequence 不会被合并、覆盖或删除，因此断线客户端使用 `afterSequence` 恢复时
  不会重复或漏掉已提交事件；
- `pipeline_decision_checkpoints` 保存版本化关卡、有限选项、服务端默认项、deadline 和
  resolution。SQLite partial unique index 从数据库层保证同一 Job 同时最多一个 pending
  checkpoint；resolve 与 cancel 使用条件更新 CAS，人工、超时和取消竞争只有一个终态获胜。

所有事件只能通过生命周期、阶段、浏览、决策和候选等命名记录方法写入。每类 payload 和
checkpoint context 都执行字段名、类型、长度、嵌套结构和敏感键双重校验；错误终态必须携带
注册的稳定 error code，公开文案从不可变映射生成，不保存异常正文。Cookie、Token、API Key、
Authorization、浏览器 Profile、Prompt/Response 和未知字段不能落库。

连续或超额的 `scroll/wait` 高频事件采用 append-only 抑制，不改变已提交 sequence；
`extract/error/done` 等关键事件始终追加。Recorder 使用独立 `NullPool` Engine 和 50ms SQLite
锁等待，不提交或回滚调用方事务；遥测写入失败返回 `persisted=false`，不推进内存 watermark、
不消耗高频预算，也不改变 Pipeline 业务结果。Job 终态成功提交后清理对应内存缓存，后续读取
仍从数据库恢复权威 watermark。

在 H4-A 提交点只交付了模型、Store、Recorder 和并发/旧库兼容测试；当时 Runner 尚未向该
Store 写入真实 Job 事件，`waiting_decision`、10 秒默认、受认证实时 API 和 Web 作战窗口均未
完成。下面按后续小步记录新增能力，当前页面仍不应被描述为已经支持互动关卡。

### H4-B 服务端决策状态与 Runner 策略

Job 与 Stage 增加 `waiting_decision`，只能通过专用双实体 CAS 在
`running ↔ waiting_decision` 间迁移。通用 `set_job_status()` 明确拒绝进入等待或从等待恢复，
避免调用方只更新 Job 而留下 Stage 状态不一致。专用迁移使用 savepoint 和定向 identity 同步，
失败时整体回滚且不 flush/expire Session 中无关实体。

`DecisionGateService` 使用固定、不可变的关卡定义注册表。普通关卡默认由服务端记录 10 秒
deadline；页面是否打开不影响人工/timeout 对同一 checkpoint 的 CAS。最后一秒竞争只有一个
resolution 获胜，迟到调用返回同一权威结果。普通超时只选择已注册默认项，服务没有任何写入
候选 `qualified/rejected` 的路径。

等待状态取消会在同一事务中取消 active checkpoint，并把 Job、当前 Stage 与尚未开始的 Stage
结束为 cancelled；服务启动恢复把遗留 waiting Job 变为 interrupted，并关闭 pending
checkpoint。等待协程被取消或 clock/sleeper/数据库发生异常时，Gate 先幂等取消 checkpoint 并
恢复运行；恢复失败则只把当前 Job/Stage 降级为 interrupted/failed，不能永久遗留 waiting。
关闭事件循环上的陈旧 waiter 会被逐个剔除，不会让已经提交的 resolve/cancel 对外误报失败。

Task 5 已把 Gate 接入 AI 获客 Runner；legacy Job 没有 AcquisitionCampaign，不创建这些关卡。
策略只公开当时真正可执行的固定 option 子集，且默认项必须属于该子集：

- **collect 完成后**：仅当候选为零、`needs_more_evidence` 比例过高或 Collector 明确返回权威
  截断时创建 `insufficient_evidence`。默认 `continue_with_current_evidence`；还可跳过剩余管线或
  取消任务。当前没有安全的累计预算深挖执行器，因此即使快照仍有余量也不公开
  `deepen_with_remaining_budget`，不会用一次新执行重新消费完整冻结预算；
- **filter 完成后**：只读取当前 Job、当前平台持久化的 `manual_review/need_enrichment` 待办；
  有待办时创建 `qualification_review`，默认 `continue_with_qualified_only`，不会修改任何候选
  `qualified/rejected` 终态。批量补资料执行器尚未实现，所以不公开对应 option；
- **outreach 开始前**：创建 `outreach_confirmation`。只有建单包含 outreach 且 Campaign 与
  Job 平台一致时才保持默认 `execute_approved_outreach`；执行该默认动作仍只处理当前 Job、
  当前平台、人工 `qualified`，并逐条使用与实际 outreach 相同的严格 `StrategyResult` 校验，
  无合法目标时安全返回零。`skip_outreach` 会直接把本阶段置为 skipped，不调用发送执行器。

普通关卡仍由服务端在 10 秒后执行默认项，因此全程无人选择也能进入终态。用户在普通关卡中
主动选择 `open_review_workbench` 时，普通 checkpoint 立即解决并进入第二个显式人工会话；该
会话没有自动 timeout，页面关闭也不会替用户作出资格结论。Runner 在进入人工会话前释放
Browser Session 和账号并发租约；复核完成后不复用旧对象，而是复用 `PipelineJobService`
建单时的完整 preflight，重新检查账号仍为 `logged_in`、账号与 Job 平台一致、TikTok 指纹
Provider/Profile 完整及 Provider 可用，再获取全新租约、Browser Session、PipelineService 和
`PipelineRunContext`。Provider 调用正常返回但 Session 未标记 `_released` 仍视为释放失败；
只有 Browser Session 已确认释放后才会归还账号租约。一次释放失败会在 Runner 最终清理中
重试。若仍无法确认释放，Job 稳定失败，Session 与 lease 会进入可触达的
账号级 quarantine：该账号继续 fail-closed，但不占用平台并发名额，其他账号仍可运行；进程内
幂等恢复入口会在后续释放成功后解除 quarantine。释放、重获、取消或等待异常都会关闭当前
checkpoint/waiter，并收口到稳定 Job/Stage 终态。

Runner 在普通关卡返回后、每次实际调用阶段执行器前，以及人工复核后等待新平台 slot 的循环中
都会重新读取权威取消状态。因此选择 `execute` 的同一时刻发生取消不会再调用 outreach；人工
复核完成后即使平台 slot 被其他任务占用，取消也不必等待该 slot 释放即可进入 `cancelled`，且
不会获取新的 Browser Session 或遗留目标账号租约。

错误关卡只接受显式稳定分类或明确异常类型。`network/timeout/upstream_server` 最多重试一次，
重试会增加当前 Stage attempt，但不会重跑已经成功的其他阶段；再次失败默认 `skip_stage` 并让
Job 形成 `partial_failed`。账号阻断默认安全跳过；尚无真实执行器的深挖、批量补资料和账号恢复
动作一律不公开。Task 5 核心/Runtime、H4-A、Pipeline API/Core、Acquisition 与 Browse 分批
组合回归共 **474 passed**。

### H4-B Task 6：Job-scoped Hermes/Browse 事件

Pipeline 的执行上下文现在显式携带同一个 `event_recorder` 与真实 `job_id`，并沿
`PipelineService → HermesEvidenceAgent → BrowseAgent` 传递；不使用全局 current-job。阶段 01
产生的每条持久 Browse 事件固定 `stage=collect`。独立 CLI Browse 不提供 Job，因此只继续发布
兼容的进程内 `BROWSE_STEP/BROWSE_DONE`，不会写 `pipeline_job_events`；业务 UI 后续只以持久
Job 事件作为权威源。

Browse step 只通过 `record_browse()` 安全 builder 保存受信平台 URL、受限 rationale、页面类型、
动作参数、证据计数和截图 SHA-256 hash，不保存截图字节、DOM、Prompt/Response 或原始 evidence。
每次 Browse 只写一条权威 `browse.done`；完成预算会从内部 snake_case 转换为白名单公共字段，不把内部键带入持久 payload。
两个 Job 并行运行时各自使用显式上下文，事件不会串流。Recorder 抛错或 SQLite 遥测写失败均被
调用点 fail-open，不能改变 BrowseResult，也不能增加页面、LLM、时间或 observation 预算。同步
Recorder 由单次 Browse 的有序后台队列执行；结束时最多等待 1.25 秒，超时会丢弃剩余遥测并回收
命名 worker，不能阻塞 BrowseResult、Job 结束或账号租约释放。注入的同步 Recorder 单次调用必须
自行有界；默认 `PipelineLiveEventRecorder` 使用最多 1000ms 的 SQLite busy timeout。

Runner 使用同一个 Recorder 记录 Job/Stage 的 running 与终态生命周期；DecisionGate 在真实
checkpoint 事务提交后立即记录 pending，并在 human、timeout 或 cancel 的权威 CAS 提交后只记录
一个 `resolved/expired/cancelled` 终态。Runner 的批量 Stage 终态与 Job 终态在同一业务事务内
校验 CAS；失败会整体回滚，成功提交后才按 Stage 顺序记录事件，最后记录 Job 终态。遥测记录本身
不位于业务事务内，失败不会回滚状态机。
Task 6 专项回归 **223 passed**；H4-A/Policy/Runtime、Acquisition 与 Browse E2E 相邻回归去重后 **133 passed**。

### H4-C 实时 API 与 H4-D Task 9 监控组件

H4-C 已提供受认证的 Job live 首屏、增量 history、SSE、active checkpoint、普通 resolve 和
人工 review-complete。旧全局事件接口认证后固定 410，不再返回跨 Job 或原始 EventBus 数据；
前端 token 只通过 Header 发送，SSE 断开后按 sequence 从持久 history 恢复。

H4-D Task 9 新增 `HermesMissionMonitor`。组件只消费上述安全 DTO，展示真实阶段、行动、关键词、
预算、指标和有界事件列表，不生成演示数据。普通关卡倒计时仅依据服务端 deadline，归零后等待
权威 resolution；人工选择 pending 时防重复，409 使用数据库权威结果。终态任务直接进入回放，
不建立 SSE。收起、切换 Job、卸载都会 abort；展开重新读取 live，pending 决策事件会刷新完整
checkpoint，旧关卡终态不会覆盖新的 active checkpoint。

Task 9 专项回归 **14 passed**。该组件及阶段 01/02 业务卡、候选复核抽屉现已由 H4-D Task 12
以唯一实例挂入 Pipeline 页面，用户可以在同一任务详情内查看实时运行、处理关卡和复核候选。

### H4-D Task 10：阶段 01—02 业务结果

阶段 01 业务组件组合受认证的 `stage-01` 与 `keywords` 响应，展示候选、证据、关键词表现、
来源类型和发现状态；阶段 02 组件展示四个资格状态、匹配/可信双评分和人工待办。两个组件都只
显示业务字段，不在主界面渲染原始 Stage JSON；legacy、加载、空、失败、预算截断和稳定脱敏
错误均有独立状态。
两个组件的文字、状态色、表面和圆角均复用全局设计令牌，避免 Pipeline 页面形成第二套视觉协议。

候选列表增加 `keywordId/sourceType` 可选查询参数。两项存在时要求同一条当前 Job 证据同时匹配，
查询使用 correlated `EXISTS`，因此多条证据不会复制候选，分页 total 与 items 同源，其他 Job
的关键词或来源不能串入。发现状态、关键词、来源和资格状态卡均发出与该 API 一致的精确筛选
对象，供 Task 12 在同一 Job 上下文中打开候选工作台。

Task 10 验收：后端 Acquisition API **50 passed**，阶段组件 **6 passed**，类型检查、Python 编译
和差异检查通过。候选复核抽屉已由 Task 11 完成，Pipeline 页面集成仍待 Task 12。

### H4-D Task 11：候选复核工作台

`CandidateReviewDrawer` 严格绑定当前 Job，提供候选队列、独立证据/审计分页、公开主页与来源链、
双评分、标签、缺失字段和四状态人工动作。`manual_review` 可以直接通过、淘汰或请求补资料，
`need_enrichment` 可以完成补资料、通过或淘汰；`qualified/rejected` 为终态，界面不再提供修改动作。

mutation 成功后必须重新读取队列、详情和审计，任一权威读取失败都不会发送成功事件，并会清除
旧的可操作详情。切换 Job/候选、关闭或卸载会取消读取并使旧 generation 失效。显式人工会话只接受
同 Job、pending 且匹配 id/version 的 manual checkpoint；同一 Job 的新 checkpoint 会立即解锁界面，
旧响应不会解决或锁住新关卡。专项 **12 passed**、类型检查通过。该抽屉现已由 Task 12 挂载到
`Pipeline.vue`，并与阶段筛选和人工关卡共享同一 Job 上下文。

### H4-D Task 12：Pipeline 页面集成

选中 Job 后，详情顶部只挂载一个按 Job id keyed 的 `HermesMissionMonitor`。页面继续用既有 5 秒
非重入轮询刷新 Job/Stage 权威状态，但不直接调用实时订阅 API，因此不会形成第二条 SSE。
`waiting_decision` 使用 warning 状态并允许取消。

AI 获客任务通过冻结快照的 `businessMode=ai_acquisition` 判定，并兼容既有
`creatorSource=pipeline_ui` 快照。collect/filter 阶段分别显示发现与资格业务卡；卡片筛选和监控器
发出的人工复核入口都只打开当前 Job 的唯一 `CandidateReviewDrawer`。候选 mutation 或人工会话
完成后只刷新同一选中 Job；切 Job 会立即关闭旧抽屉，旧事件不能刷新新任务。

legacy Job 继续显示兼容摘要。所有存在结果的 Stage 原始 JSON 只保留在一个默认关闭的“技术诊断”
折叠区，不再分散在每个阶段主内容中。H4-D 四组专项 **53 passed**，Pipeline 集成专项 **21 passed**，
Smoke **142 passed**，类型检查与生产构建通过；独立复审无 Critical/Important。

## 阶段执行流程

```text
Runner 为每个 Stage: pending → running → terminal
  │
  ├── 阶段1 _run_collect:
  │   ├── 无 AcquisitionCampaign → 保持旧关键词用户搜索路径
  │   └── 有 AcquisitionCampaign → 新获客路径
  │       ├── 冻结画像 + effective/new 70/30 关键词计划
  │       ├── Pipeline 注入 HermesEvidenceAgent，先确定性进入关键词视频搜索页
  │       ├── 视频/评论优先；即使 Agent 达到 max_steps，仍执行直接用户辅助搜索
  │       ├── 跨关键词共享 keywords/videos/comments/pages/duration/LLM 等预算
  │       ├── Schema、平台、整批预算和权威 metrics 双重校验
  │       ├── 全部关键词 max_steps 且视频/用户证据均为零时失败关闭
  │       └── 批量写用户/Job 关联/多来源证据；相同重试幂等
  │
  ├── 阶段2 _run_filter:
  │   ├── 无 AcquisitionCampaign → 保持 legacy 预筛 + LLMFilter 路径
  │   └── 有 AcquisitionCampaign → 新资格路径
  │       ├── 按 user_id keyset 分页处理当前 job + platform 候选，不截断在前 200 条
  │       ├── 公开字段白名单 + 采购评论/来源多样性优先的 20 条证据
  │       ├── 单字段上限 + 最终 UTF-8 Prompt 24,000 字节硬上限
  │       ├── EnrichmentAgent → QualificationAgent（route=qualification）
  │       ├── 追加双评分、多标签、证据和缺失字段 assessment
  │       └── AI 条件更新；qualified/rejected 均只建议，人工版本变化时 stale_skipped
  │
  ├── 阶段3 _run_strategy:
  │   ├── Campaign 只查询当前 Job + platform 中人工确认的 qualified 用户
  │   ├── keyset 分页覆盖全部合格用户，不截断在前 100 条
  │   ├── CampaignStrategyAgent 严格校验；模型自由话术替换为固定中性模板
  │   └── 合法结果写入 strategies.job_id；Schema/安全失败不落库
  │
  ├── 阶段4 _run_outreach:
  │   ├── JOIN 当前 Job 的 strategies × users；Campaign 再次检查 qualified
  │   ├── Campaign 发送前重新用 StrategyResult 校验已落库模板
  │   ├── 发送前预留 messages(status=sending)
  │   ├── CommentChannel.execute(target, template)
  │   ├── DMChannel.execute(target, template)
  │   └── 成功→sent；异常→uncertain（不会自动重复触达）
  │
  ├── 阶段5 _run_report:
  │   ├── 阶段结果按 Job 隔离
  │   ├── 全局日报按 Job 用户和 sent 消息聚合
  │   └── 推送 Telegram（如果配置了 TOKEN）
  │
  └── 阶段6 _run_iterate:
      ├── 只分析当前 Job + platform 数据
      ├── llm.json_completion(analysis prompt)
      ├── vector metadata 写入 platform/job_id
      └── ExperienceRule 写入 platform/job_id
```

## 状态、取消、重试与恢复

- Job：`queued → running → succeeded/partial_failed/failed`；
  运行中取消先进入 `cancelling`，在决策返回、执行器调用前、人工重获 slot 等安全边界结束为
  `cancelled`。
- Stage：`pending → running → succeeded/failed/skipped/cancelled`。
- queued 任务取消会直接变为 `cancelled`；终态不可原地重开。
- retry 会创建新 Job 并记录 `retry_of_job_id`。legacy Job 从原任务首个失败阶段开始；带
  Campaign 的 AI 获客 Job 固定从 `collect` 开始，并在同一事务克隆 Campaign/关键词定义。
  新 Job 不复制 Evidence、Assessment、人工审计或原关键词运行计数。
- 进程重启时，遗留的 running Stage 变为 failed，Job 变为 `interrupted`。
- Job 与 Stage 认领、状态迁移和取消都使用 SQLite 条件更新，避免并发重复执行。

阶段 01 的 `usage_count` 以 Job 为一次 execution：同一 Job 的阶段重试仍为 1；新增视频、
相关视频和候选数从该 Job 持久化后的不同证据重新聚合。预算耗尽不会全量降级，只把
实际受截断的候选置为 `needs_more_evidence`。阶段 01 永远不能产生 `qualified` 或发送消息。
浏览器任务不会从 `about:blank` 等待模型猜测搜索入口，而是在同一秒级 deadline 内通过
`Platform.search_video_url()` 进入当前关键词视频搜索页并计为一次页面访问。模型
`max_steps` 只是视频探索终止原因，不会跳过 `direct_users` 辅助路径；如果所有关键词均以
该原因结束且两条路径都没有任何视频或用户证据，Collector 抛出稳定错误
`search_exhausted_without_evidence`，Stage/Job 按既有失败状态机落库，不能显示为成功空结果。
迭代模型偶发返回非法 JSON 时，该次调用记录为无效步骤，并继续下一次有界决策；它同时
消耗一个 step 和一次 LLM call，不扩张任务预算。认证、配置、熔断等路由错误仍立即失败。
单次 `network / timeout / upstream_server` 也按相同预算继续下一步；限流、认证、配置与
熔断不在此范围，避免无意义地连续请求。
页面预算由每关键词与 totals 的权威 `pages` 访问计数双重验证；证据中的 `video_url`、
`comment_url`、`author_url` 是可在同一页面读取的引用，不按 URL 个数冒充页面访问数。
视频、评论、作者视频、用户与总证据仍分别执行独立硬上限校验。

阶段 02 的四个状态为 `qualified / manual_review / need_enrichment / rejected`。人工复核可
直接通过或淘汰，不强制先补资料。AI 永远不能写 `qualified` 或 `rejected` 两个终态；模型
执行期间的人工结论通过 CAS 获得最终优先级。阶段 03 和阶段 04 都有独立的当前 Job、
当前平台、`qualified` 闸门，
避免从旧策略记录或其他任务绕过复核。

全局业务页面不直接使用上一次 Job 的阶段 JSON，也不把 AI 结论反写 `User`。统一投影按
“回复 → 已发送触达 → 最新 AI 资格 → legacy”的优先级生成展示状态；这保证当前 Pipeline
落下的候选、资格和证据能够被 Users、Lead、Dashboard、Reports 与关键词图表共同读取。
日报写入、无参数日报读取、Dashboard 与 Pipeline 概览的“今日”统计统一使用 UTC 00:00
日界线；显式日报日期仍按调用方传入值查询。数据库 UTC 时间戳不会在本地午夜到 UTC 午夜
之间被临时排除。单日日报与趋势响应都在数据库 Session 内转换为普通响应对象，不把过期
ORM 实例带出 Session。

## Provider 与并发

- **抖音**：每个运行任务创建独立 Playwright Client/Context，恢复所选账号 Cookie；
  平台总并发由 `douyin_max_concurrency` 控制，同一账号始终只运行一个任务。
- **TikTok**：必须通过指纹浏览器 Provider；当前只提供通用 Provider 接口和默认
  unavailable 占位实现，未接入任何具体厂商，也绝不回退到普通 Playwright。
- `auto` 账号模式会选择同平台已登录且空闲的可用账号；全部繁忙时任务保持 queued。
- `specified` 模式固定指定账号；平台不匹配、未登录或 Provider 不可用会返回稳定错误码。

`douyin_max_concurrency` 的合法范围是 1..20。配置保存为一个原子配置集，
并发值变化会返回 `restartRequired=true`；新值在服务重启后进入 Runtime。
