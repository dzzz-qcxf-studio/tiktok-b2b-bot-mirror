# 05 — Pipeline 编排

> 关联: [索引](00-索引.md) | [Plugin层](04-Plugin层.md) | [CLI-API-UI](06-CLI-API-UI.md)
> 最后更新: 2026-08-09（Hermes H2 获客创建器）

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
应用壳在移动端使用底部横向导航，不再由固定侧栏压缩主内容。

## 阶段执行流程

```text
Runner 为每个 Stage: pending → running → terminal
  │
  ├── 阶段1 _run_collect:
  │   ├── 无 AcquisitionCampaign → 保持旧关键词用户搜索路径
  │   └── 有 AcquisitionCampaign → 新获客路径
  │       ├── 冻结画像 + effective/new 70/30 关键词计划
  │       ├── Pipeline 注入 HermesEvidenceAgent，视频/评论优先、用户搜索辅助
  │       ├── 跨关键词共享 keywords/videos/comments/pages/duration/LLM 等预算
  │       ├── Schema、平台、整批预算和权威 metrics 双重校验
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
  运行中取消先进入 `cancelling`，在阶段边界结束为 `cancelled`。
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
