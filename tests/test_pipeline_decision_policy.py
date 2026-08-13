import asyncio
from pathlib import Path
import tempfile

import pytest

from tiktok_bot_core.models.entities import PipelineJobUser, Strategy, User
from tiktok_bot_core.services.pipeline_decision_policy import (
    DecisionPolicyCapabilities,
    PipelineDecisionPolicy,
)
from tiktok_bot_core.services.pipeline import (
    _collection_decision_summary,
    _stable_stage_error_code,
)
from tiktok_bot_core.storage.acquisition_store import AcquisitionStore
from tiktok_bot_core.storage.database import Database
from tiktok_bot_core.storage.pipeline_job_store import PipelineJobStore


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


def _seed_job(
    database: Database,
    *,
    stages=("collect", "filter", "strategy", "outreach", "report"),
    acquisition: bool = True,
) -> str:
    with database.session() as session:
        job = PipelineJobStore().create_job(
            session,
            platform="douyin",
            account_mode="auto",
            account_id=None,
            stages=stages,
            config_snapshot={"businessMode": "ai_acquisition"},
        )
        if acquisition:
            AcquisitionStore().create_campaign(
                session,
                job_id=job.id,
                platform="douyin",
                countries=["CN"],
                industries=["power"],
                customer_roles=["buyer"],
                search_budget={
                    "maxPages": 10,
                    "maxLlmCalls": 20,
                    "maxDurationMinutes": 30,
                },
            )
        return job.id


def _seed_candidate(
    database: Database,
    job_id: str,
    *,
    suffix: str,
    qualification_status: str,
) -> int:
    with database.session() as session:
        user = User(
            platform="douyin",
            tiktok_id=f"policy-{suffix}",
            username=f"policy-{suffix}",
        )
        session.add(user)
        session.flush()
        link = PipelineJobUser(
                job_id=job_id,
                user_id=user.id,
                source_stage="collect",
                status=(
                    "qualified"
                    if qualification_status == "qualified"
                    else "pending"
                ),
            )
        session.add(link)
        session.flush()
        # Some legacy SQLite defaults normalize a newly inserted pending link
        # to manual_review.  Set the persisted AI state explicitly for this
        # policy fixture, matching the stage-02 update path.
        link.qualification_status = qualification_status
        session.flush()
        return user.id


def test_legacy_jobs_never_receive_ai_acquisition_decisions(db):
    job_id = _seed_job(db, acquisition=False)
    policy = PipelineDecisionPolicy(db)

    assert policy.after_stage(
        job_id=job_id,
        stage="collect",
        result={"candidate": 0, "needs_more_evidence": 0},
    ) is None
    assert policy.before_stage(job_id=job_id, stage="outreach") is None


@pytest.mark.parametrize(
    "result",
    [
        {"candidate": 0, "needs_more_evidence": 0},
        {"candidate": 1, "needs_more_evidence": 3},
        {
            "candidate": 3,
            "needs_more_evidence": 0,
            "truncation_reasons": ["max_pages"],
        },
    ],
)
def test_collect_insufficient_evidence_has_safe_default_and_control_actions(
    db, result
):
    job_id = _seed_job(db)
    plan = PipelineDecisionPolicy(db).after_stage(
        job_id=job_id,
        stage="collect",
        result=result,
    )

    assert plan is not None
    assert plan.kind == "insufficient_evidence"
    assert plan.default_option_key == "continue_with_current_evidence"
    assert plan.option_keys == (
        "continue_with_current_evidence",
        "skip_remaining_pipeline",
        "cancel_job",
    )


def test_collect_deepen_requires_both_authoritative_budget_and_executor(db):
    job_id = _seed_job(db)
    result = {
        "candidate": 0,
        "needs_more_evidence": 0,
        "remaining_budget": {
            "pages": 2,
            "llmCalls": 3,
            "durationSeconds": 60,
        },
    }

    without_executor = PipelineDecisionPolicy(db).after_stage(
        job_id=job_id,
        stage="collect",
        result=result,
    )
    assert without_executor is not None
    assert "deepen_with_remaining_budget" not in without_executor.option_keys
    with pytest.raises(ValueError, match="not implemented"):
        PipelineDecisionPolicy(
            db,
            capabilities=DecisionPolicyCapabilities(deepen_collect=True),
        )


def test_filter_gate_uses_persisted_pending_counts_and_real_capabilities(db):
    job_id = _seed_job(db)
    _seed_candidate(
        db,
        job_id,
        suffix="manual",
        qualification_status="manual_review",
    )
    _seed_candidate(
        db,
        job_id,
        suffix="enrich",
        qualification_status="need_enrichment",
    )

    ordinary = PipelineDecisionPolicy(db).after_stage(
        job_id=job_id,
        stage="filter",
        result={"manual_review": 999, "need_enrichment": 999},
    )
    assert ordinary is not None
    assert ordinary.default_option_key == "continue_with_qualified_only"
    assert ordinary.context["candidateCounts"] == {
        "manualReview": 1,
        "needEnrichment": 1,
        "qualified": 0,
    }
    assert ordinary.option_keys == (
        "open_review_workbench",
        "continue_with_qualified_only",
    )
    with pytest.raises(ValueError, match="not implemented"):
        PipelineDecisionPolicy(
            db,
            capabilities=DecisionPolicyCapabilities(batch_enrichment=True),
        )


def test_account_recovery_capability_cannot_publish_unimplemented_action(db):
    with pytest.raises(ValueError, match="not implemented"):
        PipelineDecisionPolicy(
            db,
            capabilities=DecisionPolicyCapabilities(account_recovery=True),
        )


def test_filter_without_persisted_review_work_has_no_gate(db):
    job_id = _seed_job(db)
    _seed_candidate(
        db,
        job_id,
        suffix="qualified",
        qualification_status="qualified",
    )

    assert PipelineDecisionPolicy(db).after_stage(
        job_id=job_id,
        stage="filter",
        result={"manual_review": 50},
    ) is None


def test_outreach_gate_is_job_platform_and_authorization_scoped(db):
    job_id = _seed_job(db)
    user_id = _seed_candidate(
        db,
        job_id,
        suffix="outreach",
        qualification_status="qualified",
    )
    with db.session() as session:
        session.add(
            Strategy(
                job_id=job_id,
                user_id=user_id,
                persona="buyer",
                strategy_type="soft_sell",
                comment_template="欢迎交流行业需求。",
                dm_template="如方便，可进一步沟通需求。",
                action_plan="先评论，后私信。",
                priority=1,
            )
        )
    invalid_user_id = _seed_candidate(
        db,
        job_id,
        suffix="invalid-strategy",
        qualification_status="qualified",
    )
    with db.session() as session:
        session.add(
            Strategy(
                job_id=job_id,
                user_id=invalid_user_id,
                persona="not-a-persona",
                strategy_type="soft_sell",
                comment_template="这条记录不应通过严格策略校验。",
                dm_template="",
                action_plan="invalid",
                priority=1,
            )
        )

    plan = PipelineDecisionPolicy(db).before_stage(
        job_id=job_id,
        stage="outreach",
    )

    assert plan is not None
    assert plan.kind == "strategy_review"
    assert plan.default_option_key == "skip_outreach"
    assert plan.option_keys == (
        "open_strategy_workbench",
        "approve_all_safe_drafts",
        "skip_outreach",
        "cancel_job",
    )
    assert plan.context["candidateCounts"] == {
        "qualified": 2,
        "drafts": 2,
        "safeDrafts": 1,
    }


def test_error_gates_accept_only_stable_classes_and_retry_once(db):
    job_id = _seed_job(db)
    policy = PipelineDecisionPolicy(db)

    first = policy.for_stage_error(
        job_id=job_id,
        stage="collect",
        error_code="network",
        retry_count=0,
    )
    second = policy.for_stage_error(
        job_id=job_id,
        stage="collect",
        error_code="network",
        retry_count=1,
    )
    blocked = policy.for_stage_error(
        job_id=job_id,
        stage="collect",
        error_code="account_blocked",
        retry_count=0,
    )

    assert first is not None
    assert first.kind == "retryable_failure"
    assert first.default_option_key == "retry_once"
    assert first.option_keys == ("retry_once", "skip_stage", "stop_job")
    assert second is not None
    assert second.default_option_key == "skip_stage"
    assert second.option_keys == ("skip_stage", "stop_job")
    assert blocked is not None
    assert blocked.kind == "account_blocked"
    assert blocked.default_option_key == "skip_stage"
    assert blocked.option_keys == ("skip_stage", "stop_job")
    assert policy.for_stage_error(
        job_id=job_id,
        stage="collect",
        error_code="raw exception text",
        retry_count=0,
    ) is None


def test_collect_decision_summary_uses_authoritative_budget_totals():
    summary = _collection_decision_summary(
        {
            "maxPages": 10,
            "maxLlmCalls": 20,
            "maxDurationMinutes": 2,
        },
        {
            "totals": {
                "pages": 7,
                "llm_calls": 15,
                "duration_seconds": 90,
            },
            "keywords": {
                "1": {
                    "truncation_reasons": ["max_comments_per_video"],
                    "exhaustion_reason": "max_pages",
                },
                "2": {"truncation_reasons": []},
            },
        },
    )

    assert summary == {
        "remaining_budget": {
            "pages": 3,
            "llmCalls": 5,
            "durationSeconds": 30.0,
        },
        "truncation_reasons": ["max_comments_per_video", "max_pages"],
    }


def test_stage_error_code_is_stable_and_never_parses_arbitrary_text():
    import httpx
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError

    class CategorizedError(RuntimeError):
        error_category = "network"

    class RawError(RuntimeError):
        pass

    assert _stable_stage_error_code(CategorizedError("private body")) == "network"
    assert _stable_stage_error_code(TimeoutError("private timeout")) == "timeout"
    assert _stable_stage_error_code(
        asyncio.TimeoutError("private asyncio timeout")
    ) == "timeout"
    assert _stable_stage_error_code(
        httpx.ConnectError(
            "private connect body",
            request=httpx.Request("GET", "https://example.test"),
        )
    ) == "network"
    assert _stable_stage_error_code(
        httpx.TimeoutException(
            "private timeout body",
            request=httpx.Request("GET", "https://example.test"),
        )
    ) == "timeout"
    assert _stable_stage_error_code(
        PlaywrightTimeoutError("private browser timeout")
    ) == "timeout"
    assert _stable_stage_error_code(
        RawError("network timeout account_blocked")
    ) == ""
