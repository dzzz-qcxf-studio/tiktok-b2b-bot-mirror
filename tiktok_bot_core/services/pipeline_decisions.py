"""Server-owned, durable decision gates for one running Pipeline Job."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import Lock
from types import MappingProxyType
from typing import Any

from tiktok_bot_core.models.entities import PipelineDecisionCheckpoint
from tiktok_bot_core.models.pipeline_states import (
    JOB_STATUS_WAITING_DECISION,
)
from tiktok_bot_core.storage.database import Database
from tiktok_bot_core.storage.pipeline_job_store import PipelineJobStore
from tiktok_bot_core.storage.pipeline_live_store import (
    CheckpointConflictError,
    PipelineLiveStore,
    PipelineLiveValidationError,
)


ContextBuilder = Callable[[Mapping[str, Any]], Mapping[str, Any]]
Clock = Callable[[], datetime]
Sleeper = Callable[[float], Awaitable[None]]

_CONTEXT_FIELDS = frozenset(
    {
        "schemaVersion",
        "title",
        "question",
        "summary",
        "metrics",
        "warnings",
        "candidateCounts",
        "remainingBudget",
        "defaultReason",
        "blockingReason",
        "manualSession",
    }
)


class DecisionGateError(RuntimeError):
    """Base error for stable decision-gate failures."""


class DecisionGateValidationError(DecisionGateError, ValueError):
    """The caller requested an unregistered decision or public action."""


class DecisionGateStateError(DecisionGateError):
    """The Job and Stage are not in the state required by the gate."""


class DecisionGateCancelledError(DecisionGateError):
    """The Job or decision waiter was cancelled."""


class DecisionGateConflictError(DecisionGateError):
    """Another human, timeout, or cancellation won the resolution CAS."""

    def __init__(
        self,
        message: str = "decision conflict",
        *,
        authoritative: DecisionResolution | None = None,
    ) -> None:
        super().__init__(message)
        self.authoritative = authoritative


@dataclass(frozen=True, slots=True)
class CheckpointDefinition:
    """A server-registered finite decision kind."""

    kind: str
    option_keys: tuple[str, ...]
    default_option_key: str
    context_builder: ContextBuilder
    auto_timeout: bool = True


@dataclass(frozen=True, slots=True)
class DecisionResolution:
    checkpoint_id: str
    job_id: str
    stage: str
    kind: str
    option_key: str | None
    source: str
    status: str
    resolved_at: datetime | None
    deadline_at: datetime | None


@dataclass(frozen=True, slots=True)
class JobCancellationResult:
    job_id: str
    status: str


@dataclass(frozen=True, slots=True)
class _Waiter:
    loop: asyncio.AbstractEventLoop
    event: asyncio.Event


def _safe_context(source: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(source, Mapping):
        raise DecisionGateValidationError("decision context must be an object")
    if set(source) - _CONTEXT_FIELDS:
        raise DecisionGateValidationError(
            "decision context contains unsupported fields"
        )
    if "schemaVersion" in source and source["schemaVersion"] != 1:
        raise DecisionGateValidationError(
            "decision context schema version is unsupported"
        )
    return {"schemaVersion": 1, **dict(source)}


def _definition(
    kind: str,
    options: tuple[str, ...],
    default: str,
    *,
    auto_timeout: bool = True,
) -> CheckpointDefinition:
    return CheckpointDefinition(
        kind=kind,
        option_keys=options,
        default_option_key=default,
        context_builder=_safe_context,
        auto_timeout=auto_timeout,
    )


CHECKPOINT_DEFINITIONS: Mapping[str, CheckpointDefinition] = MappingProxyType(
    {
        "insufficient_evidence": _definition(
            "insufficient_evidence",
            (
                "deepen_with_remaining_budget",
                "continue_with_current_evidence",
                "skip_remaining_pipeline",
                "cancel_job",
            ),
            "continue_with_current_evidence",
        ),
        "qualification_review": _definition(
            "qualification_review",
            (
                "open_review_workbench",
                "request_batch_enrichment",
                "continue_with_qualified_only",
            ),
            "continue_with_qualified_only",
        ),
        "outreach_confirmation": _definition(
            "outreach_confirmation",
            (
                "execute_approved_outreach",
                "open_review_workbench",
                "skip_outreach",
            ),
            "execute_approved_outreach",
        ),
        "retryable_failure": _definition(
            "retryable_failure",
            ("retry_once", "skip_stage", "stop_job"),
            "retry_once",
        ),
        "account_blocked": _definition(
            "account_blocked",
            ("open_account_recovery", "skip_stage", "stop_job"),
            "skip_stage",
        ),
        "manual_review_session": _definition(
            "manual_review_session",
            ("review_complete",),
            "review_complete",
            auto_timeout=False,
        ),
    }
)


class DecisionGateService:
    """Create, await, resolve, and cancel short server-side checkpoints."""

    def __init__(
        self,
        database: Database,
        *,
        job_store: PipelineJobStore | None = None,
        live_store: PipelineLiveStore | None = None,
        timeout_seconds: float = 10,
        poll_interval_seconds: float = 0.1,
        clock: Clock = datetime.utcnow,
        sleeper: Sleeper = asyncio.sleep,
    ) -> None:
        for name, value in (
            ("timeout_seconds", timeout_seconds),
            ("poll_interval_seconds", poll_interval_seconds),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value <= 0
                or value > 3_600
            ):
                raise ValueError(f"{name} must be between 0 and 3600")
        if not callable(clock) or not callable(sleeper):
            raise TypeError("clock and sleeper must be callable")
        self._database = database
        self._job_store = job_store or PipelineJobStore()
        self._live_store = live_store or PipelineLiveStore()
        self._timeout_seconds = float(timeout_seconds)
        self._poll_interval_seconds = min(
            float(poll_interval_seconds),
            self._timeout_seconds,
        )
        self._clock = clock
        self._sleeper = sleeper
        self._waiter_lock = Lock()
        self._waiters: dict[str, set[_Waiter]] = {}

    @property
    def waiter_count(self) -> int:
        with self._waiter_lock:
            return sum(len(waiters) for waiters in self._waiters.values())

    def _definition(self, kind: str) -> CheckpointDefinition:
        if not isinstance(kind, str):
            raise DecisionGateValidationError(
                "decision kind must be registered"
            )
        definition = CHECKPOINT_DEFINITIONS.get(kind.strip())
        if definition is None:
            raise DecisionGateValidationError(
                "decision kind must be registered"
            )
        return definition

    def _register_waiter(self, job_id: str) -> _Waiter:
        waiter = _Waiter(
            loop=asyncio.get_running_loop(),
            event=asyncio.Event(),
        )
        with self._waiter_lock:
            self._waiters.setdefault(job_id, set()).add(waiter)
        return waiter

    def _unregister_waiter(self, job_id: str, waiter: _Waiter) -> None:
        with self._waiter_lock:
            waiters = self._waiters.get(job_id)
            if waiters is None:
                return
            waiters.discard(waiter)
            if not waiters:
                self._waiters.pop(job_id, None)

    def _notify(self, job_id: str) -> None:
        with self._waiter_lock:
            waiters = tuple(self._waiters.get(job_id, ()))
        for waiter in waiters:
            try:
                waiter.loop.call_soon_threadsafe(waiter.event.set)
            except RuntimeError:
                # A response is already committed at this point. A browser or
                # task may have closed its loop, so prune only that stale
                # waiter and never turn successful business work into failure.
                self._unregister_waiter(job_id, waiter)

    @staticmethod
    def _resolution(
        checkpoint: PipelineDecisionCheckpoint,
    ) -> DecisionResolution | None:
        if checkpoint.status == "pending":
            return None
        return DecisionResolution(
            checkpoint_id=checkpoint.id,
            job_id=checkpoint.job_id,
            stage=checkpoint.stage,
            kind=checkpoint.kind,
            option_key=checkpoint.resolution_key,
            source=checkpoint.resolution_source or "system",
            status=checkpoint.status,
            resolved_at=checkpoint.resolved_at,
            deadline_at=(
                None
                if checkpoint.kind == "manual_review_session"
                else checkpoint.deadline_at
            ),
        )

    @staticmethod
    def _option_subset(
        definition: CheckpointDefinition,
        option_keys: tuple[str, ...] | list[str] | None,
        default_option_key: str | None,
    ) -> tuple[tuple[str, ...], str]:
        if option_keys is None:
            normalized_options = definition.option_keys
        elif isinstance(option_keys, (tuple, list)):
            normalized_options = tuple(option_keys)
        else:
            raise DecisionGateValidationError(
                "decision options must be a registered subset"
            )
        if (
            not normalized_options
            or len(set(normalized_options)) != len(normalized_options)
            or any(
                not isinstance(option, str)
                or option not in definition.option_keys
                for option in normalized_options
            )
        ):
            raise DecisionGateValidationError(
                "decision options must be a registered subset"
            )
        normalized_default = (
            definition.default_option_key
            if default_option_key is None
            else default_option_key
        )
        if (
            not isinstance(normalized_default, str)
            or normalized_default not in normalized_options
        ):
            raise DecisionGateValidationError(
                "decision default must be in the registered subset"
            )
        return normalized_options, normalized_default

    def get_resolution(
        self,
        *,
        job_id: str,
        checkpoint_id: str,
    ) -> DecisionResolution | None:
        with self._database.session() as session:
            checkpoint = self._live_store.get_checkpoint(
                session,
                job_id=job_id,
                checkpoint_id=checkpoint_id,
            )
            if checkpoint is None:
                return None
            return self._resolution(checkpoint)

    def _resolve_once(
        self,
        *,
        job_id: str,
        checkpoint_id: str,
        option_key: str,
        version: int,
        source: str,
        operator: str = "",
        reason: str = "",
    ) -> DecisionResolution:
        resolution: DecisionResolution | None = None
        try:
            with self._database.session() as session:
                checkpoint = self._live_store.resolve_checkpoint(
                    session,
                    job_id=job_id,
                    checkpoint_id=checkpoint_id,
                    option_key=option_key,
                    version=version,
                    resolution_source=source,
                    operator=operator,
                    reason=reason,
                    resolved_at=self._clock(),
                )
                if not self._job_store.resume_from_decision(
                    session,
                    job_id,
                    checkpoint.stage,
                ):
                    raise CheckpointConflictError(
                        "decision resume conflict"
                    )
                resolution = self._resolution(checkpoint)
                if resolution is None:
                    raise DecisionGateStateError(
                        "decision did not reach a terminal state"
                    )
        except PipelineLiveValidationError as exc:
            raise DecisionGateValidationError(
                "decision option is not registered"
            ) from exc
        except CheckpointConflictError as exc:
            raise DecisionGateConflictError(
                authoritative=self.get_resolution(
                    job_id=job_id,
                    checkpoint_id=checkpoint_id,
                )
            ) from exc
        self._notify(job_id)
        return resolution

    def resolve(
        self,
        *,
        job_id: str,
        checkpoint_id: str,
        option_key: str,
        version: int,
        operator: str = "",
        reason: str = "",
    ) -> DecisionResolution:
        with self._database.session() as session:
            checkpoint = self._live_store.get_checkpoint(
                session,
                job_id=job_id,
                checkpoint_id=checkpoint_id,
            )
            if checkpoint is None or checkpoint.status != "pending":
                authoritative = (
                    self._resolution(checkpoint)
                    if checkpoint is not None
                    else None
                )
                raise DecisionGateConflictError(
                    authoritative=authoritative
                )
            definition = self._definition(checkpoint.kind)
            if (
                definition.auto_timeout
                and self._clock() >= checkpoint.deadline_at
            ):
                deadline_default = checkpoint.default_option_key
                deadline_version = checkpoint.version
            else:
                deadline_default = None
                deadline_version = checkpoint.version
        if deadline_default is not None:
            try:
                authoritative = self._resolve_once(
                    job_id=job_id,
                    checkpoint_id=checkpoint_id,
                    option_key=deadline_default,
                    version=deadline_version,
                    source="timeout",
                    reason="deadline_elapsed",
                )
            except DecisionGateConflictError as exc:
                authoritative = exc.authoritative
            raise DecisionGateConflictError(
                authoritative=authoritative
            )
        return self._resolve_once(
            job_id=job_id,
            checkpoint_id=checkpoint_id,
            option_key=option_key,
            version=version,
            source="human",
            operator=operator,
            reason=reason,
        )

    def cancel_job(self, job_id: str) -> JobCancellationResult | None:
        result: JobCancellationResult | None = None
        with self._database.session() as session:
            job = self._job_store.request_cancel(session, job_id)
            if job is not None:
                result = JobCancellationResult(job_id=job.id, status=job.status)
        self._notify(job_id)
        return result

    async def _wait_once(self, waiter: _Waiter, delay: float) -> None:
        notified = asyncio.create_task(waiter.event.wait())
        slept = asyncio.create_task(self._sleeper(delay))
        tasks = {notified, slept}
        try:
            completed, _ = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
            # asyncio.wait does not propagate task exceptions by itself.
            # Surface an injected/operational sleeper failure to the gate's
            # cleanup path instead of spinning until the deadline.
            if slept in completed:
                slept.result()
            if notified in completed:
                notified.result()
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def _cleanup_timestamp(self) -> datetime:
        try:
            value = self._clock()
            if isinstance(value, datetime):
                return value
        except Exception:
            pass
        return datetime.utcnow()

    def _cleanup_waiting_checkpoint(
        self,
        *,
        job_id: str,
        checkpoint_id: str,
        stage: str,
    ) -> None:
        cleanup_time = self._cleanup_timestamp()
        first_error: Exception | None = None
        try:
            with self._database.session() as session:
                self._live_store.cancel_checkpoint(
                    session,
                    job_id=job_id,
                    checkpoint_id=checkpoint_id,
                    reason="decision_waiter_cancelled",
                    resolved_at=cleanup_time,
                )
                if self._job_store.resume_from_decision(
                    session,
                    job_id,
                    stage,
                ):
                    return
                job = self._job_store.get_job(session, job_id)
                if (
                    job is None
                    or job.status != JOB_STATUS_WAITING_DECISION
                ):
                    return
        except Exception as exc:
            first_error = exc

        try:
            with self._database.session() as session:
                self._live_store.cancel_checkpoint(
                    session,
                    job_id=job_id,
                    checkpoint_id=checkpoint_id,
                    reason="decision_waiter_cancelled",
                    resolved_at=cleanup_time,
                )
                if self._job_store.interrupt_waiting_decision(
                    session,
                    job_id,
                    stage,
                ):
                    return
                job = self._job_store.get_job(session, job_id)
                if (
                    job is None
                    or job.status != JOB_STATUS_WAITING_DECISION
                ):
                    return
        except Exception as exc:
            if first_error is None:
                first_error = exc
        raise DecisionGateError("decision cleanup failed") from first_error

    async def await_decision(
        self,
        *,
        job_id: str,
        stage: str,
        kind: str,
        context: Mapping[str, Any],
        option_keys: tuple[str, ...] | list[str] | None = None,
        default_option_key: str | None = None,
    ) -> DecisionResolution:
        definition = self._definition(kind)
        selected_options, selected_default = self._option_subset(
            definition,
            option_keys,
            default_option_key,
        )
        safe_context = definition.context_builder(context)
        now = self._clock()
        if not isinstance(now, datetime):
            raise TypeError("clock must return a datetime")
        deadline = (
            now + timedelta(seconds=self._timeout_seconds)
            if definition.auto_timeout
            else datetime.max
        )
        checkpoint_id = ""
        version = 0
        try:
            with self._database.session() as session:
                checkpoint = self._live_store.create_checkpoint(
                    session,
                    job_id=job_id,
                    stage=stage,
                    kind=definition.kind,
                    option_keys=selected_options,
                    default_option_key=selected_default,
                    context=safe_context,
                    deadline_at=deadline,
                )
                checkpoint_id = checkpoint.id
                version = checkpoint.version
                if not self._job_store.pause_for_decision(
                    session,
                    job_id,
                    stage,
                ):
                    raise DecisionGateStateError(
                        "job and stage cannot enter decision waiting"
                    )
        except PipelineLiveValidationError as exc:
            raise DecisionGateValidationError(
                "decision checkpoint is invalid"
            ) from exc

        waiter = self._register_waiter(job_id)
        try:
            while True:
                # Clear before reading durable state so a concurrent notifier
                # cannot be erased between the read and the wait.
                waiter.event.clear()
                with self._database.session() as session:
                    checkpoint = self._live_store.get_checkpoint(
                        session,
                        job_id=job_id,
                        checkpoint_id=checkpoint_id,
                    )
                    if checkpoint is None:
                        raise DecisionGateStateError(
                            "decision checkpoint is unavailable"
                        )
                    resolution = self._resolution(checkpoint)
                    if resolution is not None:
                        if checkpoint.status == "cancelled":
                            raise DecisionGateCancelledError(
                                "decision was cancelled"
                            )
                        return resolution
                    remaining = (
                        (checkpoint.deadline_at - self._clock()).total_seconds()
                        if definition.auto_timeout
                        else self._poll_interval_seconds
                    )
                if definition.auto_timeout and remaining <= 0:
                    try:
                        return self._resolve_once(
                            job_id=job_id,
                            checkpoint_id=checkpoint_id,
                            option_key=selected_default,
                            version=version,
                            source="timeout",
                            reason="deadline_elapsed",
                        )
                    except DecisionGateConflictError as exc:
                        if exc.authoritative is not None:
                            if exc.authoritative.status == "cancelled":
                                raise DecisionGateCancelledError(
                                    "decision was cancelled"
                                ) from exc
                            return exc.authoritative
                        await asyncio.sleep(0)
                        continue
                await self._wait_once(
                    waiter,
                    min(remaining, self._poll_interval_seconds),
                )
        except asyncio.CancelledError:
            try:
                self._cleanup_waiting_checkpoint(
                    job_id=job_id,
                    checkpoint_id=checkpoint_id,
                    stage=stage,
                )
            except DecisionGateError as cleanup_error:
                raise cleanup_error from None
            raise
        except Exception as original_error:
            try:
                self._cleanup_waiting_checkpoint(
                    job_id=job_id,
                    checkpoint_id=checkpoint_id,
                    stage=stage,
                )
            except DecisionGateError as cleanup_error:
                raise cleanup_error from original_error
            raise
        finally:
            self._unregister_waiter(job_id, waiter)

    async def await_manual_review(
        self,
        *,
        job_id: str,
        stage: str,
        context: Mapping[str, Any],
    ) -> DecisionResolution:
        """Wait for explicit review completion without an automatic default."""

        if not isinstance(context, Mapping):
            raise DecisionGateValidationError(
                "decision context must be an object"
            )
        return await self.await_decision(
            job_id=job_id,
            stage=stage,
            kind="manual_review_session",
            option_keys=("review_complete",),
            default_option_key="review_complete",
            context={**dict(context), "manualSession": True},
        )


__all__ = [
    "CHECKPOINT_DEFINITIONS",
    "CheckpointDefinition",
    "DecisionGateCancelledError",
    "DecisionGateConflictError",
    "DecisionGateError",
    "DecisionGateService",
    "DecisionGateStateError",
    "DecisionGateValidationError",
    "DecisionResolution",
    "JobCancellationResult",
]
