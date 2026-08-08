"""Phase 4 双平台 + 真实登录测试

测试 platforms.py 抽象和 auth_service.py 元数据操作（不实际启动浏览器）。
"""

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import inspect, text

from tiktok_bot_core.storage.database import Database
from tiktok_bot_core.platforms import (
    PlatformType, get_platform, list_platforms, TIKTOK, DOUYIN
)


_SENSITIVE_LOGIN_KEYS = {
    "cookies",
    "cookies_json",
    "storage_state",
    "storage_state_path",
    "profile_path",
    "browser",
    "browser_profile_id",
    "browserProfileId",
    "context",
    "page",
    "qrcode_url",
    "qrcode_path",
    "qrcode_payload",
}


def _assert_no_sensitive_login_keys(payload):
    if isinstance(payload, dict):
        assert not (_SENSITIVE_LOGIN_KEYS & set(payload))
        for value in payload.values():
            _assert_no_sensitive_login_keys(value)
    elif isinstance(payload, list):
        for value in payload:
            _assert_no_sensitive_login_keys(value)


def _fake_login_session(
    *,
    token="token-1",
    status="waiting_user",
    error_code="",
    error_message="",
):
    return SimpleNamespace(
        token=token,
        platform="douyin",
        account_alias="dy_01",
        account_id=7,
        status=status,
        browser_provider="playwright",
        browser_profile_id="douyin-profile-7",
        started_at=datetime(2026, 7, 28, 8, 0, tzinfo=timezone.utc),
        expires_at=datetime(2026, 7, 28, 8, 5, tzinfo=timezone.utc),
        error_code=error_code,
        error_message=error_message,
        authenticated=status in {"persisted", "confirmed"},
        persisted=status in {"persisted", "confirmed"},
    )


class _FakeInteractiveLoginService:
    def __init__(self):
        self.session = _fake_login_session()
        self.calls = []
        self.errors = {}
        self.closed = False

    async def start(self, *, platform, account_alias):
        self.calls.append(("start", platform, account_alias))
        if "start" in self.errors:
            raise self.errors["start"]
        return self.session

    async def status(self, token):
        self.calls.append(("status", token))
        if "status" in self.errors:
            raise self.errors["status"]
        return self.session

    async def verify(self, token):
        self.calls.append(("verify", token))
        if "verify" in self.errors:
            raise self.errors["verify"]
        self.session = _fake_login_session(
            token=token,
            status="confirmed",
        )
        return self.session

    async def cancel(self, token):
        self.calls.append(("cancel", token))
        if "cancel" in self.errors:
            raise self.errors["cancel"]
        self.session = _fake_login_session(
            token=token,
            status="cancelled",
            error_code="login_cancelled",
            error_message="sensitive C:/private/profile cookies=secret",
        )
        return self.session

    async def aclose(self):
        self.calls.append(("aclose",))
        self.closed = True


@pytest.fixture
async def interactive_login_api_client():
    from tiktok_bot_api.main import app

    missing = object()
    original_service = getattr(
        app.state,
        "interactive_login_service",
        missing,
    )
    fake = _FakeInteractiveLoginService()
    app.state.interactive_login_service = fake
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        yield client, fake
    if original_service is missing:
        delattr(app.state, "interactive_login_service")
    else:
        app.state.interactive_login_service = original_service


# ===== Platforms =====

def test_platform_type_parse_aliases():
    """PlatformType.parse 应接受多种别名"""
    assert PlatformType.parse("tiktok") == PlatformType.TIKTOK
    assert PlatformType.parse("TikTok") == PlatformType.TIKTOK
    assert PlatformType.parse("tk") == PlatformType.TIKTOK
    assert PlatformType.parse("douyin") == PlatformType.DOUYIN
    assert PlatformType.parse("dy") == PlatformType.DOUYIN
    assert PlatformType.parse("抖音") == PlatformType.DOUYIN
    assert PlatformType.parse(PlatformType.TIKTOK) == PlatformType.TIKTOK


def test_platform_type_parse_invalid_raises():
    """未知平台应抛 ValueError"""
    with pytest.raises(ValueError, match="未知平台"):
        PlatformType.parse("facebook")


def test_tiktok_platform_urls():
    """TikTok URL 模板正确"""
    pf = get_platform("tiktok")
    assert "tiktok.com" in pf.home_url
    assert "search?q=" in pf.search_user_url_tpl
    assert "/@{username}" in pf.user_profile_url_tpl
    # 中文关键词自动 URL 编码
    assert "%E6%91%87" not in pf.search_user_url("中文") or "=" in pf.search_user_url("中文")
    # username URL 也正确编码
    url = pf.user_profile_url("alice_中文")
    assert "alice" in url


def test_douyin_platform_urls():
    """抖音 URL 模板正确"""
    pf = get_platform("douyin")
    assert "douyin.com" in pf.home_url
    # 抖音搜索是 path 参数
    assert "{kw}" in pf.search_user_url_tpl
    assert "/user/{username}" in pf.user_profile_url_tpl


def test_platform_selectors_required_keys():
    """两个平台都应有必需的 selector"""
    required = ["user_card", "user_link", "comment_input", "message_btn"]
    for pf in [TIKTOK, DOUYIN]:
        for key in required:
            assert key in pf.selectors, f"{pf.name} 缺少 selector: {key}"


def test_list_platforms():
    """至少支持 2 个平台"""
    platforms = list_platforms()
    names = [p.name for p in platforms]
    assert "tiktok" in names
    assert "douyin" in names


# ===== Database platform support =====

@pytest.fixture
def db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = Path(f.name)
    database = Database(f"sqlite:///{path}")
    database.init()

    # 把全局 db 单例切到我们这个测试 DB
    from tiktok_bot_core import storage
    old_db = storage.database._db_instance
    storage.database._db_instance = database

    # 同样重置 AuthService 单例（避免缓存了旧 db 引用）
    import tiktok_bot_core.services.auth_service as auth_mod
    old_auth = auth_mod._auth_service
    auth_mod._auth_service = None

    yield database

    storage.database._db_instance = old_db
    auth_mod._auth_service = old_auth
    database.engine.dispose()
    import gc; gc.collect()
    try:
        path.unlink()
    except PermissionError:
        pass


def test_db_add_user_with_platform(db):
    """User 表存 platform 字段"""
    from tiktok_bot_core.models.entities import User

    with db.session() as s:
        u = User(
            platform="douyin",
            tiktok_id="douyin:alice",
            username="alice",
            bio="抖音用户",
            follower_count=100,
        )
        s.add(u)
        s.commit()

        from sqlalchemy import select
        loaded = s.execute(select(User)).scalar_one()
        assert loaded.platform == "douyin"
        assert loaded.username == "alice"
        assert loaded.follower_count == 100


def test_db_add_tiktok_account_with_platform(db):
    """tiktok_accounts 表存 platform 字段"""
    from tiktok_bot_core.storage.sqlite_store import SqliteStore
    store = SqliteStore()

    with db.session() as s:
        store.add_tiktok_account(s, platform="douyin", username="dy_acc_01", status="logged_in")
        store.add_tiktok_account(s, platform="tiktok", username="tk_acc_01", status="logged_in")

        dy_accounts = store.get_tiktok_accounts(s, platform="douyin")
        tk_accounts = store.get_tiktok_accounts(s, platform="tiktok")

        assert len(dy_accounts) == 1
        assert dy_accounts[0].username == "dy_acc_01"
        assert dy_accounts[0].platform == "douyin"
        assert len(tk_accounts) == 1
        assert tk_accounts[0].username == "tk_acc_01"
        assert tk_accounts[0].platform == "tiktok"


def test_db_account_unique_per_platform(db):
    """同名账号在不同平台应各占一条记录"""
    from tiktok_bot_core.storage.sqlite_store import SqliteStore
    store = SqliteStore()

    with db.session() as s:
        store.add_tiktok_account(s, platform="tiktok", username="alice")
        store.add_tiktok_account(s, platform="douyin", username="alice")

        accounts = store.get_tiktok_accounts(s)
        # 在 session 内取值
        pairs = [(a.platform, a.username) for a in accounts]
    assert ("tiktok", "alice") in pairs
    assert ("douyin", "alice") in pairs


def test_db_account_status_update(db):
    """账号状态可更新"""
    from tiktok_bot_core.storage.sqlite_store import SqliteStore
    store = SqliteStore()

    with db.session() as s:
        a = store.add_tiktok_account(s, platform="tiktok", username="tmp", status="pending")
        aid = a.id
        store.update_account_status(s, aid, "logged_in")

        acc = store.get_tiktok_account(s, aid)
        status = acc.status
    assert status == "logged_in"


def test_db_get_active_account_per_platform(db):
    """每个平台应能获取该平台下已登录的账号"""
    from tiktok_bot_core.storage.sqlite_store import SqliteStore
    store = SqliteStore()

    with db.session() as s:
        store.add_tiktok_account(s, platform="douyin", username="dy_x", status="logged_in")
        store.add_tiktok_account(s, platform="tiktok", username="tk_x", status="pending")
        store.add_tiktok_account(s, platform="tiktok", username="tk_y", status="logged_in")

        dy = store.get_active_account(s, "douyin")
        tk = store.get_active_account(s, "tiktok")
        dy_platform = dy.platform if dy else None
        tk_platform = tk.platform if tk else None

    assert dy_platform == "douyin"
    assert tk_platform == "tiktok"


def test_social_account_has_persistent_auth_fields(db):
    columns = {
        column["name"]
        for column in inspect(db.engine).get_columns("tiktok_accounts")
    }

    assert {
        "storage_state_path",
        "profile_path",
        "auth_verified_at",
        "auth_version",
    } <= columns


def test_auth_migration_is_idempotent(tmp_path):
    path = tmp_path / "legacy-auth.db"
    database = Database(f"sqlite:///{path}")
    with database.engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE tiktok_accounts ("
            "id INTEGER PRIMARY KEY, "
            "platform VARCHAR(20), "
            "username VARCHAR(100)"
            ")"
        ))

    database.init()
    database.init()

    columns = {
        column["name"]
        for column in inspect(database.engine).get_columns("tiktok_accounts")
    }
    assert {
        "storage_state_path",
        "profile_path",
        "auth_verified_at",
        "auth_version",
    } <= columns
    database.engine.dispose()


def test_persistent_auth_fields_survive_upsert_when_omitted(db):
    from tiktok_bot_core.storage.sqlite_store import SqliteStore

    verified_at = datetime(2026, 7, 28, 12, 30)
    store = SqliteStore()
    with db.session() as session:
        account = store.add_tiktok_account(
            session,
            platform="douyin",
            username="persistent-account",
            storage_state_path="auth_states/douyin/42.json",
            profile_path="browser_profiles/douyin/42",
            auth_verified_at=verified_at,
            auth_version=2,
        )
        account_id = account.id
        store.add_tiktok_account(
            session,
            platform="douyin",
            username="persistent-account",
            status="logged_in",
        )

    with db.session() as session:
        account = store.get_tiktok_account(session, account_id)
        assert account.storage_state_path == "auth_states/douyin/42.json"
        assert account.profile_path == "browser_profiles/douyin/42"
        assert account.auth_verified_at == verified_at
        assert account.auth_version == 2


def test_auth_paths_are_account_scoped_and_relative(tmp_path):
    from tiktok_bot_core.services.auth_service import build_auth_paths

    paths = build_auth_paths(
        data_root=tmp_path,
        platform="douyin",
        account_key="42",
    )

    assert paths.profile_dir == (
        tmp_path
        / "browser_profiles"
        / "douyin"
        / "42-73475cb40a56"
    )
    assert paths.storage_state == (
        tmp_path
        / "auth_states"
        / "douyin"
        / "42-73475cb40a56.json"
    )


def test_auth_paths_keep_unicode_alias_readable(tmp_path):
    from tiktok_bot_core.services.auth_service import build_auth_paths

    paths = build_auth_paths(
        data_root=tmp_path,
        platform="douyin",
        account_key="中文账号",
    )

    assert paths.profile_dir.name.startswith("中文账号-")
    assert paths.storage_state.stem == paths.profile_dir.name


def test_auth_paths_distinguish_sanitization_collisions(tmp_path):
    from tiktok_bot_core.services.auth_service import build_auth_paths

    slash = build_auth_paths(tmp_path, "douyin", "a/b")
    question = build_auth_paths(tmp_path, "douyin", "a?b")

    assert slash.profile_dir != question.profile_dir
    assert slash.profile_dir.name.startswith("a-b-")
    assert question.profile_dir.name.startswith("a-b-")


def test_auth_paths_normalize_equivalent_account_keys(tmp_path):
    from tiktok_bot_core.services.auth_service import build_auth_paths

    ascii_key = build_auth_paths(tmp_path, "douyin", "A")
    full_width_key = build_auth_paths(tmp_path, "douyin", " Ａ ")

    assert ascii_key == full_width_key


def test_auth_paths_repr_hides_private_paths(tmp_path):
    from tiktok_bot_core.services.auth_service import build_auth_paths

    paths = build_auth_paths(tmp_path, "douyin", "account")

    rendered = repr(paths)
    assert paths.profile_dir.as_posix() not in rendered
    assert paths.storage_state.as_posix() not in rendered


def test_auth_paths_sanitize_account_key_traversal(tmp_path):
    from tiktok_bot_core.services.auth_service import build_auth_paths

    paths = build_auth_paths(
        data_root=tmp_path,
        platform="tiktok",
        account_key="../../outside",
    )

    assert paths.profile_dir.is_relative_to(tmp_path / "browser_profiles" / "tiktok")
    assert paths.storage_state.is_relative_to(tmp_path / "auth_states" / "tiktok")
    assert ".." not in paths.profile_dir.parts
    assert ".." not in paths.storage_state.parts


@pytest.mark.parametrize("platform", ["facebook", "../douyin", ""])
def test_auth_paths_reject_unsupported_platform(tmp_path, platform):
    from tiktok_bot_core.services.auth_service import build_auth_paths

    with pytest.raises(ValueError, match="platform"):
        build_auth_paths(
            data_root=tmp_path,
            platform=platform,
            account_key="42",
        )


@pytest.mark.parametrize("account_key", ["", "   ", "\t\n"])
def test_auth_paths_reject_empty_account_key(tmp_path, account_key):
    from tiktok_bot_core.services.auth_service import build_auth_paths

    with pytest.raises(ValueError, match="account key"):
        build_auth_paths(
            data_root=tmp_path,
            platform="douyin",
            account_key=account_key,
        )


def test_playwright_minimum_version_supports_indexed_db_storage_state():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text(encoding="utf-8")

    assert '"playwright>=1.51.0"' in pyproject


# ===== Auth Service 元数据 =====

def test_auth_service_add_and_list(db):
    """AuthService: 添加账号 + 列表"""
    from tiktok_bot_core.services.auth_service import get_auth_service

    svc = get_auth_service()
    # add_account 返回的 ORM 对象需在 session 内取值
    with db.session() as s:
        from tiktok_bot_core.storage.sqlite_store import SqliteStore
        store = SqliteStore()
        a1 = store.add_tiktok_account(s, platform="tiktok", username="marketing_01", status="pending")
        a2 = store.add_tiktok_account(s, platform="douyin", username="marketing_02", status="pending")
        a1_id, a1_platform = a1.id, a1.platform
        a2_id, a2_platform = a2.id, a2.platform

    assert a1_id is not None
    assert a2_id is not None
    assert a1_platform == "tiktok"
    assert a2_platform == "douyin"

    with db.session() as s:
        from tiktok_bot_core.storage.sqlite_store import SqliteStore
        store = SqliteStore()
        accounts = store.get_tiktok_accounts(s)
        usernames = {a.username for a in accounts}
    assert "marketing_01" in usernames
    assert "marketing_02" in usernames


def test_auth_service_add_invalid_platform(db):
    """无效平台应抛 ValueError"""
    from tiktok_bot_core.services.auth_service import get_auth_service

    svc = get_auth_service()
    with pytest.raises(ValueError):
        svc.add_account("facebook", "test")


def test_qr_login_ignores_visitor_cookies():
    """Visitor cookies must never turn a QR session into a logged-in session."""
    import asyncio
    from tiktok_bot_core.services.auth_service import AuthService

    service = object.__new__(AuthService)
    visitor_context = MagicMock()
    visitor_context.cookies = AsyncMock(return_value=[
        {"name": "ttwid", "value": "visitor"},
        {"name": "msToken", "value": "visitor"},
        {"name": "uid_tt", "value": "visitor"},
    ])

    assert asyncio.run(service._check_login_cookies(visitor_context, "tiktok")) is False


@pytest.mark.parametrize("marker", ["sessionid", "sessionid_ss", "sid_guard"])
def test_qr_login_accepts_reliable_authenticated_cookie(marker):
    """Any non-empty reliable session cookie is sufficient authentication."""
    import asyncio
    from tiktok_bot_core.services.auth_service import AuthService

    service = object.__new__(AuthService)
    authenticated_context = MagicMock()
    authenticated_context.cookies = AsyncMock(return_value=[
        {"name": marker, "value": "authenticated-session"},
    ])

    assert asyncio.run(
        service._check_login_cookies(authenticated_context, "tiktok")
    ) is True


def test_qr_login_rejects_empty_authenticated_cookie():
    import asyncio
    from tiktok_bot_core.services.auth_service import AuthService

    service = object.__new__(AuthService)
    context = MagicMock()
    context.cookies = AsyncMock(return_value=[
        {"name": "sessionid", "value": ""},
    ])

    assert asyncio.run(service._check_login_cookies(context, "tiktok")) is False


def test_qr_login_is_confirmed_only_after_persistence():
    """The polling API exposes verifying until the authenticated account is saved."""
    import time
    from tiktok_bot_core.services.auth_service import AuthService

    service = object.__new__(AuthService)
    service._active_sessions = {
        "qr": {
            "started_at": time.time(),
            "platform": "douyin",
            "username": "account_01",
            "logged_in": True,
            "persisted": False,
            "status": "verifying",
        }
    }

    assert service.check_login("qr")["status"] == "verifying"
    service._active_sessions["qr"]["persisted"] = True
    assert service.check_login("qr")["status"] == "confirmed"


# ===== Interactive login HTTP API =====


@pytest.mark.asyncio
async def test_create_interactive_login_session_is_201_and_safe(
    interactive_login_api_client,
):
    client, service = interactive_login_api_client

    response = await client.post(
        "/api/accounts/login-sessions",
        json={
            "platform": "douyin",
            "accountAlias": "dy_01",
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["token"] == "token-1"
    assert payload["platform"] == "douyin"
    assert payload["accountAlias"] == "dy_01"
    assert payload["accountId"] == 7
    assert payload["status"] == "waiting_user"
    assert payload["browserOpened"] is True
    assert payload["expiresAt"].endswith("Z")
    assert service.calls == [("start", "douyin", "dy_01")]
    _assert_no_sensitive_login_keys(payload)


@pytest.mark.asyncio
async def test_get_verify_and_cancel_interactive_login_session(
    interactive_login_api_client,
):
    client, service = interactive_login_api_client

    fetched = await client.get(
        "/api/accounts/login-sessions/token-1"
    )
    verified = await client.post(
        "/api/accounts/login-sessions/token-1/verify"
    )
    cancelled = await client.post(
        "/api/accounts/login-sessions/token-1/cancel"
    )

    assert fetched.status_code == 200
    assert fetched.json()["status"] == "waiting_user"
    assert verified.status_code == 200
    assert verified.json()["status"] == "confirmed"
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["errorMessage"] == "登录会话已取消"
    assert service.calls == [
        ("status", "token-1"),
        ("verify", "token-1"),
        ("cancel", "token-1"),
    ]
    for response in (fetched, verified, cancelled):
        _assert_no_sensitive_login_keys(response.json())


@pytest.mark.asyncio
async def test_legacy_login_endpoints_use_interactive_service_without_qr(
    interactive_login_api_client,
    monkeypatch,
):
    from tiktok_bot_api import main as api_main

    client, service = interactive_login_api_client
    legacy_auth = MagicMock()
    legacy_auth.start_qrcode_login.side_effect = AssertionError(
        "legacy QR login must not run"
    )
    legacy_auth._qrcode_login_task.side_effect = AssertionError(
        "legacy background task must not run"
    )
    legacy_auth.get_qrcode_path.side_effect = AssertionError(
        "legacy QR files must not be read"
    )
    monkeypatch.setattr(
        api_main,
        "get_auth_service",
        lambda: legacy_auth,
    )

    created = await client.post(
        "/api/accounts/login-qrcode",
        json={"platform": "douyin", "username": "dy_01"},
    )
    status = await client.get(
        "/api/accounts/login-status",
        params={"token": "token-1"},
    )
    image = await client.get("/api/accounts/qrcode/token-1")

    assert created.status_code == 201
    assert created.json()["deprecated"] is True
    assert created.json()["session_token"] == "token-1"
    assert status.status_code == 200
    assert status.json()["deprecated"] is True
    assert image.status_code == 410
    assert service.calls == [
        ("start", "douyin", "dy_01"),
        ("status", "token-1"),
    ]
    legacy_auth.start_qrcode_login.assert_not_called()
    legacy_auth._qrcode_login_task.assert_not_called()
    legacy_auth.get_qrcode_path.assert_not_called()
    _assert_no_sensitive_login_keys(created.json())
    _assert_no_sensitive_login_keys(status.json())
    _assert_no_sensitive_login_keys(image.json())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "error_factory", "expected_status", "code"),
    [
        (
            "get",
            "/api/accounts/login-sessions/missing",
            lambda: __import__(
                "tiktok_bot_core.services.interactive_login",
                fromlist=["LoginSessionNotFoundError"],
            ).LoginSessionNotFoundError(),
            404,
            "login_session_not_found",
        ),
        (
            "post",
            "/api/accounts/login-sessions",
            lambda: __import__(
                "tiktok_bot_core.services.account_leases",
                fromlist=["AccountBusyError"],
            ).AccountBusyError(
                platform="douyin",
                account_key="7",
                owner="login:new",
                current_owner="pipeline:old",
            ),
            409,
            "account_busy",
        ),
        (
            "post",
            "/api/accounts/login-sessions",
            lambda: __import__(
                "tiktok_bot_core.services.interactive_login",
                fromlist=["LoginUnavailableError"],
            ).LoginUnavailableError(
                "fingerprint_provider_unavailable",
            ),
            409,
            "fingerprint_provider_unavailable",
        ),
        (
            "post",
            "/api/accounts/login-sessions/token-1/verify",
            lambda: __import__(
                "tiktok_bot_core.services.interactive_login",
                fromlist=["LoginOperationError"],
            ).LoginOperationError(
                "persistence_failed",
                "C:/private/auth.json cookies=secret",
            ),
            500,
            "persistence_failed",
        ),
        (
            "post",
            "/api/accounts/login-sessions",
            lambda: __import__(
                "tiktok_bot_core.services.interactive_login",
                fromlist=["LoginOperationError"],
            ).LoginOperationError(
                "login_service_closed",
            ),
            409,
            "login_service_closed",
        ),
        (
            "post",
            "/api/accounts/login-sessions",
            lambda: __import__(
                "tiktok_bot_core.services.interactive_login",
                fromlist=["LoginOperationError"],
            ).LoginOperationError(
                "account_not_found",
            ),
            422,
            "account_not_found",
        ),
    ],
)
async def test_interactive_login_error_mapping_is_stable_and_safe(
    interactive_login_api_client,
    method,
    path,
    error_factory,
    expected_status,
    code,
):
    client, service = interactive_login_api_client
    operation = (
        "status"
        if method == "get"
        else "verify"
        if path.endswith("/verify")
        else "start"
    )
    service.errors[operation] = error_factory()
    request_kwargs = {}
    if path == "/api/accounts/login-sessions":
        request_kwargs["json"] = {
            "platform": (
                "tiktok"
                if code == "fingerprint_provider_unavailable"
                else "douyin"
            ),
            "accountAlias": "dy_01",
        }

    response = await getattr(client, method)(path, **request_kwargs)

    assert response.status_code == expected_status
    assert response.json()["detail"]["code"] == code
    assert "private" not in response.text.lower()
    assert "secret" not in response.text.lower()
    _assert_no_sensitive_login_keys(response.json())


@pytest.mark.asyncio
async def test_interactive_login_api_normalizes_malicious_external_codes(
    interactive_login_api_client,
    caplog,
):
    from tiktok_bot_core.services.interactive_login import (
        LoginUnavailableError,
    )

    client, service = interactive_login_api_client
    malicious = r"C:\secret\cookies=sessionid=private-value"
    service.errors["start"] = LoginUnavailableError(
        malicious,
        malicious,
    )

    response = await client.post(
        "/api/accounts/login-sessions",
        json={
            "platform": "douyin",
            "accountAlias": "dy_01",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == (
        "interactive_login_unavailable"
    )
    assert "secret" not in response.text.lower()
    assert "sessionid" not in response.text.lower()
    assert "secret" not in caplog.text.lower()
    assert "sessionid" not in caplog.text.lower()


@pytest.mark.asyncio
async def test_login_session_response_normalizes_malicious_stored_code(
    interactive_login_api_client,
):
    client, service = interactive_login_api_client
    malicious = r"C:\secret\cookies=sessionid=private-value"
    service.session = _fake_login_session(
        status="failed",
        error_code=malicious,
        error_message=malicious,
    )

    response = await client.get(
        "/api/accounts/login-sessions/token-1"
    )

    assert response.status_code == 200
    assert response.json()["errorCode"] == "interactive_login_failed"
    assert response.json()["errorMessage"] == "交互式登录操作失败"
    assert "secret" not in response.text.lower()
    assert "sessionid" not in response.text.lower()


@pytest.mark.asyncio
async def test_login_session_response_passes_through_provider_diagnostic_codes(
    interactive_login_api_client,
):
    """Provider 产出的固定诊断码必须能传到前端，否则用户看不到失败原因。"""

    client, service = interactive_login_api_client
    passthrough_codes = [
        "profile_not_logged_in",
        "profile_probe_failed",
        "profile_probe_http_error",
        "profile_probe_invalid_json",
        "profile_status_unknown",
        "profile_identity_missing",
        "cookie_consistency_failed",
        "homepage_not_available",
        "homepage_navigation_failed",
    ]
    for code in passthrough_codes:
        service.session = _fake_login_session(
            status="waiting_user",
            error_code=code,
        )
        response = await client.get(
            "/api/accounts/login-sessions/token-1"
        )
        assert response.status_code == 200
        assert response.json()["errorCode"] == code, code
        assert "sessionid" not in response.text.lower()


@pytest.mark.asyncio
async def test_service_normalizes_malicious_provider_availability_code(
    caplog,
):
    from tiktok_bot_core.browser.providers import BrowserAvailability
    from tiktok_bot_core.services.account_leases import AccountLeaseManager
    from tiktok_bot_core.services.interactive_login import (
        InteractiveLoginService,
        LoginUnavailableError,
    )

    malicious = r"C:\secret\cookies=sessionid=private-value"

    class UnavailableProvider:
        async def check_interactive_available(self, _account):
            return BrowserAvailability(
                available=False,
                code=malicious,
                message=malicious,
            )

    class UnavailableExceptionProvider:
        async def check_interactive_available(self, _account):
            raise LoginUnavailableError(malicious, malicious)

    class AdapterError(RuntimeError):
        code = malicious

    class AdapterExceptionProvider:
        async def check_interactive_available(self, _account):
            raise AdapterError(malicious)

    for provider in (
        UnavailableProvider(),
        UnavailableExceptionProvider(),
        AdapterExceptionProvider(),
    ):
        service = InteractiveLoginService(
            providers=SimpleNamespace(
                get_interactive=lambda _platform, current=provider: current,
            ),
            leases=AccountLeaseManager(),
        )

        with pytest.raises(LoginUnavailableError) as error:
            await service.start(
                platform="douyin",
                account_alias="dy_01",
            )

        assert error.value.code == "interactive_provider_unavailable"
        assert "secret" not in str(error.value).lower()
        assert "sessionid" not in str(error.value).lower()
        await service.aclose()

    assert "secret" not in caplog.text.lower()
    assert "sessionid" not in caplog.text.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"platform": "facebook", "accountAlias": "dy_01"},
        {"platform": "douyin", "accountAlias": "  "},
        {"platform": "douyin"},
        {
            "platform": "douyin",
            "accountAlias": "dy_01",
            "storageStatePath": "C:/private/state.json",
        },
    ],
)
async def test_create_interactive_login_session_rejects_invalid_input(
    interactive_login_api_client,
    payload,
):
    client, service = interactive_login_api_client

    response = await client.post(
        "/api/accounts/login-sessions",
        json=payload,
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "request_validation_error"
    assert service.calls == []


@pytest.mark.asyncio
async def test_login_lifespan_closes_service():
    from tiktok_bot_api.main import app, lifespan

    original_service = app.state.interactive_login_service
    original_disabled = app.state.pipeline_runtime_disabled
    fake = _FakeInteractiveLoginService()
    app.state.interactive_login_service = fake
    app.state.pipeline_runtime_disabled = True
    try:
        async with lifespan(app):
            assert fake.closed is False
        assert fake.closed is True
        assert ("aclose",) in fake.calls
    finally:
        app.state.interactive_login_service = original_service
        app.state.pipeline_runtime_disabled = original_disabled


@pytest.mark.asyncio
async def test_login_lifespan_propagates_cleanup_incomplete(caplog):
    from tiktok_bot_api.main import app, lifespan
    from tiktok_bot_core.services.interactive_login import (
        LoginCleanupIncompleteError,
    )

    class IncompleteService(_FakeInteractiveLoginService):
        async def aclose(self):
            self.closed = True
            raise LoginCleanupIncompleteError()

    chromium = SimpleNamespace(aclose=AsyncMock())
    original_service = app.state.interactive_login_service
    original_chromium = app.state.interactive_login_chromium
    original_disabled = app.state.pipeline_runtime_disabled
    app.state.interactive_login_service = IncompleteService()
    app.state.interactive_login_chromium = chromium
    app.state.pipeline_runtime_disabled = True
    try:
        with pytest.raises(
            LoginCleanupIncompleteError,
            match="login_cleanup_incomplete",
        ):
            async with lifespan(app):
                pass
        chromium.aclose.assert_awaited_once()
        assert "login_cleanup_incomplete" in caplog.text
    finally:
        app.state.interactive_login_service = original_service
        app.state.interactive_login_chromium = original_chromium
        app.state.pipeline_runtime_disabled = original_disabled


@pytest.mark.asyncio
async def test_transactional_account_updater_failure_never_confirms(
    db,
    tmp_path,
):
    from sqlalchemy import event

    from tiktok_bot_api.main import _build_login_account_callbacks
    from tiktok_bot_core.browser.providers import BrowserAvailability
    from tiktok_bot_core.services.account_leases import AccountLeaseManager
    from tiktok_bot_core.services.auth_service import build_auth_paths
    from tiktok_bot_core.services.interactive_login import (
        AuthVerification,
        InteractiveBrowserSession,
        InteractiveLoginService,
        LoginOperationError,
        PersistedAuthState,
    )
    from tiktok_bot_core.storage.sqlite_store import SqliteStore

    account_store = SqliteStore()
    with db.session() as database_session:
        account = account_store.add_tiktok_account(
            database_session,
            platform="douyin",
            username="transactional-login",
            status="pending",
        )
        account_id = account.id

    paths = build_auth_paths(
        tmp_path,
        "douyin",
        "douyin:transactional-login",
    )
    browser = InteractiveBrowserSession(
        account_key=str(account_id),
        context=MagicMock(),
        page=MagicMock(),
        profile_dir=paths.profile_dir,
        storage_state_path=paths.storage_state,
    )
    verification = AuthVerification(
        authenticated=True,
        has_authenticated_cookie=True,
        protected_page_ok=True,
        identity_probe_ok=True,
    )
    persisted = PersistedAuthState(
        storage_state_path=paths.storage_state,
        cookie_count=1,
        origin_count=1,
        cookies=[{"name": "sessionid", "value": "private-cookie"}],
    )

    class Provider:
        provider_name = "playwright"

        async def check_interactive_available(self, _account):
            return BrowserAvailability(available=True)

        async def open_interactive_login(self, **_kwargs):
            return browser

        async def verify_interactive_login(self, _browser):
            return verification

        async def persist_interactive_login(self, _browser):
            return persisted

        async def close_interactive_login(self, _browser):
            return None

    provider = Provider()
    registry = SimpleNamespace(
        get_interactive=lambda _platform: provider,
    )
    resolver, updater = _build_login_account_callbacks(
        db,
        data_root=tmp_path,
    )
    service = InteractiveLoginService(
        providers=registry,
        leases=AccountLeaseManager(),
        account_resolver=resolver,
        account_updater=updater,
    )
    started = await service.start(
        platform="douyin",
        account_alias="transactional-login",
    )

    def fail_commit(_session):
        raise RuntimeError("forced commit failure")

    event.listen(db.SessionLocal.class_, "before_commit", fail_commit)
    try:
        with pytest.raises(
            LoginOperationError,
            match="account_update_failed",
        ):
            await service.verify(started.token)
    finally:
        event.remove(
            db.SessionLocal.class_,
            "before_commit",
            fail_commit,
        )
        await service.aclose()

    status = await service.status(started.token)
    assert status.status == "failed"
    assert status.error_code == "account_update_failed"
    assert status.persisted is False
    with db.session() as database_session:
        account = account_store.get_tiktok_account(
            database_session,
            account_id,
        )
        assert account.status == "pending"
        assert account.cookies_json == ""
        assert account.storage_state_path == ""
        assert account.profile_path == ""


def test_production_account_updater_persists_complete_auth_metadata(
    db,
    tmp_path,
):
    from tiktok_bot_api.main import _build_login_account_callbacks
    from tiktok_bot_core.services.auth_service import build_auth_paths
    from tiktok_bot_core.services.interactive_login import (
        AuthVerification,
        PersistedAuthState,
    )
    from tiktok_bot_core.storage.sqlite_store import SqliteStore

    account_store = SqliteStore()
    with db.session() as database_session:
        account = account_store.add_tiktok_account(
            database_session,
            platform="douyin",
            username="metadata-login",
            status="pending",
        )
        account_id = account.id
        account.browser_provider = "playwright"

    resolver, updater = _build_login_account_callbacks(
        db,
        data_root=tmp_path,
    )
    account = resolver("douyin", "metadata-login")
    paths = build_auth_paths(
        tmp_path,
        "douyin",
        account.browser_account_key,
    )
    updater(
        account,
        PersistedAuthState(
            storage_state_path=paths.storage_state,
            cookie_count=1,
            origin_count=1,
            cookies=[
                {
                    "name": "sessionid",
                    "value": "private-cookie",
                }
            ],
        ),
        AuthVerification(
            authenticated=True,
            has_authenticated_cookie=True,
            protected_page_ok=True,
            identity_probe_ok=True,
            nickname="真实抖音昵称",
            avatar_url="https://p3.douyinpic.com/avatar.jpeg",
            follower_count=321,
        ),
    )

    with db.session() as database_session:
        updated = account_store.get_tiktok_account(
            database_session,
            account_id,
        )
        assert updated.status == "logged_in"
        assert updated.login_method == "interactive_browser"
        assert json.loads(updated.cookies_json) == [
            {"name": "sessionid", "value": "private-cookie"}
        ]
        assert updated.storage_state_path == (
            f"auth_states/douyin/{paths.storage_state.name}"
        )
        assert updated.profile_path == (
            f"browser_profiles/douyin/{paths.profile_dir.name}"
        )
        assert updated.auth_verified_at is not None
        assert updated.auth_version == 2
        assert updated.browser_provider == "playwright"
        assert updated.browser_profile_id == paths.profile_dir.name
        assert updated.nickname == "真实抖音昵称"
        assert updated.avatar_url == "https://p3.douyinpic.com/avatar.jpeg"
        assert updated.follower_count == 321


def test_production_account_updater_preserves_profile_fields_when_omitted(
    db,
    tmp_path,
):
    from tiktok_bot_api.main import _build_login_account_callbacks
    from tiktok_bot_core.services.auth_service import build_auth_paths
    from tiktok_bot_core.services.interactive_login import (
        AuthVerification,
        PersistedAuthState,
    )
    from tiktok_bot_core.storage.sqlite_store import SqliteStore

    account_store = SqliteStore()
    with db.session() as database_session:
        account = account_store.add_tiktok_account(
            database_session,
            platform="douyin",
            username="preserve-profile",
            status="logged_in",
        )
        account.nickname = "历史昵称"
        account.avatar_url = "https://p3.douyinpic.com/old-avatar.jpeg"
        account.follower_count = 777
        account_id = account.id

    resolver, updater = _build_login_account_callbacks(
        db,
        data_root=tmp_path,
    )
    resolved = resolver("douyin", "preserve-profile")
    paths = build_auth_paths(
        tmp_path,
        "douyin",
        resolved.browser_account_key,
    )
    updater(
        resolved,
        PersistedAuthState(
            storage_state_path=paths.storage_state,
            cookie_count=1,
            origin_count=0,
            cookies=[{"name": "sessionid", "value": "private-cookie"}],
        ),
        AuthVerification(
            authenticated=True,
            has_authenticated_cookie=True,
            protected_page_ok=True,
            identity_probe_ok=True,
        ),
    )

    with db.session() as database_session:
        updated = account_store.get_tiktok_account(
            database_session,
            account_id,
        )
        assert updated.nickname == "历史昵称"
        assert updated.avatar_url == "https://p3.douyinpic.com/old-avatar.jpeg"
        assert updated.follower_count == 777


def test_production_resolver_keeps_browser_account_key_stable_after_insert(
    db,
    tmp_path,
):
    from tiktok_bot_api.main import _build_login_account_callbacks
    from tiktok_bot_core.services.auth_service import build_auth_paths
    from tiktok_bot_core.services.interactive_login import (
        AuthVerification,
        PersistedAuthState,
    )

    resolver, updater = _build_login_account_callbacks(
        db,
        data_root=tmp_path,
    )
    first = resolver("douyin", "new-alias")
    first_paths = build_auth_paths(
        tmp_path,
        "douyin",
        first.browser_account_key,
    )

    updater(
        first,
        PersistedAuthState(
            storage_state_path=first_paths.storage_state,
            cookie_count=1,
            origin_count=0,
            cookies=[
                {
                    "name": "sessionid",
                    "value": "private-cookie",
                }
            ],
        ),
        AuthVerification(
            authenticated=True,
            has_authenticated_cookie=True,
            protected_page_ok=True,
            identity_probe_ok=True,
        ),
    )
    second = resolver("douyin", "new-alias")
    second_paths = build_auth_paths(
        tmp_path,
        "douyin",
        second.browser_account_key,
    )

    assert first.id is None
    assert second.id is not None
    assert first.browser_account_key == second.browser_account_key
    assert first.browser_account_key == "douyin:new-alias"
    assert first_paths.profile_dir == second_paths.profile_dir
    assert first_paths.storage_state == second_paths.storage_state


def test_production_resolver_browser_key_never_guesses_numeric_alias_as_id(
    db,
    tmp_path,
):
    from tiktok_bot_api.main import _build_login_account_callbacks
    from tiktok_bot_core.services.auth_service import build_auth_paths
    from tiktok_bot_core.storage.sqlite_store import SqliteStore

    account_store = SqliteStore()
    with db.session() as database_session:
        placeholders = []
        for index in range(1, 7):
            placeholders.append(
                account_store.add_tiktok_account(
                    database_session,
                    platform="douyin",
                    username=f"placeholder-{index}",
                )
            )
        account = account_store.add_tiktok_account(
            database_session,
            platform="douyin",
            username="7",
        )
        assert account.id == 7
        for placeholder in placeholders:
            database_session.delete(placeholder)

    resolver, _updater = _build_login_account_callbacks(
        db,
        data_root=tmp_path,
    )
    numeric_alias = resolver("douyin", "7")
    same_alias_other_platform = resolver("tiktok", "7")
    other_alias = resolver("douyin", "8")
    ascii_alias = resolver("douyin", "A")
    full_width_alias = resolver("douyin", " Ａ ")

    assert numeric_alias.id == 7
    assert numeric_alias.browser_account_key == "douyin:7"
    assert numeric_alias.browser_account_key != str(numeric_alias.id)
    assert (
        numeric_alias.browser_account_key
        != same_alias_other_platform.browser_account_key
    )
    assert numeric_alias.browser_account_key != other_alias.browser_account_key
    assert (
        ascii_alias.browser_account_key
        == full_width_alias.browser_account_key
        == "douyin:A"
    )
    assert build_auth_paths(
        tmp_path,
        "douyin",
        numeric_alias.browser_account_key,
    ) != build_auth_paths(
        tmp_path,
        "douyin",
        str(numeric_alias.id),
    )


@pytest.mark.asyncio
async def test_service_passes_resolver_browser_key_separately_from_db_id(
    db,
    tmp_path,
):
    from tiktok_bot_api.main import _build_login_account_callbacks
    from tiktok_bot_core.browser.providers import BrowserAvailability
    from tiktok_bot_core.services.account_leases import AccountLeaseManager
    from tiktok_bot_core.services.auth_service import build_auth_paths
    from tiktok_bot_core.services.interactive_login import (
        InteractiveBrowserSession,
        InteractiveLoginService,
    )
    from tiktok_bot_core.storage.sqlite_store import SqliteStore

    account_store = SqliteStore()
    with db.session() as database_session:
        for index in range(1, 7):
            account_store.add_tiktok_account(
                database_session,
                platform="douyin",
                username=f"existing-{index}",
            )
        account = account_store.add_tiktok_account(
            database_session,
            platform="douyin",
            username="7",
        )
        assert account.id == 7

    resolver, _updater = _build_login_account_callbacks(
        db,
        data_root=tmp_path,
    )
    opened_keys = []

    class Provider:
        async def check_interactive_available(self, _account):
            return BrowserAvailability(available=True)

        async def open_interactive_login(
            self,
            *,
            account_key,
            account,
        ):
            opened_keys.append(account_key)
            paths = build_auth_paths(
                tmp_path,
                "douyin",
                account_key,
            )
            return InteractiveBrowserSession(
                account_key=account_key,
                context=MagicMock(),
                page=MagicMock(),
                profile_dir=paths.profile_dir,
                storage_state_path=paths.storage_state,
            )

        async def close_interactive_login(self, _session):
            return None

    provider = Provider()
    service = InteractiveLoginService(
        providers=SimpleNamespace(
            get_interactive=lambda _platform: provider,
        ),
        leases=AccountLeaseManager(),
        account_resolver=resolver,
        account_updater=None,
    )

    session = await service.start(
        platform="douyin",
        account_alias="7",
    )
    assert session.account_id == 7
    assert opened_keys == ["douyin:7"]
    await service.cancel(session.token)
    await service.aclose()


def test_normalize_account_alias_is_single_canonical_rule():
    from tiktok_bot_core.services.auth_service import (
        normalize_account_alias,
    )

    assert normalize_account_alias(" foo ") == "foo"
    assert normalize_account_alias(" Ａ ") == "A"
    assert normalize_account_alias("7") == "7"
    assert normalize_account_alias("Case") == "Case"
    assert normalize_account_alias("case") == "case"
    with pytest.raises(ValueError, match="alias"):
        normalize_account_alias(" \t ")


def test_auth_service_add_account_persists_canonical_alias_and_rejects_collision(
    db,
):
    from tiktok_bot_core.services.auth_service import (
        AccountAliasConflictError,
        get_auth_service,
    )

    service = get_auth_service()
    created = service.add_account("douyin", " Ａ ")

    assert created["platform"] == "douyin"
    assert created["username"] == "A"
    with pytest.raises(AccountAliasConflictError) as error:
        service.add_account("douyin", "A")
    assert error.value.code == "account_alias_conflict"
    assert service.add_account("douyin", " foo ")["username"] == "foo"
    with pytest.raises(AccountAliasConflictError):
        service.add_account("douyin", "foo")
    assert service.add_account("tiktok", "Ａ")["username"] == "A"
    assert service.add_account("douyin", "7")["username"] == "7"


def test_auth_service_concurrent_canonical_alias_insert_is_serialized(db):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from tiktok_bot_core.services.auth_service import (
        AccountAliasConflictError,
        get_auth_service,
    )

    service = get_auth_service()
    barrier = Barrier(2)

    def create(alias):
        barrier.wait(timeout=5)
        try:
            return ("created", service.add_account("douyin", alias))
        except AccountAliasConflictError:
            return ("conflict", None)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(create, "A")
        second = executor.submit(create, "Ａ")
        results = [first.result(timeout=10), second.result(timeout=10)]

    assert sorted(result[0] for result in results) == [
        "conflict",
        "created",
    ]
    assert [result[1]["username"] for result in results if result[1]] == ["A"]
    assert [
        account["username"]
        for account in service.list_accounts(platform="douyin")
    ] == ["A"]


def test_production_resolver_fails_closed_on_legacy_canonical_collision(
    db,
    tmp_path,
):
    from tiktok_bot_api.main import _build_login_account_callbacks
    from tiktok_bot_core.services.interactive_login import (
        LoginOperationError,
    )
    from tiktok_bot_core.storage.sqlite_store import SqliteStore

    account_store = SqliteStore()
    with db.session() as database_session:
        account_store.add_tiktok_account(
            database_session,
            platform="douyin",
            username="A",
        )
        account_store.add_tiktok_account(
            database_session,
            platform="douyin",
            username="Ａ",
        )
    resolver, _updater = _build_login_account_callbacks(
        db,
        data_root=tmp_path,
    )

    with pytest.raises(LoginOperationError) as error:
        resolver("douyin", " A ")

    assert error.value.code == "account_alias_conflict"


def test_production_updater_rechecks_alias_conflict_before_insert(
    db,
    tmp_path,
):
    from tiktok_bot_api.main import _build_login_account_callbacks
    from tiktok_bot_core.services.auth_service import (
        AccountAliasConflictError,
        build_auth_paths,
    )
    from tiktok_bot_core.services.interactive_login import (
        AuthVerification,
        PersistedAuthState,
    )
    from tiktok_bot_core.storage.sqlite_store import SqliteStore

    account_store = SqliteStore()
    resolver, updater = _build_login_account_callbacks(
        db,
        data_root=tmp_path,
    )
    new_account = resolver("douyin", " Ａ ")
    paths = build_auth_paths(
        tmp_path,
        "douyin",
        new_account.browser_account_key,
    )
    with db.session() as database_session:
        existing = account_store.add_tiktok_account(
            database_session,
            platform="douyin",
            username="A",
            status="pending",
        )
        existing_id = existing.id

    with pytest.raises(AccountAliasConflictError):
        updater(
            new_account,
            PersistedAuthState(
                storage_state_path=paths.storage_state,
                cookie_count=1,
                origin_count=0,
                cookies=[
                    {
                        "name": "sessionid",
                        "value": "private-cookie",
                    }
                ],
            ),
            AuthVerification(
                authenticated=True,
                has_authenticated_cookie=True,
                protected_page_ok=True,
                identity_probe_ok=True,
            ),
        )

    with db.session() as database_session:
        existing = account_store.get_tiktok_account(
            database_session,
            existing_id,
        )
        assert existing.status == "pending"
        assert existing.cookies_json == ""


def test_production_updater_serializes_concurrent_first_insert(
    db,
    tmp_path,
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from tiktok_bot_api.main import _build_login_account_callbacks
    from tiktok_bot_core.services.auth_service import (
        AccountAliasConflictError,
        build_auth_paths,
    )
    from tiktok_bot_core.services.interactive_login import (
        AuthVerification,
        PersistedAuthState,
    )
    from tiktok_bot_core.storage.sqlite_store import SqliteStore

    resolver, updater = _build_login_account_callbacks(
        db,
        data_root=tmp_path,
    )
    accounts = [
        resolver("douyin", "A"),
        resolver("douyin", "Ａ"),
    ]
    paths = build_auth_paths(
        tmp_path,
        "douyin",
        accounts[0].browser_account_key,
    )
    persisted = PersistedAuthState(
        storage_state_path=paths.storage_state,
        cookie_count=1,
        origin_count=0,
        cookies=[{"name": "sessionid", "value": "private-cookie"}],
    )
    verification = AuthVerification(
        authenticated=True,
        has_authenticated_cookie=True,
        protected_page_ok=True,
        identity_probe_ok=True,
    )
    barrier = Barrier(2)

    def persist(account):
        barrier.wait(timeout=5)
        try:
            updater(account, persisted, verification)
            return "created"
        except AccountAliasConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(persist, account)
            for account in accounts
        ]
        results = [future.result(timeout=10) for future in futures]

    assert sorted(results) == ["conflict", "created"]
    with db.session() as database_session:
        stored = SqliteStore().get_tiktok_accounts(
            database_session,
            platform="douyin",
        )
        assert len(stored) == 1
        assert stored[0].username == "A"
        assert stored[0].status == "logged_in"


@pytest.mark.asyncio
async def test_account_alias_validation_and_conflict_http_mapping(
    interactive_login_api_client,
    db,
):
    client, _fake = interactive_login_api_client

    empty = await client.post(
        "/api/accounts",
        json={"platform": "douyin", "username": "  "},
    )
    created = await client.post(
        "/api/accounts",
        json={"platform": "douyin", "username": " Ａ "},
    )
    conflict = await client.post(
        "/api/accounts",
        json={"platform": "douyin", "username": "A"},
    )

    assert empty.status_code == 422
    assert created.status_code == 200
    assert created.json()["username"] == "A"
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "account_alias_conflict"


@pytest.mark.asyncio
async def test_login_create_empty_and_legacy_alias_conflict_http_mapping(
    interactive_login_api_client,
    db,
    tmp_path,
):
    from tiktok_bot_api.main import (
        _build_login_account_callbacks,
        app,
    )
    from tiktok_bot_core.services.account_leases import AccountLeaseManager
    from tiktok_bot_core.services.interactive_login import (
        InteractiveLoginService,
    )
    from tiktok_bot_core.storage.sqlite_store import SqliteStore

    client, fake_service = interactive_login_api_client
    empty = await client.post(
        "/api/accounts/login-sessions",
        json={"platform": "douyin", "accountAlias": "  "},
    )

    account_store = SqliteStore()
    with db.session() as database_session:
        account_store.add_tiktok_account(
            database_session,
            platform="douyin",
            username="A",
        )
        account_store.add_tiktok_account(
            database_session,
            platform="douyin",
            username="Ａ",
        )
    resolver, updater = _build_login_account_callbacks(
        db,
        data_root=tmp_path,
    )
    real_service = InteractiveLoginService(
        providers=SimpleNamespace(),
        leases=AccountLeaseManager(),
        account_resolver=resolver,
        account_updater=updater,
    )
    app.state.interactive_login_service = real_service
    try:
        conflict = await client.post(
            "/api/accounts/login-sessions",
            json={"platform": "douyin", "accountAlias": " A "},
        )
    finally:
        await real_service.aclose()
        app.state.interactive_login_service = fake_service

    assert empty.status_code == 422
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "account_alias_conflict"


def _fill_account_capacity(database, *, include_alias=None):
    from tiktok_bot_core.services.auth_service import MAX_ACCOUNTS
    from tiktok_bot_core.storage.sqlite_store import SqliteStore

    aliases = [
        f"capacity-{index}"
        for index in range(MAX_ACCOUNTS)
    ]
    if include_alias is not None:
        aliases[0] = include_alias
    with database.session() as database_session:
        for alias in aliases:
            SqliteStore().add_tiktok_account(
                database_session,
                platform="douyin",
                username=alias,
                status="pending",
            )
    return aliases


def _login_persisted_state(tmp_path, account):
    from tiktok_bot_core.services.auth_service import build_auth_paths
    from tiktok_bot_core.services.interactive_login import (
        AuthVerification,
        PersistedAuthState,
    )

    paths = build_auth_paths(
        tmp_path,
        account.platform,
        account.browser_account_key,
    )
    return (
        PersistedAuthState(
            storage_state_path=paths.storage_state,
            cookie_count=1,
            origin_count=0,
            cookies=[
                {
                    "name": "sessionid",
                    "value": "private-cookie",
                }
            ],
        ),
        AuthVerification(
            authenticated=True,
            has_authenticated_cookie=True,
            protected_page_ok=True,
            identity_probe_ok=True,
        ),
        paths,
    )


@pytest.mark.asyncio
async def test_full_capacity_resolver_and_start_reject_before_browser_open(
    db,
    tmp_path,
):
    from tiktok_bot_api.main import _build_login_account_callbacks
    from tiktok_bot_core.services.account_leases import AccountLeaseManager
    from tiktok_bot_core.services.interactive_login import (
        InteractiveLoginService,
        LoginOperationError,
    )

    _fill_account_capacity(db)
    resolver, updater = _build_login_account_callbacks(
        db,
        data_root=tmp_path,
    )

    with pytest.raises(LoginOperationError) as resolver_error:
        resolver("douyin", "new-at-capacity")
    assert resolver_error.value.code == "account_limit_reached"

    provider_lookup = MagicMock()
    service = InteractiveLoginService(
        providers=SimpleNamespace(get_interactive=provider_lookup),
        leases=AccountLeaseManager(),
        account_resolver=resolver,
        account_updater=updater,
    )
    try:
        with pytest.raises(LoginOperationError) as start_error:
            await service.start(
                platform="douyin",
                account_alias="new-at-capacity",
            )
    finally:
        await service.aclose()

    assert start_error.value.code == "account_limit_reached"
    provider_lookup.assert_not_called()


def test_full_capacity_updater_recheck_blocks_sixth_account(
    db,
    tmp_path,
):
    from tiktok_bot_api.main import _build_login_account_callbacks
    from tiktok_bot_core.services.auth_service import (
        AccountLimitReachedError,
        MAX_ACCOUNTS,
    )
    from tiktok_bot_core.storage.sqlite_store import SqliteStore

    resolver, updater = _build_login_account_callbacks(
        db,
        data_root=tmp_path,
    )
    with db.session() as database_session:
        for index in range(MAX_ACCOUNTS - 1):
            SqliteStore().add_tiktok_account(
                database_session,
                platform="douyin",
                username=f"preflight-{index}",
                status="pending",
            )
    new_account = resolver("douyin", "late-sixth")
    persisted, verification, _paths = _login_persisted_state(
        tmp_path,
        new_account,
    )
    with db.session() as database_session:
        SqliteStore().add_tiktok_account(
            database_session,
            platform="tiktok",
            username="claimed-last-slot",
            status="pending",
        )

    with pytest.raises(AccountLimitReachedError):
        updater(new_account, persisted, verification)

    with db.session() as database_session:
        accounts = SqliteStore().get_tiktok_accounts(database_session)
        assert len(accounts) == MAX_ACCOUNTS
        assert all(account.username != "late-sixth" for account in accounts)
        assert all(account.cookies_json == "" for account in accounts)


@pytest.mark.asyncio
async def test_verify_reports_capacity_race_without_sixth_auth_record(
    db,
    tmp_path,
):
    from tiktok_bot_api.main import _build_login_account_callbacks
    from tiktok_bot_core.browser.providers import BrowserAvailability
    from tiktok_bot_core.services.account_leases import AccountLeaseManager
    from tiktok_bot_core.services.auth_service import MAX_ACCOUNTS
    from tiktok_bot_core.services.interactive_login import (
        InteractiveBrowserSession,
        InteractiveLoginService,
        LoginOperationError,
    )
    from tiktok_bot_core.storage.sqlite_store import SqliteStore

    with db.session() as database_session:
        for index in range(MAX_ACCOUNTS - 1):
            SqliteStore().add_tiktok_account(
                database_session,
                platform="douyin",
                username=f"verify-race-{index}",
                status="pending",
            )
    resolver, updater = _build_login_account_callbacks(
        db,
        data_root=tmp_path,
    )
    pending = resolver("douyin", "verify-race-new")
    persisted, verification, paths = _login_persisted_state(
        tmp_path,
        pending,
    )
    browser = InteractiveBrowserSession(
        account_key=pending.browser_account_key,
        context=MagicMock(),
        page=MagicMock(),
        profile_dir=paths.profile_dir,
        storage_state_path=paths.storage_state,
    )

    class Provider:
        async def check_interactive_available(self, _account):
            return BrowserAvailability(available=True)

        async def open_interactive_login(self, **_kwargs):
            return browser

        async def verify_interactive_login(self, _browser):
            return verification

        async def persist_interactive_login(self, _browser):
            return persisted

        async def close_interactive_login(self, _browser):
            return None

    provider = Provider()
    service = InteractiveLoginService(
        providers=SimpleNamespace(
            get_interactive=lambda _platform: provider,
        ),
        leases=AccountLeaseManager(),
        account_resolver=resolver,
        account_updater=updater,
    )
    started = await service.start(
        platform="douyin",
        account_alias="verify-race-new",
    )
    with db.session() as database_session:
        SqliteStore().add_tiktok_account(
            database_session,
            platform="tiktok",
            username="verify-race-last-slot",
            status="pending",
        )
    try:
        with pytest.raises(LoginOperationError) as error:
            await service.verify(started.token)
        status = await service.status(started.token)
    finally:
        await service.aclose()

    assert error.value.code == "account_limit_reached"
    assert status.status == "failed"
    assert status.error_code == "account_limit_reached"
    assert status.persisted is False
    with db.session() as database_session:
        accounts = SqliteStore().get_tiktok_accounts(database_session)
        assert len(accounts) == MAX_ACCOUNTS
        assert all(
            account.username != "verify-race-new"
            for account in accounts
        )
        assert all(account.cookies_json == "" for account in accounts)


def test_existing_account_can_update_when_capacity_is_full(
    db,
    tmp_path,
):
    from tiktok_bot_api.main import _build_login_account_callbacks
    from tiktok_bot_core.services.auth_service import MAX_ACCOUNTS
    from tiktok_bot_core.storage.sqlite_store import SqliteStore

    aliases = _fill_account_capacity(
        db,
        include_alias="existing-at-capacity",
    )
    resolver, updater = _build_login_account_callbacks(
        db,
        data_root=tmp_path,
    )
    existing = resolver("douyin", "existing-at-capacity")
    persisted, verification, _paths = _login_persisted_state(
        tmp_path,
        existing,
    )

    updater(existing, persisted, verification)

    with db.session() as database_session:
        accounts = SqliteStore().get_tiktok_accounts(database_session)
        target = next(
            account
            for account in accounts
            if account.username == aliases[0]
        )
        assert len(accounts) == MAX_ACCOUNTS
        assert target.status == "logged_in"
        assert json.loads(target.cookies_json)[0]["name"] == "sessionid"


def test_alias_conflict_wins_over_capacity_error_in_updater(
    db,
    tmp_path,
):
    from tiktok_bot_api.main import _build_login_account_callbacks
    from tiktok_bot_core.services.auth_service import (
        AccountAliasConflictError,
    )

    _fill_account_capacity(db, include_alias="same-alias")
    _resolver, updater = _build_login_account_callbacks(
        db,
        data_root=tmp_path,
    )
    new_snapshot = SimpleNamespace(
        id=None,
        platform="douyin",
        username=" same-alias ",
        browser_account_key="douyin:same-alias",
        browser_provider="playwright",
        browser_profile_id="",
    )
    persisted, verification, _paths = _login_persisted_state(
        tmp_path,
        new_snapshot,
    )

    with pytest.raises(AccountAliasConflictError):
        updater(new_snapshot, persisted, verification)


def test_concurrent_first_logins_compete_for_last_global_slot(
    db,
    tmp_path,
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from tiktok_bot_api.main import _build_login_account_callbacks
    from tiktok_bot_core.services.auth_service import (
        AccountLimitReachedError,
        MAX_ACCOUNTS,
    )
    from tiktok_bot_core.storage.sqlite_store import SqliteStore

    with db.session() as database_session:
        for index in range(MAX_ACCOUNTS - 1):
            SqliteStore().add_tiktok_account(
                database_session,
                platform="douyin",
                username=f"last-slot-{index}",
                status="pending",
            )
    resolver, updater = _build_login_account_callbacks(
        db,
        data_root=tmp_path,
    )
    pending = [
        resolver("douyin", "candidate-a"),
        resolver("tiktok", "candidate-b"),
    ]
    states = [
        _login_persisted_state(tmp_path, account)
        for account in pending
    ]
    barrier = Barrier(2)

    def persist(index):
        barrier.wait(timeout=5)
        try:
            updater(
                pending[index],
                states[index][0],
                states[index][1],
            )
            return "created"
        except AccountLimitReachedError:
            return "limit"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [
            future.result(timeout=10)
            for future in (
                executor.submit(persist, 0),
                executor.submit(persist, 1),
            )
        ]

    assert sorted(results) == ["created", "limit"]
    with db.session() as database_session:
        accounts = SqliteStore().get_tiktok_accounts(database_session)
        assert len(accounts) == MAX_ACCOUNTS
        assert sum(
            account.username in {"candidate-a", "candidate-b"}
            for account in accounts
        ) == 1


@pytest.mark.asyncio
async def test_legacy_login_route_maps_full_capacity_without_old_qr_or_auth_write(
    interactive_login_api_client,
    db,
    tmp_path,
    monkeypatch,
):
    from tiktok_bot_api import main as api_main
    from tiktok_bot_api.main import (
        _build_login_account_callbacks,
        app,
    )
    from tiktok_bot_core.services.account_leases import AccountLeaseManager
    from tiktok_bot_core.services.auth_service import MAX_ACCOUNTS
    from tiktok_bot_core.services.interactive_login import (
        InteractiveLoginService,
    )
    from tiktok_bot_core.storage.sqlite_store import SqliteStore

    client, fake_service = interactive_login_api_client
    _fill_account_capacity(db)
    resolver, updater = _build_login_account_callbacks(
        db,
        data_root=tmp_path,
    )
    provider_lookup = MagicMock()
    real_service = InteractiveLoginService(
        providers=SimpleNamespace(get_interactive=provider_lookup),
        leases=AccountLeaseManager(),
        account_resolver=resolver,
        account_updater=updater,
    )
    legacy_auth = MagicMock()
    monkeypatch.setattr(
        api_main,
        "get_auth_service",
        lambda: legacy_auth,
    )
    app.state.interactive_login_service = real_service
    try:
        response = await client.post(
            "/api/accounts/login-qrcode",
            json={
                "platform": "douyin",
                "username": "legacy-new-at-capacity",
            },
        )
    finally:
        await real_service.aclose()
        app.state.interactive_login_service = fake_service

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "account_limit_reached"
    provider_lookup.assert_not_called()
    legacy_auth.start_qrcode_login.assert_not_called()
    legacy_auth._qrcode_login_task.assert_not_called()
    with db.session() as database_session:
        accounts = SqliteStore().get_tiktok_accounts(database_session)
        assert len(accounts) == MAX_ACCOUNTS
        assert all(account.cookies_json == "" for account in accounts)
