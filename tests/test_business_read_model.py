"""Tests for the unified, read-only business user projection."""

from __future__ import annotations

import gc
import tempfile
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from tiktok_bot_core.models.entities import (
    AcquisitionCampaign,
    DiscoveryEvidence,
    Message,
    PipelineJob,
    PipelineJobUser,
    Reply,
    User,
)
from tiktok_bot_core.services.business_read_model import BusinessReadModel
from tiktok_bot_core.storage.database import Database


@pytest.fixture
def db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as file:
        path = Path(file.name)
    database = Database(f"sqlite:///{path}")
    database.init()
    yield database
    database.engine.dispose()
    gc.collect()
    try:
        path.unlink()
    except PermissionError:
        pass


def _add_user(
    session,
    identity: str,
    *,
    status: str = "pending",
    category: str = "unknown",
    source_keyword: str = "legacy-keyword",
    created_at: datetime | None = None,
) -> User:
    user = User(
        platform="tiktok",
        tiktok_id=f"tiktok:{identity}",
        username=identity,
        nickname=f"{identity} nickname",
        bio=f"{identity} bio",
        follower_count=123,
        following_count=45,
        like_count=678,
        video_count=9,
        country="VN",
        category=category,
        status=status,
        source="keyword_search",
        source_keyword=source_keyword,
        profile_url=f"https://www.tiktok.com/@{identity}",
        created_at=created_at or datetime.utcnow(),
    )
    session.add(user)
    session.flush()
    return user


def _add_acquisition_link(
    session,
    user: User,
    *,
    job_id: str,
    created_at: datetime,
    qualification_status: str,
    category: str,
    match_score: float | None = None,
    confidence_score: float | None = None,
    labels: list[str] | None = None,
    with_campaign: bool = True,
) -> PipelineJob:
    job = PipelineJob(
        id=job_id,
        platform=user.platform,
        stages_json=["collect", "filter"],
        created_at=created_at,
    )
    session.add(job)
    session.flush()
    if with_campaign:
        session.add(
            AcquisitionCampaign(
                job_id=job.id,
                platform=user.platform,
                industries=["power infrastructure"],
            )
        )
    legacy_status = (
        qualification_status
        if qualification_status in {"qualified", "rejected"}
        else "pending"
    )
    link = PipelineJobUser(
        job_id=job.id,
        user_id=user.id,
        source_stage="collect",
        status=legacy_status,
        qualification_status=qualification_status,
        category=category,
        match_score=match_score,
        confidence_score=confidence_score,
        labels_json=labels or [],
    )
    session.add(link)
    session.flush()
    if qualification_status == "need_enrichment":
        # The compatibility INSERT trigger deliberately initializes legacy
        # pending rows as manual_review.  Production AI updates then perform
        # the explicit manual_review -> need_enrichment transition.
        from tiktok_bot_core.storage.pipeline_job_store import PipelineJobStore

        assert PipelineJobStore().update_ai_qualification(
            session,
            job.id,
            user.id,
            qualification_status="need_enrichment",
            match_score=match_score,
            confidence_score=confidence_score,
            category=category,
            expected_review_version=0,
            expected_qualification_status="manual_review",
        )
    return job


def test_latest_campaign_projection_wins_without_mutating_user(db):
    older = datetime(2026, 8, 1, 8, 0)
    newer = datetime(2026, 8, 2, 8, 0)
    with db.session() as session:
        user = _add_user(
            session,
            "latest-wins",
            category="peer",
            source_keyword="legacy-transformer",
        )
        older_job = _add_acquisition_link(
            session,
            user,
            job_id="older-job",
            created_at=older,
            qualification_status="qualified",
            category="distributor",
            match_score=95,
            confidence_score=92,
            labels=["old"],
        )
        newer_job = _add_acquisition_link(
            session,
            user,
            job_id="newer-job",
            created_at=newer,
            qualification_status="manual_review",
            category="buyer",
            match_score=81,
            confidence_score=74,
            labels=["buyer", "contractor"],
        )
        session.add_all(
            [
                DiscoveryEvidence(
                    job_id=older_job.id,
                    user_id=user.id,
                    source_type="video_comment",
                    keyword_text="old keyword",
                ),
                DiscoveryEvidence(
                    job_id=newer_job.id,
                    user_id=user.id,
                    source_type="video_comment",
                    keyword_text="new keyword",
                ),
            ]
        )
        user_id = user.id

    with db.session() as session:
        projection = BusinessReadModel().list_users(session)[0]
        persisted_user = session.get(User, user_id)

        assert projection.source_job_id == "newer-job"
        assert projection.business_source == "ai_acquisition"
        assert projection.status == "pending"
        assert projection.qualification_status == "manual_review"
        assert projection.category == "buyer"
        assert projection.match_score == 81
        assert projection.confidence_score == 74
        assert projection.labels == ("buyer", "contractor")
        assert projection.source_keyword == "new keyword"
        assert persisted_user is not None
        assert persisted_user.status == "pending"
        assert persisted_user.category == "peer"
        assert persisted_user.source_keyword == "legacy-transformer"


def test_engagement_status_overlays_qualification(db):
    with db.session() as session:
        contacted = _add_user(session, "contacted")
        replied = _add_user(session, "replied")
        contacted_job = _add_acquisition_link(
            session,
            contacted,
            job_id="contacted-job",
            created_at=datetime(2026, 8, 1),
            qualification_status="rejected",
            category="buyer",
        )
        replied_job = _add_acquisition_link(
            session,
            replied,
            job_id="replied-job",
            created_at=datetime(2026, 8, 1),
            qualification_status="rejected",
            category="buyer",
        )
        sent = Message(
            user_id=contacted.id,
            job_id=contacted_job.id,
            message_type="dm",
            content="Hello",
            status="sent",
        )
        replied_message = Message(
            user_id=replied.id,
            job_id=replied_job.id,
            message_type="dm",
            content="Hello",
            status="sent",
        )
        session.add_all([sent, replied_message])
        session.flush()
        session.add(
            Reply(
                message_id=replied_message.id,
                user_id=replied.id,
                reply_content="Please quote",
            )
        )

    with db.session() as session:
        projections = {
            item.username: item for item in BusinessReadModel().list_users(session)
        }
        assert projections["contacted"].status == "contacted"
        assert projections["contacted"].qualification_status == "rejected"
        assert projections["replied"].status == "replied"
        assert projections["replied"].qualification_status == "rejected"


@pytest.mark.parametrize(
    "raw_status", ["manual_review", "need_enrichment"]
)
def test_pending_qualification_maps_to_pending_but_keeps_raw_status(
    db, raw_status
):
    with db.session() as session:
        user = _add_user(session, raw_status, status="rejected")
        _add_acquisition_link(
            session,
            user,
            job_id=f"{raw_status}-job",
            created_at=datetime(2026, 8, 1),
            qualification_status=raw_status,
            category="buyer",
        )

    with db.session() as session:
        projection = BusinessReadModel().list_users(session)[0]
        assert projection.status == "pending"
        assert projection.qualification_status == raw_status


def test_legacy_user_falls_back_without_campaign(db):
    with db.session() as session:
        _add_user(
            session,
            "legacy-only",
            status="qualified",
            category="distributor",
            source_keyword="legacy-grid",
        )

    with db.session() as session:
        projection = BusinessReadModel().list_users(session)[0]
        assert projection.business_source == "legacy"
        assert projection.source_job_id is None
        assert projection.qualification_status is None
        assert projection.status == "qualified"
        assert projection.category == "distributor"
        assert projection.source_keyword == "legacy-grid"
        assert projection.labels == ()


def test_projection_filters_counts_statuses_and_personas_share_one_source(db):
    fixed_now = datetime(2026, 8, 4, 0, 30)
    yesterday = datetime(2026, 8, 3, 12, 0)
    old_day = datetime(2026, 7, 30, 12, 0)
    with db.session() as session:
        buyer = _add_user(
            session,
            "ai-buyer",
            status="rejected",
            category="peer",
            created_at=yesterday,
        )
        _add_acquisition_link(
            session,
            buyer,
            job_id="buyer-job",
            created_at=datetime(2026, 8, 1),
            qualification_status="qualified",
            category="buyer",
        )
        _add_user(
            session,
            "legacy-distributor",
            status="qualified",
            category="distributor",
            created_at=old_day,
        )
        pending = _add_user(
            session,
            "ai-pending",
            status="qualified",
            category="peer",
            created_at=datetime(2026, 8, 4, 0, 0),
        )
        _add_acquisition_link(
            session,
            pending,
            job_id="pending-job",
            created_at=datetime(2026, 8, 1),
            qualification_status="need_enrichment",
            category="manufacturer",
        )

    model = BusinessReadModel()
    with db.session() as session:
        qualified_buyers = model.list_users(
            session, status="qualified", category="buyer"
        )
        assert [item.username for item in qualified_buyers] == ["ai-buyer"]
        assert model.count_users(
            session, status="qualified", category="buyer"
        ) == len(qualified_buyers)
        assert model.count_users(session) == 3
        assert model.status_counts(session, now=fixed_now) == {
            "total": 3,
            "pending": 1,
            "qualified": 2,
            "contacted": 0,
            "replied": 0,
            "rejected": 0,
            "new_today": 1,
        }
        assert model.persona_counts(session) == {
            "distributor": 1,
            "buyer": 1,
            "peer": 0,
            "unknown": 1,
        }


def test_status_counts_uses_utc_day_boundary(db):
    fixed_now = datetime(2026, 8, 4, 0, 30)
    with db.session() as session:
        _add_user(
            session,
            "utc-today",
            created_at=datetime(2026, 8, 4, 0, 0),
        )
        _add_user(
            session,
            "utc-yesterday",
            created_at=datetime(2026, 8, 3, 23, 59, 59),
        )

    with db.session() as session:
        counts = BusinessReadModel().status_counts(session, now=fixed_now)
        assert counts["total"] == 2
        assert counts["new_today"] == 1


def test_same_created_at_uses_descending_job_id_as_stable_tiebreaker(db):
    same_time = datetime(2026, 8, 1, 8, 0)
    with db.session() as session:
        user = _add_user(session, "stable-tie")
        _add_acquisition_link(
            session,
            user,
            job_id="a-job",
            created_at=same_time,
            qualification_status="qualified",
            category="buyer",
        )
        _add_acquisition_link(
            session,
            user,
            job_id="z-job",
            created_at=same_time,
            qualification_status="rejected",
            category="peer",
        )

    with db.session() as session:
        projection = BusinessReadModel().list_users(session)[0]
        assert projection.source_job_id == "z-job"
        assert projection.qualification_status == "rejected"
        assert projection.status == "rejected"


def test_job_user_without_campaign_cannot_pollute_projection(db):
    with db.session() as session:
        user = _add_user(
            session,
            "legacy-with-unscoped-link",
            status="qualified",
            category="distributor",
        )
        _add_acquisition_link(
            session,
            user,
            job_id="not-an-acquisition-job",
            created_at=datetime(2026, 8, 3),
            qualification_status="rejected",
            category="peer",
            with_campaign=False,
        )
        user_id = user.id

    with db.session() as session:
        projection = BusinessReadModel().list_users(session)[0]
        persisted = session.scalar(select(User).where(User.id == user_id))
        assert projection.business_source == "legacy"
        assert projection.source_job_id is None
        assert projection.status == "qualified"
        assert projection.category == "distributor"
        assert persisted is not None and persisted.status == "qualified"
