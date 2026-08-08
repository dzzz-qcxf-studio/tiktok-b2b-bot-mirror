"""TikTok B2B Bot — FastAPI REST 服务器

启动: uvicorn tiktok_bot_api.main:app --env-file .env --reload
"""

import asyncio
import json
import logging
import os
import threading
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, Optional
from urllib.parse import urlsplit

from fastapi import FastAPI, Query, HTTPException, Depends, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse, Response
from dotenv import set_key
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import aliased

from tiktok_bot_core.llm import aclose_llm_router
from tiktok_bot_core.llm.providers import (
    LLMProviderConfig,
    LLMProviderError,
    OpenAICompatibleProvider,
    resolve_llm_api_key,
)
from tiktok_bot_core.models.entities import (
    AcquisitionCampaign,
    AcquisitionKeyword,
    CandidateAssessment,
    CandidateReviewAudit,
    DiscoveryEvidence,
    LLM_ROUTE_KEYS,
    PipelineJob,
    PipelineJobUser,
    PipelineSchedule,
    TikTokAccount,
    User,
)
from tiktok_bot_core.models.pipeline_states import PIPELINE_STAGES
from tiktok_bot_core.platforms import PlatformType
from tiktok_bot_core.services.pipeline_jobs import (
    PipelineJobError,
    PipelineJobService,
    PipelineRuntime,
)
from tiktok_bot_core.services.acquisition_jobs import (
    AcquisitionJobService,
    validate_acquisition_config_snapshot,
    validate_acquisition_stages,
)
from tiktok_bot_core.services.business_read_model import BusinessReadModel
from tiktok_bot_core.services.pipeline_scheduler import next_cron_run
from tiktok_bot_core.browser.providers import (
    BrowserProviderRegistry,
    DouyinInteractiveLoginProvider,
)
from tiktok_bot_core.services.account_leases import (
    AccountBusyError,
    AccountLeaseManager,
)
from tiktok_bot_core.services.interactive_login import (
    InteractiveLoginError,
    InteractiveLoginService,
    LoginCleanupIncompleteError,
    LoginOperationError,
    LoginSession,
    LoginSessionNotFoundError,
    LoginUnavailableError,
    PersistedAuthState,
)
from tiktok_bot_core.storage.database import get_db
from tiktok_bot_core.storage.acquisition_store import AcquisitionStore
from tiktok_bot_core.storage.llm_store import (
    LLMProviderConflictError,
    LLMProviderInUseError,
    LLMProviderNotFoundError,
    LLMRouteValidationError,
    LLMStore,
    LLMWriteTransactionError,
)
from tiktok_bot_core.storage.sqlite_store import SqliteStore
from tiktok_bot_core.settings import get_settings, reload_settings
from tiktok_bot_core.events.bus import get_event_bus
from tiktok_bot_api.auth import (
    LoginRequest, RegisterRequest, TokenResponse,
    authenticate, authenticate_apikey, create_token, decode_token,
    get_current_user, require_user,
)
# 集中导入到顶部，避免每个 endpoint 都重复 inline import
from tiktok_bot_core.services.auth_service import (
    AccountAliasConflictError,
    AccountLimitReachedError,
    begin_immediate_account_write,
    build_auth_paths,
    ensure_account_capacity,
    get_auth_service,
    normalize_account_alias,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DOUYIN_MAX_CONCURRENCY_DEFAULT = 1
DOUYIN_MAX_CONCURRENCY_LIMIT = 20
PIPELINE_INTEGER_CONFIG_RANGES = {
    "daily_users": (20, 500),
    "daily_comment_limit": (1, 50),
    "daily_dm_limit": (1, 30),
    "comment_interval_min": (1, 60),
    "comment_interval_max": (1, 120),
    "dm_interval_min": (1, 60),
    "dm_interval_max": (1, 120),
    "comment_dm_gap_hours": (6, 72),
    "douyin_max_concurrency": (
        1,
        DOUYIN_MAX_CONCURRENCY_LIMIT,
    ),
}
PIPELINE_INTERVAL_PAIRS = {
    "comment_interval_min": (
        "comment_interval_min",
        "comment_interval_max",
    ),
    "comment_interval_max": (
        "comment_interval_min",
        "comment_interval_max",
    ),
    "dm_interval_min": ("dm_interval_min", "dm_interval_max"),
    "dm_interval_max": ("dm_interval_min", "dm_interval_max"),
}


def _parse_bounded_integer(key: str, value: Any) -> int:
    minimum, maximum = PIPELINE_INTEGER_CONFIG_RANGES[key]
    text = str(value).strip()
    if not text:
        raise ValueError(f"{key} 不能为空")
    try:
        parsed = int(text)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} 必须是整数") from exc
    if not minimum <= parsed <= maximum:
        raise ValueError(
            f"{key} 必须在 {minimum}..{maximum} 范围内"
        )
    return parsed


def _normalize_keywords(value: Any) -> list[str]:
    if isinstance(value, list):
        raw_items = value
    else:
        text = str(value or "").strip()
        if not text:
            raw_items = []
        elif text.startswith("["):
            try:
                raw_items = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError("tiktok_keywords JSON 格式无效") from exc
            if not isinstance(raw_items, list):
                raise ValueError("tiktok_keywords JSON 必须是字符串数组")
        else:
            raw_items = text.replace("\n", ",").split(",")

    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        if not isinstance(item, str):
            raise ValueError("tiktok_keywords 只能包含字符串")
        keyword = item.strip()
        if keyword and keyword not in seen:
            seen.add(keyword)
            normalized.append(keyword)
    return normalized


def _pipeline_config_defaults() -> dict[str, Any]:
    return {
        "daily_users": 120,
        "daily_comment_limit": settings.daily_comment_limit,
        "daily_dm_limit": settings.daily_dm_limit,
        "comment_interval_min": settings.comment_interval_min,
        "comment_interval_max": settings.comment_interval_max,
        "dm_interval_min": settings.dm_interval_min,
        "dm_interval_max": settings.dm_interval_max,
        "comment_dm_gap_hours": 24,
        "douyin_max_concurrency": DOUYIN_MAX_CONCURRENCY_DEFAULT,
        "tiktok_keywords": list(settings.tiktok_keywords),
    }


def _read_typed_pipeline_config(
    values: dict[str, str],
) -> dict[str, Any]:
    defaults = _pipeline_config_defaults()
    result: dict[str, Any] = {}
    for key in PIPELINE_INTEGER_CONFIG_RANGES:
        raw_value = values.get(key, defaults[key])
        try:
            result[key] = _parse_bounded_integer(key, raw_value)
        except ValueError:
            logger.warning(
                "Invalid persisted %s=%r; using default %r",
                key,
                raw_value,
                defaults[key],
            )
            result[key] = defaults[key]
    try:
        result["tiktok_keywords"] = _normalize_keywords(
            values.get("tiktok_keywords", defaults["tiktok_keywords"])
        )
    except ValueError:
        logger.warning(
            "Invalid persisted tiktok_keywords; using settings default"
        )
        result["tiktok_keywords"] = defaults["tiktok_keywords"]
    return result


def _parse_douyin_max_concurrency(value: Any) -> int:
    try:
        return _parse_bounded_integer(
            "douyin_max_concurrency",
            value,
        )
    except ValueError as exc:
        message = str(exc).replace(
            "douyin_max_concurrency",
            "抖音并发数",
        )
        raise ValueError(message) from exc


def _load_douyin_max_concurrency(database, config_store=None) -> int:
    """Load the persisted restart-time concurrency, falling back safely."""

    config_store = config_store or SqliteStore()
    with database.session() as session:
        record = config_store.get_config(
            session,
            "douyin_max_concurrency",
        )
        raw_value = (
            record.value
            if record is not None
            else DOUYIN_MAX_CONCURRENCY_DEFAULT
        )
    try:
        return _parse_douyin_max_concurrency(raw_value)
    except ValueError:
        logger.warning(
            "Invalid persisted douyin_max_concurrency=%r; using default %s",
            raw_value,
            DOUYIN_MAX_CONCURRENCY_DEFAULT,
        )
        return DOUYIN_MAX_CONCURRENCY_DEFAULT


class _LazyChromiumOwner:
    """Start Playwright only when a user actually opens a login browser."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._playwright = None

    async def launch_persistent_context(self, **kwargs):
        async with self._lock:
            if self._playwright is None:
                try:
                    from playwright.async_api import async_playwright
                except ImportError as exc:
                    raise RuntimeError(
                        "Playwright 未安装，无法打开交互式登录浏览器"
                    ) from exc
                self._playwright = await async_playwright().start()
            chromium = self._playwright.chromium
        return await chromium.launch_persistent_context(**kwargs)

    async def aclose(self) -> None:
        async with self._lock:
            playwright = self._playwright
            self._playwright = None
        if playwright is not None:
            await playwright.stop()


_API_WORKER_LOCK_PATH = Path(
    os.getenv(
        "TIKTOK_BOT_API_WORKER_LOCK",
        str(Path(__file__).resolve().parents[1] / "data" / "api-worker.lock"),
    )
)


def _acquire_api_worker_lock(path: Path | None = None):
    """Hold one cross-process lock because runtime and LLM state are process-local."""

    path = path or _API_WORKER_LOCK_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise RuntimeError(
            "TikTok Bot requires a single API worker; another worker is active"
        ) from exc
    return handle


def _release_api_worker_lock(handle) -> None:
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Start exactly one durable pipeline runtime for the API process."""

    worker_lock = _acquire_api_worker_lock()
    runtime = application.state.pipeline_runtime
    runtime_enabled = not (
        application.state.pipeline_runtime_disabled
        or os.getenv("TIKTOK_BOT_DISABLE_PIPELINE_RUNTIME", "").lower()
        in {"1", "true", "yes"}
    )
    try:
        if runtime_enabled:
            await runtime.start()
    except BaseException:
        _release_api_worker_lock(worker_lock)
        raise
    try:
        yield
    finally:
        shutdown_error: BaseException | None = None
        login_service = application.state.interactive_login_service
        try:
            await login_service.aclose()
        except LoginCleanupIncompleteError as exc:
            logger.error(
                "交互式登录资源尚未清理完成，可再次执行关闭: %s",
                exc.code,
            )
            shutdown_error = exc
        except BaseException as exc:
            logger.exception("关闭交互式登录服务失败")
            shutdown_error = exc

        chromium_owner = application.state.interactive_login_chromium
        try:
            await chromium_owner.aclose()
        except BaseException as exc:
            logger.exception("关闭交互式登录 Playwright 失败")
            if shutdown_error is None:
                shutdown_error = exc

        if runtime_enabled:
            try:
                await runtime.stop()
            except BaseException as exc:
                logger.exception("关闭 Pipeline runtime 失败")
                if shutdown_error is None:
                    shutdown_error = exc

        try:
            await aclose_llm_router()
        except BaseException as exc:
            logger.exception("关闭 LLM Router 失败")
            if shutdown_error is None:
                shutdown_error = exc
        try:
            _release_api_worker_lock(worker_lock)
        except BaseException as exc:
            logger.exception("释放 API 单 Worker 锁失败")
            if shutdown_error is None:
                shutdown_error = exc
        if shutdown_error is not None:
            raise shutdown_error


app = FastAPI(
    title="TikTok B2B Bot API",
    version="0.1.0",
    description="TikTok B2B 业务拓展机器人 REST API",
    lifespan=lifespan,
)

_DEFAULT_CORS_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:8080",
    "http://127.0.0.1:8080",
)


def _allowed_cors_origins() -> list[str]:
    raw = os.getenv("CORS_ALLOWED_ORIGINS", "")
    origins = [
        item.strip().rstrip("/")
        for item in raw.split(",")
        if item.strip()
    ] or list(_DEFAULT_CORS_ORIGINS)
    if any(
        origin == "*"
        or not origin.startswith(("http://", "https://"))
        for origin in origins
    ):
        raise RuntimeError(
            "CORS_ALLOWED_ORIGINS must contain explicit http(s) origins"
        )
    return origins


app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_cors_origins(),
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key"],
)

db = get_db()
store = SqliteStore()
bus = get_event_bus()
settings = get_settings()
interactive_login_data_root = (
    Path(__file__).resolve().parents[1] / "data"
)
pipeline_runtime = PipelineRuntime(
    database=db,
    douyin_limit=_load_douyin_max_concurrency(db, store),
)
interactive_login_chromium = _LazyChromiumOwner()
interactive_login_registry: BrowserProviderRegistry = (
    pipeline_runtime.providers
)
interactive_login_registry.register_interactive(
    "douyin",
    DouyinInteractiveLoginProvider(
        interactive_login_chromium,
        data_root=interactive_login_data_root,
    ),
)
account_lease_manager = AccountLeaseManager()


def _relative_login_path(path: Path, data_root: Path) -> str:
    resolved_root = data_root.resolve()
    resolved_path = Path(path).resolve()
    try:
        return resolved_path.relative_to(resolved_root).as_posix()
    except ValueError as exc:
        raise ValueError(
            "interactive login state must stay inside the protected data root"
        ) from exc


def _browser_account_key(platform: str, account_alias: str) -> str:
    return f"{platform}:{normalize_account_alias(account_alias)}"


def _canonical_account_matches(
    account_store: SqliteStore,
    session: Any,
    *,
    platform: str,
    account_alias: str,
) -> list[TikTokAccount]:
    canonical_alias = normalize_account_alias(account_alias)
    matches = []
    for account in account_store.get_tiktok_accounts(
        session,
        platform=platform,
    ):
        try:
            existing_alias = normalize_account_alias(
                account.username
            )
        except ValueError:
            continue
        if existing_alias == canonical_alias:
            matches.append(account)
    return matches


def _build_login_account_callbacks(database, *, data_root: Path):
    """Build detached resolver snapshots and one-transaction auth updates."""

    account_store = SqliteStore()
    protected_root = Path(data_root)

    def resolve_account(platform: str, account_alias: str):
        canonical_alias = normalize_account_alias(account_alias)
        with database.session() as session:
            matches = _canonical_account_matches(
                account_store,
                session,
                platform=platform,
                account_alias=canonical_alias,
            )
            if len(matches) > 1:
                raise LoginOperationError(
                    "account_alias_conflict"
                )
            account = matches[0] if matches else None
            if account is None:
                try:
                    ensure_account_capacity(
                        len(
                            account_store.get_tiktok_accounts(
                                session
                            )
                        )
                    )
                except AccountLimitReachedError as exc:
                    raise LoginOperationError(
                        "account_limit_reached"
                    ) from exc
            account_id = account.id if account is not None else None
            browser_account_key = _browser_account_key(
                platform,
                canonical_alias,
            )
            browser_provider = (
                str(account.browser_provider or "")
                if account is not None
                else ""
            )
            browser_profile_id = (
                str(account.browser_profile_id or "")
                if account is not None
                else ""
            )
            profile_path = (
                str(account.profile_path or "")
                if account is not None
                else ""
            )
            if platform == "douyin":
                paths = build_auth_paths(
                    protected_root,
                    platform,
                    browser_account_key,
                )
                browser_provider = browser_provider or "playwright"
                browser_profile_id = paths.profile_dir.name
                profile_path = _relative_login_path(
                    paths.profile_dir,
                    protected_root,
                )
            return SimpleNamespace(
                id=account_id,
                platform=platform,
                username=canonical_alias,
                browser_provider=browser_provider,
                browser_profile_id=browser_profile_id,
                profile_path=profile_path,
                browser_account_key=browser_account_key,
            )

    def update_account(
        account: Any,
        persisted: PersistedAuthState,
        verification: Any,
    ) -> None:
        platform = str(getattr(account, "platform", "")).strip()
        username = normalize_account_alias(
            str(getattr(account, "username", ""))
        )
        account_id = getattr(account, "id", None)
        browser_account_key = str(
            getattr(account, "browser_account_key", "")
            or _browser_account_key(platform, username)
        )
        storage_state_path = _relative_login_path(
            persisted.storage_state_path,
            protected_root,
        )
        profile_path = getattr(persisted, "profile_path", None)
        if profile_path is not None:
            profile_path = _relative_login_path(
                Path(profile_path),
                protected_root,
            )
        elif platform == "douyin":
            paths = build_auth_paths(
                protected_root,
                platform,
                browser_account_key,
            )
            if Path(persisted.storage_state_path).resolve() != (
                paths.storage_state.resolve()
            ):
                raise ValueError(
                    "persisted storage state does not match the account"
                )
            profile_path = _relative_login_path(
                paths.profile_dir,
                protected_root,
            )

        browser_provider = str(
            getattr(persisted, "browser_provider", "")
            or getattr(account, "browser_provider", "")
            or ""
        )
        browser_profile_id = str(
            getattr(persisted, "browser_profile_id", "")
            or getattr(account, "browser_profile_id", "")
            or ""
        )
        auth_version = getattr(persisted, "auth_version", None)
        if auth_version is None:
            auth_version = 2

        # Database.session owns commit/rollback. No state escapes as logged in
        # unless this whole block commits successfully.
        try:
            with database.session() as session:
                begin_immediate_account_write(session)
                matches = _canonical_account_matches(
                    account_store,
                    session,
                    platform=platform,
                    account_alias=username,
                )
                if len(matches) > 1:
                    raise AccountAliasConflictError()
                if account_id is not None:
                    target = account_store.get_tiktok_account(
                        session,
                        int(account_id),
                    )
                    if (
                        target is None
                        or target.platform != platform
                        or normalize_account_alias(
                            target.username
                        )
                        != username
                    ):
                        raise AccountAliasConflictError()
                else:
                    if matches:
                        raise AccountAliasConflictError()
                    ensure_account_capacity(
                        len(
                            account_store.get_tiktok_accounts(
                                session
                            )
                        )
                    )
                    target = TikTokAccount(
                        platform=platform,
                        username=username,
                        status="pending",
                    )
                    session.add(target)
                    session.flush()
                target.username = username
                target.cookies_json = json.dumps(
                    persisted.cookies,
                    ensure_ascii=False,
                )
                target.status = "logged_in"
                target.login_method = "interactive_browser"
                target.last_login_at = datetime.utcnow()
                target.storage_state_path = storage_state_path
                if profile_path:
                    target.profile_path = profile_path
                target.auth_verified_at = datetime.utcnow()
                target.auth_version = int(auth_version)
                if browser_provider:
                    target.browser_provider = browser_provider
                if browser_profile_id:
                    target.browser_profile_id = browser_profile_id
                nickname = str(
                    getattr(verification, "nickname", "") or ""
                ).strip()
                avatar_url = str(
                    getattr(verification, "avatar_url", "") or ""
                ).strip()
                follower_count = getattr(
                    verification,
                    "follower_count",
                    0,
                )
                if nickname:
                    target.nickname = nickname[:200]
                parsed_avatar = urlsplit(avatar_url)
                if (
                    avatar_url
                    and len(avatar_url) <= 1000
                    and parsed_avatar.scheme == "https"
                    and bool(parsed_avatar.netloc)
                ):
                    target.avatar_url = avatar_url[:1000]
                if (
                    isinstance(follower_count, int)
                    and not isinstance(follower_count, bool)
                    and follower_count >= 0
                ):
                    target.follower_count = follower_count
                target.updated_at = datetime.utcnow()
        except IntegrityError as exc:
            raise AccountAliasConflictError() from exc

    return resolve_account, update_account


login_account_resolver, login_account_updater = (
    _build_login_account_callbacks(
        db,
        data_root=interactive_login_data_root,
    )
)
interactive_login_service = InteractiveLoginService(
    providers=interactive_login_registry,
    leases=account_lease_manager,
    account_resolver=login_account_resolver,
    account_updater=login_account_updater,
)
app.state.pipeline_runtime = pipeline_runtime
app.state.pipeline_job_service = pipeline_runtime.job_service
app.state.pipeline_database = db
app.state.pipeline_runtime_disabled = False
app.state.interactive_login_service = interactive_login_service
app.state.interactive_login_chromium = interactive_login_chromium
app.state.account_lease_manager = account_lease_manager


# ===== Pydantic Models =====

PipelinePlatform = Literal["tiktok", "douyin"]
PipelineAccountMode = Literal["auto", "specified"]
PipelineStageName = Literal[
    "collect",
    "filter",
    "strategy",
    "outreach",
    "report",
    "iterate",
]
PipelineJobStatus = Literal[
    "queued",
    "running",
    "cancelling",
    "cancelled",
    "succeeded",
    "partial_failed",
    "failed",
    "interrupted",
]


class ApiRequestModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class PipelineJobRequest(ApiRequestModel):
    platform: PipelinePlatform
    account_mode: PipelineAccountMode = Field(alias="accountMode")
    account_id: Optional[int] = Field(default=None, alias="accountId", ge=1)
    stages: list[PipelineStageName] = Field(min_length=1)
    config_snapshot: dict[str, Any] = Field(
        default_factory=dict,
        alias="configSnapshot",
    )

    @model_validator(mode="after")
    def validate_account_selection(self):
        if self.account_mode == "specified" and self.account_id is None:
            raise ValueError("specified 模式必须提供 accountId")
        if self.account_mode == "auto" and self.account_id is not None:
            raise ValueError("auto 模式不能提供 accountId")
        if len(set(self.stages)) != len(self.stages):
            raise ValueError("stages 不能包含重复阶段")
        return self


class PipelineRunRequest(PipelineJobRequest):
    """Legacy request accepted by /api/pipeline/run."""


class PipelineScheduleRequest(PipelineJobRequest):
    name: str = Field(min_length=1, max_length=100)
    cron_expression: str = Field(alias="cronExpression", min_length=1)
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=50)
    enabled: bool = True
    config_snapshot: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("config", "configSnapshot"),
    )


class ConfigUpdateRequest(BaseModel):
    value: str
    description: str = ""


class PipelineConfigRequest(ApiRequestModel):
    daily_users: int = Field(strict=True, ge=20, le=500)
    daily_comment_limit: int = Field(strict=True, ge=1, le=50)
    daily_dm_limit: int = Field(strict=True, ge=1, le=30)
    comment_interval_min: int = Field(strict=True, ge=1, le=60)
    comment_interval_max: int = Field(strict=True, ge=1, le=120)
    dm_interval_min: int = Field(strict=True, ge=1, le=60)
    dm_interval_max: int = Field(strict=True, ge=1, le=120)
    comment_dm_gap_hours: int = Field(strict=True, ge=6, le=72)
    tiktok_keywords: list[str]
    douyin_max_concurrency: int = Field(strict=True, ge=1, le=20)

    @field_validator("tiktok_keywords")
    @classmethod
    def normalize_keywords(cls, value):
        return _normalize_keywords(value)

    @model_validator(mode="after")
    def validate_interval_pairs(self):
        if self.comment_interval_min > self.comment_interval_max:
            raise ValueError(
                "comment_interval_min 不能大于 comment_interval_max"
            )
        if self.dm_interval_min > self.dm_interval_max:
            raise ValueError("dm_interval_min 不能大于 dm_interval_max")
        return self


KeywordStatus = Literal[
    "new",
    "testing",
    "effective",
    "cooling",
    "low_yield",
    "disabled",
]
DiscoveryStatus = Literal[
    "candidate",
    "needs_more_evidence",
    "obvious_irrelevant",
    "duplicate",
    "blocked",
]
QualificationStatus = Literal[
    "qualified",
    "manual_review",
    "need_enrichment",
    "rejected",
]


class AcquisitionHardConditions(ApiRequestModel):
    excluded_subjects: list[str] = Field(
        default_factory=list,
        alias="excludedSubjects",
        max_length=50,
    )
    required_keywords: list[str] = Field(
        default_factory=list,
        alias="requiredKeywords",
        max_length=50,
    )
    must_be_business_account: Optional[bool] = Field(
        default=None,
        alias="mustBeBusinessAccount",
    )
    not_listed: Optional[bool] = Field(default=None, alias="notListed")

    @field_validator("excluded_subjects", "required_keywords")
    @classmethod
    def validate_condition_list(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item or len(item) > 200 for item in normalized):
            raise ValueError("条件项长度必须为 1 到 200 个字符")
        return normalized


class AcquisitionPreferenceConditions(ApiRequestModel):
    employee_count: Optional[str] = Field(
        default=None,
        alias="employeeCount",
        max_length=100,
    )
    registered_capital: Optional[str] = Field(
        default=None,
        alias="registeredCapital",
        max_length=100,
    )
    listing_status: Optional[
        Literal["listed", "unlisted", "unknown"]
    ] = Field(default=None, alias="listingStatus")
    company_scale: Optional[str] = Field(
        default=None,
        alias="companyScale",
        max_length=100,
    )
    minimum_years_established: Optional[int] = Field(
        default=None,
        alias="minimumYearsEstablished",
        ge=0,
        le=500,
    )
    maximum_years_established: Optional[int] = Field(
        default=None,
        alias="maximumYearsEstablished",
        ge=0,
        le=500,
    )

    @model_validator(mode="after")
    def validate_year_range(self):
        if (
            self.minimum_years_established is not None
            and self.maximum_years_established is not None
            and self.minimum_years_established
            > self.maximum_years_established
        ):
            raise ValueError("minimumYearsEstablished 不能大于 maximumYearsEstablished")
        return self


class AcquisitionSearchBudget(ApiRequestModel):
    max_keywords: int = Field(
        default=20,
        alias="maxKeywords",
        strict=True,
        ge=1,
        le=100,
    )
    max_videos_per_keyword: int = Field(
        default=20,
        alias="maxVideosPerKeyword",
        strict=True,
        ge=1,
        le=100,
    )
    max_comments_per_video: int = Field(
        default=30,
        alias="maxCommentsPerVideo",
        strict=True,
        ge=1,
        le=200,
    )
    max_author_videos: int = Field(
        default=5,
        alias="maxAuthorVideos",
        strict=True,
        ge=1,
        le=20,
    )
    max_pages: int = Field(
        default=10,
        alias="maxPages",
        strict=True,
        ge=1,
        le=100,
    )
    max_duration_minutes: int = Field(
        default=60,
        alias="maxDurationMinutes",
        strict=True,
        ge=1,
        le=1440,
    )
    max_llm_calls: int = Field(
        default=100,
        alias="maxLlmCalls",
        strict=True,
        ge=1,
        le=1000,
    )


class AcquisitionKeywordMix(ApiRequestModel):
    effective_percent: int = Field(
        default=70,
        alias="effectivePercent",
        strict=True,
        ge=0,
        le=100,
    )
    new_percent: int = Field(
        default=30,
        alias="newPercent",
        strict=True,
        ge=0,
        le=100,
    )

    @model_validator(mode="after")
    def validate_total_percent(self):
        if self.effective_percent + self.new_percent != 100:
            raise ValueError("关键词比例合计必须为 100")
        return self


class AcquisitionCampaignRequest(ApiRequestModel):
    countries: list[str] = Field(default_factory=list, max_length=50)
    languages: list[str] = Field(default_factory=list, max_length=50)
    industries: list[str] = Field(default_factory=list, max_length=50)
    products: list[str] = Field(default_factory=list, max_length=50)
    customer_roles: list[str] = Field(
        default_factory=list,
        alias="customerRoles",
        max_length=50,
    )
    hard_conditions: AcquisitionHardConditions = Field(
        default_factory=AcquisitionHardConditions,
        alias="hardConditions",
    )
    preference_conditions: AcquisitionPreferenceConditions = Field(
        default_factory=AcquisitionPreferenceConditions,
        alias="preferenceConditions",
    )
    excluded_targets: list[str] = Field(
        default_factory=list,
        alias="excludedTargets",
        max_length=50,
    )
    search_budget: AcquisitionSearchBudget = Field(
        default_factory=AcquisitionSearchBudget,
        alias="searchBudget",
    )
    keyword_mix: AcquisitionKeywordMix = Field(
        default_factory=AcquisitionKeywordMix,
        alias="keywordMix",
    )

    @field_validator(
        "countries",
        "languages",
        "industries",
        "products",
        "customer_roles",
        "excluded_targets",
    )
    @classmethod
    def validate_campaign_list(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item or len(item) > 200 for item in normalized):
            raise ValueError("列表项长度必须为 1 到 200 个字符")
        return normalized


class AcquisitionKeywordCreateRequest(ApiRequestModel):
    text: str = Field(min_length=1, max_length=300)
    language: str = Field(default="", max_length=20)
    keyword_type: str = Field(
        default="industry",
        alias="keywordType",
        min_length=1,
        max_length=50,
    )
    source: str = Field(default="manual", min_length=1, max_length=50)
    status: KeywordStatus = "new"

    @field_validator("text")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("关键词不能为空白")
        return normalized


class AcquisitionJobRequest(PipelineJobRequest):
    campaign: AcquisitionCampaignRequest
    keywords: list[AcquisitionKeywordCreateRequest] = Field(
        min_length=1,
        max_length=100,
    )

    @field_validator("config_snapshot")
    @classmethod
    def reject_sensitive_config_snapshot(
        cls,
        value: dict[str, Any],
    ) -> dict[str, Any]:
        validate_acquisition_config_snapshot(value)
        return value

    @model_validator(mode="after")
    def validate_acquisition_definition(self):
        validate_acquisition_stages(self.stages)
        seen: set[tuple[str, str]] = set()
        for keyword in self.keywords:
            normalized = (
                " ".join(keyword.text.split()).casefold(),
                keyword.language.strip().casefold(),
            )
            if normalized in seen:
                raise ValueError("keywords 不能包含规范化后重复的关键词")
            seen.add(normalized)
        return self


class AcquisitionKeywordStatsRequest(ApiRequestModel):
    status: Optional[KeywordStatus] = None
    usage_count: Optional[int] = Field(default=None, alias="usageCount", ge=0)
    video_count: Optional[int] = Field(default=None, alias="videoCount", ge=0)
    relevant_video_count: Optional[int] = Field(
        default=None,
        alias="relevantVideoCount",
        ge=0,
    )
    candidate_count: Optional[int] = Field(
        default=None,
        alias="candidateCount",
        ge=0,
    )
    qualified_count: Optional[int] = Field(
        default=None,
        alias="qualifiedCount",
        ge=0,
    )
    reply_count: Optional[int] = Field(default=None, alias="replyCount", ge=0)
    business_lead_count: Optional[int] = Field(
        default=None,
        alias="businessLeadCount",
        ge=0,
    )
    last_used_at: Optional[datetime] = Field(default=None, alias="lastUsedAt")


class CandidateReviewRequest(ApiRequestModel):
    review_version: int = Field(alias="reviewVersion", ge=0)
    reason: str = Field(default="", max_length=2000)
    labels: Optional[list[str]] = Field(default=None, max_length=50)
    priority: Optional[int] = Field(default=None, ge=1, le=5)

    @field_validator("labels")
    @classmethod
    def validate_review_labels(
        cls, value: Optional[list[str]]
    ) -> Optional[list[str]]:
        if value is None:
            return None
        normalized = [label.strip() for label in value]
        if any(not label or len(label) > 100 for label in normalized):
            raise ValueError("标签长度必须为 1 到 100 个字符")
        return normalized


class CandidateLabelsRequest(ApiRequestModel):
    review_version: int = Field(alias="reviewVersion", ge=0)
    labels: list[str] = Field(max_length=50)
    reason: str = Field(default="", max_length=2000)

    @field_validator("labels")
    @classmethod
    def validate_labels(cls, value: list[str]) -> list[str]:
        normalized = [label.strip() for label in value]
        if any(not label or len(label) > 100 for label in normalized):
            raise ValueError("标签长度必须为 1 到 100 个字符")
        return normalized

class UserResponse(BaseModel):
    id: int
    username: str
    status: str
    category: str
    bio: str = ""
    follower_count: int = 0
    created_at: Optional[datetime] = None


class InteractiveLoginSessionRequest(ApiRequestModel):
    platform: PipelinePlatform
    account_alias: str = Field(
        alias="accountAlias",
        min_length=1,
        max_length=100,
    )
    account_id: Optional[int] = Field(
        default=None,
        alias="accountId",
        ge=1,
    )

    @field_validator("account_alias")
    @classmethod
    def normalize_account_alias(cls, value: str) -> str:
        return normalize_account_alias(value)


class InteractiveLoginSessionResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    token: str
    platform: PipelinePlatform
    account_alias: str = Field(alias="accountAlias")
    account_id: Optional[int] = Field(alias="accountId")
    status: str
    browser_opened: bool = Field(alias="browserOpened")
    browser_provider: str = Field(alias="browserProvider")
    authenticated: bool
    persisted: bool
    started_at: datetime = Field(alias="startedAt")
    expires_at: datetime = Field(alias="expiresAt")
    error_code: str = Field(alias="errorCode")
    error_message: str = Field(alias="errorMessage")


_PUBLIC_LOGIN_ERROR_MESSAGES = {
    "account_busy": "该账号正在被其他任务使用",
    "account_alias_conflict": "该平台下存在冲突的账号别名",
    "account_limit_reached": "账号数量已达上限，请先删除不用的账号",
    "account_not_found": "账号不存在",
    "account_identity_mismatch": "账号信息不匹配",
    "browser_cleanup_failed": "登录浏览器清理失败，请重试取消",
    "browser_open_failed": "登录浏览器打开失败",
    "browser_open_timeout": "登录浏览器打开超时",
    "fingerprint_provider_unavailable": "TikTok 指纹浏览器尚未配置",
    "interactive_provider_unavailable": "该平台登录浏览器尚未配置",
    "interactive_login_unavailable": "交互式登录暂不可用",
    "interactive_login_failed": "交互式登录操作失败",
    "login_cancelled": "登录会话已取消",
    "login_cleanup_incomplete": "登录资源尚未清理完成，请重试",
    "login_service_closed": "登录服务正在关闭",
    "login_session_expired": "登录会话已过期",
    "login_session_not_found": "登录会话不存在或已过期",
    "login_session_not_verifiable": "当前登录会话不可验证",
    "login_operation_cancelled": "登录操作已取消",
    "login_start_cancelled": "登录启动已取消",
    "login_start_task_missing": "登录服务内部状态异常",
    "account_resolution_failed": "账号读取失败",
    "persistence_failed": "登录状态保存失败",
    "verification_failed": "登录验证失败",
    "account_update_failed": "账号登录状态更新失败",
    # Provider 产出的固定诊断码：原样透出，前端可显示具体失败原因。
    "profile_not_logged_in": "抖音服务端判定该账号未登录，请重新完成登录",
    "profile_probe_failed": "抖音登录态服务探针失败，请稍后重试",
    "profile_probe_http_error": "抖音登录态服务探针 HTTP 失败，请稍后重试",
    "profile_probe_invalid_json": "抖音登录态服务探针返回非预期内容",
    "profile_status_unknown": "抖音登录态服务探针返回未知状态",
    "profile_identity_missing": "抖音登录态服务探针缺少用户身份",
    "cookie_consistency_failed": "登录 Cookie 与服务端不一致，请重新登录",
    "homepage_not_available": "抖音首页未通过受保护页面校验",
    "homepage_navigation_failed": "抖音首页导航失败，请重试",
}


def _public_login_error_code(
    error_code: Any,
    *,
    unavailable: bool = False,
) -> str:
    candidate = str(error_code or "").strip()
    if candidate in _PUBLIC_LOGIN_ERROR_MESSAGES:
        return candidate
    return (
        "interactive_login_unavailable"
        if unavailable
        else "interactive_login_failed"
    )


def _public_login_error_message(error_code: str) -> str:
    if not error_code:
        return ""
    public_code = _public_login_error_code(error_code)
    return _PUBLIC_LOGIN_ERROR_MESSAGES[public_code]


def _serialize_login_session(
    session: LoginSession | Any,
) -> dict[str, Any]:
    status = str(getattr(session, "status", ""))
    raw_error_code = getattr(session, "error_code", "")
    error_code = (
        _public_login_error_code(raw_error_code)
        if raw_error_code
        else ""
    )
    response = InteractiveLoginSessionResponse(
        token=str(getattr(session, "token")),
        platform=str(getattr(session, "platform")),
        accountAlias=str(getattr(session, "account_alias")),
        accountId=getattr(session, "account_id", None),
        status=status,
        browserOpened=status in {
            "waiting_user",
            "verifying",
            "persisted",
            "confirmed",
        },
        browserProvider=str(
            getattr(session, "browser_provider", "") or ""
        ),
        authenticated=bool(
            getattr(session, "authenticated", False)
        ),
        persisted=bool(getattr(session, "persisted", False)),
        startedAt=getattr(session, "started_at"),
        expiresAt=getattr(session, "expires_at"),
        errorCode=error_code,
        errorMessage=_public_login_error_message(error_code),
    )
    return response.model_dump(mode="json", by_alias=True)


def get_pipeline_job_service(request: Request) -> PipelineJobService:
    return request.app.state.pipeline_job_service


def get_pipeline_database(request: Request):
    return request.app.state.pipeline_database


def get_acquisition_job_service(request: Request) -> AcquisitionJobService:
    return AcquisitionJobService(
        database=request.app.state.pipeline_database,
        pipeline_jobs=request.app.state.pipeline_job_service,
    )


def get_interactive_login_service(
    request: Request,
) -> InteractiveLoginService:
    return request.app.state.interactive_login_service


def _error_detail(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(
    _request: Request,
    exc: RequestValidationError,
):
    errors = exc.errors()
    message = errors[0].get("msg", "请求参数无效") if errors else "请求参数无效"
    return JSONResponse(
        status_code=422,
        content={"detail": _error_detail("request_validation_error", message)},
    )


@app.exception_handler(PipelineJobError)
async def pipeline_job_error_handler(_request: Request, exc: PipelineJobError):
    if exc.code == "job_not_found":
        status_code = 404
    elif exc.code in {
        "invalid_platform",
        "invalid_account_mode",
        "account_required",
        "auto_account_id_forbidden",
    }:
        status_code = 422
    else:
        status_code = 409
    return JSONResponse(
        status_code=status_code,
        content={"detail": _error_detail(exc.code, exc.message)},
    )


@app.exception_handler(AccountBusyError)
async def account_busy_error_handler(
    _request: Request,
    _exc: AccountBusyError,
):
    code = "account_busy"
    return JSONResponse(
        status_code=409,
        content={
            "detail": _error_detail(
                code,
                _public_login_error_message(code),
            )
        },
    )


@app.exception_handler(InteractiveLoginError)
async def interactive_login_error_handler(
    _request: Request,
    exc: InteractiveLoginError,
):
    public_code = _public_login_error_code(
        exc.code,
        unavailable=isinstance(exc, LoginUnavailableError),
    )
    if isinstance(exc, LoginSessionNotFoundError):
        status_code = 404
    elif isinstance(exc, LoginUnavailableError) or public_code in {
        "account_alias_conflict",
        "account_limit_reached",
        "login_service_closed",
        "login_session_not_verifiable",
    }:
        status_code = 409
    elif public_code in {
        "account_not_found",
        "account_identity_mismatch",
    }:
        status_code = 422
    else:
        status_code = 500
        logger.error(
            "交互式登录操作失败: code=%s",
            public_code,
        )
    return JSONResponse(
        status_code=status_code,
        content={
            "detail": _error_detail(
                public_code,
                _public_login_error_message(public_code),
            )
        },
    )


# ===== Health =====

@app.get("/")
async def root():
    return {"service": "TikTok B2B Bot API", "version": "0.1.0"}


@app.get("/api/health")
async def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


# ===== Auth =====

@app.post("/api/auth/login", response_model=TokenResponse)
async def login(req: LoginRequest):
    if req.method == "apikey":
        username = authenticate_apikey(req.password)
        if username:
            return {"access_token": create_token(username), "username": username}
        raise HTTPException(401, "API Key 无效")
    if authenticate(req.username, req.password):
        return {"access_token": create_token(req.username), "username": req.username}
    raise HTTPException(401, "用户名或密码错误")


@app.post("/api/auth/register")
async def register(req: RegisterRequest):
    db = get_db()
    from tiktok_bot_api.auth import hash_password
    with db.session() as s:
        existing = store.get_account(s, req.username)
        if existing:
            raise HTTPException(400, "用户名已存在")
        api_key = os.urandom(24).hex()
        store.create_account(s, req.username, hash_password(req.password), hash_password(api_key))
    return {"username": req.username, "api_key": api_key}


@app.get("/api/auth/me")
async def me(current_user: str = Depends(get_current_user)):
    if current_user == "guest":
        return {"username": "guest", "authenticated": False}
    return {"username": current_user, "authenticated": True}


# ===== Users =====

def _build_profile_url(platform: str, username: str) -> str:
    """根据 platform + username 兜底拼接主页链接。

    TikTok → https://www.tiktok.com/@<username>
    抖音   → https://www.douyin.com/user/<username>
    其它   → 留空，由前端自行决定展示。
    """
    u = (username or "").lstrip("@").strip()
    if not u:
        return ""
    p = (platform or "").lower()
    if p == "tiktok":
        return f"https://www.tiktok.com/@{u}"
    if p in ("douyin", "抖音"):
        return f"https://www.douyin.com/user/{u}"
    return ""


def _resolve_profile_url(user) -> str:
    """从 ORM 实例取 profile_url,空值时按 platform+username 兜底。"""
    stored = (getattr(user, "profile_url", "") or "").strip()
    if stored:
        return stored
    return _build_profile_url(user.platform, user.username)


@app.get("/api/users")
async def list_users(
    status: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    database=Depends(get_pipeline_database),
):
    """用户列表。
    - `total` 用 count_users(忽略 limit/offset),保证与 /api/users/stats 同源,
      让前端 subtitle / 翻页总页数严格一致。
    - `items` 是当前页切片(受 limit/offset 影响)。
    """
    read_model = BusinessReadModel()
    with database.session() as s:
        users = read_model.list_users(
            s,
            status=status,
            category=category,
            limit=limit,
            offset=offset,
        )
        total = read_model.count_users(
            s,
            status=status,
            category=category,
        )
        # 在 session 内取值，避免 DetachedInstanceError
        items = [
            {
                "id": u.id, "tiktok_id": u.tiktok_id, "username": u.username,
                "nickname": u.nickname, "bio": u.bio, "follower_count": u.follower_count,
                "country": u.country, "category": u.category, "status": u.status,
                "source": u.source, "source_keyword": u.source_keyword,
                "profile_url": _resolve_profile_url(u),
                "created_at": u.created_at.isoformat() if u.created_at else None,
                "updated_at": u.updated_at.isoformat() if u.updated_at else None,
                "business_source": u.business_source,
                "source_job_id": u.source_job_id,
                "qualification_status": u.qualification_status,
                "match_score": u.match_score,
                "confidence_score": u.confidence_score,
                "labels": list(u.labels),
            }
            for u in users
        ]
    return {"total": total, "items": items}


@app.get("/api/users/stats")
async def user_stats(database=Depends(get_pipeline_database)):
    """用户状态聚合统计 — 与 /api/users 返回的 total 同源,
    让前端 subtitle / KPI / 筛选 chip 计数有单一权威来源。
    """
    read_model = BusinessReadModel()
    with database.session() as s:
        result = read_model.status_counts(s)
        result["by_persona"] = read_model.persona_counts(s)
        return result


class AddUserRequest(BaseModel):
    username: str
    platform: str = "tiktok"
    bio: str = ""
    follower_count: int = 0
    country: str = ""
    category: str = "unknown"
    source: str = "manual"
    profile_url: str = ""


@app.post("/api/users")
async def add_user(req: AddUserRequest):
    """手动添加用户到数据库"""
    # 兜底：未提供 profile_url 时按平台 + username 自动拼主页链接，
    # 避免前端拿到空值后无法跳转。
    profile_url = (req.profile_url or "").strip()
    if not profile_url:
        profile_url = _build_profile_url(req.platform, req.username)

    with db.session() as s:
        user = store.add_user(
            s,
            platform=req.platform,
            tiktok_id=f"{req.platform}:{req.username}",
            username=req.username,
            bio=req.bio,
            follower_count=req.follower_count,
            country=req.country,
            category=req.category,
            status="pending",
            source=req.source,
            source_keyword="",
            profile_url=profile_url,
        )
        return {
            "id": user.id,
            "username": user.username,
            "platform": user.platform,
            "status": user.status,
            "profile_url": profile_url,
        }


@app.get("/api/users/{user_id}")
async def get_user(user_id: int):
    with db.session() as s:
        user = store.get_user(s, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    return {
        "id": user.id, "username": user.username, "status": user.status,
        "category": user.category, "bio": user.bio,
        "follower_count": user.follower_count,
        "profile_url": _resolve_profile_url(user),
        "source": user.source, "created_at": user.created_at.isoformat() if user.created_at else None,
    }


@app.get("/api/users/{username}/detail")
async def get_user_detail(username: str):
    """用户详情页数据（供 UserDetail.vue 渲染画像/策略/时间线）"""
    with db.session() as s:
        from sqlalchemy import select as sa_select
        from tiktok_bot_core.models.entities import User, Strategy, Message, Reply
        user = s.execute(sa_select(User).where(User.username == username)).scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")
        # 取策略
        strategies = list(s.execute(sa_select(Strategy).where(Strategy.user_id == user.id)).scalars().all())
        # 取消息和回复
        messages = list(s.execute(sa_select(Message).where(Message.user_id == user.id)).scalars().all())
        replies = list(s.execute(sa_select(Reply).where(Reply.user_id == user.id)).scalars().all())

        # 全部 ORM 字段读取 + 响应构造都在 session 内完成,
        # 避免 session 关闭后访问触发 DetachedInstanceError
        # (profile_url 是后续新增列,默认会 expire,故必须显式读出)。
        profile_url = _resolve_profile_url(user)
        bio = user.bio or ''
        category = user.category or 'unknown'
        country = user.country or ''
        follower_count = user.follower_count or 0
        video_count = user.video_count or 0
        like_count = user.like_count or 0

        strategy = strategies[0] if strategies else None
        strategy_body = strategy.action_plan if strategy else "暂无策略"
        reply_count = len(replies)
        reply_rate = reply_count / len(messages) if messages else 0

        # 把 messages 字典化,免去后续访问 ORM 属性
        timeline_items = [
            {
                "time": m.created_at.strftime("%m-%d %H:%M") if m.created_at else "",
                "cls": "ok" if any(r.message_id == m.id and r.sentiment == "positive" for r in replies) else "",
                "who": "私信已发送" if m.message_type == "dm" else "评论已发送",
                "desc": m.content[:80] if m.content else "",
            }
            for m in messages[:10]
        ]

        return {
            "username": username,
            "profile_url": profile_url,
            "profile": {
                "bio_zh": bio or f"@{username} — 暂无详细画像",
                "meta_zh": f"@{username} · {category} · {country or '未知区域'} · 粉丝 {follower_count:,}",
                "stats": {
                    "followers": follower_count,
                    "videos": video_count,
                    "likes": like_count,
                    "engagement_pct": round(reply_rate * 100, 1),
                },
            },
            "breakdown": [
                {"name": "Bio 商业关键词", "v": 72, "cls": "brand"},
                {"name": "视频内容相关", "v": 68, "cls": "brand"},
                {"name": "粉丝量级健康", "v": 76, "cls": "cyan"},
                {"name": "互动率真实", "v": 70, "cls": "cyan"},
                {"name": "地区匹配", "v": 80, "cls": "ok"},
                {"name": "更新频率", "v": 64, "cls": ""},
            ],
            "videos": [],
            "timeline": timeline_items,
            "strategy": {
                "body": strategy_body,
                "window": "触达窗口 9:00 – 21:00",
                "gap": "评论→私信间隔 24h",
                "expected": f"期望回复率 {reply_rate:.0%}",
                "historical": "历史同画像 —",
            },
        }


# ===== Pipeline =====

def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _serialize_pipeline_stage(stage) -> dict[str, Any]:
    return {
        "id": stage.id,
        "stage": stage.stage,
        "order": stage.stage_order,
        "status": stage.status,
        "attempt": stage.attempt,
        "result": dict(stage.result_json or {}),
        "errorMessage": stage.error_message or "",
        "startedAt": _iso(stage.started_at),
        "finishedAt": _iso(stage.finished_at),
    }


def _serialize_pipeline_job(job) -> dict[str, Any]:
    return {
        "id": job.id,
        "triggerType": job.trigger_type,
        "scheduleId": job.schedule_id,
        "platform": job.platform,
        "accountMode": job.account_mode,
        "accountId": job.account_id,
        "requestedStages": list(job.stages_json or []),
        "stages": [
            _serialize_pipeline_stage(stage)
            for stage in sorted(
                list(job.stages),
                key=lambda item: item.stage_order,
            )
        ],
        "configSnapshot": dict(job.config_snapshot_json or {}),
        "status": job.status,
        "currentStage": job.current_stage or "",
        "priority": job.priority,
        "retryOfJobId": job.retry_of_job_id,
        "errorSummary": job.error_summary or "",
        "queuedAt": _iso(job.queued_at),
        "startedAt": _iso(job.started_at),
        "finishedAt": _iso(job.finished_at),
        "createdAt": _iso(job.created_at),
        "updatedAt": _iso(job.updated_at),
    }


def _serialize_pipeline_schedule(schedule) -> dict[str, Any]:
    return {
        "id": schedule.id,
        "name": schedule.name,
        "platform": schedule.platform,
        "accountMode": schedule.account_mode,
        "accountId": schedule.account_id,
        "stages": list(schedule.stages_json or []),
        "cronExpression": schedule.cron_expression,
        "timezone": schedule.timezone,
        "enabled": bool(schedule.enabled),
        "config": dict(schedule.config_json or {}),
        "nextRunAt": _iso(schedule.next_run_at),
        "lastRunAt": _iso(schedule.last_run_at),
        "createdAt": _iso(schedule.created_at),
        "updatedAt": _iso(schedule.updated_at),
    }


@app.post("/api/pipeline/jobs", status_code=202)
async def create_pipeline_job(
    req: PipelineJobRequest,
    service: PipelineJobService = Depends(get_pipeline_job_service),
):
    job = await service.create_job(
        platform=req.platform,
        account_mode=req.account_mode,
        account_id=req.account_id,
        stages=list(req.stages),
        trigger_type="manual",
        config_snapshot=req.config_snapshot,
    )
    return {"job": _serialize_pipeline_job(job)}


@app.get("/api/pipeline/jobs")
async def list_pipeline_jobs(
    platform: Optional[PipelinePlatform] = None,
    status: Optional[PipelineJobStatus] = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: PipelineJobService = Depends(get_pipeline_job_service),
):
    jobs = service.list_jobs(
        platform=platform,
        status=status,
        limit=limit,
        offset=offset,
    )
    total = service.count_jobs(platform=platform, status=status)
    return {
        "items": [_serialize_pipeline_job(job) for job in jobs],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@app.get("/api/pipeline/jobs/{job_id}")
async def get_pipeline_job(
    job_id: str,
    service: PipelineJobService = Depends(get_pipeline_job_service),
):
    job = service.get_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail=_error_detail("job_not_found", "Pipeline 任务不存在"),
        )
    return {"job": _serialize_pipeline_job(job)}


@app.post("/api/pipeline/jobs/{job_id}/cancel")
async def cancel_pipeline_job(
    job_id: str,
    service: PipelineJobService = Depends(get_pipeline_job_service),
):
    await service.cancel_job(job_id)
    job = service.get_job(job_id)
    return {"job": _serialize_pipeline_job(job)}


@app.post("/api/pipeline/jobs/{job_id}/retry", status_code=202)
async def retry_pipeline_job(
    job_id: str,
    service: PipelineJobService = Depends(get_pipeline_job_service),
):
    job = await service.retry_job(job_id)
    return {"job": _serialize_pipeline_job(job)}


def _acquisition_not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail=_error_detail(
            "acquisition_resource_not_found",
            "获客任务或候选记录不存在",
        ),
    )


def _require_acquisition_job(session, job_id: str) -> PipelineJob:
    job = session.get(PipelineJob, job_id)
    if job is None:
        raise _acquisition_not_found()
    return job


def _require_candidate_link(
    session, job_id: str, user_id: int
) -> PipelineJobUser:
    _require_acquisition_job(session, job_id)
    link = session.get(PipelineJobUser, (job_id, user_id))
    if link is None:
        raise _acquisition_not_found()
    return link


def _validated_acquisition_options(
    model_type: type[ApiRequestModel],
    raw_value: Any,
) -> dict[str, Any]:
    """Normalize legacy JSON field-by-field without leaking unknown keys."""
    raw_mapping = raw_value if isinstance(raw_value, dict) else {}
    field_sources: dict[str, tuple[str, Any]] = {}
    for field_name, field_info in model_type.model_fields.items():
        alias = (
            field_info.alias
            if isinstance(field_info.alias, str)
            else field_name
        )
        if alias in raw_mapping:
            field_sources[field_name] = (alias, raw_mapping[alias])
        elif field_name in raw_mapping:
            field_sources[field_name] = (
                field_name,
                raw_mapping[field_name],
            )

    allowlisted = {
        source_key: value for source_key, value in field_sources.values()
    }
    try:
        return model_type.model_validate(allowlisted).model_dump(by_alias=True)
    except ValueError as exc:
        logger.warning(
            "旧获客画像 %s 含非法值，按字段回退默认值: %s",
            model_type.__name__,
            exc,
        )

    default_dump = model_type().model_dump(by_alias=True)
    if model_type is AcquisitionKeywordMix:
        return default_dump

    recovered: dict[str, Any] = {}
    for field_name, (source_key, value) in field_sources.items():
        field_info = model_type.model_fields[field_name]
        alias = (
            field_info.alias
            if isinstance(field_info.alias, str)
            else field_name
        )
        try:
            validated = model_type.model_validate({source_key: value})
            recovered[alias] = validated.model_dump(by_alias=True)[alias]
        except ValueError as exc:
            logger.warning(
                "旧获客画像 %s.%s 非法，已使用默认值: %s",
                model_type.__name__,
                alias,
                exc,
            )

    try:
        return model_type.model_validate(recovered).model_dump(by_alias=True)
    except ValueError as exc:
        if model_type is AcquisitionPreferenceConditions:
            recovered.pop("minimumYearsEstablished", None)
            recovered.pop("maximumYearsEstablished", None)
            logger.warning(
                "旧获客画像成立年限范围非法，已回退默认值: %s",
                exc,
            )
            return model_type.model_validate(recovered).model_dump(
                by_alias=True
            )
        logger.warning(
            "旧获客画像 %s 无法安全归一化，已回退整节默认值: %s",
            model_type.__name__,
            exc,
        )
        return default_dump


def _serialize_campaign(campaign: AcquisitionCampaign) -> dict[str, Any]:
    return {
        "id": campaign.id,
        "jobId": campaign.job_id,
        "platform": campaign.platform,
        "countries": list(campaign.countries or []),
        "languages": list(campaign.languages or []),
        "industries": list(campaign.industries or []),
        "products": list(campaign.products or []),
        "customerRoles": list(campaign.customer_roles or []),
        "hardConditions": _validated_acquisition_options(
            AcquisitionHardConditions,
            campaign.hard_conditions,
        ),
        "preferenceConditions": _validated_acquisition_options(
            AcquisitionPreferenceConditions,
            campaign.preference_conditions,
        ),
        "excludedTargets": list(campaign.excluded_targets or []),
        "searchBudget": _validated_acquisition_options(
            AcquisitionSearchBudget,
            campaign.search_budget,
        ),
        "keywordMix": _validated_acquisition_options(
            AcquisitionKeywordMix,
            campaign.keyword_mix,
        ),
        "createdAt": _iso(campaign.created_at),
    }


def _serialize_acquisition_keyword(
    keyword: AcquisitionKeyword,
) -> dict[str, Any]:
    return {
        "id": keyword.id,
        "jobId": keyword.job_id,
        "platform": keyword.platform,
        "text": keyword.text,
        "language": keyword.language,
        "keywordType": keyword.keyword_type,
        "source": keyword.source,
        "status": keyword.status,
        "usageCount": keyword.usage_count,
        "videoCount": keyword.video_count,
        "relevantVideoCount": keyword.relevant_video_count,
        "candidateCount": keyword.candidate_count,
        "qualifiedCount": keyword.qualified_count,
        "replyCount": keyword.reply_count,
        "businessLeadCount": keyword.business_lead_count,
        "lastUsedAt": _iso(keyword.last_used_at),
        "createdAt": _iso(keyword.created_at),
        "updatedAt": _iso(keyword.updated_at),
    }


def _serialize_candidate(
    link: PipelineJobUser,
    user: User,
    *,
    evidence_count: int = 0,
) -> dict[str, Any]:
    return {
        "jobId": link.job_id,
        "userId": link.user_id,
        "platform": user.platform,
        "username": user.username,
        "nickname": user.nickname or "",
        "bio": user.bio or "",
        "country": user.country or "",
        "followerCount": user.follower_count,
        "profileUrl": user.profile_url or "",
        "sourceStage": link.source_stage,
        "discoveryStatus": link.discovery_status,
        "qualificationStatus": link.qualification_status,
        "matchScore": link.match_score,
        "confidenceScore": link.confidence_score,
        "labels": list(link.labels_json or []),
        "priority": link.priority,
        "reviewVersion": link.review_version,
        "manuallyConfirmedAt": _iso(link.manually_confirmed_at),
        "evidenceCount": evidence_count,
        "createdAt": _iso(link.created_at),
        "updatedAt": _iso(link.updated_at),
    }


def _serialize_evidence(evidence: DiscoveryEvidence) -> dict[str, Any]:
    # Deliberately omit arbitrary metadata: browser paths, cookies and secrets
    # are not part of the public acquisition contract.
    return {
        "id": evidence.id,
        "sourceType": evidence.source_type,
        "keywordId": evidence.keyword_id,
        "keywordText": evidence.keyword_text,
        "videoId": evidence.video_id,
        "videoUrl": evidence.video_url,
        "commentId": evidence.comment_id,
        "commentUrl": evidence.comment_url,
        "authorId": evidence.author_id,
        "authorUrl": evidence.author_url,
        "rawText": evidence.raw_text,
        "translatedText": evidence.translated_text,
        "relevanceScore": evidence.relevance_score,
        "completenessScore": evidence.completeness_score,
        "collectedAt": _iso(evidence.collected_at),
    }


def _serialize_assessment(
    assessment: CandidateAssessment | None,
) -> dict[str, Any] | None:
    if assessment is None:
        return None
    # model_metadata_json is intentionally excluded because it may contain
    # provider diagnostics that do not belong in the business response.
    return {
        "id": assessment.id,
        "labels": list(assessment.labels_json or []),
        "matchScore": assessment.match_score,
        "confidenceScore": assessment.confidence_score,
        "positiveEvidence": list(assessment.positive_evidence_json or []),
        "negativeEvidence": list(assessment.negative_evidence_json or []),
        "missingFields": list(assessment.missing_fields_json or []),
        "reasoning": assessment.reasoning,
        "suggestedStatus": assessment.suggested_status,
        "modelProvider": assessment.model_provider,
        "modelName": assessment.model_name,
        "schemaVersion": assessment.schema_version,
        "createdAt": _iso(assessment.created_at),
    }


def _serialize_review_audit(
    audit: CandidateReviewAudit,
) -> dict[str, Any]:
    return {
        "id": audit.id,
        "jobId": audit.job_id,
        "userId": audit.user_id,
        "action": audit.action,
        "beforeStatus": audit.before_status,
        "afterStatus": audit.after_status,
        "labelsBefore": list(audit.labels_before_json or []),
        "labelsAfter": list(audit.labels_after_json or []),
        "priorityBefore": audit.priority_before,
        "priorityAfter": audit.priority_after,
        "reason": audit.reason,
        "operator": audit.operator,
        "createdAt": _iso(audit.created_at),
    }


@app.post("/api/acquisition/jobs", status_code=202)
async def create_atomic_acquisition_job(
    req: AcquisitionJobRequest,
    service: AcquisitionJobService = Depends(get_acquisition_job_service),
    _current_user: str = Depends(require_user),
):
    bundle = await service.create_job(
        platform=req.platform,
        account_mode=req.account_mode,
        account_id=req.account_id,
        stages=list(req.stages),
        config_snapshot=req.config_snapshot,
        campaign={
            "countries": list(req.campaign.countries),
            "languages": list(req.campaign.languages),
            "industries": list(req.campaign.industries),
            "products": list(req.campaign.products),
            "customer_roles": list(req.campaign.customer_roles),
            "hard_conditions": req.campaign.hard_conditions.model_dump(
                by_alias=True
            ),
            "preference_conditions": (
                req.campaign.preference_conditions.model_dump(
                    by_alias=True
                )
            ),
            "excluded_targets": list(req.campaign.excluded_targets),
            "search_budget": req.campaign.search_budget.model_dump(
                by_alias=True
            ),
            "keyword_mix": req.campaign.keyword_mix.model_dump(
                by_alias=True
            ),
        },
        keywords=[keyword.model_dump() for keyword in req.keywords],
    )
    return {
        "job": _serialize_pipeline_job(bundle.job),
        "campaign": _serialize_campaign(bundle.campaign),
        "keywords": [
            _serialize_acquisition_keyword(keyword)
            for keyword in bundle.keywords
        ],
    }


@app.post("/api/acquisition/jobs/{job_id}/campaign", status_code=201)
async def create_acquisition_campaign(
    job_id: str,
    req: AcquisitionCampaignRequest,
    database=Depends(get_pipeline_database),
    _current_user: str = Depends(require_user),
):
    store = AcquisitionStore()
    try:
        with database.session() as session:
            job = _require_acquisition_job(session, job_id)
            campaign = store.create_campaign(
                session,
                job_id=job_id,
                platform=job.platform,
                countries=req.countries,
                languages=req.languages,
                industries=req.industries,
                products=req.products,
                customer_roles=req.customer_roles,
                hard_conditions=req.hard_conditions.model_dump(by_alias=True),
                preference_conditions=req.preference_conditions.model_dump(
                    by_alias=True
                ),
                excluded_targets=req.excluded_targets,
                search_budget=req.search_budget.model_dump(by_alias=True),
                keyword_mix=req.keyword_mix.model_dump(by_alias=True),
            )
            return {"campaign": _serialize_campaign(campaign)}
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=_error_detail("campaign_already_exists", str(exc)),
        ) from exc


@app.get("/api/acquisition/jobs/{job_id}/campaign")
async def get_acquisition_campaign(
    job_id: str,
    database=Depends(get_pipeline_database),
    _current_user: str = Depends(require_user),
):
    store = AcquisitionStore()
    with database.session() as session:
        _require_acquisition_job(session, job_id)
        campaign = store.get_campaign(session, job_id)
        if campaign is None:
            raise _acquisition_not_found()
        return {"campaign": _serialize_campaign(campaign)}


@app.post("/api/acquisition/jobs/{job_id}/keywords", status_code=201)
async def create_acquisition_keyword(
    job_id: str,
    req: AcquisitionKeywordCreateRequest,
    database=Depends(get_pipeline_database),
    _current_user: str = Depends(require_user),
):
    store = AcquisitionStore()
    try:
        with database.session() as session:
            job = _require_acquisition_job(session, job_id)
            keyword = store.create_keyword(
                session,
                job_id=job_id,
                platform=job.platform,
                text=req.text,
                language=req.language,
                keyword_type=req.keyword_type,
                source=req.source,
                status=req.status,
            )
            return {"keyword": _serialize_acquisition_keyword(keyword)}
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail=_error_detail("keyword_already_exists", "关键词已存在"),
        ) from exc


@app.get("/api/acquisition/jobs/{job_id}/keywords")
async def list_acquisition_keywords(
    job_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    database=Depends(get_pipeline_database),
    _current_user: str = Depends(require_user),
):
    with database.session() as session:
        _require_acquisition_job(session, job_id)
        total = session.scalar(
            select(func.count(AcquisitionKeyword.id)).where(
                AcquisitionKeyword.job_id == job_id
            )
        ) or 0
        keywords = list(
            session.scalars(
                select(AcquisitionKeyword)
                .where(AcquisitionKeyword.job_id == job_id)
                .order_by(AcquisitionKeyword.id.asc())
                .offset(offset)
                .limit(limit)
            )
        )
        return {
            "items": [
                _serialize_acquisition_keyword(keyword)
                for keyword in keywords
            ],
            "total": total,
            "limit": limit,
            "offset": offset,
        }


@app.patch("/api/acquisition/jobs/{job_id}/keywords/{keyword_id}")
async def update_acquisition_keyword_stats(
    job_id: str,
    keyword_id: int,
    req: AcquisitionKeywordStatsRequest,
    database=Depends(get_pipeline_database),
    _current_user: str = Depends(require_user),
):
    store = AcquisitionStore()
    with database.session() as session:
        _require_acquisition_job(session, job_id)
        keyword = session.get(AcquisitionKeyword, keyword_id)
        if keyword is None or keyword.job_id != job_id:
            raise _acquisition_not_found()
        updated = store.update_keyword_stats(
            session,
            keyword_id,
            status=req.status,
            usage_count=req.usage_count,
            video_count=req.video_count,
            relevant_video_count=req.relevant_video_count,
            candidate_count=req.candidate_count,
            qualified_count=req.qualified_count,
            reply_count=req.reply_count,
            business_lead_count=req.business_lead_count,
            last_used_at=req.last_used_at,
        )
        return {"keyword": _serialize_acquisition_keyword(updated)}


@app.delete("/api/acquisition/jobs/{job_id}/keywords/{keyword_id}")
async def delete_acquisition_keyword(
    job_id: str,
    keyword_id: int,
    database=Depends(get_pipeline_database),
    _current_user: str = Depends(require_user),
):
    try:
        with database.session() as session:
            # SQLite has no SELECT FOR UPDATE. Acquiring the reserved write
            # lock before the first read serializes evidence insertion against
            # the reference check and delete.
            session.execute(text("BEGIN IMMEDIATE"))
            _require_acquisition_job(session, job_id)
            keyword = session.get(AcquisitionKeyword, keyword_id)
            if keyword is None or keyword.job_id != job_id:
                raise _acquisition_not_found()
            evidence_count = session.scalar(
                select(func.count(DiscoveryEvidence.id)).where(
                    DiscoveryEvidence.job_id == job_id,
                    DiscoveryEvidence.keyword_id == keyword_id,
                )
            ) or 0
            if evidence_count:
                raise HTTPException(
                    status_code=409,
                    detail=_error_detail(
                        "keyword_in_use",
                        "关键词已有发现证据引用，不能删除",
                    ),
                )
            delete_hook = getattr(
                app.state,
                "acquisition_keyword_delete_hook",
                None,
            )
            if callable(delete_hook):
                delete_hook(
                    session=session,
                    job_id=job_id,
                    keyword=keyword,
                )
            session.delete(keyword)
            session.flush()
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail=_error_detail(
                "keyword_in_use",
                "关键词已有发现证据引用，不能删除",
            ),
        ) from exc
    except OperationalError as exc:
        database_message = str(getattr(exc, "orig", exc)).lower()
        if "locked" in database_message or "busy" in database_message:
            raise HTTPException(
                status_code=409,
                detail=_error_detail(
                    "keyword_delete_conflict",
                    "关键词正在被其他任务更新，请稍后重试",
                ),
            ) from exc
        raise
    return Response(status_code=204)


@app.get("/api/acquisition/jobs/{job_id}/stage-01")
async def get_acquisition_stage_01_summary(
    job_id: str,
    database=Depends(get_pipeline_database),
    _current_user: str = Depends(require_user),
):
    with database.session() as session:
        _require_acquisition_job(session, job_id)
        total_candidates = session.scalar(
            select(func.count(PipelineJobUser.user_id)).where(
                PipelineJobUser.job_id == job_id
            )
        ) or 0
        evidence_count = session.scalar(
            select(func.count(DiscoveryEvidence.id)).where(
                DiscoveryEvidence.job_id == job_id
            )
        ) or 0
        keyword_count = session.scalar(
            select(func.count(AcquisitionKeyword.id)).where(
                AcquisitionKeyword.job_id == job_id
            )
        ) or 0
        discovery_rows = session.execute(
            select(
                PipelineJobUser.discovery_status,
                func.count(PipelineJobUser.user_id),
            )
            .where(PipelineJobUser.job_id == job_id)
            .group_by(PipelineJobUser.discovery_status)
        ).all()
        source_rows = session.execute(
            select(
                DiscoveryEvidence.source_type,
                func.count(DiscoveryEvidence.id),
            )
            .where(DiscoveryEvidence.job_id == job_id)
            .group_by(DiscoveryEvidence.source_type)
        ).all()
        return {
            "jobId": job_id,
            "summary": {
                "totalCandidates": total_candidates,
                "evidenceCount": evidence_count,
                "keywordCount": keyword_count,
                "byDiscoveryStatus": {
                    status: count for status, count in discovery_rows
                },
                "bySourceType": {
                    source_type: count for source_type, count in source_rows
                },
            },
        }


@app.get("/api/acquisition/jobs/{job_id}/stage-02")
async def get_acquisition_stage_02_summary(
    job_id: str,
    database=Depends(get_pipeline_database),
    _current_user: str = Depends(require_user),
):
    with database.session() as session:
        _require_acquisition_job(session, job_id)
        status_rows = session.execute(
            select(
                PipelineJobUser.qualification_status,
                func.count(PipelineJobUser.user_id),
            )
            .where(PipelineJobUser.job_id == job_id)
            .group_by(PipelineJobUser.qualification_status)
        ).all()
        by_status = {status: count for status, count in status_rows}
        averages = session.execute(
            select(
                func.avg(PipelineJobUser.match_score),
                func.avg(PipelineJobUser.confidence_score),
            ).where(PipelineJobUser.job_id == job_id)
        ).one()
        return {
            "jobId": job_id,
            "summary": {
                "totalCandidates": sum(by_status.values()),
                "byQualificationStatus": by_status,
                "pendingHumanReview": (
                    by_status.get("manual_review", 0)
                    + by_status.get("need_enrichment", 0)
                ),
                "averageMatchScore": averages[0],
                "averageConfidenceScore": averages[1],
            },
        }


@app.get("/api/acquisition/jobs/{job_id}/candidates")
async def list_acquisition_candidates(
    job_id: str,
    discovery_status: Optional[DiscoveryStatus] = Query(
        default=None, alias="discoveryStatus"
    ),
    qualification_status: Optional[QualificationStatus] = Query(
        default=None, alias="qualificationStatus"
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    database=Depends(get_pipeline_database),
    _current_user: str = Depends(require_user),
):
    store = AcquisitionStore()
    with database.session() as session:
        _require_acquisition_job(session, job_id)
        filters = [PipelineJobUser.job_id == job_id]
        if discovery_status is not None:
            filters.append(PipelineJobUser.discovery_status == discovery_status)
        if qualification_status is not None:
            filters.append(
                PipelineJobUser.qualification_status == qualification_status
            )
        total = session.scalar(
            select(func.count(PipelineJobUser.user_id)).where(*filters)
        ) or 0
        rows = session.execute(
            select(PipelineJobUser, User)
            .join(User, User.id == PipelineJobUser.user_id)
            .where(*filters)
            .order_by(
                PipelineJobUser.priority.asc(),
                PipelineJobUser.created_at.desc(),
            )
            .offset(offset)
            .limit(limit)
        ).all()
        user_ids = [user.id for _, user in rows]
        evidence_counts: dict[int, int] = {}
        evidence_by_user: dict[int, list[DiscoveryEvidence]] = {
            user_id: [] for user_id in user_ids
        }
        latest_assessments: dict[int, CandidateAssessment] = {}
        if user_ids:
            evidence_counts = {
                user_id: count
                for user_id, count in session.execute(
                    select(
                        DiscoveryEvidence.user_id,
                        func.count(DiscoveryEvidence.id),
                    )
                    .where(
                        DiscoveryEvidence.job_id == job_id,
                        DiscoveryEvidence.user_id.in_(user_ids),
                    )
                    .group_by(DiscoveryEvidence.user_id)
                ).all()
            }
            ranked_evidence = (
                select(
                    DiscoveryEvidence,
                    func.row_number()
                    .over(
                        partition_by=DiscoveryEvidence.user_id,
                        order_by=DiscoveryEvidence.id.desc(),
                    )
                    .label("candidate_evidence_rank"),
                )
                .where(
                    DiscoveryEvidence.job_id == job_id,
                    DiscoveryEvidence.user_id.in_(user_ids),
                )
                .subquery()
            )
            evidence_alias = aliased(
                DiscoveryEvidence,
                ranked_evidence,
            )
            evidence_rows = list(
                session.scalars(
                    select(evidence_alias)
                    .where(ranked_evidence.c.candidate_evidence_rank <= 3)
                    .order_by(
                        evidence_alias.user_id.asc(),
                        evidence_alias.id.desc(),
                    )
                )
            )
            for evidence in evidence_rows:
                evidence_by_user[evidence.user_id].append(evidence)
            ranked_assessments = (
                select(
                    CandidateAssessment,
                    func.row_number()
                    .over(
                        partition_by=CandidateAssessment.user_id,
                        order_by=CandidateAssessment.id.desc(),
                    )
                    .label("candidate_assessment_rank"),
                )
                .where(
                    CandidateAssessment.job_id == job_id,
                    CandidateAssessment.user_id.in_(user_ids),
                )
                .subquery()
            )
            assessment_alias = aliased(
                CandidateAssessment,
                ranked_assessments,
            )
            assessment_rows = list(
                session.scalars(
                    select(assessment_alias)
                    .where(
                        ranked_assessments.c.candidate_assessment_rank <= 1
                    )
                    .order_by(
                        assessment_alias.user_id.asc(),
                        assessment_alias.id.desc(),
                    )
                )
            )
            for assessment in assessment_rows:
                latest_assessments.setdefault(
                    assessment.user_id,
                    assessment,
                )
        items = []
        for link, user in rows:
            item = _serialize_candidate(
                link,
                user,
                evidence_count=evidence_counts.get(user.id, 0),
            )
            item["evidence"] = [
                _serialize_evidence(evidence)
                for evidence in evidence_by_user.get(user.id, [])
            ]
            item["latestAssessment"] = _serialize_assessment(
                latest_assessments.get(user.id)
            )
            items.append(item)
        return {
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
        }


@app.get("/api/acquisition/jobs/{job_id}/candidates/{user_id}")
async def get_acquisition_candidate(
    job_id: str,
    user_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    database=Depends(get_pipeline_database),
    _current_user: str = Depends(require_user),
):
    store = AcquisitionStore()
    with database.session() as session:
        link = _require_candidate_link(session, job_id, user_id)
        user = session.get(User, user_id)
        if user is None:
            raise _acquisition_not_found()
        evidence_total = session.scalar(
            select(func.count(DiscoveryEvidence.id)).where(
                DiscoveryEvidence.job_id == job_id,
                DiscoveryEvidence.user_id == user_id,
            )
        ) or 0
        evidence = list(
            session.scalars(
                select(DiscoveryEvidence)
                .where(
                    DiscoveryEvidence.job_id == job_id,
                    DiscoveryEvidence.user_id == user_id,
                )
                .order_by(DiscoveryEvidence.id.asc())
                .offset(offset)
                .limit(limit)
            )
        )
        return {
            "candidate": _serialize_candidate(
                link, user, evidence_count=evidence_total
            ),
            "evidence": {
                "items": [_serialize_evidence(item) for item in evidence],
                "total": evidence_total,
                "limit": limit,
                "offset": offset,
            },
            "latestAssessment": _serialize_assessment(
                store.latest_assessment(session, job_id, user_id)
            ),
        }


def _review_candidate(
    *,
    database,
    job_id: str,
    user_id: int,
    req: CandidateReviewRequest,
    operator: str,
    action: str,
    target_status: str,
) -> dict[str, Any]:
    store = AcquisitionStore()
    try:
        with database.session() as session:
            _require_candidate_link(session, job_id, user_id)
            store.transition_candidate(
                session,
                job_id=job_id,
                user_id=user_id,
                target_status=target_status,
                action=action,
                operator=operator,
                reason=req.reason,
                labels=req.labels,
                priority=req.priority,
                expected_version=req.review_version,
            )
            link = _require_candidate_link(session, job_id, user_id)
            user = session.get(User, user_id)
            if user is None:
                raise _acquisition_not_found()
            return {"candidate": _serialize_candidate(link, user)}
    except RuntimeError as exc:
        raise HTTPException(
            status_code=409,
            detail=_error_detail(
                "candidate_review_conflict",
                "候选记录已被其他操作更新，请刷新后重试",
            ),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=_error_detail("candidate_transition_invalid", str(exc)),
        ) from exc


@app.post("/api/acquisition/jobs/{job_id}/candidates/{user_id}/approve")
async def approve_acquisition_candidate(
    job_id: str,
    user_id: int,
    req: CandidateReviewRequest,
    database=Depends(get_pipeline_database),
    current_user: str = Depends(require_user),
):
    return _review_candidate(
        database=database,
        job_id=job_id,
        user_id=user_id,
        req=req,
        operator=current_user,
        action="approve",
        target_status="qualified",
    )


@app.post("/api/acquisition/jobs/{job_id}/candidates/{user_id}/reject")
async def reject_acquisition_candidate(
    job_id: str,
    user_id: int,
    req: CandidateReviewRequest,
    database=Depends(get_pipeline_database),
    current_user: str = Depends(require_user),
):
    return _review_candidate(
        database=database,
        job_id=job_id,
        user_id=user_id,
        req=req,
        operator=current_user,
        action="reject",
        target_status="rejected",
    )


@app.post(
    "/api/acquisition/jobs/{job_id}/candidates/{user_id}/request-enrichment"
)
async def request_acquisition_candidate_enrichment(
    job_id: str,
    user_id: int,
    req: CandidateReviewRequest,
    database=Depends(get_pipeline_database),
    current_user: str = Depends(require_user),
):
    return _review_candidate(
        database=database,
        job_id=job_id,
        user_id=user_id,
        req=req,
        operator=current_user,
        action="request_enrichment",
        target_status="need_enrichment",
    )


@app.post(
    "/api/acquisition/jobs/{job_id}/candidates/{user_id}/complete-enrichment"
)
async def complete_acquisition_candidate_enrichment(
    job_id: str,
    user_id: int,
    req: CandidateReviewRequest,
    database=Depends(get_pipeline_database),
    current_user: str = Depends(require_user),
):
    return _review_candidate(
        database=database,
        job_id=job_id,
        user_id=user_id,
        req=req,
        operator=current_user,
        action="complete_enrichment",
        target_status="manual_review",
    )


@app.put("/api/acquisition/jobs/{job_id}/candidates/{user_id}/labels")
async def update_acquisition_candidate_labels(
    job_id: str,
    user_id: int,
    req: CandidateLabelsRequest,
    database=Depends(get_pipeline_database),
    current_user: str = Depends(require_user),
):
    store = AcquisitionStore()
    try:
        with database.session() as session:
            _require_candidate_link(session, job_id, user_id)
            store.update_candidate_labels(
                session,
                job_id=job_id,
                user_id=user_id,
                labels=req.labels,
                operator=current_user,
                reason=req.reason,
                expected_version=req.review_version,
            )
            link = _require_candidate_link(session, job_id, user_id)
            user = session.get(User, user_id)
            if user is None:
                raise _acquisition_not_found()
            return {"candidate": _serialize_candidate(link, user)}
    except RuntimeError as exc:
        raise HTTPException(
            status_code=409,
            detail=_error_detail(
                "candidate_review_conflict",
                "候选记录已被其他操作更新，请刷新后重试",
            ),
        ) from exc


@app.get("/api/acquisition/jobs/{job_id}/candidates/{user_id}/audits")
async def list_acquisition_candidate_audits(
    job_id: str,
    user_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    database=Depends(get_pipeline_database),
    _current_user: str = Depends(require_user),
):
    with database.session() as session:
        _require_candidate_link(session, job_id, user_id)
        total = session.scalar(
            select(func.count(CandidateReviewAudit.id)).where(
                CandidateReviewAudit.job_id == job_id,
                CandidateReviewAudit.user_id == user_id,
            )
        ) or 0
        audits = list(
            session.scalars(
                select(CandidateReviewAudit)
                .where(
                    CandidateReviewAudit.job_id == job_id,
                    CandidateReviewAudit.user_id == user_id,
                )
                .order_by(CandidateReviewAudit.id.asc())
                .offset(offset)
                .limit(limit)
            )
        )
        return {
            "items": [_serialize_review_audit(audit) for audit in audits],
            "total": total,
            "limit": limit,
            "offset": offset,
        }


@app.get("/api/pipeline/capabilities")
async def pipeline_capabilities(
    service: PipelineJobService = Depends(get_pipeline_job_service),
    database=Depends(get_pipeline_database),
):
    account_snapshots: dict[str, list[SimpleNamespace]] = {
        "tiktok": [],
        "douyin": [],
    }
    with database.session() as session:
        accounts = list(
            session.scalars(
                select(TikTokAccount)
                .where(
                    TikTokAccount.platform.in_(("tiktok", "douyin")),
                    TikTokAccount.status == "logged_in",
                )
                .order_by(TikTokAccount.platform.asc(), TikTokAccount.id.asc())
            )
        )
        for account in accounts:
            account_snapshots[account.platform].append(
                SimpleNamespace(
                    **{
                        column.key: getattr(account, column.key)
                        for column in TikTokAccount.__table__.columns
                    }
                )
            )

    limits = getattr(service.concurrency, "_limits", {}) if service.concurrency else {}
    capabilities: dict[str, Any] = {}
    for platform in ("tiktok", "douyin"):
        platform_accounts = account_snapshots[platform]
        account_count = len(platform_accounts)
        if account_count == 0:
            provider_available = False
            code = "no_available_account"
            message = f"没有已登录的 {platform} 账号"
        else:
            provider = service.providers.get(platform)
            availability_results = []
            for account in platform_accounts:
                try:
                    availability_results.append(
                        await provider.check_available(account)
                    )
                except Exception as exc:
                    logger.warning(
                        "Pipeline provider capability check failed for %s "
                        "account %s: %s",
                        platform,
                        account.id,
                        exc,
                    )
                    availability_results.append(
                        SimpleNamespace(
                            available=False,
                            code="provider_check_failed",
                            message="浏览器 Provider 可用性检查失败",
                        )
                    )
            provider_available = any(
                result.available for result in availability_results
            )
            if provider_available:
                code = ""
                message = ""
            else:
                first_failure = availability_results[0]
                code = (
                    first_failure.code
                    or "browser_provider_unavailable"
                )
                message = (
                    first_failure.message
                    or "浏览器 Provider 不可用"
                )
        capabilities[platform] = {
            "available": bool(provider_available and account_count),
            "providerAvailable": provider_available,
            "provider": (
                "fingerprint" if platform == "tiktok" else "playwright"
            ),
            "code": code,
            "message": message,
            "accountCount": account_count,
            "maxConcurrency": int(limits.get(platform, 0)),
        }
    return {"platforms": capabilities}


async def _validate_schedule_request(
    req: PipelineScheduleRequest,
    service: PipelineJobService,
    *,
    preflight: bool = True,
) -> datetime:
    if preflight:
        await service.preflight_job(
            platform=req.platform,
            account_mode=req.account_mode,
            account_id=req.account_id,
        )
    try:
        return next_cron_run(
            req.cron_expression,
            req.timezone,
            datetime.utcnow(),
        )
    except (ValueError, KeyError) as exc:
        raise HTTPException(
            status_code=422,
            detail=_error_detail("invalid_schedule", str(exc)),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=_error_detail("invalid_timezone", str(exc)),
        ) from exc


@app.post("/api/pipeline/schedules", status_code=201)
async def create_pipeline_schedule(
    req: PipelineScheduleRequest,
    service: PipelineJobService = Depends(get_pipeline_job_service),
    database=Depends(get_pipeline_database),
):
    next_run_at = await _validate_schedule_request(
        req,
        service,
        preflight=req.enabled,
    )
    with database.session() as session:
        schedule = PipelineSchedule(
            name=req.name,
            platform=req.platform,
            account_mode=req.account_mode,
            account_id=req.account_id,
            stages_json=list(req.stages),
            cron_expression=req.cron_expression,
            timezone=req.timezone,
            enabled=req.enabled,
            config_json=dict(req.config_snapshot),
            next_run_at=next_run_at if req.enabled else None,
        )
        session.add(schedule)
        session.flush()
        payload = _serialize_pipeline_schedule(schedule)
    return {"schedule": payload}


@app.get("/api/pipeline/schedules")
async def list_pipeline_schedules(
    platform: Optional[PipelinePlatform] = None,
    database=Depends(get_pipeline_database),
):
    statement = select(PipelineSchedule)
    if platform is not None:
        statement = statement.where(PipelineSchedule.platform == platform)
    statement = statement.order_by(
        PipelineSchedule.created_at.desc(),
        PipelineSchedule.id.desc(),
    )
    with database.session() as session:
        schedules = list(session.scalars(statement))
        items = [_serialize_pipeline_schedule(item) for item in schedules]
    return {"items": items, "total": len(items)}


@app.put("/api/pipeline/schedules/{schedule_id}")
async def update_pipeline_schedule(
    schedule_id: int,
    req: PipelineScheduleRequest,
    service: PipelineJobService = Depends(get_pipeline_job_service),
    database=Depends(get_pipeline_database),
):
    with database.session() as session:
        schedule = session.get(PipelineSchedule, schedule_id)
        if schedule is None:
            raise HTTPException(
                status_code=404,
                detail=_error_detail(
                    "schedule_not_found",
                    "Pipeline 定时计划不存在",
                ),
            )
    next_run_at = await _validate_schedule_request(
        req,
        service,
        preflight=req.enabled,
    )
    with database.session() as session:
        schedule = session.get(PipelineSchedule, schedule_id)
        if schedule is None:
            raise HTTPException(
                status_code=404,
                detail=_error_detail(
                    "schedule_not_found",
                    "Pipeline 定时计划不存在",
                ),
            )
        schedule.name = req.name
        schedule.platform = req.platform
        schedule.account_mode = req.account_mode
        schedule.account_id = req.account_id
        schedule.stages_json = list(req.stages)
        schedule.cron_expression = req.cron_expression
        schedule.timezone = req.timezone
        schedule.enabled = req.enabled
        schedule.config_json = dict(req.config_snapshot)
        schedule.next_run_at = next_run_at if req.enabled else None
        schedule.updated_at = datetime.utcnow()
        session.flush()
        payload = _serialize_pipeline_schedule(schedule)
    return {"schedule": payload}


@app.delete(
    "/api/pipeline/schedules/{schedule_id}",
    status_code=204,
    response_class=Response,
)
async def delete_pipeline_schedule(
    schedule_id: int,
    database=Depends(get_pipeline_database),
):
    with database.session() as session:
        schedule = session.get(PipelineSchedule, schedule_id)
        if schedule is None:
            raise HTTPException(
                status_code=404,
                detail=_error_detail(
                    "schedule_not_found",
                    "Pipeline 定时计划不存在",
                ),
            )
        session.delete(schedule)
    return Response(status_code=204)


@app.post("/api/pipeline/run", status_code=202)
async def run_pipeline(
    req: PipelineRunRequest,
    service: PipelineJobService = Depends(get_pipeline_job_service),
):
    """Compatibility wrapper that only creates a durable unified job."""

    job = await service.create_job(
        platform=req.platform,
        account_mode=req.account_mode,
        account_id=req.account_id,
        stages=list(req.stages),
        trigger_type="legacy",
        config_snapshot=req.config_snapshot,
    )
    return {"job": _serialize_pipeline_job(job)}


@app.get("/api/pipeline/events")
async def pipeline_events(limit: int = 50):
    events = bus.history(limit=limit)
    return [_format_event(e) for e in events]


def _format_event(e) -> dict:
    """将内部 Event 转换为前端 Pipeline.vue 期望的格式"""
    type_val = e.type.value if hasattr(e.type, "value") else str(e.type)
    payload = e.payload if isinstance(e.payload, dict) else {}
    level, message = _event_to_message(type_val, payload)
    return {
        "type": type_val,
        "level": level,
        "message": message,
        "payload": payload,
        "timestamp": e.timestamp.isoformat(),
    }


# 事件类型 → 中文动作映射
_ACTION_MAP = {"discovered": "发现", "qualified": "合格", "rejected": "淘汰", "contacted": "触达", "replied": "已回复"}


def _event_to_message(type_val: str, payload: dict) -> tuple[str, str]:
    """根据事件类型推断 level 和生成 human-readable message"""
    if "error" in type_val:
        stage = payload.get("stage", "")
        err = payload.get("error", "")
        msg = f"Pipeline 阶段 {stage} 失败: {err}" if stage else f"错误: {err}"
        return "err", msg

    if ".done" in type_val:
        stage = type_val.split(".")[0]
        parts = [f"环节 {stage} 完成"]
        for key, label in [("total", "项"), ("qualified", "合格"), ("saved", "保存")]:
            if key in payload:
                parts.append(f"{payload[key]} {label}")
        return "ok", " · ".join(parts)

    if "user." in type_val:
        uid = payload.get("user_id", "")
        via = payload.get("via", "")
        action = type_val.split(".")[-1]
        msg = f"用户 {uid} {_ACTION_MAP.get(action, action)}"
        if via:
            msg += f" ({via})"
        return "", msg

    if type_val == "pipeline.start":
        return "", f"Pipeline 启动 · {len(payload.get('stages', []))} 个环节"

    if type_val == "pipeline.end":
        return "ok", "Pipeline 全部完成"

    msg = f"{type_val} {json.dumps(payload, ensure_ascii=False)[:80]}" if payload else type_val
    return "", msg


@app.get("/api/pipeline/overview")
async def pipeline_overview():
    """Pipeline 总览（供 Pipeline 页面渲染 6 阶段卡片 + 最近 7 天运行 + 摘要）

    当无真实 Pipeline 运行数据时返回合理的默认值。
    """
    with db.session() as s:
        # 从数据库获取基础统计
        total_users = store.count_users(s)
        qualified = store.count_users(s, "qualified")
        contacted = store.count_users(s, "contacted")
        today = datetime.utcnow().date()
        today_comments = store.count_messages(s, "comment", today)
        today_dms = store.count_messages(s, "dm", today)

    # 最近 7 天运行记录（从 event bus 推断，无事件则返回空）
    jobs = []
    events = bus.history(limit=200)
    if events:
        # 按日期分组事件，生成 job 行
        from collections import defaultdict
        day_events: dict[str, list] = defaultdict(list)
        for e in events:
            day = e.timestamp.strftime("%m-%d")
            day_events[day].append(e)
        for day in sorted(day_events.keys(), reverse=True)[:7]:
            evts = day_events[day]
            job_id = f"2026{day.replace('-', '')}-01"
            jobs.append({
                "date": day,
                "status": "完成",
                "statusCls": "ok",
                "detail": f"{len(evts)} 事件",
                "duration": "—",
                "jobId": job_id,
            })

    # 6 阶段卡片（默认 pending，无真实运行数据）
    stages = [
        {"index": 1, "key": "collect", "nameI18n": "pipeline.collect", "descI18n": "pipeline.collectDs", "ix": "01 / COLLECT", "status": "pending", "metric": str(total_users), "metricLabelI18n": "pipeline.usersStored", "extra": None, "time": "—"},
        {"index": 2, "key": "filter", "nameI18n": "pipeline.filter", "descI18n": "pipeline.filterDs", "ix": "02 / FILTER", "status": "pending", "metric": str(qualified), "metricLabelI18n": "pipeline.qualifiedCount", "extra": None, "time": "—"},
        {"index": 3, "key": "strategy", "nameI18n": "pipeline.strategy", "descI18n": "pipeline.strategyDs", "ix": "03 / STRATEGY", "status": "pending", "metric": str(qualified), "metricLabelI18n": "pipeline.strategyGenerated", "extra": None, "time": "—"},
        {"index": 4, "key": "outreach", "nameI18n": "pipeline.outreach", "descI18n": "pipeline.outreachDs", "ix": "04 / OUTREACH", "status": "pending", "metric": str(contacted), "metricLabelI18n": "pipeline.reached", "extra": None, "time": "—"},
        {"index": 5, "key": "report", "nameI18n": "pipeline.report", "descI18n": "pipeline.reportDs", "ix": "05 / REPORT", "status": "pending", "metric": "—", "metricLabelI18n": "pipeline.triggerAt", "extra": None, "time": "—"},
        {"index": 6, "key": "iterate", "nameI18n": "pipeline.iterate", "descI18n": "pipeline.iterateDs", "ix": "06 / ITERATE", "status": "pending", "metric": "—", "metricLabelI18n": "pipeline.weeklySun", "extra": None, "time": "—"},
    ]

    results = [
        {"stage": 1, "cls": "pending", "msg": "待运行"},
        {"stage": 2, "cls": "pending", "msg": "待运行"},
        {"stage": 3, "cls": "pending", "msg": "待运行"},
        {"stage": 4, "cls": "pending", "msg": "待运行"},
        {"stage": 5, "cls": "pending", "msg": "待运行"},
        {"stage": 6, "cls": "pending", "msg": "待运行"},
    ]

    summary = {
        "totalDuration": "—",
        "llmCalls": "0",
        "llmCost": "¥0",
        "browserOps": "0",
        "browserErrors": "0",
        "accountSwitches": "0",
        "commentsSent": str(today_comments),
        "dmsSent": str(today_dms),
    }

    return {"jobs": jobs, "results": results, "stages": stages, "summary": summary}


@app.get("/api/pipeline/events/stream")
async def pipeline_events_stream():
    """SSE 实时事件流"""
    async def event_stream():
        last_count = len(bus.history())
        while True:
            await asyncio.sleep(2)
            hist = bus.history()
            if len(hist) > last_count:
                for e in hist[last_count:]:
                    yield f"data: {e.type.value} {e.payload}\n\n"
                last_count = len(hist)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ===== Reports =====

@app.get("/api/reports/daily")
async def daily_report(d: Optional[str] = None):
    report_date = date.fromisoformat(d) if d else datetime.utcnow().date()
    with db.session() as s:
        reports = store.list_daily_reports(s, days=180)
        r = next((r for r in reports if r.report_date == report_date), None)
        if not r:
            return {"date": str(report_date), "message": "暂无数据"}
        return {
            "date": str(r.report_date),
            "new_users_found": r.new_users_found,
            "users_qualified": r.users_qualified,
            "users_rejected": r.users_rejected,
            "comments_sent": r.comments_sent,
            "dms_sent": r.dms_sent,
            "replies_received": r.replies_received,
            "reply_rate": r.reply_rate,
            "positive_replies": r.positive_replies,
            "business_leads": r.business_leads,
        }


@app.get("/api/reports/trend")
async def trend_report(days: int = 30):
    with db.session() as s:
        reports = list(store.list_daily_reports(s, days=days))
        return [
            {
                "date": str(r.report_date),
                "new_users": r.new_users_found,
                "reply_rate": r.reply_rate,
                "comments": r.comments_sent,
                "dms": r.dms_sent,
                "replies": r.replies_received,
                "leads": r.business_leads,
            }
            for r in reports
        ]


@app.get("/api/reports/overview")
async def reports_overview(database=Depends(get_pipeline_database)):
    """报告页子面板（转化漏斗 + 地区分布 + 情感分布）"""
    read_model = BusinessReadModel()
    with database.session() as s:
        funnel = read_model.funnel_counts(s)
        regions = read_model.region_metrics(s)
        sentiment = read_model.sentiment_metrics(s)

    total = funnel["imported"]

    def percent(count: int) -> int:
        return round(count / total * 100) if total else 0

    return {
        "funnel": [
            {"label": "imported", "count": total, "pct": 100, "color": "oklch(14% 0.012 280)"},
            {"label": "qualified", "count": funnel["qualified"], "pct": percent(funnel["qualified"]), "color": "oklch(70% 0.12 200)"},
            {"label": "contacted", "count": funnel["contacted"], "pct": percent(funnel["contacted"]), "color": "oklch(58% 0.22 350)"},
            {"label": "replied", "count": funnel["replied"], "pct": percent(funnel["replied"]), "color": "oklch(72% 0.16 75)"},
            {"label": "businessIntent", "count": funnel["businessIntent"], "pct": percent(funnel["businessIntent"]), "color": "oklch(62% 0.16 150)"},
        ],
        "regions": regions,
        "sentiment": sentiment,
    }


# ===== LLM Config =====

LLM_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
LLM_PROBE_TIMEOUT_SECONDS = 5.0
_LLM_SECRET_WRITE_LOCK = threading.Lock()


class LLMProviderCreateRequest(ApiRequestModel):
    name: str = Field(min_length=1, max_length=100)
    display_name: str = Field(
        alias="displayName", min_length=1, max_length=160
    )
    protocol: Literal["openai_chat"] = "openai_chat"
    base_url: str = Field(alias="baseUrl", min_length=1, max_length=500)
    default_model: str = Field(
        alias="defaultModel", min_length=1, max_length=200
    )
    api_key_env: str = Field(
        alias="apiKeyEnv", min_length=1, max_length=160
    )
    enabled: bool = True
    timeout_seconds: float = Field(
        default=30.0, alias="timeoutSeconds", gt=0, le=86400
    )


class LLMProviderUpdateRequest(ApiRequestModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    display_name: Optional[str] = Field(
        default=None, alias="displayName", min_length=1, max_length=160
    )
    protocol: Optional[Literal["openai_chat"]] = None
    base_url: Optional[str] = Field(
        default=None, alias="baseUrl", min_length=1, max_length=500
    )
    default_model: Optional[str] = Field(
        default=None, alias="defaultModel", min_length=1, max_length=200
    )
    api_key_env: Optional[str] = Field(
        default=None, alias="apiKeyEnv", min_length=1, max_length=160
    )
    enabled: Optional[bool] = None
    timeout_seconds: Optional[float] = Field(
        default=None, alias="timeoutSeconds", gt=0, le=86400
    )

    @model_validator(mode="after")
    def require_change(self):
        if not self.model_fields_set:
            raise ValueError("at least one provider field is required")
        for field_name in self.model_fields_set:
            if getattr(self, field_name) is None:
                raise ValueError(f"{field_name} must not be null")
        return self


class LLMRouteEntryRequest(ApiRequestModel):
    provider_id: str = Field(alias="providerId", min_length=1)
    priority: int = Field(default=100, strict=True, ge=0)
    model_override: Optional[str] = Field(
        default=None, alias="modelOverride"
    )
    enabled: bool = True


class LLMRouteUpdateRequest(ApiRequestModel):
    providers: list[LLMRouteEntryRequest]


class LLMSecretUpdateRequest(ApiRequestModel):
    api_key: str = Field(alias="apiKey", min_length=1, max_length=10000)

    @field_validator("api_key")
    @classmethod
    def reject_multiline_secret(cls, value: str) -> str:
        if "\r" in value or "\n" in value:
            raise ValueError("apiKey must not contain line breaks")
        return value


def _llm_error(exc: Exception) -> HTTPException:
    if isinstance(exc, LLMProviderNotFoundError):
        status_code, code = 404, "llm_provider_not_found"
    elif isinstance(exc, LLMProviderConflictError):
        status_code, code = 409, "llm_provider_conflict"
    elif isinstance(exc, LLMProviderInUseError):
        status_code, code = 409, "llm_provider_in_use"
    elif isinstance(exc, LLMWriteTransactionError):
        status_code, code = 409, "llm_write_conflict"
    else:
        status_code, code = 422, "invalid_llm_configuration"
    return HTTPException(
        status_code=status_code,
        detail=_error_detail(code, str(exc)),
    )


def _provider_response(provider) -> dict[str, Any]:
    return {
        "id": provider.id,
        "name": provider.name,
        "displayName": provider.display_name,
        "protocol": provider.protocol,
        "baseUrl": provider.base_url,
        "defaultModel": provider.default_model,
        "apiKeyEnv": provider.api_key_env,
        "enabled": bool(provider.enabled),
        "timeoutSeconds": float(provider.timeout_seconds),
        "configured": bool(
            resolve_llm_api_key(
                provider.api_key_env,
                env_path=LLM_ENV_PATH,
            )
        ),
        "createdAt": provider.created_at.isoformat(),
        "updatedAt": provider.updated_at.isoformat(),
    }


def _route_response(route_key: str, entries) -> dict[str, Any]:
    return {
        "routeKey": route_key,
        "providers": [
            {
                "providerId": entry.provider_id,
                "priority": entry.priority,
                "modelOverride": entry.model_override,
                "enabled": bool(entry.enabled),
            }
            for entry in entries
        ],
    }


def _provider_snapshot(provider) -> SimpleNamespace:
    return SimpleNamespace(
        id=provider.id,
        name=provider.name,
        protocol=provider.protocol,
        base_url=provider.base_url,
        default_model=provider.default_model,
        api_key_env=provider.api_key_env,
        timeout_seconds=float(provider.timeout_seconds),
        updated_at=provider.updated_at,
    )


async def _probe_llm_provider(provider) -> dict[str, Any]:
    timeout = min(
        float(provider.timeout_seconds), LLM_PROBE_TIMEOUT_SECONDS
    )
    adapter = OpenAICompatibleProvider(
        LLMProviderConfig(
            id=provider.id,
            name=provider.name,
            protocol=provider.protocol,
            base_url=provider.base_url,
            default_model=provider.default_model,
            api_key_env=provider.api_key_env,
            timeout_seconds=timeout,
            updated_at=provider.updated_at,
        )
    )
    loop = asyncio.get_running_loop()
    started = loop.time()
    try:
        await asyncio.wait_for(
            adapter.chat(
                prompt="Reply with OK.",
                system=None,
                model=provider.default_model,
            ),
            timeout=timeout,
        )
        return {
            "reachable": True,
            "latencyMs": round((loop.time() - started) * 1000, 2),
        }
    except (LLMProviderError, asyncio.TimeoutError) as exc:
        return {
            "reachable": False,
            "latencyMs": round((loop.time() - started) * 1000, 2),
            "errorCategory": getattr(exc, "category", "timeout"),
        }
    finally:
        await adapter.aclose()


def _write_llm_secret(env_var: str, value: str) -> None:
    with _LLM_SECRET_WRITE_LOCK:
        LLM_ENV_PATH.touch(exist_ok=True)
        set_key(
            str(LLM_ENV_PATH),
            env_var,
            value,
            quote_mode="always",
        )
        os.environ[env_var] = value


def _preferred_llm_env_var(providers) -> str:
    providers = list(providers)
    if not providers:
        return "LLM_API_KEY"
    enabled = [provider for provider in providers if provider.enabled]
    chosen = (enabled or providers)[0]
    return str(chosen.api_key_env or "LLM_API_KEY")


@app.get("/api/llm/providers")
async def llm_providers(
    database=Depends(get_pipeline_database),
    _current_user: str = Depends(require_user),
):
    llm_store = LLMStore()
    with database.session() as session:
        return [
            _provider_response(provider)
            for provider in llm_store.list_providers(session)
        ]


@app.post("/api/llm/providers", status_code=201)
async def create_llm_provider(
    req: LLMProviderCreateRequest,
    database=Depends(get_pipeline_database),
    _current_user: str = Depends(require_user),
):
    try:
        with database.session() as session:
            provider = LLMStore().create_provider(
                session, **req.model_dump()
            )
            return _provider_response(provider)
    except (
        ValueError,
        LLMProviderConflictError,
        LLMWriteTransactionError,
    ) as exc:
        raise _llm_error(exc) from exc


@app.put("/api/llm/providers/{provider_id}")
async def update_llm_provider(
    provider_id: str,
    req: LLMProviderUpdateRequest,
    database=Depends(get_pipeline_database),
    _current_user: str = Depends(require_user),
):
    try:
        with database.session() as session:
            provider = LLMStore().update_provider(
                session,
                provider_id,
                **req.model_dump(exclude_unset=True),
            )
            return _provider_response(provider)
    except (
        ValueError,
        LLMProviderConflictError,
        LLMProviderNotFoundError,
        LLMWriteTransactionError,
    ) as exc:
        raise _llm_error(exc) from exc


@app.delete("/api/llm/providers/{provider_id}", status_code=204)
async def delete_llm_provider(
    provider_id: str,
    database=Depends(get_pipeline_database),
    _current_user: str = Depends(require_user),
):
    try:
        with database.session() as session:
            LLMStore().delete_provider(session, provider_id)
    except (
        LLMProviderNotFoundError,
        LLMProviderInUseError,
        LLMWriteTransactionError,
    ) as exc:
        raise _llm_error(exc) from exc
    return Response(status_code=204)


@app.post("/api/llm/providers/{provider_id}/test")
async def test_llm_provider(
    provider_id: str,
    database=Depends(get_pipeline_database),
    _current_user: str = Depends(require_user),
):
    with database.session() as session:
        provider = next(
            (
                item
                for item in LLMStore().list_providers(session)
                if item.id == provider_id
            ),
            None,
        )
        if provider is None:
            raise _llm_error(
                LLMProviderNotFoundError(
                    f"LLM provider not found: {provider_id}"
                )
            )
        snapshot = _provider_snapshot(provider)
    return await _probe_llm_provider(snapshot)


@app.put("/api/llm/providers/{provider_id}/secret")
async def update_llm_provider_secret(
    provider_id: str,
    req: LLMSecretUpdateRequest,
    database=Depends(get_pipeline_database),
    _current_user: str = Depends(require_user),
):
    with database.session() as session:
        provider = next(
            (
                item
                for item in LLMStore().list_providers(session)
                if item.id == provider_id
            ),
            None,
        )
        if provider is None:
            raise _llm_error(
                LLMProviderNotFoundError(
                    f"LLM provider not found: {provider_id}"
                )
            )
        env_var = provider.api_key_env
    _write_llm_secret(env_var, req.api_key)
    await aclose_llm_router()
    return {
        "status": "ok",
        "configured": True,
        "envVar": env_var,
    }


@app.get("/api/llm/routes")
async def list_llm_routes(
    database=Depends(get_pipeline_database),
    _current_user: str = Depends(require_user),
):
    llm_store = LLMStore()
    with database.session() as session:
        return [
            _route_response(
                route_key,
                llm_store.get_route_chain(session, route_key),
            )
            for route_key in LLM_ROUTE_KEYS
        ]


@app.put("/api/llm/routes/{route_key}")
async def replace_llm_route(
    route_key: str,
    req: LLMRouteUpdateRequest,
    database=Depends(get_pipeline_database),
    _current_user: str = Depends(require_user),
):
    try:
        with database.session() as session:
            routes = LLMStore().replace_route_chain(
                session,
                route_key,
                [entry.model_dump() for entry in req.providers],
            )
            return _route_response(route_key, routes)
    except (
        LLMRouteValidationError,
        LLMWriteTransactionError,
    ) as exc:
        raise _llm_error(exc) from exc


@app.get("/api/llm/usage")
async def llm_usage(
    database=Depends(get_pipeline_database),
    _current_user: str = Depends(require_user),
):
    with database.session() as session:
        usage = LLMStore().usage_summary(session)
    return {
        "requestCount": usage["request_count"],
        "successCount": usage["success_count"],
        "failureCount": usage["failure_count"],
        "inputTokens": usage["input_tokens"],
        "outputTokens": usage["output_tokens"],
        "totalTokens": usage["total_tokens"],
        "fallbackCount": usage["fallback_count"],
        "averageLatencyMs": usage["average_latency_ms"],
    }


# ===== Stats =====

@app.get("/api/stats/dashboard")
async def dashboard_stats(database=Depends(get_pipeline_database)):
    read_model = BusinessReadModel()
    with database.session() as s:
        today = datetime.utcnow().date()
        statuses = read_model.status_counts(s)
        total = statuses["total"]
        qualified = (
            statuses["qualified"]
            + statuses["contacted"]
            + statuses["replied"]
        )
        today_new = statuses["new_today"]
        today_comments = store.count_messages(s, "comment", today)
        today_dms = store.count_messages(s, "dm", today)
        today_replies = store.count_replies(s, since_date=today)
        today_leads = store.count_business_leads(s, today)
        sent = today_comments + today_dms
        reply_rate = today_replies / sent if sent else 0

        keyword_stats = read_model.keyword_effectiveness(s)[:10]
        category_stats = [
            {"category": category, "count": count}
            for category, count in read_model.persona_counts(s).items()
        ]

    return {
        "overview": {
            "total_users": total,
            "qualified_users": qualified,
            "today_new": today_new,
            "today_comments": today_comments,
            "today_dms": today_dms,
            "today_replies": today_replies,
            "today_reply_rate": reply_rate,
            "today_leads": today_leads,
        },
        "keywords": keyword_stats,
        "categories": category_stats,
    }


# ===== Config =====

@app.get("/api/config")
async def list_config(database=Depends(get_pipeline_database)):
    # 实时读 settings 单例，避免模块级缓存读到过期 key。
    live_settings = get_settings()
    with database.session() as s:
        cfgs = {c.key: c.value for c in store.list_configs(s)}
        api_key_env = _preferred_llm_env_var(
            LLMStore().list_providers(s)
        )
    has_api_key = bool(
        resolve_llm_api_key(api_key_env, env_path=LLM_ENV_PATH)
    )
    pipeline_config = _read_typed_pipeline_config(cfgs)
    result = {
        "llm_provider": cfgs.get("llm_provider", live_settings.llm_provider),
        "llm_model": cfgs.get("llm_model", live_settings.llm_model),
        "llm_api_key": "***" if has_api_key else "",
        "has_api_key": has_api_key,
        **pipeline_config,
    }
    return result


@app.put("/api/config/pipeline")
async def update_pipeline_config(
    req: PipelineConfigRequest,
    database=Depends(get_pipeline_database),
):
    """Atomically replace the complete Pipeline runtime configuration."""

    config = req.model_dump()
    with database.session() as session:
        previous_record = store.get_config(
            session,
            "douyin_max_concurrency",
        )
        previous_raw = (
            previous_record.value
            if previous_record is not None
            else DOUYIN_MAX_CONCURRENCY_DEFAULT
        )
        try:
            previous_concurrency = _parse_douyin_max_concurrency(
                previous_raw
            )
        except ValueError:
            previous_concurrency = DOUYIN_MAX_CONCURRENCY_DEFAULT

        for key, typed_value in config.items():
            if key == "tiktok_keywords":
                persisted_value = json.dumps(
                    typed_value,
                    ensure_ascii=False,
                )
            else:
                persisted_value = str(typed_value)
            store.set_config(
                session,
                key,
                persisted_value,
                "Pipeline 批量配置",
            )

    return {
        "status": "ok",
        "config": config,
        "restartRequired": (
            previous_concurrency != req.douyin_max_concurrency
        ),
    }


@app.put("/api/config/{key}")
async def update_config(
    key: str,
    req: ConfigUpdateRequest,
    database=Depends(get_pipeline_database),
):
    value = req.value
    restart_required = False
    parsed_integer: int | None = None
    if key in PIPELINE_INTEGER_CONFIG_RANGES:
        try:
            parsed_integer = _parse_bounded_integer(key, req.value)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=_error_detail(
                    "invalid_config_value",
                    str(exc),
                ),
            ) from exc
        value = str(parsed_integer)
        restart_required = key == "douyin_max_concurrency"
    elif key == "tiktok_keywords":
        try:
            keywords = _normalize_keywords(req.value)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=_error_detail(
                    "invalid_config_value",
                    str(exc),
                ),
            ) from exc
        value = json.dumps(keywords, ensure_ascii=False)

    with database.session() as s:
        if key in PIPELINE_INTERVAL_PAIRS:
            minimum_key, maximum_key = PIPELINE_INTERVAL_PAIRS[key]
            defaults = _pipeline_config_defaults()
            pair_values: dict[str, int] = {}
            for pair_key in (minimum_key, maximum_key):
                if pair_key == key:
                    pair_values[pair_key] = parsed_integer
                    continue
                record = store.get_config(s, pair_key)
                raw_pair_value = (
                    record.value
                    if record is not None
                    else defaults[pair_key]
                )
                try:
                    pair_values[pair_key] = _parse_bounded_integer(
                        pair_key,
                        raw_pair_value,
                    )
                except ValueError:
                    pair_values[pair_key] = defaults[pair_key]
            if pair_values[minimum_key] > pair_values[maximum_key]:
                raise HTTPException(
                    status_code=422,
                    detail=_error_detail(
                        "invalid_config_value",
                        f"{minimum_key} 不能大于 {maximum_key}",
                    ),
                )
        store.set_config(s, key, value, req.description)
    return {
        "status": "ok",
        "key": key,
        "value": value,
        "restartRequired": restart_required,
    }


class ApiKeyRequest(BaseModel):
    api_key: str = Field(min_length=1, max_length=10000)

    @field_validator("api_key")
    @classmethod
    def reject_multiline_secret(cls, value: str) -> str:
        if "\r" in value or "\n" in value:
            raise ValueError("api_key must not contain line breaks")
        return value

@app.post("/api/config/apikey")
async def update_api_key(
    req: ApiKeyRequest,
    database=Depends(get_pipeline_database),
    _current_user: str = Depends(require_user),
):
    """已弃用的兼容入口；复用 Provider Secret 的安全写入边界。"""
    with database.session() as session:
        env_var = _preferred_llm_env_var(
            LLMStore().list_providers(session)
        )
    _write_llm_secret(env_var, req.api_key)
    await aclose_llm_router()
    reload_settings()
    return {
        "status": "ok",
        "configured": True,
        "envVar": env_var,
    }


# ===== Social Accounts (TikTok + 抖音) =====

class SocialAccountRequest(BaseModel):
    """添加社交账号请求（支持双平台）"""
    platform: str = "tiktok"  # "tiktok" / "douyin"
    username: str
    cookies_json: str = ""


class AccountMetadataRequest(ApiRequestModel):
    display_name: str = Field(
        default="",
        alias="displayName",
        max_length=100,
    )


class LoginQRCodeRequest(ApiRequestModel):
    """已弃用：旧 QR 请求映射为人工交互式登录。"""

    platform: PipelinePlatform = "tiktok"
    username: str = Field(min_length=1, max_length=100)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        return normalize_account_alias(value)


@app.get("/api/accounts")
async def list_accounts(platform: Optional[str] = None):
    """列出账号（可按平台过滤）"""
    # list_accounts 已返回 dict 列表，直接返回
    return get_auth_service().list_accounts(platform=platform)


@app.post("/api/accounts")
async def add_account(req: SocialAccountRequest):
    """仅添加账号元信息（不实际登录）"""
    try:
        PlatformType.parse(req.platform)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=_error_detail("invalid_platform", str(exc)),
        )
    try:
        canonical_alias = normalize_account_alias(req.username)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=_error_detail("invalid_account_alias", str(exc)),
        )
    try:
        return get_auth_service().add_account(
            req.platform,
            canonical_alias,
        )
    except AccountAliasConflictError:
        raise HTTPException(
            status_code=409,
            detail=_error_detail(
                "account_alias_conflict",
                _public_login_error_message("account_alias_conflict"),
            ),
        )
    except AccountLimitReachedError:
        raise HTTPException(
            status_code=409,
            detail=_error_detail(
                "account_limit_reached",
                _public_login_error_message("account_limit_reached"),
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.put("/api/accounts/{aid}")
async def update_account_metadata(
    aid: int,
    req: AccountMetadataRequest,
):
    """Update user-managed labels without changing browser identity keys."""

    account = get_auth_service().update_account_display_name(
        aid,
        req.display_name,
    )
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    return account


@app.put("/api/accounts/{aid}/cookies")
async def update_account_cookies(aid: int, req: SocialAccountRequest):
    """手动更新账号 cookies"""
    with db.session() as s:
        store.update_tiktok_cookies(s, aid, req.cookies_json)
    return {"status": "ok"}


@app.delete("/api/accounts/{aid}")
async def remove_account(aid: int):
    """删除账号"""
    from tiktok_bot_core.services.auth_service import get_auth_service
    get_auth_service().delete_account(aid)
    return {"status": "ok"}


def _validate_requested_login_account(
    request: InteractiveLoginSessionRequest,
    database: Any,
) -> None:
    if request.account_id is None:
        return
    with database.session() as session:
        account = store.get_tiktok_account(
            session,
            request.account_id,
        )
        try:
            stored_alias = normalize_account_alias(account.username)
        except (AttributeError, ValueError):
            stored_alias = None
        if (
            account is None
            or account.platform != request.platform
            or stored_alias != request.account_alias
        ):
            raise LoginOperationError(
                "account_identity_mismatch"
            )


@app.post(
    "/api/accounts/login-sessions",
    status_code=201,
)
async def create_interactive_login_session(
    req: InteractiveLoginSessionRequest,
    service: InteractiveLoginService = Depends(
        get_interactive_login_service
    ),
    database: Any = Depends(get_pipeline_database),
):
    """Open an isolated visible browser for user-driven platform login."""

    _validate_requested_login_account(req, database)
    session = await service.start(
        platform=req.platform,
        account_alias=req.account_alias,
    )
    return _serialize_login_session(session)


@app.get("/api/accounts/login-sessions/{token}")
async def get_interactive_login_session(
    token: str,
    service: InteractiveLoginService = Depends(
        get_interactive_login_service
    ),
):
    return _serialize_login_session(await service.status(token))


@app.post("/api/accounts/login-sessions/{token}/verify")
async def verify_interactive_login_session(
    token: str,
    service: InteractiveLoginService = Depends(
        get_interactive_login_service
    ),
):
    return _serialize_login_session(await service.verify(token))


@app.post("/api/accounts/login-sessions/{token}/cancel")
async def cancel_interactive_login_session(
    token: str,
    service: InteractiveLoginService = Depends(
        get_interactive_login_service
    ),
):
    return _serialize_login_session(await service.cancel(token))


@app.post("/api/accounts/login-qrcode", status_code=201)
async def start_login_qrcode(
    req: LoginQRCodeRequest,
    service: InteractiveLoginService = Depends(
        get_interactive_login_service
    ),
):
    """Deprecated compatibility wrapper; no QR image is produced."""

    session = await service.start(
        platform=req.platform,
        account_alias=req.username,
    )
    return {
        **_serialize_login_session(session),
        "deprecated": True,
        "session_token": session.token,
    }


@app.get("/api/accounts/login-status")
async def login_status(
    token: str = Query(...),
    service: InteractiveLoginService = Depends(
        get_interactive_login_service
    ),
):
    """Deprecated status wrapper backed by InteractiveLoginService."""

    session = await service.status(token)
    return {
        **_serialize_login_session(session),
        "deprecated": True,
    }


@app.get("/api/accounts/qrcode/{token}")
async def get_qrcode_image(token: str):
    """QR images are retired; never read a token-derived filesystem path."""

    raise HTTPException(
        status_code=410,
        detail=_error_detail(
            "qrcode_endpoint_gone",
            "二维码图片端点已停用，请使用交互式登录会话",
        ),
    )


@app.post("/api/accounts/{aid}/check-session")
async def check_account_session(aid: int):
    """检测账号 cookie 是否还有效"""
    from tiktok_bot_core.services.auth_service import get_auth_service
    with db.session() as s:
        acc = store.get_tiktok_account(s, aid)
        if not acc:
            raise HTTPException(status_code=404, detail="账号不存在")
        if acc.platform != PlatformType.DOUYIN.value:
            return {
                "id": aid,
                "status": acc.status,
                "valid": None,
                "supported": False,
                "code": "session_check_unsupported",
            }
        valid = await get_auth_service().check_session_valid(acc.platform, acc.username)
        # 更新状态
        new_status = "logged_in" if valid else "expired"
        store.update_account_status(s, aid, new_status)
    return {"id": aid, "status": new_status, "valid": valid, "supported": True}


# ===== Stats/Charts =====

@app.get("/api/stats/wordcloud")
async def wordcloud_data(
    lang: str = Query(default="en"),
    limit: int = Query(default=100, ge=1, le=200),
    database=Depends(get_pipeline_database),
):
    # ``lang`` is retained for the existing component/API contract.  The
    # current persisted keyword schema has language per acquisition job but no
    # reliable language value for legacy keywords, so the unified cloud does
    # not pretend to filter incomplete data.
    _ = lang
    with database.session() as s:
        kw_stats = BusinessReadModel().keyword_effectiveness(s)
    kw_stats.sort(
        key=lambda row: (
            -int(row["total"]),
            str(row["keyword"]).casefold(),
            str(row["keyword"]),
        )
    )
    return [
        {"word": row["keyword"], "count": row["total"]}
        for row in kw_stats[:limit]
    ]


# ===== Lead Discovery =====

@app.get("/api/leads/search")
async def search_leads(
    keyword: str = Query(..., min_length=1),
    limit: int = Query(default=20, ge=1, le=100),
    database=Depends(get_pipeline_database),
):
    """公开搜索潜在 B2B 客户（不需登录）

    匹配公开资料、legacy 来源词和当前获客任务证据词，按业务相关度、
    粉丝数和用户 ID 稳定排序。若数据库无匹配，返回空列表。
    """
    normalized_keyword = keyword.strip()
    if not normalized_keyword:
        raise HTTPException(
            status_code=422,
            detail=_error_detail(
                "invalid_keyword",
                "keyword 不能为空",
            ),
        )
    with database.session() as s:
        return BusinessReadModel().search_leads(
            s,
            keyword=normalized_keyword,
            limit=limit,
        )
