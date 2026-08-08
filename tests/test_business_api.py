"""HTTP contracts for the unified Hermes business read projection."""

from __future__ import annotations

from datetime import date as RealDate, datetime

import httpx
import pytest
from sqlalchemy import select

import tiktok_bot_api.main as api_main
from tiktok_bot_api.main import app
from tiktok_bot_core.models.entities import (
    AcquisitionCampaign,
    AcquisitionKeyword,
    DailyReport,
    DiscoveryEvidence,
    Message,
    PipelineJob,
    PipelineJobUser,
    Reply,
    User,
)
from tiktok_bot_core.storage.database import Database


@pytest.fixture
def business_api_database(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'business-api.db'}")
    database.init()
    original_database = app.state.pipeline_database
    app.state.pipeline_database = database
    try:
        yield database
    finally:
        app.state.pipeline_database = original_database
        database.engine.dispose()


def api_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )


def add_user(
    session,
    identity: str,
    *,
    status: str = "pending",
    category: str = "unknown",
    source: str = "keyword_search",
    source_keyword: str = "",
    country: str = "VN",
    bio: str = "Power infrastructure buyer",
    follower_count: int = 100,
    created_at: datetime | None = None,
) -> User:
    user = User(
        platform="tiktok",
        tiktok_id=f"tiktok:{identity}",
        username=identity,
        nickname=f"{identity} public name",
        bio=bio,
        follower_count=follower_count,
        video_count=7,
        country=country,
        category=category,
        status=status,
        source=source,
        source_keyword=source_keyword,
        profile_url=f"https://www.tiktok.com/@{identity}",
        created_at=created_at or datetime(2026, 8, 4, 8, 0),
        updated_at=datetime(2026, 8, 4, 9, 0),
    )
    session.add(user)
    session.flush()
    return user


def add_campaign_candidate(
    session,
    user: User,
    *,
    job_id: str,
    qualification_status: str,
    category: str = "buyer",
    match_score: float | None = 88,
    confidence_score: float | None = 76,
    labels: list[str] | None = None,
    keyword: str = "grid project",
    stale_candidate_count: int = 0,
    stale_qualified_count: int = 0,
) -> AcquisitionKeyword:
    job = PipelineJob(
        id=job_id,
        platform="tiktok",
        stages_json=["collect", "filter"],
        created_at=datetime(2026, 8, 4, 10, 0),
    )
    session.add(job)
    session.flush()
    session.add(
        AcquisitionCampaign(
            job_id=job.id,
            platform="tiktok",
            countries=[user.country],
            industries=["power infrastructure"],
        )
    )
    legacy_status = (
        qualification_status
        if qualification_status in {"qualified", "rejected"}
        else "pending"
    )
    session.add(
        PipelineJobUser(
            job_id=job.id,
            user_id=user.id,
            source_stage="collect",
            status=legacy_status,
            qualification_status=qualification_status,
            category=category,
            match_score=match_score,
            confidence_score=confidence_score,
            labels_json=labels or ["采购方"],
        )
    )
    acquisition_keyword = AcquisitionKeyword(
        job_id=job.id,
        platform="tiktok",
        text=keyword,
        language="en",
        candidate_count=stale_candidate_count,
        qualified_count=stale_qualified_count,
    )
    session.add(acquisition_keyword)
    session.flush()
    session.add(
        DiscoveryEvidence(
            job_id=job.id,
            user_id=user.id,
            keyword_id=acquisition_keyword.id,
            source_type="video_comment",
            keyword_text=keyword,
            raw_text="Need a transformer quote",
        )
    )
    return acquisition_keyword


def add_sent_message(
    session,
    user: User,
    *,
    message_type: str = "dm",
    sent_at: datetime | None = None,
) -> Message:
    message = Message(
        user_id=user.id,
        message_type=message_type,
        content="Hello",
        status="sent",
        sent_at=sent_at or datetime(2026, 8, 4, 11, 0),
        created_at=sent_at or datetime(2026, 8, 4, 11, 0),
    )
    session.add(message)
    session.flush()
    return message


@pytest.mark.asyncio
async def test_users_and_stats_surface_latest_ai_projection(
    business_api_database,
):
    with business_api_database.session() as session:
        user = add_user(
            session,
            "projected-buyer",
            status="rejected",
            category="peer",
            source_keyword="legacy source",
        )
        add_campaign_candidate(
            session,
            user,
            job_id="users-job",
            qualification_status="qualified",
            match_score=91,
            confidence_score=83,
            labels=["采购方", "高意向"],
            keyword="substation contractor",
        )
        user_id = user.id

    async with api_client() as client:
        listed = await client.get(
            "/api/users",
            params={"status": "qualified", "category": "buyer"},
        )
        stats = await client.get("/api/users/stats")

    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    item = listed.json()["items"][0]
    assert item["id"] == user_id
    assert item["status"] == "qualified"
    assert item["category"] == "buyer"
    assert item["business_source"] == "ai_acquisition"
    assert item["source_job_id"] == "users-job"
    assert item["qualification_status"] == "qualified"
    assert item["match_score"] == 91
    assert item["confidence_score"] == 83
    assert item["labels"] == ["采购方", "高意向"]
    assert item["updated_at"] == "2026-08-04T09:00:00"
    assert stats.json()["total"] == 1
    assert stats.json()["qualified"] == 1
    assert stats.json()["by_persona"]["buyer"] == 1

    with business_api_database.session() as session:
        authoritative = session.scalar(select(User).where(User.id == user_id))
        assert authoritative is not None
        assert authoritative.status == "rejected"
        assert authoritative.category == "peer"
        assert authoritative.source_keyword == "legacy source"


@pytest.mark.asyncio
async def test_users_reject_invalid_pagination_bounds(
    business_api_database,
):
    with business_api_database.session() as session:
        add_user(session, "pagination-user")

    async with api_client() as client:
        zero_limit = await client.get("/api/users", params={"limit": 0})
        excessive_limit = await client.get(
            "/api/users", params={"limit": 501}
        )
        negative_offset = await client.get(
            "/api/users", params={"offset": -1}
        )
        valid = await client.get(
            "/api/users", params={"limit": 1, "offset": 0}
        )

    assert zero_limit.status_code == 422
    assert excessive_limit.status_code == 422
    assert negative_offset.status_code == 422
    assert valid.status_code == 200
    assert valid.json()["total"] == 1
    assert len(valid.json()["items"]) == 1


@pytest.mark.asyncio
async def test_dashboard_keyword_and_persona_metrics_include_acquisition_and_legacy_data(
    business_api_database,
):
    with business_api_database.session() as session:
        ai_user = add_user(
            session,
            "ai-keyword-user",
            status="qualified",
            category="peer",
            source_keyword="grid project",
        )
        ai_keyword = add_campaign_candidate(
            session,
            ai_user,
            job_id="keyword-job",
            qualification_status="qualified",
            category="buyer",
            keyword="grid project",
            stale_candidate_count=999,
            stale_qualified_count=998,
        )
        # The same user and keyword are present in AI + legacy sources and have
        # duplicate evidence.  They must still count as one distinct user.
        session.add(
            DiscoveryEvidence(
                job_id="keyword-job",
                user_id=ai_user.id,
                keyword_id=ai_keyword.id,
                source_type="author_profile",
                keyword_text="grid project",
            )
        )
        add_user(
            session,
            "legacy-keyword-user",
            status="pending",
            category="distributor",
            source_keyword="grid project",
        )

    async with api_client() as client:
        response = await client.get("/api/stats/dashboard")

    assert response.status_code == 200
    data = response.json()
    keyword = next(row for row in data["keywords"] if row["name"] == "grid project")
    assert keyword == {
        "name": "grid project",
        "keyword": "grid project",
        "total": 2,
        "converted": 1,
        "rate": 0.5,
    }
    assert data["overview"]["total_users"] == 2
    assert data["overview"]["qualified_users"] == 1
    categories = {row["category"]: row["count"] for row in data["categories"]}
    assert categories["buyer"] == 1
    assert categories["distributor"] == 1


@pytest.mark.asyncio
async def test_wordcloud_uses_word_count_contract_and_honors_limit(
    business_api_database,
):
    with business_api_database.session() as session:
        add_user(
            session,
            "keyword-a-1",
            source_keyword="keyword a",
        )
        add_user(
            session,
            "keyword-a-2",
            source_keyword="keyword a",
        )
        add_user(
            session,
            "keyword-b",
            source_keyword="keyword b",
        )

    async with api_client() as client:
        response = await client.get(
            "/api/stats/wordcloud",
            params={"lang": "cn", "limit": 1},
        )

    assert response.status_code == 200
    assert response.json() == [{"word": "keyword a", "count": 2}]


@pytest.mark.asyncio
async def test_lead_search_uses_ai_match_score_and_current_evidence_keyword(
    business_api_database,
):
    with business_api_database.session() as session:
        user = add_user(
            session,
            "evidence-lead",
            status="rejected",
            category="peer",
            source_keyword="legacy unrelated",
            bio="Public infrastructure company",
        )
        add_campaign_candidate(
            session,
            user,
            job_id="lead-job",
            qualification_status="manual_review",
            category="buyer",
            match_score=87.6,
            confidence_score=72,
            keyword="transformer tender",
        )

    async with api_client() as client:
        response = await client.get(
            "/api/leads/search",
            params={"keyword": "tender", "limit": 20},
        )

    assert response.status_code == 200
    assert len(response.json()) == 1
    lead = response.json()[0]
    assert lead["username"] == "evidence-lead"
    assert lead["relevance_score"] == 88
    assert lead["matched_keyword"] == "transformer tender"
    assert lead["source_job_id"] == "lead-job"
    assert lead["qualification_status"] == "manual_review"
    assert lead["confidence_score"] == 72


@pytest.mark.asyncio
async def test_public_lead_search_rejects_blank_keyword_and_zero_limit(
    business_api_database,
):
    with business_api_database.session() as session:
        add_user(session, "must-not-be-enumerated")

    async with api_client() as client:
        public_search = await client.get(
            "/api/leads/search",
            params={"keyword": "buyer"},
        )
        blank = await client.get(
            "/api/leads/search",
            params={"keyword": "   "},
        )
        zero_limit = await client.get(
            "/api/leads/search",
            params={"keyword": "buyer", "limit": 0},
        )

    assert public_search.status_code == 200
    assert [row["username"] for row in public_search.json()] == [
        "must-not-be-enumerated"
    ]
    assert blank.status_code == 422
    assert zero_limit.status_code == 422


@pytest.mark.asyncio
async def test_reports_overview_uses_real_funnel_region_and_sentiment_counts(
    business_api_database,
):
    with business_api_database.session() as session:
        qualified = add_user(
            session,
            "qualified",
            status="rejected",
            category="peer",
        )
        add_campaign_candidate(
            session,
            qualified,
            job_id="qualified-job",
            qualification_status="qualified",
        )
        contacted = add_user(session, "contacted", country="VN")
        add_sent_message(session, contacted)
        replied_positive = add_user(session, "reply-positive", country="VN")
        positive_message = add_sent_message(session, replied_positive)
        session.add(
            Reply(
                message_id=positive_message.id,
                user_id=replied_positive.id,
                reply_content="Please send a quote",
                sentiment="positive",
                is_business_intent=True,
                created_at=datetime(2026, 8, 4, 12, 0),
            )
        )
        # Multiple intent-bearing replies from one person remain one funnel
        # conversion, while region and sentiment reply metrics retain both
        # real reply rows.
        session.add(
            Reply(
                message_id=positive_message.id,
                user_id=replied_positive.id,
                reply_content="Also include delivery time",
                sentiment="positive",
                is_business_intent=True,
                created_at=datetime(2026, 8, 4, 12, 5),
            )
        )
        replied_negative = add_user(session, "reply-negative", country="US")
        negative_message = add_sent_message(session, replied_negative)
        add_sent_message(session, replied_negative, message_type="comment")
        session.add(
            Reply(
                message_id=negative_message.id,
                user_id=replied_negative.id,
                reply_content="No thanks",
                sentiment="negative",
                is_business_intent=False,
                created_at=datetime(2026, 8, 4, 12, 30),
            )
        )

    async with api_client() as client:
        response = await client.get("/api/reports/overview")

    assert response.status_code == 200
    data = response.json()
    funnel = {row["label"]: row["count"] for row in data["funnel"]}
    assert funnel == {
        "imported": 4,
        "qualified": 4,
        "contacted": 3,
        "replied": 2,
        "businessIntent": 1,
    }
    regions = {row["name"]: row for row in data["regions"]}
    assert regions["VN"]["replies"] == 2
    assert regions["VN"]["intent"] == 2
    assert regions["VN"]["rate"] == "50.0%"
    assert regions["US"]["replies"] == 1
    assert regions["US"]["rate"] == "50.0%"
    assert regions["VN"]["sharePct"] == 66.7
    assert data["sentiment"]["positive"]["count"] == 2
    assert data["sentiment"]["positive"]["pct"] == 67
    assert data["sentiment"]["neutral"]["count"] == 0
    assert data["sentiment"]["neutral"]["pct"] == 0
    assert data["sentiment"]["negative"]["count"] == 1
    assert data["sentiment"]["negative"]["pct"] == 33
    assert data["sentiment"]["avgScore"] == 0.33


@pytest.mark.asyncio
async def test_reports_overview_empty_sentiment_is_all_zero(
    business_api_database,
):
    async with api_client() as client:
        response = await client.get("/api/reports/overview")

    assert response.status_code == 200
    sentiment = response.json()["sentiment"]
    assert sentiment["positive"]["count"] == 0
    assert sentiment["positive"]["pct"] == 0
    assert sentiment["neutral"]["count"] == 0
    assert sentiment["neutral"]["pct"] == 0
    assert sentiment["negative"]["count"] == 0
    assert sentiment["negative"]["pct"] == 0
    assert sentiment["avgScore"] == 0


@pytest.mark.asyncio
async def test_daily_report_default_and_pipeline_overview_use_utc_day(
    business_api_database,
    monkeypatch,
):
    class FrozenUtcDateTime(datetime):
        @classmethod
        def utcnow(cls):
            return cls(2026, 8, 4, 16, 30)

    class FrozenLocalDate(RealDate):
        @classmethod
        def today(cls):
            return cls(2026, 8, 5)

    with business_api_database.session() as session:
        session.add_all(
            [
                DailyReport(
                    report_date=RealDate(2026, 8, 4),
                    new_users_found=4,
                ),
                DailyReport(
                    report_date=RealDate(2026, 8, 5),
                    new_users_found=5,
                ),
            ]
        )
        user = add_user(session, "utc-report-user")
        add_sent_message(
            session,
            user,
            message_type="comment",
            sent_at=datetime(2026, 8, 4, 12, 0),
        )
        add_sent_message(
            session,
            user,
            message_type="dm",
            sent_at=datetime(2026, 8, 4, 13, 0),
        )

    monkeypatch.setattr(api_main, "db", business_api_database)
    monkeypatch.setattr(api_main, "datetime", FrozenUtcDateTime)
    monkeypatch.setattr(api_main, "date", FrozenLocalDate)

    async with api_client() as client:
        default_report = await client.get("/api/reports/daily")
        explicit_local_day = await client.get(
            "/api/reports/daily", params={"d": "2026-08-05"}
        )
        trend = await client.get("/api/reports/trend", params={"days": 2})
        overview = await client.get("/api/pipeline/overview")

    assert default_report.status_code == 200
    assert default_report.json()["date"] == "2026-08-04"
    assert default_report.json()["new_users_found"] == 4
    assert explicit_local_day.status_code == 200
    assert explicit_local_day.json()["date"] == "2026-08-05"
    assert explicit_local_day.json()["new_users_found"] == 5
    assert trend.status_code == 200
    assert [item["date"] for item in trend.json()] == [
        "2026-08-05",
        "2026-08-04",
    ]
    assert [item["new_users"] for item in trend.json()] == [5, 4]
    assert overview.status_code == 200
    assert overview.json()["summary"]["commentsSent"] == "1"
    assert overview.json()["summary"]["dmsSent"] == "1"
