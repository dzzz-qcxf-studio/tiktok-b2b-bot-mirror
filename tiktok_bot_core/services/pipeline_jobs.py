"""Unified durable job service and execution runtime.

SQLite is the only queue authority.  The dispatcher only keeps references to
currently running asyncio tasks so resources can be released during shutdown.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
import logging
from types import SimpleNamespace
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from tiktok_bot_core.browser.providers import (
    BrowserProviderRegistry,
    BrowserProviderUnavailableError,
)
from tiktok_bot_core.models.entities import (
    PipelineJob,
    PipelineJobStage,
    TikTokAccount,
)
from tiktok_bot_core.models.pipeline_states import (
    JOB_STATUS_CANCELLED,
    JOB_STATUS_CANCELLING,
    JOB_STATUS_FAILED,
    JOB_STATUS_INTERRUPTED,
    JOB_STATUS_PARTIAL_FAILED,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCEEDED,
    STAGE_STATUS_FAILED,
    STAGE_STATUS_RUNNING,
    STAGE_STATUS_SUCCEEDED,
    TERMINAL_JOB_STATUSES,
)
from tiktok_bot_core.services.pipeline import PipelineRunContext, PipelineService
from tiktok_bot_core.services.pipeline_concurrency import (
    ConcurrencyLease,
    PipelineConcurrencyManager,
)
from tiktok_bot_core.storage.database import Database, get_db
from tiktok_bot_core.storage.acquisition_store import AcquisitionStore
from tiktok_bot_core.storage.pipeline_job_store import PipelineJobStore

logger = logging.getLogger(__name__)

SUPPORTED_PLATFORMS = frozenset({"tiktok", "douyin"})
ACCOUNT_MODES = frozenset({"auto", "specified"})


class PipelineJobError(RuntimeError):
    """Stable service-layer error consumed by the HTTP API."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class PipelineJobService:
    """The sole create/cancel/retry/query entry point for pipeline jobs."""

    def __init__(
        self,
        *,
        database: Database | None = None,
        store: PipelineJobStore | None = None,
        acquisition_store: AcquisitionStore | None = None,
        providers: BrowserProviderRegistry | None = None,
        concurrency: PipelineConcurrencyManager | None = None,
    ) -> None:
        self.database = database or get_db()
        self.store = store or PipelineJobStore()
        self.acquisition_store = acquisition_store or AcquisitionStore()
        self.providers = providers or BrowserProviderRegistry()
        self.concurrency = concurrency

    async def create_job(
        self,
        *,
        platform: str,
        account_mode: str,
        account_id: int | None,
        stages: list[str],
        trigger_type: str = "manual",
        schedule_id: int | None = None,
        priority: int = 100,
        config_snapshot: dict[str, Any] | None = None,
        retry_of_job_id: str | None = None,
        _session: Session | None = None,
        _preflighted: bool = False,
    ) -> PipelineJob:
        platform = self._validate_job_request(
            platform=platform,
            account_mode=account_mode,
            account_id=account_id,
        )
        if not _preflighted:
            await self.preflight_job(
                platform=platform,
                account_mode=account_mode,
                account_id=account_id,
            )

        create_args = {
            "platform": platform,
            "account_mode": account_mode,
            "account_id": account_id,
            "stages": stages,
            "trigger_type": trigger_type,
            "schedule_id": schedule_id,
            "priority": priority,
            "config_snapshot": config_snapshot,
            "retry_of_job_id": retry_of_job_id,
        }
        if _session is not None:
            return self.store.create_job(_session, **create_args)

        with self.database.session() as session:
            job = self.store.create_job(session, **create_args)
            session.expunge(job)
        return job

    async def preflight_job(
        self,
        *,
        platform: str,
        account_mode: str,
        account_id: int | None,
    ) -> None:
        """Validate account/provider availability without creating a job."""

        platform = self._validate_job_request(
            platform=platform,
            account_mode=account_mode,
            account_id=account_id,
        )
        with self.database.session() as session:
            accounts = self._eligible_accounts(
                session,
                platform=platform,
                account_mode=account_mode,
                account_id=account_id,
            )
            await self._check_provider_preflight(platform, accounts)

    @staticmethod
    def _validate_job_request(
        *,
        platform: str,
        account_mode: str,
        account_id: int | None,
    ) -> str:
        platform = _platform_name(platform)
        if platform not in SUPPORTED_PLATFORMS:
            raise PipelineJobError(
                "invalid_platform",
                f"Unsupported pipeline platform: {platform}",
            )
        if account_mode not in ACCOUNT_MODES:
            raise PipelineJobError(
                "invalid_account_mode",
                f"Unsupported account mode: {account_mode}",
            )
        if account_mode == "specified" and account_id is None:
            raise PipelineJobError(
                "account_required",
                "Specified account mode requires account_id",
            )
        if account_mode == "auto" and account_id is not None:
            raise PipelineJobError(
                "auto_account_id_forbidden",
                "Auto account mode must not specify account_id",
            )
        return platform

    async def cancel_job(self, job_id: str) -> PipelineJob:
        with self.database.session() as session:
            job = self.store.request_cancel(session, job_id)
            if job is None:
                raise PipelineJobError("job_not_found", "Pipeline job not found")
            session.expunge(job)
        return job

    async def retry_job(self, job_id: str) -> PipelineJob:
        with self.database.session() as session:
            original = self.store.get_job(session, job_id)
            if original is None:
                raise PipelineJobError("job_not_found", "Pipeline job not found")
            if original.status not in {
                JOB_STATUS_FAILED,
                JOB_STATUS_PARTIAL_FAILED,
                JOB_STATUS_INTERRUPTED,
            }:
                raise PipelineJobError(
                    "job_not_retryable",
                    f"Pipeline job status is not retryable: {original.status}",
                )
            ordered_stages = sorted(
                original.stages,
                key=lambda stage: stage.stage_order,
            )
            campaign = self.acquisition_store.get_campaign(session, original.id)
            campaign_snapshot = None
            keyword_snapshots: list[dict[str, Any]] = []
            if campaign is None:
                first_failed = next(
                    (
                        index
                        for index, stage in enumerate(ordered_stages)
                        if stage.status == STAGE_STATUS_FAILED
                    ),
                    0,
                )
                retry_stages = [
                    stage.stage for stage in ordered_stages[first_failed:]
                ]
            else:
                collect_index = next(
                    (
                        index
                        for index, stage in enumerate(ordered_stages)
                        if stage.stage == "collect"
                    ),
                    None,
                )
                if collect_index is None:
                    raise PipelineJobError(
                        "acquisition_retry_collect_required",
                        "Acquisition pipeline retry requires a collect stage",
                    )
                retry_stages = [
                    stage.stage for stage in ordered_stages[collect_index:]
                ]
                campaign_snapshot = {
                    "platform": campaign.platform,
                    "countries": deepcopy(campaign.countries or []),
                    "languages": deepcopy(campaign.languages or []),
                    "industries": deepcopy(campaign.industries or []),
                    "products": deepcopy(campaign.products or []),
                    "customer_roles": deepcopy(campaign.customer_roles or []),
                    "hard_conditions": deepcopy(campaign.hard_conditions or {}),
                    "preference_conditions": deepcopy(
                        campaign.preference_conditions or {}
                    ),
                    "excluded_targets": deepcopy(
                        campaign.excluded_targets or []
                    ),
                    "search_budget": deepcopy(campaign.search_budget or {}),
                    "keyword_mix": deepcopy(campaign.keyword_mix or {}),
                }
                keyword_snapshots = [
                    {
                        "platform": keyword.platform,
                        "text": keyword.text,
                        "language": keyword.language,
                        "keyword_type": keyword.keyword_type,
                        "source": keyword.source,
                        "status": keyword.status,
                    }
                    for keyword in self.acquisition_store.list_keywords(
                        session,
                        original.id,
                    )
                ]
            account_id = (
                original.account_id
                if original.account_mode == "specified"
                else None
            )
            retry_args = {
                "platform": original.platform,
                "account_mode": original.account_mode,
                "account_id": account_id,
                "stages": retry_stages,
                "trigger_type": "retry",
                "priority": original.priority,
                "config_snapshot": deepcopy(
                    original.config_snapshot_json or {}
                ),
                "retry_of_job_id": original.id,
            }
        if campaign_snapshot is None:
            return await self.create_job(**retry_args)

        await self.preflight_job(
            platform=retry_args["platform"],
            account_mode=retry_args["account_mode"],
            account_id=retry_args["account_id"],
        )
        with self.database.session() as session:
            retried = await self.create_job(
                **retry_args,
                _session=session,
                _preflighted=True,
            )
            self.acquisition_store.create_campaign(
                session,
                job_id=retried.id,
                **campaign_snapshot,
            )
            for keyword_snapshot in keyword_snapshots:
                self.acquisition_store.create_keyword(
                    session,
                    job_id=retried.id,
                    **keyword_snapshot,
                )
            _load_job(retried)
            session.expunge(retried)
        return retried

    def get_job(self, job_id: str) -> PipelineJob | None:
        with self.database.session() as session:
            job = self.store.get_job(session, job_id)
            if job is not None:
                _load_job(job)
                session.expunge(job)
            return job

    def list_jobs(
        self,
        *,
        platform: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[PipelineJob]:
        with self.database.session() as session:
            jobs = self.store.list_jobs(
                session,
                platform=platform,
                status=status,
                limit=limit,
                offset=offset,
            )
            for job in jobs:
                _load_job(job)
                session.expunge(job)
            return jobs

    def count_jobs(
        self,
        *,
        platform: str | None = None,
        status: str | None = None,
    ) -> int:
        with self.database.session() as session:
            return self.store.count_jobs(
                session,
                platform=platform,
                status=status,
            )

    def _eligible_accounts(
        self,
        session,
        *,
        platform: str,
        account_mode: str,
        account_id: int | None,
    ) -> list[TikTokAccount]:
        if account_mode == "specified":
            account = session.get(TikTokAccount, account_id)
            if account is None:
                raise PipelineJobError(
                    "account_not_found",
                    "Social platform account not found",
                )
            if account.platform != platform:
                raise PipelineJobError(
                    "platform_account_mismatch",
                    "Account does not belong to the selected platform",
                )
            if account.status != "logged_in":
                raise PipelineJobError(
                    "account_not_logged_in",
                    "Selected account is not logged in",
                )
            return [account]

        accounts = list(
            session.scalars(
                select(TikTokAccount)
                .where(
                    TikTokAccount.platform == platform,
                    TikTokAccount.status == "logged_in",
                )
                .order_by(
                    TikTokAccount.last_login_at.desc().nullslast(),
                    TikTokAccount.id.asc(),
                )
            )
        )
        if not accounts:
            raise PipelineJobError(
                "no_available_account",
                f"No logged-in {platform} account is available",
            )
        return accounts

    async def _check_provider_preflight(
        self,
        platform: str,
        accounts: list[TikTokAccount],
    ) -> None:
        provider = self.providers.get(platform)
        first_failure = None
        for account in accounts:
            availability = await provider.check_available(account)
            if not availability.available:
                first_failure = first_failure or availability
                continue
            if platform == "tiktok" and (
                not account.browser_provider or not account.browser_profile_id
            ):
                first_failure = SimpleNamespace(
                    code="fingerprint_profile_required",
                    message=(
                        "TikTok account requires a fingerprint provider "
                        "and browser profile"
                    ),
                )
                continue
            return

        if first_failure is not None:
            raise PipelineJobError(
                first_failure.code or "browser_provider_unavailable",
                first_failure.message or "Browser provider is unavailable",
            )
        raise PipelineJobError(
            "browser_provider_unavailable",
            f"No {platform} browser provider is available",
        )


class PipelineJobRunner:
    """Execute one already claimed job and persist every stage boundary."""

    def __init__(
        self,
        *,
        database: Database | None = None,
        store: PipelineJobStore | None = None,
        providers: BrowserProviderRegistry | None = None,
        concurrency: PipelineConcurrencyManager | None = None,
        pipeline_factory: Callable[[], PipelineService] = PipelineService,
    ) -> None:
        self.database = database or get_db()
        self.store = store or PipelineJobStore()
        self.providers = providers or BrowserProviderRegistry()
        self.concurrency = concurrency or PipelineConcurrencyManager()
        self.pipeline_factory = pipeline_factory

    async def run_job(
        self,
        job_id: str,
        *,
        lease: ConcurrencyLease | None = None,
    ) -> None:
        owned_lease = lease
        account = None
        browser_session = None
        try:
            account = self._load_runnable_job_account(job_id)
            if account is None:
                self._finish_cancellation(job_id)
                return
            if owned_lease is None:
                owned_lease = await self.concurrency.acquire(
                    account.platform,
                    account.id,
                )
            provider = self.providers.get(account.platform)
            browser_session = await provider.acquire(account)
            context = PipelineRunContext(
                job_id=job_id,
                platform=account.platform,
                account_id=account.id,
                account_username=account.username,
                browser_session=browser_session,
            )
            pipeline = self.pipeline_factory()
            failed_stages: list[str] = []

            for stage in self._ordered_stages(job_id):
                if self._is_cancelling(job_id):
                    self._finish_cancellation(job_id)
                    return
                with self.database.session() as session:
                    started = self.store.start_stage(session, job_id, stage)
                    if started is None:
                        raise RuntimeError(
                            f"Could not start pipeline stage: {stage}"
                        )

                outcome = None
                config = self._job_config(job_id)
                async for item in pipeline.run(
                    context,
                    stages=[stage],
                    collection_config=config.get("collection_config"),
                    strategy_config=config.get("strategy_config"),
                    outreach_config=config.get("outreach_config"),
                ):
                    outcome = item
                if outcome is None:
                    outcome = {
                        "status": "error",
                        "result": {"error": "Pipeline stage returned no result"},
                    }

                result = dict(outcome.get("result") or {})
                if outcome.get("status") == "ok":
                    stage_status = STAGE_STATUS_SUCCEEDED
                    error = ""
                else:
                    stage_status = STAGE_STATUS_FAILED
                    error = str(result.get("error") or "Pipeline stage failed")
                    failed_stages.append(stage)
                with self.database.session() as session:
                    finished = self.store.finish_stage(
                        session,
                        job_id,
                        stage,
                        stage_status,
                        result=result,
                        error=error,
                    )
                    if finished is None:
                        raise RuntimeError(
                            f"Could not finish pipeline stage: {stage}"
                        )

                if self._is_cancelling(job_id):
                    self._finish_cancellation(job_id)
                    return

            final_status = (
                JOB_STATUS_PARTIAL_FAILED
                if failed_stages
                else JOB_STATUS_SUCCEEDED
            )
            with self.database.session() as session:
                completed = self.store.set_job_status(
                    session,
                    job_id,
                    final_status,
                    expected_statuses={JOB_STATUS_RUNNING},
                    error_summary=", ".join(failed_stages),
                    finished_at=_utcnow(),
                )
            if not completed:
                if self._is_cancelling(job_id):
                    self._finish_cancellation(job_id)
                    return
                with self.database.session() as session:
                    job = self.store.get_job(session, job_id)
                    actual_status = job.status if job is not None else "missing"
                if actual_status != final_status:
                    raise RuntimeError(
                        "Pipeline terminal status CAS failed: "
                        f"expected {final_status}, found {actual_status}"
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Pipeline job %s failed internally", job_id)
            self._finish_internal_error(job_id, exc)
        finally:
            cleanup_cancelled = False
            if browser_session is not None and account is not None:
                try:
                    await self.providers.get(account.platform).release(
                        browser_session
                    )
                except asyncio.CancelledError:
                    cleanup_cancelled = True
                except Exception:
                    logger.exception(
                        "Failed to release browser session for job %s",
                        job_id,
                    )
            if owned_lease is not None:
                try:
                    await owned_lease.release()
                except asyncio.CancelledError:
                    cleanup_cancelled = True
                except Exception:
                    logger.exception(
                        "Failed to release concurrency lease for job %s",
                        job_id,
                    )
            if cleanup_cancelled:
                raise asyncio.CancelledError

    def _load_runnable_job_account(
        self,
        job_id: str,
    ) -> SimpleNamespace | None:
        with self.database.session() as session:
            job = self.store.get_job(session, job_id)
            if job is None:
                raise PipelineJobError("job_not_found", "Pipeline job not found")
            if job.status == JOB_STATUS_CANCELLING:
                return None
            if job.status != JOB_STATUS_RUNNING:
                raise PipelineJobError(
                    "job_not_running",
                    f"Pipeline job is not running: {job.status}",
                )
            if job.account_id is None:
                raise PipelineJobError(
                    "account_not_assigned",
                    "Pipeline job has no assigned account",
                )
            account = session.get(TikTokAccount, job.account_id)
            if account is None:
                raise PipelineJobError(
                    "account_not_found",
                    "Assigned social platform account not found",
                )
            return _account_snapshot(account)

    def _ordered_stages(self, job_id: str) -> list[str]:
        with self.database.session() as session:
            job = self.store.get_job(session, job_id)
            if job is None:
                raise PipelineJobError("job_not_found", "Pipeline job not found")
            return [
                stage.stage
                for stage in sorted(
                    job.stages,
                    key=lambda row: row.stage_order,
                )
            ]

    def _job_config(self, job_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            job = self.store.get_job(session, job_id)
            return dict(job.config_snapshot_json or {}) if job else {}

    def _is_cancelling(self, job_id: str) -> bool:
        with self.database.session() as session:
            job = self.store.get_job(session, job_id)
            return bool(job and job.status == JOB_STATUS_CANCELLING)

    def _finish_cancellation(self, job_id: str) -> None:
        with self.database.session() as session:
            self.store.cancel_pending_stages(session, job_id)
            self.store.set_job_status(
                session,
                job_id,
                JOB_STATUS_CANCELLED,
                expected_statuses={JOB_STATUS_CANCELLING},
                finished_at=_utcnow(),
            )

    def _finish_internal_error(self, job_id: str, exc: Exception) -> None:
        with self.database.session() as session:
            job = self.store.get_job(session, job_id)
            if job is None or job.status in TERMINAL_JOB_STATUSES:
                return
            running_stages = list(
                session.scalars(
                    select(PipelineJobStage).where(
                        PipelineJobStage.job_id == job_id,
                        PipelineJobStage.status == STAGE_STATUS_RUNNING,
                    )
                )
            )
            for stage in running_stages:
                self.store.finish_stage(
                    session,
                    job_id,
                    stage.stage,
                    STAGE_STATUS_FAILED,
                    error=str(exc),
                )
            if job.status == JOB_STATUS_CANCELLING:
                self.store.cancel_pending_stages(session, job_id)
                self.store.set_job_status(
                    session,
                    job_id,
                    JOB_STATUS_CANCELLED,
                    expected_statuses={JOB_STATUS_CANCELLING},
                    finished_at=_utcnow(),
                )
                return
            self.store.set_job_status(
                session,
                job_id,
                JOB_STATUS_FAILED,
                expected_statuses={JOB_STATUS_RUNNING},
                error_summary=str(exc),
                finished_at=_utcnow(),
            )


class PipelineDispatcher:
    """Poll the SQLite queue and dispatch jobs with available account slots."""

    def __init__(
        self,
        *,
        database: Database | None = None,
        runner: PipelineJobRunner,
        providers: BrowserProviderRegistry,
        concurrency: PipelineConcurrencyManager,
        store: PipelineJobStore | None = None,
        poll_interval: float = 0.25,
    ) -> None:
        self.database = database or get_db()
        self.runner = runner
        self.providers = providers
        self.concurrency = concurrency
        self.store = store or PipelineJobStore()
        self.poll_interval = poll_interval
        self._running_tasks: set[asyncio.Task] = set()

    async def dispatch_once(self) -> bool:
        for job in self._queued_job_snapshots():
            try:
                accounts = self._candidate_accounts(job)
            except Exception:
                logger.exception(
                    "Could not load accounts for pipeline job %s",
                    job.id,
                )
                continue
            for account in accounts:
                if self.concurrency.is_account_active(
                    account.platform,
                    account.id,
                ):
                    continue
                try:
                    availability = await self.providers.get(
                        account.platform
                    ).check_available(account)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        "Browser preflight failed for account %s",
                        account.id,
                    )
                    continue
                if not availability.available:
                    continue
                lease = await self.concurrency.try_acquire(
                    account.platform,
                    account.id,
                )
                if lease is None:
                    continue
                lease_transferred = False
                try:
                    with self.database.session() as session:
                        claimed = self.store.claim_job(
                            session,
                            job.id,
                            account_id=account.id,
                        )
                    if not claimed:
                        continue
                    task = asyncio.create_task(
                        self.runner.run_job(job.id, lease=lease),
                        name=f"pipeline-job-{job.id}",
                    )
                    lease_transferred = True
                    self._running_tasks.add(task)
                    task.add_done_callback(self._task_finished)
                    return True
                finally:
                    if not lease_transferred:
                        await lease.release()
        return False

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            try:
                dispatched = await self.dispatch_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Pipeline dispatcher poll failed")
                dispatched = False
            if dispatched:
                continue
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=self.poll_interval,
                )
            except asyncio.TimeoutError:
                pass

    async def stop(self) -> None:
        tasks = list(self._running_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._running_tasks.clear()

    def _queued_job_snapshots(self) -> list[SimpleNamespace]:
        with self.database.session() as session:
            jobs = list(
                session.scalars(
                    select(PipelineJob)
                    .where(PipelineJob.status == JOB_STATUS_QUEUED)
                    .order_by(
                        PipelineJob.priority.asc(),
                        PipelineJob.queued_at.asc(),
                        PipelineJob.created_at.asc(),
                        PipelineJob.id.asc(),
                    )
                )
            )
            return [
                SimpleNamespace(
                    id=job.id,
                    platform=job.platform,
                    account_mode=job.account_mode,
                    account_id=job.account_id,
                )
                for job in jobs
            ]

    def _candidate_accounts(
        self,
        job: SimpleNamespace,
    ) -> list[SimpleNamespace]:
        with self.database.session() as session:
            statement = select(TikTokAccount).where(
                TikTokAccount.platform == job.platform,
                TikTokAccount.status == "logged_in",
            )
            if job.account_mode == "specified":
                statement = statement.where(TikTokAccount.id == job.account_id)
            statement = statement.order_by(
                TikTokAccount.last_login_at.desc().nullslast(),
                TikTokAccount.id.asc(),
            )
            return [
                _account_snapshot(account)
                for account in session.scalars(statement)
            ]

    def _task_finished(self, task: asyncio.Task) -> None:
        self._running_tasks.discard(task)
        if task.cancelled():
            return
        try:
            task.result()
        except Exception:
            logger.exception("Pipeline runner task failed")


class PipelineRuntime:
    """One lifecycle for scheduler, dispatcher, runner, and job service."""

    def __init__(
        self,
        *,
        database: Database | None = None,
        providers: BrowserProviderRegistry | None = None,
        concurrency: PipelineConcurrencyManager | None = None,
        douyin_limit: int = 1,
        poll_interval: float = 0.25,
        pipeline_factory: Callable[[], PipelineService] = PipelineService,
    ) -> None:
        self.database = database or get_db()
        self.providers = providers or BrowserProviderRegistry()
        self.concurrency = concurrency or PipelineConcurrencyManager(
            douyin_limit=douyin_limit
        )
        self.store = PipelineJobStore()
        self.job_service = PipelineJobService(
            database=self.database,
            store=self.store,
            providers=self.providers,
            concurrency=self.concurrency,
        )
        self.service = self.job_service
        self.runner = PipelineJobRunner(
            database=self.database,
            store=self.store,
            providers=self.providers,
            concurrency=self.concurrency,
            pipeline_factory=pipeline_factory,
        )
        self.dispatcher = PipelineDispatcher(
            database=self.database,
            runner=self.runner,
            providers=self.providers,
            concurrency=self.concurrency,
            store=self.store,
            poll_interval=poll_interval,
        )
        from tiktok_bot_core.services.pipeline_scheduler import (
            PipelineScheduler,
        )

        self.scheduler = PipelineScheduler(
            database=self.database,
            job_service=self.job_service,
            poll_interval=poll_interval,
        )
        self._stop_event = asyncio.Event()
        self._loop_tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        if self._loop_tasks:
            return
        with self.database.session() as session:
            self.store.recover_interrupted(session)
        self._stop_event.clear()
        self._loop_tasks = [
            asyncio.create_task(
                self.scheduler.run_forever(self._stop_event),
                name="pipeline-scheduler",
            ),
            asyncio.create_task(
                self.dispatcher.run_forever(self._stop_event),
                name="pipeline-dispatcher",
            ),
        ]

    async def stop(self) -> None:
        self._stop_event.set()
        tasks, self._loop_tasks = self._loop_tasks, []
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.dispatcher.stop()


def _load_job(job: PipelineJob) -> None:
    list(job.stages)


def _account_snapshot(account: TikTokAccount) -> SimpleNamespace:
    return SimpleNamespace(
        id=account.id,
        platform=account.platform,
        username=account.username,
        cookies_json=account.cookies_json,
        status=account.status,
        browser_provider=account.browser_provider,
        browser_profile_id=account.browser_profile_id,
    )


def _platform_name(platform: Any) -> str:
    return str(getattr(platform, "value", platform)).strip().lower()


def _utcnow():
    from datetime import datetime

    return datetime.utcnow()


__all__ = [
    "PipelineDispatcher",
    "PipelineJobError",
    "PipelineJobRunner",
    "PipelineJobService",
    "PipelineRuntime",
]
