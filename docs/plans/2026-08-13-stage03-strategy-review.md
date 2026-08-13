# 阶段 03 策略审核闭环实施计划

> **For implementer:** 全程使用 TDD。先写失败测试并确认 RED，再写最小实现。

**Goal:** 将阶段 03 的安全策略草案升级为 Job-scoped、带版本 CAS 的人工审核闭环，并让阶段 04 只消费人工批准策略。

**Architecture:** 复用现有 `Strategy`、`PipelineJobUser`、`StrategyResult`、`DecisionGateService` 和 Pipeline 页面组合方式。数据层先建立 draft/approved/rejected 状态及短事务服务；API 和 Runner 只调用领域服务；UI 通过独立阶段卡和审核抽屉消费严格 DTO。

**Tech Stack:** Python 3.11、SQLAlchemy 2、FastAPI、SQLite、Pytest、Vue 3、TypeScript、Axios、Vitest。

---

## Gate 03-A：数据与领域服务

### Task 1：策略审核模型与旧库迁移

**Files:**
- Modify: `tiktok_bot_core/models/entities.py`
- Modify: `tiktok_bot_core/storage/database.py`
- Test: `tests/test_strategy_review_store.py`

**RED:**

1. 写测试：新 Strategy 默认 `draft/review_version=0`；非法状态和负版本被数据库拒绝；旧库补列后已有策略保持 `draft`。
2. 运行：`python -X utf8 -m pytest tests/test_strategy_review_store.py -q`
3. 预期：字段不存在或迁移断言失败。

**GREEN:**

1. 增加 `review_status/review_version/reviewed_at/reviewed_by/review_reason/updated_at`。
2. 为新库增加 CheckConstraint；旧 SQLite 使用幂等 `ALTER TABLE ADD COLUMN`，已有行回填 draft/0。
3. 重跑专项并确认通过。

### Task 2：Job-scoped Store 与审核 CAS

**Files:**
- Create: `tiktok_bot_core/storage/strategy_review_store.py`
- Modify: `tiktok_bot_core/storage/sqlite_store.py`
- Test: `tests/test_strategy_review_store.py`

**RED:**

1. 覆盖分页、reviewStatus、Job/platform/qualified 隔离、版本冲突、编辑回 draft、approve/reject、批量批准逐条验证。
2. 两个真实 Session 并发批准，只有一个 CAS 胜者；输家读取权威当前记录。
3. 编辑内容必须先通过 `StrategyResult` 安全校验；非法链接、联系方式、空双模板均拒绝且不改版本。

**GREEN:**

1. 实现严格输入 dataclass/结果快照和短事务 Store。
2. 任何 mutation 使用 `UPDATE ... WHERE review_version=:expected`；不 `expire_all`，只同步目标 identity。
3. `add_strategy` 对 AI 重新生成时重置为 draft 并递增版本，历史人工批准不会被静默覆盖。

### Task 3：策略审核领域服务与审计

**Files:**
- Create: `tiktok_bot_core/services/strategy_review.py`
- Modify: `tiktok_bot_core/models/entities.py`
- Modify: `tiktok_bot_core/storage/database.py`
- Test: `tests/test_strategy_review_service.py`

**RED:**

1. 未知 Job/策略、跨 Job、平台不匹配、非 qualified、legacy job_id=null、非法 operator/reason 稳定失败。
2. 审计记录只含 action、前后状态、版本、操作者和受限原因，不保存凭据或异常正文。
3. 批量批准返回 total/approved/skipped/conflicted，任一非法策略不让其他合法条目回滚。

**GREEN:**

1. 增加 `StrategyReviewAudit` 或等价独立表。
2. 实现 `StrategyReviewService`，API/Runner 以后只能通过它做审核 mutation。
3. 错误码使用固定注册表，错误消息不拼接输入正文。

## Gate 03-B：API 与 Runner 安全关卡

### Task 4：受认证策略审核 API

**Files:**
- Modify: `tiktok_bot_api/main.py`
- Test: `tests/test_strategy_review_api.py`

**RED:**

1. 覆盖设计中的 7 个端点，匿名 401、严格 camelCase、Job 隔离、分页边界和未知资源。
2. PATCH/approve/reject/batch 使用 `reviewVersion`；409 返回安全权威摘要且不回显输入或异常。
3. Stage03 摘要计数来自同一 Session：qualified/drafts/approved/rejected/missingStrategies。

**GREEN:**

1. Pydantic 请求使用 `extra=forbid` 和长度/枚举边界。
2. DTO 仅投影公开用户字段、双评分和安全策略字段。
3. 所有 mutation 复用 `StrategyReviewService`。

### Task 5：阶段 03 决策定义与 10 秒安全默认

**Files:**
- Modify: `tiktok_bot_core/services/pipeline_decisions.py`
- Modify: `tiktok_bot_core/services/pipeline_decision_policy.py`
- Test: `tests/test_pipeline_decisions.py`
- Test: `tests/test_pipeline_decision_policy.py`

**RED:**

1. 仅 AI Campaign + requestedStages 含 outreach + 当前存在 draft 时创建 `strategy_review`。
2. 动态 options 只公开真实可执行项；默认固定 `skip_outreach`。
3. 10 秒无人选择权威 resolution 为 timeout/skip_outreach，零策略或未请求 outreach 不建关卡。

**GREEN:**

1. 注册固定 kind/options/context 白名单。
2. Policy 只读持久化摘要，不根据旧 Stage JSON 猜测。
3. 复用现有 CAS、等待器、异常清理和事件记录。

### Task 6：Runner 策略审核与阶段 04 approved 闸门

**Files:**
- Modify: `tiktok_bot_core/services/pipeline_jobs.py`
- Modify: `tiktok_bot_core/services/pipeline.py`
- Modify: `tiktok_bot_core/services/pipeline_decision_policy.py`
- Test: `tests/test_pipeline_jobs.py`
- Test: `tests/test_qualification_workflow.py`

**RED:**

1. skip_outreach 保留草案、把 outreach 标为 skipped、继续 report/iterate。
2. approve_all_safe_drafts 只批准当前合法草案；open workbench 前释放资源，人工完成后完整 preflight/reacquire。
3. 阶段 04 只查询 approved；批准后资格/平台/模板变化会跳过；取消时不触达且清 checkpoint/lease/browser。

**GREEN:**

1. 在 strategy→outreach 边界接入关卡，不复用旧自动执行默认。
2. 为 skip remaining/current stage 复用现有原子状态方法和 lifecycle 事件。
3. `_run_campaign_outreach` JOIN 当前 Job Strategy 并要求 review_status=approved，再执行既有四重闸门。

## Gate 03-C：前端业务界面

### Task 7：前端策略审核原子客户端与类型

**Files:**
- Modify: `tiktok_bot_console/ui/src/types/pipeline.ts`
- Modify: `tiktok_bot_console/ui/src/api/index.ts`
- Create: `tiktok_bot_console/ui/src/api/strategyReview.spec.ts`

**RED:** 覆盖 7 个端点 URL/参数/请求体、AbortSignal、409 安全错误和 camelCase DTO。

**GREEN:** 实现类型与客户端，不在组件里拼 URL 或直接使用 Axios。

### Task 8：阶段 03 业务结果卡

**Files:**
- Create: `tiktok_bot_console/ui/src/components/StageStrategyResult.vue`
- Create: `tiktok_bot_console/ui/src/components/StageStrategyResult.spec.ts`
- Modify: `tiktok_bot_console/ui/src/i18n/zh-CN.ts`
- Modify: `tiktok_bot_console/ui/src/i18n/en-US.ts`

**RED:** 运行中/空/失败/legacy/正常五态；显示 qualified/draft/approved/rejected/missing；按钮发出精确当前 Job 打开事件。

**GREEN:** 复用阶段 01/02 design tokens、44px 目标、移动两列/单列布局，不默认显示 JSON。

### Task 9：策略审核工作台

**Files:**
- Create: `tiktok_bot_console/ui/src/components/StrategyReviewDrawer.vue`
- Create: `tiktok_bot_console/ui/src/components/StrategyReviewDrawer.spec.ts`
- Modify: `tiktok_bot_console/ui/src/i18n/zh-CN.ts`
- Modify: `tiktok_bot_console/ui/src/i18n/en-US.ts`

**RED:**

1. Job-scoped 队列/详情/编辑/批准/退回/批量批准；mutation 防双击、权威重读失败不报成功。
2. 切 Job/策略/关闭/卸载 abort+generation；版本换代/409 显示权威状态。
3. Teleport、Escape、焦点恢复、移动全屏、44px、reduced-motion。

**GREEN:** 只通过原子客户端调用；任何成功都在 queue/detail/stage03 三处权威重读后 emit。

### Task 10：Pipeline 集成与单一入口

**Files:**
- Modify: `tiktok_bot_console/ui/src/views/Pipeline.vue`
- Modify: `tiktok_bot_console/ui/src/views/PipelineAcquisition.spec.ts`
- Modify: `tiktok_bot_console/ui/tests/smoke.mjs`

**RED:** AI Job 的 strategy 阶段显示业务卡；详情顶部有策略审核入口；只挂载一个 keyed drawer；切 Job 关闭旧上下文；旧事件不能刷新新 Job；技术诊断仍唯一。

**GREEN:** 页面不新增第二条 SSE；复用 Hermes monitor、5 秒非重入 Job 轮询和单一抽屉模式。

## Gate 03-D：交付

### Task 11：完整验证与真实浏览器验收

**Files:**
- Modify: `docs/wiki/00-索引.md`
- Modify: `docs/wiki/05-Pipeline.md`
- Modify: `docs/wiki/06-CLI-API-UI.md`
- Modify: `docs/wiki/07-数据库.md`
- Modify: `docs/wiki/12-测试报告.md`

**Steps:**

1. 后端专项、相邻回归、`py_compile/compileall`、`git diff --check`。
2. 前端组件/API 专项、`vue-tsc`、production build、smoke。
3. 真实 Job 至少生成一条 draft；人工编辑并 approve；确认阶段 04 只能读取 approved。真实外发需要单独授权，本 Gate 不发送。
4. 桌面与 390×844 验证无横向溢出、抽屉焦点与退出入口可达。
5. 独立规格与代码审查关闭全部 Critical/Important。

### Task 12：阶段提交、备份和推送

每个 Gate 绿后独立提交；最终创建 `backup/stage03-strategy-review-20260813`，推送 `mirror/master` 与标签。确认 `.env`、数据库、日志、Cookie、Profile、截图和 API Key 均未跟踪。

