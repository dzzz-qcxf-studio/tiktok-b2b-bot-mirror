"""Fail-open named recorder for durable Pipeline live telemetry."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import logging
from threading import Lock
from typing import Any, Mapping, Sequence

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from tiktok_bot_core.models.pipeline_states import (
    JOB_STATUSES,
    STAGE_STATUSES,
    TERMINAL_JOB_STATUSES,
)
from tiktok_bot_core.storage.database import Database
from tiktok_bot_core.storage.pipeline_live_store import (
    BROWSE_ACTIONS,
    CHECKPOINT_STATUSES,
    JOB_ERROR_EVENT_STATUSES,
    PipelineLiveStore,
    PUBLIC_ERROR_MESSAGES,
    STAGE_ERROR_EVENT_STATUSES,
)


logger = logging.getLogger(__name__)
_HIGH_FREQUENCY_EVENT_TYPES = ("browse.scroll", "browse.wait")


@dataclass(frozen=True, slots=True)
class EventRecordResult:
    """Unambiguous outcome of one best-effort telemetry attempt."""

    sequence: int | None
    watermark: int
    persisted: bool


def _required_job_id(job_id: Any) -> str:
    if not isinstance(job_id, str):
        raise ValueError("job_id must be text")
    normalized = job_id.strip()
    if not normalized:
        raise ValueError("job_id must not be empty")
    return normalized


def _optional(payload: dict[str, Any], key: str, value: Any) -> None:
    if value is not None and value != "":
        payload[key] = value


def _registered_error_payload(
    payload: dict[str, Any],
    error_code: Any,
) -> bool:
    if not isinstance(error_code, str):
        return False
    normalized = error_code.strip().lower()
    public_message = PUBLIC_ERROR_MESSAGES.get(normalized)
    if public_message is None:
        return False
    payload["errorCode"] = normalized
    payload["message"] = public_message
    return True


class PipelineLiveEventRecorder:
    """Records only explicitly named, versioned UI event payloads.

    The recorder owns a short independent database transaction. A telemetry
    failure therefore cannot poison the caller's business transaction or
    change its result.
    """

    def __init__(
        self,
        database: Database,
        *,
        store: PipelineLiveStore | None = None,
        max_high_frequency_events: int = 100,
        sqlite_busy_timeout_ms: int = 50,
    ) -> None:
        if (
            isinstance(max_high_frequency_events, bool)
            or not isinstance(max_high_frequency_events, int)
            or not 1 <= max_high_frequency_events <= 10_000
        ):
            raise ValueError("max_high_frequency_events must be between 1 and 10000")
        if (
            isinstance(sqlite_busy_timeout_ms, bool)
            or not isinstance(sqlite_busy_timeout_ms, int)
            or not 1 <= sqlite_busy_timeout_ms <= 1_000
        ):
            raise ValueError("sqlite_busy_timeout_ms must be between 1 and 1000")
        self._database = database
        self._store = store or PipelineLiveStore()
        self._max_high_frequency_events = max_high_frequency_events
        self._state_lock = Lock()
        self._job_watermarks: dict[str, int] = {}
        self._exhausted_high_frequency_jobs: set[str] = set()
        self._telemetry_session_factory: sessionmaker[Session] | None = None

        url = make_url(database.db_url)
        self._sqlite_backend = url.get_backend_name() == "sqlite"
        if (
            self._sqlite_backend
            and url.database not in {None, "", ":memory:"}
        ):
            telemetry_engine = create_engine(
                database.db_url,
                echo=False,
                future=True,
                hide_parameters=True,
                connect_args={
                    "check_same_thread": False,
                    "timeout": sqlite_busy_timeout_ms / 1_000,
                },
                poolclass=NullPool,
            )

            @event.listens_for(telemetry_engine, "connect")
            def configure_telemetry_connection(
                dbapi_connection,
                _connection_record,
            ) -> None:
                cursor = dbapi_connection.cursor()
                try:
                    cursor.execute("PRAGMA foreign_keys=ON")
                    cursor.execute(
                        f"PRAGMA busy_timeout={sqlite_busy_timeout_ms}"
                    )
                    cursor.execute("PRAGMA recursive_triggers=OFF")
                finally:
                    cursor.close()

            self._telemetry_session_factory = sessionmaker(
                bind=telemetry_engine,
                autoflush=False,
                autocommit=False,
                future=True,
            )

    @contextmanager
    def _session(self):
        if self._telemetry_session_factory is None:
            with self._database.session() as session:
                yield session
            return
        session = self._telemetry_session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _watermark(self, job_id: str) -> int:
        with self._state_lock:
            return self._job_watermarks.get(job_id, 0)

    def _is_high_frequency_exhausted(self, job_id: str) -> bool:
        with self._state_lock:
            return job_id in self._exhausted_high_frequency_jobs

    def _apply_committed_cache_update(
        self,
        *,
        job_id: str,
        watermark: int | None,
        mark_high_frequency_exhausted: bool,
        clear_job_cache: bool,
    ) -> None:
        """Apply recorder-local state only after the DB transaction commits."""
        with self._state_lock:
            if clear_job_cache:
                self._job_watermarks.pop(job_id, None)
                self._exhausted_high_frequency_jobs.discard(job_id)
                return
            if watermark is not None:
                self._job_watermarks[job_id] = max(
                    watermark,
                    self._job_watermarks.get(job_id, 0),
                )
            if mark_high_frequency_exhausted:
                self._exhausted_high_frequency_jobs.add(job_id)

    def _not_persisted(self, job_id: str) -> EventRecordResult:
        return EventRecordResult(
            sequence=None,
            watermark=self._watermark(job_id),
            persisted=False,
        )

    def _record(
        self,
        *,
        job_id: str,
        stage: str,
        event_type: str,
        level: str,
        payload: Mapping[str, Any],
        merge_high_frequency: bool = False,
    ) -> EventRecordResult:
        if merge_high_frequency and self._is_high_frequency_exhausted(job_id):
            return self._not_persisted(job_id)
        try:
            result: EventRecordResult
            committed_watermark: int | None = None
            mark_high_frequency_exhausted = False
            with self._session() as session:
                safe_payload = dict(payload)
                high_frequency_count: int | None = None
                suppress = False
                if merge_high_frequency:
                    latest = self._store.get_latest_event(
                        session,
                        job_id=job_id,
                    )
                    if latest is not None and latest.event_type == event_type:
                        committed_watermark = latest.sequence
                        suppress = True
                    else:
                        high_frequency_count = self._store.count_event_types(
                            session,
                            job_id=job_id,
                            event_types=_HIGH_FREQUENCY_EVENT_TYPES,
                        )
                    if (
                        high_frequency_count is not None
                        and high_frequency_count
                        >= self._max_high_frequency_events
                    ):
                        if latest is not None:
                            committed_watermark = latest.sequence
                        mark_high_frequency_exhausted = True
                        suppress = True
                if suppress:
                    result = EventRecordResult(
                        sequence=None,
                        watermark=(
                            committed_watermark
                            if committed_watermark is not None
                            else self._watermark(job_id)
                        ),
                        persisted=False,
                    )
                else:
                    event = self._store.append_event(
                        session,
                        job_id=job_id,
                        stage=stage,
                        event_type=event_type,
                        level=level,
                        payload=safe_payload,
                    )
                    sequence = event.sequence
                    committed_watermark = sequence
                    if (
                        merge_high_frequency
                        and high_frequency_count is not None
                        and high_frequency_count + 1
                        >= self._max_high_frequency_events
                    ):
                        mark_high_frequency_exhausted = True
                    result = EventRecordResult(
                        sequence=sequence,
                        watermark=sequence,
                        persisted=True,
                    )
            self._apply_committed_cache_update(
                job_id=job_id,
                watermark=committed_watermark,
                mark_high_frequency_exhausted=mark_high_frequency_exhausted,
                clear_job_cache=(
                    event_type == "job.lifecycle"
                    and safe_payload.get("status") in TERMINAL_JOB_STATUSES
                ),
            )
            return result
        except Exception:
            # Never interpolate the exception: upstream/database errors can
            # contain request bodies, paths or credentials.
            logger.warning(
                "Pipeline live telemetry write was skipped for %s",
                event_type,
            )
            return self._not_persisted(job_id)

    @staticmethod
    def _checkpoint_decision_payload(
        checkpoint: Any,
        *,
        status: str,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schemaVersion": 1,
            "checkpointId": checkpoint.id,
            "kind": checkpoint.kind,
            "status": status,
        }
        deadline = (
            None
            if checkpoint.kind == "manual_review_session"
            else checkpoint.deadline_at
        )
        if deadline is not None:
            payload["deadlineAt"] = deadline.isoformat()
        if status == "pending":
            payload["defaultOptionKey"] = checkpoint.default_option_key
        else:
            _optional(payload, "resolutionKey", checkpoint.resolution_key)
            _optional(
                payload,
                "resolutionSource",
                checkpoint.resolution_source or "system",
            )
        return payload

    def _record_checkpoint_decision(
        self,
        *,
        job_id: str,
        stage: str,
        checkpoint_id: str,
        kind: str,
    ) -> EventRecordResult:
        """Append the canonical pending/terminal prefix for one checkpoint.

        ``BEGIN IMMEDIATE`` serializes independent recorder connections before
        they inspect existing events.  The checkpoint row is the authority, so
        an early or contradictory caller cannot invent a terminal event.
        """

        try:
            committed_watermark: int | None = None
            persisted_sequence: int | None = None
            with self._session() as session:
                if self._sqlite_backend:
                    session.execute(text("BEGIN IMMEDIATE"))
                checkpoint = self._store.get_checkpoint(
                    session,
                    job_id=job_id,
                    checkpoint_id=checkpoint_id,
                )
                if (
                    checkpoint is None
                    or checkpoint.stage != stage
                    or checkpoint.kind != kind
                ):
                    return self._not_persisted(job_id)
                existing = self._store.list_checkpoint_events(
                    session,
                    job_id=job_id,
                    checkpoint_id=checkpoint_id,
                )
                statuses = [
                    str(event.payload_json.get("status") or "")
                    for event in existing
                ]
                if existing:
                    committed_watermark = existing[-1].sequence
                terminal_statuses = CHECKPOINT_STATUSES - {"pending"}
                has_pending = "pending" in statuses
                has_terminal = any(
                    status in terminal_statuses for status in statuses
                )

                # Never append a late pending behind a legacy terminal.  Fresh
                # writes always create pending and terminal in this transaction.
                if not has_pending and not has_terminal:
                    pending = self._store.append_event(
                        session,
                        job_id=job_id,
                        stage=stage,
                        event_type="decision.lifecycle",
                        level="warning",
                        payload=self._checkpoint_decision_payload(
                            checkpoint,
                            status="pending",
                        ),
                    )
                    persisted_sequence = pending.sequence
                    committed_watermark = pending.sequence
                    has_pending = True

                if checkpoint.status != "pending" and not has_terminal:
                    terminal = self._store.append_event(
                        session,
                        job_id=job_id,
                        stage=stage,
                        event_type="decision.lifecycle",
                        level="info",
                        payload=self._checkpoint_decision_payload(
                            checkpoint,
                            status=checkpoint.status,
                        ),
                    )
                    persisted_sequence = terminal.sequence
                    committed_watermark = terminal.sequence

                result = EventRecordResult(
                    sequence=persisted_sequence,
                    watermark=(
                        committed_watermark
                        if committed_watermark is not None
                        else self._watermark(job_id)
                    ),
                    persisted=persisted_sequence is not None,
                )
            self._apply_committed_cache_update(
                job_id=job_id,
                watermark=committed_watermark,
                mark_high_frequency_exhausted=False,
                clear_job_cache=False,
            )
            return result
        except Exception:
            logger.warning(
                "Pipeline live telemetry write was skipped for decision.lifecycle"
            )
            return self._not_persisted(job_id)
    def record_lifecycle(
        self,
        *,
        job_id: str,
        status: str,
        previous_status: str = "",
        trigger_type: str = "",
        duration_ms: int | None = None,
        error_code: str = "",
        message: str = "",
    ) -> EventRecordResult:
        normalized_job_id = _required_job_id(job_id)
        if not isinstance(status, str) or status not in JOB_STATUSES:
            logger.warning(
                "Pipeline live telemetry rejected an unregistered job status"
            )
            return self._not_persisted(normalized_job_id)
        if status in JOB_ERROR_EVENT_STATUSES and not error_code:
            logger.warning(
                "Pipeline live telemetry rejected an unsafe job error event"
            )
            return self._not_persisted(normalized_job_id)
        payload: dict[str, Any] = {"schemaVersion": 1, "status": status}
        _optional(payload, "previousStatus", previous_status)
        _optional(payload, "triggerType", trigger_type)
        _optional(payload, "durationMs", duration_ms)
        if error_code:
            if not _registered_error_payload(payload, error_code):
                logger.warning(
                    "Pipeline live telemetry rejected an unregistered error code"
                )
                return self._not_persisted(normalized_job_id)
        else:
            _optional(payload, "message", message)
        return self._record(
            job_id=normalized_job_id,
            stage="",
            event_type="job.lifecycle",
            level="error" if error_code else "info",
            payload=payload,
        )

    def record_stage(
        self,
        *,
        job_id: str,
        stage: str,
        status: str,
        previous_status: str = "",
        attempt: int | None = None,
        result_count: int | None = None,
        duration_ms: int | None = None,
        error_code: str = "",
        message: str = "",
    ) -> EventRecordResult:
        normalized_job_id = _required_job_id(job_id)
        if not isinstance(status, str) or status not in STAGE_STATUSES:
            logger.warning(
                "Pipeline live telemetry rejected an unregistered stage status"
            )
            return self._not_persisted(normalized_job_id)
        if status in STAGE_ERROR_EVENT_STATUSES and not error_code:
            logger.warning(
                "Pipeline live telemetry rejected an unsafe stage error event"
            )
            return self._not_persisted(normalized_job_id)
        payload: dict[str, Any] = {"schemaVersion": 1, "status": status}
        _optional(payload, "previousStatus", previous_status)
        _optional(payload, "attempt", attempt)
        _optional(payload, "resultCount", result_count)
        _optional(payload, "durationMs", duration_ms)
        if error_code:
            if not _registered_error_payload(payload, error_code):
                logger.warning(
                    "Pipeline live telemetry rejected an unregistered error code"
                )
                return self._not_persisted(normalized_job_id)
        else:
            _optional(payload, "message", message)
        return self._record(
            job_id=normalized_job_id,
            stage=stage,
            event_type="stage.lifecycle",
            level="error" if error_code else "info",
            payload=payload,
        )

    def record_browse(
        self,
        *,
        job_id: str,
        stage: str,
        action: str,
        step: int,
        keyword: str = "",
        page_type: str = "",
        url: str = "",
        rationale: str = "",
        screenshot_hash: str = "",
        wait_ms: int | None = None,
        scroll_px: int | None = None,
        evidence_count: int | None = None,
        budget: Mapping[str, int | float] | None = None,
        summary: str = "",
        error_code: str = "",
        message: str = "",
    ) -> EventRecordResult:
        normalized_job_id = _required_job_id(job_id)
        if not isinstance(action, str):
            raise ValueError("browse action must be text")
        normalized_action = action.strip().lower()
        if normalized_action not in BROWSE_ACTIONS:
            raise ValueError("unsupported browse action")
        payload: dict[str, Any] = {
            "schemaVersion": 1,
            "action": normalized_action,
            "step": step,
            "mergedCount": 1,
        }
        if normalized_action == "error":
            if not _registered_error_payload(payload, error_code):
                logger.warning(
                    "Pipeline live telemetry rejected an unregistered error code"
                )
                return self._not_persisted(normalized_job_id)
            return self._record(
                job_id=normalized_job_id,
                stage=stage,
                event_type="browse.error",
                level="error",
                payload=payload,
            )
        if error_code:
            logger.warning(
                "Pipeline live telemetry rejected an error code on a non-error event"
            )
            return self._not_persisted(normalized_job_id)
        _optional(payload, "keyword", keyword)
        _optional(payload, "pageType", page_type)
        _optional(payload, "url", url)
        _optional(payload, "rationale", rationale)
        _optional(payload, "screenshotHash", screenshot_hash)
        _optional(payload, "waitMs", wait_ms)
        _optional(payload, "scrollPx", scroll_px)
        _optional(payload, "evidenceCount", evidence_count)
        _optional(payload, "budget", budget)
        _optional(payload, "summary", summary)
        _optional(payload, "message", message)
        return self._record(
            job_id=normalized_job_id,
            stage=stage,
            event_type=f"browse.{normalized_action}",
            level="info",
            payload=payload,
            merge_high_frequency=normalized_action in {"scroll", "wait"},
        )

    def record_decision(
        self,
        *,
        job_id: str,
        stage: str,
        checkpoint_id: str,
        kind: str,
        status: str,
        default_option_key: str = "",
        deadline_at: datetime | str | None = None,
        resolution_key: str = "",
        resolution_source: str = "",
        message: str = "",
    ) -> EventRecordResult:
        normalized_job_id = _required_job_id(job_id)
        if not isinstance(status, str) or status not in CHECKPOINT_STATUSES:
            logger.warning(
                "Pipeline live telemetry rejected an unregistered decision status"
            )
            return self._not_persisted(normalized_job_id)
        # Caller fields are accepted for API compatibility, but the durable
        # checkpoint row is the only authority for pending/default/deadline and
        # terminal resolution.  This also prevents raw caller text from being
        # persisted on decision events.
        del default_option_key, deadline_at, resolution_key, resolution_source, message
        return self._record_checkpoint_decision(
            job_id=normalized_job_id,
            stage=stage,
            checkpoint_id=str(checkpoint_id or "").strip(),
            kind=str(kind or "").strip(),
        )

    def record_candidate(
        self,
        *,
        job_id: str,
        stage: str,
        user_id: int,
        status: str,
        match_score: float | None = None,
        confidence_score: float | None = None,
        labels: Sequence[str] | None = None,
        evidence_count: int | None = None,
        missing_fields: Sequence[str] | None = None,
        message: str = "",
    ) -> EventRecordResult:
        normalized_job_id = _required_job_id(job_id)
        payload: dict[str, Any] = {
            "schemaVersion": 1,
            "userId": user_id,
            "status": status,
        }
        _optional(payload, "matchScore", match_score)
        _optional(payload, "confidenceScore", confidence_score)
        _optional(payload, "labels", labels)
        _optional(payload, "evidenceCount", evidence_count)
        _optional(
            payload,
            "missingFields",
            missing_fields,
        )
        _optional(payload, "message", message)
        return self._record(
            job_id=normalized_job_id,
            stage=stage,
            event_type="candidate.lifecycle",
            level="info",
            payload=payload,
        )


__all__ = ["EventRecordResult", "PipelineLiveEventRecorder"]
