"""Bounded stage-01 acquisition agents and evidence ingestion."""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import NamedTemporaryFile
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError
from sqlalchemy import event

from tiktok_bot_core.browser.providers import BrowserSession
from tiktok_bot_core.events.bus import EventBus
from tiktok_bot_core.models.entities import DiscoveryEvidence, PipelineJobUser
from tiktok_bot_core.models.pipeline_states import (
    DISCOVERY_STATUS_CANDIDATE,
    DISCOVERY_STATUS_NEEDS_MORE_EVIDENCE,
    QUALIFICATION_STATUS_MANUAL_REVIEW,
)
from tiktok_bot_core.services.acquisition_agents import (
    CandidateObservation,
    DiscoveryPlan,
    DiscoveryPlannerAgent,
    EvidenceObservation,
    ExplorationBudget,
    ExplorationBudgetTracker,
    Stage01CandidateAgent,
)
from tiktok_bot_core.platforms import PlatformType, get_platform
from tiktok_bot_core.services.browse_agent import BrowseAgent
from tiktok_bot_core.plugins.collectors.keyword_collector import KeywordCollector
from tiktok_bot_core.storage.acquisition_store import AcquisitionStore
from tiktok_bot_core.storage.database import Database
from tiktok_bot_core.storage.pipeline_job_store import PipelineJobStore
from tiktok_bot_core.storage.sqlite_store import SqliteStore


def _keyword(keyword_id: int, text: str, status: str, score: int = 0) -> dict:
    return {
        "id": keyword_id,
        "text": text,
        "status": status,
        "qualified_count": score,
        "candidate_count": score,
        "relevant_video_count": score,
    }


def _evidence(**overrides) -> EvidenceObservation:
    values = {
        "platform": "douyin",
        "platform_user_id": "sec-user-1",
        "username": "buyer-one",
        "source_type": "comment_author",
        "keyword_text": "变压器采购",
        "video_id": "video-1",
        "video_url": "https://www.douyin.com/video/video-1",
        "comment_id": "comment-1",
        "comment_url": "https://www.douyin.com/video/video-1?comment=comment-1",
        "author_url": "https://www.douyin.com/user/sec-user-1",
        "raw_text": "请问越南有供货吗",
        "source_path": ["keyword", "video", "comment", "author"],
        "relevance_score": 0.8,
        "completeness_score": 0.7,
    }
    values.update(overrides)
    return EvidenceObservation(**values)


def test_contracts_are_versioned_strict_and_cannot_write_qualification():
    plan = DiscoveryPlan(
        platform="douyin",
        keywords=[],
        budget=ExplorationBudget(),
    )
    assert plan.schema_version == "1.0"
    assert plan.search_modes == ("video_comments", "direct_users")

    with pytest.raises(ValidationError):
        EvidenceObservation(**{
            **_evidence().model_dump(),
            "cookie": "must-not-enter-agent-output",
        })
    with pytest.raises(ValidationError):
        CandidateObservation(
            platform="douyin",
            platform_user_id="sec-user-1",
            username="buyer-one",
            evidence=[_evidence()],
            qualification_state="qualified",
        )


def test_planner_uses_deterministic_70_30_mix_and_video_first():
    planner = DiscoveryPlannerAgent()
    plan = planner.plan(
        platform="douyin",
        keywords=[
            _keyword(1, "effective-low", "effective", 1),
            _keyword(2, "effective-high", "effective", 9),
            _keyword(3, "new-b", "new"),
            _keyword(4, "new-a", "new"),
            _keyword(5, "effective-mid", "effective", 5),
        ],
        budget=ExplorationBudget(max_keywords=10),
        total_keywords=4,
    )

    assert [item.text for item in plan.keywords] == [
        "effective-high",
        "effective-mid",
        "effective-low",
        "new-b",
    ]
    assert [item.pool for item in plan.keywords] == [
        "effective",
        "effective",
        "effective",
        "new",
    ]
    assert plan.search_modes == ("video_comments", "direct_users")


def test_exploration_budget_has_bounded_browser_limits():
    budget = ExplorationBudget()
    assert budget.max_author_videos == 5
    assert budget.max_pages == 10
    assert budget.max_duration_minutes == 60
    assert budget.max_llm_calls == 100

    for values in (
        {"max_author_videos": 21},
        {"max_pages": 101},
        {"max_duration_minutes": 1441},
        {"max_llm_calls": 1001},
    ):
        with pytest.raises(ValidationError):
            ExplorationBudget(**values)


def test_planner_excludes_non_runnable_keyword_statuses():
    plan = DiscoveryPlannerAgent().plan(
        platform="douyin",
        keywords=[
            _keyword(1, "effective", "effective", 5),
            _keyword(2, "new", "new"),
            _keyword(3, "disabled", "disabled", 999),
            _keyword(4, "low", "low_yield", 999),
            _keyword(5, "cooling", "cooling", 999),
            _keyword(6, "testing", "testing", 999),
        ],
        budget=ExplorationBudget(max_keywords=10),
    )

    assert [keyword.text for keyword in plan.keywords] == ["effective", "new"]


def test_budget_tracker_enforces_each_level_and_never_grows_unbounded():
    budget = ExplorationBudget(
        max_keywords=1,
        max_videos_per_keyword=1,
        max_comments_per_video=2,
        max_profiles=1,
        max_total_observations=4,
    )
    tracker = ExplorationBudgetTracker(budget)

    assert tracker.allow_keyword("kw") is True
    assert tracker.allow_keyword("another") is False
    assert tracker.allow_video("kw", "v1") is True
    assert tracker.allow_video("kw", "v2") is False
    assert tracker.allow_comment("v1", "c1") is True
    assert tracker.allow_comment("v1", "c2") is True
    assert tracker.allow_comment("v1", "c3") is False
    assert tracker.allow_profile("u1") is True
    assert tracker.allow_profile("u2") is False
    assert tracker.exhausted is True
    assert tracker.total_observations == 4


class _Browser:
    def __init__(self):
        self.current_url = "https://www.douyin.com/"
        self.closed = False

    async def init(self): pass
    async def close(self): self.closed = True
    async def screenshot(self, full_page=False): return b"jpg"
    async def navigate(self, url): self.current_url = url
    async def click(self, selector): pass
    async def scroll_down(self, px=500): pass
    async def wait(self, ms): pass
    async def query(self, selector): return None
    async def query_all(self, selector): return []


@pytest.mark.asyncio
async def test_hermes_extract_accumulates_only_schema_valid_observations():
    router = MagicMock()
    valid = _evidence().model_dump(mode="json")
    router.json_completion = AsyncMock(side_effect=[
        {"action": "extract", "payload": {"observation": valid}},
        {
            "action": "extract",
            "payload": {"observation": {**valid, "api_key": "forbidden"}},
        },
        {"action": "done", "payload": {"summary": "done"}},
    ])
    agent = BrowseAgent(
        router=router,
        bus=EventBus(),
        browser_factory=lambda: _Browser(),
        max_steps=3,
    )

    result = await agent.run(
        goal="collect public evidence",
        platform="douyin",
        account_id=1,
    )
    await asyncio.sleep(0)

    assert result.status == "done"
    assert result.observations == [EvidenceObservation.model_validate(valid)]
    assert result.steps_detail[1].action is None


@pytest.fixture
def acquisition_db():
    with NamedTemporaryFile(suffix=".db", delete=False) as file:
        path = Path(file.name)
    database = Database(f"sqlite:///{path}")
    database.init()
    try:
        yield database
    finally:
        database.engine.dispose()
        path.unlink(missing_ok=True)


def _create_job(
    database: Database, *, search_budget: dict | None = None
) -> str:
    with database.session() as session:
        job = PipelineJobStore().create_job(
            session,
            platform="douyin",
            account_mode="specified",
            account_id=None,
            stages=["collect"],
        )
        AcquisitionStore().create_campaign(
            session,
            job_id=job.id,
            platform="douyin",
            search_budget=search_budget,
        )
        return job.id


def test_candidate_agent_deduplicates_user_but_keeps_every_source_path(acquisition_db):
    job_id = _create_job(acquisition_db)
    candidate = CandidateObservation(
        platform="douyin",
        platform_user_id="sec-user-1",
        username="buyer-one",
        evidence=[
            _evidence(comment_id="comment-1", raw_text="first"),
            _evidence(comment_id="comment-2", raw_text="second"),
        ],
    )

    with acquisition_db.session() as session:
        summary = Stage01CandidateAgent().persist(
            session,
            job_id=job_id,
            candidates=[candidate],
            acquisition_store=AcquisitionStore(),
            pipeline_store=PipelineJobStore(),
            user_store=SqliteStore(),
        )
        links = session.query(PipelineJobUser).all()
        evidence = AcquisitionStore().list_evidence(
            session, job_id, links[0].user_id
        )
        qualification_status = links[0].qualification_status
        discovery_status = links[0].discovery_status

    assert summary.candidates == 1
    assert summary.evidence == 2
    assert len(links) == 1
    assert len(evidence) == 2
    assert qualification_status == QUALIFICATION_STATUS_MANUAL_REVIEW
    assert discovery_status == DISCOVERY_STATUS_CANDIDATE


def test_candidate_agent_retry_skips_identical_evidence_but_keeps_new_source(
    acquisition_db,
):
    job_id = _create_job(acquisition_db)
    original = _evidence(comment_id="comment-1", raw_text="same")
    candidate = CandidateObservation(
        platform="douyin",
        platform_user_id="sec-user-1",
        username="buyer-one",
        evidence=[original],
    )
    different_source = CandidateObservation(
        platform="douyin",
        platform_user_id="sec-user-1",
        username="buyer-one",
        evidence=[
            _evidence(comment_id="comment-1", raw_text="updated rendering"),
            _evidence(comment_id="comment-2", raw_text="same"),
        ],
    )

    with acquisition_db.session() as session:
        first = Stage01CandidateAgent().persist(
            session,
            job_id=job_id,
            candidates=[candidate],
            acquisition_store=AcquisitionStore(),
            pipeline_store=PipelineJobStore(),
            user_store=SqliteStore(),
        )
    with acquisition_db.session() as session:
        retry = Stage01CandidateAgent().persist(
            session,
            job_id=job_id,
            candidates=[different_source],
            acquisition_store=AcquisitionStore(),
            pipeline_store=PipelineJobStore(),
            user_store=SqliteStore(),
        )
        link = session.query(PipelineJobUser).one()
        evidence = AcquisitionStore().list_evidence(session, job_id, link.user_id)
        comment_ids = {item.comment_id for item in evidence}

    assert first.evidence == 1
    assert retry.evidence == 1
    assert comment_ids == {"comment-1", "comment-2"}


def test_candidate_agent_evidence_batch_has_constant_query_count(acquisition_db):
    job_id = _create_job(acquisition_db)
    candidate = CandidateObservation(
        platform="douyin",
        platform_user_id="batch-user",
        username="batch-user",
        evidence=[
            _evidence(
                platform_user_id="batch-user",
                username="batch-user",
                comment_id=f"comment-{index}",
            )
            for index in range(25)
        ],
    )
    statements: list[str] = []

    def record(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(acquisition_db.engine, "before_cursor_execute", record)
    try:
        with acquisition_db.session() as session:
            summary = Stage01CandidateAgent().persist(
                session,
                job_id=job_id,
                candidates=[candidate],
                acquisition_store=AcquisitionStore(),
                pipeline_store=PipelineJobStore(),
                user_store=SqliteStore(),
            )
    finally:
        event.remove(acquisition_db.engine, "before_cursor_execute", record)

    assert summary.evidence == 25
    assert len(statements) <= 15, statements


def test_candidate_agent_concurrent_retry_inserts_one_source_once(acquisition_db):
    job_id = _create_job(acquisition_db)
    candidate = CandidateObservation(
        platform="douyin",
        platform_user_id="concurrent-user",
        username="concurrent-user",
        evidence=[
            _evidence(
                platform_user_id="concurrent-user",
                username="concurrent-user",
                comment_id="same-comment",
            )
        ],
    )
    ready = threading.Barrier(2)

    def persist_once() -> int:
        ready.wait(timeout=5)
        with acquisition_db.session() as session:
            return Stage01CandidateAgent().persist(
                session,
                job_id=job_id,
                candidates=[candidate],
                acquisition_store=AcquisitionStore(),
                pipeline_store=PipelineJobStore(),
                user_store=SqliteStore(),
            ).evidence

    with ThreadPoolExecutor(max_workers=2) as executor:
        counts = sorted(executor.map(lambda _index: persist_once(), range(2)))

    with acquisition_db.session() as session:
        evidence_count = session.query(DiscoveryEvidence).count()
    assert counts == [0, 1]
    assert evidence_count == 1


def test_budget_exhaustion_marks_candidate_as_needs_more_evidence(acquisition_db):
    job_id = _create_job(acquisition_db)
    candidate = CandidateObservation(
        platform="douyin",
        platform_user_id="sec-user-2",
        username="buyer-two",
        evidence=[_evidence(platform_user_id="sec-user-2", username="buyer-two")],
        discovery_state=DISCOVERY_STATUS_NEEDS_MORE_EVIDENCE,
    )

    with acquisition_db.session() as session:
        Stage01CandidateAgent().persist(
            session,
            job_id=job_id,
            candidates=[candidate],
            acquisition_store=AcquisitionStore(),
            pipeline_store=PipelineJobStore(),
            user_store=SqliteStore(),
        )
        link = session.query(PipelineJobUser).one()
        discovery_status = link.discovery_status
        qualification_status = link.qualification_status

    assert discovery_status == DISCOVERY_STATUS_NEEDS_MORE_EVIDENCE
    assert qualification_status == QUALIFICATION_STATUS_MANUAL_REVIEW


def test_candidate_requires_evidence_and_same_platform_identity():
    with pytest.raises(ValidationError):
        CandidateObservation(
            platform="douyin",
            platform_user_id="sec-user-1",
            username="buyer-one",
            evidence=[],
        )
    with pytest.raises(ValidationError):
        CandidateObservation(
            platform="tiktok",
            platform_user_id="sec-user-1",
            username="buyer-one",
            evidence=[_evidence()],
        )


@pytest.mark.asyncio
async def test_keyword_collector_acquisition_mode_is_video_first_user_auxiliary():
    calls: list[tuple[str, str]] = []
    collector = KeywordCollector()
    collector._search_video_comment_candidates = AsyncMock(
        side_effect=lambda _browser, keyword, *_args, **_kwargs: (
            calls.append(("video_comments", keyword)) or []
        )
    )
    collector._search_one = AsyncMock(
        side_effect=lambda _browser, keyword, *_args, **_kwargs: (
            calls.append(("direct_users", keyword)) or []
        )
    )

    await collector.collect({
        "platform": "douyin",
        "keywords": ["变压器", "电网工程"],
        "acquisition_mode": True,
        "allow_dom_fallback": True,
        "browser_session": BrowserSession(
            platform="douyin", account_id=1, client=MagicMock()
        ),
        "budget": {
            "max_videos_per_keyword": 1,
            "max_comments_per_video": 1,
            "max_profiles": 1,
            "max_total_observations": 10,
        },
    })

    assert calls == [
        ("video_comments", "变压器"),
        ("direct_users", "变压器"),
        ("video_comments", "电网工程"),
        ("direct_users", "电网工程"),
    ]


@pytest.mark.asyncio
async def test_keyword_collector_uses_injected_hermes_as_primary_evidence_agent():
    evidence_agent = MagicMock()
    evidence_agent.collect_keyword = AsyncMock(return_value=[])
    collector = KeywordCollector()
    collector._search_video_comment_candidates = AsyncMock()
    collector._search_one = AsyncMock(return_value=[])
    session = BrowserSession(
        platform="douyin", account_id=9, client=MagicMock()
    )

    await collector.collect({
        "platform": "douyin",
        "keywords": ["变压器"],
        "keyword_ids": {"变压器": 7},
        "acquisition_mode": True,
        "browser_session": session,
        "evidence_agent": evidence_agent,
        "account_id": 9,
    })

    evidence_agent.collect_keyword.assert_awaited_once()
    passed = evidence_agent.collect_keyword.await_args.kwargs
    assert passed["browser"] is session.client
    assert passed["keyword"] == "变压器"
    assert passed["keyword_id"] == 7
    assert passed["platform"] == "douyin"
    assert passed["account_id"] == 9
    assert isinstance(passed["budget"], ExplorationBudget)
    assert isinstance(passed["tracker"], ExplorationBudgetTracker)
    assert passed["max_duration_seconds"] == pytest.approx(3600.0)
    collector._search_video_comment_candidates.assert_not_awaited()


@pytest.mark.asyncio
async def test_keyword_collector_enforces_campaign_budget_across_keywords():
    candidate = CandidateObservation(
        platform="douyin",
        platform_user_id="shared-budget-user",
        username="shared-budget-user",
        evidence=[_evidence(
            platform_user_id="shared-budget-user",
            username="shared-budget-user",
            keyword_id=1,
            keyword_text="first",
        )],
    )
    evidence_agent = MagicMock()

    async def collect_keyword(**kwargs):
        evidence_agent.last_budget_usage = {
            "steps": 1,
            "pages": 1,
            "llm_calls": 1,
            "duration_seconds": 1.0,
        }
        evidence_agent.last_exhaustion_reason = "max_pages"
        evidence_agent.last_visited_urls = []
        return [candidate]

    evidence_agent.collect_keyword = AsyncMock(side_effect=collect_keyword)
    evidence_agent.last_budget_usage = {}
    evidence_agent.last_exhaustion_reason = ""
    evidence_agent.last_visited_urls = []
    metrics = {"keywords": {}}
    collector = KeywordCollector(monotonic=lambda: 0.0)
    collector._search_one = AsyncMock(return_value=[])

    result = await collector.collect({
        "platform": "douyin",
        "keywords": ["first", "second"],
        "keyword_ids": {"first": 1, "second": 2},
        "acquisition_mode": True,
        "browser_session": BrowserSession(
            platform="douyin", account_id=9, client=MagicMock()
        ),
        "evidence_agent": evidence_agent,
        "account_id": 9,
        "collection_metrics": metrics,
        "budget": {
            "max_keywords": 2,
            "max_videos_per_keyword": 2,
            "max_comments_per_video": 2,
            "max_profiles": 10,
            "max_total_observations": 10,
            "max_author_videos": 2,
            "max_pages": 1,
            "max_duration_minutes": 5,
            "max_llm_calls": 2,
        },
    })

    evidence_agent.collect_keyword.assert_awaited_once()
    passed_budget = evidence_agent.collect_keyword.await_args.kwargs["budget"]
    assert passed_budget.max_pages == 1
    assert passed_budget.max_llm_calls == 2
    collector._search_one.assert_not_awaited()
    assert metrics["totals"]["pages"] == 1
    assert metrics["totals"]["llm_calls"] == 1
    assert metrics["keywords"]["1"]["truncation_reasons"] == ["max_pages"]
    assert result[0]["discovery_state"] == "needs_more_evidence"
    assert result[0]["truncation_reasons"] == ["max_pages"]


@pytest.mark.asyncio
async def test_direct_user_navigation_consumes_shared_page_budget():
    evidence_agent = MagicMock()

    async def collect_keyword(**_kwargs):
        evidence_agent.last_budget_usage = {
            "steps": 1,
            "pages": 0,
            "llm_calls": 1,
            "duration_seconds": 0.1,
        }
        evidence_agent.last_exhaustion_reason = ""
        evidence_agent.last_visited_urls = []
        return []

    evidence_agent.collect_keyword = AsyncMock(side_effect=collect_keyword)
    evidence_agent.last_budget_usage = {}
    evidence_agent.last_exhaustion_reason = ""
    evidence_agent.last_visited_urls = []
    collector = KeywordCollector(monotonic=lambda: 0.0)
    collector._search_one = AsyncMock(return_value=[])
    metrics = {"keywords": {}}

    await collector.collect({
        "platform": "douyin",
        "keywords": ["first", "second"],
        "acquisition_mode": True,
        "browser_session": BrowserSession(
            platform="douyin", account_id=9, client=MagicMock()
        ),
        "evidence_agent": evidence_agent,
        "account_id": 9,
        "collection_metrics": metrics,
        "budget": {
            "max_keywords": 2,
            "max_pages": 1,
            "max_llm_calls": 2,
            "max_duration_minutes": 5,
        },
    })

    evidence_agent.collect_keyword.assert_awaited_once()
    collector._search_one.assert_awaited_once()
    assert metrics["keywords"]["first"]["truncation_reasons"] == [
        "max_pages"
    ]


@pytest.mark.asyncio
async def test_keyword_collector_passes_one_tracker_and_exact_remaining_duration():
    evidence_agent = MagicMock()
    seen_trackers = []
    seen_seconds = []

    async def collect_keyword(**kwargs):
        seen_trackers.append(kwargs["tracker"])
        seen_seconds.append(kwargs["max_duration_seconds"])
        evidence_agent.last_budget_usage = {
            "pages": 1,
            "llm_calls": 1,
            "duration_seconds": 0.25,
        }
        evidence_agent.last_exhaustion_reason = ""
        evidence_agent.last_visited_urls = []
        return []

    evidence_agent.collect_keyword = AsyncMock(side_effect=collect_keyword)
    evidence_agent.last_budget_usage = {}
    evidence_agent.last_exhaustion_reason = ""
    evidence_agent.last_visited_urls = []
    ticks = iter([0.0, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0])
    collector = KeywordCollector(monotonic=lambda: next(ticks, 2.0))
    collector._search_one = AsyncMock(return_value=[])

    await collector.collect({
        "platform": "douyin",
        "keywords": ["first", "second"],
        "acquisition_mode": True,
        "browser_session": BrowserSession(
            platform="douyin", account_id=9, client=MagicMock()
        ),
        "evidence_agent": evidence_agent,
        "account_id": 9,
        "budget": {
            "max_keywords": 2,
            "max_pages": 4,
            "max_llm_calls": 4,
            "max_duration_minutes": 1,
        },
    })

    assert len(seen_trackers) == 2
    assert seen_trackers[0] is seen_trackers[1]
    assert seen_seconds[0] == pytest.approx(59.25)
    assert seen_seconds[1] < seen_seconds[0]


@pytest.mark.asyncio
async def test_keyword_collector_counts_authoritative_visited_video_without_candidate():
    evidence_agent = MagicMock()

    async def collect_keyword(**_kwargs):
        evidence_agent.last_budget_usage = {
            "pages": 1,
            "llm_calls": 1,
            "duration_seconds": 0.1,
        }
        evidence_agent.last_exhaustion_reason = "max_pages"
        evidence_agent.last_visited_urls = [
            "https://www.douyin.com/video/visited-only",
            "javascript:alert(1)",
        ]
        return []

    evidence_agent.collect_keyword = AsyncMock(side_effect=collect_keyword)
    metrics = {"keywords": {}}

    await KeywordCollector(monotonic=lambda: 0.0).collect({
        "platform": "douyin",
        "keywords": ["visited"],
        "keyword_ids": {"visited": 1},
        "acquisition_mode": True,
        "browser_session": BrowserSession(
            platform="douyin", account_id=9, client=MagicMock()
        ),
        "evidence_agent": evidence_agent,
        "collection_metrics": metrics,
        "budget": {"max_keywords": 1, "max_pages": 1, "max_llm_calls": 1},
    })

    assert metrics["keywords"]["1"]["videos_explored"] == 1
    assert metrics["keywords"]["1"]["relevant_videos"] == 0


def test_direct_users_mark_only_candidate_consuming_exact_last_profile_budget():
    tracker = ExplorationBudgetTracker(ExplorationBudget(
        max_profiles=2,
        max_total_observations=10,
    ))
    candidates = KeywordCollector()._direct_user_candidates(
        [
            {"tiktok_id": "first", "username": "first"},
            {"tiktok_id": "last", "username": "last"},
        ],
        keyword="profile-cap",
        keyword_id=1,
        platform=PlatformType.DOUYIN,
        pf=get_platform(PlatformType.DOUYIN),
        tracker=tracker,
    )

    assert candidates[0].get("discovery_state", "candidate") == "candidate"
    assert candidates[0].get("truncation_reasons", []) == []
    assert candidates[1]["discovery_state"] == "needs_more_evidence"
    assert candidates[1]["truncation_reasons"] == ["max_profiles"]


@pytest.mark.asyncio
async def test_hermes_per_user_exhaustion_preserves_unaffected_candidate():
    class EvidenceAgent:
        last_budget_usage = {
            "pages": 1,
            "llm_calls": 2,
            "duration_seconds": 0.01,
        }
        last_exhaustion_reason = "max_total_observations"
        last_visited_urls = ["https://www.douyin.com/video/one"]

        async def collect_keyword(self, **_kwargs):
            return [
                CandidateObservation(
                    platform="douyin",
                    platform_user_id="affected",
                    username="affected",
                    evidence=[_evidence(
                        platform_user_id="affected", username="affected"
                    )],
                    discovery_state="needs_more_evidence",
                    truncation_reasons=["max_total_observations"],
                ),
                CandidateObservation(
                    platform="douyin",
                    platform_user_id="complete",
                    username="complete",
                    evidence=[_evidence(
                        platform_user_id="complete",
                        username="complete",
                        comment_id="complete-comment",
                    )],
                ),
            ]

    collector = KeywordCollector(monotonic=lambda: 0.0)
    collector._search_one = AsyncMock(return_value=[])
    result = await collector.collect({
        "platform": "douyin",
        "keywords": ["采购"],
        "acquisition_mode": True,
        "browser_session": BrowserSession(
            platform="douyin", account_id=1, client=MagicMock()
        ),
        "evidence_agent": EvidenceAgent(),
        "account_id": 1,
    })

    by_id = {item["platform_user_id"]: item for item in result}
    assert by_id["affected"]["discovery_state"] == "needs_more_evidence"
    assert by_id["complete"]["discovery_state"] == "candidate"
    assert by_id["complete"]["truncation_reasons"] == []


@pytest.mark.asyncio
async def test_direct_user_auxiliary_runs_after_shared_llm_budget_is_exhausted():
    evidence_agent = MagicMock()

    async def collect_keyword(**_kwargs):
        evidence_agent.last_budget_usage = {
            "pages": 1,
            "llm_calls": 1,
            "duration_seconds": 0.1,
        }
        evidence_agent.last_exhaustion_reason = "max_llm_calls"
        evidence_agent.last_visited_urls = []
        return []

    evidence_agent.collect_keyword = AsyncMock(side_effect=collect_keyword)
    collector = KeywordCollector(monotonic=lambda: 0.0)
    collector._search_one = AsyncMock(return_value=[{
        "tiktok_id": "direct-after-llm",
        "username": "direct-after-llm",
    }])
    metrics = {"keywords": {}}

    candidates = await collector.collect({
        "platform": "douyin",
        "keywords": ["direct"],
        "keyword_ids": {"direct": 1},
        "acquisition_mode": True,
        "browser_session": BrowserSession(
            platform="douyin", account_id=9, client=MagicMock()
        ),
        "evidence_agent": evidence_agent,
        "collection_metrics": metrics,
        "budget": {
            "max_keywords": 1,
            "max_profiles": 2,
            "max_total_observations": 2,
            "max_pages": 2,
            "max_llm_calls": 1,
        },
    })

    collector._search_one.assert_awaited_once()
    assert [item["platform_user_id"] for item in candidates] == [
        "direct-after-llm"
    ]
    assert metrics["totals"]["llm_calls"] == 1


@pytest.mark.asyncio
async def test_exact_last_page_marks_only_candidate_from_truncated_keyword():
    evidence_agent = MagicMock()
    call_index = 0

    async def collect_keyword(**kwargs):
        nonlocal call_index
        keyword = kwargs["keyword"]
        call_index += 1
        evidence_agent.last_budget_usage = {
            "pages": 1,
            "llm_calls": 1,
            "duration_seconds": 0.1,
        }
        evidence_agent.last_exhaustion_reason = ""
        evidence_agent.last_visited_urls = [
            f"https://www.douyin.com/video/{keyword}"
        ]
        return [CandidateObservation(
            platform="douyin",
            platform_user_id=f"user-{keyword}",
            username=f"user-{keyword}",
            evidence=[_evidence(
                platform_user_id=f"user-{keyword}",
                username=f"user-{keyword}",
                keyword_id=call_index,
                keyword_text=keyword,
                video_id=keyword,
                video_url=f"https://www.douyin.com/video/{keyword}",
                comment_id=f"comment-{keyword}",
                comment_url=f"https://www.douyin.com/video/{keyword}?comment=1",
            )],
        )]

    evidence_agent.collect_keyword = AsyncMock(side_effect=collect_keyword)
    collector = KeywordCollector(monotonic=lambda: 0.0)
    collector._search_one = AsyncMock(return_value=[])

    candidates = await collector.collect({
        "platform": "douyin",
        "keywords": ["first", "second"],
        "keyword_ids": {"first": 1, "second": 2},
        "acquisition_mode": True,
        "browser_session": BrowserSession(
            platform="douyin", account_id=9, client=MagicMock()
        ),
        "evidence_agent": evidence_agent,
        "budget": {
            "max_keywords": 2,
            "max_pages": 3,
            "max_llm_calls": 2,
        },
    })

    by_id = {item["platform_user_id"]: item for item in candidates}
    assert by_id["user-first"]["discovery_state"] == "candidate"
    assert by_id["user-first"]["truncation_reasons"] == []
    assert by_id["user-second"]["discovery_state"] == "needs_more_evidence"
    assert by_id["user-second"]["truncation_reasons"] == ["max_pages"]


@pytest.mark.asyncio
async def test_keyword_collector_legacy_mode_does_not_start_video_discovery():
    collector = KeywordCollector()
    collector._search_video_comment_candidates = AsyncMock()
    collector._search_one = AsyncMock(return_value=[])

    await collector.collect({
        "platform": "douyin",
        "keywords": ["legacy"],
        "browser_session": BrowserSession(
            platform="douyin", account_id=1, client=MagicMock()
        ),
    })

    collector._search_video_comment_candidates.assert_not_awaited()
    collector._search_one.assert_awaited_once()


@pytest.mark.asyncio
async def test_keyword_collector_never_follows_malicious_dom_video_url():
    class _Card:
        async def get_attribute(self, name):
            return "https://evil.example/video/stolen" if name == "href" else None

        async def inner_text(self):
            return "malicious"

    browser = MagicMock()
    browser.navigate = AsyncMock()
    browser.wait = AsyncMock()
    browser.scroll_down = AsyncMock()
    browser.query_all_limited = AsyncMock(return_value=[_Card()])
    tracker = ExplorationBudgetTracker(
        ExplorationBudget(
            max_keywords=1,
            max_videos_per_keyword=1,
            max_comments_per_video=1,
            max_profiles=1,
            max_total_observations=10,
        )
    )
    assert tracker.allow_keyword("采购") is True

    candidates = await KeywordCollector()._search_video_comment_candidates(
        browser,
        "采购",
        1,
        get_platform(PlatformType.DOUYIN),
        PlatformType.DOUYIN,
        tracker=tracker,
    )

    assert candidates == []
    assert browser.navigate.await_count == 1
    assert browser.navigate.await_args.args[0].startswith(
        "https://www.douyin.com/search/"
    )
    browser.query_all_limited.assert_awaited()


@pytest.mark.asyncio
async def test_pipeline_campaign_collect_persists_evidence_without_outreach(
    acquisition_db, monkeypatch,
):
    from tiktok_bot_core.browser.providers import BrowserSession
    from tiktok_bot_core.extensions.registry import register as get_registry
    from tiktok_bot_core.services import pipeline as pipeline_module
    from tiktok_bot_core.services.pipeline import PipelineRunContext, PipelineService
    from tiktok_bot_core import storage

    job_id = _create_job(acquisition_db)
    with acquisition_db.session() as session:
        AcquisitionStore().create_keyword(
            session,
            job_id=job_id,
            platform="douyin",
            text="变压器采购",
            status="effective",
        )

    candidate = {
        "platform": "douyin",
        "platform_user_id": "sec-pipeline-user",
        "username": "pipeline-buyer",
        "evidence": [{
            **_evidence(
                platform_user_id="sec-pipeline-user",
                username="pipeline-buyer",
                comment_id="pipeline-comment",
                relevance_score=None,
            ).model_dump(mode="json"),
            "keyword_id": 1,
        }],
    }
    hermes = MagicMock()

    async def collect_keyword(**_kwargs):
        hermes.last_budget_usage = {
            "pages": 3,
            "llm_calls": 1,
            "duration_seconds": 0.1,
        }
        hermes.last_exhaustion_reason = ""
        hermes.last_visited_urls = [
            "https://www.douyin.com/video/video-1"
        ]
        return [candidate]

    hermes.collect_keyword = AsyncMock(side_effect=collect_keyword)
    collector = KeywordCollector(monotonic=lambda: 0.0)
    collector._search_one = AsyncMock(return_value=[])
    collector.collect = AsyncMock(wraps=collector.collect)
    monkeypatch.setattr(pipeline_module, "HermesEvidenceAgent", lambda **_kwargs: hermes)
    registry = get_registry()
    old_collectors = dict(registry.collectors)
    old_channels = dict(registry.channels)
    comment = MagicMock(execute=AsyncMock(return_value=True))
    dm = MagicMock(execute=AsyncMock(return_value=True))
    registry.collectors["keyword"] = collector
    registry.channels["comment"] = comment
    registry.channels["dm"] = dm
    old_db = storage.database._db_instance
    storage.database._db_instance = acquisition_db
    try:
        service = PipelineService()
        service.vector = MagicMock()
        context = PipelineRunContext(
            job_id=job_id,
            platform="douyin",
            account_id=1,
            account_username="account",
            browser_session=BrowserSession(
                platform="douyin", account_id=1, client=MagicMock()
            ),
        )
        results = [item async for item in service.run(
            context,
            stages=["collect"],
            collection_config={"keywords": ["ignored-legacy"]},
        )]
        [item async for item in service.run(
            context,
            stages=["collect"],
            collection_config={"keywords": ["ignored-legacy"]},
        )]
    finally:
        storage.database._db_instance = old_db
        registry.collectors.clear()
        registry.collectors.update(old_collectors)
        registry.channels.clear()
        registry.channels.update(old_channels)

    assert results[0]["status"] == "ok"
    assert results[0]["result"] == {
        "mode": "acquisition",
        "keywords_planned": 1,
        "candidates": 1,
        "evidence": 1,
        "candidate": 1,
        "needs_more_evidence": 0,
        "keyword_stats": [{
            "keyword_id": 1,
            "keyword": "变压器采购",
            "usage_count": 1,
            "video_count": 1,
            "relevant_video_count": 0,
            "candidate_count": 1,
        }],
    }
    passed_config = collector.collect.await_args.args[0]
    assert passed_config["acquisition_mode"] is True
    assert passed_config["search_modes"] == ["video_comments", "direct_users"]
    comment.execute.assert_not_awaited()
    dm.execute.assert_not_awaited()
    with acquisition_db.session() as session:
        link = session.query(PipelineJobUser).one()
        assert link.qualification_status == QUALIFICATION_STATUS_MANUAL_REVIEW
        assert len(AcquisitionStore().list_evidence(session, job_id, link.user_id)) == 1
        keyword = AcquisitionStore().list_keywords(session, job_id)[0]
        assert keyword.usage_count == 1
        assert keyword.video_count == 1
        assert keyword.relevant_video_count == 0
        assert keyword.candidate_count == 1


@pytest.mark.asyncio
async def test_pipeline_retry_updates_discovery_state_with_identical_evidence(
    acquisition_db, monkeypatch,
):
    from tiktok_bot_core import storage
    from tiktok_bot_core.browser.providers import BrowserSession
    from tiktok_bot_core.extensions.registry import register as get_registry
    from tiktok_bot_core.services import pipeline as pipeline_module
    from tiktok_bot_core.services.pipeline import PipelineRunContext, PipelineService

    job_id = _create_job(acquisition_db)
    with acquisition_db.session() as session:
        keyword = AcquisitionStore().create_keyword(
            session, job_id=job_id, platform="douyin", text="retry-state"
        )
        keyword_id = keyword.id
    evidence = _evidence(
        platform_user_id="retry-state-user",
        username="retry-state-user",
        keyword_id=keyword_id,
        keyword_text="retry-state",
    ).model_dump(mode="json")
    first = {
        "platform": "douyin",
        "platform_user_id": "retry-state-user",
        "username": "retry-state-user",
        "evidence": [evidence],
    }
    second = {
        **first,
        "discovery_state": "needs_more_evidence",
        "truncation_reasons": ["max_pages"],
    }
    hermes = MagicMock()
    candidates = iter([[first], [second]])

    async def collect_keyword(**_kwargs):
        hermes.last_budget_usage = {
            "pages": 3,
            "llm_calls": 1,
            "duration_seconds": 0.1,
        }
        hermes.last_exhaustion_reason = ""
        hermes.last_visited_urls = [evidence["video_url"]]
        return next(candidates)

    hermes.collect_keyword = AsyncMock(side_effect=collect_keyword)
    collector = KeywordCollector(monotonic=lambda: 0.0)
    collector._search_one = AsyncMock(return_value=[])
    monkeypatch.setattr(pipeline_module, "HermesEvidenceAgent", lambda **_kwargs: hermes)
    registry = get_registry()
    old_collectors = dict(registry.collectors)
    registry.collectors["keyword"] = collector
    old_db = storage.database._db_instance
    storage.database._db_instance = acquisition_db
    try:
        service = PipelineService()
        context = PipelineRunContext(
            job_id=job_id,
            platform="douyin",
            account_id=1,
            account_username="account",
            browser_session=BrowserSession(
                platform="douyin", account_id=1, client=MagicMock()
            ),
        )
        for _ in range(2):
            [item async for item in service.run(context, stages=["collect"])]
    finally:
        storage.database._db_instance = old_db
        registry.collectors.clear()
        registry.collectors.update(old_collectors)

    with acquisition_db.session() as session:
        link = session.query(PipelineJobUser).one()
        assert link.discovery_status == DISCOVERY_STATUS_NEEDS_MORE_EVIDENCE
        assert session.query(DiscoveryEvidence).count() == 1


@pytest.mark.asyncio
async def test_pipeline_counts_metrics_for_explored_video_without_candidate(
    acquisition_db, monkeypatch,
):
    from tiktok_bot_core import storage
    from tiktok_bot_core.browser.providers import BrowserSession
    from tiktok_bot_core.extensions.registry import register as get_registry
    from tiktok_bot_core.services import pipeline as pipeline_module
    from tiktok_bot_core.services.pipeline import PipelineRunContext, PipelineService

    job_id = _create_job(acquisition_db)
    with acquisition_db.session() as session:
        keyword = AcquisitionStore().create_keyword(
            session, job_id=job_id, platform="douyin", text="零候选"
        )
        keyword_id = keyword.id

    hermes = MagicMock()

    async def collect_keyword(**_kwargs):
        hermes.last_budget_usage = {
            "pages": 1,
            "llm_calls": 1,
            "duration_seconds": 0.1,
        }
        hermes.last_exhaustion_reason = "max_pages"
        hermes.last_visited_urls = [
            "https://www.douyin.com/video/visited-no-candidate"
        ]
        return []

    hermes.collect_keyword = AsyncMock(side_effect=collect_keyword)
    collector = KeywordCollector(monotonic=lambda: 0.0)
    collector._search_one = AsyncMock(return_value=[])
    monkeypatch.setattr(pipeline_module, "HermesEvidenceAgent", lambda **_kwargs: hermes)
    registry = get_registry()
    old_collectors = dict(registry.collectors)
    registry.collectors["keyword"] = collector
    old_db = storage.database._db_instance
    storage.database._db_instance = acquisition_db
    try:
        service = PipelineService()
        context = PipelineRunContext(
            job_id=job_id,
            platform="douyin",
            account_id=1,
            account_username="account",
            browser_session=BrowserSession(
                platform="douyin", account_id=1, client=MagicMock()
            ),
        )
        result = [item async for item in service.run(
            context, stages=["collect"]
        )][0]
    finally:
        storage.database._db_instance = old_db
        registry.collectors.clear()
        registry.collectors.update(old_collectors)

    assert result["status"] == "ok"
    assert result["result"]["keyword_stats"][0]["video_count"] == 1
    assert result["result"]["keyword_stats"][0]["relevant_video_count"] == 0
    with acquisition_db.session() as session:
        keyword = AcquisitionStore().list_keywords(session, job_id)[0]
        assert keyword.video_count == 1
        assert keyword.candidate_count == 0


def test_pipeline_accepts_many_direct_users_when_authoritative_llm_total_is_bounded():
    budget = ExplorationBudget(
        max_keywords=1,
        max_profiles=3,
        max_total_observations=3,
        max_llm_calls=1,
    )
    candidates = [
        CandidateObservation(
            platform="douyin",
            platform_user_id=f"direct-{index}",
            username=f"direct-{index}",
            evidence=[_evidence(
                platform_user_id=f"direct-{index}",
                username=f"direct-{index}",
                source_type="direct_user",
                keyword_id=1,
                keyword_text="direct",
                video_id="",
                video_url="",
                comment_id="",
                comment_url="",
                author_url=f"https://www.douyin.com/user/direct-{index}",
            )],
        )
        for index in range(3)
    ]
    plan = DiscoveryPlannerAgent().plan(
        platform="douyin",
        keywords=[_keyword(1, "direct", "new")],
        budget=budget,
    )
    metrics = {
        "keywords": {
            "1": {
                "videos_explored": 0,
                "explored_video_ids": [],
                "relevant_videos": 0,
                "candidate_count": 3,
                "pages": 1,
                "llm_calls": 1,
                "duration_minutes": 0.01,
                "author_videos_explored": 0,
                "total_observations": 3,
                "truncation_reasons": [],
            }
        },
        "totals": {"pages": 1, "llm_calls": 1, "duration_seconds": 0.6},
    }

    from tiktok_bot_core.services.pipeline import PipelineService

    PipelineService._validate_acquisition_batch(
        candidates,
        budget=budget,
        planned_keywords=plan.keywords,
        collection_metrics=metrics,
    )


def test_pipeline_rejects_missing_authoritative_collection_metrics():
    budget = ExplorationBudget(max_keywords=1)
    plan = DiscoveryPlannerAgent().plan(
        platform="douyin",
        keywords=[_keyword(1, "missing", "new")],
        budget=budget,
    )
    from tiktok_bot_core.services.pipeline import PipelineService

    with pytest.raises(ValueError, match="metrics"):
        PipelineService._validate_acquisition_batch(
            [],
            budget=budget,
            planned_keywords=plan.keywords,
            collection_metrics={"keywords": {}},
        )

    complete_keyword_metric = {
        "videos_explored": 0,
        "explored_video_ids": [],
        "relevant_videos": 0,
        "candidate_count": 0,
        "pages": 0,
        "llm_calls": 0,
        "duration_minutes": 0.0,
        "author_videos_explored": 0,
        "total_observations": 0,
        "truncation_reasons": [],
    }
    with pytest.raises(ValueError, match="total metrics"):
        PipelineService._validate_acquisition_batch(
            [],
            budget=budget,
            planned_keywords=plan.keywords,
            collection_metrics={"keywords": {"1": complete_keyword_metric}},
        )


@pytest.mark.asyncio
async def test_pipeline_retry_recomputes_distinct_keyword_evidence_across_runs(
    acquisition_db, monkeypatch,
):
    from tiktok_bot_core import storage
    from tiktok_bot_core.extensions.registry import register as get_registry
    from tiktok_bot_core.services import pipeline as pipeline_module
    from tiktok_bot_core.services.pipeline import PipelineRunContext, PipelineService

    job_id = _create_job(acquisition_db, search_budget={
        "maxKeywords": 1,
        "maxVideosPerKeyword": 2,
        "maxCommentsPerVideo": 2,
        "maxPages": 3,
        "maxLlmCalls": 1,
    })
    with acquisition_db.session() as session:
        keyword = AcquisitionStore().create_keyword(
            session, job_id=job_id, platform="douyin", text="retry-videos"
        )
        keyword_id = keyword.id

    hermes = MagicMock()
    call_index = 0

    async def collect_keyword(**_kwargs):
        nonlocal call_index
        video_id = ("video-a", "video-b")[call_index]
        call_index += 1
        hermes.last_budget_usage = {
            "pages": 3,
            "llm_calls": 1,
            "duration_seconds": 0.1,
        }
        hermes.last_exhaustion_reason = "max_pages"
        hermes.last_visited_urls = [f"https://www.douyin.com/video/{video_id}"]
        candidate = CandidateObservation(
            platform="douyin",
            platform_user_id="retry-user",
            username="retry-user",
            evidence=[_evidence(
                platform_user_id="retry-user",
                username="retry-user",
                keyword_id=keyword_id,
                keyword_text="retry-videos",
                video_id=video_id,
                video_url=f"https://www.douyin.com/video/{video_id}",
                comment_id=f"comment-{video_id}",
                comment_url=f"https://www.douyin.com/video/{video_id}?comment=1",
            )],
        )
        return [candidate] if video_id == "video-a" else []

    hermes.collect_keyword = AsyncMock(side_effect=collect_keyword)
    collector = KeywordCollector(monotonic=lambda: 0.0)
    collector._search_one = AsyncMock(return_value=[])
    monkeypatch.setattr(pipeline_module, "HermesEvidenceAgent", lambda **_kwargs: hermes)
    registry = get_registry()
    old_collectors = dict(registry.collectors)
    registry.collectors["keyword"] = collector
    old_db = storage.database._db_instance
    storage.database._db_instance = acquisition_db
    try:
        service = PipelineService()
        context = PipelineRunContext(
            job_id=job_id,
            platform="douyin",
            account_id=1,
            account_username="account",
            browser_session=BrowserSession(
                platform="douyin", account_id=1, client=MagicMock()
            ),
        )
        results = []
        for _ in range(2):
            results.append([item async for item in service.run(
                context, stages=["collect"]
            )][0])
    finally:
        storage.database._db_instance = old_db
        registry.collectors.clear()
        registry.collectors.update(old_collectors)

    assert results[1]["status"] == "ok"
    assert results[1]["result"]["keyword_stats"][0]["video_count"] == 2
    assert results[1]["result"]["keyword_stats"][0]["usage_count"] == 1
    with acquisition_db.session() as session:
        keyword = AcquisitionStore().list_keywords(session, job_id)[0]
        assert keyword.video_count == 2
        assert keyword.usage_count == 1
        assert session.query(DiscoveryEvidence).count() == 1


@pytest.mark.asyncio
async def test_pipeline_campaign_invalid_observation_fails_closed_without_evidence(
    acquisition_db,
):
    from tiktok_bot_core.extensions.registry import register as get_registry
    from tiktok_bot_core.services.pipeline import PipelineRunContext, PipelineService
    from tiktok_bot_core import storage

    job_id = _create_job(acquisition_db)
    with acquisition_db.session() as session:
        AcquisitionStore().create_keyword(
            session,
            job_id=job_id,
            platform="douyin",
            text="变压器采购",
        )

    invalid = {
        "platform": "douyin",
        "platform_user_id": "sec-invalid",
        "username": "invalid-buyer",
        "evidence": [{
            **_evidence(
                platform_user_id="sec-invalid",
                username="invalid-buyer",
            ).model_dump(mode="json"),
            "api_key": "must-be-rejected",
        }],
    }
    collector = MagicMock(collect=AsyncMock(return_value=[invalid]))
    registry = get_registry()
    old_collectors = dict(registry.collectors)
    registry.collectors["keyword"] = collector
    old_db = storage.database._db_instance
    storage.database._db_instance = acquisition_db
    try:
        service = PipelineService()
        service.vector = MagicMock()
        context = PipelineRunContext(
            job_id=job_id,
            platform="douyin",
            account_id=1,
            account_username="account",
            browser_session=BrowserSession(
                platform="douyin", account_id=1, client=MagicMock()
            ),
        )
        results = [item async for item in service.run(
            context,
            stages=["collect"],
        )]
    finally:
        storage.database._db_instance = old_db
        registry.collectors.clear()
        registry.collectors.update(old_collectors)

    assert results[0]["status"] == "error"
    with acquisition_db.session() as session:
        assert session.query(PipelineJobUser).count() == 0
        assert session.query(DiscoveryEvidence).count() == 0


@pytest.mark.asyncio
async def test_pipeline_passes_all_campaign_budgets_and_hermes_agent(
    acquisition_db, monkeypatch,
):
    from tiktok_bot_core import storage
    from tiktok_bot_core.browser.providers import BrowserSession
    from tiktok_bot_core.extensions.registry import register as get_registry
    from tiktok_bot_core.services import pipeline as pipeline_module
    from tiktok_bot_core.services.pipeline import PipelineRunContext, PipelineService

    search_budget = {
        "maxKeywords": 1,
        "maxVideosPerKeyword": 2,
        "maxCommentsPerVideo": 3,
        "maxAuthorVideos": 4,
        "maxPages": 5,
        "maxDurationMinutes": 6,
        "maxLlmCalls": 7,
    }
    job_id = _create_job(acquisition_db, search_budget=search_budget)
    with acquisition_db.session() as session:
        AcquisitionStore().create_keyword(
            session, job_id=job_id, platform="douyin", text="采购"
        )
    hermes = MagicMock()

    async def collect_keyword(**_kwargs):
        hermes.last_budget_usage = {
            "pages": 0,
            "llm_calls": 0,
            "duration_seconds": 0.0,
        }
        hermes.last_exhaustion_reason = ""
        hermes.last_visited_urls = []
        return []

    hermes.collect_keyword = AsyncMock(side_effect=collect_keyword)
    collector = KeywordCollector(monotonic=lambda: 0.0)
    collector._search_one = AsyncMock(return_value=[])
    collector.collect = AsyncMock(wraps=collector.collect)
    monkeypatch.setattr(pipeline_module, "HermesEvidenceAgent", lambda **_kwargs: hermes)
    registry = get_registry()
    old_collectors = dict(registry.collectors)
    registry.collectors["keyword"] = collector
    old_db = storage.database._db_instance
    storage.database._db_instance = acquisition_db
    try:
        service = PipelineService()
        context = PipelineRunContext(
            job_id=job_id,
            platform="douyin",
            account_id=1,
            account_username="account",
            browser_session=BrowserSession(
                platform="douyin", account_id=1, client=MagicMock()
            ),
        )
        result = [item async for item in service.run(
            context, stages=["collect"]
        )][0]
    finally:
        storage.database._db_instance = old_db
        registry.collectors.clear()
        registry.collectors.update(old_collectors)

    assert result["status"] == "ok"
    config = collector.collect.await_args.args[0]
    assert config["budget"] == {
        "max_keywords": 1,
        "max_videos_per_keyword": 2,
        "max_comments_per_video": 3,
        "max_author_videos": 4,
        "max_pages": 5,
        "max_duration_minutes": 6,
        "max_llm_calls": 7,
        "max_profiles": 8,
        "max_total_observations": 16,
    }
    assert config["evidence_agent"] is hermes


@pytest.mark.asyncio
async def test_pipeline_rejects_oversized_collector_batch_before_transaction(
    acquisition_db,
):
    from tiktok_bot_core import storage
    from tiktok_bot_core.browser.providers import BrowserSession
    from tiktok_bot_core.extensions.registry import register as get_registry
    from tiktok_bot_core.services.pipeline import PipelineRunContext, PipelineService

    job_id = _create_job(acquisition_db, search_budget={
        "maxKeywords": 1,
        "maxVideosPerKeyword": 1,
        "maxCommentsPerVideo": 1,
        "maxAuthorVideos": 1,
        "maxPages": 1,
        "maxDurationMinutes": 1,
        "maxLlmCalls": 1,
    })
    with acquisition_db.session() as session:
        keyword = AcquisitionStore().create_keyword(
            session, job_id=job_id, platform="douyin", text="采购"
        )
        keyword_id = keyword.id
    candidate = {
        "platform": "douyin",
        "platform_user_id": "oversized",
        "username": "oversized",
        "evidence": [
            _evidence(
                platform_user_id="oversized",
                username="oversized",
                keyword_id=keyword_id,
                video_id=f"video-{index}",
                comment_id=f"comment-{index}",
            ).model_dump(mode="json")
            for index in range(2)
        ],
    }
    collector = MagicMock(collect=AsyncMock(return_value=[candidate]))
    registry = get_registry()
    old_collectors = dict(registry.collectors)
    registry.collectors["keyword"] = collector
    old_db = storage.database._db_instance
    storage.database._db_instance = acquisition_db
    try:
        service = PipelineService()
        context = PipelineRunContext(
            job_id=job_id,
            platform="douyin",
            account_id=1,
            account_username="account",
            browser_session=BrowserSession(
                platform="douyin", account_id=1, client=MagicMock()
            ),
        )
        result = [item async for item in service.run(
            context, stages=["collect"]
        )][0]
    finally:
        storage.database._db_instance = old_db
        registry.collectors.clear()
        registry.collectors.update(old_collectors)

    assert result["status"] == "error"
    assert "budget" in result["result"]["error"].lower()
    with acquisition_db.session() as session:
        assert session.query(PipelineJobUser).count() == 0
        assert session.query(DiscoveryEvidence).count() == 0
