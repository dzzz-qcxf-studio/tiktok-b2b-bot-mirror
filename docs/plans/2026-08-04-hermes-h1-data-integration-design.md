# Hermes H1 数据接线设计

> 状态：已确认
> 最后更新：2026-08-08
> 版本：0.6.2-hermes-h1
> 范围：统一数据契约、AI 获客任务原子创建、AI 重试连续性、全局业务读模型；不包含 H2 页面表单和 H3 复核工作台

## 1. 目标

Hermes 继续作为现有六阶段 Pipeline 的受限执行引擎，不建立第二套任务系统或独立数据孤岛。H1 解决四个问题：

1. Pipeline Job、目标画像和初始关键词必须在一个数据库事务中创建，提交前 Dispatcher 不可见。
2. AI 获客任务重试后仍然携带画像和关键词，不能退回 legacy 采集路径。
3. `User` 只保存平台身份和公开资料；某次获客任务的资格状态、评分、标签和证据继续由 Job 级表保存。
4. Users、Lead、Dashboard、Reports 和关键词图表通过同一业务投影读取数据，不依赖把 Job 结论反写到全局 `User.status/category`。

## 2. 原子创建契约

新增受认证端点：

```text
POST /api/acquisition/jobs
```

请求由现有 Pipeline 字段、一个严格 `campaign` 和 1..100 个严格 `keywords` 组成。`stages` 必须包含 `collect`，并且必须是 `collect → filter → strategy → outreach → report → iterate` 的严格有序子序列；不得重复、倒序或包含未知阶段。API 与 Service 共用同一校验，避免非 HTTP 调用绕过。服务执行顺序：

```text
账号与 Browser Provider 预检
        ↓
BEGIN transaction
        ├── pipeline_jobs + pipeline_job_stages
        ├── acquisition_campaigns
        └── acquisition_keywords
COMMIT
        ↓
Dispatcher 首次可见 queued Job
```

任意画像或关键词写入失败时整个事务回滚，不允许留下没有 Campaign 的 queued Job。服务端在 `config_snapshot_json` 中写入 `businessMode=ai_acquisition` 与 `acquisitionSchemaVersion=1.0` 作为显示和诊断元数据；实际执行分支仍以 `acquisition_campaigns` 是否存在为权威判断。

旧的 `POST /api/acquisition/jobs/{jobId}/campaign` 保留兼容和诊断用途，新 UI 不再使用两次请求创建任务。

## 3. AI 任务重试

Legacy Job 继续从首个失败阶段重试。带 `AcquisitionCampaign` 的 Job 使用以下规则：

- 新建独立 retry Job，不覆盖原 Job；
- 从原任务的 `collect` 阶段重新开始，避免复制半成品 Evidence、Assessment 或人工审计；
- 在同一事务复制不可变 Campaign 快照和关键词定义；
- 关键词使用次数、视频数、候选数等本次执行计数重置为 0；关键词文本、语言、类型、来源和状态保留；
- 新 Job 提交前同样不会被 Dispatcher 看到。

## 4. 统一业务读模型

新增只读投影服务，不新增权威业务表。对每个用户：

1. 只在存在 `AcquisitionCampaign` 的任务中选择最新 `PipelineJobUser`，排序为 `PipelineJob.created_at DESC, PipelineJob.id DESC`；
2. `replied` 和已发送 Message 的 `contacted` 状态优先于资格状态；
3. 最新资格为 `qualified/rejected` 时映射到全局展示状态；`manual_review/need_enrichment` 在旧 Users 页面映射为 `pending`，并通过新增字段 `qualification_status` 保留原值；
4. 分类、匹配度、可信度、标签和来源 Job 从最新 Job 关系读取；没有 AI 数据时回退到 `User` legacy 字段；
5. `User.status/category` 不因 AI 评估或人工复核被覆盖。

投影响应保留现有字段，并补充：

```text
business_source, source_job_id, qualification_status,
match_score, confidence_score, labels
```

## 5. 关键词与图表口径

关键词效果不再只统计 `User.source == keyword_search`：

- AI 关键词的候选数按 `DiscoveryEvidence` 中不同用户实时聚合；
- 合格数按同 Job 的 `PipelineJobUser.qualification_status == qualified` 实时聚合；
- legacy 关键词继续从 `User.source_keyword` 聚合，并把 `qualified/contacted/replied` 视为已通过；
- 同名关键词合并后返回 `name/keyword/total/converted/rate`，兼容 Dashboard；
- 词云返回现有组件真实需要的 `word/count`，不再返回不匹配的 `name/value`。

Dashboard、Users 统计、Lead 搜索和 Reports Overview 都使用同一投影。日报趋势继续读取 `DailyReport` 历史快照；H2 的 AI 创建器默认包含 `report`，未运行 report 的任务不会伪造历史日报。

实时统计以 UTC 自然日为边界。Reports Overview 的漏斗使用累计业务状态；地区统计来自真实已发送消息和回复；情感统计来自真实 Reply 情感；`businessIntent` 只统计已发送消息对应、具有商业意向的不同回复用户，不使用固定占位百分比。

## 6. Lead 排序

Lead 搜索仍保留原 URL 和字段：

- 有 AI 评估时，`relevance_score` 直接使用最新 `match_score`（契约范围为 0..100，并在读模型边界钳制和四舍五入）；
- 没有 AI 评估时才使用 legacy 启发式分数；
- 搜索范围包含用户公开资料、legacy 来源关键词和 Discovery Evidence 关键词；
- 额外返回来源 Job、资格状态和可信度，旧页面可忽略新增字段。

## 7. 安全与兼容

- 原子创建端点必须认证；顶层及 Campaign/Keyword 定义不接受 operator、tenant、Cookie、
  Token、Secret 或未知嵌套字段。`configSnapshot` 沿用现有 Pipeline 的开放配置快照契约，
  但 API 与 Service 会递归拒绝 API Key、Cookie、Token、Password、Authorization、
  Client Secret、Private Key、Credentials 等凭据类键及常见组合变体；同时识别 `authHeader`、
  `authValue`、`auth: "Bearer ..."` 和 `auth.type + auth.value` 等认证结构。错误响应不回显键名或值。
- API Key、Cookie、浏览器 Profile 和完整 Prompt 不进入投影或响应。
- 不删除旧接口、不修改六阶段名称、不改变 legacy Job 的创建和重试语义。
- H1 不宣称多租户安全；租户隔离仍是 P0 上线阻断项。

## 8. H1 验收

1. 原子创建成功后一次返回 Job、Campaign 和 Keywords。
2. 注入关键词写入失败时，三类记录全部不存在。
3. 另一个数据库连接在事务提交前看不到 queued Job。
4. AI Job 重试后仍有 Campaign/Keywords，并从 collect 开始。
5. 同一用户的不同 Job 结论不覆盖 `User`，全局页面读取最新投影。
6. Dashboard、Users、Lead、Reports 和词云能读取 AI 获客数据；legacy 数据仍可见。
7. 定向测试、全量 pytest、类型/语法检查和 `git diff --check` 通过。
