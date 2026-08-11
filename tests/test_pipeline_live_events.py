"""Named Pipeline live recorder and high-frequency retention tests."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
import logging
import time

import pytest
from sqlalchemy import text, update

from tiktok_bot_core.models.entities import PipelineJob
from tiktok_bot_core.services.pipeline_live_events import (
    PipelineLiveEventRecorder,
)
from tiktok_bot_core.storage.database import Database
from tiktok_bot_core.storage.pipeline_live_store import PipelineLiveStore


@pytest.fixture
def db(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'pipeline-live-events.db'}")
    database.init()
    yield database
    database.engine.dispose()


def _seed_job(database: Database) -> str:
    with database.session() as session:
        job = PipelineJob(platform="douyin", stages_json=["collect", "filter"])
        session.add(job)
        session.flush()
        return job.id


def test_named_recorders_cover_five_event_families_and_bind_job(db):
    job_id = _seed_job(db)
    recorder = PipelineLiveEventRecorder(db)
    with db.session() as session:
        checkpoint_id = PipelineLiveStore().create_checkpoint(
            session,
            job_id=job_id,
            stage="filter",
            kind="qualification_review",
            option_keys=("continue_with_qualified_only",),
            default_option_key="continue_with_qualified_only",
            context={"schemaVersion": 1, "summary": "review candidates"},
            deadline_at=datetime.utcnow() + timedelta(seconds=10),
        ).id

    results = [
        recorder.record_lifecycle(
            job_id=job_id,
            status="running",
            previous_status="queued",
        ),
        recorder.record_stage(
            job_id=job_id,
            stage="collect",
            status="running",
            attempt=1,
        ),
        recorder.record_browse(
            job_id=job_id,
            stage="collect",
            action="navigate",
            step=1,
            keyword="industrial transformer",
            page_type="video_search",
            url="https://www.douyin.com/search/industrial%20transformer?type=video",
            screenshot_hash="0123456789abcdef",
        ),
        recorder.record_decision(
            job_id=job_id,
            stage="filter",
            checkpoint_id=checkpoint_id,
            kind="qualification_review",
            status="pending",
            default_option_key="continue_with_qualified_only",
        ),
        recorder.record_candidate(
            job_id=job_id,
            stage="filter",
            user_id=42,
            status="manual_review",
            evidence_count=3,
        ),
    ]

    assert all(result.persisted for result in results)
    sequences = [result.sequence for result in results]
    assert all(isinstance(sequence, int) for sequence in sequences)
    assert sequences == sorted(sequences)
    with db.session() as session:
        events = PipelineLiveStore().list_events(
            session,
            job_id=job_id,
            after_sequence=0,
            limit=20,
        )
        assert {row.event_type.split(".", 1)[0] for row in events} == {
            "job",
            "stage",
            "browse",
            "decision",
            "candidate",
        }
        assert {row.job_id for row in events} == {job_id}


def test_scroll_and_wait_are_merged_and_bounded_but_critical_events_remain(db):
    job_id = _seed_job(db)
    recorder = PipelineLiveEventRecorder(db, max_high_frequency_events=2)

    first = recorder.record_browse(
        job_id=job_id,
        stage="collect",
        action="scroll",
        step=1,
        scroll_px=500,
    )
    second = recorder.record_browse(
        job_id=job_id,
        stage="collect",
        action="scroll",
        step=2,
        scroll_px=500,
    )
    assert first.persisted is True
    assert second.persisted is False
    assert second.sequence is None
    assert second.watermark == first.sequence
    recorder.record_browse(
        job_id=job_id,
        stage="collect",
        action="wait",
        step=3,
        wait_ms=500,
    )
    wait_sequence = recorder.record_browse(
        job_id=job_id,
        stage="collect",
        action="wait",
        step=4,
        wait_ms=500,
    )
    assert wait_sequence.persisted is False
    recorder.record_browse(
        job_id=job_id,
        stage="collect",
        action="scroll",
        step=5,
        scroll_px=600,
    )
    recorder.record_browse(
        job_id=job_id,
        stage="collect",
        action="extract",
        step=6,
        evidence_count=2,
        summary="2 candidate observations",
    )
    recorder.record_browse(
        job_id=job_id,
        stage="collect",
        action="error",
        step=7,
        error_code="network",
        message="上游暂时不可用",
    )
    recorder.record_browse(
        job_id=job_id,
        stage="collect",
        action="done",
        step=8,
        summary="bounded exploration finished",
    )

    with db.session() as session:
        events = PipelineLiveStore().list_events(
            session,
            job_id=job_id,
            after_sequence=0,
            limit=100,
        )
        event_snapshots = [
            (row.event_type, dict(row.payload_json)) for row in events
        ]

    high_frequency = [
        row for row in event_snapshots if row[0] in {"browse.scroll", "browse.wait"}
    ]
    assert len(high_frequency) <= 2
    assert [event_type for event_type, _ in high_frequency] == [
        "browse.scroll",
        "browse.wait",
    ]
    assert {"browse.extract", "browse.error", "browse.done"} <= {
        event_type for event_type, _payload in event_snapshots
    }


def test_high_frequency_coalescing_is_append_only_and_reconnect_safe(db):
    job_id = _seed_job(db)
    recorder = PipelineLiveEventRecorder(db, max_high_frequency_events=2)
    store = PipelineLiveStore()

    scroll_result = recorder.record_browse(
        job_id=job_id,
        stage="collect",
        action="scroll",
        step=1,
        scroll_px=500,
    )
    scroll_sequence = scroll_result.sequence
    assert scroll_result.persisted is True
    suppressed_scroll = recorder.record_browse(
        job_id=job_id,
        stage="collect",
        action="scroll",
        step=2,
        scroll_px=500,
    )
    assert suppressed_scroll.persisted is False
    assert suppressed_scroll.sequence is None
    assert suppressed_scroll.watermark == scroll_sequence
    wait_result = recorder.record_browse(
        job_id=job_id,
        stage="collect",
        action="wait",
        step=3,
        wait_ms=500,
    )
    wait_sequence = wait_result.sequence
    assert wait_result.persisted is True
    suppressed_wait = recorder.record_browse(
        job_id=job_id,
        stage="collect",
        action="wait",
        step=4,
        wait_ms=500,
    )
    assert suppressed_wait.persisted is False
    assert suppressed_wait.sequence is None
    assert suppressed_wait.watermark == wait_sequence
    extract_result = recorder.record_browse(
        job_id=job_id,
        stage="collect",
        action="extract",
        step=5,
        evidence_count=1,
        summary="one safe observation",
    )

    extract_sequence = extract_result.sequence
    assert extract_result.persisted is True
    with db.session() as session:
        all_sequences = [
            row.sequence
            for row in store.list_events(
                session,
                job_id=job_id,
                after_sequence=0,
                limit=100,
            )
        ]
        reconnect_sequences = [
            row.sequence
            for row in store.list_events(
                session,
                job_id=job_id,
                after_sequence=scroll_sequence,
                limit=100,
            )
        ]

    assert all_sequences == [scroll_sequence, wait_sequence, extract_sequence]
    assert reconnect_sequences == [wait_sequence, extract_sequence]

    # The high-frequency budget is exhausted. A later scroll is intentionally
    # not persisted and returns the current committed watermark.
    exhausted = recorder.record_browse(
        job_id=job_id,
        stage="collect",
        action="scroll",
        step=6,
        scroll_px=600,
    )
    assert exhausted.persisted is False
    assert exhausted.sequence is None
    assert exhausted.watermark == extract_sequence
    with db.session() as session:
        assert store.list_events(
            session,
            job_id=job_id,
            after_sequence=extract_sequence,
            limit=100,
        ) == []


def test_error_recorder_uses_registered_public_message_and_ignores_raw_text(db):
    job_id = _seed_job(db)
    recorder = PipelineLiveEventRecorder(db)

    recorder.record_browse(
        job_id=job_id,
        stage="collect",
        action="error",
        step=1,
        error_code="network",
        message="private upstream response body number 500",
        summary="private summary",
        rationale="private model rationale",
    )

    with db.session() as session:
        event = PipelineLiveStore().list_events(
            session,
            job_id=job_id,
            after_sequence=0,
            limit=10,
        )[0]
        assert event.payload_json == {
            "schemaVersion": 1,
            "action": "error",
            "step": 1,
            "errorCode": "network",
            "message": "网络连接暂时不可用",
            "mergedCount": 1,
        }


def test_unknown_error_code_is_not_persisted(db):
    job_id = _seed_job(db)
    recorder = PipelineLiveEventRecorder(db)

    result = recorder.record_stage(
        job_id=job_id,
        stage="collect",
        status="failed",
        error_code="upstream-private-code",
        message="private upstream text",
    )
    assert result.persisted is False
    assert result.sequence is None
    with db.session() as session:
        assert PipelineLiveStore().list_events(
            session,
            job_id=job_id,
            after_sequence=0,
            limit=10,
        ) == []


@pytest.mark.parametrize("status", ["failed", "partial_failed", "interrupted"])
def test_job_error_terminal_without_error_code_is_fail_open_and_not_persisted(
    db, status, caplog
):
    job_id = _seed_job(db)
    recorder = PipelineLiveEventRecorder(db)
    private_exception = "private exception response body 91827"

    with caplog.at_level(logging.WARNING):
        result = recorder.record_lifecycle(
            job_id=job_id,
            status=status,
            message=private_exception,
        )
        assert result.persisted is False
        assert result.sequence is None

    with db.session() as session:
        assert PipelineLiveStore().list_events(
            session,
            job_id=job_id,
            after_sequence=0,
            limit=10,
        ) == []
    assert private_exception not in caplog.text


def test_stage_failed_without_error_code_is_fail_open_and_not_persisted(
    db, caplog
):
    job_id = _seed_job(db)
    recorder = PipelineLiveEventRecorder(db)
    private_exception = "private stage traceback body 77119"

    with caplog.at_level(logging.WARNING):
        result = recorder.record_stage(
            job_id=job_id,
            stage="collect",
            status="failed",
            message=private_exception,
        )
        assert result.persisted is False
        assert result.sequence is None

    with db.session() as session:
        assert PipelineLiveStore().list_events(
            session,
            job_id=job_id,
            after_sequence=0,
            limit=10,
        ) == []
    assert private_exception not in caplog.text


def test_registered_error_code_is_the_only_text_saved_for_failed_lifecycle(db):
    job_id = _seed_job(db)
    recorder = PipelineLiveEventRecorder(db)

    result = recorder.record_stage(
        job_id=job_id,
        stage="collect",
        status="failed",
        error_code="network",
        message="private upstream exception body",
    )

    assert result.persisted is True
    assert isinstance(result.sequence, int)
    with db.session() as session:
        event = PipelineLiveStore().list_events(
            session,
            job_id=job_id,
            after_sequence=0,
            limit=10,
        )[0]
        assert event.level == "error"
        assert event.payload_json == {
            "schemaVersion": 1,
            "status": "failed",
            "errorCode": "network",
            "message": "网络连接暂时不可用",
        }


@pytest.mark.parametrize(
    ("family", "status"),
    [("job", "invented_job_status"), ("stage", "invented_stage_status")],
)
def test_named_lifecycle_recorder_rejects_unregistered_status(db, family, status):
    job_id = _seed_job(db)
    recorder = PipelineLiveEventRecorder(db)

    if family == "job":
        result = recorder.record_lifecycle(job_id=job_id, status=status)
    else:
        result = recorder.record_stage(
            job_id=job_id,
            stage="collect",
            status=status,
        )
    assert result.persisted is False
    assert result.sequence is None
    with db.session() as session:
        assert PipelineLiveStore().list_events(
            session,
            job_id=job_id,
            after_sequence=0,
            limit=10,
        ) == []


def test_recording_failure_is_fail_open_and_logs_no_exception_value(
    db, monkeypatch, caplog
):
    job_id = _seed_job(db)
    store = PipelineLiveStore()
    recorder = PipelineLiveEventRecorder(db, store=store)
    secret = "credential-do-not-log-this-value"

    def fail(*args, **kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(store, "append_event", fail)
    business_result = {"candidates": 7}
    with caplog.at_level(logging.WARNING):
        result = recorder.record_stage(
            job_id=job_id,
            stage="collect",
            status="succeeded",
            result_count=business_result["candidates"],
        )

    assert result.persisted is False
    assert result.sequence is None
    assert business_result == {"candidates": 7}
    assert secret not in caplog.text


@pytest.mark.parametrize("job_id", ["", "   ", None])
def test_named_recorders_reject_missing_job_id_before_database_write(db, job_id):
    recorder = PipelineLiveEventRecorder(db)

    with pytest.raises(ValueError, match="job_id"):
        recorder.record_lifecycle(job_id=job_id, status="running")


def test_telemetry_fails_open_quickly_while_caller_holds_sqlite_write_lock(db):
    job_id = _seed_job(db)
    recorder = PipelineLiveEventRecorder(db, sqlite_busy_timeout_ms=50)
    writer = db.SessionLocal()
    try:
        writer.execute(text("BEGIN IMMEDIATE"))
        writer.execute(
            update(PipelineJob)
            .where(PipelineJob.id == job_id)
            .values(error_summary="caller-write-lock-held")
        )
        started = time.monotonic()
        result = recorder.record_stage(
            job_id=job_id,
            stage="collect",
            status="running",
        )
        elapsed = time.monotonic() - started

        assert result.persisted is False
        assert result.sequence is None
        assert elapsed < 0.5
        assert writer.get(PipelineJob, job_id).error_summary == (
            "caller-write-lock-held"
        )
        assert writer.in_transaction()
    finally:
        writer.rollback()
        writer.close()

    with db.session() as session:
        assert PipelineLiveStore().list_events(
            session,
            job_id=job_id,
            after_sequence=0,
            limit=10,
        ) == []


def test_exhausted_high_frequency_budget_does_not_repeat_count_query(
    db, monkeypatch
):
    job_id = _seed_job(db)
    store = PipelineLiveStore()
    recorder = PipelineLiveEventRecorder(
        db,
        store=store,
        max_high_frequency_events=2,
    )
    assert recorder.record_browse(
        job_id=job_id,
        stage="collect",
        action="scroll",
        step=1,
        scroll_px=500,
    ).persisted
    assert recorder.record_browse(
        job_id=job_id,
        stage="collect",
        action="wait",
        step=2,
        wait_ms=500,
    ).persisted

    def unexpected_count(*args, **kwargs):
        raise AssertionError("high-frequency count must be cached after exhaustion")

    monkeypatch.setattr(store, "count_event_types", unexpected_count)
    suppressed = recorder.record_browse(
        job_id=job_id,
        stage="collect",
        action="scroll",
        step=3,
        scroll_px=600,
    )
    assert suppressed.persisted is False
    assert suppressed.sequence is None


def test_commit_failure_does_not_advance_watermark_or_exhaust_budget(
    db, monkeypatch
):
    job_id = _seed_job(db)
    store = PipelineLiveStore()
    recorder = PipelineLiveEventRecorder(
        db,
        store=store,
        max_high_frequency_events=1,
    )
    baseline = recorder.record_lifecycle(job_id=job_id, status="running")
    assert baseline.persisted is True
    original_session = recorder._session
    flushed_sequences: list[int] = []
    original_append = store.append_event

    def capture_flushed_event(*args, **kwargs):
        event = original_append(*args, **kwargs)
        flushed_sequences.append(event.sequence)
        return event

    @contextmanager
    def fail_after_flush():
        session = db.SessionLocal()
        try:
            yield session
            raise RuntimeError("simulated commit failure")
        finally:
            session.rollback()
            session.close()

    monkeypatch.setattr(store, "append_event", capture_flushed_event)
    monkeypatch.setattr(recorder, "_session", fail_after_flush)
    failed = recorder.record_browse(
        job_id=job_id,
        stage="collect",
        action="scroll",
        step=1,
        scroll_px=500,
    )

    assert flushed_sequences
    assert failed.persisted is False
    assert failed.sequence is None
    assert failed.watermark == baseline.sequence
    assert job_id not in recorder._exhausted_high_frequency_jobs

    monkeypatch.setattr(recorder, "_session", original_session)
    retry = recorder.record_browse(
        job_id=job_id,
        stage="collect",
        action="scroll",
        step=2,
        scroll_px=600,
    )
    assert retry.persisted is True
    assert retry.sequence is not None
    assert retry.watermark == retry.sequence


def test_terminal_commit_failure_keeps_cache_until_successful_retry(
    db, monkeypatch
):
    job_id = _seed_job(db)
    recorder = PipelineLiveEventRecorder(db, max_high_frequency_events=1)
    browse = recorder.record_browse(
        job_id=job_id,
        stage="collect",
        action="scroll",
        step=1,
        scroll_px=500,
    )
    assert browse.persisted is True
    assert job_id in recorder._exhausted_high_frequency_jobs
    original_session = recorder._session

    @contextmanager
    def fail_after_flush():
        session = db.SessionLocal()
        try:
            yield session
            raise RuntimeError("simulated terminal commit failure")
        finally:
            session.rollback()
            session.close()

    monkeypatch.setattr(recorder, "_session", fail_after_flush)
    failed = recorder.record_lifecycle(job_id=job_id, status="succeeded")
    assert failed.persisted is False
    assert failed.watermark == browse.sequence
    assert recorder._job_watermarks[job_id] == browse.sequence
    assert job_id in recorder._exhausted_high_frequency_jobs

    monkeypatch.setattr(recorder, "_session", original_session)
    terminal = recorder.record_lifecycle(job_id=job_id, status="succeeded")
    assert terminal.persisted is True
    assert job_id not in recorder._job_watermarks
    assert job_id not in recorder._exhausted_high_frequency_jobs


@pytest.mark.parametrize(
    ("status", "error_code"),
    [
        ("succeeded", ""),
        ("cancelled", ""),
        ("failed", "network"),
        ("partial_failed", "network"),
        ("interrupted", "network"),
    ],
)
def test_committed_terminal_status_clears_cache_and_recovers_db_watermark(
    db, status, error_code
):
    job_id = _seed_job(db)
    recorder = PipelineLiveEventRecorder(db, max_high_frequency_events=1)
    browse = recorder.record_browse(
        job_id=job_id,
        stage="collect",
        action="scroll",
        step=1,
        scroll_px=500,
    )
    assert browse.persisted is True

    terminal = recorder.record_lifecycle(
        job_id=job_id,
        status=status,
        error_code=error_code,
    )
    assert terminal.persisted is True
    assert job_id not in recorder._job_watermarks
    assert job_id not in recorder._exhausted_high_frequency_jobs

    suppressed = recorder.record_browse(
        job_id=job_id,
        stage="collect",
        action="wait",
        step=2,
        wait_ms=500,
    )
    assert suppressed.persisted is False
    assert suppressed.sequence is None
    assert suppressed.watermark == terminal.sequence


def test_terminal_events_release_cache_for_many_jobs(db):
    recorder = PipelineLiveEventRecorder(db, max_high_frequency_events=1)
    with db.session() as session:
        jobs = [
            PipelineJob(platform="douyin", stages_json=["collect"])
            for _ in range(64)
        ]
        session.add_all(jobs)
        session.flush()
        job_ids = [job.id for job in jobs]

    for job_id in job_ids:
        result = recorder.record_browse(
            job_id=job_id,
            stage="collect",
            action="scroll",
            step=1,
            scroll_px=500,
        )
        assert result.persisted is True
    assert len(recorder._job_watermarks) == len(job_ids)
    assert len(recorder._exhausted_high_frequency_jobs) == len(job_ids)

    for job_id in job_ids:
        result = recorder.record_lifecycle(job_id=job_id, status="succeeded")
        assert result.persisted is True
    assert recorder._job_watermarks == {}
    assert recorder._exhausted_high_frequency_jobs == set()
