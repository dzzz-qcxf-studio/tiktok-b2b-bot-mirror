from unittest.mock import AsyncMock, MagicMock
from pathlib import Path
import tempfile

import pytest
from pydantic import ValidationError

from tiktok_bot_core.storage.database import Database


@pytest.fixture
def db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
        path = Path(handle.name)
    database = Database(f"sqlite:///{path}")
    database.init()
    yield database
    database.engine.dispose()
    try:
        path.unlink()
    except PermissionError:
        pass


def _patch_global_db(database):
    import tiktok_bot_core.storage.database as database_module

    database_module._db_instance = database


def _campaign_candidate(database, *, platform="douyin", username="buyer"):
    from tiktok_bot_core.storage.acquisition_store import AcquisitionStore
    from tiktok_bot_core.storage.pipeline_job_store import PipelineJobStore
    from tiktok_bot_core.storage.sqlite_store import SqliteStore

    with database.session() as session:
        job = PipelineJobStore().create_job(
            session,
            platform=platform,
            account_mode="auto",
            account_id=None,
            stages=["filter", "strategy", "outreach"],
        )
        user = SqliteStore().add_user(
            session,
            tiktok_id=f"{platform}:{username}",
            username=username,
            nickname="Grid Buyer",
            bio="Electrical infrastructure contractor",
            follower_count=1200,
            platform=platform,
        )
        PipelineJobStore().link_user(session, job.id, user.id, "collect")
        acquisition = AcquisitionStore()
        acquisition.create_campaign(
            session,
            job_id=job.id,
            platform=platform,
            industries=["electrical infrastructure"],
            hard_conditions={"excludedIdentities": ["consumer"]},
        )
        acquisition.add_evidence(
            session,
            job_id=job.id,
            user_id=user.id,
            source_type="comment_author",
            comment_id="comment-1",
            raw_text="Please quote 20 units for our next substation project",
        )
        return job.id, user.id


def _context(job_id, *, platform="douyin"):
    from tiktok_bot_core.browser.providers import BrowserSession
    from tiktok_bot_core.services.pipeline import PipelineRunContext

    return PipelineRunContext(
        job_id=job_id,
        platform=platform,
        account_id=1,
        account_username="test-account",
        browser_session=BrowserSession(
            platform=platform, account_id=1, client=MagicMock()
        ),
    )


def _qualification_payload(**overrides):
    payload = {
        "schema_version": "1.0",
        "labels": ["buyer", "contractor"],
        "match_score": 86,
        "confidence_score": 62,
        "positive_evidence": ["comment asks for a wholesale quotation"],
        "negative_evidence": [],
        "missing_fields": ["company_registration"],
        "reasoning": "Explicit purchase intent, but company identity is incomplete.",
        "suggested_status": "qualified",
        "hard_exclusion": False,
        "hard_exclusion_reasons": [],
    }
    payload.update(overrides)
    return payload


def test_stage02_contracts_are_versioned_strict_and_keep_scores_separate():
    from tiktok_bot_core.services.acquisition_agents import (
        EnrichmentResult,
        QualificationResult,
    )

    result = QualificationResult.model_validate(_qualification_payload())
    assert result.match_score == 86
    assert result.confidence_score == 62
    assert result.labels == ("buyer", "contractor")

    EnrichmentResult.model_validate(
        {
            "schema_version": "1.0",
            "profile_summary": "Public company profile",
            "representative_content": ["Substation construction project"],
            "business_signals": ["Public procurement contact"],
            "missing_fields": ["registration_capital"],
        }
    )

    with pytest.raises(ValidationError):
        QualificationResult.model_validate(
            _qualification_payload(unexpected_private_field="secret")
        )


@pytest.mark.asyncio
async def test_enrichment_and_qualification_agents_use_qualification_route():
    from tiktok_bot_core.services.acquisition_agents import (
        EnrichmentAgent,
        QualificationAgent,
    )

    router = MagicMock()
    router.json_completion = AsyncMock(
        side_effect=[
            {
                "schema_version": "1.0",
                "profile_summary": "Electrical contractor",
                "representative_content": ["Grid upgrade project"],
                "business_signals": ["Requests supplier quotation"],
                "missing_fields": ["employee_count"],
            },
            _qualification_payload(),
        ]
    )
    public_profile = {
        "username": "gridbuyer",
        "nickname": "Grid Buyer",
        "bio": "Electrical infrastructure contractor",
        "follower_count": 1200,
    }
    evidence = [
        {
            "source_type": "comment_author",
            "raw_text": "Please quote 20 units for our next substation project",
            "translated_text": "",
            "video_url": "https://www.tiktok.com/video/1",
        }
    ]

    enriched = await EnrichmentAgent(router=router).run(
        public_profile=public_profile,
        public_content=[],
        evidence=evidence,
    )
    assessed = await QualificationAgent(router=router).run(
        campaign={"industries": ["electrical infrastructure"]},
        public_profile=public_profile,
        enrichment=enriched,
        evidence=evidence,
    )

    assert assessed.positive_evidence[0].startswith("comment asks")
    assert [call.kwargs["route"] for call in router.json_completion.await_args_list] == [
        "qualification",
        "qualification",
    ]


@pytest.mark.asyncio
async def test_enrichment_agent_repairs_schema_once_without_replaying_raw_output():
    from tiktok_bot_core.services.acquisition_agents import EnrichmentAgent

    router = MagicMock()
    router.json_completion = AsyncMock(
        side_effect=[
            {
                "enriched_profile": "RAW_MODEL_CANARY",
                "normalized_content": [],
                "confidence": 0.8,
            },
            {
                "schema_version": "1.0",
                "profile_summary": "Electrical infrastructure contractor",
                "representative_content": [],
                "business_signals": ["Public project activity"],
                "missing_fields": ["employee_count"],
            },
        ]
    )

    result = await EnrichmentAgent(router=router).run(
        public_profile={"username": "grid-builder"},
        public_content=[],
        evidence=[],
    )

    assert result.profile_summary == "Electrical infrastructure contractor"
    assert router.json_completion.await_count == 2
    first_prompt = router.json_completion.await_args_list[0].args[0]
    repair_prompt = router.json_completion.await_args_list[1].args[0]
    for field in (
        "schema_version",
        "profile_summary",
        "representative_content",
        "business_signals",
        "missing_fields",
    ):
        assert field in first_prompt
        assert field in repair_prompt
    assert "grid-builder" in repair_prompt
    assert "RAW_MODEL_CANARY" not in repair_prompt
    assert "enriched_profile" not in repair_prompt


@pytest.mark.asyncio
async def test_qualification_agent_repairs_schema_once_without_replaying_raw_output():
    from tiktok_bot_core.services.acquisition_agents import (
        EnrichmentResult,
        QualificationAgent,
    )

    router = MagicMock()
    router.json_completion = AsyncMock(
        side_effect=[
            {
                "classification": "RAW_QUALIFICATION_CANARY",
                "score": 88,
            },
            _qualification_payload(),
        ]
    )

    result = await QualificationAgent(router=router).run(
        campaign={"industries": ["electrical infrastructure"]},
        public_profile={"username": "grid-builder"},
        enrichment=EnrichmentResult(),
        evidence=[],
    )

    assert result.suggested_status == "qualified"
    assert router.json_completion.await_count == 2
    first_prompt = router.json_completion.await_args_list[0].args[0]
    repair_prompt = router.json_completion.await_args_list[1].args[0]
    for field in (
        "match_score",
        "confidence_score",
        "positive_evidence",
        "negative_evidence",
        "suggested_status",
        "hard_exclusion_reasons",
    ):
        assert field in first_prompt
        assert field in repair_prompt
    for status in ("qualified", "manual_review", "need_enrichment", "rejected"):
        assert status in first_prompt
    assert "RAW_QUALIFICATION_CANARY" not in repair_prompt
    assert "classification" not in repair_prompt


@pytest.mark.asyncio
async def test_enrichment_agent_stops_after_one_schema_repair():
    from tiktok_bot_core.services.acquisition_agents import EnrichmentAgent

    router = MagicMock()
    router.json_completion = AsyncMock(
        side_effect=[{"unexpected": "first"}, {"unexpected": "second"}]
    )

    with pytest.raises(ValidationError):
        await EnrichmentAgent(router=router).run(
            public_profile={"username": "grid-builder"},
            public_content=[],
            evidence=[],
        )

    assert router.json_completion.await_count == 2


@pytest.mark.asyncio
async def test_qualification_agent_stops_after_one_schema_repair():
    from tiktok_bot_core.services.acquisition_agents import (
        EnrichmentResult,
        QualificationAgent,
    )

    router = MagicMock()
    router.json_completion = AsyncMock(
        side_effect=[{"unexpected": "first"}, {"unexpected": "second"}]
    )

    with pytest.raises(ValidationError):
        await QualificationAgent(router=router).run(
            campaign={},
            public_profile={"username": "grid-builder"},
            enrichment=EnrichmentResult(),
            evidence=[],
        )

    assert router.json_completion.await_count == 2


@pytest.mark.asyncio
async def test_stage02_agents_do_not_duplicate_router_network_retries():
    from tiktok_bot_core.llm.router import LLMRouteError
    from tiktok_bot_core.services.acquisition_agents import EnrichmentAgent

    router = MagicMock()
    router.json_completion = AsyncMock(
        side_effect=LLMRouteError(route="qualification", error_category="network")
    )

    with pytest.raises(LLMRouteError):
        await EnrichmentAgent(router=router).run(
            public_profile={"username": "grid-builder"},
            public_content=[],
            evidence=[],
        )

    assert router.json_completion.await_count == 1


def test_pipeline_job_store_ai_update_does_not_touch_human_labels_or_version(db):
    from tiktok_bot_core.storage.pipeline_job_store import PipelineJobStore
    from tiktok_bot_core.models.entities import PipelineJobUser

    job_id, user_id = _campaign_candidate(db)
    store = PipelineJobStore()
    with db.session() as session:
        link = session.get(PipelineJobUser, (job_id, user_id))
        link.labels_json = ["human-corrected"]
        link.review_version = 7

    with db.session() as session:
        assert store.update_ai_qualification(
            session,
            job_id,
            user_id,
            qualification_status="manual_review",
            match_score=91,
            confidence_score=73,
            category="buyer",
            expected_review_version=7,
            expected_qualification_status="manual_review",
        )

    with db.session() as session:
        link = session.get(PipelineJobUser, (job_id, user_id))
        assert link.labels_json == ["human-corrected"]
        assert link.review_version == 7
        assert link.match_score == 91
        assert link.confidence_score == 73


@pytest.mark.parametrize("terminal_status", ["qualified", "rejected"])
def test_pipeline_job_store_rejects_ai_terminal_write(db, terminal_status):
    from tiktok_bot_core.storage.pipeline_job_store import PipelineJobStore

    job_id, user_id = _campaign_candidate(db)
    with db.session() as session, pytest.raises(ValueError, match="AI.*terminal"):
        PipelineJobStore().update_ai_qualification(
            session,
            job_id,
            user_id,
            qualification_status=terminal_status,
            expected_review_version=0,
            expected_qualification_status="manual_review",
        )


@pytest.mark.asyncio
async def test_campaign_filter_appends_ai_assessment_but_high_score_stays_manual(
    db, monkeypatch
):
    _patch_global_db(db)
    job_id, user_id = _campaign_candidate(db)
    router = MagicMock()
    router.json_completion = AsyncMock(
        side_effect=[
            {
                "schema_version": "1.0",
                "profile_summary": "Electrical contractor",
                "representative_content": [],
                "business_signals": ["purchase request"],
                "missing_fields": [],
            },
            _qualification_payload(match_score=98, confidence_score=97),
        ]
    )
    monkeypatch.setattr(
        "tiktok_bot_core.services.pipeline.get_llm_client", lambda: router
    )

    from tiktok_bot_core.services.pipeline import PipelineService
    from tiktok_bot_core.models.entities import PipelineJobUser
    from tiktok_bot_core.storage.acquisition_store import AcquisitionStore

    result = await PipelineService()._run_filter(
        None, None, None, _context(job_id)
    )

    assert result["total"] == 1
    assert result["manual_review"] == 1
    with db.session() as session:
        link = session.get(PipelineJobUser, (job_id, user_id))
        assessment = AcquisitionStore().latest_assessment(
            session, job_id, user_id
        )
        assert link.qualification_status == "manual_review"
        assert link.labels_json == []
        assert link.review_version == 0
        assert assessment.suggested_status == "qualified"
        assert assessment.labels_json == ["buyer", "contractor"]
        assert assessment.match_score == 98
        assert assessment.confidence_score == 97


@pytest.mark.asyncio
async def test_campaign_filter_invalid_qualification_schema_fails_safe_to_manual(
    db, monkeypatch
):
    _patch_global_db(db)
    job_id, user_id = _campaign_candidate(db)
    router = MagicMock()
    router.json_completion = AsyncMock(
        side_effect=[
            {
                "schema_version": "1.0",
                "profile_summary": "Known public profile",
                "representative_content": [],
                "business_signals": [],
                "missing_fields": [],
            },
            _qualification_payload(private_cookie="forbidden"),
        ]
    )
    monkeypatch.setattr(
        "tiktok_bot_core.services.pipeline.get_llm_client", lambda: router
    )

    from tiktok_bot_core.services.pipeline import PipelineService
    from tiktok_bot_core.models.entities import PipelineJobUser

    result = await PipelineService()._run_filter(
        None, None, None, _context(job_id)
    )

    assert result["manual_review"] == 1
    with db.session() as session:
        link = session.get(PipelineJobUser, (job_id, user_id))
        assert link.qualification_status == "manual_review"
        assert link.status == "pending"


def test_unknown_information_is_missing_not_negative_or_auto_rejected():
    from tiktok_bot_core.services.acquisition_agents import QualificationResult

    result = QualificationResult.model_validate(
        _qualification_payload(
            suggested_status="need_enrichment",
            negative_evidence=[],
            missing_fields=["employee_count", "registration_capital"],
        )
    )
    assert result.suggested_status == "need_enrichment"
    assert result.negative_evidence == ()

    with pytest.raises(ValidationError):
        QualificationResult.model_validate(
            _qualification_payload(
                suggested_status="rejected",
                hard_exclusion=False,
                hard_exclusion_reasons=[],
                negative_evidence=["employee_count is unknown"],
                missing_fields=["employee_count"],
            )
        )


@pytest.mark.parametrize(
    "unknown_statement",
    [
        "company size is unknown",
        "registration capital is not provided",
        "公司规模未知",
        "注册资本不详",
        "员工数未提供",
    ],
)
def test_unknown_statements_are_never_negative_even_without_missing_fields(
    unknown_statement,
):
    from tiktok_bot_core.services.acquisition_agents import QualificationResult

    with pytest.raises(ValidationError):
        QualificationResult.model_validate(
            _qualification_payload(
                suggested_status="manual_review",
                negative_evidence=[unknown_statement],
                missing_fields=[],
            )
        )


def test_concrete_mismatch_remains_legitimate_negative_evidence():
    from tiktok_bot_core.services.acquisition_agents import QualificationResult

    result = QualificationResult.model_validate(
        _qualification_payload(
            suggested_status="manual_review",
            negative_evidence=["company size exceeds the target maximum"],
            missing_fields=[],
        )
    )
    assert result.negative_evidence == (
        "company size exceeds the target maximum",
    )

    with pytest.raises(ValidationError):
        QualificationResult.model_validate(
            _qualification_payload(
                suggested_status="manual_review",
                negative_evidence=["employee_count is unknown"],
                missing_fields=["employee_count"],
            )
        )


@pytest.mark.asyncio
async def test_enrichment_prompt_allowlists_public_content_fields():
    from tiktok_bot_core.services.acquisition_agents import EnrichmentAgent

    router = MagicMock()

    async def answer(prompt, *, route, **_):
        assert route == "qualification"
        assert "Public project caption" in prompt
        assert "private-cookie-value" not in prompt
        assert "local_profile_path" not in prompt
        return {
            "schema_version": "1.0",
            "profile_summary": "Public profile",
            "representative_content": ["Public project caption"],
            "business_signals": [],
            "missing_fields": [],
        }

    router.json_completion = AsyncMock(side_effect=answer)
    await EnrichmentAgent(router=router).run(
        public_profile={"username": "buyer", "cookie": "private-cookie-value"},
        public_content=[
            {
                "caption": "Public project caption",
                "cookie": "private-cookie-value",
                "local_profile_path": "C:/private/profile",
            }
        ],
        evidence=[],
    )


@pytest.mark.asyncio
async def test_enrichment_prompt_truncates_plain_string_content():
    from tiktok_bot_core.services.acquisition_agents import EnrichmentAgent

    router = MagicMock()

    async def answer(prompt, *, route, **_):
        assert route == "qualification"
        assert "PLAIN_CONTENT" in prompt
        assert "PLAIN_CONTENT_TAIL_SENTINEL" not in prompt
        return {
            "schema_version": "1.0",
            "profile_summary": "bounded",
            "representative_content": [],
            "business_signals": [],
            "missing_fields": [],
        }

    router.json_completion = AsyncMock(side_effect=answer)
    await EnrichmentAgent(router=router).run(
        public_profile={"username": "buyer"},
        public_content=[
            "PLAIN_CONTENT" + ("x" * 1200) + "PLAIN_CONTENT_TAIL_SENTINEL"
        ],
        evidence=[],
    )


@pytest.mark.asyncio
async def test_agent_prompt_bounds_evidence_count_and_field_length():
    from tiktok_bot_core.services.acquisition_agents import EnrichmentAgent

    router = MagicMock()

    async def answer(prompt, *, route, **_):
        assert route == "qualification"
        assert "EVIDENCE_00" in prompt
        assert "EVIDENCE_19" in prompt
        assert "EVIDENCE_20" not in prompt
        assert "TRUNCATION_SENTINEL" not in prompt
        return {
            "schema_version": "1.0",
            "profile_summary": "bounded",
            "representative_content": [],
            "business_signals": [],
            "missing_fields": [],
        }

    router.json_completion = AsyncMock(side_effect=answer)
    await EnrichmentAgent(router=router).run(
        public_profile={"username": "buyer"},
        public_content=[],
        evidence=[
            {
                "source_type": "comment_author",
                "raw_text": f"EVIDENCE_{index:02d}:"
                + ("x" * 1200)
                + "TRUNCATION_SENTINEL",
            }
            for index in range(25)
        ],
    )


def test_agent_prompt_budget_counts_utf8_bytes(monkeypatch):
    import tiktok_bot_core.services.acquisition_agents as agent_module

    instruction = "i"
    payload = {"text": "中" * 5}
    rendered = instruction + "\n" + '{"text":"' + ("中" * 5) + '"}'
    assert len(rendered) < 20
    assert len(rendered.encode("utf-8")) > 20
    monkeypatch.setattr(agent_module, "MAX_AGENT_PROMPT_CHARS", 20)
    with pytest.raises(ValueError, match="prompt.*large"):
        agent_module._build_agent_prompt(instruction, payload)


@pytest.mark.asyncio
async def test_purchase_demand_comment_after_first_20_is_prioritized():
    from tiktok_bot_core.services.acquisition_agents import EnrichmentAgent

    router = MagicMock()

    async def answer(prompt, *, route, **_):
        assert route == "qualification"
        assert "Please quote 50 units for our procurement" in prompt
        assert "ordinary-00" in prompt
        return {
            "schema_version": "1.0",
            "profile_summary": "bounded",
            "representative_content": [],
            "business_signals": [],
            "missing_fields": [],
        }

    router.json_completion = AsyncMock(side_effect=answer)
    evidence = [
        {
            "source_type": "video_author",
            "raw_text": f"ordinary-{index:02d}",
            "relevance_score": 1.0,
            "completeness_score": 1.0,
        }
        for index in range(20)
    ]
    evidence.append(
        {
            "source_type": "comment_author",
            "raw_text": "Please quote 50 units for our procurement",
            "relevance_score": 0.1,
            "completeness_score": 0.1,
        }
    )
    await EnrichmentAgent(router=router).run(
        public_profile={"username": "buyer"},
        public_content=[],
        evidence=evidence,
    )


@pytest.mark.asyncio
async def test_evidence_selection_preserves_source_diversity():
    from tiktok_bot_core.services.acquisition_agents import EnrichmentAgent

    router = MagicMock()

    async def answer(prompt, *, route, **_):
        assert "PROFILE_SOURCE" in prompt
        assert "DIRECT_SOURCE" in prompt
        return {
            "schema_version": "1.0",
            "profile_summary": "bounded",
            "representative_content": [],
            "business_signals": [],
            "missing_fields": [],
        }

    router.json_completion = AsyncMock(side_effect=answer)
    evidence = [
        {
            "source_type": "comment_author",
            "raw_text": f"comment-{index:02d}",
            "relevance_score": 0.9,
            "completeness_score": 0.9,
        }
        for index in range(20)
    ] + [
        {
            "source_type": "profile",
            "raw_text": "PROFILE_SOURCE",
            "relevance_score": 0.8,
            "completeness_score": 0.8,
        },
        {
            "source_type": "direct_user",
            "raw_text": "DIRECT_SOURCE",
            "relevance_score": 0.7,
            "completeness_score": 0.7,
        },
    ]
    await EnrichmentAgent(router=router).run(
        public_profile={"username": "buyer"},
        public_content=[],
        evidence=evidence,
    )


@pytest.mark.asyncio
async def test_enrichment_prompt_total_budget_fails_closed_before_router():
    from tiktok_bot_core.services.acquisition_agents import EnrichmentAgent

    router = MagicMock(json_completion=AsyncMock())
    evidence = [
        {
            "source_type": "comment_author",
            "keyword_text": "k" * 1000,
            "video_url": "v" * 1000,
            "comment_url": "c" * 1000,
            "author_url": "a" * 1000,
            "raw_text": "r" * 1000,
            "translated_text": "t" * 1000,
        }
        for _ in range(20)
    ]
    with pytest.raises(ValueError, match="prompt.*large"):
        await EnrichmentAgent(router=router).run(
            public_profile={"username": "u" * 1000, "bio": "b" * 1000},
            public_content=["p" * 1000 for _ in range(20)],
            evidence=evidence,
        )
    assert router.json_completion.await_count == 0


@pytest.mark.asyncio
async def test_qualification_prompt_total_budget_fails_closed_before_router():
    from tiktok_bot_core.services.acquisition_agents import (
        EnrichmentResult,
        QualificationAgent,
    )

    router = MagicMock(json_completion=AsyncMock())
    with pytest.raises(ValueError, match="prompt.*large"):
        await QualificationAgent(router=router).run(
            campaign={
                "industries": ["industry-" + ("x" * 1000) for _ in range(50)],
                "products": ["product-" + ("y" * 1000) for _ in range(50)],
            },
            public_profile={"username": "buyer"},
            enrichment=EnrichmentResult(),
            evidence=[],
        )
    assert router.json_completion.await_count == 0


@pytest.mark.asyncio
async def test_campaign_strategy_prompt_budget_fails_closed_before_router(
    monkeypatch,
):
    import tiktok_bot_core.services.acquisition_agents as agent_module

    monkeypatch.setattr(agent_module, "MAX_AGENT_PROMPT_CHARS", 100)
    router = MagicMock(json_completion=AsyncMock())
    with pytest.raises(ValueError, match="prompt.*large"):
        await agent_module.CampaignStrategyAgent(router=router).run(
            public_profile={"username": "buyer", "bio": "public bio"},
            category="buyer",
        )
    assert router.json_completion.await_count == 0


@pytest.mark.asyncio
async def test_agent_prompt_rejects_non_json_number_before_router():
    from tiktok_bot_core.services.acquisition_agents import EnrichmentAgent

    router = MagicMock(json_completion=AsyncMock())
    with pytest.raises(ValueError):
        await EnrichmentAgent(router=router).run(
            public_profile={"username": "buyer"},
            public_content=[],
            evidence=[
                {
                    "source_type": "comment_author",
                    "relevance_score": float("nan"),
                }
            ],
        )
    assert router.json_completion.await_count == 0


@pytest.mark.asyncio
async def test_demand_comment_is_sent_to_qualification_as_priority_evidence():
    from tiktok_bot_core.services.acquisition_agents import (
        EnrichmentResult,
        QualificationAgent,
    )

    router = MagicMock()

    async def answer(prompt, *, route, **_):
        assert route == "qualification"
        assert "Please quote 20 units" in prompt
        assert "purchase-demand comments as strong positive evidence" in prompt
        return _qualification_payload()

    router.json_completion = AsyncMock(side_effect=answer)
    result = await QualificationAgent(router=router).run(
        campaign={"industries": ["electrical infrastructure"]},
        public_profile={"username": "buyer", "bio": "contractor"},
        enrichment=EnrichmentResult(
            profile_summary="contractor",
            representative_content=[],
            business_signals=[],
            missing_fields=[],
        ),
        evidence=[
            {
                "source_type": "comment_author",
                "raw_text": "Please quote 20 units",
            }
        ],
    )
    assert "comment asks" in result.positive_evidence[0]


@pytest.mark.asyncio
async def test_qualification_prompt_allowlists_campaign_fields():
    from tiktok_bot_core.services.acquisition_agents import (
        EnrichmentResult,
        QualificationAgent,
    )

    router = MagicMock()

    async def answer(prompt, *, route, **_):
        assert route == "qualification"
        assert "electrical infrastructure" in prompt
        assert "campaign-client-secret" not in prompt
        assert "private-cookie" not in prompt
        assert "local_profile_path" not in prompt
        return _qualification_payload()

    router.json_completion = AsyncMock(side_effect=answer)
    await QualificationAgent(router=router).run(
        campaign={
            "industries": ["electrical infrastructure"],
            "clientSecret": "campaign-client-secret",
            "cookie": "private-cookie",
            "local_profile_path": "C:/private/profile",
        },
        public_profile={"username": "buyer"},
        enrichment=EnrichmentResult(),
        evidence=[],
    )


def test_required_hard_condition_is_not_an_exclusion():
    from tiktok_bot_core.services.acquisition_agents import QualificationResult
    from tiktok_bot_core.services.pipeline import PipelineService

    result = QualificationResult.model_validate(
        _qualification_payload(
            suggested_status="rejected",
            hard_exclusion=True,
            hard_exclusion_reasons=["power"],
        )
    )
    assert not PipelineService._confirmed_hard_exclusion(
        {
            "excluded_targets": [],
            "hard_conditions": {"requiredKeywords": ["power"]},
        },
        result,
    )


def test_explicit_exclusion_hard_condition_can_auto_reject():
    from tiktok_bot_core.services.acquisition_agents import QualificationResult
    from tiktok_bot_core.services.pipeline import PipelineService

    result = QualificationResult.model_validate(
        _qualification_payload(
            suggested_status="rejected",
            hard_exclusion=True,
            hard_exclusion_reasons=["consumer"],
        )
    )
    assert PipelineService._confirmed_hard_exclusion(
        {
            "excluded_targets": [],
            "hard_conditions": {"excludedIdentities": ["consumer"]},
        },
        result,
    )


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("consumer", True),
        (" Consumer ", True),
        ("not a consumer", False),
        ("consumer-like", False),
    ],
)
def test_hard_exclusion_requires_exact_normalized_value(reason, expected):
    from tiktok_bot_core.services.acquisition_agents import QualificationResult
    from tiktok_bot_core.services.pipeline import PipelineService

    result = QualificationResult.model_validate(
        _qualification_payload(
            suggested_status="rejected",
            hard_exclusion=True,
            hard_exclusion_reasons=[reason],
        )
    )
    assert (
        PipelineService._confirmed_hard_exclusion(
            {
                "excluded_targets": ["consumer"],
                "hard_conditions": {},
            },
            result,
        )
        is expected
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("suggested", "hard_exclusion", "reasons", "expected"),
    [
        ("need_enrichment", False, [], "need_enrichment"),
        ("rejected", True, ["consumer"], "manual_review"),
        ("rejected", True, ["unconfigured reason"], "manual_review"),
    ],
)
async def test_campaign_filter_never_gives_ai_terminal_rejection_authority(
    db,
    monkeypatch,
    suggested,
    hard_exclusion,
    reasons,
    expected,
):
    _patch_global_db(db)
    job_id, user_id = _campaign_candidate(db)
    router = MagicMock()
    router.json_completion = AsyncMock(
        side_effect=[
            {
                "schema_version": "1.0",
                "profile_summary": "Public profile",
                "representative_content": [],
                "business_signals": [],
                "missing_fields": [],
            },
            _qualification_payload(
                suggested_status=suggested,
                hard_exclusion=hard_exclusion,
                hard_exclusion_reasons=reasons,
            ),
        ]
    )
    monkeypatch.setattr(
        "tiktok_bot_core.services.pipeline.get_llm_client", lambda: router
    )

    from tiktok_bot_core.services.pipeline import PipelineService
    from tiktok_bot_core.models.entities import PipelineJobUser
    from tiktok_bot_core.storage.acquisition_store import AcquisitionStore

    result = await PipelineService()._run_filter(
        None, None, None, _context(job_id)
    )
    assert result[expected] == 1
    with db.session() as session:
        link = session.get(PipelineJobUser, (job_id, user_id))
        assert link.qualification_status == expected
        assessment = AcquisitionStore().latest_assessment(
            session, job_id, user_id
        )
        assert assessment.suggested_status == suggested
        if suggested == "rejected":
            assert assessment.model_metadata_json["hardExclusion"] is True
            assert assessment.model_metadata_json[
                "hardExclusionReasons"
            ] == reasons


@pytest.mark.asyncio
async def test_enrichment_missing_fields_are_preserved_in_assessment(
    db, monkeypatch
):
    _patch_global_db(db)
    job_id, user_id = _campaign_candidate(db)
    router = MagicMock()
    router.json_completion = AsyncMock(
        side_effect=[
            {
                "schema_version": "1.0",
                "profile_summary": "Public profile",
                "representative_content": [],
                "business_signals": [],
                "missing_fields": ["employee_count"],
            },
            _qualification_payload(missing_fields=[]),
        ]
    )
    monkeypatch.setattr(
        "tiktok_bot_core.services.pipeline.get_llm_client", lambda: router
    )
    from tiktok_bot_core.services.pipeline import PipelineService
    from tiktok_bot_core.storage.acquisition_store import AcquisitionStore

    await PipelineService()._run_filter(None, None, None, _context(job_id))
    with db.session() as session:
        assessment = AcquisitionStore().latest_assessment(
            session, job_id, user_id
        )
        assert assessment.missing_fields_json == ["employee_count"]


@pytest.mark.asyncio
async def test_human_decision_wins_when_review_changes_during_llm_call(
    db, monkeypatch
):
    _patch_global_db(db)
    job_id, user_id = _campaign_candidate(db)
    from tiktok_bot_core.storage.acquisition_store import AcquisitionStore
    from tiktok_bot_core.models.entities import PipelineJobUser

    with db.session() as session:
        link = session.get(PipelineJobUser, (job_id, user_id))
        link.match_score = 11
        link.confidence_score = 22

    router = MagicMock()
    calls = 0

    async def answer(*_, **__):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "schema_version": "1.0",
                "profile_summary": "Public profile",
                "representative_content": [],
                "business_signals": [],
                "missing_fields": [],
            }
        with db.session() as session:
            AcquisitionStore().transition_candidate(
                session,
                job_id=job_id,
                user_id=user_id,
                target_status="qualified",
                action="approve",
                operator="reviewer-during-llm",
            )
        return _qualification_payload(match_score=99, confidence_score=99)

    router.json_completion = AsyncMock(side_effect=answer)
    monkeypatch.setattr(
        "tiktok_bot_core.services.pipeline.get_llm_client", lambda: router
    )
    from tiktok_bot_core.services.pipeline import PipelineService

    result = await PipelineService()._run_filter(
        None, None, None, _context(job_id)
    )
    with db.session() as session:
        link = session.get(PipelineJobUser, (job_id, user_id))
        assert link.qualification_status == "qualified"
        assert link.review_version == 1
        assert link.match_score == 11
        assert link.confidence_score == 22
    assert result["qualified"] == 1
    assert result["manual_review"] == 0
    assert result["stale_skipped"] == 1


@pytest.mark.asyncio
async def test_campaign_filter_processes_more_than_legacy_200_limit(
    db, monkeypatch
):
    _patch_global_db(db)
    job_id, _ = _campaign_candidate(db, username="candidate-000")
    for index in range(1, 205):
        _add_linked_user(
            db,
            job_id=job_id,
            username=f"candidate-{index:03d}",
            qualification_status="manual_review",
        )

    from tiktok_bot_core.services.acquisition_agents import (
        EnrichmentResult,
        QualificationResult,
    )

    class FastEnrichmentAgent:
        def __init__(self, *, router):
            pass

        async def run(self, **_):
            return EnrichmentResult()

    class FastQualificationAgent:
        def __init__(self, *, router):
            pass

        async def run(self, **_):
            return QualificationResult.model_validate(
                _qualification_payload(suggested_status="manual_review")
            )

    monkeypatch.setattr(
        "tiktok_bot_core.services.pipeline.EnrichmentAgent",
        FastEnrichmentAgent,
    )
    monkeypatch.setattr(
        "tiktok_bot_core.services.pipeline.QualificationAgent",
        FastQualificationAgent,
    )
    monkeypatch.setattr(
        "tiktok_bot_core.services.pipeline.get_llm_client", lambda: MagicMock()
    )
    from tiktok_bot_core.services.pipeline import PipelineService

    result = await PipelineService()._run_filter(
        None, None, None, _context(job_id)
    )
    assert result["total"] == 205
    assert result["manual_review"] == 205


def _add_linked_user(
    database,
    *,
    job_id,
    username,
    platform="douyin",
    qualification_status="manual_review",
):
    from tiktok_bot_core.storage.pipeline_job_store import PipelineJobStore
    from tiktok_bot_core.storage.sqlite_store import SqliteStore

    with database.session() as session:
        user = SqliteStore().add_user(
            session,
            tiktok_id=f"{platform}:{username}",
            username=username,
            bio="public business profile",
            platform=platform,
        )
        store = PipelineJobStore()
        store.link_user(session, job_id, user.id, "collect")
        if qualification_status == "qualified":
            from tiktok_bot_core.storage.acquisition_store import AcquisitionStore

            AcquisitionStore().transition_candidate(
                session,
                job_id=job_id,
                user_id=user.id,
                target_status="qualified",
                action="approve",
                operator="test-reviewer",
            )
        elif qualification_status != "manual_review":
            store.update_ai_qualification(
                session,
                job_id,
                user.id,
                qualification_status=qualification_status,
                expected_review_version=0,
                expected_qualification_status="manual_review",
            )
        return user.id


@pytest.mark.asyncio
async def test_campaign_strategy_uses_current_job_platform_and_qualified_only(
    db, monkeypatch
):
    _patch_global_db(db)
    job_id, qualified_id = _campaign_candidate(db, username="qualified")
    from tiktok_bot_core.storage.pipeline_job_store import PipelineJobStore
    from tiktok_bot_core.storage.acquisition_store import AcquisitionStore

    with db.session() as session:
        AcquisitionStore().transition_candidate(
            session,
            job_id=job_id,
            user_id=qualified_id,
            target_status="qualified",
            action="approve",
            operator="reviewer",
        )
    _add_linked_user(
        db, job_id=job_id, username="manual", qualification_status="manual_review"
    )
    _add_linked_user(
        db, job_id=job_id, username="enrich", qualification_status="need_enrichment"
    )
    _add_linked_user(
        db,
        job_id=job_id,
        username="wrong-platform",
        platform="tiktok",
        qualification_status="qualified",
    )
    with db.session() as session:
        other = PipelineJobStore().create_job(
            session,
            platform="douyin",
            account_mode="auto",
            account_id=None,
            stages=["strategy"],
        )
        AcquisitionStore().create_campaign(
            session, job_id=other.id, platform="douyin"
        )
        other_job_id = other.id
    _add_linked_user(
        db,
        job_id=other_job_id,
        username="other-job",
        qualification_status="qualified",
    )
    router = MagicMock()
    router.json_completion = AsyncMock(
        return_value={
            "persona": "buyer",
            "strategy_type": "soft_sell",
            "comment_template": "Hello",
            "dm_template": "Hi",
            "priority": 3,
            "action_plan": "review",
        }
    )
    monkeypatch.setattr(
        "tiktok_bot_core.services.pipeline.get_llm_client", lambda: router
    )

    from tiktok_bot_core.services.pipeline import PipelineService

    result = await PipelineService()._run_strategy(
        None, None, None, _context(job_id)
    )
    assert result == {"total": 1, "strategies": 1}
    assert router.json_completion.await_count == 1


@pytest.mark.asyncio
async def test_campaign_outreach_has_second_qualified_gate(db):
    _patch_global_db(db)
    job_id, qualified_id = _campaign_candidate(db, username="qualified")
    manual_id = _add_linked_user(
        db, job_id=job_id, username="manual", qualification_status="manual_review"
    )
    from tiktok_bot_core.storage.acquisition_store import AcquisitionStore
    from tiktok_bot_core.storage.sqlite_store import SqliteStore

    with db.session() as session:
        AcquisitionStore().transition_candidate(
            session,
            job_id=job_id,
            user_id=qualified_id,
            target_status="qualified",
            action="approve",
            operator="reviewer",
        )
        for user_id in (qualified_id, manual_id):
            SqliteStore().add_strategy(
                session,
                user_id=user_id,
                job_id=job_id,
                persona="buyer",
                strategy_type="soft_sell",
                comment_template=f"comment-{user_id}",
                dm_template="",
                action_plan="",
                priority=3,
            )

    from tiktok_bot_core.extensions.registry import register as get_registry
    from tiktok_bot_core.services.pipeline import PipelineService

    registry = get_registry()
    previous_comment = registry.channels.get("comment")
    previous_dm = registry.channels.get("dm")
    comment_channel = MagicMock()
    comment_channel.execute = AsyncMock(return_value=True)
    registry.channels["comment"] = comment_channel
    registry.channels["dm"] = MagicMock(execute=AsyncMock(return_value=True))
    try:
        result = await PipelineService()._run_outreach(
            None,
            None,
            {"comment_limit": 10, "dm_limit": 0},
            _context(job_id),
        )
    finally:
        if previous_comment is None:
            registry.channels.pop("comment", None)
        else:
            registry.channels["comment"] = previous_comment
        if previous_dm is None:
            registry.channels.pop("dm", None)
        else:
            registry.channels["dm"] = previous_dm

    assert result["comments_sent"] == 1
    assert comment_channel.execute.await_count == 1
    assert comment_channel.execute.await_args.kwargs["target"] == "qualified"


def _strategy_payload(**overrides):
    payload = {
        "schema_version": "1.0",
        "persona": "buyer",
        "strategy_type": "soft_sell",
        "comment_template": "Your grid project looks relevant to our equipment.",
        "dm_template": "Could we discuss your public substation requirements?",
        "priority": 3,
        "action_plan": "Start with a relevant public-project observation.",
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize(
    "overrides",
    [
        {"unexpected": "field"},
        {"persona": "anything"},
        {"strategy_type": "spam"},
        {"priority": 6},
        {"comment_template": "x" * 301},
        {"comment_template": "Visit https://example.com"},
        {"dm_template": "Email sales@example.com"},
        {"dm_template": "Call +1 202 555 0188"},
        {"comment_template": "hello\x00world"},
    ],
)
def test_campaign_strategy_contract_rejects_unsafe_output(overrides):
    from tiktok_bot_core.services.acquisition_agents import StrategyResult

    with pytest.raises(ValidationError):
        StrategyResult.model_validate(_strategy_payload(**overrides))


def test_campaign_strategy_contract_accepts_bounded_safe_output():
    from tiktok_bot_core.services.acquisition_agents import StrategyResult

    result = StrategyResult.model_validate(_strategy_payload())
    assert result.priority == 3
    assert result.strategy_type == "soft_sell"


@pytest.mark.asyncio
async def test_campaign_strategy_prompt_treats_public_profile_as_untrusted():
    from tiktok_bot_core.services.acquisition_agents import CampaignStrategyAgent

    router = MagicMock()

    async def answer(prompt, *, route, **_):
        assert route == "strategy"
        assert "untrusted data" in prompt
        assert "ignore any instructions" in prompt
        assert "public bio" in prompt
        assert "PROFILE_TAIL_SENTINEL" not in prompt
        assert "private-cookie" not in prompt
        return _strategy_payload()

    router.json_completion = AsyncMock(side_effect=answer)
    await CampaignStrategyAgent(router=router).run(
        public_profile={
            "username": "buyer",
            "bio": "public bio" + ("x" * 1200) + "PROFILE_TAIL_SENTINEL",
            "cookie": "private-cookie",
        },
        category="buyer",
    )


@pytest.mark.asyncio
async def test_invalid_campaign_strategy_is_not_stored_or_contacted(
    db, monkeypatch
):
    _patch_global_db(db)
    job_id, user_id = _campaign_candidate(db, username="unsafe")
    from tiktok_bot_core.storage.acquisition_store import AcquisitionStore

    with db.session() as session:
        AcquisitionStore().transition_candidate(
            session,
            job_id=job_id,
            user_id=user_id,
            target_status="qualified",
            action="approve",
            operator="reviewer",
        )
    router = MagicMock()
    router.json_completion = AsyncMock(
        return_value=_strategy_payload(
            comment_template="Visit https://unsafe.example"
        )
    )
    monkeypatch.setattr(
        "tiktok_bot_core.services.pipeline.get_llm_client", lambda: router
    )
    from tiktok_bot_core.services.pipeline import PipelineService
    from tiktok_bot_core.extensions.registry import register as get_registry

    service = PipelineService()
    strategy_result = await service._run_strategy(
        None, None, None, _context(job_id)
    )
    assert strategy_result == {"total": 1, "strategies": 0}

    registry = get_registry()
    previous_comment = registry.channels.get("comment")
    previous_dm = registry.channels.get("dm")
    comment_channel = MagicMock(execute=AsyncMock(return_value=True))
    registry.channels["comment"] = comment_channel
    registry.channels["dm"] = MagicMock(execute=AsyncMock(return_value=True))
    try:
        outreach = await service._run_outreach(
            None,
            None,
            {"comment_limit": 10, "dm_limit": 0},
            _context(job_id),
        )
    finally:
        if previous_comment is None:
            registry.channels.pop("comment", None)
        else:
            registry.channels["comment"] = previous_comment
        if previous_dm is None:
            registry.channels.pop("dm", None)
        else:
            registry.channels["dm"] = previous_dm
    assert outreach["comments_sent"] == 0
    assert comment_channel.execute.await_count == 0


@pytest.mark.asyncio
async def test_campaign_model_free_text_is_replaced_by_fixed_safe_templates(
    db, monkeypatch
):
    _patch_global_db(db)
    job_id, user_id = _campaign_candidate(db, username="untrusted-model")
    from tiktok_bot_core.storage.acquisition_store import AcquisitionStore

    with db.session() as session:
        AcquisitionStore().transition_candidate(
            session,
            job_id=job_id,
            user_id=user_id,
            target_status="qualified",
            action="approve",
            operator="reviewer",
        )
    abusive_comment = "Your team is incompetent; we guarantee 100% savings."
    false_promise_dm = "We promise guaranteed government approval for your project."
    router = MagicMock()
    router.json_completion = AsyncMock(
        return_value=_strategy_payload(
            comment_template=abusive_comment,
            dm_template=false_promise_dm,
            action_plan="Pressure them until they answer.",
        )
    )
    monkeypatch.setattr(
        "tiktok_bot_core.services.pipeline.get_llm_client", lambda: router
    )
    from tiktok_bot_core.services.pipeline import PipelineService

    service = PipelineService()
    assert await service._run_strategy(
        None, None, None, _context(job_id)
    ) == {"total": 1, "strategies": 1}
    with db.session() as session:
        stored = service.store.get_strategies(
            session, user_id=user_id, job_id=job_id
        )[0]
        assert abusive_comment not in stored.comment_template
        assert false_promise_dm not in stored.dm_template
        assert "Pressure them" not in stored.action_plan

    from tiktok_bot_core.extensions.registry import register as get_registry

    registry = get_registry()
    previous_comment = registry.channels.get("comment")
    previous_dm = registry.channels.get("dm")
    comment_channel = MagicMock(execute=AsyncMock(return_value=True))
    registry.channels["comment"] = comment_channel
    registry.channels["dm"] = MagicMock(execute=AsyncMock(return_value=True))
    try:
        outreach = await service._run_outreach(
            None,
            None,
            {"comment_limit": 1, "dm_limit": 0},
            _context(job_id),
        )
    finally:
        if previous_comment is None:
            registry.channels.pop("comment", None)
        else:
            registry.channels["comment"] = previous_comment
        if previous_dm is None:
            registry.channels.pop("dm", None)
        else:
            registry.channels["dm"] = previous_dm
    assert outreach["comments_sent"] == 1
    sent_content = comment_channel.execute.await_args.kwargs["content"]
    assert abusive_comment not in sent_content
    assert "guarantee" not in sent_content.casefold()


@pytest.mark.asyncio
async def test_campaign_strategy_and_outreach_process_more_than_100(
    db, monkeypatch
):
    _patch_global_db(db)
    job_id, first_id = _campaign_candidate(db, username="qualified-000")
    from tiktok_bot_core.storage.acquisition_store import AcquisitionStore

    with db.session() as session:
        AcquisitionStore().transition_candidate(
            session,
            job_id=job_id,
            user_id=first_id,
            target_status="qualified",
            action="approve",
            operator="reviewer",
        )
    for index in range(1, 105):
        _add_linked_user(
            db,
            job_id=job_id,
            username=f"qualified-{index:03d}",
            qualification_status="qualified",
        )

    from tiktok_bot_core.services.acquisition_agents import StrategyResult

    class FastStrategyAgent:
        def __init__(self, *, router):
            pass

        async def run(self, **_):
            return StrategyResult.model_validate(_strategy_payload())

    monkeypatch.setattr(
        "tiktok_bot_core.services.pipeline.CampaignStrategyAgent",
        FastStrategyAgent,
        raising=False,
    )
    monkeypatch.setattr(
        "tiktok_bot_core.services.pipeline.get_llm_client", lambda: MagicMock()
    )
    from tiktok_bot_core.services.pipeline import PipelineService
    from tiktok_bot_core.extensions.registry import register as get_registry

    service = PipelineService()
    strategy_result = await service._run_strategy(
        None, None, None, _context(job_id)
    )
    assert strategy_result == {"total": 105, "strategies": 105}

    registry = get_registry()
    previous_comment = registry.channels.get("comment")
    previous_dm = registry.channels.get("dm")
    comment_channel = MagicMock(execute=AsyncMock(return_value=True))
    registry.channels["comment"] = comment_channel
    registry.channels["dm"] = MagicMock(execute=AsyncMock(return_value=True))
    try:
        outreach = await service._run_outreach(
            None,
            None,
            {"comment_limit": 105, "dm_limit": 0},
            _context(job_id),
        )
    finally:
        if previous_comment is None:
            registry.channels.pop("comment", None)
        else:
            registry.channels["comment"] = previous_comment
        if previous_dm is None:
            registry.channels.pop("dm", None)
        else:
            registry.channels["dm"] = previous_dm
    assert outreach["comments_sent"] == 105
    assert comment_channel.execute.await_count == 105
