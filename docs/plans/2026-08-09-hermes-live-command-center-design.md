# Hermes 实时作战窗口与交互关卡设计

> 状态：已确认，进入分阶段实现  
> 日期：2026-08-09  
> 范围：Pipeline 单任务实时监控、10 秒自动决策关卡、阶段 01—02 业务结果与人工复核；
> 不包含完整浏览器视频直播和多租户隔离修复

## 1. 背景与问题

当前 `/pipeline` 已有四步 AI 获客创建器、统一 Job 历史、六阶段状态、取消和重试，但任务详情
仍以每 5 秒轮询 Job、逐阶段展开原始 JSON 为主。用户只能在任务结束后阅读技术结果，无法在
运行过程中理解 Hermes 正在搜索什么、为什么采取某个动作，也不能在关键节点接管决策。

系统虽然已有 `BROWSE_STEP / BROWSE_DONE` 事件和全局 SSE 入口，但它们目前存在四个限制：

- 事件只保存在进程内内存，重启后丢失；
- 浏览事件没有 `jobId`，多个任务运行时无法可靠归属；
- SSE 输出不是稳定的结构化 Job 事件契约，前端也没有消费它；
- Job 状态机只有运行、取消和重试，没有可审计的等待选择与恢复语义。

因此本设计不是给现有 JSON 增加动画，而是在唯一 Pipeline Job 状态机之上增加一套可持久化、
可追踪、可自动继续的交互层。

## 2. 产品目标

将 Pipeline 详情升级为一个嵌入式 Hermes 作战窗口：

- 运行时持续显示当前阶段、Hermes 动作、关键词、页面类型、探索计数、证据与异常；
- 需要用户判断时显示有限、明确、可执行的选项；
- 每个普通决策关卡有 10 秒服务端倒计时，未选择时执行预先展示的默认选项；
- 用户可以全程不操作，页面关闭也不会阻止任务走向终态；
- 用户主动选择“进入人工复核”后可以长时间停留在复核工作台，直到明确完成复核；
- 任务完成后同一窗口切换为行动回放和业务结果，不再把原始 JSON 作为主要内容；
- 所有人工选择、自动选择、失败和跳过均有审计记录。

“无人值守完成”表示任务不会因为普通选择无人点击而永久等待。登录失效、验证码、账号风控或
其他无法自动安全处理的故障可以以 `partial_failed / failed` 结束，但不能无限挂起，也不能绕过
平台验证或擅自扩大权限。

## 3. 方案选择

采用“真实作战窗口 + 持久化关卡”，不采用以下两种替代方案：

1. **纯前端换皮监视器**：只能把轮询结果做成动画，无法保证选择发生在动作之前；
2. **完整浏览器视频直播**：传输和存储成本高，容易暴露登录、验证码和页面隐私。

第一版不传输完整浏览器画面。窗口展示安全的运行遥测、页面类型、同平台公开 URL、截图哈希、
证据摘要和计数。以后若增加画面预览，必须单独设计脱敏、访问控制、过期和禁止持久化规则。

## 4. 总体架构

```text
PipelineJobRunner / PipelineService / Hermes BrowseAgent
                         │
                         ├─ JobEventRecorder ── pipeline_job_events
                         │                         │
                         │                         ├─ 历史回放 API
                         │                         └─ Job 级实时流
                         │
                         └─ DecisionGateService ─ pipeline_decision_checkpoints
                                                   │
                                      ┌────────────┴────────────┐
                                      │                         │
                                用户 10 秒内选择           超时执行默认选项
                                      │                         │
                                      └────────────┬────────────┘
                                                   ▼
                                            恢复同一 Job

Vue Pipeline 页面
  ├─ HermesMissionMonitor      实时状态、事件流、倒计时和选择
  ├─ StageDiscoveryResult      阶段 01 业务结果
  ├─ StageQualificationResult  阶段 02 四状态结果
  └─ CandidateReviewDrawer     候选证据与人工动作
```

`PipelineJobService` 仍是唯一任务状态来源；Hermes 不创建第二套任务队列。决策关卡只暂停或恢复
当前 Job，不能自行创建任务、改变账号或绕过资格闸门。

## 5. 持久化模型

### 5.1 `pipeline_job_events`

每条事件至少保存：

- 自增 `sequence`，用于稳定增量读取；
- `job_id`、`stage`、`event_type`、`level`；
- 版本化、白名单化的 `payload_json`；
- `created_at`。

事件 payload 只保存 UI 所需的安全字段，例如动作类型、关键词、页面类型、计数、公开来源引用、
脱敏证据摘要、预算使用量和稳定错误码。禁止保存 Cookie、Token、API Key、浏览器 Profile 路径、
完整页面正文、模型原始 Prompt/Response 和任意认证 Header。

高频浏览事件采用有界写入和合并策略。连续滚动、等待等动作可以保留最近事件并聚合计数，避免
一个长任务无限膨胀数据库。

### 5.2 `pipeline_decision_checkpoints`

每个关卡至少保存：

- `id`、`job_id`、`stage`、`kind`、`version`；
- 服务端注册的 `option_keys` 与 `default_option_key`；
- 安全的上下文摘要；
- `status`：`pending / resolved / expired / cancelled`；
- `deadline_at`、`resolved_at`；
- `resolution_key`、`resolution_source`：`human / timeout / system`；
- 操作人标识和可选原因。

客户端只能提交当前关卡公开的 `optionKey + version`，不能提交自由动作或替换默认选项。数据库
使用条件更新保证“用户最后一秒点击”和“服务端超时”只有一个结果获胜；响应始终返回权威结果。

## 6. 状态机

Job 增加 `waiting_decision`，Stage 增加 `waiting_decision`：

```text
queued → running → waiting_decision → running → terminal
                    │       │
                    │       └─ 10 秒超时：执行 default，再恢复
                    ├─ 人工选择：执行 option，再恢复
                    └─ 取消任务：checkpoint cancelled，Job cancelled
```

普通关卡默认 10 秒。倒计时以 `deadlineAt` 服务端时间为准，前端倒计时只负责显示，不能决定
超时结果。页面刷新、切换任务或关闭页面不改变服务端计时。

当用户在 10 秒内主动选择“进入复核工作台”时，这个选择本身立即解决普通关卡，并创建一个
显式人工会话。人工会话没有自动替用户通过/淘汰的倒计时；用户点击“完成本轮复核”后继续任务。
任务取消仍能中断人工会话。这样既保证全程不选择可以完成，也允许主动接管后认真处理。

服务进程异常重启仍沿用当前 `interrupted` 语义。第一版保证页面关闭不影响自动继续，但不伪称
能在进程崩溃后原地恢复浏览器动作；重启后的 Job 可通过现有重试创建新执行。

## 7. 第一版关卡注册表

### 7.1 阶段 01：证据不足

仅在候选为零、`needs_more_evidence` 比例过高或 Collector 明确返回预算截断时出现。选项必须
按实际剩余预算动态生成：

- `deepen_with_remaining_budget`：仅在原任务预算尚有余量时显示；
- `continue_with_current_evidence`：保留证据并进入筛选；
- `skip_remaining_pipeline`：跳过依赖候选的后续阶段；
- `cancel_job`：结束任务。

默认：`continue_with_current_evidence`。不得为了提供“深挖”按钮突破冻结的任务预算。

### 7.2 阶段 02：人工复核入口

- `open_review_workbench`：进入候选工作台，允许逐条通过、淘汰、补资料和改标签；
- `request_batch_enrichment`：对当前 `need_enrichment` 队列执行有界补充；
- `continue_with_qualified_only`：只让当前任务中已人工确认的 `qualified` 用户进入阶段 03。

默认：`continue_with_qualified_only`。AI 仍不能自动写 `qualified / rejected`；如果没有任何人工
合格用户，阶段 03—04 以零目标安全完成。

### 7.3 阶段 04：触达前确认

- `execute_approved_outreach`：只对当前 Job、当前平台、人工 `qualified` 且策略再次校验通过的
  用户执行；
- `open_review_workbench`：返回复核工作台；
- `skip_outreach`：跳过本轮触达并继续报告。

若创建任务时明确包含 `outreach`，默认 `execute_approved_outreach`；否则不会创建这个关卡，
也不会触达。默认行为不能扩大建单时授权的阶段范围。

### 7.4 可重试故障与账号阻断

- 普通可重试网络故障：`retry_once / skip_stage / stop_job`，默认按稳定错误分类执行一次有界重试，
  再失败则跳过并形成 `partial_failed`；
- 登录失效、验证码、账号风控：`open_account_recovery / skip_stage / stop_job`，默认安全跳过并记录
  阻断，不自动处理验证码、不绕过登录验证。

## 8. API 契约

新增受认证、严格绑定 `jobId` 的接口：

```text
GET  /api/pipeline/jobs/{jobId}/live
GET  /api/pipeline/jobs/{jobId}/events?afterSequence=&limit=
GET  /api/pipeline/jobs/{jobId}/events/stream?afterSequence=
GET  /api/pipeline/jobs/{jobId}/checkpoints/active
POST /api/pipeline/jobs/{jobId}/checkpoints/{checkpointId}/resolve
POST /api/pipeline/jobs/{jobId}/checkpoints/{checkpointId}/review-complete
```

`live` 返回 Job/Stage 摘要、最近安全事件、累计指标和当前关卡，供页面首次加载。增量事件使用
单调 `sequence` 去重。实时流采用携带现有 Bearer Token 的 `fetch` 流式读取；断线时从最后序号
恢复，并退化为短轮询，不使用 URL Query 传递 JWT。

解决关卡请求示例：

```json
{
  "optionKey": "continue_with_qualified_only",
  "version": 1,
  "reason": ""
}
```

成功响应返回实际 resolution。关卡不存在返回 404；版本或终态竞争返回 409，并附带不含敏感数据
的当前权威状态；非法选项返回 422。

## 9. 作战窗口交互设计

### 9.1 位置与层级

作战窗口嵌入当前任务详情顶部，运行时保持可见，但不遮挡历史和页面导航：

- 桌面端高度约 420—520px，内部包含阶段轨道、当前行动、实时日志、指标和决策卡；
- 可收起为一行任务状态条，重新展开不会丢事件序号和倒计时；
- 移动端全宽单列，决策按钮最小高度 44px，不使用悬浮层遮挡底部导航；
- 任务完成后原位置切换为行动回放和结果摘要。

### 9.2 视觉系统

保持现有产品的白色业务卡片、洋红品牌色和等宽技术字体。作战窗口使用克制的深色监控面板：

- 绿色：运行正常与已完成；
- 琥珀色：等待选择和即将执行默认项；
- 红色：风控、失败和不可逆风险；
- 洋红色：当前 Hermes 焦点和用户可操作主按钮。

只为当前活动、倒计时和新事件使用轻量动效；支持 `prefers-reduced-motion`。不使用虚构数据、
老虎机式数值跳动、过度霓虹或持续闪烁。

### 9.3 信息结构

```text
标题栏：HERMES LIVE / Job / 状态 / 运行时长 / 收起
阶段轨道：01—06 当前进度与阻断点
行动区：当前目标、关键词、页面类型、动作和原因摘要
指标区：视频、评论、候选、证据、LLM 调用和剩余预算
事件带：最近安全事件，可滚动查看完整回放
决策卡：问题、上下文、选项、默认项和服务端倒计时
```

ARIA live 只播报阶段变化、关卡出现和关卡解决，不播报每条高频滚动事件。键盘焦点进入新关卡，
关卡解决后回到监视器标题；颜色不是唯一状态表达。

## 10. 业务结果与人工工作台

实时窗口下面使用既有阶段 01—02 API，替换默认 JSON：

- `StageDiscoveryResult`：关键词表现、视频/评论覆盖、候选发现状态、来源类型和预算截断；
- `StageQualificationResult`：四状态漏斗、双评分均值、人工待办和筛选异常；
- `CandidateReviewDrawer`：公开账号资料、来源链、原始证据、AI 建议、双评分、多标签、缺失字段、
  通过/淘汰/补资料/改标签和审计记录。

原始阶段 result 只在“技术诊断”折叠区显示。阶段指标必须可以下钻到当前 Job 的候选与证据，
不得使用全局用户状态冒充任务结果。

## 11. 并发、取消与失败边界

- 同一 Job 同时最多一个 active checkpoint；创建使用唯一约束；
- resolve、timeout、cancel 使用 CAS，只有一个终态结果；
- Job 进入 `cancelling` 时取消 active checkpoint 并唤醒等待中的 Runner；
- 关卡等待期间保留现有账号与浏览器租约，普通等待固定 10 秒；人工工作台长暂停时释放浏览器
  资源，并在恢复前重新校验账号、Provider 和登录态；
- 实时流断开不影响 Job；前端恢复时从持久化 sequence 补齐事件；
- 任何选项执行失败都形成新事件和稳定错误，不把已 resolved 的选择伪装成成功动作。

## 12. 分阶段验收

### Gate H4-A：持久化事件

- 所有 Pipeline/Hermes 事件严格绑定 `jobId`；
- 事件分页、增量序号、认证和敏感字段拒绝测试通过；
- 多任务并发不会串流，重连不会重复或漏掉已提交事件。

### Gate H4-B：10 秒决策关卡

- 人工选择、超时默认、最后一秒竞争、取消和非法选项均有测试；
- 页面关闭时任务仍在 10 秒后自动继续；
- 无人选择的任务能进入明确终态；AI 不越过人工资格闸门。

### Gate H4-C：嵌入式作战窗口

- 真实 Job 事件驱动，不使用演示计时器或 Mock 随机数据；
- 倒计时以服务端 deadline 为准，断线恢复后显示权威 resolution；
- 桌面与 390px 页面可用，键盘、ARIA live 和 reduced-motion 通过专项检查。

### Gate H4-D：业务结果与复核工作台

- 阶段 01—02 默认显示业务看板，不显示原始 JSON；
- 候选可以直接通过、淘汰、请求补资料和改标签；
- 操作后刷新权威候选详情与审计，未确认候选不能进入触达。

### Gate H4-E：真实全链路验收

- 使用真实后端创建一套抖音获客 Job，观察实时事件和至少一个自动关卡；
- 验证全程不选择仍完成、人工选择可接管、取消可解除等待；
- 完整后端、前端组件、类型检查、构建、Smoke 和浏览器桌面/移动验收通过；
- 更新 WIKI 索引、Pipeline 和 API/UI 文档后，创建备份 tag 并推送镜像仓库。
