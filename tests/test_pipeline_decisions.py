"""Server-side Pipeline decision gate state machine tests."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import threading

import pytest
from sqlalchemy import select

from tiktok_bot_core.models.entities import (
    PipelineDecisionCheckpoint,
    PipelineJobUser,
    User,
)
from tiktok_bot_core.models.pipeline_states import (
    JOB_STATUS_CANCELLED,
    JOB_STATUS_INTERRUPTED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_WAITING_DECISION,
    STAGE_STATUS_CANCELLED,
    STAGE_STATUS_FAILED,
    STAGE_STATUS_RUNNING,
    STAGE_STATUS_WAITING_DECISION,
)
from tiktok_bot_core.services.pipeline_decisions import (
    CHECKPOINT_DEFINITIONS,
    DecisionGateCancelledError,
    DecisionGateConflictError,
    DecisionGateService,
    DecisionGateValidationError,
    _Waiter,
)
from tiktok_bot_core.services.pipeline_live_events import (
    PipelineLiveEventRecorder,
)
from tiktok_bot_core.storage.database import Database
from tiktok_bot_core.storage.pipeline_job_store import PipelineJobStore
from tiktok_bot_core.storage.pipeline_live_store import PipelineLiveStore


@pytest.fixture
def db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as file:
        path = Path(file.name)
    database = Database(f"sqlite:///{path}")
    database.init()
    yield database
    database.engine.dispose()
    try:
        path.unlink()
    except PermissionError:
        pass


class FakeClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 8, 9, 12, 0, 0)
        self.delays: list[float] = []

    def now(self) -> datetime:
        return self.current

    async def sleep(self, delay: float) -> None:
        self.delays.append(delay)
        self.current += timedelta(seconds=delay)
        await asyncio.sleep(0)


class CapturingDecisionRecorder:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict] = []

    def record_decision(self, **kwargs):
        self.calls.append(dict(kwargs))
        if self.fail:
            raise RuntimeError("telemetry unavailable")


def _seed_running_stage(database: Database) -> tuple[str, str]:
    store = PipelineJobStore()
    with database.session() as session:
        job = store.create_job(
            session,
            platform="douyin",
            account_mode="auto",
            account_id=None,
            stages=["collect"],
        )
        claimed = store.claim_next_job(session, platforms={"douyin"})
        assert claimed is not None and claimed.id == job.id
        stage = store.start_stage(session, job.id, "collect")
        assert stage is not None
        return job.id, stage.stage


async def _wait_for_checkpoint(database: Database, job_id: str):
    live_store = PipelineLiveStore()
    for _ in range(100):
        with database.session() as session:
            checkpoint = live_store.get_active_checkpoint(
                session,
                job_id=job_id,
            )
            if checkpoint is not None:
                return checkpoint.id, checkpoint.version
        await asyncio.sleep(0)
    raise AssertionError("decision checkpoint was not created")


@pytest.mark.asyncio
async def test_decision_recorder_emits_pending_then_one_human_resolution(db):
    recorder = CapturingDecisionRecorder()
    gate = DecisionGateService(
        db,
        timeout_seconds=60,
        poll_interval_seconds=0.01,
        event_recorder=recorder,
    )
    job_id, stage = _seed_running_stage(db)
    waiting = asyncio.create_task(
        gate.await_decision(
            job_id=job_id,
            stage=stage,
            kind="insufficient_evidence",
            option_keys=("continue_with_current_evidence",),
            default_option_key="continue_with_current_evidence",
            context={"summary": "evidence is limited"},
        )
    )
    checkpoint_id, version = await _wait_for_checkpoint(db, job_id)
    with db.session() as session:
        checkpoint = PipelineLiveStore().get_checkpoint(
            session,
            job_id=job_id,
            checkpoint_id=checkpoint_id,
        )
        deadline_at = checkpoint.deadline_at

    assert recorder.calls == [
        {
            "job_id": job_id,
            "stage": stage,
            "checkpoint_id": checkpoint_id,
            "kind": "insufficient_evidence",
            "status": "pending",
            "default_option_key": "continue_with_current_evidence",
            "deadline_at": deadline_at,
        }
    ]
    gate.resolve(
        job_id=job_id,
        checkpoint_id=checkpoint_id,
        option_key="continue_with_current_evidence",
        version=version,
    )
    resolution = await waiting

    assert resolution.source == "human"
    terminal = [call for call in recorder.calls if call["status"] != "pending"]
    assert terminal == [
        {
            "job_id": job_id,
            "stage": stage,
            "checkpoint_id": checkpoint_id,
            "kind": "insufficient_evidence",
            "status": "resolved",
            "deadline_at": deadline_at,
            "resolution_key": "continue_with_current_evidence",
            "resolution_source": "human",
        }
    ]


@pytest.mark.asyncio
async def test_decision_timeout_records_single_authoritative_expiry(db):
    recorder = CapturingDecisionRecorder()
    clock = FakeClock()
    gate = DecisionGateService(
        db,
        timeout_seconds=0.01,
        poll_interval_seconds=0.01,
        clock=clock.now,
        sleeper=clock.sleep,
        event_recorder=recorder,
    )
    job_id, stage = _seed_running_stage(db)

    resolution = await gate.await_decision(
        job_id=job_id,
        stage=stage,
        kind="insufficient_evidence",
        option_keys=("continue_with_current_evidence",),
        default_option_key="continue_with_current_evidence",
        context={"summary": "evidence is limited"},
    )

    assert resolution.source == "timeout"
    terminal = [call for call in recorder.calls if call["status"] != "pending"]
    assert len(terminal) == 1
    assert terminal[0]["checkpoint_id"] == resolution.checkpoint_id
    assert terminal[0]["status"] == "expired"
    assert terminal[0]["resolution_source"] == "timeout"


@pytest.mark.asyncio
async def test_decision_recorder_failure_is_fail_open(db):
    recorder = CapturingDecisionRecorder(fail=True)
    clock = FakeClock()
    gate = DecisionGateService(
        db,
        timeout_seconds=0.01,
        poll_interval_seconds=0.01,
        clock=clock.now,
        sleeper=clock.sleep,
        event_recorder=recorder,
    )
    job_id, stage = _seed_running_stage(db)

    resolution = await gate.await_decision(
        job_id=job_id,
        stage=stage,
        kind="insufficient_evidence",
        option_keys=("continue_with_current_evidence",),
        default_option_key="continue_with_current_evidence",
        context={"summary": "evidence is limited"},
    )

    assert resolution.source == "timeout"
    assert resolution.option_key == "continue_with_current_evidence"
    with db.session() as session:
        job = PipelineJobStore().get_job(session, job_id)
        assert job.status == JOB_STATUS_RUNNING


@pytest.mark.asyncio
async def test_decision_cancel_records_one_authoritative_terminal_event(db):
    recorder = CapturingDecisionRecorder()
    gate = DecisionGateService(
        db,
        timeout_seconds=60,
        poll_interval_seconds=0.01,
        event_recorder=recorder,
    )
    job_id, stage = _seed_running_stage(db)
    waiting = asyncio.create_task(
        gate.await_decision(
            job_id=job_id,
            stage=stage,
            kind="insufficient_evidence",
            option_keys=("continue_with_current_evidence",),
            default_option_key="continue_with_current_evidence",
            context={"summary": "evidence is limited"},
        )
    )
    checkpoint_id, _version = await _wait_for_checkpoint(db, job_id)

    result = gate.cancel_job(job_id)
    assert result is not None and result.status == JOB_STATUS_CANCELLED
    with pytest.raises(DecisionGateCancelledError):
        await waiting

    terminal = [call for call in recorder.calls if call["status"] != "pending"]
    assert len(terminal) == 1
    assert terminal[0]["checkpoint_id"] == checkpoint_id
    assert terminal[0]["status"] == "cancelled"
    assert terminal[0]["resolution_source"] == "system"


@pytest.mark.asyncio
async def test_decision_cancel_persists_one_authoritative_terminal_event(db):
    gate = DecisionGateService(
        db,
        timeout_seconds=60,
        poll_interval_seconds=0.01,
        event_recorder=PipelineLiveEventRecorder(db),
    )
    job_id, stage = _seed_running_stage(db)
    waiting = asyncio.create_task(
        gate.await_decision(
            job_id=job_id,
            stage=stage,
            kind="insufficient_evidence",
            option_keys=("continue_with_current_evidence",),
            default_option_key="continue_with_current_evidence",
            context={"summary": "evidence is limited"},
        )
    )
    checkpoint_id, _version = await _wait_for_checkpoint(db, job_id)

    result = gate.cancel_job(job_id)
    assert result is not None and result.status == JOB_STATUS_CANCELLED
    with pytest.raises(DecisionGateCancelledError):
        await waiting

    contradictory = PipelineLiveEventRecorder(db).record_decision(
        job_id=job_id,
        stage=stage,
        checkpoint_id=checkpoint_id,
        kind="insufficient_evidence",
        status="resolved",
        resolution_key="continue_with_current_evidence",
        resolution_source="human",
    )
    assert contradictory.persisted is False

    with db.session() as session:
        decision_events = [
            dict(event.payload_json)
            for event in PipelineLiveStore().list_events(
                session,
                job_id=job_id,
            )
            if event.event_type == "decision.lifecycle"
        ]
    assert [payload["status"] for payload in decision_events] == [
        "pending",
        "cancelled",
    ]
    terminal = decision_events[-1]
    assert terminal["checkpointId"] == checkpoint_id
    assert terminal["resolutionSource"] == "system"


def test_cross_instance_terminal_cannot_overtake_or_duplicate_pending(db):
    inner_pending = PipelineLiveEventRecorder(db)
    terminal_recorder = PipelineLiveEventRecorder(db)
    pending_entered = threading.Event()
    release_pending = threading.Event()

    class BlockingPendingRecorder:
        def record_decision(self, **kwargs):
            if kwargs["status"] == "pending":
                pending_entered.set()
                assert release_pending.wait(timeout=2)
            return inner_pending.record_decision(**kwargs)

    job_id, stage = _seed_running_stage(db)
    waiting_gate = DecisionGateService(
        db,
        timeout_seconds=60,
        poll_interval_seconds=0.01,
        event_recorder=BlockingPendingRecorder(),
    )
    resolving_gate = DecisionGateService(
        db,
        timeout_seconds=60,
        poll_interval_seconds=0.01,
        event_recorder=terminal_recorder,
    )

    def wait_in_second_loop():
        return asyncio.run(
            waiting_gate.await_decision(
                job_id=job_id,
                stage=stage,
                kind="insufficient_evidence",
                option_keys=("continue_with_current_evidence",),
                default_option_key="continue_with_current_evidence",
                context={"summary": "evidence is limited"},
            )
        )

    with ThreadPoolExecutor(max_workers=1) as pool:
        waiting = pool.submit(wait_in_second_loop)
        assert pending_entered.wait(timeout=2)
        with db.session() as session:
            checkpoint = PipelineLiveStore().get_active_checkpoint(
                session,
                job_id=job_id,
            )
            checkpoint_id = checkpoint.id
            version = checkpoint.version
        resolving_gate.resolve(
            job_id=job_id,
            checkpoint_id=checkpoint_id,
            option_key="continue_with_current_evidence",
            version=version,
        )
        release_pending.set()
        resolution = waiting.result(timeout=2)

    assert resolution.source == "human"
    with db.session() as session:
        statuses = [
            event.payload_json["status"]
            for event in PipelineLiveStore().list_events(
                session,
                job_id=job_id,
            )
            if event.event_type == "decision.lifecycle"
        ]
    assert statuses == ["pending", "resolved"]


def test_checkpoint_registry_is_fixed_and_defaults_are_registered():
    assert set(CHECKPOINT_DEFINITIONS) >= {
        "insufficient_evidence",
        "qualification_review",
        "outreach_confirmation",
        "strategy_review",
        "retryable_failure",
        "account_blocked",
    }
    for kind, definition in CHECKPOINT_DEFINITIONS.items():
        assert definition.kind == kind
        assert definition.default_option_key in definition.option_keys
        with pytest.raises(TypeError):
            CHECKPOINT_DEFINITIONS[kind] = definition


@pytest.mark.asyncio
async def test_registered_option_subset_uses_subset_default(db):
    job_id, stage = _seed_running_stage(db)
    fake = FakeClock()
    gate = DecisionGateService(
        db,
        timeout_seconds=0.01,
        poll_interval_seconds=0.01,
        clock=fake.now,
        sleeper=fake.sleep,
    )

    resolution = await gate.await_decision(
        job_id=job_id,
        stage=stage,
        kind="insufficient_evidence",
        option_keys=("skip_remaining_pipeline", "cancel_job"),
        default_option_key="skip_remaining_pipeline",
        context={"summary": "Only executable actions are exposed"},
    )

    assert resolution.option_key == "skip_remaining_pipeline"
    assert resolution.source == "timeout"
    with db.session() as session:
        checkpoint = session.get(PipelineDecisionCheckpoint, resolution.checkpoint_id)
        assert checkpoint.option_keys_json == [
            "skip_remaining_pipeline",
            "cancel_job",
        ]
        assert checkpoint.default_option_key == "skip_remaining_pipeline"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("option_keys", "default_option_key"),
    [
        (("invented_action",), "invented_action"),
        (("cancel_job",), "continue_with_current_evidence"),
        ((), "continue_with_current_evidence"),
    ],
)
async def test_option_subset_rejects_unknown_empty_or_external_default(
    db, option_keys, default_option_key
):
    job_id, stage = _seed_running_stage(db)
    gate = DecisionGateService(db)

    with pytest.raises(DecisionGateValidationError):
        await gate.await_decision(
            job_id=job_id,
            stage=stage,
            kind="insufficient_evidence",
            option_keys=option_keys,
            default_option_key=default_option_key,
            context={"summary": "invalid subset"},
        )

    with db.session() as session:
        job = PipelineJobStore().get_job(session, job_id)
        assert job is not None
        assert job.status == JOB_STATUS_RUNNING
        assert job.stages[0].status == STAGE_STATUS_RUNNING
        assert PipelineLiveStore().get_active_checkpoint(
            session, job_id=job_id
        ) is None


@pytest.mark.asyncio
async def test_manual_review_session_never_times_out_after_sentinel(db):
    job_id, stage = _seed_running_stage(db)
    fake = FakeClock()
    crossed_sentinel = asyncio.Event()
    release_sleep = asyncio.Event()

    async def cross_all_deadlines(_delay: float) -> None:
        fake.current = datetime.max
        crossed_sentinel.set()
        await release_sleep.wait()

    gate = DecisionGateService(
        db,
        poll_interval_seconds=0.01,
        clock=fake.now,
        sleeper=cross_all_deadlines,
    )
    waiting = asyncio.create_task(
        gate.await_manual_review(
            job_id=job_id,
            stage=stage,
            context={"summary": "Explicit reviewer session"},
        )
    )
    checkpoint_id, version = await _wait_for_checkpoint(db, job_id)
    await asyncio.wait_for(crossed_sentinel.wait(), timeout=1)
    await asyncio.sleep(0)

    assert waiting.done() is False
    manual = gate.resolve(
        job_id=job_id,
        checkpoint_id=checkpoint_id,
        option_key="review_complete",
        version=version,
        operator="reviewer@example.test",
    )
    awaited = await asyncio.wait_for(waiting, timeout=1)

    assert awaited == manual
    assert manual.kind == "manual_review_session"
    assert manual.source == "human"
    assert manual.deadline_at is None
    with db.session() as session:
        checkpoint = session.get(PipelineDecisionCheckpoint, checkpoint_id)
        assert checkpoint is not None
        assert checkpoint.deadline_at == datetime.max
        assert checkpoint.context_json["manualSession"] is True


@pytest.mark.asyncio
async def test_human_resolution_resumes_job_and_stage(db):
    job_id, stage = _seed_running_stage(db)
    gate = DecisionGateService(db, timeout_seconds=10, poll_interval_seconds=0.01)
    waiting = asyncio.create_task(
        gate.await_decision(
            job_id=job_id,
            stage=stage,
            kind="qualification_review",
            context={
                "title": "Qualification review",
                "question": "Continue with qualified only?",
                "candidateCounts": {"qualified": 2, "manualReview": 4},
            },
        )
    )
    checkpoint_id, version = await _wait_for_checkpoint(db, job_id)

    manual = gate.resolve(
        job_id=job_id,
        checkpoint_id=checkpoint_id,
        option_key="continue_with_qualified_only",
        version=version,
        operator="reviewer@example.test",
    )
    awaited = await asyncio.wait_for(waiting, timeout=1)

    assert awaited == manual
    assert manual.option_key == "continue_with_qualified_only"
    assert manual.source == "human"
    with db.session() as session:
        job = PipelineJobStore().get_job(session, job_id)
        assert job is not None
        assert job.status == JOB_STATUS_RUNNING
        assert job.stages[0].status == STAGE_STATUS_RUNNING


@pytest.mark.asyncio
async def test_timeout_uses_registered_default_without_real_wait(db):
    job_id, stage = _seed_running_stage(db)
    fake = FakeClock()
    gate = DecisionGateService(
        db,
        timeout_seconds=10,
        poll_interval_seconds=2,
        clock=fake.now,
        sleeper=fake.sleep,
    )

    resolution = await gate.await_decision(
        job_id=job_id,
        stage=stage,
        kind="insufficient_evidence",
        context={"summary": "No candidate evidence"},
    )

    assert resolution.option_key == "continue_with_current_evidence"
    assert resolution.source == "timeout"
    assert sum(fake.delays) == pytest.approx(10)
    with db.session() as session:
        job = PipelineJobStore().get_job(session, job_id)
        assert job is not None
        assert job.status == JOB_STATUS_RUNNING
        assert job.stages[0].status == STAGE_STATUS_RUNNING


@pytest.mark.asyncio
async def test_default_timeout_is_ten_seconds_when_not_overridden(db):
    job_id, stage = _seed_running_stage(db)
    fake = FakeClock()
    gate = DecisionGateService(
        db,
        poll_interval_seconds=2,
        clock=fake.now,
        sleeper=fake.sleep,
    )

    resolution = await gate.await_decision(
        job_id=job_id,
        stage=stage,
        kind="insufficient_evidence",
        context={"summary": "default timeout"},
    )

    assert resolution.source == "timeout"
    assert resolution.option_key == "continue_with_current_evidence"
    assert sum(fake.delays) == pytest.approx(10)


@pytest.mark.asyncio
async def test_timeout_winner_is_returned_to_late_human_resolver(db):
    job_id, stage = _seed_running_stage(db)
    fake = FakeClock()
    gate = DecisionGateService(
        db,
        timeout_seconds=0.01,
        poll_interval_seconds=0.01,
        clock=fake.now,
        sleeper=fake.sleep,
    )

    timeout_resolution = await gate.await_decision(
        job_id=job_id,
        stage=stage,
        kind="qualification_review",
        context={"summary": "timeout wins"},
    )

    with pytest.raises(DecisionGateConflictError) as late_human:
        gate.resolve(
            job_id=job_id,
            checkpoint_id=timeout_resolution.checkpoint_id,
            option_key="open_review_workbench",
            version=1,
            operator="late-reviewer@example.test",
        )
    assert late_human.value.authoritative == timeout_resolution
    with db.session() as session:
        job = PipelineJobStore().get_job(session, job_id)
        assert job is not None
        assert job.status == JOB_STATUS_RUNNING
        assert job.stages[0].status == STAGE_STATUS_RUNNING


@pytest.mark.asyncio
async def test_human_timeout_race_has_one_authoritative_resolution(db):
    job_id, stage = _seed_running_stage(db)
    fake = FakeClock()
    reached_deadline = asyncio.Event()
    release_timeout = asyncio.Event()

    async def racing_sleep(delay: float) -> None:
        fake.current += timedelta(seconds=delay)
        reached_deadline.set()
        await release_timeout.wait()

    gate = DecisionGateService(
        db,
        timeout_seconds=0.01,
        poll_interval_seconds=0.01,
        clock=fake.now,
        sleeper=racing_sleep,
    )
    waiting = asyncio.create_task(
        gate.await_decision(
            job_id=job_id,
            stage=stage,
            kind="insufficient_evidence",
            context={"summary": "race"},
        )
    )
    checkpoint_id, version = await _wait_for_checkpoint(db, job_id)
    await asyncio.wait_for(reached_deadline.wait(), timeout=1)

    human_gate = DecisionGateService(
        db,
        timeout_seconds=0.01,
        poll_interval_seconds=0.01,
        clock=lambda: fake.current - timedelta(microseconds=1),
    )
    manual = human_gate.resolve(
        job_id=job_id,
        checkpoint_id=checkpoint_id,
        option_key="cancel_job",
        version=version,
        operator="reviewer@example.test",
    )
    release_timeout.set()
    awaited = await asyncio.wait_for(waiting, timeout=1)

    assert awaited == manual
    assert awaited.source == "human"
    with pytest.raises(DecisionGateConflictError) as duplicate:
        gate.resolve(
            job_id=job_id,
            checkpoint_id=checkpoint_id,
            option_key="continue_with_current_evidence",
            version=version,
        )
    assert duplicate.value.authoritative == manual


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        ({"option_key": "invented_action"}, "option"),
        ({"version": 2}, "conflict"),
        ({"job_id": "other-job"}, "conflict"),
    ],
)
@pytest.mark.asyncio
async def test_resolve_rejects_illegal_option_version_and_job(
    db, mutation, match
):
    job_id, stage = _seed_running_stage(db)
    gate = DecisionGateService(db, timeout_seconds=10, poll_interval_seconds=0.01)
    waiting = asyncio.create_task(
        gate.await_decision(
            job_id=job_id,
            stage=stage,
            kind="qualification_review",
            context={"summary": "strict resolve"},
        )
    )
    checkpoint_id, version = await _wait_for_checkpoint(db, job_id)
    arguments = {
        "job_id": job_id,
        "checkpoint_id": checkpoint_id,
        "option_key": "continue_with_qualified_only",
        "version": version,
    }
    arguments.update(mutation)

    error_type = (
        DecisionGateValidationError
        if "option_key" in mutation
        else DecisionGateConflictError
    )
    with pytest.raises(error_type, match=match):
        gate.resolve(**arguments)

    gate.cancel_job(job_id)
    with pytest.raises(DecisionGateCancelledError):
        await asyncio.wait_for(waiting, timeout=1)


@pytest.mark.asyncio
async def test_waiting_job_cancel_wakes_waiter_and_leaks_no_tasks(db):
    job_id, stage = _seed_running_stage(db)
    gate = DecisionGateService(db, timeout_seconds=10, poll_interval_seconds=5)
    before = set(asyncio.all_tasks())
    waiting = asyncio.create_task(
        gate.await_decision(
            job_id=job_id,
            stage=stage,
            kind="qualification_review",
            context={"summary": "cancel me"},
        )
    )
    await _wait_for_checkpoint(db, job_id)

    cancelled = gate.cancel_job(job_id)
    with pytest.raises(DecisionGateCancelledError):
        await asyncio.wait_for(waiting, timeout=1)
    await asyncio.sleep(0)

    assert cancelled is not None
    assert cancelled.status == JOB_STATUS_CANCELLED
    assert set(asyncio.all_tasks()) == before
    assert gate.waiter_count == 0
    with db.session() as session:
        job = PipelineJobStore().get_job(session, job_id)
        assert job is not None
        assert job.status == JOB_STATUS_CANCELLED
        assert job.stages[0].status == STAGE_STATUS_CANCELLED
        assert PipelineLiveStore().get_active_checkpoint(
            session,
            job_id=job_id,
        ) is None


@pytest.mark.asyncio
async def test_cancelled_await_cleans_checkpoint_waiter_and_restores_running(db):
    job_id, stage = _seed_running_stage(db)
    recorder = CapturingDecisionRecorder()
    gate = DecisionGateService(
        db,
        timeout_seconds=10,
        poll_interval_seconds=5,
        event_recorder=recorder,
    )
    waiting = asyncio.create_task(
        gate.await_decision(
            job_id=job_id,
            stage=stage,
            kind="qualification_review",
            context={"summary": "caller cancelled"},
        )
    )
    await _wait_for_checkpoint(db, job_id)

    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    await asyncio.sleep(0)

    assert gate.waiter_count == 0
    with db.session() as session:
        job = PipelineJobStore().get_job(session, job_id)
        assert job is not None
        assert job.status == JOB_STATUS_RUNNING
        assert job.stages[0].status == STAGE_STATUS_RUNNING
        assert PipelineLiveStore().get_active_checkpoint(
            session,
            job_id=job_id,
        ) is None
    terminal = [call for call in recorder.calls if call["status"] != "pending"]
    assert len(terminal) == 1
    assert terminal[0]["status"] == "cancelled"
    assert terminal[0]["resolution_source"] == "system"


@pytest.mark.asyncio
async def test_timeout_never_changes_candidate_qualification(db):
    job_id, stage = _seed_running_stage(db)
    with db.session() as session:
        user = User(
            platform="douyin",
            tiktok_id="decision-candidate",
            username="decision-candidate",
        )
        session.add(user)
        session.flush()
        link = PipelineJobUser(
            job_id=job_id,
            user_id=user.id,
            source_stage="collect",
            status="pending",
            qualification_status="manual_review",
        )
        session.add(link)
        user_id = user.id
    fake = FakeClock()
    gate = DecisionGateService(
        db,
        timeout_seconds=0.01,
        poll_interval_seconds=0.01,
        clock=fake.now,
        sleeper=fake.sleep,
    )

    result = await gate.await_decision(
        job_id=job_id,
        stage=stage,
        kind="qualification_review",
        context={"candidateCounts": {"manualReview": 1}},
    )

    assert result.option_key == "continue_with_qualified_only"
    assert result.source == "timeout"
    with db.session() as session:
        link = session.get(PipelineJobUser, (job_id, user_id))
        assert link is not None
        assert link.qualification_status == "manual_review"


@pytest.mark.asyncio
async def test_sleeper_error_cleans_waiting_checkpoint_and_restores_state(db):
    job_id, stage = _seed_running_stage(db)

    async def broken_sleep(_delay: float) -> None:
        raise RuntimeError("injected sleeper failure")

    gate = DecisionGateService(
        db,
        timeout_seconds=0.05,
        poll_interval_seconds=0.01,
        sleeper=broken_sleep,
    )

    with pytest.raises(RuntimeError, match="injected sleeper failure"):
        await gate.await_decision(
            job_id=job_id,
            stage=stage,
            kind="qualification_review",
            context={"summary": "sleeper failure"},
        )

    assert gate.waiter_count == 0
    _assert_cleaned_running_decision(db, job_id)


@pytest.mark.asyncio
async def test_clock_error_after_waiting_cleans_checkpoint_and_restores_state(db):
    job_id, stage = _seed_running_stage(db)
    calls = 0

    def broken_clock() -> datetime:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise RuntimeError("injected clock failure")
        return datetime(2026, 8, 9, 12, 0, 0)

    gate = DecisionGateService(db, clock=broken_clock)

    with pytest.raises(RuntimeError, match="injected clock failure"):
        await gate.await_decision(
            job_id=job_id,
            stage=stage,
            kind="qualification_review",
            context={"summary": "clock failure"},
        )

    assert gate.waiter_count == 0
    _assert_cleaned_running_decision(db, job_id)


@pytest.mark.asyncio
async def test_transient_checkpoint_read_error_cleans_waiting_state(db, monkeypatch):
    job_id, stage = _seed_running_stage(db)
    live_store = PipelineLiveStore()
    original_get = live_store.get_checkpoint
    failures_remaining = 1

    def fail_once(*args, **kwargs):
        nonlocal failures_remaining
        if failures_remaining:
            failures_remaining -= 1
            raise RuntimeError("injected checkpoint read failure")
        return original_get(*args, **kwargs)

    monkeypatch.setattr(live_store, "get_checkpoint", fail_once)
    gate = DecisionGateService(db, live_store=live_store)

    with pytest.raises(RuntimeError, match="injected checkpoint read failure"):
        await gate.await_decision(
            job_id=job_id,
            stage=stage,
            kind="qualification_review",
            context={"summary": "read failure"},
        )

    assert gate.waiter_count == 0
    _assert_cleaned_running_decision(db, job_id)


@pytest.mark.asyncio
async def test_resolution_error_and_resume_failure_fall_back_to_interrupted(db, monkeypatch):
    class ResumeFailingStore(PipelineJobStore):
        def resume_from_decision(self, session, job_id, stage):
            return False

    job_id, stage = _seed_running_stage(db)
    fake = FakeClock()
    gate = DecisionGateService(
        db,
        job_store=ResumeFailingStore(),
        timeout_seconds=0.01,
        poll_interval_seconds=0.01,
        clock=fake.now,
        sleeper=fake.sleep,
    )

    def fail_resolution(**_kwargs):
        raise RuntimeError("injected resolution failure")

    monkeypatch.setattr(gate, "_resolve_once", fail_resolution)
    with pytest.raises(RuntimeError, match="injected resolution failure"):
        await gate.await_decision(
            job_id=job_id,
            stage=stage,
            kind="qualification_review",
            context={"summary": "resolution failure"},
        )

    assert gate.waiter_count == 0
    with db.session() as session:
        job = PipelineJobStore().get_job(session, job_id)
        assert job is not None
        assert job.status == JOB_STATUS_INTERRUPTED
        assert job.stages[0].status == STAGE_STATUS_FAILED
        assert PipelineLiveStore().get_active_checkpoint(
            session,
            job_id=job_id,
        ) is None


@pytest.mark.parametrize("operation", ["resolve", "cancel"])
def test_closed_loop_waiter_does_not_break_committed_operation(db, operation):
    job_id, stage = _seed_running_stage(db)
    gate = DecisionGateService(db)
    live_store = PipelineLiveStore()
    checkpoint_id = ""
    version = 1
    with db.session() as session:
        checkpoint = live_store.create_checkpoint(
            session,
            job_id=job_id,
            stage=stage,
            kind="qualification_review",
            option_keys=CHECKPOINT_DEFINITIONS[
                "qualification_review"
            ].option_keys,
            default_option_key="continue_with_qualified_only",
            context={"schemaVersion": 1, "summary": "stale waiter"},
            deadline_at=datetime.utcnow() + timedelta(seconds=10),
        )
        assert PipelineJobStore().pause_for_decision(
            session,
            job_id,
            stage,
        )
        checkpoint_id = checkpoint.id
        version = checkpoint.version
    closed_loop = asyncio.new_event_loop()
    closed_loop.close()
    stale = _Waiter(loop=closed_loop, event=asyncio.Event())
    gate._waiters[job_id] = {stale}

    if operation == "resolve":
        result = gate.resolve(
            job_id=job_id,
            checkpoint_id=checkpoint_id,
            option_key="continue_with_qualified_only",
            version=version,
        )
        assert result.source == "human"
        expected_status = JOB_STATUS_RUNNING
    else:
        result = gate.cancel_job(job_id)
        assert result is not None
        expected_status = JOB_STATUS_CANCELLED

    assert gate.waiter_count == 0
    with db.session() as session:
        job = PipelineJobStore().get_job(session, job_id)
        assert job is not None
        assert job.status == expected_status


def _assert_cleaned_running_decision(database: Database, job_id: str) -> None:
    with database.session() as session:
        job = PipelineJobStore().get_job(session, job_id)
        assert job is not None
        assert job.status == JOB_STATUS_RUNNING
        assert job.stages[0].status == STAGE_STATUS_RUNNING
        checkpoint = session.scalar(
            select(PipelineDecisionCheckpoint).where(
                PipelineDecisionCheckpoint.job_id == job_id
            )
        )
        assert checkpoint is not None
        assert checkpoint.status == "cancelled"
        assert checkpoint.resolution_source == "system"
