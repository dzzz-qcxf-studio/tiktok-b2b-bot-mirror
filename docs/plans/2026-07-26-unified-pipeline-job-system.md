# 全局统一管线任务系统实施计划

> **For implementer:** Use TDD throughout. Write failing test first. Watch it fail. Then implement.

**Goal:** 用一套持久化任务、调度、并发、API 和 UI 同时管理 TikTok 与抖音 Pipeline。

**Architecture:** `PipelineJobService` 是唯一任务入口；SQLite 是任务状态权威源；Scheduler 和 Dispatcher 都围绕同一组 Job 表工作；平台差异仅通过 Browser Provider 和 Platform 抽象注入。旧 `/api/pipeline/run` 转换为统一任务创建，不保留同步旁路。

**Tech Stack:** Python 3.11、FastAPI、SQLAlchemy 2、SQLite、asyncio、Playwright、Vue 3、TypeScript、Element Plus。

**Design:** `docs/plans/2026-07-26-unified-pipeline-job-system-design.md`

---

## Task 1：任务 ORM 与幂等迁移

**Files:**

- Modify: `tiktok_bot_core/models/entities.py`
- Modify: `tiktok_bot_core/storage/database.py`
- Test: `tests/test_pipeline_jobs.py`

**Step 1: Write the failing tests**

新增测试：

```python
def test_pipeline_job_tables_created(db):
    names = set(inspect(db.engine).get_table_names())
    assert {
        "pipeline_jobs",
        "pipeline_job_stages",
        "pipeline_schedules",
        "pipeline_job_users",
    } <= names


def test_existing_tables_receive_job_columns(db):
    columns = {
        table: {c["name"] for c in inspect(db.engine).get_columns(table)}
        for table in ("strategies", "messages", "tiktok_accounts")
    }
    assert "job_id" in columns["strategies"]
    assert "job_id" in columns["messages"]
    assert {"browser_provider", "browser_profile_id"} <= columns["tiktok_accounts"]


def test_database_migration_is_idempotent(db):
    db.init()
    db.init()
```

**Step 2: Run tests and confirm RED**

```powershell
python -m pytest tests/test_pipeline_jobs.py -k "tables_created or receive_job_columns or migration_is_idempotent" -v
```

Expected: FAIL because models/tables do not exist.

**Step 3: Minimal implementation**

在 `entities.py` 新增：

- `PipelineJob`
- `PipelineJobStage`
- `PipelineSchedule`
- `PipelineJobUser`

按设计文档定义字段和关系。`PipelineJob.id` 使用 `uuid.uuid4()` 字符串默认值。

为现有模型增加：

```python
Strategy.job_id = mapped_column(ForeignKey("pipeline_jobs.id"), nullable=True, index=True)
Message.job_id = mapped_column(ForeignKey("pipeline_jobs.id"), nullable=True, index=True)
TikTokAccount.browser_provider = mapped_column(String(50), default="")
TikTokAccount.browser_profile_id = mapped_column(String(200), default="")
```

把 `Database._migrate()` 从只处理 `users` 改为遍历以下现有表：

```python
MIGRATABLE_MODELS = (User, Strategy, Message, TikTokAccount)
```

只追加不存在且可空或有默认值的简单列；表不存在时跳过。

**Step 4: Verify GREEN**

```powershell
python -m pytest tests/test_pipeline_jobs.py -k "tables_created or receive_job_columns or migration_is_idempotent" -v
python -m pytest tests/test_core.py tests/test_platforms_auth.py -v
```

Expected: PASS.

**Step 5: Commit**

```powershell
git add tiktok_bot_core/models/entities.py tiktok_bot_core/storage/database.py tests/test_pipeline_jobs.py
git commit -m "feat: add unified pipeline job models"
```

---

## Task 2：统一任务存储层

**Files:**

- Create: `tiktok_bot_core/storage/pipeline_job_store.py`
- Test: `tests/test_pipeline_jobs.py`

**Step 1: Write the failing tests**

覆盖：

```python
def test_create_job_creates_ordered_stage_rows(db): ...
def test_list_jobs_filters_platform_and_status(db): ...
def test_claim_queued_job_changes_status_atomically(db): ...
def test_link_job_user_is_idempotent(db): ...
def test_cancel_queued_job_finishes_immediately(db): ...
def test_recover_running_jobs_marks_interrupted(db): ...
```

测试必须直接使用真实临时 SQLite，不 mock Store。

**Step 2: Verify RED**

```powershell
python -m pytest tests/test_pipeline_jobs.py -k "create_job or list_jobs or claim_queued or link_job_user or cancel_queued or recover_running" -v
```

Expected: import or assertion failures because `PipelineJobStore` is absent.

**Step 3: Minimal implementation**

`PipelineJobStore` 提供：

```python
create_job(session, *, platform, account_mode, account_id, stages,
           trigger_type="manual", schedule_id=None, priority=100,
           config_snapshot=None, retry_of_job_id=None) -> PipelineJob
get_job(session, job_id) -> PipelineJob | None
list_jobs(session, *, platform=None, status=None, limit=50, offset=0)
claim_next_job(session, *, platforms: set[str]) -> PipelineJob | None
set_job_status(session, job_id, status, **timestamps)
start_stage(session, job_id, stage)
finish_stage(session, job_id, stage, status, result=None, error="")
link_user(session, job_id, user_id, source_stage)
list_job_user_ids(session, job_id, *, user_status=None)
request_cancel(session, job_id)
recover_interrupted(session) -> int
```

状态值集中定义在此模块，不在多处硬编码。

**Step 4: Verify GREEN**

```powershell
python -m pytest tests/test_pipeline_jobs.py -v
```

Expected: PASS.

**Step 5: Commit**

```powershell
git add tiktok_bot_core/storage/pipeline_job_store.py tests/test_pipeline_jobs.py
git commit -m "feat: add durable pipeline job store"
```

---

## Task 3：统一 Browser Provider 与并发控制

**Files:**

- Create: `tiktok_bot_core/browser/providers.py`
- Create: `tiktok_bot_core/services/pipeline_concurrency.py`
- Modify: `tiktok_bot_core/browser/client.py`
- Test: `tests/test_pipeline_runtime.py`

**Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_tiktok_unavailable_provider_blocks_acquire(): ...

@pytest.mark.asyncio
async def test_douyin_provider_creates_isolated_sessions(): ...

@pytest.mark.asyncio
async def test_concurrency_manager_obeys_douyin_limit(): ...

@pytest.mark.asyncio
async def test_same_account_is_never_acquired_twice(): ...
```

Playwright 用 fake factory 注入，测试不能启动真实浏览器。

**Step 2: Verify RED**

```powershell
python -m pytest tests/test_pipeline_runtime.py -v
```

Expected: missing provider/concurrency classes.

**Step 3: Minimal implementation**

新增：

```python
@dataclass
class BrowserAvailability:
    available: bool
    code: str = ""
    message: str = ""


class BrowserProvider(Protocol):
    async def check_available(self, account) -> BrowserAvailability: ...
    async def acquire(self, account): ...
    async def release(self, session) -> None: ...
```

实现：

- `DouyinPlaywrightProvider`：新建独立 BrowserClient/Context。
- `UnavailableFingerprintProvider`：稳定返回 `fingerprint_provider_unavailable`。
- `BrowserProviderRegistry`：`douyin` 与 `tiktok` 都从同一 Registry 获取。
- `PipelineConcurrencyManager`：统一维护平台 semaphore 与账号锁。

禁止 TikTok 自动回退到 `DouyinPlaywrightProvider`。

**Step 4: Verify GREEN**

```powershell
python -m pytest tests/test_pipeline_runtime.py -v
python -m pytest tests/test_plugins.py tests/test_platforms_auth.py -v
```

Expected: PASS.

**Step 5: Commit**

```powershell
git add tiktok_bot_core/browser/providers.py tiktok_bot_core/browser/client.py tiktok_bot_core/services/pipeline_concurrency.py tests/test_pipeline_runtime.py
git commit -m "feat: add unified browser provider runtime"
```

---

## Task 4：Pipeline 阶段任务上下文与数据隔离

**Files:**

- Modify: `tiktok_bot_core/services/pipeline.py`
- Modify: `tiktok_bot_core/storage/sqlite_store.py`
- Modify: `tiktok_bot_core/plugins/collectors/keyword_collector.py`
- Modify: `tiktok_bot_core/plugins/channels/comment_channel.py`
- Modify: `tiktok_bot_core/plugins/channels/dm_channel.py`
- Test: `tests/test_pipeline.py`
- Test: `tests/test_pipeline_jobs.py`

**Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_douyin_job_only_filters_linked_douyin_users(db): ...

@pytest.mark.asyncio
async def test_outreach_receives_platform_account_and_browser_session(db): ...

@pytest.mark.asyncio
async def test_collect_links_saved_users_to_job(db): ...
```

**Step 2: Verify RED**

```powershell
python -m pytest tests/test_pipeline.py tests/test_pipeline_jobs.py -k "linked_douyin or outreach_receives or collect_links" -v
```

Expected: current Pipeline processes global users and sends empty channel config.

**Step 3: Minimal implementation**

新增不可变执行上下文：

```python
@dataclass(frozen=True)
class PipelineRunContext:
    job_id: str
    platform: str
    account_id: int
    account_username: str
    browser_session: Any
```

`PipelineService.run(..., context=None)`：

- `context=None` 仅供单元测试和内部兼容。
- Job Runner 必须传 context。
- collect 后 link user。
- filter/strategy/outreach 使用 `pipeline_job_users` 限定用户。
- Channel config 必须包含 platform/account_id/account/browser_session/job_id。
- Strategy/Message 写入 job_id。

保持现有 `stages`、`collection_config` 等参数兼容。

**Step 4: Verify GREEN**

```powershell
python -m pytest tests/test_pipeline.py tests/test_pipeline_jobs.py -v
```

Expected: PASS.

**Step 5: Commit**

```powershell
git add tiktok_bot_core/services/pipeline.py tiktok_bot_core/storage/sqlite_store.py tiktok_bot_core/plugins/collectors/keyword_collector.py tiktok_bot_core/plugins/channels/comment_channel.py tiktok_bot_core/plugins/channels/dm_channel.py tests/test_pipeline.py tests/test_pipeline_jobs.py
git commit -m "feat: isolate pipeline execution by job platform"
```

---

## Task 5：统一 Job Service、Runner、Dispatcher 与 Scheduler

**Files:**

- Create: `tiktok_bot_core/services/pipeline_jobs.py`
- Create: `tiktok_bot_core/services/pipeline_scheduler.py`
- Test: `tests/test_pipeline_runtime.py`

**Step 1: Write the failing tests**

覆盖：

```python
async def test_create_job_rejects_platform_account_mismatch(): ...
async def test_create_tiktok_job_rejects_unavailable_provider(): ...
async def test_auto_account_waits_when_all_accounts_busy(): ...
async def test_runner_persists_each_stage_transition(): ...
async def test_cancel_stops_at_stage_boundary(): ...
async def test_retry_starts_from_failed_stage(): ...
async def test_scheduler_creates_jobs_in_same_job_table(): ...
async def test_scheduler_backfills_only_latest_missed_run(): ...
```

**Step 2: Verify RED**

```powershell
python -m pytest tests/test_pipeline_runtime.py -v
```

Expected: missing unified runtime.

**Step 3: Minimal implementation**

一个模块内定义并共享：

- `PipelineJobService`
- `PipelineJobRunner`
- `PipelineDispatcher`
- `PipelineRuntime`

Scheduler 轮询 `pipeline_schedules.next_run_at`；Dispatcher 轮询 `pipeline_jobs.status == queued`。SQLite 是权威队列，不另建内存任务系统。

`PipelineRuntime.start()`：

1. 恢复遗留运行任务。
2. 启动 Scheduler loop。
3. 启动 Dispatcher loop。

`PipelineRuntime.stop()` 安全取消后台 loop 并释放浏览器资源。

cron 计算在 `pipeline_scheduler.py` 内实现仅需的标准五段语义，或使用项目已有依赖；若需要新增依赖，优先国内镜像安装并锁定版本。

**Step 4: Verify GREEN**

```powershell
python -m pytest tests/test_pipeline_runtime.py tests/test_pipeline_jobs.py tests/test_pipeline.py -v
```

Expected: PASS.

**Step 5: Commit**

```powershell
git add tiktok_bot_core/services/pipeline_jobs.py tiktok_bot_core/services/pipeline_scheduler.py tests/test_pipeline_runtime.py
git commit -m "feat: add unified pipeline scheduler and dispatcher"
```

---

## Task 6：统一 FastAPI 契约与生命周期

**Files:**

- Modify: `tiktok_bot_api/main.py`
- Create: `tests/test_pipeline_api.py`

**Step 1: Write the failing tests**

使用 FastAPI TestClient 覆盖：

```python
def test_create_pipeline_job_returns_202(): ...
def test_list_and_get_pipeline_jobs(): ...
def test_cancel_and_retry_pipeline_job(): ...
def test_schedule_crud_uses_unified_model(): ...
def test_legacy_pipeline_run_creates_job_instead_of_running_inline(): ...
def test_tiktok_provider_error_has_stable_code(): ...
```

**Step 2: Verify RED**

```powershell
python -m pytest tests/test_pipeline_api.py -v
```

Expected: 404 or old synchronous response.

**Step 3: Minimal implementation**

新增 Pydantic 请求/响应模型和设计文档中的统一端点。

旧端点：

```python
@app.post("/api/pipeline/run", status_code=202)
async def run_pipeline(req):
    return await create_pipeline_job(convert_legacy_request(req))
```

FastAPI lifespan 启停全局 `PipelineRuntime`。测试可通过依赖覆盖禁用后台 loop。

所有错误使用：

```json
{
  "detail": {
    "code": "fingerprint_provider_unavailable",
    "message": "..."
  }
}
```

**Step 4: Verify GREEN**

```powershell
python -m pytest tests/test_pipeline_api.py -v
python -m pytest tests/ -v
```

Expected: PASS.

**Step 5: Commit**

```powershell
git add tiktok_bot_api/main.py tests/test_pipeline_api.py
git commit -m "feat: expose unified pipeline job api"
```

---

## Task 7：前端 API、类型与 Mock

**Files:**

- Modify: `tiktok_bot_console/ui/src/api/index.ts`
- Modify: `tiktok_bot_console/ui/src/api/mock.ts`
- Create: `tiktok_bot_console/ui/src/types/pipeline.ts`
- Modify: `tiktok_bot_console/ui/scripts/smoke.mjs`

**Step 1: Write failing smoke checks**

新增检查：

- `createPipelineJob` payload 保留 platform/accountMode/accountId/stages。
- `listPipelineJobs` 返回统一任务数组。
- `getPipelineCapabilities` 返回平台能力。
- `listPipelineSchedules` 返回统一定时计划。

**Step 2: Verify RED**

```powershell
Set-Location tiktok_bot_console/ui
npm run test
```

Expected: missing API methods/types.

**Step 3: Minimal implementation**

定义：

```ts
export type PipelinePlatform = 'tiktok' | 'douyin'
export type AccountMode = 'auto' | 'specified'
export interface CreatePipelineJobPayload { ... }
export interface PipelineJob { ... }
export interface PipelineSchedule { ... }
```

真实 API 与 Mock 使用相同方法名和响应形状。保留 `runPipeline`，但令其调用 `createPipelineJob` 的兼容包装。

**Step 4: Verify GREEN**

```powershell
npm run test
npm run type-check
```

Expected: PASS.

**Step 5: Commit**

```powershell
git add tiktok_bot_console/ui/src/api/index.ts tiktok_bot_console/ui/src/api/mock.ts tiktok_bot_console/ui/src/types/pipeline.ts tiktok_bot_console/ui/scripts/smoke.mjs
git commit -m "feat: add unified pipeline job client"
```

---

## Task 8：统一 Pipeline 页面

**Files:**

- Modify: `tiktok_bot_console/ui/src/views/Pipeline.vue`
- Modify: `tiktok_bot_console/ui/src/i18n/zh-CN.ts`
- Modify: `tiktok_bot_console/ui/src/i18n/en-US.ts`
- Modify: `tiktok_bot_console/ui/scripts/smoke.mjs`

**Step 1: Write failing checks**

Smoke source checks验证：

- 页面包含平台、账号模式、账号下拉与阶段字段。
- 运行按钮调用 `createPipelineJob`。
- 页面包含取消和重试动作。
- 不存在独立 TikTok/Douyin Pipeline 路由。

**Step 2: Verify RED**

```powershell
Set-Location tiktok_bot_console/ui
npm run test
```

Expected: selectors/API symbols missing.

**Step 3: Build the approved v0 structure**

在现有页面工具栏内增加：

1. TikTok/抖音分段选择。
2. 自动/指定账号选择。
3. 对应平台账号下拉。
4. Provider/账号/并发预检提示。
5. 创建任务按钮。

把当前假历史改为统一真实任务列表；选择任务后复用现有六阶段卡展示其阶段数据。使用现有 design tokens，不新增页面、不新增颜色体系。

状态必须覆盖：loading/empty/blocked/queued/running/cancelling/succeeded/partial_failed/failed/interrupted。

**Step 4: Verify GREEN**

```powershell
npm run test
npm run type-check
npm run build
```

Expected: PASS.

**Step 5: Commit**

```powershell
git add tiktok_bot_console/ui/src/views/Pipeline.vue tiktok_bot_console/ui/src/i18n/zh-CN.ts tiktok_bot_console/ui/src/i18n/en-US.ts tiktok_bot_console/ui/scripts/smoke.mjs
git commit -m "feat: build unified pipeline task console"
```

---

## Task 9：统一定时计划与账号 Provider 状态

**Files:**

- Modify: `tiktok_bot_console/ui/src/views/ConfigPipeline.vue`
- Modify: `tiktok_bot_console/ui/src/views/ConfigAccounts.vue`
- Modify: `tiktok_bot_console/ui/src/api/index.ts`
- Modify: `tiktok_bot_console/ui/src/api/mock.ts`
- Modify: `tiktok_bot_console/ui/src/i18n/zh-CN.ts`
- Modify: `tiktok_bot_console/ui/src/i18n/en-US.ts`
- Modify: `tiktok_bot_console/ui/scripts/smoke.mjs`

**Step 1: Write failing checks**

验证：

- `douyin_max_concurrency` 可读取和保存。
- 定时计划 CRUD 使用 `/api/pipeline/schedules`。
- 计划编辑器包含平台、账号策略、阶段、cron、时区与启停。
- TikTok 账号卡显示 Provider/Profile 配置状态。

**Step 2: Verify RED**

```powershell
Set-Location tiktok_bot_console/ui
npm run test
```

Expected: new config/schedule behavior absent.

**Step 3: Minimal implementation**

只增强现有两个页面，不创建第二套设置页。统一计划列表中的平台只作为字段和徽标。

TikTok Provider 配置只显示状态与缺失提示；具体厂商连接表单不在本阶段实现。

**Step 4: Verify GREEN**

```powershell
npm run test
npm run type-check
npm run build
```

Expected: PASS.

**Step 5: Commit**

```powershell
git add tiktok_bot_console/ui/src/views/ConfigPipeline.vue tiktok_bot_console/ui/src/views/ConfigAccounts.vue tiktok_bot_console/ui/src/api/index.ts tiktok_bot_console/ui/src/api/mock.ts tiktok_bot_console/ui/src/i18n/zh-CN.ts tiktok_bot_console/ui/src/i18n/en-US.ts tiktok_bot_console/ui/scripts/smoke.mjs
git commit -m "feat: add unified pipeline schedules and provider status"
```

---

## Task 10：文档同步与端到端验收

**Files:**

- Modify: `docs/wiki/00-索引.md`
- Modify: `docs/wiki/02-架构设计.md`
- Modify: `docs/wiki/05-Pipeline.md`
- Modify: `docs/wiki/06-CLI-API-UI.md`
- Modify: `docs/wiki/07-数据库.md`
- Modify: `docs/wiki/10-账号管理.md`
- Modify: `docs/wiki/11-双平台支持.md`
- Modify: `docs/wiki/12-测试报告.md`
- Modify: `tiktok_bot_console/ui/README.md`

**Step 1: Run complete verification**

```powershell
python -m pytest tests/ -v
Set-Location tiktok_bot_console/ui
npm run test
npm run type-check
npm run build
```

**Step 2: Update docs**

每份文档顶部日期更新为 `2026-07-26`，并确保：

- 不再描述 `/api/pipeline/run` 为同步执行。
- 明确只有一套 Job/Schedule/API/UI。
- 记录新增表、字段、状态机和 Provider 约束。
- 记录 TikTok 未配置指纹 Provider 时不可运行。
- 测试报告使用实际测试数和实际命令结果。

**Step 3: Restart services**

按 README 方式重启 FastAPI 与 Vite，日志写入 `.runtime-logs`。

**Step 4: Browser acceptance**

验证：

1. 抖音账号出现在统一任务创建器。
2. 抖音任务能创建并进入统一任务列表。
3. TikTok 显示 Provider 未配置且无法提交。
4. 定时计划与手动任务共用历史列表。
5. 页面控制台无错误或警告。

**Step 5: Commit**

只提交本功能涉及的文档和测试，不把已有无关工作区改动混入提交。

```powershell
git add docs/wiki tiktok_bot_console/ui/README.md
git commit -m "docs: document unified pipeline job system"
```

