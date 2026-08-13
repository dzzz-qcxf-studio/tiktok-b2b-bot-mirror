"""Core contracts for the Stage 03 strategy review domain boundary."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select

from tiktok_bot_core.models.entities import (
    PipelineJob,
    PipelineJobUser,
    Strategy,
    StrategyReviewAudit,
    User,
)
from tiktok_bot_core.services.strategy_review import (
    StrategyReviewError,
    StrategyReviewService,
)
from tiktok_bot_core.storage.database import Database
from tiktok_bot_core.storage.strategy_review_store import StrategyReviewEdit


@pytest.fixture
def db(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'review-service.db'}")
    database.init()
    try:
        yield database
    finally:
        database.engine.dispose()


def _seed(
    db: Database,
    *,
    job_id: str,
    suffix: str,
    platform: str = "douyin",
    qualified: bool = True,
    legacy: bool = False,
) -> int:
    with db.session() as session:
        if session.get(PipelineJob, job_id) is None:
            session.add(PipelineJob(id=job_id, platform=platform, stages_json=[]))
        user = User(
            platform=platform,
            tiktok_id=f"{platform}:service:{suffix}",
            username=f"service-{suffix}",
        )
        session.add(user)
        session.flush()
        session.add(
            PipelineJobUser(
                job_id=job_id,
                user_id=user.id,
                source_stage="filter",
                status="qualified" if qualified else "manual_review",
                qualification_status="qualified" if qualified else "manual_review",
            )
        )
        strategy = Strategy(
            job_id=None if legacy else job_id,
            user_id=user.id,
            persona="buyer",
            strategy_type="soft_sell",
            comment_template="A safe public business discussion.",
            dm_template="",
            action_plan="Ask about current requirements.",
            priority=3,
        )
        session.add(strategy)
        session.flush()
        return strategy.id


def _edit() -> StrategyReviewEdit:
    return StrategyReviewEdit(
        persona="buyer",
        strategy_type="partnership",
        comment_template="A neutral partnership discussion.",
        dm_template="",
        action_plan="Confirm public business needs first.",
        priority=2,
    )


def test_edit_approve_reject_write_bounded_safe_audits_atomically(db):
    approved_id = _seed(db, job_id="job-success", suffix="approved")
    rejected_id = _seed(db, job_id="job-success", suffix="rejected")
    service = StrategyReviewService(db)

    edited = service.edit(
        job_id="job-success",
        platform="douyin",
        strategy_id=approved_id,
        expected_version=0,
        changes=_edit(),
        operator="reviewer-01",
    )
    approved = service.approve(
        job_id="job-success",
        platform="douyin",
        strategy_id=approved_id,
        expected_version=edited.current.review_version,
        operator="reviewer-01",
    )
    rejected = service.reject(
        job_id="job-success",
        platform="douyin",
        strategy_id=rejected_id,
        expected_version=0,
        operator="reviewer-02",
        reason="Not aligned with the current campaign.",
    )

    assert approved.current.review_status == "approved"
    assert rejected.current.review_status == "rejected"
    with db.session() as session:
        rows = session.scalars(
            select(StrategyReviewAudit).order_by(StrategyReviewAudit.id)
        ).all()
        audits = [
            (
                item.action,
                item.before_status,
                item.after_status,
                item.before_version,
                item.after_version,
                item.reason,
            )
            for item in rows
        ]
    assert [item[0] for item in audits] == ["edit", "approve", "reject"]
    assert [(item[1], item[2]) for item in audits] == [
        ("draft", "draft"),
        ("draft", "approved"),
        ("draft", "rejected"),
    ]
    assert [(item[3], item[4]) for item in audits] == [
        (0, 1),
        (1, 2),
        (0, 1),
    ]
    assert audits[-1][5] == "Not aligned with the current campaign."
    assert set(StrategyReviewAudit.__table__.columns.keys()) == {
        "id", "job_id", "strategy_id", "user_id", "action",
        "before_status", "after_status", "before_version", "after_version",
        "operator", "reason", "created_at",
    }


def test_service_rejects_all_scope_and_input_failures_without_echoing_values(db):
    valid_id = _seed(db, job_id="job-a", suffix="valid")
    foreign_id = _seed(db, job_id="job-b", suffix="foreign")
    unqualified_id = _seed(
        db, job_id="job-a", suffix="unqualified", qualified=False
    )
    legacy_id = _seed(db, job_id="job-a", suffix="legacy", legacy=True)
    service = StrategyReviewService(db)
    secret = "sk-private-do-not-echo"

    calls = [
        ("job_not_found", lambda: service.approve(job_id="missing", platform="douyin", strategy_id=valid_id, expected_version=0, operator="reviewer")),
        ("strategy_not_found", lambda: service.approve(job_id="job-a", platform="douyin", strategy_id=999999, expected_version=0, operator="reviewer")),
        ("strategy_scope_mismatch", lambda: service.approve(job_id="job-a", platform="douyin", strategy_id=foreign_id, expected_version=0, operator="reviewer")),
        ("platform_mismatch", lambda: service.approve(job_id="job-a", platform="tiktok", strategy_id=valid_id, expected_version=0, operator="reviewer")),
        ("candidate_not_qualified", lambda: service.approve(job_id="job-a", platform="douyin", strategy_id=unqualified_id, expected_version=0, operator="reviewer")),
        ("legacy_strategy", lambda: service.approve(job_id="job-a", platform="douyin", strategy_id=legacy_id, expected_version=0, operator="reviewer")),
        ("invalid_operator", lambda: service.approve(job_id="job-a", platform="douyin", strategy_id=valid_id, expected_version=0, operator=secret)),
        ("invalid_reason", lambda: service.reject(job_id="job-a", platform="douyin", strategy_id=valid_id, expected_version=0, operator="reviewer", reason=secret * 100)),
    ]
    for code, call in calls:
        with pytest.raises(StrategyReviewError) as caught:
            call()
        assert caught.value.code == code
        assert secret not in caught.value.public_message
        assert secret not in str(caught.value)

    with db.session() as session:
        assert session.scalar(select(StrategyReviewAudit)) is None


def test_batch_isolates_invalid_items_and_cas_has_one_authoritative_winner(db):
    winner_id = _seed(db, job_id="job-batch", suffix="winner")
    safe_id = _seed(db, job_id="job-batch", suffix="safe")
    unqualified_id = _seed(
        db, job_id="job-batch", suffix="skip", qualified=False
    )
    service = StrategyReviewService(db)

    def approve_winner():
        try:
            return service.approve(
                job_id="job-batch",
                platform="douyin",
                strategy_id=winner_id,
                expected_version=0,
                operator="reviewer",
            )
        except StrategyReviewError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        concurrent = list(executor.map(lambda _: approve_winner(), range(2)))
    assert sum(not isinstance(item, StrategyReviewError) for item in concurrent) == 1
    conflict = next(item for item in concurrent if isinstance(item, StrategyReviewError))
    assert conflict.code == "strategy_conflict"
    assert conflict.current.review_status == "approved"
    assert conflict.current.review_version == 1

    batch = service.approve_batch(
        job_id="job-batch",
        platform="douyin",
        expected_versions={winner_id: 0, safe_id: 0, unqualified_id: 0, 999999: 0},
        operator="reviewer",
    )
    assert (batch.total, batch.approved, batch.skipped, batch.conflicted) == (4, 1, 2, 1)
    with db.session() as session:
        audits = session.scalars(select(StrategyReviewAudit)).all()
        assert len(audits) == 2
        assert {item.strategy_id for item in audits} == {winner_id, safe_id}
