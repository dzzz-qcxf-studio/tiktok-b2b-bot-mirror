# Hermes H1 Data Integration Implementation Plan

> **For implementer:** Use TDD throughout. Write failing test first. Watch it fail. Then implement.

**Goal:** 原子创建和重试 Hermes 获客任务，并让现有全局业务 API 从同一 Job 级投影读取 AI 与 legacy 数据。

**Architecture:** `PipelineJobService` 仍是唯一 Job 写入口；新增 Acquisition 编排 Service 在一个 SQLAlchemy Session 中创建 Job、Campaign 和 Keywords。新增只读 Business Projection Service 用窗口查询选择每个用户最新 Campaign 关系，并为现有 API 提供兼容字段。

**Tech Stack:** Python 3.11、FastAPI、Pydantic v2、SQLAlchemy 2、SQLite、pytest/httpx。

---

### Task 1: 原子创建 AI 获客任务

**Files:**
- Create: `tiktok_bot_core/services/acquisition_jobs.py`
- Modify: `tiktok_bot_api/main.py`
- Create: `tests/test_acquisition_job_service.py`
- Modify: `tests/test_acquisition_api.py`

**Step 1: Write the failing tests**

- `test_create_job_commits_job_campaign_and_keywords_together`
- `test_create_job_rolls_back_everything_when_keyword_write_fails`
- `test_uncommitted_job_is_not_visible_to_dispatcher_connection`
- `test_atomic_acquisition_job_endpoint_requires_auth_and_returns_complete_contract`
- `test_atomic_acquisition_job_request_requires_collect_and_rejects_duplicate_keywords`

**Step 2: Run tests — confirm RED**

```powershell
python -X utf8 -m pytest tests/test_acquisition_job_service.py tests/test_acquisition_api.py -q
```

Expected: FAIL because `AcquisitionJobService` and `POST /api/acquisition/jobs` do not exist.

**Step 3: Minimal implementation**

Implement `AcquisitionJobService.create_job(...)` with the signature:

```python
async def create_job(
    self,
    *,
    platform: str,
    account_mode: str,
    account_id: int | None,
    stages: list[str],
    config_snapshot: Mapping[str, Any],
    campaign: Mapping[str, Any],
    keywords: Sequence[Mapping[str, Any]],
) -> AcquisitionJobBundle: ...
```

Call `PipelineJobService.preflight_job()` before opening the write transaction, then call `PipelineJobService.create_job(..., _session=session, _preflighted=True)` and persist Campaign/Keywords before commit. Define strict `AcquisitionJobRequest(PipelineJobRequest)` with `campaign`, 1..100 `keywords`, `collect` validation and normalized duplicate-key rejection. Return status 202 with serialized `job/campaign/keywords`.

**Step 4: Run tests — confirm GREEN**

Run the command from Step 2. Expected: all selected tests PASS.

### Task 2: AI 获客重试连续性

**Files:**
- Modify: `tiktok_bot_core/services/pipeline_jobs.py`
- Modify: `tests/test_pipeline_jobs.py`
- Modify: `tests/test_pipeline_api.py`

**Step 1: Write the failing tests**

- `test_retry_acquisition_job_restarts_collect_and_clones_campaign_keywords_atomically`
- `test_retry_legacy_job_keeps_first_failed_stage_semantics`
- `test_retry_acquisition_clone_failure_rolls_back_retry_job`

**Step 2: Run tests — confirm RED**

```powershell
python -X utf8 -m pytest tests/test_pipeline_jobs.py tests/test_pipeline_api.py -q
```

Expected: acquisition retry has no Campaign/Keywords and therefore fails assertions.

**Step 3: Minimal implementation**

Teach `PipelineJobService.retry_job()` to detect `AcquisitionCampaign`. Legacy jobs keep current behavior. Acquisition jobs preflight first, then create retry Job and clone Campaign/keyword definitions in one transaction; retry stages start at the original `collect` stage and counters reset.

**Step 4: Run tests — confirm GREEN**

Run the command from Step 2. Expected: all selected tests PASS.

### Task 3: 统一业务用户投影

**Files:**
- Create: `tiktok_bot_core/services/business_read_model.py`
- Create: `tests/test_business_read_model.py`

**Step 1: Write the failing tests**

- `test_latest_campaign_projection_wins_without_mutating_user`
- `test_engagement_status_overlays_qualification`
- `test_manual_review_and_enrichment_map_to_pending_but_keep_raw_status`
- `test_legacy_user_falls_back_without_campaign`
- `test_projection_filters_counts_and_personas_use_the_same_source`

**Step 2: Run tests — confirm RED**

```powershell
python -X utf8 -m pytest tests/test_business_read_model.py -q
```

Expected: FAIL because `BusinessReadModel` does not exist.

**Step 3: Minimal implementation**

Create immutable `BusinessUserProjection` values and a `BusinessReadModel` that uses a `row_number()` window over Campaign Jobs, plus Message/Reply existence overlays. Implement list/count/status/persona methods with identical filters and stable ordering.

**Step 4: Run tests — confirm GREEN**

Run the command from Step 2. Expected: all selected tests PASS.

### Task 4: 关键词、Lead 和报告投影

**Files:**
- Modify: `tiktok_bot_core/services/business_read_model.py`
- Modify: `tiktok_bot_api/main.py`
- Create: `tests/test_business_api.py`

**Step 1: Write the failing tests**

- `test_users_and_stats_surface_latest_ai_projection`
- `test_dashboard_keyword_and_persona_metrics_include_acquisition_and_legacy_data`
- `test_wordcloud_uses_word_count_contract`
- `test_lead_search_uses_ai_match_score_and_evidence_keyword`
- `test_reports_overview_uses_real_funnel_region_and_sentiment_counts`

**Step 2: Run tests — confirm RED**

```powershell
python -X utf8 -m pytest tests/test_business_api.py -q
```

Expected: existing APIs either omit AI data, use legacy heuristic scores, return `name/value`, or return fixed/empty report values.

**Step 3: Minimal implementation**

Add live acquisition+legacy keyword aggregation and Lead search to `BusinessReadModel`. Change the six existing read endpoints to obtain the active pipeline database through `Depends(get_pipeline_database)` and serialize the shared projection without changing required legacy fields.

**Step 4: Run tests — confirm GREEN**

Run the command from Step 2. Expected: all selected tests PASS.

### Task 5: 文档与 H1 回归

**Files:**
- Modify: `docs/wiki/00-索引.md`
- Modify: `docs/wiki/02-架构设计.md`
- Modify: `docs/wiki/03-Core层.md`
- Modify: `docs/wiki/05-Pipeline.md`
- Modify: `docs/wiki/06-CLI-API-UI.md`
- Modify: `docs/wiki/07-数据库.md`
- Modify: `docs/wiki/12-测试报告.md`

**Step 1: Update documentation mirror**

Record the atomic endpoint, retry rule, projection precedence, API response compatibility and exact test evidence; update all document dates to 2026-08-04.

**Step 2: Run focused regression**

```powershell
python -X utf8 -m pytest tests/test_acquisition_job_service.py tests/test_business_read_model.py tests/test_business_api.py tests/test_acquisition_api.py tests/test_pipeline_jobs.py tests/test_pipeline_api.py -q
```

Expected: PASS.

**Step 3: Run broad regression and static checks**

```powershell
python -X utf8 -m pytest tests -q
python -X utf8 -m py_compile tiktok_bot_api/main.py tiktok_bot_core/services/acquisition_jobs.py tiktok_bot_core/services/business_read_model.py tiktok_bot_core/services/pipeline_jobs.py
git diff --check
```

Expected: all tests PASS, compilation succeeds, diff check has no output. Because the current worktree already contains extensive user changes in the same files, do not create an automatic Git commit that would absorb unrelated work; report the verified diff for user acceptance instead.
