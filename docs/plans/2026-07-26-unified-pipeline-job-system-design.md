# 全局统一管线任务系统设计

> 状态：已批准  
> 最后更新：2026-07-26

## 1. 目标

将当前同步调用的 Pipeline 改造成一套全局统一、可持久化、可调度、可恢复的任务系统。

TikTok 与抖音只作为同一任务系统内部的两种平台 Provider，不拆分任务表、API、调度器、执行历史或前端页面。

本次必须解决：

1. 创建任务时选择 TikTok 或抖音。
2. 账号支持“自动选择”或“明确指定”。
3. 手动任务与定时任务使用同一个执行引擎。
4. 所有 Pipeline 阶段严格按任务平台和任务用户集隔离。
5. 抖音按全局设置中的并发限制执行。
6. TikTok 使用通用指纹浏览器适配层；未配置具体 Provider 时禁止启动。
7. 服务重启后任务记录、阶段记录和排队状态不丢失。
8. 保留旧 `/api/pipeline/run`，但内部只负责创建统一任务，不能形成第二套执行链。

## 2. 非目标

本阶段不包含：

- 直接集成 AdsPower、比特浏览器等具体指纹浏览器厂商。
- 分布式队列、Redis、Celery 或多机部署。
- 同一社媒账号同时运行多个任务。
- TikTok 未配置指纹 Provider 时回退到普通 Playwright。
- 新建一套独立的 TikTok 或抖音任务页面。

## 3. 统一架构

```text
Pipeline.vue / ConfigPipeline.vue
                │
                ▼
        Unified Pipeline API
                │
                ▼
         PipelineJobService
        ┌───────┼────────┐
        │       │        │
        ▼       ▼        ▼
    JobStore  Scheduler  Dispatcher
        │                 │
        └──── SQLite ─────┘
                          │
                          ▼
                   PipelineJobRunner
                          │
                ┌─────────┴─────────┐
                ▼                   ▼
     DouyinPlaywrightProvider   FingerprintProvider
                │                   │
                └─────────┬─────────┘
                          ▼
                 Existing Pipeline Stages
```

统一性约束：

- 只有一个 `PipelineJobService` 负责创建、取消、重试和查询任务。
- 只有一个 `PipelineScheduler` 负责所有平台的定时计划。
- 只有一个 `PipelineDispatcher` 负责所有平台的任务认领和并发决策。
- 只有一个 `PipelineJobRunner` 负责阶段推进、记录结果和恢复状态。
- 平台差异只能存在于 `BrowserProvider`、平台 URL/选择器和账号预检中。
- UI 只有一个任务创建器、一个任务列表、一个任务详情区和一套定时配置。

## 4. 数据模型

### 4.1 `pipeline_jobs`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | VARCHAR(36) PK | UUID |
| `trigger_type` | VARCHAR(20) | manual / schedule / legacy |
| `schedule_id` | FK nullable | 来源定时计划 |
| `platform` | VARCHAR(20) | tiktok / douyin |
| `account_mode` | VARCHAR(20) | auto / specified |
| `account_id` | FK nullable | 指定或实际分配账号 |
| `stages_json` | JSON | 有序阶段列表 |
| `config_snapshot_json` | JSON | 创建时完整配置快照 |
| `status` | VARCHAR(24) | queued / running / succeeded / partial_failed / failed / cancelling / cancelled / interrupted |
| `current_stage` | VARCHAR(20) | 当前阶段 |
| `priority` | INTEGER | 数值越小优先级越高 |
| `retry_of_job_id` | FK nullable | 重试来源 |
| `error_summary` | TEXT | 汇总错误 |
| `queued_at` | DATETIME | 入队时间 |
| `started_at` | DATETIME nullable | 开始时间 |
| `finished_at` | DATETIME nullable | 结束时间 |
| `created_at` | DATETIME | 创建时间 |
| `updated_at` | DATETIME | 更新时间 |

### 4.2 `pipeline_job_stages`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | INTEGER PK | |
| `job_id` | FK | 所属任务 |
| `stage` | VARCHAR(20) | collect/filter/strategy/outreach/report/iterate |
| `stage_order` | INTEGER | 阶段顺序 |
| `status` | VARCHAR(20) | pending/running/succeeded/failed/skipped/cancelled |
| `attempt` | INTEGER | 当前执行次数 |
| `result_json` | JSON | 阶段结果 |
| `error_message` | TEXT | 错误 |
| `started_at` | DATETIME nullable | |
| `finished_at` | DATETIME nullable | |

唯一约束：`(job_id, stage)`。

### 4.3 `pipeline_schedules`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | INTEGER PK | |
| `name` | VARCHAR(100) | 计划名称 |
| `platform` | VARCHAR(20) | tiktok / douyin |
| `account_mode` | VARCHAR(20) | auto / specified |
| `account_id` | FK nullable | |
| `stages_json` | JSON | |
| `cron_expression` | VARCHAR(100) | 标准五段 cron |
| `timezone` | VARCHAR(50) | 默认 Asia/Shanghai |
| `enabled` | BOOLEAN | |
| `config_json` | JSON | 计划专属配置 |
| `next_run_at` | DATETIME nullable | |
| `last_run_at` | DATETIME nullable | |
| `created_at` | DATETIME | |
| `updated_at` | DATETIME | |

### 4.4 `pipeline_job_users`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `job_id` | FK | |
| `user_id` | FK | |
| `source_stage` | VARCHAR(20) | 加入任务的阶段 |
| `created_at` | DATETIME | |

唯一约束：`(job_id, user_id)`。

此表是阶段隔离的权威来源。筛选、策略与触达只能处理当前任务关联的用户。

### 4.5 现有表增量

- `strategies.job_id`：可空外键，记录策略来源任务。
- `messages.job_id`：可空外键，记录触达来源任务。
- `tiktok_accounts.browser_provider`：可空字符串。
- `tiktok_accounts.browser_profile_id`：可空字符串。

迁移必须幂等，兼容现有 SQLite 数据。

## 5. 任务执行语义

### 5.1 创建

创建任务时执行预检：

1. 平台必须是 `tiktok` 或 `douyin`。
2. 阶段列表必须非空且只能包含已知阶段。
3. 指定账号必须存在、平台匹配且状态为 `logged_in`。
4. 自动账号模式必须存在至少一个可用账号。
5. TikTok 账号必须配置指纹 Provider 和 Profile。
6. TikTok 指纹 Provider 必须报告可用，否则返回 409，不创建任务。

### 5.2 账号分配

- `specified`：使用用户指定账号。
- `auto`：从同平台、`logged_in` 且未被运行任务占用的账号中选择。
- 一个账号同一时间最多绑定一个 `running` 任务。
- 暂无可用账号时任务保留 `queued`，不失败。

### 5.3 阶段数据隔离

- `collect` 将新发现或已存在用户加入 `pipeline_job_users`。
- 单独运行 `filter` 时，在任务开始时按平台和 `pending` 状态建立任务用户快照。
- `strategy` 只处理当前任务已 qualified 的关联用户。
- `outreach` 只处理当前任务关联用户，并将 `platform`、`account_id` 和浏览器会话传给 Channel。
- `report` 记录任务维度结果，同时保留现有全局日报兼容输出。
- `iterate` 使用任务平台作为分析维度。

### 5.4 状态与恢复

- Worker 每完成一个阶段立即提交任务与阶段状态。
- 取消请求将任务置为 `cancelling`；Runner 在阶段边界安全停止并置为 `cancelled`。
- 服务启动时，遗留 `running/cancelling` 任务置为 `interrupted`。
- 用户可从失败或中断阶段创建重试任务；原任务不可覆盖。
- 定时器只补发最近一个错过的执行点，避免停机后生成大量历史任务。

## 6. 并发与浏览器 Provider

### 6.1 统一并发控制器

`PipelineConcurrencyManager` 是唯一并发入口：

- 抖音并发上限来自 `douyin_max_concurrency`，默认 1。
- 实际抖音并发上限为 `min(设置值, 可用未占用账号数)`。
- 同一账号永不并行。
- TikTok 并发能力未来由具体 Fingerprint Provider 报告；当前未配置时为 0。

### 6.2 浏览器接口

```python
class BrowserProvider(Protocol):
    async def check_available(self, account) -> AvailabilityResult: ...
    async def acquire(self, account) -> BrowserSession: ...
    async def release(self, session: BrowserSession) -> None: ...
```

- `DouyinPlaywrightProvider`：每个任务创建隔离 BrowserContext，不使用全局页面。
- `FingerprintBrowserProvider`：厂商无关协议。
- `UnavailableFingerprintProvider`：本阶段默认实现，返回可解释的不可用原因。
- Provider 配置中的 Token/API Key 只从环境变量或受保护配置读取，不写入代码和日志。

## 7. API 契约

统一端点：

```text
POST   /api/pipeline/jobs
GET    /api/pipeline/jobs
GET    /api/pipeline/jobs/{job_id}
POST   /api/pipeline/jobs/{job_id}/cancel
POST   /api/pipeline/jobs/{job_id}/retry
GET    /api/pipeline/capabilities

POST   /api/pipeline/schedules
GET    /api/pipeline/schedules
PUT    /api/pipeline/schedules/{schedule_id}
DELETE /api/pipeline/schedules/{schedule_id}
```

兼容端点：

```text
POST /api/pipeline/run
```

兼容端点将请求转换为 `pipeline_jobs` 记录并返回 `202` 语义的数据，不再同步执行 Pipeline。

## 8. 前端交互与视觉

### 8.1 设计系统

- 颜色：复用现有 `--brand`、`--ok`、`--warn`、`--err`、`--surface`、`--bg-sub`。
- 字体：复用现有 UI 字体和 `--font-mono`，不引入新字体。
- 间距：沿用 4/8/12/16/24 像素节奏。
- 圆角：输入 6–8px、卡片 10px，与当前页面一致。
- 阴影：只用于已有模态层和 sticky 操作条。
- 动效：120–180ms 状态变化；遵循 `prefers-reduced-motion`。

### 8.2 Pipeline 页面

保留一个 `/pipeline` 页面：

- 工具栏增加平台选择、账号模式、账号下拉和阶段选择。
- “运行”按钮改为创建任务。
- 预检状态明确显示账号、Provider 和并发槽位。
- 当前进度卡展示选中任务的真实阶段记录。
- 最近任务列表展示平台、账号、触发来源、状态、时间和操作。
- 支持取消、查看、重试。
- 不创建 TikTok Pipeline 页或抖音 Pipeline 页。

### 8.3 ConfigPipeline 页面

- 保留统一运行设置页。
- 增加 `douyin_max_concurrency`。
- 调度区从只读 mock 卡片改为统一计划列表和编辑器。
- 每个计划选择平台、账号策略、阶段、cron、时区和启停状态。

### 8.4 ConfigAccounts 页面

- TikTok 账号显示指纹 Provider/Profile 配置状态。
- 抖音账号显示 Playwright 隔离会话能力。
- 账号卡仍使用同一组件，通过平台字段展示差异。

完整状态：加载、空、预检失败、排队、运行、取消中、成功、部分失败、失败、中断。

## 9. 错误处理

- 参数错误：HTTP 422。
- 平台/账号不匹配：HTTP 409。
- TikTok Provider 未配置：HTTP 409，返回稳定错误码 `fingerprint_provider_unavailable`。
- 指定账号已被占用：任务可创建并保持排队。
- 阶段业务失败：写入阶段错误；任务按已有“单阶段失败继续”策略得到 `partial_failed`。
- 数据库/Runner 内部错误：任务 `failed`，保留错误摘要，日志不包含 Cookie、Token 或 API Key。

## 10. 测试策略

### 后端

- ORM 建表与幂等迁移。
- 任务创建预检与平台/账号匹配。
- 自动账号选择与账号独占。
- 抖音并发上限。
- TikTok Provider 未配置时阻止启动。
- 阶段用户隔离。
- 状态推进、取消、失败、重试、重启恢复。
- 调度器生成任务与错过执行点补发。
- 旧 `/api/pipeline/run` 只创建统一任务。

### 前端

- API payload 包含平台、账号模式、账号与阶段。
- 平台切换后只显示对应账号。
- 无可用账号或 Provider 时禁用运行。
- 任务列表与阶段详情渲染。
- 定时计划的创建、编辑、启停。
- 中英文键一致、类型检查和生产构建通过。

### 集成验收

1. 抖音登录账号可以创建并运行任务。
2. TikTok 未配置指纹 Provider 时无法启动并给出明确提示。
3. 两个抖音账号在并发限制 2 时可运行两个任务；同一账号不并行。
4. 抖音任务不会处理 TikTok 用户。
5. 重启服务后排队任务仍存在，运行中任务变为中断且可重试。
6. 手动任务与定时任务出现在同一历史列表。

