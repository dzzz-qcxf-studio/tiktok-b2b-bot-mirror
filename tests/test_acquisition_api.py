"""Stage 01/02 acquisition HTTP contract tests."""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime

import httpx
import pytest
from sqlalchemy import event, func, select, text

from tiktok_bot_api.auth import create_token
from tiktok_bot_api.main import app
from tiktok_bot_core.models.entities import (
    AcquisitionCampaign,
    AcquisitionKeyword,
    CandidateAssessment,
    DiscoveryEvidence,
    PipelineJob,
    PipelineJobUser,
    TikTokAccount,
    User,
)
from tiktok_bot_core.browser.providers import BrowserAvailability
from tiktok_bot_core.services.pipeline_jobs import PipelineJobService
from tiktok_bot_core.storage.acquisition_store import AcquisitionStore
from tiktok_bot_core.storage.database import Database


@pytest.fixture
def acquisition_database(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'acquisition-api.db'}")
    database.init()
    yield database
    database.engine.dispose()


@pytest.fixture
def acquisition_api(acquisition_database):
    original_database = app.state.pipeline_database
    app.state.pipeline_database = acquisition_database
    try:
        yield acquisition_database
    finally:
        app.state.pipeline_database = original_database


def api_client(*, authenticated: bool = True, username: str = "reviewer"):
    headers = {}
    if authenticated:
        headers["Authorization"] = f"Bearer {create_token(username)}"
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        headers=headers,
    )


def seed_job(database: Database, job_id: str, platform: str = "tiktok"):
    with database.session() as session:
        session.add(
            PipelineJob(
                id=job_id,
                platform=platform,
                stages_json=["collect", "filter"],
            )
        )


def seed_candidate(database: Database, job_id: str, username: str = "buyer-vn"):
    with database.session() as session:
        user = User(
            platform="tiktok",
            tiktok_id=f"tiktok:{job_id}:{username}",
            username=username,
            nickname="Vietnam Buyer",
            bio="Looking for transformer suppliers",
            follower_count=120,
            profile_url=f"https://www.tiktok.com/@{username}",
        )
        session.add(user)
        session.flush()
        session.add(
            PipelineJobUser(
                job_id=job_id,
                user_id=user.id,
                source_stage="collect",
                discovery_status="candidate",
                qualification_status="manual_review",
            )
        )
        return user.id


class _AvailableProvider:
    async def check_available(self, _account):
        return BrowserAvailability(True)


class _AvailableProviders:
    def get(self, _platform):
        return _AvailableProvider()


@pytest.mark.asyncio
async def test_all_acquisition_endpoints_require_authentication(acquisition_api):
    requests = [
        ("POST", "/api/acquisition/jobs", {}),
        ("GET", "/api/acquisition/jobs/job-1/campaign", None),
        ("POST", "/api/acquisition/jobs/job-1/campaign", {}),
        ("GET", "/api/acquisition/jobs/job-1/keywords", None),
        ("POST", "/api/acquisition/jobs/job-1/keywords", {}),
        ("PATCH", "/api/acquisition/jobs/job-1/keywords/1", {}),
        ("DELETE", "/api/acquisition/jobs/job-1/keywords/1", None),
        ("GET", "/api/acquisition/jobs/job-1/stage-01", None),
        ("GET", "/api/acquisition/jobs/job-1/stage-02", None),
        ("GET", "/api/acquisition/jobs/job-1/candidates", None),
        ("GET", "/api/acquisition/jobs/job-1/candidates/1", None),
        ("POST", "/api/acquisition/jobs/job-1/candidates/1/approve", {}),
        ("POST", "/api/acquisition/jobs/job-1/candidates/1/reject", {}),
        (
            "POST",
            "/api/acquisition/jobs/job-1/candidates/1/request-enrichment",
            {},
        ),
        (
            "POST",
            "/api/acquisition/jobs/job-1/candidates/1/complete-enrichment",
            {},
        ),
        ("PUT", "/api/acquisition/jobs/job-1/candidates/1/labels", {}),
        ("GET", "/api/acquisition/jobs/job-1/candidates/1/audits", None),
    ]
    async with api_client(authenticated=False) as client:
        responses = [
            await client.request(method, path, json=payload)
            for method, path, payload in requests
        ]
    assert [response.status_code for response in responses] == [401] * len(requests)


@pytest.mark.asyncio
async def test_atomic_acquisition_job_endpoint_requires_auth_and_returns_complete_contract(
    acquisition_api,
):
    with acquisition_api.session() as session:
        session.add(
            TikTokAccount(
                platform="douyin",
                username="api-atomic-account",
                status="logged_in",
            )
        )
    original_service = app.state.pipeline_job_service
    app.state.pipeline_job_service = PipelineJobService(
        database=acquisition_api,
        providers=_AvailableProviders(),
    )
    payload = {
        "platform": "douyin",
        "accountMode": "auto",
        "stages": ["collect", "filter", "report"],
        "configSnapshot": {
            "existing": "preserved",
            "businessMode": "legacy",
            "acquisitionSchemaVersion": "old",
        },
        "campaign": {
            "countries": ["CN"],
            "languages": ["zh-CN"],
            "industries": ["power infrastructure"],
            "customerRoles": ["contractor"],
            "hardConditions": {
                "requiredKeywords": ["substation"],
                "notListed": True,
            },
            "preferenceConditions": {
                "employeeCount": "10-20",
                "registeredCapital": "100w-1000w",
            },
            "searchBudget": {
                "maxKeywords": 7,
                "maxVideosPerKeyword": 9,
            },
            "keywordMix": {
                "effectivePercent": 55,
                "newPercent": 45,
            },
        },
        "keywords": [
            {
                "text": "power grid contractor",
                "language": "zh-CN",
                "keywordType": "industry",
            },
            {
                "text": "transformer procurement",
                "language": "zh-CN",
                "keywordType": "intent",
            },
        ],
    }
    try:
        async with api_client(authenticated=False) as client:
            unauthorized = await client.post(
                "/api/acquisition/jobs", json=payload
            )
        async with api_client() as client:
            response = await client.post("/api/acquisition/jobs", json=payload)
    finally:
        app.state.pipeline_job_service = original_service

    assert unauthorized.status_code == 401
    assert response.status_code == 202
    body = response.json()
    assert body["campaign"]["jobId"] == body["job"]["id"]
    assert [item["jobId"] for item in body["keywords"]] == [
        body["job"]["id"],
        body["job"]["id"],
    ]
    assert [stage["stage"] for stage in body["job"]["stages"]] == [
        "collect",
        "filter",
        "report",
    ]
    assert body["job"]["configSnapshot"] == {
        "existing": "preserved",
        "businessMode": "ai_acquisition",
        "acquisitionSchemaVersion": "1.0",
    }
    with acquisition_api.session() as session:
        campaign = session.scalar(
            select(AcquisitionCampaign).where(
                AcquisitionCampaign.job_id == body["job"]["id"]
            )
        )
        assert campaign.hard_conditions["requiredKeywords"] == [
            "substation"
        ]
        assert campaign.hard_conditions["notListed"] is True
        assert campaign.preference_conditions["employeeCount"] == "10-20"
        assert (
            campaign.preference_conditions["registeredCapital"]
            == "100w-1000w"
        )
        assert campaign.search_budget["maxKeywords"] == 7
        assert campaign.search_budget["maxVideosPerKeyword"] == 9
        assert campaign.keyword_mix == {
            "effectivePercent": 55,
            "newPercent": 45,
        }
        assert "max_keywords" not in campaign.search_budget
        assert "effective_percent" not in campaign.keyword_mix


@pytest.mark.asyncio
async def test_atomic_acquisition_job_request_requires_collect_and_rejects_duplicate_keywords(
    acquisition_api,
):
    base_payload = {
        "platform": "douyin",
        "accountMode": "auto",
        "stages": ["filter", "report"],
        "campaign": {"countries": ["CN"]},
        "keywords": [{"text": "power grid", "language": "zh-CN"}],
    }
    duplicate_payload = {
        **base_payload,
        "stages": ["collect", "filter"],
        "keywords": [
            {"text": "  Power   Grid ", "language": "ZH-cn"},
            {"text": "power grid", "language": "zh-CN"},
        ],
    }
    blank_payload = {
        **base_payload,
        "stages": ["collect", "filter"],
        "keywords": [{"text": "   ", "language": "zh-CN"}],
    }
    out_of_order_payload = {
        **base_payload,
        "stages": ["collect", "report", "filter"],
    }
    unknown_stage_payload = {
        **base_payload,
        "stages": ["collect", "unknown"],
    }
    async with api_client() as client:
        missing_collect = await client.post(
            "/api/acquisition/jobs", json=base_payload
        )
        duplicate = await client.post(
            "/api/acquisition/jobs", json=duplicate_payload
        )
        blank = await client.post(
            "/api/acquisition/jobs", json=blank_payload
        )
        out_of_order = await client.post(
            "/api/acquisition/jobs", json=out_of_order_payload
        )
        unknown_stage = await client.post(
            "/api/acquisition/jobs", json=unknown_stage_payload
        )

    assert missing_collect.status_code == 422
    assert duplicate.status_code == 422
    assert blank.status_code == 422
    assert out_of_order.status_code == 422
    assert unknown_stage.status_code == 422
    with acquisition_api.session() as session:
        assert session.scalar(select(func.count(PipelineJob.id))) == 0


@pytest.mark.parametrize(
    "sensitive_key",
    [
        "apiKey",
        "cookie",
        "token",
        "password",
        "authorization",
        "clientSecret",
        "privateKey",
        "credentials",
        "openai_api_key",
        "sessionCookie",
        "accessToken",
        "dbPassword",
        "authorizationHeader",
        "authHeader",
        "authValue",
        "oauthClientSecret",
        "signingPrivateKey",
        "serviceCredentials",
    ],
)
@pytest.mark.asyncio
async def test_atomic_acquisition_job_recursively_rejects_sensitive_config_keys(
    acquisition_api,
    sensitive_key,
):
    secret_value = "must-never-persist-or-echo"
    payload = {
        "platform": "douyin",
        "accountMode": "auto",
        "stages": ["collect", "filter"],
        "configSnapshot": {
            "ordinary": "preserved",
            "nested": [{"deeper": {sensitive_key: secret_value}}],
        },
        "campaign": {"countries": ["CN"]},
        "keywords": [{"text": "power grid", "language": "zh-CN"}],
    }

    original_service = app.state.pipeline_job_service
    app.state.pipeline_job_service = PipelineJobService(
        database=acquisition_api,
        providers=_AvailableProviders(),
    )
    try:
        async with api_client() as client:
            response = await client.post(
                "/api/acquisition/jobs", json=payload
            )
    finally:
        app.state.pipeline_job_service = original_service

    assert response.status_code == 422
    assert sensitive_key not in response.text
    assert secret_value not in response.text
    with acquisition_api.session() as session:
        assert session.scalar(select(func.count(PipelineJob.id))) == 0


@pytest.mark.asyncio
async def test_atomic_acquisition_job_rejects_nested_bearer_auth_without_echo(
    acquisition_api,
):
    secret_value = "nested-bearer-must-not-echo"
    payload = {
        "platform": "douyin",
        "accountMode": "auto",
        "stages": ["collect", "filter"],
        "configSnapshot": {
            "runtime": {
                "auth": {
                    "type": "bearer",
                    "value": secret_value,
                }
            }
        },
        "campaign": {"countries": ["CN"]},
        "keywords": [{"text": "power grid", "language": "zh-CN"}],
    }

    async with api_client() as client:
        response = await client.post("/api/acquisition/jobs", json=payload)

    assert response.status_code == 422
    assert "auth" not in response.text.casefold()
    assert secret_value not in response.text
    with acquisition_api.session() as session:
        assert session.scalar(select(func.count(PipelineJob.id))) == 0


@pytest.mark.asyncio
async def test_atomic_acquisition_job_allows_noncredential_business_config_names(
    acquisition_api,
):
    with acquisition_api.session() as session:
        session.add(
            TikTokAccount(
                platform="douyin",
                username="safe-config-account",
                status="logged_in",
            )
        )
    payload = {
        "platform": "douyin",
        "accountMode": "auto",
        "stages": ["collect", "report"],
        "configSnapshot": {
            "author": "research team",
            "authenticationMode": "external",
            "secretaryRole": "procurement assistant",
            "tokenBudget": 1200,
            "maxTokens": 400,
            "cookiePolicy": "strict",
            "passwordPolicy": "managed externally",
        },
        "campaign": {"countries": ["CN"]},
        "keywords": [{"text": "power grid", "language": "zh-CN"}],
    }

    original_service = app.state.pipeline_job_service
    app.state.pipeline_job_service = PipelineJobService(
        database=acquisition_api,
        providers=_AvailableProviders(),
    )
    try:
        async with api_client() as client:
            response = await client.post(
                "/api/acquisition/jobs", json=payload
            )
    finally:
        app.state.pipeline_job_service = original_service

    assert response.status_code == 202
    snapshot = response.json()["job"]["configSnapshot"]
    assert snapshot == {
        **payload["configSnapshot"],
        "businessMode": "ai_acquisition",
        "acquisitionSchemaVersion": "1.0",
    }


@pytest.mark.asyncio
async def test_campaign_is_created_for_existing_job_and_is_immutable(acquisition_api):
    seed_job(acquisition_api, "job-1")
    payload = {
        "countries": ["VN"],
        "languages": ["vi"],
        "industries": ["power infrastructure"],
        "products": ["transformer"],
        "customerRoles": ["contractor", "buyer"],
        "hardConditions": {
            "notListed": True,
            "excludedSubjects": ["consumer"],
        },
        "preferenceConditions": {
            "employeeCount": "10-20",
            "listingStatus": "unlisted",
        },
        "excludedTargets": ["consumer"],
        "searchBudget": {
            "maxKeywords": 10,
            "maxVideosPerKeyword": 20,
            "maxCommentsPerVideo": 30,
        },
        "keywordMix": {"effectivePercent": 70, "newPercent": 30},
    }
    async with api_client() as client:
        created = await client.post(
            "/api/acquisition/jobs/job-1/campaign", json=payload
        )
        fetched = await client.get("/api/acquisition/jobs/job-1/campaign")
        duplicate = await client.post(
            "/api/acquisition/jobs/job-1/campaign",
            json={**payload, "countries": ["CN"]},
        )
        missing = await client.post(
            "/api/acquisition/jobs/missing/campaign", json=payload
        )

    assert created.status_code == 201
    assert created.json()["campaign"]["jobId"] == "job-1"
    assert created.json()["campaign"]["platform"] == "tiktok"
    assert fetched.json()["campaign"]["countries"] == ["VN"]
    assert duplicate.status_code == 409
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_campaign_request_forbids_unknown_fields_and_bounds_lists(acquisition_api):
    seed_job(acquisition_api, "job-1")
    async with api_client() as client:
        unknown = await client.post(
            "/api/acquisition/jobs/job-1/campaign",
            json={"countries": ["VN"], "operator": "forged"},
        )
        too_many = await client.post(
            "/api/acquisition/jobs/job-1/campaign",
            json={"countries": [f"C{i}" for i in range(51)]},
        )
    assert unknown.status_code == 422
    assert too_many.status_code == 422


@pytest.mark.parametrize(
    "private_field",
    [
        "password",
        "token",
        "accessToken",
        "authorization",
        "privateKey",
        "clientSecret",
        "secretaryRole",
    ],
)
@pytest.mark.asyncio
async def test_campaign_rejects_non_allowlisted_nested_fields(
    acquisition_api,
    private_field,
):
    seed_job(acquisition_api, "job-1")
    async with api_client() as client:
        response = await client.post(
            "/api/acquisition/jobs/job-1/campaign",
            json={"hardConditions": {private_field: "must-not-store"}},
        )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_campaign_nested_dtos_enforce_budget_and_keyword_mix(
    acquisition_api,
):
    seed_job(acquisition_api, "job-1")
    async with api_client() as client:
        excessive_budget = await client.post(
            "/api/acquisition/jobs/job-1/campaign",
            json={"searchBudget": {"maxCommentsPerVideo": 201}},
        )
        invalid_mix = await client.post(
            "/api/acquisition/jobs/job-1/campaign",
            json={
                "keywordMix": {
                    "effectivePercent": 80,
                    "newPercent": 30,
                }
            },
        )
    assert excessive_budget.status_code == 422
    assert invalid_mix.status_code == 422


@pytest.mark.asyncio
async def test_campaign_response_uses_explicit_allowlist_for_legacy_rows(
    acquisition_api,
):
    seed_job(acquisition_api, "job-1")
    with acquisition_api.session() as session:
        AcquisitionStore().create_campaign(
            session,
            job_id="job-1",
            platform="tiktok",
            hard_conditions={
                "notListed": True,
                "clientSecret": "must-not-leak",
            },
            search_budget={
                "maxKeywords": 12,
                "authorization": "must-not-leak",
            },
        )

    async with api_client() as client:
        response = await client.get("/api/acquisition/jobs/job-1/campaign")

    assert response.status_code == 200
    campaign = response.json()["campaign"]
    assert campaign["hardConditions"]["notListed"] is True
    assert "clientSecret" not in campaign["hardConditions"]
    assert campaign["searchBudget"]["maxKeywords"] == 12
    assert "authorization" not in campaign["searchBudget"]


@pytest.mark.asyncio
async def test_campaign_legacy_invalid_known_values_fall_back_without_500(
    acquisition_api,
):
    seed_job(acquisition_api, "job-1")
    with acquisition_api.session() as session:
        AcquisitionStore().create_campaign(
            session,
            job_id="job-1",
            platform="tiktok",
            hard_conditions={
                "notListed": "definitely-not-a-boolean",
                "requiredKeywords": ["power"],
                "clientSecret": "must-not-leak",
            },
            preference_conditions={
                "registeredCapital": "1m-10m",
                "listingStatus": "sometimes-listed",
                "employeeCount": "x" * 101,
            },
            search_budget={
                "maxKeywords": 999,
                "maxPages": 5,
                "privateKey": "must-not-leak",
            },
            keyword_mix={"effectivePercent": 80, "newPercent": 30},
        )

    async with api_client() as client:
        response = await client.get("/api/acquisition/jobs/job-1/campaign")

    assert response.status_code == 200
    campaign = response.json()["campaign"]
    assert campaign["hardConditions"]["notListed"] is None
    assert campaign["hardConditions"]["requiredKeywords"] == ["power"]
    assert "clientSecret" not in campaign["hardConditions"]
    assert campaign["preferenceConditions"]["registeredCapital"] == "1m-10m"
    assert campaign["preferenceConditions"]["listingStatus"] is None
    assert campaign["preferenceConditions"]["employeeCount"] is None
    assert campaign["searchBudget"]["maxKeywords"] == 20
    assert campaign["searchBudget"]["maxPages"] == 5
    assert "privateKey" not in campaign["searchBudget"]
    assert campaign["keywordMix"] == {
        "effectivePercent": 70,
        "newPercent": 30,
    }


@pytest.mark.asyncio
async def test_keyword_create_list_and_effectiveness_update_are_job_scoped(
    acquisition_api,
):
    seed_job(acquisition_api, "job-1")
    seed_job(acquisition_api, "job-2")
    async with api_client() as client:
        created = await client.post(
            "/api/acquisition/jobs/job-1/keywords",
            json={
                "text": "transformer supplier",
                "language": "en",
                "keywordType": "product",
                "source": "manual",
            },
        )
        keyword_id = created.json()["keyword"]["id"]
        updated = await client.patch(
            f"/api/acquisition/jobs/job-1/keywords/{keyword_id}",
            json={
                "status": "effective",
                "usageCount": 2,
                "videoCount": 12,
                "relevantVideoCount": 7,
                "candidateCount": 4,
                "qualifiedCount": 1,
                "replyCount": 1,
                "businessLeadCount": 1,
                "lastUsedAt": "2026-08-02T08:00:00",
            },
        )
        listed = await client.get("/api/acquisition/jobs/job-1/keywords")
        foreign = await client.patch(
            f"/api/acquisition/jobs/job-2/keywords/{keyword_id}",
            json={"status": "disabled"},
        )

    assert created.status_code == 201
    assert updated.status_code == 200
    assert updated.json()["keyword"]["status"] == "effective"
    assert updated.json()["keyword"]["relevantVideoCount"] == 7
    assert listed.json()["items"][0]["text"] == "transformer supplier"
    assert foreign.status_code == 404


@pytest.mark.asyncio
async def test_keyword_delete_is_job_scoped_and_preserves_referenced_evidence(
    acquisition_api,
):
    seed_job(acquisition_api, "job-1")
    seed_job(acquisition_api, "job-2")
    user_id = seed_candidate(acquisition_api, "job-1")
    with acquisition_api.session() as session:
        AcquisitionStore().create_campaign(
            session, job_id="job-1", platform="tiktok"
        )

    async with api_client() as client:
        referenced = await client.post(
            "/api/acquisition/jobs/job-1/keywords",
            json={"text": "referenced keyword"},
        )
        removable = await client.post(
            "/api/acquisition/jobs/job-1/keywords",
            json={"text": "removable keyword"},
        )
        referenced_id = referenced.json()["keyword"]["id"]
        removable_id = removable.json()["keyword"]["id"]

        with acquisition_api.session() as session:
            AcquisitionStore().add_evidence(
                session,
                job_id="job-1",
                user_id=user_id,
                keyword_id=referenced_id,
                source_type="video_comment",
                raw_text="quote please",
            )

        protected = await client.delete(
            f"/api/acquisition/jobs/job-1/keywords/{referenced_id}"
        )
        foreign = await client.delete(
            f"/api/acquisition/jobs/job-2/keywords/{removable_id}"
        )
        deleted = await client.delete(
            f"/api/acquisition/jobs/job-1/keywords/{removable_id}"
        )
        listed = await client.get("/api/acquisition/jobs/job-1/keywords")

    assert protected.status_code == 409
    assert protected.json()["detail"]["code"] == "keyword_in_use"
    assert foreign.status_code == 404
    assert deleted.status_code == 204
    assert [item["id"] for item in listed.json()["items"]] == [referenced_id]


@pytest.mark.asyncio
async def test_keyword_delete_reference_race_returns_stable_conflict(
    acquisition_api,
    monkeypatch,
):
    seed_job(acquisition_api, "job-1")
    user_id = seed_candidate(acquisition_api, "job-1")
    with acquisition_api.session() as session:
        AcquisitionStore().create_campaign(
            session, job_id="job-1", platform="tiktok"
        )

    async with api_client() as client:
        created = await client.post(
            "/api/acquisition/jobs/job-1/keywords",
            json={"text": "race keyword"},
        )
        keyword_id = created.json()["keyword"]["id"]

        def add_reference_after_count(*, session, job_id, keyword):
            AcquisitionStore().add_evidence(
                session,
                job_id=job_id,
                user_id=user_id,
                keyword_id=keyword.id,
                source_type="video_comment",
                raw_text="arrived during delete",
            )

        monkeypatch.setattr(
            app.state,
            "acquisition_keyword_delete_hook",
            add_reference_after_count,
            raising=False,
        )
        response = await client.delete(
            f"/api/acquisition/jobs/job-1/keywords/{keyword_id}"
        )
        listed = await client.get("/api/acquisition/jobs/job-1/keywords")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "keyword_in_use"
    assert [item["id"] for item in listed.json()["items"]] == [keyword_id]


def test_keyword_delete_real_concurrent_insert_has_serializable_outcome(
    acquisition_api,
):
    seed_job(acquisition_api, "job-1")
    user_id = seed_candidate(acquisition_api, "job-1")
    with acquisition_api.session() as session:
        store = AcquisitionStore()
        store.create_campaign(session, job_id="job-1", platform="tiktok")
        keyword = store.create_keyword(
            session,
            job_id="job-1",
            platform="tiktok",
            text="real race keyword",
        )
        keyword_id = keyword.id

    reference_inserted = threading.Event()
    release_writer = threading.Event()
    delete_begin_attempted = threading.Event()
    writer_errors: list[Exception] = []
    delete_result: dict[str, object] = {}

    def observe_begin(_conn, _cursor, statement, *_args):
        if (
            threading.current_thread().name == "api-delete"
            and statement.strip().upper().startswith("BEGIN IMMEDIATE")
        ):
            delete_begin_attempted.set()

    def insert_reference():
        try:
            with acquisition_api.session() as session:
                session.execute(text("BEGIN IMMEDIATE"))
                AcquisitionStore().add_evidence(
                    session,
                    job_id="job-1",
                    user_id=user_id,
                    keyword_id=keyword_id,
                    source_type="video_comment",
                    raw_text="concurrent reference",
                )
                reference_inserted.set()
                if not release_writer.wait(timeout=5):
                    raise RuntimeError("delete did not attempt BEGIN IMMEDIATE")
        except Exception as exc:  # surfaced in the parent test thread
            writer_errors.append(exc)

    async def request_delete():
        async with api_client() as client:
            response = await client.delete(
                f"/api/acquisition/jobs/job-1/keywords/{keyword_id}"
            )
            delete_result["status"] = response.status_code
            delete_result["body"] = response.json() if response.content else None

    def delete_keyword():
        try:
            asyncio.run(request_delete())
        except Exception as exc:  # no ASGI exception may escape as a 500
            delete_result["error"] = exc

    event.listen(
        acquisition_api.engine,
        "before_cursor_execute",
        observe_begin,
    )
    writer = threading.Thread(target=insert_reference, name="evidence-writer")
    deleter = threading.Thread(target=delete_keyword, name="api-delete")
    try:
        writer.start()
        assert reference_inserted.wait(timeout=5)
        deleter.start()
        began_immediate = delete_begin_attempted.wait(timeout=2)
        release_writer.set()
        writer.join(timeout=10)
        deleter.join(timeout=10)
    finally:
        release_writer.set()
        event.remove(
            acquisition_api.engine,
            "before_cursor_execute",
            observe_begin,
        )

    assert began_immediate, "DELETE must acquire BEGIN IMMEDIATE before reads"
    assert not writer.is_alive() and not deleter.is_alive()
    assert writer_errors == []
    assert "error" not in delete_result
    assert delete_result["status"] in {204, 409}
    with acquisition_api.session() as session:
        keyword_exists = session.get(AcquisitionKeyword, keyword_id) is not None
        evidence_count = session.query(DiscoveryEvidence).filter_by(
            job_id="job-1",
            user_id=user_id,
            keyword_id=keyword_id,
        ).count()
    if delete_result["status"] == 409:
        assert keyword_exists and evidence_count == 1
    else:
        assert not keyword_exists and evidence_count == 0


@pytest.mark.asyncio
async def test_keywords_are_paginated_and_validate_bounds(acquisition_api):
    seed_job(acquisition_api, "job-1")
    async with api_client() as client:
        for text in ("alpha", "beta", "gamma"):
            await client.post(
                "/api/acquisition/jobs/job-1/keywords",
                json={"text": text},
            )
        page = await client.get(
            "/api/acquisition/jobs/job-1/keywords?limit=1&offset=1"
        )
        invalid = await client.get(
            "/api/acquisition/jobs/job-1/keywords?limit=201"
        )

    assert page.json()["items"][0]["text"] == "beta"
    assert page.json()["total"] == 3
    assert page.json()["limit"] == 1
    assert page.json()["offset"] == 1
    assert invalid.status_code == 422


@pytest.mark.asyncio
async def test_stage_summaries_and_candidate_detail_expose_business_evidence(
    acquisition_api,
):
    seed_job(acquisition_api, "job-1")
    user_id = seed_candidate(acquisition_api, "job-1")
    store = AcquisitionStore()
    with acquisition_api.session() as session:
        store.create_campaign(session, job_id="job-1", platform="tiktok")
        keyword = store.create_keyword(
            session,
            job_id="job-1",
            platform="tiktok",
            text="transformer supplier",
        )
        store.add_evidence(
            session,
            job_id="job-1",
            user_id=user_id,
            keyword_id=keyword.id,
            source_type="video_comment",
            keyword_text=keyword.text,
            video_id="video-1",
            video_url="https://www.tiktok.com/video/1",
            comment_id="comment-1",
            raw_text="Can you quote 50 units?",
            translated_text="请报价50台",
            relevance_score=0.9,
            completeness_score=0.8,
        )
        store.add_evidence(
            session,
            job_id="job-1",
            user_id=user_id,
            source_type="author_profile",
            author_id="author-1",
            author_url="https://www.tiktok.com/@buyer-vn",
            raw_text="Electrical contractor",
        )
        store.add_evidence(
            session,
            job_id="job-1",
            user_id=user_id,
            source_type="representative_video",
            video_id="video-2",
            raw_text="Electrical substation project",
            evidence_metadata={"cookieJar": "must-not-leak"},
        )
        store.add_evidence(
            session,
            job_id="job-1",
            user_id=user_id,
            source_type="direct_user_search",
            raw_text="Vietnam power contractor",
            evidence_metadata={"profilePath": "C:/private/profile"},
        )
        store.create_assessment(
            session,
            job_id="job-1",
            user_id=user_id,
            labels=["buyer", "contractor"],
            match_score=86,
            confidence_score=73,
            positive_evidence=["asked for a quote"],
            missing_fields=["employee_count"],
            reasoning="Strong purchase intent",
            suggested_status="manual_review",
            model_provider="deepseek",
            model_name="deepseek-chat",
        )

    async with api_client() as client:
        stage_01 = await client.get("/api/acquisition/jobs/job-1/stage-01")
        stage_02 = await client.get("/api/acquisition/jobs/job-1/stage-02")
        candidates = await client.get("/api/acquisition/jobs/job-1/candidates")
        detail = await client.get(
            f"/api/acquisition/jobs/job-1/candidates/{user_id}"
        )

    assert stage_01.status_code == 200
    assert stage_01.json()["summary"] == {
        "totalCandidates": 1,
        "evidenceCount": 4,
        "keywordCount": 1,
        "byDiscoveryStatus": {"candidate": 1},
        "bySourceType": {
            "author_profile": 1,
            "direct_user_search": 1,
            "representative_video": 1,
            "video_comment": 1,
        },
    }
    assert stage_02.json()["summary"]["byQualificationStatus"] == {
        "manual_review": 1
    }
    assert stage_02.json()["summary"]["pendingHumanReview"] == 1
    candidate_item = candidates.json()["items"][0]
    assert candidate_item["evidenceCount"] == 4
    assert len(candidate_item["evidence"]) == 3
    assert [item["sourceType"] for item in candidate_item["evidence"]] == [
        "direct_user_search",
        "representative_video",
        "author_profile",
    ]
    assert "cookie" not in str(candidate_item["evidence"]).lower()
    assert "profilepath" not in str(candidate_item["evidence"]).lower()
    body = detail.json()
    assert len(body["evidence"]["items"]) == 4
    assert body["evidence"]["total"] == 4
    assert body["evidence"]["limit"] == 50
    assert body["evidence"]["offset"] == 0
    assert body["latestAssessment"]["matchScore"] == 86
    assert body["candidate"]["qualificationStatus"] == "manual_review"
    assert body["candidate"]["reviewVersion"] == 0
    serialized = str(body).lower()
    assert "cookies_json" not in serialized
    assert "secret" not in serialized
    assert "profile_path" not in serialized


@pytest.mark.asyncio
async def test_candidate_list_related_data_uses_constant_query_count(
    acquisition_api,
):
    seed_job(acquisition_api, "job-1")
    store = AcquisitionStore()
    for index in range(20):
        user_id = seed_candidate(
            acquisition_api, "job-1", f"candidate-{index}"
        )
        with acquisition_api.session() as session:
            for history_index in range(8):
                store.add_evidence(
                    session,
                    job_id="job-1",
                    user_id=user_id,
                    source_type="video_comment",
                    raw_text=f"evidence-{index}-{history_index}",
                )
            for history_index in range(5):
                store.create_assessment(
                    session,
                    job_id="job-1",
                    user_id=user_id,
                    labels=["buyer"],
                    match_score=70 + history_index,
                    confidence_score=60,
                    suggested_status="manual_review",
                )

    statement_count = 0
    evidence_load_count = 0
    assessment_load_count = 0

    def count_statement(*_args):
        nonlocal statement_count
        statement_count += 1

    def count_evidence_load(_target, _context):
        nonlocal evidence_load_count
        evidence_load_count += 1

    def count_assessment_load(_target, _context):
        nonlocal assessment_load_count
        assessment_load_count += 1

    event.listen(
        acquisition_api.engine,
        "before_cursor_execute",
        count_statement,
    )
    event.listen(DiscoveryEvidence, "load", count_evidence_load)
    event.listen(CandidateAssessment, "load", count_assessment_load)
    try:
        async with api_client() as client:
            response = await client.get(
                "/api/acquisition/jobs/job-1/candidates?limit=20"
            )
    finally:
        event.remove(
            acquisition_api.engine,
            "before_cursor_execute",
            count_statement,
        )
        event.remove(DiscoveryEvidence, "load", count_evidence_load)
        event.remove(CandidateAssessment, "load", count_assessment_load)

    assert response.status_code == 200
    assert len(response.json()["items"]) == 20
    assert statement_count <= 7
    assert evidence_load_count <= 60
    assert assessment_load_count <= 20


@pytest.mark.asyncio
async def test_candidate_from_another_job_is_not_disclosed(acquisition_api):
    seed_job(acquisition_api, "job-1")
    seed_job(acquisition_api, "job-2")
    user_id = seed_candidate(acquisition_api, "job-2")
    async with api_client() as client:
        response = await client.get(
            f"/api/acquisition/jobs/job-1/candidates/{user_id}"
        )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_candidate_detail_evidence_is_paginated_and_validates_bounds(
    acquisition_api,
):
    seed_job(acquisition_api, "job-1")
    user_id = seed_candidate(acquisition_api, "job-1")
    with acquisition_api.session() as session:
        store = AcquisitionStore()
        for index in range(4):
            store.add_evidence(
                session,
                job_id="job-1",
                user_id=user_id,
                source_type=f"source-{index}",
                raw_text=f"evidence-{index}",
            )

    async with api_client() as client:
        page = await client.get(
            f"/api/acquisition/jobs/job-1/candidates/{user_id}"
            "?limit=2&offset=1"
        )
        invalid = await client.get(
            f"/api/acquisition/jobs/job-1/candidates/{user_id}?limit=201"
        )

    evidence = page.json()["evidence"]
    assert [item["sourceType"] for item in evidence["items"]] == [
        "source-1",
        "source-2",
    ]
    assert evidence["total"] == 4
    assert evidence["limit"] == 2
    assert evidence["offset"] == 1
    assert invalid.status_code == 422


@pytest.mark.asyncio
async def test_human_review_actions_use_authenticated_operator_and_write_audit(
    acquisition_api,
):
    seed_job(acquisition_api, "job-1")
    approved_id = seed_candidate(acquisition_api, "job-1", "approved")
    rejected_id = seed_candidate(acquisition_api, "job-1", "rejected")
    enriched_id = seed_candidate(acquisition_api, "job-1", "enriched")

    async with api_client(username="alice-reviewer") as client:
        forged = await client.post(
            f"/api/acquisition/jobs/job-1/candidates/{approved_id}/approve",
            json={"reviewVersion": 0, "operator": "mallory"},
        )
        approved = await client.post(
            f"/api/acquisition/jobs/job-1/candidates/{approved_id}/approve",
            json={"reviewVersion": 0, "reason": "verified", "priority": 1},
        )
        rejected = await client.post(
            f"/api/acquisition/jobs/job-1/candidates/{rejected_id}/reject",
            json={"reviewVersion": 0, "reason": "supplier"},
        )
        requested = await client.post(
            f"/api/acquisition/jobs/job-1/candidates/{enriched_id}/request-enrichment",
            json={"reviewVersion": 0, "reason": "company size missing"},
        )
        completed = await client.post(
            f"/api/acquisition/jobs/job-1/candidates/{enriched_id}/complete-enrichment",
            json={"reviewVersion": 1, "reason": "public registry checked"},
        )
        labels = await client.put(
            f"/api/acquisition/jobs/job-1/candidates/{enriched_id}/labels",
            json={
                "reviewVersion": 2,
                "labels": ["buyer", "contractor"],
                "reason": "human correction",
            },
        )
        audits = await client.get(
            f"/api/acquisition/jobs/job-1/candidates/{approved_id}/audits"
        )

    assert forged.status_code == 422
    assert approved.json()["candidate"]["qualificationStatus"] == "qualified"
    assert rejected.json()["candidate"]["qualificationStatus"] == "rejected"
    assert requested.json()["candidate"]["qualificationStatus"] == "need_enrichment"
    assert completed.json()["candidate"]["qualificationStatus"] == "manual_review"
    assert labels.json()["candidate"]["labels"] == ["buyer", "contractor"]
    assert audits.json()["items"][0]["operator"] == "alice-reviewer"


@pytest.mark.asyncio
async def test_review_audits_are_paginated_and_validate_bounds(acquisition_api):
    seed_job(acquisition_api, "job-1")
    user_id = seed_candidate(acquisition_api, "job-1")
    async with api_client() as client:
        first = await client.put(
            f"/api/acquisition/jobs/job-1/candidates/{user_id}/labels",
            json={"reviewVersion": 0, "labels": ["buyer"]},
        )
        second = await client.put(
            f"/api/acquisition/jobs/job-1/candidates/{user_id}/labels",
            json={"reviewVersion": 1, "labels": ["buyer", "contractor"]},
        )
        page = await client.get(
            f"/api/acquisition/jobs/job-1/candidates/{user_id}/audits"
            "?limit=1&offset=1"
        )
        invalid = await client.get(
            f"/api/acquisition/jobs/job-1/candidates/{user_id}/audits"
            "?offset=-1"
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert page.json()["items"][0]["labelsAfter"] == ["buyer", "contractor"]
    assert page.json()["total"] == 2
    assert page.json()["limit"] == 1
    assert page.json()["offset"] == 1
    assert invalid.status_code == 422


@pytest.mark.asyncio
async def test_stale_review_version_returns_stable_conflict(acquisition_api):
    seed_job(acquisition_api, "job-1")
    user_id = seed_candidate(acquisition_api, "job-1")
    async with api_client() as client:
        first = await client.post(
            f"/api/acquisition/jobs/job-1/candidates/{user_id}/approve",
            json={"reviewVersion": 0},
        )
        stale = await client.put(
            f"/api/acquisition/jobs/job-1/candidates/{user_id}/labels",
            json={"reviewVersion": 0, "labels": ["buyer"]},
        )

    assert first.status_code == 200
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "candidate_review_conflict"


@pytest.mark.asyncio
async def test_review_request_validates_priority_score_and_list_bounds(acquisition_api):
    seed_job(acquisition_api, "job-1")
    user_id = seed_candidate(acquisition_api, "job-1")
    async with api_client() as client:
        priority = await client.post(
            f"/api/acquisition/jobs/job-1/candidates/{user_id}/approve",
            json={"reviewVersion": 0, "priority": 6},
        )
        labels = await client.put(
            f"/api/acquisition/jobs/job-1/candidates/{user_id}/labels",
            json={"reviewVersion": 0, "labels": [f"label-{i}" for i in range(51)]},
        )
        keyword_counter = await client.patch(
            "/api/acquisition/jobs/job-1/keywords/1",
            json={"candidateCount": -1},
        )
    assert priority.status_code == 422
    assert labels.status_code == 422
    assert keyword_counter.status_code == 422
