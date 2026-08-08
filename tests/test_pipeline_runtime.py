"""Unified pipeline browser-provider and concurrency runtime tests.

The browser provider tests inject a fake client factory.  They must never
start a real Playwright process.
"""

import asyncio
import gc
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event, func, select

from tiktok_bot_core.browser.providers import (
    BrowserAvailability,
    BrowserProviderRegistry,
    BrowserProviderUnavailableError,
    BrowserSession,
    DouyinPlaywrightProvider,
    UnavailableFingerprintProvider,
)
from tiktok_bot_core.models.entities import (
    PipelineJob,
    PipelineSchedule,
    TikTokAccount,
)
from tiktok_bot_core.models.pipeline_states import (
    JOB_STATUS_CANCELLED,
    JOB_STATUS_FAILED,
    JOB_STATUS_PARTIAL_FAILED,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCEEDED,
    STAGE_STATUS_CANCELLED,
    STAGE_STATUS_FAILED,
    STAGE_STATUS_PENDING,
    STAGE_STATUS_SUCCEEDED,
)
from tiktok_bot_core.services.pipeline_concurrency import (
    PipelineConcurrencyManager,
)
from tiktok_bot_core.storage.database import Database
from tiktok_bot_core.storage.pipeline_job_store import PipelineJobStore


class FakeBrowserClient:
    def __init__(self) -> None:
        self.init = AsyncMock()
        self.close = AsyncMock()
        self._context = SimpleNamespace(add_cookies=AsyncMock())


class AvailableFakeProvider:
    def __init__(self) -> None:
        self.acquired: list[int] = []
        self.released: list[int] = []

    async def check_available(self, account):
        return BrowserAvailability(available=True)

    async def acquire(self, account):
        self.acquired.append(account.id)
        return BrowserSession(
            platform=account.platform,
            account_id=account.id,
            client=SimpleNamespace(),
        )

    async def release(self, session):
        session._released = True
        self.released.append(session.account_id)


class SelectiveFakeProvider(AvailableFakeProvider):
    def __init__(self, bad_account_id: int) -> None:
        super().__init__()
        self.bad_account_id = bad_account_id

    async def check_available(self, account):
        if account.id == self.bad_account_id:
            raise RuntimeError("broken account provider")
        return await super().check_available(account)


class FakePipelineService:
    def __init__(self, results=None, after_stage=None) -> None:
        self.results = dict(results or {})
        self.after_stage = after_stage
        self.calls: list[str] = []

    async def run(self, context, *, stages, **_configs):
        stage = stages[0]
        self.calls.append(stage)
        result = self.results.get(
            stage,
            {"stage": stage, "status": "ok", "result": {"stage": stage}},
        )
        yield result
        if self.after_stage is not None:
            await self.after_stage(stage)


@pytest.fixture
def runtime_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as file:
        path = Path(file.name)
    database = Database(f"sqlite:///{path}")
    database.init()
    yield database
    database.engine.dispose()
    gc.collect()
    try:
        path.unlink()
    except PermissionError:
        pass


def add_logged_in_account(
    database,
    *,
    platform="douyin",
    username="account-1",
    browser_provider="",
    browser_profile_id="",
):
    with database.session() as session:
        account = TikTokAccount(
            platform=platform,
            username=username,
            status="logged_in",
            cookies_json="[]",
            browser_provider=browser_provider,
            browser_profile_id=browser_profile_id,
        )
        session.add(account)
        session.flush()
        return account.id


@pytest.mark.asyncio
async def test_tiktok_unavailable_provider_blocks_acquire():
    provider = UnavailableFingerprintProvider()
    registry = BrowserProviderRegistry(
        {
            "douyin": DouyinPlaywrightProvider(client_factory=FakeBrowserClient),
            "tiktok": provider,
        }
    )
    account = SimpleNamespace(
        id=10,
        platform="tiktok",
        browser_provider="",
        browser_profile_id="",
    )

    availability = await registry.get("tiktok").check_available(account)

    assert availability.available is False
    assert availability.code == "fingerprint_provider_unavailable"
    with pytest.raises(
        BrowserProviderUnavailableError,
        match="fingerprint_provider_unavailable",
    ):
        await registry.get("tiktok").acquire(account)


@pytest.mark.asyncio
async def test_douyin_provider_creates_isolated_sessions():
    created_clients: list[FakeBrowserClient] = []

    def client_factory() -> FakeBrowserClient:
        client = FakeBrowserClient()
        created_clients.append(client)
        return client

    provider = DouyinPlaywrightProvider(client_factory=client_factory)
    account_one = SimpleNamespace(
        id=1,
        platform="douyin",
        cookies_json='[{"name": "sessionid", "value": "one"}]',
    )
    account_two = SimpleNamespace(
        id=2,
        platform="douyin",
        cookies_json='[{"name": "sessionid", "value": "two"}]',
    )

    session_one = await provider.acquire(account_one)
    session_two = await provider.acquire(account_two)

    assert session_one is not session_two
    assert session_one.client is created_clients[0]
    assert session_two.client is created_clients[1]
    assert session_one.client is not session_two.client
    for client in created_clients:
        client.init.assert_awaited_once()
        client._context.add_cookies.assert_awaited_once()

    await provider.release(session_one)
    await provider.release(session_two)
    for client in created_clients:
        client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_concurrency_manager_obeys_douyin_limit():
    manager = PipelineConcurrencyManager(douyin_limit=1)
    first = await manager.acquire("douyin", account_id=1)
    second_task = asyncio.create_task(
        manager.acquire("douyin", account_id=2)
    )
    await asyncio.sleep(0)

    assert not second_task.done()

    await first.release()
    second = await asyncio.wait_for(second_task, timeout=0.5)
    assert manager.active_count("douyin") == 1
    await second.release()
    assert manager.active_count("douyin") == 0


@pytest.mark.asyncio
async def test_same_account_is_never_acquired_twice():
    manager = PipelineConcurrencyManager(douyin_limit=2)
    first = await manager.acquire("douyin", account_id=7)
    duplicate_task = asyncio.create_task(
        manager.acquire("douyin", account_id=7)
    )
    await asyncio.sleep(0)

    assert not duplicate_task.done()

    await first.release()
    duplicate = await asyncio.wait_for(duplicate_task, timeout=0.5)
    assert duplicate.account_id == 7
    await duplicate.release()


@pytest.mark.asyncio
async def test_cancelled_lease_release_does_not_leak_capacity():
    manager = PipelineConcurrencyManager(douyin_limit=1)
    lease = await manager.acquire("douyin", account_id=1)
    release_started = asyncio.Event()
    allow_release = asyncio.Event()
    original_release = manager._release

    async def delayed_release(platform: str, account_id: int) -> None:
        release_started.set()
        await allow_release.wait()
        await original_release(platform, account_id)

    manager._release = delayed_release
    release_task = asyncio.create_task(lease.release())
    await release_started.wait()
    release_task.cancel()
    allow_release.set()

    with pytest.raises(asyncio.CancelledError):
        await release_task

    assert manager.is_account_active("douyin", 1) is False
    replacement = await asyncio.wait_for(
        manager.acquire("douyin", account_id=2),
        timeout=0.5,
    )
    await replacement.release()


@pytest.mark.asyncio
async def test_cancelled_browser_release_still_finishes_close():
    close_started = asyncio.Event()
    allow_close = asyncio.Event()
    close_finished = asyncio.Event()
    client = FakeBrowserClient()

    async def delayed_close() -> None:
        close_started.set()
        await allow_close.wait()
        close_finished.set()

    client.close = AsyncMock(side_effect=delayed_close)
    provider = DouyinPlaywrightProvider(client_factory=lambda: client)
    account = SimpleNamespace(
        id=3,
        platform="douyin",
        cookies_json="[]",
    )
    session = await provider.acquire(account)

    release_task = asyncio.create_task(provider.release(session))
    await close_started.wait()
    release_task.cancel()
    allow_close.set()

    with pytest.raises(asyncio.CancelledError):
        await release_task

    assert close_finished.is_set()
    assert session._released is True


@pytest.mark.asyncio
async def test_browser_close_failure_keeps_session_releasable():
    client = FakeBrowserClient()
    client.close = AsyncMock(
        side_effect=[RuntimeError("close broke"), None]
    )
    provider = DouyinPlaywrightProvider(client_factory=lambda: client)
    account = SimpleNamespace(
        id=4,
        platform="douyin",
        cookies_json="[]",
    )
    session = await provider.acquire(account)

    with pytest.raises(RuntimeError, match="close broke"):
        await provider.release(session)

    assert session._released is False
    await provider.release(session)
    assert session._released is True
    assert client.close.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["tiktok", "unknown"])
async def test_unavailable_platform_limit_fails_fast(platform):
    manager = PipelineConcurrencyManager(douyin_limit=1)

    with pytest.raises(
        RuntimeError,
        match="platform_concurrency_unavailable",
    ):
        await asyncio.wait_for(
            manager.acquire(platform, account_id=5),
            timeout=0.1,
        )


@pytest.mark.asyncio
async def test_explicit_zero_douyin_limit_fails_on_acquire():
    manager = PipelineConcurrencyManager(douyin_limit=0)

    with pytest.raises(
        RuntimeError,
        match="platform_concurrency_unavailable",
    ):
        await manager.acquire("douyin", account_id=5)


@pytest.mark.asyncio
async def test_init_failure_is_not_masked_by_cleanup_failure():
    client = FakeBrowserClient()
    client.init = AsyncMock(side_effect=ValueError("init broke"))
    client.close = AsyncMock(side_effect=RuntimeError("close broke"))
    provider = DouyinPlaywrightProvider(client_factory=lambda: client)
    account = SimpleNamespace(
        id=6,
        platform="douyin",
        cookies_json="[]",
    )

    with pytest.raises(ValueError, match="init broke"):
        await provider.acquire(account)

    client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_invalid_account_id_does_not_acquire_browser_resource():
    client = FakeBrowserClient()
    provider = DouyinPlaywrightProvider(client_factory=lambda: client)
    account = SimpleNamespace(
        id="not-an-integer",
        platform="douyin",
        cookies_json="[]",
    )

    with pytest.raises(ValueError):
        await provider.acquire(account)

    client.init.assert_not_awaited()
    client.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_job_rejects_platform_account_mismatch(runtime_db):
    from tiktok_bot_core.services.pipeline_jobs import (
        PipelineJobError,
        PipelineJobService,
    )

    account_id = add_logged_in_account(runtime_db, platform="douyin")
    service = PipelineJobService(
        database=runtime_db,
        providers=BrowserProviderRegistry(
            {
                "douyin": AvailableFakeProvider(),
                "tiktok": AvailableFakeProvider(),
            }
        ),
    )

    with pytest.raises(PipelineJobError) as error:
        await service.create_job(
            platform="tiktok",
            account_mode="specified",
            account_id=account_id,
            stages=["collect"],
        )

    assert error.value.code == "platform_account_mismatch"
    with runtime_db.session() as session:
        assert session.scalar(select(func.count()).select_from(PipelineJob)) == 0


@pytest.mark.asyncio
async def test_create_tiktok_job_rejects_unavailable_provider(runtime_db):
    from tiktok_bot_core.services.pipeline_jobs import (
        PipelineJobError,
        PipelineJobService,
    )

    account_id = add_logged_in_account(
        runtime_db,
        platform="tiktok",
        browser_provider="fingerprint",
        browser_profile_id="profile-1",
    )
    service = PipelineJobService(
        database=runtime_db,
        providers=BrowserProviderRegistry(
            {"tiktok": UnavailableFingerprintProvider()}
        ),
    )

    with pytest.raises(PipelineJobError) as error:
        await service.create_job(
            platform="tiktok",
            account_mode="specified",
            account_id=account_id,
            stages=["collect"],
        )

    assert error.value.code == "fingerprint_provider_unavailable"
    with runtime_db.session() as session:
        assert session.scalar(select(func.count()).select_from(PipelineJob)) == 0


@pytest.mark.asyncio
async def test_auto_account_waits_when_all_accounts_busy(runtime_db):
    from tiktok_bot_core.services.pipeline_jobs import (
        PipelineDispatcher,
        PipelineJobRunner,
        PipelineJobService,
    )

    account_id = add_logged_in_account(runtime_db)
    provider = AvailableFakeProvider()
    providers = BrowserProviderRegistry({"douyin": provider})
    concurrency = PipelineConcurrencyManager(douyin_limit=1)
    service = PipelineJobService(
        database=runtime_db,
        providers=providers,
        concurrency=concurrency,
    )
    job = await service.create_job(
        platform="douyin",
        account_mode="auto",
        account_id=None,
        stages=["collect"],
    )
    lease = await concurrency.acquire("douyin", account_id)
    runner = PipelineJobRunner(
        database=runtime_db,
        providers=providers,
        concurrency=concurrency,
        pipeline_factory=FakePipelineService,
    )
    dispatcher = PipelineDispatcher(
        database=runtime_db,
        runner=runner,
        providers=providers,
        concurrency=concurrency,
    )

    try:
        assert await dispatcher.dispatch_once() is False
        with runtime_db.session() as session:
            persisted = session.get(PipelineJob, job.id)
            assert persisted.status == JOB_STATUS_QUEUED
            assert persisted.account_id is None
    finally:
        await lease.release()


@pytest.mark.asyncio
async def test_runner_persists_each_stage_transition(runtime_db):
    from tiktok_bot_core.services.pipeline_jobs import (
        PipelineJobRunner,
        PipelineJobService,
    )

    account_id = add_logged_in_account(runtime_db)
    provider = AvailableFakeProvider()
    providers = BrowserProviderRegistry({"douyin": provider})
    concurrency = PipelineConcurrencyManager(douyin_limit=1)
    service = PipelineJobService(
        database=runtime_db,
        providers=providers,
        concurrency=concurrency,
    )
    job = await service.create_job(
        platform="douyin",
        account_mode="specified",
        account_id=account_id,
        stages=["collect", "filter"],
    )
    store = PipelineJobStore()
    with runtime_db.session() as session:
        assert store.claim_job(
            session,
            job.id,
            account_id=account_id,
        )

    pipeline = FakePipelineService()
    runner = PipelineJobRunner(
        database=runtime_db,
        providers=providers,
        concurrency=concurrency,
        pipeline_factory=lambda: pipeline,
    )
    await runner.run_job(job.id)

    with runtime_db.session() as session:
        persisted = session.get(PipelineJob, job.id)
        assert persisted.status == JOB_STATUS_SUCCEEDED
        assert [stage.status for stage in persisted.stages] == [
            STAGE_STATUS_SUCCEEDED,
            STAGE_STATUS_SUCCEEDED,
        ]
        assert [stage.attempt for stage in persisted.stages] == [1, 1]
        assert all(stage.started_at for stage in persisted.stages)
        assert all(stage.finished_at for stage in persisted.stages)
        assert [stage.result_json for stage in persisted.stages] == [
            {"stage": "collect"},
            {"stage": "filter"},
        ]
    assert pipeline.calls == ["collect", "filter"]
    assert provider.acquired == [account_id]
    assert provider.released == [account_id]


@pytest.mark.asyncio
async def test_cancel_stops_at_stage_boundary(runtime_db):
    from tiktok_bot_core.services.pipeline_jobs import (
        PipelineJobRunner,
        PipelineJobService,
    )

    account_id = add_logged_in_account(runtime_db)
    provider = AvailableFakeProvider()
    providers = BrowserProviderRegistry({"douyin": provider})
    concurrency = PipelineConcurrencyManager(douyin_limit=1)
    service = PipelineJobService(
        database=runtime_db,
        providers=providers,
        concurrency=concurrency,
    )
    job = await service.create_job(
        platform="douyin",
        account_mode="specified",
        account_id=account_id,
        stages=["collect", "filter"],
    )
    store = PipelineJobStore()
    with runtime_db.session() as session:
        assert store.claim_job(
            session,
            job.id,
            account_id=account_id,
        )

    async def cancel_after_collect(stage):
        if stage == "collect":
            await service.cancel_job(job.id)

    pipeline = FakePipelineService(after_stage=cancel_after_collect)
    runner = PipelineJobRunner(
        database=runtime_db,
        providers=providers,
        concurrency=concurrency,
        pipeline_factory=lambda: pipeline,
    )
    await runner.run_job(job.id)

    with runtime_db.session() as session:
        persisted = session.get(PipelineJob, job.id)
        assert persisted.status == JOB_STATUS_CANCELLED
        assert [stage.status for stage in persisted.stages] == [
            STAGE_STATUS_SUCCEEDED,
            STAGE_STATUS_CANCELLED,
        ]
    assert pipeline.calls == ["collect"]


@pytest.mark.asyncio
async def test_retry_starts_from_failed_stage(runtime_db):
    from tiktok_bot_core.services.pipeline_jobs import PipelineJobService

    account_id = add_logged_in_account(runtime_db)
    provider = AvailableFakeProvider()
    providers = BrowserProviderRegistry({"douyin": provider})
    service = PipelineJobService(
        database=runtime_db,
        providers=providers,
    )
    original = await service.create_job(
        platform="douyin",
        account_mode="specified",
        account_id=account_id,
        stages=["collect", "filter", "strategy"],
    )
    store = PipelineJobStore()
    with runtime_db.session() as session:
        assert store.claim_job(
            session,
            original.id,
            account_id=account_id,
        )
        store.start_stage(session, original.id, "collect")
        store.finish_stage(
            session,
            original.id,
            "collect",
            STAGE_STATUS_SUCCEEDED,
        )
        store.start_stage(session, original.id, "filter")
        store.finish_stage(
            session,
            original.id,
            "filter",
            STAGE_STATUS_FAILED,
            error="filter failed",
        )
        store.set_job_status(
            session,
            original.id,
            JOB_STATUS_FAILED,
            expected_statuses={JOB_STATUS_RUNNING},
            error_summary="filter failed",
            finished_at=datetime.utcnow(),
        )

    retried = await service.retry_job(original.id)

    assert retried.retry_of_job_id == original.id
    assert retried.stages_json == ["filter", "strategy"]
    assert retried.status == JOB_STATUS_QUEUED


@pytest.mark.asyncio
async def test_scheduler_creates_jobs_in_same_job_table(runtime_db):
    from tiktok_bot_core.services.pipeline_jobs import PipelineJobService
    from tiktok_bot_core.services.pipeline_scheduler import PipelineScheduler

    add_logged_in_account(runtime_db)
    provider = AvailableFakeProvider()
    service = PipelineJobService(
        database=runtime_db,
        providers=BrowserProviderRegistry({"douyin": provider}),
    )
    now = datetime(2026, 7, 26, 4, 5)
    with runtime_db.session() as session:
        schedule = PipelineSchedule(
            name="douyin-every-minute",
            platform="douyin",
            account_mode="auto",
            stages_json=["collect"],
            cron_expression="* * * * *",
            timezone="UTC",
            enabled=True,
            config_json={"collection_config": {"keywords": ["buyer"]}},
            next_run_at=now,
        )
        session.add(schedule)
        session.flush()
        schedule_id = schedule.id

    scheduler = PipelineScheduler(database=runtime_db, job_service=service)
    assert await scheduler.tick(now=now) == 1

    with runtime_db.session() as session:
        jobs = list(session.scalars(select(PipelineJob)))
        schedule = session.get(PipelineSchedule, schedule_id)
        assert len(jobs) == 1
        assert jobs[0].schedule_id == schedule_id
        assert jobs[0].trigger_type == "schedule"
        assert jobs[0].config_snapshot_json == {
            "collection_config": {"keywords": ["buyer"]}
        }
        assert schedule.last_run_at == now
        assert schedule.next_run_at > now


@pytest.mark.asyncio
async def test_scheduler_backfills_only_latest_missed_run(runtime_db):
    from tiktok_bot_core.services.pipeline_jobs import PipelineJobService
    from tiktok_bot_core.services.pipeline_scheduler import PipelineScheduler

    add_logged_in_account(runtime_db)
    provider = AvailableFakeProvider()
    service = PipelineJobService(
        database=runtime_db,
        providers=BrowserProviderRegistry({"douyin": provider}),
    )
    now = datetime(2026, 7, 26, 8, 0)
    with runtime_db.session() as session:
        schedule = PipelineSchedule(
            name="missed-runs",
            platform="douyin",
            account_mode="auto",
            stages_json=["collect"],
            cron_expression="*/5 * * * *",
            timezone="UTC",
            enabled=True,
            config_json={},
            next_run_at=now - timedelta(hours=3),
        )
        session.add(schedule)
        session.flush()
        schedule_id = schedule.id

    scheduler = PipelineScheduler(database=runtime_db, job_service=service)
    assert await scheduler.tick(now=now) == 1
    assert await scheduler.tick(now=now) == 0

    with runtime_db.session() as session:
        jobs = list(
            session.scalars(
                select(PipelineJob).where(
                    PipelineJob.schedule_id == schedule_id
                )
            )
        )
        schedule = session.get(PipelineSchedule, schedule_id)
        assert len(jobs) == 1
        assert schedule.last_run_at == now
        assert schedule.next_run_at == now + timedelta(minutes=5)


@pytest.mark.asyncio
async def test_cancelling_before_runner_entry_releases_claimed_lease(runtime_db):
    from tiktok_bot_core.services.pipeline_jobs import (
        PipelineJobRunner,
        PipelineJobService,
    )

    account_id = add_logged_in_account(runtime_db)
    provider = AvailableFakeProvider()
    providers = BrowserProviderRegistry({"douyin": provider})
    concurrency = PipelineConcurrencyManager(douyin_limit=1)
    service = PipelineJobService(
        database=runtime_db,
        providers=providers,
        concurrency=concurrency,
    )
    job = await service.create_job(
        platform="douyin",
        account_mode="specified",
        account_id=account_id,
        stages=["collect"],
    )
    store = PipelineJobStore()
    lease = await concurrency.acquire("douyin", account_id)
    with runtime_db.session() as session:
        assert store.claim_job(session, job.id, account_id=account_id)
    await service.cancel_job(job.id)

    runner = PipelineJobRunner(
        database=runtime_db,
        providers=providers,
        concurrency=concurrency,
        pipeline_factory=FakePipelineService,
    )
    await runner.run_job(job.id, lease=lease)

    with runtime_db.session() as session:
        persisted = session.get(PipelineJob, job.id)
        assert persisted.status == JOB_STATUS_CANCELLED
        assert persisted.stages[0].status == STAGE_STATUS_CANCELLED
    assert concurrency.is_account_active("douyin", account_id) is False
    assert provider.acquired == []


@pytest.mark.asyncio
async def test_terminal_status_cas_honours_concurrent_cancel(runtime_db):
    from tiktok_bot_core.services.pipeline_jobs import (
        PipelineJobRunner,
        PipelineJobService,
    )

    class CancelBeforeTerminalStore(PipelineJobStore):
        raced = False

        def set_job_status(
            self,
            session,
            job_id,
            status,
            *,
            expected_statuses=None,
            **timestamps,
        ):
            if (
                not self.raced
                and status in {JOB_STATUS_SUCCEEDED, JOB_STATUS_PARTIAL_FAILED}
            ):
                self.raced = True
                self.request_cancel(session, job_id)
                return False
            return super().set_job_status(
                session,
                job_id,
                status,
                expected_statuses=expected_statuses,
                **timestamps,
            )

    account_id = add_logged_in_account(runtime_db)
    provider = AvailableFakeProvider()
    providers = BrowserProviderRegistry({"douyin": provider})
    concurrency = PipelineConcurrencyManager(douyin_limit=1)
    racing_store = CancelBeforeTerminalStore()
    service = PipelineJobService(
        database=runtime_db,
        providers=providers,
    )
    job = await service.create_job(
        platform="douyin",
        account_mode="specified",
        account_id=account_id,
        stages=["collect"],
    )
    with runtime_db.session() as session:
        assert racing_store.claim_job(
            session,
            job.id,
            account_id=account_id,
        )
    runner = PipelineJobRunner(
        database=runtime_db,
        store=racing_store,
        providers=providers,
        concurrency=concurrency,
        pipeline_factory=FakePipelineService,
    )

    await runner.run_job(job.id)

    with runtime_db.session() as session:
        persisted = session.get(PipelineJob, job.id)
        assert persisted.status == JOB_STATUS_CANCELLED
        assert persisted.finished_at is not None


@pytest.mark.asyncio
async def test_concurrent_schedulers_create_one_job_per_occurrence(runtime_db):
    from tiktok_bot_core.services.pipeline_jobs import PipelineJobService
    from tiktok_bot_core.services.pipeline_scheduler import PipelineScheduler

    class BarrierProvider(AvailableFakeProvider):
        def __init__(self):
            super().__init__()
            self.arrivals = 0
            self.both_arrived = asyncio.Event()

        async def check_available(self, account):
            self.arrivals += 1
            if self.arrivals == 2:
                self.both_arrived.set()
            await self.both_arrived.wait()
            return await super().check_available(account)

    add_logged_in_account(runtime_db)
    provider = BarrierProvider()
    service = PipelineJobService(
        database=runtime_db,
        providers=BrowserProviderRegistry({"douyin": provider}),
    )
    now = datetime(2026, 7, 26, 9, 0)
    with runtime_db.session() as session:
        schedule = PipelineSchedule(
            name="one-occurrence",
            platform="douyin",
            account_mode="auto",
            stages_json=["collect"],
            cron_expression="* * * * *",
            timezone="UTC",
            enabled=True,
            config_json={},
            next_run_at=now,
        )
        session.add(schedule)

    first = PipelineScheduler(database=runtime_db, job_service=service)
    second = PipelineScheduler(database=runtime_db, job_service=service)
    results = await asyncio.gather(
        first.tick(now=now),
        second.tick(now=now),
    )

    with runtime_db.session() as session:
        assert session.scalar(
            select(func.count()).select_from(PipelineJob)
        ) == 1
    assert sorted(results) == [0, 1]


@pytest.mark.asyncio
async def test_schedule_advance_failure_rolls_back_job_insert(runtime_db):
    from tiktok_bot_core.services.pipeline_jobs import PipelineJobService
    from tiktok_bot_core.services.pipeline_scheduler import PipelineScheduler

    add_logged_in_account(runtime_db)
    service = PipelineJobService(
        database=runtime_db,
        providers=BrowserProviderRegistry(
            {"douyin": AvailableFakeProvider()}
        ),
    )
    now = datetime(2026, 7, 26, 9, 30)
    with runtime_db.session() as session:
        schedule = PipelineSchedule(
            name="atomic-failure",
            platform="douyin",
            account_mode="auto",
            stages_json=["collect"],
            cron_expression="* * * * *",
            timezone="UTC",
            enabled=True,
            config_json={},
            next_run_at=now,
        )
        session.add(schedule)
        session.flush()
        schedule_id = schedule.id

    should_fail = True

    def fail_schedule_update(
        _connection,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ):
        nonlocal should_fail
        if should_fail and statement.lstrip().upper().startswith("UPDATE"):
            if "pipeline_schedules" in statement:
                should_fail = False
                raise RuntimeError("schedule update failed")

    event.listen(
        runtime_db.engine,
        "before_cursor_execute",
        fail_schedule_update,
    )
    try:
        scheduler = PipelineScheduler(
            database=runtime_db,
            job_service=service,
        )
        assert await scheduler.tick(now=now) == 0
    finally:
        event.remove(
            runtime_db.engine,
            "before_cursor_execute",
            fail_schedule_update,
        )

    with runtime_db.session() as session:
        schedule = session.get(PipelineSchedule, schedule_id)
        assert schedule.next_run_at == now
        assert schedule.last_run_at is None
        assert session.scalar(
            select(func.count()).select_from(PipelineJob)
        ) == 0


@pytest.mark.asyncio
async def test_schedule_disabled_during_preflight_creates_no_job(runtime_db):
    from tiktok_bot_core.services.pipeline_jobs import PipelineJobService
    from tiktok_bot_core.services.pipeline_scheduler import PipelineScheduler

    account_id = add_logged_in_account(runtime_db)
    now = datetime(2026, 7, 26, 10, 0)
    with runtime_db.session() as session:
        schedule = PipelineSchedule(
            name="disable-race",
            platform="douyin",
            account_mode="auto",
            stages_json=["collect"],
            cron_expression="* * * * *",
            timezone="UTC",
            enabled=True,
            config_json={},
            next_run_at=now,
        )
        session.add(schedule)
        session.flush()
        schedule_id = schedule.id

    class DisablingProvider(AvailableFakeProvider):
        async def check_available(self, account):
            assert account.id == account_id
            with runtime_db.session() as session:
                schedule = session.get(PipelineSchedule, schedule_id)
                schedule.enabled = False
            return await super().check_available(account)

    service = PipelineJobService(
        database=runtime_db,
        providers=BrowserProviderRegistry(
            {"douyin": DisablingProvider()}
        ),
    )
    scheduler = PipelineScheduler(database=runtime_db, job_service=service)

    assert await scheduler.tick(now=now) == 0
    with runtime_db.session() as session:
        assert session.scalar(
            select(func.count()).select_from(PipelineJob)
        ) == 0


@pytest.mark.asyncio
async def test_dispatcher_skips_bad_account_and_runs_later_job(runtime_db):
    from tiktok_bot_core.services.pipeline_jobs import (
        PipelineDispatcher,
        PipelineJobRunner,
    )

    bad_account_id = add_logged_in_account(
        runtime_db,
        username="bad-account",
    )
    good_account_id = add_logged_in_account(
        runtime_db,
        username="good-account",
    )
    provider = SelectiveFakeProvider(bad_account_id)
    providers = BrowserProviderRegistry({"douyin": provider})
    concurrency = PipelineConcurrencyManager(douyin_limit=1)
    store = PipelineJobStore()
    with runtime_db.session() as session:
        bad_job = store.create_job(
            session,
            platform="douyin",
            account_mode="specified",
            account_id=bad_account_id,
            stages=["collect"],
            priority=1,
        )
        good_job = store.create_job(
            session,
            platform="douyin",
            account_mode="specified",
            account_id=good_account_id,
            stages=["collect"],
            priority=2,
        )
        bad_job_id = bad_job.id
        good_job_id = good_job.id
    runner = PipelineJobRunner(
        database=runtime_db,
        store=store,
        providers=providers,
        concurrency=concurrency,
        pipeline_factory=FakePipelineService,
    )
    dispatcher = PipelineDispatcher(
        database=runtime_db,
        runner=runner,
        providers=providers,
        concurrency=concurrency,
        store=store,
    )

    assert await dispatcher.dispatch_once() is True
    await asyncio.gather(*list(dispatcher._running_tasks))

    with runtime_db.session() as session:
        assert session.get(PipelineJob, bad_job_id).status == JOB_STATUS_QUEUED
        assert (
            session.get(PipelineJob, good_job_id).status
            == JOB_STATUS_SUCCEEDED
        )


@pytest.mark.asyncio
async def test_dispatcher_loop_survives_poll_exception(runtime_db):
    from tiktok_bot_core.services.pipeline_jobs import (
        PipelineDispatcher,
        PipelineJobRunner,
    )

    provider = AvailableFakeProvider()
    providers = BrowserProviderRegistry({"douyin": provider})
    concurrency = PipelineConcurrencyManager(douyin_limit=1)
    runner = PipelineJobRunner(
        database=runtime_db,
        providers=providers,
        concurrency=concurrency,
        pipeline_factory=FakePipelineService,
    )
    dispatcher = PipelineDispatcher(
        database=runtime_db,
        runner=runner,
        providers=providers,
        concurrency=concurrency,
        poll_interval=0,
    )
    stop_event = asyncio.Event()
    calls = 0

    async def flaky_dispatch_once():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary dispatcher failure")
        stop_event.set()
        return False

    dispatcher.dispatch_once = flaky_dispatch_once
    await dispatcher.run_forever(stop_event)
    assert calls == 2


@pytest.mark.asyncio
async def test_bad_schedule_does_not_block_later_schedule(runtime_db):
    from tiktok_bot_core.services.pipeline_jobs import PipelineJobService
    from tiktok_bot_core.services.pipeline_scheduler import PipelineScheduler

    add_logged_in_account(runtime_db)
    service = PipelineJobService(
        database=runtime_db,
        providers=BrowserProviderRegistry(
            {"douyin": AvailableFakeProvider()}
        ),
    )
    now = datetime(2026, 7, 26, 11, 0)
    with runtime_db.session() as session:
        session.add_all(
            [
                PipelineSchedule(
                    name="bad-cron",
                    platform="douyin",
                    account_mode="auto",
                    stages_json=["collect"],
                    cron_expression="not cron",
                    timezone="UTC",
                    enabled=True,
                    config_json={},
                    next_run_at=now,
                ),
                PipelineSchedule(
                    name="good-cron",
                    platform="douyin",
                    account_mode="auto",
                    stages_json=["collect"],
                    cron_expression="* * * * *",
                    timezone="UTC",
                    enabled=True,
                    config_json={},
                    next_run_at=now,
                ),
            ]
        )

    scheduler = PipelineScheduler(database=runtime_db, job_service=service)
    assert await scheduler.tick(now=now) == 1
    with runtime_db.session() as session:
        jobs = list(session.scalars(select(PipelineJob)))
        schedules = {
            schedule.name: schedule
            for schedule in session.scalars(select(PipelineSchedule))
        }
        assert len(jobs) == 1
        assert jobs[0].schedule_id == schedules["good-cron"].id


@pytest.mark.asyncio
async def test_scheduler_loop_survives_tick_exception(runtime_db):
    from tiktok_bot_core.services.pipeline_jobs import PipelineJobService
    from tiktok_bot_core.services.pipeline_scheduler import PipelineScheduler

    service = PipelineJobService(
        database=runtime_db,
        providers=BrowserProviderRegistry(
            {"douyin": AvailableFakeProvider()}
        ),
    )
    scheduler = PipelineScheduler(
        database=runtime_db,
        job_service=service,
        poll_interval=0,
    )
    stop_event = asyncio.Event()
    calls = 0

    async def flaky_tick():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary scheduler failure")
        stop_event.set()
        return 0

    scheduler.tick = flaky_tick
    await scheduler.run_forever(stop_event)
    assert calls == 2


@pytest.mark.asyncio
async def test_dispatcher_releases_lease_when_claim_raises(runtime_db):
    from tiktok_bot_core.services.pipeline_jobs import (
        PipelineDispatcher,
        PipelineJobRunner,
    )

    class FailFirstClaimStore(PipelineJobStore):
        attempts = 0

        def claim_job(self, session, job_id, *, account_id):
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("database is locked")
            return super().claim_job(
                session,
                job_id,
                account_id=account_id,
            )

    account_id = add_logged_in_account(runtime_db)
    provider = AvailableFakeProvider()
    providers = BrowserProviderRegistry({"douyin": provider})
    concurrency = PipelineConcurrencyManager(douyin_limit=1)
    store = FailFirstClaimStore()
    with runtime_db.session() as session:
        job = store.create_job(
            session,
            platform="douyin",
            account_mode="specified",
            account_id=account_id,
            stages=["collect"],
        )
        job_id = job.id
    runner = PipelineJobRunner(
        database=runtime_db,
        store=store,
        providers=providers,
        concurrency=concurrency,
        pipeline_factory=FakePipelineService,
    )
    dispatcher = PipelineDispatcher(
        database=runtime_db,
        runner=runner,
        providers=providers,
        concurrency=concurrency,
        store=store,
    )

    with pytest.raises(RuntimeError, match="database is locked"):
        await dispatcher.dispatch_once()
    assert concurrency.is_account_active("douyin", account_id) is False
    assert concurrency.active_count("douyin") == 0

    assert await dispatcher.dispatch_once() is True
    await asyncio.gather(*list(dispatcher._running_tasks))
    with runtime_db.session() as session:
        assert session.get(PipelineJob, job_id).status == JOB_STATUS_SUCCEEDED
