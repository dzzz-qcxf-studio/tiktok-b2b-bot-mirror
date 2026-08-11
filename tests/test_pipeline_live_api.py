"""Authenticated HTTP contracts for durable Pipeline live monitoring."""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timedelta

import httpx
import pytest
from starlette.requests import Request

import tiktok_bot_api.main as api_main
from tiktok_bot_api.auth import create_token
from tiktok_bot_api.main import app
from tiktok_bot_api.pipeline_live import PipelineLiveReadService
from tiktok_bot_core.models.entities import PipelineJob, PipelineJobStage
from tiktok_bot_core.services.pipeline_decisions import DecisionGateService
from tiktok_bot_core.storage.database import Database
from tiktok_bot_core.storage.pipeline_job_store import PipelineJobStore
from tiktok_bot_core.storage.pipeline_live_store import PipelineLiveStore


def _client(*, authenticated: bool = True):
    headers = {}
    if authenticated:
        headers["Authorization"] = f"Bearer {create_token('live-reviewer')}"
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        headers=headers,
    )


def _seed_job(
    database: Database,
    job_id: str,
    *,
    status: str = "running",
    stage_status: str = "running",
) -> None:
    now = datetime(2026, 8, 11, 9, 0, 0)
    with database.session() as session:
        job = PipelineJob(
            id=job_id,
            trigger_type="manual",
            platform="douyin",
            account_mode="auto",
            stages_json=["collect", "filter"],
            config_snapshot_json={"credential": "must-not-leak"},
            status=status,
            current_stage="collect",
            queued_at=now,
            started_at=now,
            created_at=now,
            updated_at=now,
        )
        job.stages = [
            PipelineJobStage(
                stage="collect",
                stage_order=0,
                status=stage_status,
                attempt=1,
                result_json={"private": "must-not-leak"},
                error_message="must-not-leak",
                started_at=now,
            ),
            PipelineJobStage(
                stage="filter",
                stage_order=1,
                status="pending",
                attempt=0,
            ),
        ]
        session.add(job)


def _append_events(database: Database, job_id: str) -> list[int]:
    store = PipelineLiveStore()
    with database.session() as session:
        first = store.append_event(
            session,
            job_id=job_id,
            stage="collect",
            event_type="browse.extract",
            level="info",
            payload={
                "schemaVersion": 1,
                "action": "extract",
                "step": 1,
                "evidenceCount": 3,
                "mergedCount": 1,
            },
        )
        second = store.append_event(
            session,
            job_id=job_id,
            stage="collect",
            event_type="browse.done",
            level="info",
            payload={
                "schemaVersion": 1,
                "action": "done",
                "step": 2,
                "mergedCount": 1,
                "budget": {
                    "videosUsed": 4,
                    "videosLimit": 10,
                    "commentsUsed": 8,
                    "commentsLimit": 20,
                    "candidatesUsed": 2,
                    "candidatesLimit": 10,
                    "evidenceUsed": 3,
                    "evidenceLimit": 10,
                    "llmCallsUsed": 2,
                    "llmCallsLimit": 5,
                },
            },
        )
        return [first.sequence, second.sequence]


def _seed_waiting_checkpoint(
    database: Database,
    job_id: str,
    *,
    deadline_at: datetime | None = None,
    kind: str = "qualification_review",
    options: tuple[str, ...] = (
        "continue_with_qualified_only",
        "open_review_workbench",
        "cancel_job",
    ),
    default_option: str = "continue_with_qualified_only",
) -> str:
    store = PipelineLiveStore()
    job_store = PipelineJobStore()
    with database.session() as session:
        assert job_store.pause_for_decision(session, job_id, "collect")
        checkpoint = store.create_checkpoint(
            session,
            job_id=job_id,
            stage="collect",
            kind=kind,
            option_keys=options,
            default_option_key=default_option,
            context={
                "schemaVersion": 1,
                "title": "人工复核",
                "question": "是否继续？",
                "remainingBudget": {"pages": 2, "llmCalls": 3},
            },
            deadline_at=deadline_at or datetime.utcnow() + timedelta(seconds=10),
        )
        return checkpoint.id


@pytest.fixture
def live_api(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'pipeline-live-api.db'}")
    database.init()
    original_database = app.state.pipeline_database
    original_gate = getattr(app.state, "pipeline_decision_gate", None)
    app.state.pipeline_database = database
    gate = DecisionGateService(database, poll_interval_seconds=0.01)
    app.state.pipeline_decision_gate = gate
    try:
        yield database, gate
    finally:
        app.state.pipeline_database = original_database
        if original_gate is None:
            delattr(app.state, "pipeline_decision_gate")
        else:
            app.state.pipeline_decision_gate = original_gate
        database.engine.dispose()


@pytest.mark.asyncio
async def test_all_pipeline_live_endpoints_require_authentication(live_api):
    requests = [
        ("GET", "/api/pipeline/jobs/job-a/live", None),
        ("GET", "/api/pipeline/jobs/job-a/events", None),
        ("GET", "/api/pipeline/jobs/job-a/events/stream", None),
        ("GET", "/api/pipeline/jobs/job-a/checkpoints/active", None),
        (
            "POST",
            "/api/pipeline/jobs/job-a/checkpoints/checkpoint-a/resolve",
            {"optionKey": "cancel_job", "version": 1},
        ),
        (
            "POST",
            "/api/pipeline/jobs/job-a/checkpoints/checkpoint-a/review-complete",
            {"version": 1},
        ),
    ]
    async with _client(authenticated=False) as client:
        responses = [
            await client.request(method, path, json=payload)
            for method, path, payload in requests
        ]
    assert [response.status_code for response in responses] == [401] * 6


@pytest.mark.asyncio
async def test_live_and_deprecated_endpoints_reject_query_token_auth(live_api):
    token = create_token("query-token-user")
    requests = [
        ("GET", "/api/pipeline/jobs/missing/live", None),
        ("GET", "/api/pipeline/jobs/missing/events", None),
        ("GET", "/api/pipeline/jobs/missing/events/stream", None),
        ("GET", "/api/pipeline/jobs/missing/checkpoints/active", None),
        (
            "POST",
            "/api/pipeline/jobs/missing/checkpoints/missing/resolve",
            {"optionKey": "cancel_job", "version": 1},
        ),
        (
            "POST",
            "/api/pipeline/jobs/missing/checkpoints/missing/review-complete",
            {"version": 1},
        ),
        ("GET", "/api/pipeline/events", None),
        ("GET", "/api/pipeline/events/stream", None),
    ]
    async with _client(authenticated=False) as client:
        responses = [
            await client.request(
                method,
                f"{path}?token={token}",
                json=payload,
            )
            for method, path, payload in requests
        ]
    assert [response.status_code for response in responses] == [401] * 8
    assert all(token not in response.text for response in responses)


def test_legacy_global_event_routes_are_both_auth_protected():
    protected_paths = {
        "/api/pipeline/events",
        "/api/pipeline/events/stream",
    }
    routes = {
        route.path: route
        for route in app.routes
        if getattr(route, "path", None) in protected_paths
    }
    assert set(routes) == protected_paths
    for route in routes.values():
        dependency_calls = {
            dependency.call for dependency in route.dependant.dependencies
        }
        assert api_main.require_pipeline_live_user in dependency_calls
        assert api_main.require_user not in dependency_calls


@pytest.mark.asyncio
async def test_legacy_global_event_endpoints_are_gone_without_raw_payload(
    live_api,
):
    marker = "raw-global-payload-must-not-return"
    async with _client(authenticated=False) as client:
        anonymous = await client.get("/api/pipeline/events")
    assert anonymous.status_code == 401

    async with _client() as client:
        history = await client.get("/api/pipeline/events")
        stream = await client.get("/api/pipeline/events/stream")
    for response in (history, stream):
        assert response.status_code == 410
        assert marker not in response.text
        assert response.json() == {
            "detail": {
                "code": "pipeline_global_events_deprecated",
                "message": "全局 Pipeline 事件接口已停用，请使用 Job 实时接口",
            }
        }


@pytest.mark.asyncio
async def test_live_header_only_auth_accepts_x_api_key(
    live_api,
    monkeypatch,
):
    database, _gate = live_api
    _seed_job(database, "job-a")
    supplied_key = "header-only-test-credential"
    seen = []

    def authenticate_header_key(value):
        seen.append(value)
        return "api-key-user" if value == supplied_key else None

    monkeypatch.setattr(api_main, "authenticate_apikey", authenticate_header_key)
    async with _client(authenticated=False) as client:
        response = await client.get(
            "/api/pipeline/jobs/job-a/live",
            headers={"X-API-Key": supplied_key},
        )
    assert response.status_code == 200
    assert seen == [supplied_key]
    assert supplied_key not in response.text


@pytest.mark.asyncio
async def test_live_snapshot_is_strict_camel_case_safe_and_uses_one_session(
    live_api,
    monkeypatch,
):
    database, _gate = live_api
    _seed_job(database, "job-a")
    sequences = _append_events(database, "job-a")
    checkpoint_id = _seed_waiting_checkpoint(database, "job-a")

    original_session = database.session
    opened = 0

    @contextmanager
    def counted_session():
        nonlocal opened
        opened += 1
        with original_session() as session:
            yield session

    monkeypatch.setattr(database, "session", counted_session)
    async with _client() as client:
        response = await client.get("/api/pipeline/jobs/job-a/live")

    assert response.status_code == 200
    assert opened == 1
    body = response.json()
    assert set(body) == {
        "job",
        "stage",
        "metrics",
        "recentEvents",
        "activeCheckpoint",
        "lastSequence",
    }
    assert set(body["job"]) == {
        "id",
        "platform",
        "status",
        "currentStage",
        "requestedStages",
        "startedAt",
        "finishedAt",
        "updatedAt",
    }
    assert set(body["stage"]) == {
        "stage",
        "order",
        "status",
        "attempt",
        "startedAt",
        "finishedAt",
    }
    assert body["lastSequence"] == sequences[-1]
    assert body["activeCheckpoint"]["id"] == checkpoint_id
    assert body["metrics"] == {
        "totalEvents": 2,
        "browserActions": 2,
        "videos": 4,
        "comments": 8,
        "candidates": 2,
        "evidence": 3,
        "llmCalls": 2,
        "remainingBudget": {"pages": 2, "llmCalls": 3},
    }
    serialized = json.dumps(body, ensure_ascii=False).lower()
    for prohibited in (
        "configsnapshot",
        "result_json",
        "errormessage",
        "credential",
        "must-not-leak",
        "operator",
        "reason",
    ):
        assert prohibited not in serialized


@pytest.mark.asyncio
async def test_history_is_incremental_bounded_and_job_isolated(live_api):
    database, _gate = live_api
    _seed_job(database, "job-a")
    _seed_job(database, "job-b", status="succeeded", stage_status="succeeded")
    a_sequences = _append_events(database, "job-a")
    _append_events(database, "job-b")

    async with _client() as client:
        response = await client.get(
            f"/api/pipeline/jobs/job-a/events?afterSequence={a_sequences[0]}&limit=1"
        )
    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "sequence": a_sequences[1],
                "jobId": "job-a",
                "stage": "collect",
                "eventType": "browse.done",
                "level": "info",
                "payload": {
                    "schemaVersion": 1,
                    "action": "done",
                    "step": 2,
                    "mergedCount": 1,
                    "budget": {
                        "videosUsed": 4,
                        "videosLimit": 10,
                        "commentsUsed": 8,
                        "commentsLimit": 20,
                        "candidatesUsed": 2,
                        "candidatesLimit": 10,
                        "evidenceUsed": 3,
                        "evidenceLimit": 10,
                        "llmCallsUsed": 2,
                        "llmCallsLimit": 5,
                    },
                },
                "createdAt": response.json()["items"][0]["createdAt"],
            }
        ],
        "lastSequence": a_sequences[1],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/api/pipeline/jobs/job-a/events?afterSequence=-1",
        "/api/pipeline/jobs/job-a/events?limit=0",
        "/api/pipeline/jobs/job-a/events?limit=501",
        "/api/pipeline/jobs/job-a/events/stream?afterSequence=-1",
    ],
)
async def test_history_and_stream_reject_invalid_boundaries(live_api, path):
    database, _gate = live_api
    _seed_job(database, "job-a")
    async with _client() as client:
        response = await client.get(path)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "request_validation_error"


@pytest.mark.asyncio
async def test_unknown_job_is_404_for_all_read_endpoints(live_api):
    async with _client() as client:
        responses = [
            await client.get("/api/pipeline/jobs/missing/live"),
            await client.get("/api/pipeline/jobs/missing/events"),
            await client.get("/api/pipeline/jobs/missing/events/stream"),
            await client.get(
                "/api/pipeline/jobs/missing/checkpoints/active"
            ),
        ]
    assert [response.status_code for response in responses] == [404] * 4
    assert all(
        response.json()["detail"]["code"] == "job_not_found"
        for response in responses
    )


@pytest.mark.asyncio
async def test_unknown_job_is_distinct_from_checkpoint_job_mismatch(live_api):
    async with _client() as client:
        response = await client.post(
            "/api/pipeline/jobs/missing/checkpoints/missing/resolve",
            json={"optionKey": "cancel_job", "version": 1},
        )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "job_not_found"


@pytest.mark.asyncio
async def test_active_checkpoint_is_safe_and_manual_deadline_is_null(live_api):
    database, _gate = live_api
    _seed_job(database, "job-a")
    checkpoint_id = _seed_waiting_checkpoint(
        database,
        "job-a",
        deadline_at=datetime.max,
        kind="manual_review_session",
        options=("review_complete",),
        default_option="review_complete",
    )
    async with _client() as client:
        response = await client.get(
            "/api/pipeline/jobs/job-a/checkpoints/active"
        )
    assert response.status_code == 200
    checkpoint = response.json()["checkpoint"]
    assert checkpoint["id"] == checkpoint_id
    assert checkpoint["deadlineAt"] is None
    assert "operator" not in checkpoint
    assert "reason" not in checkpoint


@pytest.mark.asyncio
async def test_resolve_uses_shared_gate_and_returns_human_resolution(
    live_api,
    monkeypatch,
):
    database, gate = live_api
    _seed_job(database, "job-a")
    checkpoint_id = _seed_waiting_checkpoint(database, "job-a")
    calls = []
    original_resolve = gate.resolve

    def tracked_resolve(**kwargs):
        calls.append(kwargs)
        return original_resolve(**kwargs)

    monkeypatch.setattr(gate, "resolve", tracked_resolve)
    async with _client() as client:
        response = await client.post(
            f"/api/pipeline/jobs/job-a/checkpoints/{checkpoint_id}/resolve",
            json={
                "optionKey": "continue_with_qualified_only",
                "version": 1,
                "reason": "operator confirmed",
            },
        )
    assert response.status_code == 200
    assert calls[0]["operator"] == "live-reviewer"
    resolution = response.json()["resolution"]
    assert {
        key: value
        for key, value in resolution.items()
        if key not in {"resolvedAt", "deadlineAt"}
    } == {
        "checkpointId": checkpoint_id,
        "jobId": "job-a",
        "stage": "collect",
        "kind": "qualification_review",
        "optionKey": "continue_with_qualified_only",
        "source": "human",
        "status": "resolved",
    }
    assert resolution["resolvedAt"] is not None
    assert resolution["deadlineAt"] is not None


@pytest.mark.asyncio
async def test_checkpoint_job_mismatch_is_404_and_invalid_option_is_422(live_api):
    database, _gate = live_api
    _seed_job(database, "job-a")
    _seed_job(database, "job-b")
    foreign_checkpoint = _seed_waiting_checkpoint(database, "job-b")
    local_checkpoint = _seed_waiting_checkpoint(database, "job-a")
    async with _client() as client:
        mismatch = await client.post(
            f"/api/pipeline/jobs/job-a/checkpoints/{foreign_checkpoint}/resolve",
            json={"optionKey": "cancel_job", "version": 1},
        )
        invalid = await client.post(
            f"/api/pipeline/jobs/job-a/checkpoints/{local_checkpoint}/resolve",
            json={"optionKey": "invented_action", "version": 1},
        )
    assert mismatch.status_code == 404
    assert mismatch.json()["detail"]["code"] == "checkpoint_not_found"
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "invalid_checkpoint_option"


@pytest.mark.asyncio
async def test_timeout_human_race_returns_authoritative_resolution(live_api):
    database, _gate = live_api
    _seed_job(database, "job-a")
    checkpoint_id = _seed_waiting_checkpoint(
        database,
        "job-a",
        deadline_at=datetime.utcnow() - timedelta(milliseconds=1),
    )
    async with _client() as client:
        response = await client.post(
            f"/api/pipeline/jobs/job-a/checkpoints/{checkpoint_id}/resolve",
            json={"optionKey": "cancel_job", "version": 1},
        )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "checkpoint_conflict"
    resolution = response.json()["detail"]["resolution"]
    assert resolution["checkpointId"] == checkpoint_id
    assert resolution["optionKey"] == "continue_with_qualified_only"
    assert resolution["source"] == "timeout"
    assert resolution["status"] == "expired"


@pytest.mark.asyncio
async def test_stale_version_returns_409_without_fabricating_resolution(live_api):
    database, _gate = live_api
    _seed_job(database, "job-a")
    checkpoint_id = _seed_waiting_checkpoint(database, "job-a")
    async with _client() as client:
        response = await client.post(
            f"/api/pipeline/jobs/job-a/checkpoints/{checkpoint_id}/resolve",
            json={"optionKey": "cancel_job", "version": 2},
        )
    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "checkpoint_conflict",
        "message": "关卡状态已变化，请使用当前权威结果",
        "resolution": None,
    }


@pytest.mark.asyncio
async def test_resolve_error_never_echoes_reason_or_sensitive_input(live_api):
    database, _gate = live_api
    _seed_job(database, "job-a")
    checkpoint_id = _seed_waiting_checkpoint(database, "job-a")
    sensitive = "Bearer credential-value-123456"
    async with _client() as client:
        response = await client.post(
            f"/api/pipeline/jobs/job-a/checkpoints/{checkpoint_id}/resolve",
            json={
                "optionKey": "cancel_job",
                "version": 1,
                "reason": sensitive,
            },
        )
    assert response.status_code == 422
    assert sensitive not in response.text
    assert response.json()["detail"] == {
        "code": "invalid_checkpoint_option",
        "message": "关卡选项、版本或说明无效",
    }


@pytest.mark.asyncio
async def test_review_complete_uses_same_gate_with_fixed_option(live_api):
    database, gate = live_api
    _seed_job(database, "job-a")
    checkpoint_id = _seed_waiting_checkpoint(
        database,
        "job-a",
        deadline_at=datetime.max,
        kind="manual_review_session",
        options=("review_complete",),
        default_option="review_complete",
    )
    async with _client() as client:
        response = await client.post(
            f"/api/pipeline/jobs/job-a/checkpoints/{checkpoint_id}/review-complete",
            json={"version": 1},
        )
    assert response.status_code == 200
    assert response.json()["resolution"]["optionKey"] == "review_complete"
    assert gate.get_resolution(
        job_id="job-a", checkpoint_id=checkpoint_id
    ).source == "human"


@pytest.mark.asyncio
async def test_sse_uses_json_frames_resume_cursor_and_job_isolation(live_api):
    database, _gate = live_api
    _seed_job(database, "job-a", status="succeeded", stage_status="succeeded")
    _seed_job(database, "job-b", status="succeeded", stage_status="succeeded")
    a_sequences = _append_events(database, "job-a")
    _append_events(database, "job-b")
    async with _client() as client:
        response = await client.get(
            "/api/pipeline/jobs/job-a/events/stream?afterSequence=0",
            headers={
                "Authorization": f"Bearer {create_token('live-reviewer')}",
                "Last-Event-ID": str(a_sequences[0]),
            },
        )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    frames = [frame for frame in response.text.split("\n\n") if frame]
    assert len(frames) == 1
    lines = dict(line.split(": ", 1) for line in frames[0].splitlines())
    assert lines["id"] == str(a_sequences[1])
    assert lines["event"] == "pipeline.event"
    payload = json.loads(lines["data"])
    assert payload["jobId"] == "job-a"
    assert payload["sequence"] == a_sequences[1]
    assert "job-b" not in response.text


@pytest.mark.asyncio
async def test_sse_rejects_invalid_last_event_id_without_echoing_it(live_api):
    database, _gate = live_api
    _seed_job(database, "job-a")
    invalid_cursor = "Bearer should-never-return-123456"
    async with _client() as client:
        response = await client.get(
            "/api/pipeline/jobs/job-a/events/stream",
            headers={
                "Authorization": f"Bearer {create_token('live-reviewer')}",
                "Last-Event-ID": invalid_cursor,
            },
        )
    assert response.status_code == 422
    assert invalid_cursor not in response.text
    assert response.json()["detail"] == {
        "code": "request_validation_error",
        "message": "Last-Event-ID 无效",
    }


@pytest.mark.asyncio
async def test_sse_disconnect_closes_generator_without_open_db_session(
    live_api,
    monkeypatch,
):
    database, _gate = live_api
    _seed_job(database, "job-a")
    original_session = database.session
    active_sessions = 0

    @contextmanager
    def tracked_session():
        nonlocal active_sessions
        active_sessions += 1
        try:
            with original_session() as session:
                yield session
        finally:
            active_sessions -= 1

    monkeypatch.setattr(database, "session", tracked_session)
    disconnected = False

    async def receive():
        nonlocal disconnected
        if not disconnected:
            disconnected = True
            return {"type": "http.disconnect"}
        return {"type": "http.disconnect"}

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/pipeline/jobs/job-a/events/stream",
            "headers": [],
            "query_string": b"",
        },
        receive=receive,
    )
    response = await api_main.stream_pipeline_job_events(
        request=request,
        job_id="job-a",
        after_sequence=0,
        last_event_id=None,
        live=PipelineLiveReadService(database),
        _current_user="live-reviewer",
    )
    chunks = [chunk async for chunk in response.body_iterator]
    assert chunks == []
    assert active_sessions == 0
