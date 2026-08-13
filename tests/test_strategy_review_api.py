"""Focused HTTP contracts for the Stage 03 strategy review workbench."""

import httpx
import pytest

from tiktok_bot_api.auth import create_token
from tiktok_bot_api.main import app
from tiktok_bot_core.models.entities import PipelineJob, PipelineJobUser, Strategy, User
from tiktok_bot_core.storage.database import Database


@pytest.fixture
def strategy_api(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'strategy-api.db'}")
    database.init()
    previous = app.state.pipeline_database
    app.state.pipeline_database = database
    try:
        yield database
    finally:
        app.state.pipeline_database = previous
        database.engine.dispose()


def _client(authenticated=True):
    headers = {}
    if authenticated:
        headers["Authorization"] = f"Bearer {create_token('stage03-reviewer')}"
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        headers=headers,
    )


def _seed(database, *, job_id="job-03", suffix="one", status="qualified"):
    with database.session() as session:
        if session.get(PipelineJob, job_id) is None:
            session.add(PipelineJob(id=job_id, platform="douyin", stages_json=[]))
        user = User(
            platform="douyin", tiktok_id=f"stage03:{job_id}:{suffix}",
            username=f"buyer-{suffix}", nickname="Public buyer", bio="Grid projects",
        )
        session.add(user)
        session.flush()
        session.add(PipelineJobUser(
            job_id=job_id, user_id=user.id, source_stage="filter",
            qualification_status=status, status=status, match_score=81, confidence_score=74,
        ))
        strategy = Strategy(
            job_id=job_id, user_id=user.id, persona="buyer", strategy_type="soft_sell",
            comment_template="A safe public business discussion.", dm_template="",
            action_plan="Confirm current requirements.", priority=3,
        )
        session.add(strategy)
        session.flush()
        return strategy.id


@pytest.mark.asyncio
async def test_stage03_reads_require_auth_and_return_scoped_safe_dtos(strategy_api):
    strategy_id = _seed(strategy_api)
    _seed(strategy_api, suffix="missing", status="qualified")
    with strategy_api.session() as session:
        missing = session.query(Strategy).order_by(Strategy.id.desc()).first()
        session.delete(missing)
    async with _client(False) as anonymous:
        assert (await anonymous.get("/api/acquisition/jobs/job-03/stage-03")).status_code == 401
    async with _client() as client:
        summary = await client.get("/api/acquisition/jobs/job-03/stage-03")
        listed = await client.get("/api/acquisition/jobs/job-03/strategies")
        detail = await client.get(f"/api/acquisition/jobs/job-03/strategies/{strategy_id}")
    assert summary.json()["summary"] == {
        "qualified": 2, "drafts": 1, "approved": 0, "rejected": 0,
        "missingStrategies": 1,
    }
    assert listed.json()["total"] == 1
    item = detail.json()["strategy"]
    assert item["candidate"]["username"] == "buyer-one"
    assert item["candidate"]["matchScore"] == 81
    assert "tiktokId" not in str(item)


@pytest.mark.asyncio
async def test_stage03_mutations_are_strict_audited_and_conflicts_are_authoritative(strategy_api):
    approved_id = _seed(strategy_api, suffix="approve")
    rejected_id = _seed(strategy_api, suffix="reject")
    edit = {
        "reviewVersion": 0, "persona": "contractor", "strategyType": "partnership",
        "commentTemplate": "A neutral project discussion.", "dmTemplate": "",
        "actionPlan": "Ask about public project requirements.", "priority": 2,
    }
    async with _client() as client:
        edited = await client.patch(f"/api/acquisition/jobs/job-03/strategies/{approved_id}", json=edit)
        approved = await client.post(
            f"/api/acquisition/jobs/job-03/strategies/{approved_id}/approve",
            json={"reviewVersion": 1},
        )
        conflict = await client.post(
            f"/api/acquisition/jobs/job-03/strategies/{approved_id}/approve",
            json={"reviewVersion": 1},
        )
        rejected = await client.post(
            f"/api/acquisition/jobs/job-03/strategies/{rejected_id}/reject",
            json={"reviewVersion": 0, "reason": "Not aligned with this campaign."},
        )
    assert edited.json()["strategy"]["reviewVersion"] == 1
    assert approved.json()["strategy"]["reviewedBy"] == "stage03-reviewer"
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["current"]["reviewStatus"] == "approved"
    assert rejected.json()["strategy"]["reviewStatus"] == "rejected"


@pytest.mark.asyncio
async def test_stage03_batch_pagination_and_job_isolation(strategy_api):
    ids = [_seed(strategy_api, suffix=f"batch-{index}") for index in range(3)]
    foreign_id = _seed(strategy_api, job_id="job-other", suffix="foreign")
    async with _client() as client:
        page = await client.get("/api/acquisition/jobs/job-03/strategies?limit=1&offset=1&reviewStatus=draft")
        batch = await client.post(
            "/api/acquisition/jobs/job-03/strategies/approve-batch",
            json={"items": [{"strategyId": item, "reviewVersion": 0} for item in ids]},
        )
        foreign = await client.get(f"/api/acquisition/jobs/job-03/strategies/{foreign_id}")
    assert (page.json()["total"], len(page.json()["items"])) == (3, 1)
    assert batch.json() == {"total": 3, "approved": 3, "skipped": 0, "conflicted": 0}
    assert foreign.status_code == 404
