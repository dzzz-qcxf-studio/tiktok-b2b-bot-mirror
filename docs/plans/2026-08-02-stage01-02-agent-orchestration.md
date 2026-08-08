# 阶段 01—02 Agent 编排实施计划

> **For implementer:** Use TDD throughout. Write failing test first. Watch it fail. Then implement.

**Goal:** 实现带目标画像、关键词效果、来源证据、双评分、四状态和人工复核闸门的阶段 01—02，并以 Hermes 作为受限浏览执行 Agent。

**Architecture:** 现有 PipelineJobService 保持唯一任务编排器；新增 Acquisition 数据与服务边界，Agent 只提交 schema 化观察，Service 负责事务、去重、状态迁移和审计。前端以阶段业务看板和候选复核工作台替换默认原始 JSON。

**Tech Stack:** Python 3.11、FastAPI、SQLAlchemy/SQLite、Pydantic、Vue 3、TypeScript、Vitest、Pytest、现有 Hermes BrowseAgent 与 LLM Router。

---

## Task 1：获客数据模型、迁移和状态机

**Files:**
- Modify: `tiktok_bot_core/models/entities.py`
- Modify: `tiktok_bot_core/models/pipeline_states.py`
- Modify: `tiktok_bot_core/storage/database.py`
- Create: `tiktok_bot_core/storage/acquisition_store.py`
- Create: `tests/test_acquisition_store.py`

**RED:** 先测试画像、关键词、同用户多证据、AI 评估、四状态和人工审计可落库；测试旧库迁移新增列/表且原数据保留；测试非法状态迁移被拒绝。

**GREEN:** 实现最小 ORM、迁移、Store 和状态验证。`PipelineJobUser` 增加发现状态、筛选状态、匹配度、可信度、标签、优先级和人工确认时间；原 `status` 保持兼容映射。

**Verify:**
`python -X utf8 -m pytest tests/test_acquisition_store.py -q`

## Task 2：画像、关键词、候选证据和人工复核 API

**Files:**
- Modify: `tiktok_bot_api/main.py`
- Modify: `tiktok_bot_console/ui/src/api/index.ts`
- Modify: `tiktok_bot_console/ui/src/types/pipeline.ts`
- Create: `tests/test_acquisition_api.py`

**RED:** 覆盖创建画像任务、关键词 CRUD/统计、阶段 01/02 聚合、候选详情、人工直接通过、直接淘汰、补充资料、修改标签和审计读取；未登录及非法跨状态必须失败。

**GREEN:** 增加 `/api/acquisition/*` 端点，所有写操作走 AcquisitionService/Store，不从客户端接受操作人或租户字段；响应不包含密钥、Cookie 或私密路径。

**Verify:**
`python -X utf8 -m pytest tests/test_acquisition_api.py tests/test_auth.py -q`

## Task 3：有边界的 Agent 编排与阶段 01 证据采集

**Files:**
- Create: `tiktok_bot_core/services/acquisition_agents.py`
- Modify: `tiktok_bot_core/services/browse_agent.py`
- Modify: `tiktok_bot_core/plugins/collectors/keyword_collector.py`
- Modify: `tiktok_bot_core/services/pipeline.py`
- Create: `tests/test_acquisition_agents.py`

**RED:** 测试 Planner 的 70/30 词组合、每级预算、视频优先/用户辅助顺序、Hermes 域名/动作限制、重复用户保留多证据、预算耗尽进入待核验、Agent 不可直接写 qualified 或发送消息。

**GREEN:** 实现 DiscoveryPlannerAgent、HermesEvidenceAgent 和 CandidateAgent 的版本化 Pydantic 契约；扩展 Hermes `extract` 返回结构化观察；阶段 01 保存业务结果和证据聚合。

**Verify:**
`python -X utf8 -m pytest tests/test_acquisition_agents.py tests/test_browse_agent.py tests/test_pipeline.py -q`

## Task 4：阶段 02 资格判断、补全与人工闸门

**Files:**
- Modify: `tiktok_bot_core/services/acquisition_agents.py`
- Modify: `tiktok_bot_core/plugins/filters/llm_filter.py`
- Modify: `tiktok_bot_core/services/pipeline.py`
- Modify: `tiktok_bot_core/storage/pipeline_job_store.py`
- Create: `tests/test_qualification_workflow.py`

**RED:** 测试匹配度和可信度分离、多身份标签、资料未知不自动淘汰、需求评论优先保留、四状态阈值、人工复核直接通过/淘汰、阶段 03 只取 qualified。

**GREEN:** 实现 EnrichmentAgent 和 QualificationAgent；LLM 输出经过 schema 校验并落原始 AI 建议；人工结论独立保存；阶段 02 汇总四状态。

**Verify:**
`python -X utf8 -m pytest tests/test_qualification_workflow.py tests/test_pipeline.py tests/test_pipeline_jobs.py -q`

## Task 5：创建任务目标画像 UI

**Files:**
- Modify: `tiktok_bot_console/ui/src/views/Pipeline.vue`
- Modify: `tiktok_bot_console/ui/src/types/pipeline.ts`
- Modify: `tiktok_bot_console/ui/src/i18n/zh-CN.ts`
- Modify: `tiktok_bot_console/ui/src/i18n/en-US.ts`
- Create: `tiktok_bot_console/ui/src/views/PipelineAcquisition.spec.ts`

**RED:** 测试 TikTok 国家必填、抖音默认中国、硬性/偏好条件区分、关键词可人工增删、预算验证和完整 `configSnapshot` 提交。

**GREEN:** 在创建任务区增加分步画像配置，保留平台/账号/阶段能力预检；提交后展示不可变任务摘要。

**Verify:**
`npm exec vitest -- --run src/views/PipelineAcquisition.spec.ts`

## Task 6：阶段 01/02 看板和人工复核工作台

**Files:**
- Modify: `tiktok_bot_console/ui/src/views/Pipeline.vue`
- Create: `tiktok_bot_console/ui/src/components/StageDiscoveryResult.vue`
- Create: `tiktok_bot_console/ui/src/components/StageQualificationResult.vue`
- Create: `tiktok_bot_console/ui/src/components/CandidateReviewDrawer.vue`
- Create: `tiktok_bot_console/ui/src/components/StageResults.spec.ts`

**RED:** 测试看板指标可下钻、证据链接可见、双评分/多标签/缺失信息展示、人工通过/淘汰/补资料/改标签调用真实 API、并发操作禁用、审计刷新、原始 JSON 仅在诊断区。

**GREEN:** 实现业务组件、空/错/加载状态、桌面/移动布局和可访问性；任务轮询与复核操作使用请求代次保护，避免迟到响应覆盖新状态。

**Verify:**
`npm exec vitest -- --run src/components/StageResults.spec.ts src/views/PipelineAcquisition.spec.ts && npm run type-check && npm run build`

## Task 7：文档、回归和阶段验收

**Files:**
- Modify: `docs/wiki/00-索引.md`
- Modify: `docs/wiki/05-Pipeline.md`
- Modify: `docs/wiki/06-CLI-API-UI.md`
- Modify: `docs/wiki/07-数据库.md`
- Modify: `docs/wiki/12-测试报告.md`
- Modify: `README.md`

**Verify:**
- 后端阶段 01/02 专项全部通过；
- 现有 Pipeline、认证、LLM Router 与账号登录回归通过；
- 前端专项、类型检查、生产构建和 Smoke 通过；
- 真实本地 API 创建一条只含阶段 01/02 的任务，确认画像、证据、四状态和人工操作落库；
- 桌面与 390px 页面检查通过；
- `git diff --check` 通过且仓库中不存在密钥正文。
