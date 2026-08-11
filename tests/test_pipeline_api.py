"""HTTP/CLI contract tests for the unified durable pipeline job system."""

from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from click.testing import CliRunner

import tiktok_bot_api.main as api_main
from tiktok_bot_api.main import app
from tiktok_bot_console.cli.main import cli
from tiktok_bot_core.browser.providers import BrowserAvailability
from tiktok_bot_core.models.entities import PipelineSchedule, TikTokAccount
from tiktok_bot_core.services.pipeline_jobs import PipelineJobError
from tiktok_bot_core.storage.database import Database


@pytest.fixture
def isolated_api_worker_lock(tmp_path, monkeypatch):
    """Keep lifespan tests independent from a running local API service."""

    monkeypatch.setattr(
        api_main,
        "_API_WORKER_LOCK_PATH",
        tmp_path / "api-worker.lock",
    )


def make_job(
    job_id: str,
    *,
    platform: str = "douyin",
    status: str = "queued",
    trigger_type: str = "manual",
    retry_of_job_id: str | None = None,
):
    now = datetime(2026, 7, 26, 12, 0, 0)
    stage = SimpleNamespace(
        id=1,
        stage="collect",
        stage_order=0,
        status="pending",
        attempt=0,
        result_json={},
        error_message="",
        started_at=None,
        finished_at=None,
    )
    return SimpleNamespace(
        id=job_id,
        trigger_type=trigger_type,
        schedule_id=None,
        platform=platform,
        account_mode="auto",
        account_id=None,
        stages_json=["collect"],
        stages=[stage],
        config_snapshot_json={},
        status=status,
        current_stage="",
        priority=100,
        retry_of_job_id=retry_of_job_id,
        error_summary="",
        queued_at=now,
        started_at=None,
        finished_at=None,
        created_at=now,
        updated_at=now,
    )


class FakeProvider:
    def __init__(
        self,
        availability: BrowserAvailability,
        *,
        require_real_account: bool = False,
    ):
        self.availability = availability
        self.require_real_account = require_real_account
        self.checked_accounts = []

    async def check_available(self, account):
        if self.require_real_account:
            assert isinstance(account.id, int)
            assert hasattr(account, "browser_profile_id")
            assert hasattr(account, "cookies_json")
        self.checked_accounts.append(account)
        return self.availability


class FakeProviders:
    def __init__(self):
        self._providers = {
            "douyin": FakeProvider(
                BrowserAvailability(True),
                require_real_account=True,
            ),
            "tiktok": FakeProvider(
                BrowserAvailability(
                    False,
                    "fingerprint_provider_unavailable",
                    "TikTok 指纹浏览器 Provider 尚未配置",
                ),
                require_real_account=True,
            ),
        }

    def get(self, platform):
        return self._providers[platform]


class FakePipelineJobService:
    def __init__(self):
        self.jobs = {"job-1": make_job("job-1", status="failed")}
        self.create_calls = []
        self.providers = FakeProviders()
        self.concurrency = SimpleNamespace(
            _limits={"tiktok": 0, "douyin": 2}
        )

    async def create_job(self, **kwargs):
        if kwargs["platform"] == "tiktok":
            raise PipelineJobError(
                "fingerprint_provider_unavailable",
                "TikTok 指纹浏览器 Provider 尚未配置",
            )
        self.create_calls.append(kwargs)
        job_id = f"job-{len(self.jobs) + 1}"
        job = make_job(
            job_id,
            platform=kwargs["platform"],
            trigger_type=kwargs.get("trigger_type", "manual"),
        )
        job.account_mode = kwargs["account_mode"]
        job.account_id = kwargs["account_id"]
        job.stages_json = list(kwargs["stages"])
        job.stages = [
            SimpleNamespace(
                id=index,
                stage=stage,
                stage_order=index,
                status="pending",
                attempt=0,
                result_json={},
                error_message="",
                started_at=None,
                finished_at=None,
            )
            for index, stage in enumerate(kwargs["stages"], 1)
        ]
        self.jobs[job_id] = job
        return job

    def list_jobs(self, **filters):
        jobs = list(self.jobs.values())
        if filters.get("platform"):
            jobs = [
                job
                for job in jobs
                if job.platform == filters["platform"]
            ]
        if filters.get("status"):
            jobs = [job for job in jobs if job.status == filters["status"]]
        offset = filters.get("offset", 0)
        limit = filters.get("limit", 50)
        return jobs[offset : offset + limit]

    def count_jobs(self, **filters):
        jobs = list(self.jobs.values())
        if filters.get("platform"):
            jobs = [
                job
                for job in jobs
                if job.platform == filters["platform"]
            ]
        if filters.get("status"):
            jobs = [job for job in jobs if job.status == filters["status"]]
        return len(jobs)

    def get_job(self, job_id):
        return self.jobs.get(job_id)

    async def cancel_job(self, job_id):
        job = self.jobs.get(job_id)
        if job is None:
            raise PipelineJobError("job_not_found", "Pipeline job not found")
        job.status = "cancelled"
        return job

    async def retry_job(self, job_id):
        original = self.jobs.get(job_id)
        if original is None:
            raise PipelineJobError("job_not_found", "Pipeline job not found")
        retry = make_job(
            f"job-{len(self.jobs) + 1}",
            platform=original.platform,
            trigger_type="retry",
            retry_of_job_id=job_id,
        )
        self.jobs[retry.id] = retry
        return retry

    async def preflight_job(self, **kwargs):
        if kwargs["platform"] == "tiktok":
            raise PipelineJobError(
                "fingerprint_provider_unavailable",
                "TikTok 指纹浏览器 Provider 尚未配置",
            )


@pytest.fixture(autouse=True)
def isolated_pipeline_dependencies(tmp_path):
    original_service = app.state.pipeline_job_service
    original_database = app.state.pipeline_database
    original_disabled = app.state.pipeline_runtime_disabled

    database = Database(f"sqlite:///{tmp_path / 'pipeline-api.db'}")
    database.init()
    with database.session() as session:
        session.add(
            TikTokAccount(
                platform="douyin",
                username="douyin-test",
                status="logged_in",
            )
        )

    service = FakePipelineJobService()
    app.state.pipeline_job_service = service
    app.state.pipeline_database = database
    app.state.pipeline_runtime_disabled = True
    yield service

    app.state.pipeline_job_service = original_service
    app.state.pipeline_database = original_database
    app.state.pipeline_runtime_disabled = original_disabled


@pytest.fixture
async def api_client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        yield client


@pytest.mark.asyncio
async def test_create_pipeline_job_returns_202(
    api_client,
    isolated_pipeline_dependencies,
):
    response = await api_client.post(
        "/api/pipeline/jobs",
        json={
            "platform": "douyin",
            "accountMode": "specified",
            "accountId": 1,
            "stages": ["collect", "filter"],
        },
    )

    assert response.status_code == 202
    assert response.json()["job"]["platform"] == "douyin"
    assert isolated_pipeline_dependencies.create_calls[0]["stages"] == [
        "collect",
        "filter",
    ]


@pytest.mark.asyncio
async def test_list_and_get_pipeline_jobs(api_client):
    listed = await api_client.get("/api/pipeline/jobs")
    fetched = await api_client.get("/api/pipeline/jobs/job-1")

    assert listed.status_code == 200
    assert "items" in listed.json()
    assert fetched.status_code == 200
    assert fetched.json()["job"]["id"] == "job-1"


@pytest.mark.asyncio
async def test_job_list_total_ignores_page_limit_and_offset(
    api_client,
    isolated_pipeline_dependencies,
):
    isolated_pipeline_dependencies.jobs.update(
        {
            "job-2": make_job("job-2"),
            "job-3": make_job("job-3"),
        }
    )

    response = await api_client.get(
        "/api/pipeline/jobs?limit=1&offset=2"
    )

    assert response.status_code == 200
    assert len(response.json()["items"]) == 1
    assert response.json()["total"] == 3


@pytest.mark.asyncio
async def test_job_list_accepts_waiting_decision_status(
    api_client,
    isolated_pipeline_dependencies,
):
    isolated_pipeline_dependencies.jobs["job-waiting"] = make_job(
        "job-waiting",
        status="waiting_decision",
    )

    response = await api_client.get(
        "/api/pipeline/jobs?status=waiting_decision"
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [
        "job-waiting"
    ]
    assert response.json()["total"] == 1


@pytest.mark.asyncio
async def test_cancel_and_retry_pipeline_job(api_client):
    cancelled = await api_client.post("/api/pipeline/jobs/job-1/cancel")
    retried = await api_client.post("/api/pipeline/jobs/job-1/retry")

    assert cancelled.status_code == 200
    assert cancelled.json()["job"]["status"] == "cancelled"
    assert retried.status_code == 202
    assert retried.json()["job"]["retryOfJobId"] == "job-1"


@pytest.mark.asyncio
async def test_pipeline_capabilities_are_exposed(api_client):
    response = await api_client.get("/api/pipeline/capabilities")

    assert response.status_code == 200
    assert set(response.json()["platforms"]) == {"tiktok", "douyin"}
    douyin_provider = app.state.pipeline_job_service.providers.get("douyin")
    tiktok_provider = app.state.pipeline_job_service.providers.get("tiktok")
    assert [account.id for account in douyin_provider.checked_accounts] == [1]
    assert tiktok_provider.checked_accounts == []


@pytest.mark.asyncio
async def test_schedule_crud_uses_unified_model(api_client):
    created = await api_client.post(
        "/api/pipeline/schedules",
        json={
            "name": "每日抖音任务",
            "platform": "douyin",
            "accountMode": "auto",
            "stages": ["collect"],
            "cronExpression": "0 9 * * *",
            "timezone": "Asia/Shanghai",
            "enabled": True,
        },
    )

    assert created.status_code == 201
    schedule_id = created.json()["schedule"]["id"]

    listed = await api_client.get("/api/pipeline/schedules")
    assert listed.json()["total"] == 1

    updated = await api_client.put(
        f"/api/pipeline/schedules/{schedule_id}",
        json={
            "name": "每小时抖音任务",
            "platform": "douyin",
            "accountMode": "auto",
            "stages": ["collect", "filter"],
            "cronExpression": "0 * * * *",
            "timezone": "Asia/Shanghai",
            "enabled": False,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["schedule"]["enabled"] is False

    deleted = await api_client.delete(
        f"/api/pipeline/schedules/{schedule_id}"
    )
    assert deleted.status_code == 204
    assert (await api_client.get("/api/pipeline/schedules")).json()["total"] == 0


@pytest.mark.asyncio
async def test_disabling_unavailable_tiktok_schedule_skips_preflight(
    api_client,
    isolated_pipeline_dependencies,
):
    database = app.state.pipeline_database
    with database.session() as session:
        schedule = PipelineSchedule(
            name="旧 TikTok 计划",
            platform="tiktok",
            account_mode="specified",
            account_id=None,
            stages_json=["collect"],
            cron_expression="0 9 * * *",
            timezone="Asia/Shanghai",
            enabled=True,
        )
        session.add(schedule)
        session.flush()
        schedule_id = schedule.id

    response = await api_client.put(
        f"/api/pipeline/schedules/{schedule_id}",
        json={
            "name": "旧 TikTok 计划",
            "platform": "tiktok",
            "accountMode": "auto",
            "stages": ["collect"],
            "cronExpression": "0 9 * * *",
            "timezone": "Asia/Shanghai",
            "enabled": False,
        },
    )

    assert response.status_code == 200
    assert response.json()["schedule"]["enabled"] is False


@pytest.mark.asyncio
async def test_create_disabled_tiktok_schedule_skips_preflight(api_client):
    response = await api_client.post(
        "/api/pipeline/schedules",
        json={
            "name": "禁用的 TikTok 草稿",
            "platform": "tiktok",
            "accountMode": "auto",
            "stages": ["collect"],
            "cronExpression": "0 9 * * *",
            "timezone": "Asia/Shanghai",
            "enabled": False,
        },
    )

    assert response.status_code == 201
    assert response.json()["schedule"]["enabled"] is False
    assert response.json()["schedule"]["nextRunAt"] is None


@pytest.mark.asyncio
async def test_missing_schedule_returns_404_before_provider_preflight(api_client):
    response = await api_client.put(
        "/api/pipeline/schedules/99999",
        json={
            "name": "不存在的 TikTok 计划",
            "platform": "tiktok",
            "accountMode": "auto",
            "stages": ["collect"],
            "cronExpression": "0 9 * * *",
            "timezone": "Asia/Shanghai",
            "enabled": True,
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "schedule_not_found"


@pytest.mark.asyncio
async def test_legacy_pipeline_run_creates_job_instead_of_running_inline(api_client):
    response = await api_client.post(
        "/api/pipeline/run",
        json={
            "platform": "douyin",
            "accountMode": "specified",
            "accountId": 1,
            "stages": ["collect"],
        },
    )

    assert response.status_code == 202
    assert response.json()["job"]["triggerType"] == "legacy"


@pytest.mark.asyncio
async def test_request_validation_uses_stable_error_shape(api_client):
    response = await api_client.post(
        "/api/pipeline/jobs",
        json={
            "platform": "instagram",
            "accountMode": "auto",
            "stages": [],
        },
    )

    assert response.status_code == 422
    assert set(response.json()["detail"]) == {"code", "message"}


@pytest.mark.asyncio
async def test_tiktok_provider_error_has_stable_code(api_client):
    response = await api_client.post(
        "/api/pipeline/jobs",
        json={
            "platform": "tiktok",
            "accountMode": "auto",
            "stages": ["collect"],
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == (
        "fingerprint_provider_unavailable"
    )


@pytest.mark.asyncio
async def test_config_returns_default_and_persisted_douyin_concurrency(
    api_client,
    monkeypatch,
):
    monkeypatch.setattr(api_main, "db", app.state.pipeline_database)

    default_response = await api_client.get("/api/config")
    assert default_response.status_code == 200
    assert default_response.json()["douyin_max_concurrency"] == 1

    updated = await api_client.put(
        "/api/config/douyin_max_concurrency",
        json={"value": "3"},
    )
    assert updated.status_code == 200
    assert updated.json()["value"] == "3"
    assert updated.json()["restartRequired"] is True

    persisted_response = await api_client.get("/api/config")
    assert persisted_response.json()["douyin_max_concurrency"] == 3


@pytest.mark.asyncio
async def test_config_round_trips_all_pipeline_keys_with_typed_values(
    api_client,
):
    updates = [
        ("daily_users", "200"),
        ("daily_comment_limit", "40"),
        ("daily_dm_limit", "20"),
        ("comment_interval_max", "12"),
        ("comment_interval_min", "4"),
        ("dm_interval_max", "18"),
        ("dm_interval_min", "6"),
        ("comment_dm_gap_hours", "36"),
        ("tiktok_keywords", '["alpha", " beta ", "alpha"]'),
    ]
    for key, value in updates:
        response = await api_client.put(
            f"/api/config/{key}",
            json={"value": value},
        )
        assert response.status_code == 200

    config = (await api_client.get("/api/config")).json()

    assert config["daily_users"] == 200
    assert config["daily_comment_limit"] == 40
    assert config["daily_dm_limit"] == 20
    assert config["comment_interval_min"] == 4
    assert config["comment_interval_max"] == 12
    assert config["dm_interval_min"] == 6
    assert config["dm_interval_max"] == 18
    assert config["comment_dm_gap_hours"] == 36
    assert config["tiktok_keywords"] == ["alpha", "beta"]


@pytest.mark.asyncio
async def test_config_reads_legacy_comma_separated_keywords(api_client):
    with app.state.pipeline_database.session() as session:
        api_main.store.set_config(
            session,
            "tiktok_keywords",
            " importer, wholesale ,, importer ",
        )

    response = await api_client.get("/api/config")

    assert response.json()["tiktok_keywords"] == ["importer", "wholesale"]


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["0", "-1", "21", "abc", "1.5"])
async def test_douyin_concurrency_rejects_invalid_values_with_stable_error(
    api_client,
    monkeypatch,
    value,
):
    monkeypatch.setattr(api_main, "db", app.state.pipeline_database)

    response = await api_client.put(
        "/api/config/douyin_max_concurrency",
        json={"value": value},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_config_value"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("daily_users", ""),
        ("daily_users", "NaN"),
        ("daily_users", "20.5"),
        ("daily_users", "19"),
        ("daily_users", "501"),
        ("daily_comment_limit", "0"),
        ("daily_comment_limit", "51"),
        ("daily_dm_limit", "31"),
        ("comment_interval_min", "0"),
        ("comment_interval_max", "121"),
        ("dm_interval_min", "61"),
        ("dm_interval_max", "121"),
        ("comment_dm_gap_hours", "5"),
        ("comment_dm_gap_hours", "73"),
    ],
)
async def test_pipeline_numeric_config_rejects_invalid_values(
    api_client,
    key,
    value,
):
    response = await api_client.put(
        f"/api/config/{key}",
        json={"value": value},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_config_value"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("comment_interval_min", "11"),
        ("dm_interval_max", "4"),
    ],
)
async def test_interval_config_rejects_min_greater_than_max(
    api_client,
    key,
    value,
):
    response = await api_client.put(
        f"/api/config/{key}",
        json={"value": value},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_config_value"


@pytest.mark.asyncio
async def test_unknown_config_key_keeps_legacy_string_compatibility(api_client):
    updated = await api_client.put(
        "/api/config/custom_legacy_key",
        json={"value": "legacy-value"},
    )

    assert updated.status_code == 200
    assert updated.json()["value"] == "legacy-value"


def pipeline_config_payload(**overrides):
    payload = {
        "daily_users": 120,
        "daily_comment_limit": 25,
        "daily_dm_limit": 12,
        "comment_interval_min": 3,
        "comment_interval_max": 10,
        "dm_interval_min": 5,
        "dm_interval_max": 15,
        "comment_dm_gap_hours": 24,
        "tiktok_keywords": ["wholesale", "importer"],
        "douyin_max_concurrency": 1,
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_pipeline_config_batch_updates_pairs_across_old_values(api_client):
    payload = pipeline_config_payload(
        comment_interval_min=20,
        comment_interval_max=30,
        dm_interval_min=25,
        dm_interval_max=35,
        douyin_max_concurrency=3,
    )
    response = await api_client.put("/api/config/pipeline", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["config"]["comment_interval_min"] == 20
    assert body["config"]["comment_interval_max"] == 30
    assert body["restartRequired"] is True
    persisted = (await api_client.get("/api/config")).json()
    assert persisted["dm_interval_min"] == 25
    assert persisted["dm_interval_max"] == 35
    unchanged = await api_client.put("/api/config/pipeline", json=payload)
    assert unchanged.json()["restartRequired"] is False


@pytest.mark.asyncio
async def test_pipeline_config_batch_invalid_pair_writes_nothing(api_client):
    before = (await api_client.get("/api/config")).json()

    response = await api_client.put(
        "/api/config/pipeline",
        json=pipeline_config_payload(
            daily_users=300,
            comment_interval_min=40,
            comment_interval_max=30,
        ),
    )

    assert response.status_code == 422
    assert "comment_interval_min" in response.json()["detail"]["message"]
    assert "comment_interval_max" in response.json()["detail"]["message"]
    after = (await api_client.get("/api/config")).json()
    assert after["daily_users"] == before["daily_users"]
    assert after["comment_interval_min"] == before["comment_interval_min"]
    assert after["comment_interval_max"] == before["comment_interval_max"]


@pytest.mark.asyncio
async def test_pipeline_config_batch_rolls_back_mid_write_error(
    api_client,
    monkeypatch,
):
    original_set_config = api_main.store.set_config
    call_count = 0

    def fail_during_write(session, key, value, description=""):
        nonlocal call_count
        call_count += 1
        if call_count == 4:
            raise RuntimeError("injected config write failure")
        return original_set_config(session, key, value, description)

    monkeypatch.setattr(api_main.store, "set_config", fail_during_write)
    with pytest.raises(RuntimeError, match="injected config write failure"):
        await api_client.put(
            "/api/config/pipeline",
            json=pipeline_config_payload(daily_users=300),
        )

    with app.state.pipeline_database.session() as session:
        records = {
            record.key: record.value
            for record in api_main.store.list_configs(session)
        }
    assert records == {}


def test_runtime_concurrency_helper_reads_persisted_value(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'runtime-config.db'}")
    database.init()
    with database.session() as session:
        api_main.store.set_config(
            session,
            "douyin_max_concurrency",
            "4",
        )

    assert api_main._load_douyin_max_concurrency(database) == 4


@pytest.mark.asyncio
async def test_accounts_api_includes_browser_provider_profile(
    api_client,
    monkeypatch,
    tmp_path,
):
    from tiktok_bot_core.services.auth_service import AuthService

    database = Database(f"sqlite:///{tmp_path / 'accounts-api.db'}")
    database.init()
    with database.session() as session:
        session.add(
            TikTokAccount(
                platform="tiktok",
                username="provider-account",
                status="logged_in",
                browser_provider="fingerprint-vendor",
                browser_profile_id="profile-42",
            )
        )
    auth_service = object.__new__(AuthService)
    auth_service.db = database
    auth_service.store = api_main.store
    monkeypatch.setattr(
        api_main,
        "get_auth_service",
        lambda: auth_service,
    )

    response = await api_client.get("/api/accounts?platform=tiktok")

    assert response.status_code == 200
    assert response.json()[0]["browser_provider"] == "fingerprint-vendor"
    assert response.json()[0]["browser_profile_id"] == "profile-42"


@pytest.mark.asyncio
async def test_account_metadata_api_updates_only_local_display_name(
    api_client,
    monkeypatch,
    tmp_path,
):
    from tiktok_bot_core.services.auth_service import AuthService

    database = Database(f"sqlite:///{tmp_path / 'account-metadata.db'}")
    database.init()
    with database.session() as session:
        account = TikTokAccount(
            platform="douyin",
            username="browser-isolation-key",
            display_name="旧备注",
            nickname="平台昵称",
            avatar_url="https://p3.douyinpic.com/avatar.jpeg",
            status="logged_in",
        )
        session.add(account)
        session.flush()
        account_id = account.id

    auth_service = object.__new__(AuthService)
    auth_service.db = database
    auth_service.store = api_main.store
    monkeypatch.setattr(api_main, "get_auth_service", lambda: auth_service)

    response = await api_client.put(
        f"/api/accounts/{account_id}",
        json={"displayName": "重点客户号"},
    )

    assert response.status_code == 200
    assert response.json()["display_name"] == "重点客户号"
    assert response.json()["username"] == "browser-isolation-key"
    assert response.json()["nickname"] == "平台昵称"
    assert response.json()["avatar_url"].startswith("https://")

    with database.session() as session:
        stored = api_main.store.get_tiktok_account(session, account_id)
        assert stored.display_name == "重点客户号"
        assert stored.username == "browser-isolation-key"


@pytest.mark.asyncio
async def test_tiktok_session_check_is_unsupported_without_expiring_account(
    api_client,
    monkeypatch,
    tmp_path,
):
    database = Database(f"sqlite:///{tmp_path / 'tiktok-check.db'}")
    database.init()
    with database.session() as session:
        account = TikTokAccount(
            platform="tiktok",
            username="tiktok-browser-key",
            status="logged_in",
        )
        session.add(account)
        session.flush()
        account_id = account.id

    auth_service = SimpleNamespace(check_session_valid=AsyncMock())
    monkeypatch.setattr(api_main, "db", database)
    monkeypatch.setattr(api_main, "get_auth_service", lambda: auth_service)

    response = await api_client.post(
        f"/api/accounts/{account_id}/check-session"
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": account_id,
        "status": "logged_in",
        "valid": None,
        "supported": False,
        "code": "session_check_unsupported",
    }
    auth_service.check_session_valid.assert_not_awaited()
    with database.session() as session:
        stored = api_main.store.get_tiktok_account(session, account_id)
        assert stored.status == "logged_in"


def test_cli_pipeline_run_accepts_unified_platform_and_account_options(
    monkeypatch,
):
    fake_service = FakePipelineJobService()
    import tiktok_bot_core.services.pipeline_jobs as pipeline_jobs

    monkeypatch.setattr(
        pipeline_jobs,
        "PipelineJobService",
        lambda **_kwargs: fake_service,
    )
    result = CliRunner().invoke(
        cli,
        [
            "pipeline",
            "run",
            "--platform",
            "douyin",
            "--account-mode",
            "auto",
            "--stages",
            "collect",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["job"]["platform"] == "douyin"
    assert payload["job"]["id"]


@pytest.mark.asyncio
async def test_lifespan_starts_and_stops_single_runtime(
    monkeypatch,
    isolated_api_worker_lock,
):
    runtime = SimpleNamespace(
        start=AsyncMock(),
        stop=AsyncMock(),
    )
    original_runtime = app.state.pipeline_runtime
    app.state.pipeline_runtime = runtime
    app.state.pipeline_runtime_disabled = False
    monkeypatch.delenv("TIKTOK_BOT_DISABLE_PIPELINE_RUNTIME", raising=False)
    try:
        async with app.router.lifespan_context(app):
            runtime.start.assert_awaited_once()
        runtime.stop.assert_awaited_once()
    finally:
        app.state.pipeline_runtime = original_runtime


@pytest.mark.asyncio
async def test_lifespan_can_disable_background_runtime(
    isolated_api_worker_lock,
):
    runtime = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
    original_runtime = app.state.pipeline_runtime
    app.state.pipeline_runtime = runtime
    app.state.pipeline_runtime_disabled = True
    try:
        async with app.router.lifespan_context(app):
            pass
        runtime.start.assert_not_awaited()
        runtime.stop.assert_not_awaited()
    finally:
        app.state.pipeline_runtime = original_runtime


@pytest.mark.asyncio
async def test_lifespan_closes_initialized_llm_router(
    monkeypatch,
    isolated_api_worker_lock,
):
    close_router = AsyncMock()
    monkeypatch.setattr(
        api_main,
        "aclose_llm_router",
        close_router,
        raising=False,
    )
    original_disabled = app.state.pipeline_runtime_disabled
    app.state.pipeline_runtime_disabled = True
    try:
        async with app.router.lifespan_context(app):
            pass
        close_router.assert_awaited_once()
    finally:
        app.state.pipeline_runtime_disabled = original_disabled
