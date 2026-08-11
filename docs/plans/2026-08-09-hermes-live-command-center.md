# Hermes 实时作战窗口与交互关卡实施计划

> 日期：2026-08-09  
> 设计依据：[`2026-08-09-hermes-live-command-center-design.md`](2026-08-09-hermes-live-command-center-design.md)  
> 开发方式：Gate 文档约束 + TDD + 小步提交 + 子代理实现/审查

## 0. 交付策略

本功能分五个可独立验收的 Gate。每个 Gate 都遵循：先写失败测试、确认 RED、最小实现、确认
GREEN、只读审查、修复 Important/Critical、更新文档并提交。后一个 Gate 不以未验证的前一个
Gate 为基础继续扩张。

```text
H4-A 持久化事件
  → H4-B 决策状态机与 Runner 接线
    → H4-C 受认证实时 API
      → H4-D 作战窗口 + 阶段结果/复核工作台
        → H4-E 真实浏览器验收、文档、备份与推送
```

所有测试使用临时 SQLite 和注入时钟；不得让测试真实等待 10 秒。所有事件和关卡 payload 都用
固定 DTO/白名单构造，不能把任意内部 dict 直接持久化或返回前端。

## 1. Gate H4-A：持久化 Job 事件

### Task 1：事件与关卡数据模型

**文件**

- 修改：`tiktok_bot_core/models/entities.py`
- 修改：`tiktok_bot_core/models/__init__.py`
- 新增：`tiktok_bot_core/storage/pipeline_live_store.py`
- 新增测试：`tests/test_pipeline_live_store.py`

**RED**

1. 写测试创建两个 Job，各追加多条事件；断言 sequence 单调且分页严格按 `job_id` 隔离。
2. 写测试覆盖 `after_sequence`、limit 边界、空结果和未知 Job。
3. 写测试覆盖 event payload 白名单：Cookie、Token、Authorization、API Key、Profile 路径、
   Prompt/Response、嵌套凭据全部拒绝，错误不回显敏感值。
4. 写测试创建 active checkpoint，断言同一 Job 只能有一个 `pending` 关卡，终态后才可创建下一个。
5. 运行并确认失败：

```powershell
python -X utf8 -m pytest tests/test_pipeline_live_store.py -q
```

**GREEN**

1. 新增 `PipelineJobEvent`：`sequence/job_id/stage/event_type/level/payload_json/created_at`。
2. 新增 `PipelineDecisionCheckpoint`：设计文档中的版本、选项、默认项、deadline、resolution 和
   操作人字段。
3. Store 提供 append/list/count、create/get-active/resolve/cancel checkpoint 的原子方法。
4. 事件 payload 通过专用 DTO 或逐类 builder 生成；拒绝未定义字段和凭据类字段。
5. `Base.metadata.create_all()` 自动创建新表；不修改已有列，不做破坏性迁移。
6. 重跑专项测试、`py_compile` 和 `git diff --check`。

### Task 2：事件记录服务与有界高频策略

**文件**

- 新增：`tiktok_bot_core/services/pipeline_live_events.py`
- 修改测试：`tests/test_pipeline_live_store.py`
- 新增测试：`tests/test_pipeline_live_events.py`

**RED**

1. 验证 lifecycle、stage、browse、decision、candidate 五类事件都强制携带 jobId。
2. 验证连续 `scroll/wait` 高频事件按规则合并或受上限约束，关键 extract/error/done 不丢失。
3. 验证安全错误只记录稳定错误码和公开消息，不记录异常 repr、请求正文或密钥。

**GREEN**

1. 实现 `PipelineLiveEventRecorder`，公开命名方法而不是通用任意 payload 入口。
2. 实现 per-job 有界保留和高频合并；数据库写入失败只能记录本地日志，不能改变业务结果。
3. 每个方法返回已提交的 sequence，供实时连接恢复。
4. 运行：

```powershell
python -X utf8 -m pytest tests/test_pipeline_live_store.py tests/test_pipeline_live_events.py -q
```

**Gate H4-A 验收提交**

```powershell
git add -- tiktok_bot_core/models tiktok_bot_core/storage/pipeline_live_store.py `
  tiktok_bot_core/services/pipeline_live_events.py tests/test_pipeline_live_store.py `
  tests/test_pipeline_live_events.py docs
git commit -m "feat: persist job-scoped pipeline live events"
```

## 2. Gate H4-B：10 秒决策状态机与 Runner 接线

### Task 3：共享状态常量和 Store 状态迁移

**文件**

- 修改：`tiktok_bot_core/models/pipeline_states.py`
- 修改：`tiktok_bot_core/storage/pipeline_job_store.py`
- 修改：`tests/test_pipeline_jobs.py`

**RED**

1. Job `running → waiting_decision → running` 合法，终态不能进入等待。
2. Stage `running → waiting_decision → running` 合法，等待态不能直接伪装成功。
3. waiting Job 取消后 checkpoint 被取消，Stage/Job 最终变为 cancelled。
4. 启动恢复将 running、cancelling、waiting_decision 全部按既有边界变为 interrupted，并关闭
   pending checkpoint。

**GREEN**

1. 增加 Job/Stage `waiting_decision` 常量和转换表。
2. Store 增加 `pause_for_decision/resume_from_decision` CAS 方法。
3. `request_cancel` 接受 waiting Job；恢复和 pending stage 清理覆盖新状态。
4. API/前端尚未接线前，旧状态仍保持完全兼容。

### Task 4：DecisionGateService

**文件**

- 新增：`tiktok_bot_core/services/pipeline_decisions.py`
- 新增测试：`tests/test_pipeline_decisions.py`

**RED**

1. 人工在 deadline 前解决关卡，Job 恢复 running，source=human。
2. 无人选择时由注入时钟触发默认项，source=timeout，不依赖浏览器页面。
3. 人工点击与 timeout 并发时只有一个 CAS 成功，调用方读取同一个权威 resolution。
4. 非公开 option、错误 version、其他 Job checkpoint、重复 resolve 全部拒绝。
5. 任务取消能唤醒等待协程；普通关卡不泄漏后台 Task。

**GREEN**

1. 以固定 `CheckpointDefinition` 注册 kind、可执行 options、默认项和安全 context builder。
2. `await_decision` 创建持久化记录、切换状态、等待通知或 deadline、CAS 解决并恢复 Job。
3. 默认 10 秒，通过构造参数注入 timeout/clock，测试使用毫秒级假时钟。
4. 显式人工复核会话与普通 10 秒关卡分开；普通超时永远不会自动写候选终态。

### Task 5：Runner 关卡策略和动作执行

**文件**

- 修改：`tiktok_bot_core/services/pipeline_jobs.py`
- 新增：`tiktok_bot_core/services/pipeline_decision_policy.py`
- 修改：`tiktok_bot_core/services/pipeline.py`
- 修改测试：`tests/test_pipeline_jobs.py`
- 新增测试：`tests/test_pipeline_decision_policy.py`

**RED**

1. collect 结果证据不足时出现关卡；默认保留当前证据并继续。
2. filter 完成后出现复核入口；默认只使用当前 Job 人工 qualified 用户继续。
3. outreach 开始前出现确认；建单包含 outreach 时默认只执行已人工合格且策略合法的目标。
4. 无人选择全程可以进入 succeeded/partial_failed/failed/cancelled 之一，不永久 waiting。
5. `skip_remaining_pipeline/cancel_job/skip_outreach` 对后续阶段结果和状态有确定行为。
6. `deepen_with_remaining_budget` 只在权威预算仍有余量时出现，绝不突破 Campaign 快照。

**GREEN**

1. `PipelineDecisionPolicy` 只基于持久化 Campaign、Stage result、预算和资格统计产生关卡定义。
2. Runner 在阶段完成落库前和 outreach 执行前调用 Gate；选择结果由固定 action handler 执行。
3. 默认路径不修改 `qualified/rejected`；零人工合格用户时策略与触达安全返回零。
4. waiting 时普通 10 秒继续持有短租约；显式打开复核工作台时释放浏览器资源，恢复前重新预检。
5. 取消和错误必须解除 waiter，不能遗留账号租约。

### Task 6：Hermes/Browse 事件绑定 Job

**文件**

- 修改：`tiktok_bot_core/services/browse_agent.py`
- 修改：`tiktok_bot_core/services/acquisition_agents.py`（包含现有 `KeywordCollector` / Hermes 注入链）
- 修改：`tiktok_bot_core/services/pipeline.py`
- 修改测试：`tests/test_browse_agent.py`
- 修改测试：`tests/test_keyword_collector_username.py`
- 修改测试：`tests/test_pipeline.py`

**RED**

1. Pipeline 路径调用 BrowseAgent 时每个 browse.step/done 都携带真实 job_id 与 stage=collect。
2. CLI 独立 browse 未提供 Job 时不得写入 Pipeline Job 事件表。
3. 并行两个 Job 的 Browse 事件不能串流。
4. 事件记录失败不改变 BrowseResult 或突破页面/LLM/时间预算。

**GREEN**

1. 通过显式 `job_id/event_recorder` 参数传递上下文，不使用全局 current-job 变量。
2. 保留现有内存 EventBus 兼容订阅；持久化 Job 事件作为业务 UI 权威源。
3. rationale、URL 和 evidence preview 进入专用安全 builder，截图仍只记录 hash。

**Gate H4-B 验收命令**

```powershell
python -X utf8 -m pytest tests/test_pipeline_jobs.py tests/test_pipeline_decisions.py `
  tests/test_pipeline_decision_policy.py tests/test_browse_agent.py `
  tests/test_keyword_collector_username.py tests/test_pipeline.py -q
```

通过后更新 `docs/wiki/05-Pipeline.md` 并提交：

```powershell
git commit -m "feat: add automatic Hermes decision checkpoints"
```

## 3. Gate H4-C：实时与决策 API

### Task 7：严格 API DTO 与端点

**文件**

- 修改：`tiktok_bot_api/main.py`
- 新增测试：`tests/test_pipeline_live_api.py`
- 修改测试：`tests/test_pipeline_api.py`（若实际文件名不同，使用现有 Pipeline API 测试文件）

**RED**

1. 未认证访问 live/events/stream/checkpoint 全部 401。
2. `GET live` 返回 job、stage、metrics、recentEvents、activeCheckpoint 的 camelCase 安全 DTO。
3. `afterSequence/limit` 边界、未知 Job、checkpoint/job 不匹配和非法 option 状态码符合设计。
4. resolve timeout 竞争返回实际权威 resolution；错误不回显 payload、异常或敏感字段。
5. 两个 Job 的 history/stream 严格隔离。

**GREEN**

1. 增加 `PipelineLiveResponse/EventResponse/CheckpointResponse/ResolveRequest` 严格模型。
2. history 使用稳定 sequence；live 首屏在单个数据库 Session 内序列化为普通 dict。
3. SSE 每条使用 JSON `data:`，支持 `Last-Event-ID/afterSequence`；连接断开只释放订阅资源。
4. resolve 调用同一 `DecisionGateService`，API 不自行改 Job 状态。

### Task 8：前端原子客户端和领域类型

**文件**

- 修改：`tiktok_bot_console/ui/src/types/pipeline.ts`
- 修改：`tiktok_bot_console/ui/src/api/index.ts`
- 新增测试：`tiktok_bot_console/ui/src/api/pipelineLive.spec.ts`

**RED/GREEN**

1. 先测试 live/events/resolve URL 编码、参数和请求体。
2. 增加 `waiting_decision` 类型、事件、指标、checkpoint、resolution DTO。
3. 增加 Bearer fetch stream helper；断线从 lastSequence 继续，失败降级为 1 秒 history polling。
4. abort 必须在切换 Job、组件卸载和终态时释放 reader/timer。
5. 不把 token 放入 URL、日志或错误提示。

**Gate H4-C 验收命令**

```powershell
python -X utf8 -m pytest tests/test_pipeline_live_api.py tests/test_acquisition_api.py -q
Set-Location tiktok_bot_console/ui
npx vitest run src/api/pipelineLive.spec.ts
npm run type-check
```

更新 `docs/wiki/06-CLI-API-UI.md` 并提交：

```powershell
git commit -m "feat: expose authenticated pipeline live controls"
```

## 4. Gate H4-D：作战窗口与业务结果工作台

### Task 9：HermesMissionMonitor 组件

**文件**

- 新增：`tiktok_bot_console/ui/src/components/HermesMissionMonitor.vue`
- 新增：`tiktok_bot_console/ui/src/components/HermesMissionMonitor.spec.ts`
- 修改：`tiktok_bot_console/ui/src/i18n/zh-CN.ts`
- 修改：`tiktok_bot_console/ui/src/i18n/en-US.ts`

**RED**

1. running/waiting/terminal/empty/error/断线恢复状态都可渲染。
2. 当前阶段、行动、关键词、预算和事件来自传入真实 DTO，不使用随机计时器或演示数据。
3. 决策卡显示服务端 deadline、默认项、10 秒倒计时；点击一次锁定并处理 409 权威结果。
4. 组件收起/展开、切换 Job、卸载时正确终止流。
5. ARIA live、键盘焦点、44px 操作目标和 reduced-motion 有回归断言。

**GREEN**

1. 按设计文档实现深色嵌入式监控面板，复用现有 CSS tokens。
2. 使用语义图标、文本和颜色共同表达状态；不显示完整浏览器截图。
3. 倒计时归零后等待服务端 resolution，不在前端自行假定默认已执行。
4. 任务结束后切换为回放模式，仍能查看持久化事件。

### Task 10：阶段 01—02 业务结果组件

**文件**

- 新增：`tiktok_bot_console/ui/src/components/StageDiscoveryResult.vue`
- 新增：`tiktok_bot_console/ui/src/components/StageQualificationResult.vue`
- 新增：`tiktok_bot_console/ui/src/components/StageResults.spec.ts`

**RED/GREEN**

1. 阶段 01 显示关键词、候选、证据、来源和发现状态；指标可触发候选筛选。
2. 阶段 02 显示四状态、双评分和人工待办；状态卡可下钻。
3. 空、加载、失败、预算截断和 legacy Job 都有明确状态。
4. 原始 JSON 不在组件主界面出现。

### Task 11：候选复核抽屉

**文件**

- 新增：`tiktok_bot_console/ui/src/components/CandidateReviewDrawer.vue`
- 新增：`tiktok_bot_console/ui/src/components/CandidateReviewDrawer.spec.ts`

**RED/GREEN**

1. 加载候选详情、分页证据和审计；显示 profile URL、来源链、双评分、多标签与缺失字段。
2. `manual_review` 可直接通过、淘汰或补资料；`need_enrichment` 可完成补资料、通过或淘汰。
3. 每个 mutation pending 时防重复提交；成功后重新读取候选详情，因为 action response 不含完整证据。
4. 终态动作禁用；错误保留当前抽屉和用户上下文。
5. 关闭、切候选和卸载使用 generation/abort guard 防止串用户。

### Task 12：Pipeline 页面集成

**文件**

- 修改：`tiktok_bot_console/ui/src/views/Pipeline.vue`
- 修改：`tiktok_bot_console/ui/src/views/PipelineAcquisition.spec.ts`
- 修改：`tiktok_bot_console/ui/scripts/smoke.mjs`

**RED/GREEN**

1. 选中 Job 后，详情顶部只挂载一个 monitor；轮询和 stream 不重复创建。
2. acquisition Job 使用阶段业务组件；legacy Job 使用兼容摘要。
3. 原始 JSON 移入单一“技术诊断”折叠区，默认关闭。
4. 指标筛选、候选抽屉、关卡 `open_review_workbench` 使用同一 Job 上下文。
5. 历史、取消、重试、创建器和移动导航回归不退化。

**Gate H4-D 验收命令**

```powershell
Set-Location tiktok_bot_console/ui
npm test -- --run src/components/HermesMissionMonitor.spec.ts `
  src/components/StageResults.spec.ts `
  src/components/CandidateReviewDrawer.spec.ts `
  src/views/PipelineAcquisition.spec.ts
npm run type-check
npm run build
node scripts/smoke.mjs
```

通过后更新 `docs/wiki/05-Pipeline.md`、`docs/wiki/06-CLI-API-UI.md`、`docs/wiki/00-索引.md`
并提交：

```powershell
git commit -m "feat: add Hermes live acquisition command center"
```

## 5. Gate H4-E：真实全链路与交付

### Task 13：后端完整回归

```powershell
python -X utf8 -m pytest -q
python -X utf8 -m compileall -q tiktok_bot_core tiktok_bot_api
git diff --check
```

### Task 14：真实浏览器验收

1. 使用启动脚本确认 API 8000、UI 5173 健康。
2. 登录真实后端，在 `/pipeline` 创建包含 collect/filter/strategy/outreach/report/iterate 的抖音任务。
3. 验证：
   - monitor 显示真实 Job 事件而非 Mock；
   - 切换页面再返回可从 sequence 恢复；
   - 至少一个关卡无人选择，10 秒后显示 `timeout` resolution 并继续；
   - 至少一个关卡人工选择，动作审计和后续阶段符合选择；
   - 阶段 01—02 业务结果与候选证据能下钻；
   - 取消 waiting Job 不遗留关卡或账号租约；
   - 桌面和 390×844 无横向溢出，底部导航/退出仍可触达。

4. 不在验收中自动发送未经当前任务明确授权的外部评论或私信。若任务包含 outreach，只允许当前
   Job 人工 `qualified` 且策略安全校验通过的对象。

### Task 15：最终审查、提交与备份

1. 独立只读审查 Critical/Important；修复后重跑受影响测试和全量。
2. WIKI 顶部日期和版本与代码一致；索引包含设计与实施计划。
3. 确认 `.env`、数据库、截图、Cookie、Profile、日志和 API Key 均未被跟踪。
4. 提交、打备份 tag、推送 `mirror/master` 和 tag：

```powershell
git status --short
git commit -m "feat: deliver Hermes live decision workflow"
git tag "backup/hermes-live-command-center-20260809"
git push mirror master
git push mirror "backup/hermes-live-command-center-20260809"
```

## 6. 完成定义

只有同时满足以下条件才能宣称完成：

- 实时信息来自持久化 Job 事件，多个任务不串流；
- 10 秒默认由服务端执行，关闭页面不阻塞任务；
- AI 未自动写人工资格终态，阶段 04 仍有当前 Job/platform/qualified 三重闸门；
- 阶段 01—02 默认是业务界面，原始 JSON 仅为技术诊断；
- 候选人工动作与审计可在 Pipeline 内完成；
- 后端全量、前端组件、类型检查、构建、Smoke、真实浏览器桌面/移动验收全部通过；
- 文档、提交、备份 tag 和镜像仓库同步完成。
