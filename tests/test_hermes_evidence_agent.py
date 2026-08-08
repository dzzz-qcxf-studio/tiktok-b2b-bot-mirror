"""HermesEvidenceAgent delegates bounded collection to the real BrowseAgent."""

from __future__ import annotations

from pathlib import Path
from tempfile import NamedTemporaryFile
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import event

from tiktok_bot_core.events.bus import EventBus
from tiktok_bot_core.llm.router import LLMRouteError
from tiktok_bot_core.services.acquisition_agents import (
    CandidateObservation,
    EvidenceObservation,
    ExplorationBudget,
    ExplorationBudgetTracker,
    HermesEvidenceAgent,
    Stage01CandidateAgent,
)
from tiktok_bot_core.storage.acquisition_store import AcquisitionStore
from tiktok_bot_core.storage.database import Database
from tiktok_bot_core.storage.pipeline_job_store import PipelineJobStore
from tiktok_bot_core.storage.sqlite_store import SqliteStore


class _Body:
    async def inner_text(self):
        return "public search results"


class _LeasedBrowser:
    def __init__(self):
        self.current_url = "https://www.douyin.com/search/test"
        self.init = AsyncMock()
        self.close = AsyncMock()

    async def screenshot(self, full_page=False):
        return b"jpg"

    async def navigate(self, url):
        self.current_url = url

    async def click(self, selector):
        return None

    async def scroll_down(self, px=500):
        return None

    async def wait(self, ms):
        return None

    async def query(self, selector):
        return _Body() if selector == "body" else None

    async def query_all(self, selector):
        return []


def _evidence(
    user_id: str, comment_id: str, username: str, *, video_id: str = ""
) -> dict:
    return EvidenceObservation(
        platform="douyin",
        platform_user_id=user_id,
        username=username,
        source_type="comment_author",
        video_id=video_id,
        comment_id=comment_id,
        comment_url=f"https://www.douyin.com/video/1?comment={comment_id}",
        author_url=f"https://www.douyin.com/user/{user_id}",
        source_path=["keyword", "video", "comment", "author"],
    ).model_dump(mode="json")


@pytest.mark.asyncio
async def test_collect_keyword_uses_browse_agent_groups_users_and_reuses_browser():
    router = MagicMock()
    router.json_completion = AsyncMock(side_effect=[
        {"action": "extract", "payload": {"observation": _evidence("u1", "c1", "buyer")}},
        {"action": "extract", "payload": {"observation": _evidence("u1", "c2", "buyer")}},
        {"action": "extract", "payload": {"observation": _evidence("u2", "c3", "other")}},
        {"action": "done", "payload": {"summary": "done"}},
    ])
    browser = _LeasedBrowser()
    agent = HermesEvidenceAgent(router=router, bus=EventBus(), max_steps=10)

    candidates = await agent.collect_keyword(
        browser=browser,
        keyword="transformer buyer",
        keyword_id=7,
        platform="douyin",
        account_id=3,
        budget=ExplorationBudget(max_total_observations=10),
    )

    assert [item.platform_user_id for item in candidates] == ["u1", "u2"]
    assert [len(item.evidence) for item in candidates] == [2, 1]
    assert all(obs.keyword_id == 7 for item in candidates for obs in item.evidence)
    assert all(obs.keyword_text == "transformer buyer" for item in candidates for obs in item.evidence)
    assert router.json_completion.await_count == 4
    browser.init.assert_not_awaited()
    browser.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_author_video_truncation_only_downgrades_affected_candidate():
    router = MagicMock()
    router.json_completion = AsyncMock(side_effect=[
        {"action": "extract", "payload": {"observation": {
            **_evidence("u1", "c1", "buyer", video_id="v1"),
            "source_type": "profile", "comment_id": "",
        }}},
        {"action": "extract", "payload": {"observation": {
            **_evidence("u1", "c2", "buyer", video_id="v2"),
            "source_type": "profile", "comment_id": "",
        }}},
        {"action": "extract", "payload": {"observation": _evidence("u2", "c3", "other", video_id="v3")}},
        {"action": "done", "payload": {"summary": "done"}},
    ])
    agent = HermesEvidenceAgent(router=router, bus=EventBus(), max_steps=10)

    candidates = await agent.collect_keyword(
        browser=_LeasedBrowser(),
        keyword="buyer",
        keyword_id=7,
        platform="douyin",
        account_id=3,
        budget=ExplorationBudget(max_author_videos=1),
    )

    by_user = {item.platform_user_id: item for item in candidates}
    assert by_user["u1"].discovery_state == "needs_more_evidence"
    assert by_user["u1"].truncation_reasons == ("max_author_videos",)
    assert by_user["u2"].discovery_state == "candidate"
    assert by_user["u2"].truncation_reasons == ()


@pytest.mark.asyncio
async def test_collect_keyword_exposes_exact_last_browse_budget_usage():
    router = MagicMock()
    router.json_completion = AsyncMock(side_effect=[
        {"action": "extract", "payload": {"observation": _evidence("u1", "c1", "buyer")}},
        {"action": "done", "payload": {"summary": "done"}},
    ])
    agent = HermesEvidenceAgent(router=router, bus=EventBus(), max_steps=10)

    await agent.collect_keyword(
        browser=_LeasedBrowser(),
        keyword="buyer",
        keyword_id=7,
        platform="douyin",
        account_id=3,
        budget=ExplorationBudget(),
    )

    assert agent.last_budget_usage["steps"] == 2
    assert agent.last_budget_usage["pages"] == 1
    assert agent.last_budget_usage["llm_calls"] == 2
    assert agent.last_budget_usage["duration_seconds"] >= 0
    assert agent.last_exhaustion_reason == ""


@pytest.mark.asyncio
async def test_collect_keyword_resets_last_budget_metrics_before_exception():
    router = MagicMock()
    router.json_completion = AsyncMock(
        return_value={"action": "done", "payload": {"summary": "done"}}
    )
    agent = HermesEvidenceAgent(router=router, bus=EventBus(), max_steps=10)
    call = {
        "browser": _LeasedBrowser(),
        "keyword": "buyer",
        "keyword_id": 7,
        "platform": "douyin",
        "account_id": 3,
        "budget": ExplorationBudget(),
    }
    await agent.collect_keyword(**call)
    assert agent.last_budget_usage["llm_calls"] == 1

    router.json_completion = AsyncMock(side_effect=RuntimeError("broken"))
    with pytest.raises(LLMRouteError):
        await agent.collect_keyword(**call)

    assert agent.last_budget_usage == {}
    assert agent.last_exhaustion_reason == ""


def test_tracker_atomically_rejects_observation_with_exact_reason():
    tracker = ExplorationBudgetTracker(
        ExplorationBudget(
            max_videos_per_keyword=1,
            max_comments_per_video=5,
            max_profiles=5,
            max_author_videos=5,
            max_total_observations=5,
        )
    )
    first = EvidenceObservation.model_validate(
        _evidence("u1", "c1", "one", video_id="v1")
    )
    rejected = EvidenceObservation.model_validate(
        _evidence("u2", "c2", "two", video_id="v2")
    )

    assert tracker.consume_observation(first, current_keyword="kw").accepted
    decision = tracker.consume_observation(rejected, current_keyword="kw")

    assert decision.accepted is False
    assert decision.reason == "max_videos_per_keyword"
    assert decision.stop_after is True
    assert tracker.total_observations == 1
    # Reusing the rejected item under a different keyword proves none of its
    # video/comment/profile/author counters were partially mutated.
    assert tracker.consume_observation(
        rejected, current_keyword="other"
    ).accepted is True
    assert tracker.total_observations == 2


def test_tracker_reports_per_author_limit_without_stopping_other_authors():
    tracker = ExplorationBudgetTracker(
        ExplorationBudget(max_author_videos=1, max_total_observations=10)
    )
    first = EvidenceObservation.model_validate(
        {
            **_evidence("u1", "c1", "one", video_id="v1"),
            "source_type": "profile",
            "comment_id": "",
        }
    )
    second = EvidenceObservation.model_validate(
        {
            **_evidence("u1", "c2", "one", video_id="v2"),
            "source_type": "profile",
            "comment_id": "",
        }
    )

    assert tracker.consume_observation(first, current_keyword="kw").accepted
    decision = tracker.consume_observation(second, current_keyword="kw")

    assert decision.accepted is False
    assert decision.reason == "max_author_videos"
    assert decision.stop_after is False
    assert tracker.total_observations == 1


def test_comment_videos_do_not_consume_author_profile_video_budget():
    tracker = ExplorationBudgetTracker(
        ExplorationBudget(max_author_videos=1, max_total_observations=20)
    )
    first = EvidenceObservation.model_validate(
        _evidence("buyer", "c1", "buyer", video_id="video-1")
    )
    second = EvidenceObservation.model_validate(
        _evidence("buyer", "c2", "buyer", video_id="video-2")
    )

    assert tracker.consume_observation(
        first, current_keyword="采购"
    ).accepted is True
    assert tracker.consume_observation(
        second, current_keyword="采购"
    ).accepted is True


@pytest.mark.asyncio
async def test_collect_keyword_consumes_shared_tracker_live_and_stops_at_global_limit():
    router = MagicMock()
    router.json_completion = AsyncMock(side_effect=[
        {"action": "extract", "payload": {"observation": _evidence("u1", "c1", "one")}},
        {"action": "done", "payload": {"summary": "done"}},
        {"action": "extract", "payload": {"observation": _evidence("u2", "c2", "two")}},
        {"action": "done", "payload": {"summary": "must not run"}},
    ])
    budget = ExplorationBudget(max_profiles=1, max_total_observations=10)
    tracker = ExplorationBudgetTracker(budget)
    agent = HermesEvidenceAgent(router=router, bus=EventBus(), max_steps=10)

    first = await agent.collect_keyword(
        browser=_LeasedBrowser(), keyword="first", keyword_id=1,
        platform="douyin", account_id=3, budget=budget, tracker=tracker,
    )
    second = await agent.collect_keyword(
        browser=_LeasedBrowser(), keyword="second", keyword_id=2,
        platform="douyin", account_id=3, budget=budget, tracker=tracker,
    )

    assert [candidate.platform_user_id for candidate in first] == ["u1"]
    assert first[0].discovery_state == "needs_more_evidence"
    assert first[0].truncation_reasons == ("max_profiles",)
    assert second == []
    assert agent.last_exhaustion_reason == "max_profiles"
    assert router.json_completion.await_count == 3


@pytest.mark.asyncio
async def test_collect_keyword_passes_exact_seconds_deadline():
    async def hang(*_args, **_kwargs):
        import asyncio
        await asyncio.Event().wait()

    router = MagicMock()
    router.json_completion = AsyncMock(side_effect=hang)
    agent = HermesEvidenceAgent(router=router, bus=EventBus(), max_steps=10)

    candidates = await agent.collect_keyword(
        browser=_LeasedBrowser(), keyword="buyer", keyword_id=7,
        platform="douyin", account_id=3, budget=ExplorationBudget(),
        max_duration_seconds=0.02,
    )

    assert candidates == []
    assert agent.last_exhaustion_reason == "max_duration"


def test_candidate_persist_query_count_is_constant_from_one_to_fifty():
    def persist_and_count(candidate_count: int) -> int:
        with NamedTemporaryFile(suffix=".db", delete=False) as file:
            path = Path(file.name)
        database = Database(f"sqlite:///{path}")
        database.init()
        try:
            with database.session() as session:
                job = PipelineJobStore().create_job(
                    session,
                    platform="douyin",
                    account_mode="specified",
                    account_id=None,
                    stages=["collect"],
                )
                AcquisitionStore().create_campaign(
                    session, job_id=job.id, platform="douyin"
                )
                job_id = job.id
            candidates = [
                CandidateObservation(
                    platform="douyin",
                    platform_user_id=f"scale-user-{index}",
                    username=f"scale-user-{index}",
                    evidence=[
                        EvidenceObservation.model_validate(
                            _evidence(
                                f"scale-user-{index}",
                                f"scale-comment-{index}",
                                f"scale-user-{index}",
                            )
                        )
                    ],
                )
                for index in range(candidate_count)
            ]
            statements: list[str] = []

            def record(_conn, _cursor, statement, _params, _ctx, _many):
                statements.append(statement)

            event.listen(database.engine, "before_cursor_execute", record)
            try:
                with database.session() as session:
                    Stage01CandidateAgent().persist(
                        session,
                        job_id=job_id,
                        candidates=candidates,
                        acquisition_store=AcquisitionStore(),
                        pipeline_store=PipelineJobStore(),
                        user_store=SqliteStore(),
                    )
            finally:
                event.remove(database.engine, "before_cursor_execute", record)
            return len(statements)
        finally:
            database.engine.dispose()
            path.unlink(missing_ok=True)

    one = persist_and_count(1)
    fifty = persist_and_count(50)

    assert fifty <= one + 3, {"one": one, "fifty": fifty}
