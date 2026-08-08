"""Atomic Hermes acquisition job creation service tests."""

from __future__ import annotations

from sqlalchemy import func, select

import pytest

from tiktok_bot_core.browser.providers import BrowserAvailability
from tiktok_bot_core.models.entities import (
    AcquisitionCampaign,
    AcquisitionKeyword,
    PipelineJob,
    PipelineJobStage,
    TikTokAccount,
)
from tiktok_bot_core.services.acquisition_jobs import AcquisitionJobService
from tiktok_bot_core.services.pipeline_jobs import PipelineJobService
from tiktok_bot_core.storage.acquisition_store import AcquisitionStore
from tiktok_bot_core.storage.database import Database


class _AvailableProvider:
    async def check_available(self, _account):
        return BrowserAvailability(True)


class _AvailableProviders:
    def get(self, _platform):
        return _AvailableProvider()


class _FailingKeywordStore(AcquisitionStore):
    def __init__(self) -> None:
        self.keyword_writes = 0

    def create_keyword(self, session, **kwargs):
        self.keyword_writes += 1
        if self.keyword_writes == 2:
            raise RuntimeError("injected keyword failure")
        return super().create_keyword(session, **kwargs)


class _VisibilityStore(AcquisitionStore):
    def __init__(self, database: Database) -> None:
        self.database = database
        self.visible_job_count_before_commit: int | None = None

    def create_keyword(self, session, **kwargs):
        keyword = super().create_keyword(session, **kwargs)
        job_id = kwargs["job_id"]
        with self.database.engine.connect() as connection:
            self.visible_job_count_before_commit = int(
                connection.execute(
                    select(func.count(PipelineJob.id)).where(
                        PipelineJob.id == job_id
                    )
                ).scalar_one()
            )
        return keyword


@pytest.fixture
def acquisition_database(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'atomic-acquisition.db'}")
    database.init()
    with database.session() as session:
        session.add(
            TikTokAccount(
                platform="douyin",
                username="atomic-test-account",
                status="logged_in",
            )
        )
    yield database
    database.engine.dispose()


def _service(
    database: Database,
    *,
    acquisition_store: AcquisitionStore | None = None,
) -> AcquisitionJobService:
    pipeline_jobs = PipelineJobService(
        database=database,
        providers=_AvailableProviders(),
    )
    return AcquisitionJobService(
        database=database,
        pipeline_jobs=pipeline_jobs,
        acquisition_store=acquisition_store,
    )


def _create_args() -> dict:
    return {
        "platform": "douyin",
        "account_mode": "auto",
        "account_id": None,
        "stages": ["collect", "filter", "report"],
        "config_snapshot": {
            "existing": "preserved",
            "businessMode": "legacy",
            "acquisitionSchemaVersion": "old",
        },
        "campaign": {
            "countries": ["CN"],
            "languages": ["zh-CN"],
            "industries": ["power infrastructure"],
            "products": ["transformer"],
            "customer_roles": ["contractor"],
            "hard_conditions": {"notListed": True},
            "preference_conditions": {"employeeCount": "10-20"},
            "excluded_targets": ["consumer"],
            "search_budget": {"maxKeywords": 20},
            "keyword_mix": {"effectivePercent": 70, "newPercent": 30},
        },
        "keywords": [
            {
                "text": "power grid contractor",
                "language": "zh-CN",
                "keyword_type": "industry",
                "source": "manual",
                "status": "new",
            },
            {
                "text": "transformer procurement",
                "language": "zh-CN",
                "keyword_type": "intent",
                "source": "manual",
                "status": "testing",
            },
        ],
    }


@pytest.mark.asyncio
async def test_create_job_commits_job_campaign_and_keywords_together(
    acquisition_database,
):
    bundle = await _service(acquisition_database).create_job(**_create_args())

    assert bundle.campaign.job_id == bundle.job.id
    assert [keyword.job_id for keyword in bundle.keywords] == [
        bundle.job.id,
        bundle.job.id,
    ]
    assert bundle.job.config_snapshot_json == {
        "existing": "preserved",
        "businessMode": "ai_acquisition",
        "acquisitionSchemaVersion": "1.0",
    }
    with acquisition_database.session() as session:
        assert session.scalar(select(func.count(PipelineJob.id))) == 1
        assert session.scalar(select(func.count(PipelineJobStage.id))) == 3
        assert session.scalar(select(func.count(AcquisitionCampaign.id))) == 1
        assert session.scalar(select(func.count(AcquisitionKeyword.id))) == 2


@pytest.mark.asyncio
async def test_create_job_rolls_back_everything_when_keyword_write_fails(
    acquisition_database,
):
    service = _service(
        acquisition_database,
        acquisition_store=_FailingKeywordStore(),
    )

    with pytest.raises(RuntimeError, match="injected keyword failure"):
        await service.create_job(**_create_args())

    with acquisition_database.session() as session:
        assert session.scalar(select(func.count(PipelineJob.id))) == 0
        assert session.scalar(select(func.count(PipelineJobStage.id))) == 0
        assert session.scalar(select(func.count(AcquisitionCampaign.id))) == 0
        assert session.scalar(select(func.count(AcquisitionKeyword.id))) == 0


@pytest.mark.asyncio
async def test_uncommitted_job_is_not_visible_to_dispatcher_connection(
    acquisition_database,
):
    store = _VisibilityStore(acquisition_database)
    bundle = await _service(
        acquisition_database,
        acquisition_store=store,
    ).create_job(**_create_args())

    assert store.visible_job_count_before_commit == 0
    with acquisition_database.engine.connect() as connection:
        visible_after_commit = connection.execute(
            select(func.count(PipelineJob.id)).where(
                PipelineJob.id == bundle.job.id
            )
        ).scalar_one()
    assert visible_after_commit == 1


@pytest.mark.asyncio
async def test_blank_keyword_is_rejected_by_service_without_partial_rows(
    acquisition_database,
):
    args = _create_args()
    args["keywords"] = [
        {
            "text": " \t  ",
            "language": "zh-CN",
            "keyword_type": "industry",
            "source": "manual",
            "status": "new",
        }
    ]

    with pytest.raises(ValueError, match="keyword text must not be blank"):
        await _service(acquisition_database).create_job(**args)

    with acquisition_database.session() as session:
        assert session.scalar(select(func.count(PipelineJob.id))) == 0
        assert session.scalar(select(func.count(PipelineJobStage.id))) == 0
        assert session.scalar(select(func.count(AcquisitionCampaign.id))) == 0
        assert session.scalar(select(func.count(AcquisitionKeyword.id))) == 0


@pytest.mark.parametrize(
    "stages",
    [
        ["collect", "report", "filter"],
        ["collect", "unknown"],
        ["collect", "collect", "report"],
        ["filter", "report"],
    ],
)
@pytest.mark.asyncio
async def test_service_rejects_invalid_acquisition_stage_sequences_before_writes(
    acquisition_database,
    stages,
):
    args = _create_args()
    args["stages"] = stages

    with pytest.raises(ValueError, match="ordered subsequence"):
        await _service(acquisition_database).create_job(**args)

    with acquisition_database.session() as session:
        assert session.scalar(select(func.count(PipelineJob.id))) == 0
        assert session.scalar(select(func.count(PipelineJobStage.id))) == 0
        assert session.scalar(select(func.count(AcquisitionCampaign.id))) == 0
        assert session.scalar(select(func.count(AcquisitionKeyword.id))) == 0


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
async def test_service_recursively_rejects_sensitive_config_snapshot_keys(
    acquisition_database,
    sensitive_key,
):
    args = _create_args()
    secret_value = "must-never-persist-or-echo"
    args["config_snapshot"] = {
        "ordinary": "preserved",
        "nested": [{"deeper": {sensitive_key: secret_value}}],
    }

    with pytest.raises(ValueError, match="sensitive credential") as exc_info:
        await _service(acquisition_database).create_job(**args)

    assert sensitive_key not in str(exc_info.value)
    assert secret_value not in str(exc_info.value)
    with acquisition_database.session() as session:
        assert session.scalar(select(func.count(PipelineJob.id))) == 0
        assert session.scalar(select(func.count(PipelineJobStage.id))) == 0
        assert session.scalar(select(func.count(AcquisitionCampaign.id))) == 0
        assert session.scalar(select(func.count(AcquisitionKeyword.id))) == 0


@pytest.mark.asyncio
async def test_service_rejects_nested_bearer_auth_structure_without_echo(
    acquisition_database,
):
    args = _create_args()
    secret_value = "nested-bearer-must-not-echo"
    args["config_snapshot"] = {
        "runtime": {
            "auth": {
                "type": "bearer",
                "value": secret_value,
            }
        }
    }

    with pytest.raises(ValueError, match="sensitive credential") as exc_info:
        await _service(acquisition_database).create_job(**args)

    assert "auth" not in str(exc_info.value).casefold()
    assert secret_value not in str(exc_info.value)
    with acquisition_database.session() as session:
        assert session.scalar(select(func.count(PipelineJob.id))) == 0


@pytest.mark.asyncio
async def test_service_rejects_scalar_bearer_auth_without_echo(
    acquisition_database,
):
    args = _create_args()
    secret_value = "Bearer scalar-secret-must-not-echo"
    args["config_snapshot"] = {"runtime": {"auth": secret_value}}

    with pytest.raises(ValueError, match="sensitive credential") as exc_info:
        await _service(acquisition_database).create_job(**args)

    assert secret_value not in str(exc_info.value)
    with acquisition_database.session() as session:
        assert session.scalar(select(func.count(PipelineJob.id))) == 0


@pytest.mark.asyncio
async def test_service_allows_noncredential_business_config_names(
    acquisition_database,
):
    args = _create_args()
    args["config_snapshot"] = {
        "author": "research team",
        "authenticationMode": "external",
        "secretaryRole": "procurement assistant",
        "tokenBudget": 1200,
        "maxTokens": 400,
        "cookiePolicy": "strict",
        "passwordPolicy": "managed externally",
    }

    bundle = await _service(acquisition_database).create_job(**args)

    assert bundle.job.config_snapshot_json == {
        **args["config_snapshot"],
        "businessMode": "ai_acquisition",
        "acquisitionSchemaVersion": "1.0",
    }
