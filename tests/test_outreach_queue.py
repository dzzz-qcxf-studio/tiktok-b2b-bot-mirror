"""Stage 04 persistent outreach queue contracts."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from tiktok_bot_core.models.entities import (
    Message,
    OutreachItem,
    PipelineJob,
    PipelineJobUser,
    Strategy,
    User,
)
from tiktok_bot_core.services.outreach_queue import (
    OutreachQueueError,
    OutreachQueueService,
)
from tiktok_bot_core.storage.database import Database


@pytest.fixture
def db(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'outreach-queue.db'}")
    database.init()
    try:
        yield database
    finally:
        database.engine.dispose()


def _seed_strategy(
    database: Database,
    *,
    job_id: str,
    suffix: str,
    platform: str = "douyin",
    user_platform: str | None = None,
    qualification_status: str = "qualified",
    review_status: str = "approved",
    review_version: int = 1,
    comment: str = "A safe professional discussion.",
    dm: str = "Hello. We welcome a professional B2B discussion.",
) -> int:
    with database.session() as session:
        if session.get(PipelineJob, job_id) is None:
            session.add(
                PipelineJob(
                    id=job_id,
                    platform=platform,
                    stages_json=[],
                    status="running",
                )
            )
        user = User(
            platform=user_platform or platform,
            tiktok_id=f"{platform}:outreach:{suffix}",
            username=f"outreach-{suffix}",
        )
        session.add(user)
        session.flush()
        session.add(
            PipelineJobUser(
                job_id=job_id,
                user_id=user.id,
                source_stage="filter",
                status=qualification_status,
                qualification_status=qualification_status,
            )
        )
        strategy = Strategy(
            job_id=job_id,
            user_id=user.id,
            persona="buyer",
            strategy_type="soft_sell",
            comment_template=comment,
            dm_template=dm,
            action_plan="Begin with a neutral project-related exchange.",
            priority=3,
            review_status=review_status,
            review_version=review_version,
        )
        session.add(strategy)
        session.flush()
        return strategy.id


def test_prepare_is_idempotent_and_enforces_job_platform_qualification_and_safety(db):
    eligible_id = _seed_strategy(db, job_id="job-main", suffix="eligible")
    _seed_strategy(
        db, job_id="job-main", suffix="draft", review_status="draft"
    )
    _seed_strategy(
        db,
        job_id="job-main",
        suffix="unqualified",
        qualification_status="manual_review",
    )
    _seed_strategy(
        db,
        job_id="job-main",
        suffix="wrong-platform",
        user_platform="tiktok",
    )
    _seed_strategy(
        db,
        job_id="job-main",
        suffix="unsafe",
        comment="Visit https://unsafe.example now",
        dm="",
    )
    _seed_strategy(db, job_id="job-other", suffix="other-job")
    service = OutreachQueueService(db)

    first = service.prepare(job_id="job-main", platform="douyin")
    second = service.prepare(job_id="job-main", platform="douyin")

    assert (first.created, first.total) == (2, 2)
    assert (second.created, second.total) == (0, 2)
    page = service.list_items(job_id="job-main", platform="douyin")
    assert page.total == 2
    assert {(item.strategy_id, item.channel) for item in page.items} == {
        (eligible_id, "comment"),
        (eligible_id, "dm"),
    }
    assert {item.status for item in page.items} == {"pending_approval"}
    assert {item.target_username for item in page.items} == {
        "outreach-eligible"
    }
    with db.session() as session:
        other_item = service.list_items(
            job_id="job-other", platform="douyin"
        )
        assert other_item.total == 0
    service.prepare(job_id="job-other", platform="douyin")
    with db.session() as session:
        other_strategy = session.scalar(
            select(Strategy).where(Strategy.job_id == "job-other")
        )
        session.get(User, other_strategy.user_id).username = "renamed-target"
    drifted = service.authorize(job_id="job-other", platform="douyin")
    assert (drifted.ready, drifted.skipped) == (0, 2)

    with db.session() as session:
        session.get(PipelineJob, "job-main").status = "cancelled"
    with pytest.raises(OutreachQueueError) as terminal_job:
        service.prepare(job_id="job-main", platform="douyin")
    assert terminal_job.value.code == "job_not_executable"


def test_authorize_skip_cancel_are_cas_scoped_and_revalidate_strategy_version(db):
    execute_id = _seed_strategy(
        db, job_id="job-execute", suffix="execute", dm=""
    )
    _seed_strategy(db, job_id="job-skip", suffix="skip", dm="")
    _seed_strategy(db, job_id="job-cancel", suffix="cancel", dm="")
    service = OutreachQueueService(db)
    for job_id in ("job-execute", "job-skip", "job-cancel"):
        service.prepare(job_id=job_id, platform="douyin")

    with db.session() as session:
        session.get(Strategy, execute_id).review_version += 1

    authorized = service.authorize(job_id="job-execute", platform="douyin")
    skipped = service.skip(job_id="job-skip", platform="douyin")
    cancelled = service.cancel(job_id="job-cancel", platform="douyin")

    assert (authorized.ready, authorized.skipped) == (0, 1)
    assert skipped.skipped == 1
    assert cancelled.cancelled == 1
    with db.session() as session:
        session.get(PipelineJob, "job-cancel").status = "cancelled"
        cancelled_strategy = session.scalar(
            select(Strategy).where(Strategy.job_id == "job-cancel")
        )
        cancelled_strategy.review_version += 1
        cancelled_strategy.comment_template = "Changed after cancellation."
    with pytest.raises(OutreachQueueError) as cancelled_job:
        service.prepare(job_id="job-cancel", platform="douyin")
    assert cancelled_job.value.code == "job_not_executable"
    assert service.list_items(
        job_id="job-cancel", platform="douyin"
    ).items[0].status == "cancelled"
    assert service.summary(job_id="job-execute", platform="douyin").by_status == {
        "skipped": 1
    }
    stale = service.list_items(job_id="job-execute", platform="douyin").items[0]
    assert stale.error_code == "strategy_changed"
    assert service.skip(job_id="job-skip", platform="douyin").skipped == 0
    with pytest.raises(OutreachQueueError) as exc_info:
        service.skip(
            job_id="job-skip",
            platform="douyin",
            error_code="raw browser exception: secret-cookie",
        )
    assert exc_info.value.code == "invalid_error_code"
    with pytest.raises(OutreachQueueError) as operator_error:
        service.authorize(
            job_id="job-execute",
            platform="douyin",
            operator="sk-secret-auth-token",
        )
    assert operator_error.value.code == "invalid_operator"

    with db.session() as session:
        changed = session.get(Strategy, execute_id)
        changed.comment_template = "A newly approved safe discussion."
        changed.review_status = "approved"
    refreshed = service.prepare(job_id="job-execute", platform="douyin")
    assert refreshed.created == 0
    refreshed_item = service.list_items(
        job_id="job-execute", platform="douyin"
    ).items[0]
    assert refreshed_item.status == "pending_approval"
    assert refreshed_item.content == "A newly approved safe discussion."


def test_two_sessions_claim_once_and_terminal_results_never_retry(db):
    _seed_strategy(db, job_id="job-claim", suffix="claim")
    service = OutreachQueueService(db)
    service.prepare(job_id="job-claim", platform="douyin")
    assert service.authorize(job_id="job-claim", platform="douyin").ready == 2

    first = service.claim_next(job_id="job-claim", platform="douyin")
    assert first is not None and first.status == "sending"
    # A second database Session cannot win the same ready-row CAS.
    with db.session() as second_session:
        same_row = service.store.transition(
            second_session,
            item_id=first.id,
            from_statuses=("ready",),
            to_status="sending",
        )
    assert same_row.applied is False
    with db.session() as session:
        wrong = Message(
            job_id="job-claim",
            user_id=first.user_id,
            message_type="dm" if first.channel == "comment" else "comment",
            content="wrong channel",
            status="sending",
        )
        session.add(wrong)
        session.flush()
        wrong_message_id = wrong.id
    with pytest.raises(OutreachQueueError) as wrong_message:
        service.finalize(
            job_id="job-claim",
            platform="douyin",
            item_id=first.id,
            outcome="sent",
            message_id=wrong_message_id,
        )
    assert wrong_message.value.code == "message_scope_mismatch"
    with db.session() as session:
        message = Message(
            job_id="job-claim",
            user_id=first.user_id,
            message_type=first.channel,
            content=first.content,
            status="sending",
        )
        session.add(message)
        session.flush()
        message_id = message.id
    sent = service.finalize(
        job_id="job-claim",
        platform="douyin",
        item_id=first.id,
        outcome="sent",
        message_id=message_id,
    )
    assert sent.applied is True
    assert sent.current is not None and sent.current.status == "sent"
    again = service.finalize(
        job_id="job-claim",
        platform="douyin",
        item_id=first.id,
        outcome="uncertain",
        error_code="channel_uncertain",
        message_id=None,
    )
    assert again.applied is False
    assert again.current is not None and again.current.status == "sent"
    second = service.claim_next(job_id="job-claim", platform="douyin")
    assert second is not None and second.id != first.id
    uncertain = service.finalize(
        job_id="job-claim",
        platform="douyin",
        item_id=second.id,
        outcome="uncertain",
        error_code="channel_uncertain",
        message_id=None,
    )
    assert uncertain.applied is True
    assert uncertain.current is not None
    assert uncertain.current.error_code == "channel_uncertain"
    assert service.claim_next(job_id="job-claim", platform="douyin") is None
    with db.session() as session:
        assert session.query(OutreachItem).count() == 2
