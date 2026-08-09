"""Durable, job-scoped Pipeline live event storage tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier

import pytest
from sqlalchemy import delete, inspect, select, text
from sqlalchemy.exc import IntegrityError

from tiktok_bot_core.models import (
    PipelineDecisionCheckpoint,
    PipelineJobEvent,
)
from tiktok_bot_core.models.entities import PipelineJob
from tiktok_bot_core.storage.database import Database
from tiktok_bot_core.storage.pipeline_live_store import (
    CheckpointConflictError,
    PipelineLiveStore,
    PipelineLiveValidationError,
)


@pytest.fixture
def db(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'pipeline-live.db'}")
    database.init()
    yield database
    database.engine.dispose()


def _seed_jobs(database: Database) -> tuple[str, str]:
    with database.session() as session:
        first = PipelineJob(platform="douyin", stages_json=["collect"])
        second = PipelineJob(platform="douyin", stages_json=["collect"])
        session.add_all([first, second])
        session.flush()
        return first.id, second.id


def _append(
    store: PipelineLiveStore,
    session,
    *,
    job_id: str,
    step: int,
) -> PipelineJobEvent:
    return store.append_event(
        session,
        job_id=job_id,
        stage="collect",
        event_type="browse.scroll",
        level="info",
        payload={
            "schemaVersion": 1,
            "action": "scroll",
            "step": step,
            "scrollPx": 500,
            "mergedCount": 1,
        },
    )


def test_event_sequences_are_monotonic_incremental_and_job_scoped(db):
    store = PipelineLiveStore()
    first_job, second_job = _seed_jobs(db)

    with db.session() as session:
        first = _append(store, session, job_id=first_job, step=1)
        other = _append(store, session, job_id=second_job, step=1)
        second = _append(store, session, job_id=first_job, step=2)
        sequences = (first.sequence, other.sequence, second.sequence)

    assert sequences == tuple(sorted(sequences))
    assert len(set(sequences)) == 3

    with db.session() as session:
        first_page = store.list_events(
            session,
            job_id=first_job,
            after_sequence=0,
            limit=1,
        )
        second_page = store.list_events(
            session,
            job_id=first_job,
            after_sequence=first_page[-1].sequence,
            limit=10,
        )
        other_page = store.list_events(
            session,
            job_id=second_job,
            after_sequence=0,
            limit=10,
        )

        assert [row.sequence for row in first_page + second_page] == [
            sequences[0],
            sequences[2],
        ]
        assert [row.sequence for row in other_page] == [sequences[1]]
        assert store.count_events(session, job_id=first_job) == 2
        assert store.count_events(session, job_id="missing-job") == 0
        assert store.list_events(
            session,
            job_id="missing-job",
            after_sequence=0,
            limit=10,
        ) == []


@pytest.mark.parametrize(
    ("after_sequence", "limit"),
    [(-1, 10), (0, 0), (0, 501), (True, 10), (0, True)],
)
def test_event_pagination_rejects_invalid_boundaries(
    db, after_sequence, limit
):
    store = PipelineLiveStore()
    job_id, _ = _seed_jobs(db)

    with pytest.raises(PipelineLiveValidationError):
        with db.session() as session:
            store.list_events(
                session,
                job_id=job_id,
                after_sequence=after_sequence,
                limit=limit,
            )


@pytest.mark.parametrize(
    "payload",
    [
        {"schemaVersion": 1, "action": "scroll", "cookie": "cookie-secret"},
        {"schemaVersion": 1, "action": "scroll", "nested": {"accessToken": "token-secret"}},
        {"schemaVersion": 1, "action": "scroll", "Authorization": "Bearer auth-secret"},
        {"schemaVersion": 1, "action": "scroll", "api_key": "key-secret"},
        {"schemaVersion": 1, "action": "scroll", "profilePath": "C:/private/profile"},
        {"schemaVersion": 1, "action": "scroll", "prompt": "prompt-secret"},
        {"schemaVersion": 1, "action": "scroll", "modelResponse": "response-secret"},
        {"schemaVersion": 1, "action": "scroll", "credentials": {"value": "credential-secret"}},
    ],
)
def test_event_payload_recursively_rejects_sensitive_or_unknown_fields(
    db, payload
):
    store = PipelineLiveStore()
    job_id, _ = _seed_jobs(db)
    secret = next(
        value
        for value in (
            "cookie-secret",
            "token-secret",
            "auth-secret",
            "key-secret",
            "C:/private/profile",
            "prompt-secret",
            "response-secret",
            "credential-secret",
        )
        if value in repr(payload)
    )

    with pytest.raises(PipelineLiveValidationError) as caught:
        with db.session() as session:
            store.append_event(
                session,
                job_id=job_id,
                stage="collect",
                event_type="browse.scroll",
                level="info",
                payload=payload,
            )

    assert secret not in str(caught.value)


def test_event_payload_accepts_only_the_registered_builder_fields(db):
    store = PipelineLiveStore()
    job_id, _ = _seed_jobs(db)

    with pytest.raises(
        PipelineLiveValidationError,
        match="unsupported event payload fields",
    ):
        with db.session() as session:
            store.append_event(
                session,
                job_id=job_id,
                stage="collect",
                event_type="browse.scroll",
                level="info",
                payload={
                    "schemaVersion": 1,
                    "action": "scroll",
                    "madeUpInternalObject": {"debug": "not-for-ui"},
                },
            )


@pytest.mark.parametrize(
    ("event_type", "payload"),
    [
        (
            "job.lifecycle",
            {"schemaVersion": 1, "status": {"value": "running"}},
        ),
        (
            "stage.lifecycle",
            {"schemaVersion": 1, "status": "running", "message": ["bad"]},
        ),
        (
            "browse.extract",
            {
                "schemaVersion": 1,
                "action": "extract",
                "step": 1,
                "summary": {"debug": "raw body"},
            },
        ),
        (
            "browse.navigate",
            {
                "schemaVersion": 1,
                "action": "navigate",
                "step": 1,
                "rationale": ["not", "text"],
            },
        ),
        (
            "browse.scroll",
            {
                "schemaVersion": 1,
                "action": "scroll",
                "step": {"number": 1},
                "scrollPx": 500,
            },
        ),
        (
            "browse.scroll",
            {
                "schemaVersion": 1,
                "action": "scroll",
                "step": 1,
                "budget": {"stepsUsed": {"value": 1}},
            },
        ),
        (
            "candidate.lifecycle",
            {
                "schemaVersion": 1,
                "userId": 1,
                "status": "manual_review",
                "labels": {"buyer": True},
            },
        ),
        (
            "job.lifecycle",
            {"schemaVersion": 1, "status": "x" * 65},
        ),
        (
            "stage.lifecycle",
            {
                "schemaVersion": 1,
                "status": "running",
                "message": "x" * 501,
            },
        ),
    ],
)
def test_each_event_field_has_a_strict_type_and_length_contract(
    db, event_type, payload
):
    store = PipelineLiveStore()
    job_id, _ = _seed_jobs(db)

    with pytest.raises(PipelineLiveValidationError):
        with db.session() as session:
            store.append_event(
                session,
                job_id=job_id,
                stage="collect",
                event_type=event_type,
                level="info",
                payload=payload,
            )


@pytest.mark.parametrize(
    "context",
    [
        {"schemaVersion": 1, "title": {"text": "review"}},
        {"schemaVersion": 1, "question": ["continue?"]},
        {"schemaVersion": 1, "summary": {"raw": "payload"}},
        {"schemaVersion": 1, "defaultReason": ["automatic"]},
        {"schemaVersion": 1, "blockingReason": {"code": "risk"}},
        {"schemaVersion": 1, "metrics": {"total": "twenty"}},
        {"schemaVersion": 1, "title": "x" * 161},
    ],
)
def test_each_checkpoint_context_field_has_a_strict_contract(db, context):
    store = PipelineLiveStore()
    job_id, _ = _seed_jobs(db)

    with pytest.raises(PipelineLiveValidationError):
        with db.session() as session:
            store.create_checkpoint(
                session,
                job_id=job_id,
                stage="filter",
                kind="qualification_review",
                option_keys=["continue_with_qualified_only"],
                default_option_key="continue_with_qualified_only",
                context=context,
                deadline_at=datetime.utcnow() + timedelta(seconds=10),
            )


def test_error_event_rejects_arbitrary_text_even_when_fields_are_known(db):
    store = PipelineLiveStore()
    job_id, _ = _seed_jobs(db)

    with pytest.raises(PipelineLiveValidationError):
        with db.session() as session:
            store.append_event(
                session,
                job_id=job_id,
                stage="collect",
                event_type="browse.error",
                level="error",
                payload={
                    "schemaVersion": 1,
                    "action": "error",
                    "step": 2,
                    "errorCode": "network",
                    "message": "private upstream response body number 500",
                    "summary": "private diagnostic summary",
                    "rationale": "private model rationale",
                    "mergedCount": 1,
                },
            )


@pytest.mark.parametrize(
    ("event_type", "status"),
    [
        ("job.lifecycle", "failed"),
        ("job.lifecycle", "partial_failed"),
        ("job.lifecycle", "interrupted"),
        ("stage.lifecycle", "failed"),
    ],
)
def test_error_terminal_lifecycle_status_requires_registered_error_code(
    db, event_type, status
):
    store = PipelineLiveStore()
    job_id, _ = _seed_jobs(db)

    with pytest.raises(PipelineLiveValidationError):
        with db.session() as session:
            store.append_event(
                session,
                job_id=job_id,
                stage="collect" if event_type == "stage.lifecycle" else "",
                event_type=event_type,
                level="info",
                payload={
                    "schemaVersion": 1,
                    "status": status,
                    "message": "private exception response body",
                },
            )


@pytest.mark.parametrize(
    ("event_type", "status"),
    [
        ("job.lifecycle", "invented_job_status"),
        ("stage.lifecycle", "invented_stage_status"),
        ("decision.lifecycle", "invented_checkpoint_status"),
        ("candidate.lifecycle", "invented_candidate_status"),
    ],
)
def test_lifecycle_status_is_strictly_enumerated(db, event_type, status):
    store = PipelineLiveStore()
    job_id, _ = _seed_jobs(db)
    payload = {"schemaVersion": 1, "status": status}
    if event_type == "decision.lifecycle":
        payload.update(
            {"checkpointId": "checkpoint-1", "kind": "qualification_review"}
        )
    elif event_type == "candidate.lifecycle":
        payload["userId"] = 42

    with pytest.raises(PipelineLiveValidationError):
        with db.session() as session:
            store.append_event(
                session,
                job_id=job_id,
                stage="collect",
                event_type=event_type,
                level="info",
                payload=payload,
            )


def test_checkpoint_single_pending_is_enforced_by_database_and_store(db):
    store = PipelineLiveStore()
    job_id, _ = _seed_jobs(db)
    deadline = datetime.utcnow() + timedelta(seconds=10)

    with db.session() as session:
        first = store.create_checkpoint(
            session,
            job_id=job_id,
            stage="filter",
            kind="qualification_review",
            option_keys=["open_review_workbench", "continue_with_qualified_only"],
            default_option_key="continue_with_qualified_only",
            context={"schemaVersion": 1, "summary": "20 candidates"},
            deadline_at=deadline,
        )
        first_id = first.id

    with pytest.raises(CheckpointConflictError):
        with db.session() as session:
            store.create_checkpoint(
                session,
                job_id=job_id,
                stage="filter",
                kind="qualification_review",
                option_keys=["continue_with_qualified_only"],
                default_option_key="continue_with_qualified_only",
                context={"schemaVersion": 1, "summary": "duplicate"},
                deadline_at=deadline,
            )

    # The invariant must exist in SQLite too; callers cannot bypass it by
    # skipping the Store's application-level checks.
    with pytest.raises(IntegrityError):
        with db.session() as session:
            session.add(
                PipelineDecisionCheckpoint(
                    job_id=job_id,
                    stage="filter",
                    kind="qualification_review",
                    option_keys_json=["continue_with_qualified_only"],
                    default_option_key="continue_with_qualified_only",
                    context_json={"schemaVersion": 1},
                    deadline_at=deadline,
                    status="pending",
                )
            )

    with db.session() as session:
        active = store.get_active_checkpoint(session, job_id=job_id)
        assert active is not None
        assert active.id == first_id
        resolved = store.resolve_checkpoint(
            session,
            job_id=job_id,
            checkpoint_id=first_id,
            option_key="continue_with_qualified_only",
            version=1,
            resolution_source="human",
            operator="reviewer@example.test",
        )
        assert resolved.status == "resolved"
        assert resolved.resolution_source == "human"

    with db.session() as session:
        second = store.create_checkpoint(
            session,
            job_id=job_id,
            stage="outreach",
            kind="outreach_confirmation",
            option_keys=["execute_approved_outreach", "skip_outreach"],
            default_option_key="execute_approved_outreach",
            context={"schemaVersion": 1, "summary": "approved targets only"},
            deadline_at=deadline,
        )
        assert second.id != first_id


def test_checkpoint_resolution_is_job_version_option_and_terminal_safe(db):
    store = PipelineLiveStore()
    job_id, other_job_id = _seed_jobs(db)
    deadline = datetime.utcnow() + timedelta(seconds=10)

    with db.session() as session:
        checkpoint = store.create_checkpoint(
            session,
            job_id=job_id,
            stage="collect",
            kind="insufficient_evidence",
            option_keys=["continue_with_current_evidence", "cancel_job"],
            default_option_key="continue_with_current_evidence",
            context={"schemaVersion": 1, "summary": "low evidence"},
            deadline_at=deadline,
        )
        checkpoint_id = checkpoint.id

    for kwargs in (
        {"job_id": other_job_id, "option_key": "continue_with_current_evidence", "version": 1},
        {"job_id": job_id, "option_key": "invented_action", "version": 1},
        {"job_id": job_id, "option_key": "continue_with_current_evidence", "version": 2},
    ):
        with pytest.raises((PipelineLiveValidationError, CheckpointConflictError)):
            with db.session() as session:
                store.resolve_checkpoint(
                    session,
                    checkpoint_id=checkpoint_id,
                    resolution_source="human",
                    **kwargs,
                )

    with db.session() as session:
        resolved = store.resolve_checkpoint(
            session,
            job_id=job_id,
            checkpoint_id=checkpoint_id,
            option_key="continue_with_current_evidence",
            version=1,
            resolution_source="timeout",
        )
        assert resolved.status == "expired"
        assert resolved.resolution_key == "continue_with_current_evidence"

    with pytest.raises(CheckpointConflictError):
        with db.session() as session:
            store.resolve_checkpoint(
                session,
                job_id=job_id,
                checkpoint_id=checkpoint_id,
                option_key="continue_with_current_evidence",
                version=1,
                resolution_source="human",
            )


def test_new_live_tables_are_created_by_metadata(db):
    inspector = inspect(db.engine)
    assert "pipeline_job_events" in inspector.get_table_names()
    assert "pipeline_decision_checkpoints" in inspector.get_table_names()

    with db.session() as session:
        assert session.scalar(select(PipelineJobEvent.sequence)) is None

    event_indexes = inspector.get_indexes("pipeline_job_events")
    assert any(
        index["column_names"] == ["job_id", "event_type"]
        for index in event_indexes
    )


def test_event_sequence_is_not_reused_after_the_current_maximum_is_deleted(db):
    store = PipelineLiveStore()
    job_id, _ = _seed_jobs(db)

    with db.session() as session:
        first = _append(store, session, job_id=job_id, step=1)
        second = _append(store, session, job_id=job_id, step=2)
        first_sequence = first.sequence
        second_sequence = second.sequence

    with db.session() as session:
        session.execute(
            delete(PipelineJobEvent).where(
                PipelineJobEvent.sequence == second_sequence
            )
        )

    with db.session() as session:
        third = _append(store, session, job_id=job_id, step=3)
        assert first_sequence < second_sequence < third.sequence


def test_two_sessions_racing_to_create_pending_checkpoint_have_one_winner(db):
    store = PipelineLiveStore()
    job_id, _ = _seed_jobs(db)
    barrier = Barrier(2)
    deadline = datetime.utcnow() + timedelta(seconds=10)

    def create_pending(index: int) -> tuple[str, str]:
        try:
            with db.session() as session:
                barrier.wait(timeout=5)
                checkpoint = store.create_checkpoint(
                    session,
                    job_id=job_id,
                    stage="filter",
                    kind=f"qualification_review_{index}",
                    option_keys=["continue_with_qualified_only"],
                    default_option_key="continue_with_qualified_only",
                    context={"schemaVersion": 1, "summary": "review"},
                    deadline_at=deadline,
                )
                return "created", checkpoint.id
        except CheckpointConflictError:
            return "conflict", ""

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(create_pending, (1, 2)))

    assert sorted(outcome for outcome, _ in outcomes) == ["conflict", "created"]
    with db.session() as session:
        assert store.get_active_checkpoint(session, job_id=job_id) is not None


def _seed_pending_checkpoint(db, store: PipelineLiveStore, job_id: str) -> str:
    with db.session() as session:
        checkpoint = store.create_checkpoint(
            session,
            job_id=job_id,
            stage="collect",
            kind="insufficient_evidence",
            option_keys=["continue_with_current_evidence", "cancel_job"],
            default_option_key="continue_with_current_evidence",
            context={"schemaVersion": 1, "summary": "low evidence"},
            deadline_at=datetime.utcnow() + timedelta(seconds=10),
        )
        return checkpoint.id


def test_human_and_timeout_resolution_race_has_one_authoritative_winner(db):
    store = PipelineLiveStore()
    job_id, _ = _seed_jobs(db)
    checkpoint_id = _seed_pending_checkpoint(db, store, job_id)
    barrier = Barrier(2)

    def resolve(source: str) -> tuple[str, str]:
        try:
            with db.session() as session:
                barrier.wait(timeout=5)
                checkpoint = store.resolve_checkpoint(
                    session,
                    job_id=job_id,
                    checkpoint_id=checkpoint_id,
                    option_key=(
                        "cancel_job"
                        if source == "human"
                        else "continue_with_current_evidence"
                    ),
                    version=1,
                    resolution_source=source,
                )
                return "resolved", checkpoint.resolution_source or ""
        except CheckpointConflictError:
            return "conflict", ""

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(resolve, ("human", "timeout")))

    assert sorted(outcome for outcome, _ in outcomes) == ["conflict", "resolved"]
    winner = next(source for outcome, source in outcomes if outcome == "resolved")
    with db.session() as session:
        authoritative = store.get_checkpoint(
            session,
            job_id=job_id,
            checkpoint_id=checkpoint_id,
        )
        assert authoritative is not None
        assert authoritative.resolution_source == winner
        assert authoritative.status == (
            "expired" if winner == "timeout" else "resolved"
        )


def test_resolve_and_cancel_race_has_one_authoritative_winner(db):
    store = PipelineLiveStore()
    job_id, _ = _seed_jobs(db)
    checkpoint_id = _seed_pending_checkpoint(db, store, job_id)
    barrier = Barrier(2)

    def resolve() -> str:
        try:
            with db.session() as session:
                barrier.wait(timeout=5)
                store.resolve_checkpoint(
                    session,
                    job_id=job_id,
                    checkpoint_id=checkpoint_id,
                    option_key="continue_with_current_evidence",
                    version=1,
                    resolution_source="human",
                )
                return "resolved"
        except CheckpointConflictError:
            return "lost"

    def cancel() -> str:
        with db.session() as session:
            barrier.wait(timeout=5)
            checkpoint = store.cancel_checkpoint(
                session,
                job_id=job_id,
                checkpoint_id=checkpoint_id,
                reason="job_cancelled",
            )
            return "cancelled" if checkpoint is not None else "lost"

    with ThreadPoolExecutor(max_workers=2) as executor:
        resolve_future = executor.submit(resolve)
        cancel_future = executor.submit(cancel)
        outcomes = [resolve_future.result(), cancel_future.result()]

    assert outcomes.count("lost") == 1
    assert set(outcomes) & {"resolved", "cancelled"}
    with db.session() as session:
        authoritative = store.get_checkpoint(
            session,
            job_id=job_id,
            checkpoint_id=checkpoint_id,
        )
        assert authoritative is not None
        assert authoritative.status in {"resolved", "cancelled"}
        if "cancelled" in outcomes:
            assert authoritative.status == "cancelled"
            assert authoritative.resolution_source == "system"
        else:
            assert authoritative.status == "resolved"
            assert authoritative.resolution_source == "human"


@pytest.mark.parametrize("operation", ["resolve", "cancel"])
def test_checkpoint_cas_does_not_flush_or_expire_unrelated_session_state(
    db, operation
):
    store = PipelineLiveStore()
    job_id, other_job_id = _seed_jobs(db)
    checkpoint_id = _seed_pending_checkpoint(db, store, job_id)
    session = db.SessionLocal()
    try:
        unrelated = session.get(PipelineJob, other_job_id)
        assert unrelated is not None
        unrelated.error_summary = "unrelated-dirty-value"
        invalid_unflushed = PipelineJobEvent(
            job_id=job_id,
            stage="collect",
            event_type=None,
            level="info",
            payload_json={},
        )
        session.add(invalid_unflushed)

        if operation == "resolve":
            checkpoint = store.resolve_checkpoint(
                session,
                job_id=job_id,
                checkpoint_id=checkpoint_id,
                option_key="continue_with_current_evidence",
                version=1,
                resolution_source="human",
            )
            assert checkpoint.status == "resolved"
        else:
            checkpoint = store.cancel_checkpoint(
                session,
                job_id=job_id,
                checkpoint_id=checkpoint_id,
                reason="job_cancelled",
            )
            assert checkpoint is not None
            assert checkpoint.status == "cancelled"

        assert invalid_unflushed.sequence is None
        assert invalid_unflushed in session.new
        assert unrelated in session.dirty
        assert inspect(unrelated).expired is False
        assert unrelated.error_summary == "unrelated-dirty-value"
    finally:
        session.rollback()
        session.close()


def test_non_pending_unique_integrity_error_is_not_misreported_as_conflict(db):
    store = PipelineLiveStore()
    job_id, _ = _seed_jobs(db)
    with db.engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TRIGGER force_other_checkpoint_integrity "
                "BEFORE INSERT ON pipeline_decision_checkpoints "
                "WHEN NEW.kind = 'forced_integrity' "
                "BEGIN SELECT RAISE(ABORT, 'forced other integrity'); END"
            )
        )

    with pytest.raises(IntegrityError):
        with db.session() as session:
            store.create_checkpoint(
                session,
                job_id=job_id,
                stage="filter",
                kind="forced_integrity",
                option_keys=["continue_with_qualified_only"],
                default_option_key="continue_with_qualified_only",
                context={"schemaVersion": 1, "summary": "review"},
                deadline_at=datetime.utcnow() + timedelta(seconds=10),
            )


def test_init_adds_live_tables_and_pending_index_without_losing_old_job(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'legacy-pipeline.db'}")
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE pipeline_jobs ("
                "id VARCHAR(36) PRIMARY KEY, "
                "platform VARCHAR(20) NOT NULL, "
                "stages_json JSON NOT NULL)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO pipeline_jobs (id, platform, stages_json) "
                "VALUES ('legacy-job', 'douyin', '[\"collect\"]')"
            )
        )

    database.init()
    inspector = inspect(database.engine)
    assert "pipeline_job_events" in inspector.get_table_names()
    assert "pipeline_decision_checkpoints" in inspector.get_table_names()
    pending_index = next(
        index
        for index in inspector.get_indexes("pipeline_decision_checkpoints")
        if index["name"] == "uq_pipeline_checkpoint_job_pending"
    )
    assert pending_index["unique"] == 1
    with database.engine.connect() as connection:
        assert connection.scalar(
            text("SELECT COUNT(*) FROM pipeline_jobs WHERE id = 'legacy-job'")
        ) == 1
    database.engine.dispose()
