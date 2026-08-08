from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest

import tiktok_bot_core.browser.providers as browser_providers
from tiktok_bot_core.browser.providers import (
    BrowserProviderRegistry,
    DouyinInteractiveLoginProvider,
    FingerprintInteractiveLoginProvider,
)
import tiktok_bot_core.services.interactive_login as interactive_login
from tiktok_bot_core.services.account_leases import (
    AccountBusyError,
    AccountLeaseManager,
)
from tiktok_bot_core.services.interactive_login import (
    ALLOWED_TRANSITIONS,
    AuthVerification,
    InteractiveBrowserSession,
    LoginCleanupIncompleteError,
    InteractiveLoginService,
    InvalidLoginTransition,
    LoginOperationError,
    LoginSession,
    LoginUnavailableError,
    PersistedAuthState,
)

DOUYIN_PROFILE_PROBE_URL = (
    "https://www.douyin.com/aweme/v1/web/user/profile/self/"
    "?device_platform=webapp&aid=6383&channel=channel_pc_web"
    "&publish_video_strategy_type=2&version_code=170400"
    "&version_name=17.4.0&pc_client_type=1"
)


def configure_profile_probe(
    page,
    *,
    payload=None,
    http_ok=True,
    http_status=200,
    error_code="",
    exception=None,
    local_storage=None,
    events=None,
):
    if payload is None:
        payload = {
            "status_code": 0,
            "user": {"uid": "authenticated-user"},
        }
    if local_storage is None:
        local_storage = {}

    async def evaluate(script, argument=None):
        if "window.fetch" in script:
            if events is not None:
                events.append("probe")
            if exception is not None:
                raise exception
            return {
                "ok": http_ok,
                "status": http_status,
                "payload": payload,
                "error_code": error_code,
            }
        if "window.localStorage" in script:
            return local_storage
        raise AssertionError("unexpected page.evaluate script")

    page.evaluate.side_effect = evaluate


@pytest.fixture
def fake_chromium(monkeypatch):
    secure_directory = MagicMock(
        side_effect=lambda directory: directory.mkdir(
            parents=True,
            exist_ok=True,
        )
    )
    monkeypatch.setattr(
        browser_providers,
        "secure_private_directory",
        secure_directory,
        raising=False,
    )
    monkeypatch.setattr(
        interactive_login,
        "secure_private_directory",
        secure_directory,
        raising=False,
    )
    response = MagicMock()
    response.status = 200
    page = MagicMock()
    page.goto = AsyncMock(return_value=response)
    page.url = "https://www.douyin.com/"
    page.click = AsyncMock()
    page.screenshot = AsyncMock()
    page.query_selector = AsyncMock()
    page.locator = MagicMock()
    page.evaluate = AsyncMock()
    configure_profile_probe(page)
    context = MagicMock()
    context.pages = [page]
    context.new_page = AsyncMock(return_value=page)
    context.close = AsyncMock()
    context.cookies = AsyncMock(
        return_value=authenticated_douyin_cookies()
    )
    context.storage_state = AsyncMock(
        return_value={"cookies": [], "origins": []}
    )
    chromium = MagicMock()
    chromium.launch_persistent_context = AsyncMock(return_value=context)
    return chromium


def authenticated_douyin_cookies(
    *,
    domain: str = ".douyin.com",
    expires: float = -1,
    include_login_status: bool = True,
):
    cookies = [
        {
            "name": "sessionid",
            "value": "authenticated-session",
            "domain": domain,
            "expires": expires,
        },
    ]
    if include_login_status:
        cookies.append(
            {
                "name": "LOGIN_STATUS",
                "value": "1",
                "domain": domain,
                "expires": expires,
            }
        )
    return cookies


@pytest.mark.asyncio
async def test_douyin_login_launches_persistent_headed_context(
    tmp_path,
    fake_chromium,
):
    provider = DouyinInteractiveLoginProvider(
        fake_chromium,
        data_root=tmp_path,
    )

    opened = await provider.open(account_key="42")

    kwargs = fake_chromium.launch_persistent_context.await_args.kwargs
    assert kwargs["user_data_dir"] == (
        tmp_path
        / "browser_profiles"
        / "douyin"
        / "42-73475cb40a56"
    )
    assert kwargs["headless"] is False
    opened.page.goto.assert_awaited_once_with(
        "https://www.douyin.com/",
        wait_until="domcontentloaded",
    )


@pytest.mark.asyncio
async def test_douyin_login_never_automates_login_or_qrcode_controls(
    tmp_path,
    fake_chromium,
):
    provider = DouyinInteractiveLoginProvider(
        fake_chromium,
        data_root=tmp_path,
    )

    opened = await provider.open(account_key="42")

    assert opened.page.click.await_count == 0
    assert opened.page.locator.call_count == 0
    assert opened.page.query_selector.await_count == 0
    assert opened.page.screenshot.await_count == 0


@pytest.mark.asyncio
async def test_douyin_login_secures_profile_directory(
    tmp_path,
    fake_chromium,
):
    provider = DouyinInteractiveLoginProvider(
        fake_chromium,
        data_root=tmp_path,
    )

    opened = await provider.open(account_key="42")

    browser_providers.secure_private_directory.assert_called_once_with(
        opened.profile_dir,
    )


@pytest.mark.asyncio
async def test_douyin_login_closes_context_when_reading_pages_fails(
    tmp_path,
    fake_chromium,
):
    context = await fake_chromium.launch_persistent_context()
    fake_chromium.launch_persistent_context.reset_mock()
    type(context).pages = PropertyMock(
        side_effect=RuntimeError("pages unavailable"),
    )
    provider = DouyinInteractiveLoginProvider(
        fake_chromium,
        data_root=tmp_path,
    )

    with pytest.raises(RuntimeError, match="pages unavailable"):
        await provider.open(account_key="42")

    context.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_douyin_login_closes_context_when_new_page_fails(
    tmp_path,
    fake_chromium,
):
    context = await fake_chromium.launch_persistent_context()
    fake_chromium.launch_persistent_context.reset_mock()
    context.pages = []
    context.new_page.side_effect = RuntimeError("new page failed")
    provider = DouyinInteractiveLoginProvider(
        fake_chromium,
        data_root=tmp_path,
    )

    with pytest.raises(RuntimeError, match="new page failed"):
        await provider.open(account_key="42")

    context.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_douyin_login_closes_context_when_navigation_fails(
    tmp_path,
    fake_chromium,
):
    context = await fake_chromium.launch_persistent_context()
    fake_chromium.launch_persistent_context.reset_mock()
    context.pages[0].goto.side_effect = RuntimeError("navigation failed")
    provider = DouyinInteractiveLoginProvider(
        fake_chromium,
        data_root=tmp_path,
    )

    with pytest.raises(RuntimeError, match="navigation failed"):
        await provider.open(account_key="42")

    context.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_douyin_login_closes_context_when_navigation_is_cancelled(
    tmp_path,
    fake_chromium,
):
    context = await fake_chromium.launch_persistent_context()
    fake_chromium.launch_persistent_context.reset_mock()
    context.pages[0].goto.side_effect = asyncio.CancelledError
    provider = DouyinInteractiveLoginProvider(
        fake_chromium,
        data_root=tmp_path,
    )

    with pytest.raises(asyncio.CancelledError):
        await provider.open(account_key="42")

    context.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_verify_requires_authenticated_cookie_despite_local_storage_signal(
    tmp_path,
    fake_chromium,
):
    provider = DouyinInteractiveLoginProvider(
        fake_chromium,
        data_root=tmp_path,
    )
    opened = await provider.open(account_key="42")
    opened.context.cookies.return_value = [
        {
            "name": "ttwid",
            "value": "visitor-only",
            "domain": ".douyin.com",
            "expires": -1,
        },
        {
            "name": "LOGIN_STATUS",
            "value": "1",
            "domain": ".douyin.com",
            "expires": -1,
        },
    ]
    configure_profile_probe(
        opened.page,
        payload={
            "status_code": 8,
            "status_msg": "用户未登录",
        },
        local_storage={"HasUserLogin": "1"},
    )
    opened.page.url = "https://www.douyin.com/"

    result = await provider.verify(opened)

    assert result.authenticated is False
    assert result.has_authenticated_cookie is False
    assert result.local_storage_login_detected is True


@pytest.mark.asyncio
async def test_verify_rejects_guest_profile_probe_on_homepage_200(
    tmp_path,
    fake_chromium,
):
    provider = DouyinInteractiveLoginProvider(
        fake_chromium,
        data_root=tmp_path,
    )
    opened = await provider.open(account_key="42")
    configure_profile_probe(
        opened.page,
        payload={
            "status_code": 8,
            "status_msg": "用户未登录",
        },
    )

    result = await provider.verify(opened)

    assert result.authenticated is False
    assert result.identity_probe_ok is False
    assert result.diagnostic_code == "profile_not_logged_in"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user",
    [
        {"uid": "123456"},
        {"sec_uid": "MS4wLjABAAAA"},
    ],
)
async def test_verify_accepts_profile_probe_with_account_identity(
    tmp_path,
    fake_chromium,
    user,
):
    provider = DouyinInteractiveLoginProvider(
        fake_chromium,
        data_root=tmp_path,
    )
    opened = await provider.open(account_key="42")
    configure_profile_probe(
        opened.page,
        payload={"status_code": 0, "user": user},
    )

    result = await provider.verify(opened)

    assert result.authenticated is True
    assert result.identity_probe_ok is True
    assert result.diagnostic_code == ""


@pytest.mark.asyncio
async def test_verify_extracts_safe_douyin_profile_metadata(
    tmp_path,
    fake_chromium,
):
    provider = DouyinInteractiveLoginProvider(
        fake_chromium,
        data_root=tmp_path,
    )
    opened = await provider.open(account_key="42")
    configure_profile_probe(
        opened.page,
        payload={
            "status_code": 0,
            "user": {
                "uid": "123456",
                "nickname": "真实抖音昵称",
                "follower_count": 321,
                "avatar_thumb": {
                    "url_list": [
                        "javascript:alert(1)",
                        "https://p3.douyinpic.com/avatar.jpeg",
                    ]
                },
            },
        },
    )

    result = await provider.verify(opened)

    assert result.nickname == "真实抖音昵称"
    assert result.avatar_url == "https://p3.douyinpic.com/avatar.jpeg"
    assert result.follower_count == 321


@pytest.mark.asyncio
async def test_verify_treats_missing_follower_count_as_unknown(
    tmp_path,
    fake_chromium,
):
    provider = DouyinInteractiveLoginProvider(
        fake_chromium,
        data_root=tmp_path,
    )
    opened = await provider.open(account_key="42")
    configure_profile_probe(
        opened.page,
        payload={
            "status_code": 0,
            "user": {
                "uid": "123456",
                "nickname": "仍然有效的账号",
            },
        },
    )

    result = await provider.verify(opened)

    assert result.authenticated is True
    assert result.follower_count is None


@pytest.mark.asyncio
async def test_verify_rejects_profile_probe_without_account_identity(
    tmp_path,
    fake_chromium,
):
    provider = DouyinInteractiveLoginProvider(
        fake_chromium,
        data_root=tmp_path,
    )
    opened = await provider.open(account_key="42")
    configure_profile_probe(
        opened.page,
        payload={"status_code": 0, "user": {}},
    )

    result = await provider.verify(opened)

    assert result.authenticated is False
    assert result.identity_probe_ok is False
    assert result.diagnostic_code == "profile_identity_missing"


@pytest.mark.asyncio
async def test_verify_rejects_unknown_profile_status(
    tmp_path,
    fake_chromium,
):
    provider = DouyinInteractiveLoginProvider(
        fake_chromium,
        data_root=tmp_path,
    )
    opened = await provider.open(account_key="42")
    configure_profile_probe(
        opened.page,
        payload={"status_code": 999, "user": {"uid": "123456"}},
    )

    result = await provider.verify(opened)

    assert result.authenticated is False
    assert result.diagnostic_code == "profile_status_unknown"


@pytest.mark.asyncio
async def test_verify_rejects_profile_probe_http_error(
    tmp_path,
    fake_chromium,
):
    provider = DouyinInteractiveLoginProvider(
        fake_chromium,
        data_root=tmp_path,
    )
    opened = await provider.open(account_key="42")
    configure_profile_probe(
        opened.page,
        http_ok=False,
        http_status=503,
        payload=None,
        error_code="http_error",
    )

    result = await provider.verify(opened)

    assert result.authenticated is False
    assert result.diagnostic_code == "profile_probe_http_error"


@pytest.mark.asyncio
async def test_verify_rejects_profile_probe_json_error(
    tmp_path,
    fake_chromium,
):
    provider = DouyinInteractiveLoginProvider(
        fake_chromium,
        data_root=tmp_path,
    )
    opened = await provider.open(account_key="42")
    configure_profile_probe(
        opened.page,
        payload={},
        error_code="invalid_json",
    )

    result = await provider.verify(opened)

    assert result.authenticated is False
    assert result.diagnostic_code == "profile_probe_invalid_json"


@pytest.mark.asyncio
async def test_verify_rejects_profile_probe_fetch_exception(
    tmp_path,
    fake_chromium,
):
    provider = DouyinInteractiveLoginProvider(
        fake_chromium,
        data_root=tmp_path,
    )
    opened = await provider.open(account_key="42")
    configure_profile_probe(
        opened.page,
        exception=RuntimeError("execution context lost"),
    )

    result = await provider.verify(opened)

    assert result.authenticated is False
    assert result.diagnostic_code == "profile_probe_failed"


@pytest.mark.asyncio
async def test_verify_fetches_profile_self_with_credentials(
    tmp_path,
    fake_chromium,
):
    provider = DouyinInteractiveLoginProvider(
        fake_chromium,
        data_root=tmp_path,
    )
    opened = await provider.open(account_key="42")

    await provider.verify(opened)

    probe_calls = [
        call
        for call in opened.page.evaluate.await_args_list
        if len(call.args) == 2
    ]
    assert len(probe_calls) == 1
    script, url = probe_calls[0].args
    assert url == DOUYIN_PROFILE_PROBE_URL
    assert "window.fetch" in script
    assert "credentials: \"include\"" in script


@pytest.mark.asyncio
async def test_verify_reads_cookies_only_after_identity_probe(
    tmp_path,
    fake_chromium,
):
    events = []
    provider = DouyinInteractiveLoginProvider(
        fake_chromium,
        data_root=tmp_path,
    )
    opened = await provider.open(account_key="42")
    configure_profile_probe(opened.page, events=events)

    async def cookies_after_probe(urls):
        events.append("cookies")
        assert events == ["probe", "cookies"]
        return []

    opened.context.cookies.side_effect = cookies_after_probe

    result = await provider.verify(opened)

    assert result.identity_probe_ok is True
    assert result.has_authenticated_cookie is False
    assert result.authenticated is False


@pytest.mark.asyncio
async def test_verify_requires_protected_page_not_to_return_to_login(
    tmp_path,
    fake_chromium,
):
    provider = DouyinInteractiveLoginProvider(
        fake_chromium,
        data_root=tmp_path,
    )
    opened = await provider.open(account_key="42")
    opened.context.cookies.return_value = authenticated_douyin_cookies()
    opened.page.url = "https://www.douyin.com/passport/web/login/"

    result = await provider.verify(opened)

    assert result.authenticated is False
    assert result.has_authenticated_cookie is True
    assert result.protected_page_ok is False


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 302])
async def test_verify_accepts_cookie_and_protected_page_together(
    tmp_path,
    fake_chromium,
    status,
):
    provider = DouyinInteractiveLoginProvider(
        fake_chromium,
        data_root=tmp_path,
    )
    opened = await provider.open(account_key="42")
    opened.context.cookies.return_value = authenticated_douyin_cookies()
    opened.page.goto.return_value.status = status
    opened.page.url = "https://www.douyin.com/"

    result = await provider.verify(opened)

    assert result.authenticated is True
    assert result.has_authenticated_cookie is True
    assert result.protected_page_ok is True


@pytest.mark.asyncio
async def test_verify_uses_official_homepage_for_server_check(
    tmp_path,
    fake_chromium,
):
    provider = DouyinInteractiveLoginProvider(
        fake_chromium,
        data_root=tmp_path,
    )
    opened = await provider.open(account_key="42")
    opened.page.goto.reset_mock()
    opened.page.url = "https://www.douyin.com/"

    await provider.verify(opened)

    opened.page.goto.assert_awaited_once_with(
        "https://www.douyin.com/",
        wait_until="domcontentloaded",
    )


@pytest.mark.asyncio
async def test_verify_rejects_http_404_response(
    tmp_path,
    fake_chromium,
):
    provider = DouyinInteractiveLoginProvider(
        fake_chromium,
        data_root=tmp_path,
    )
    opened = await provider.open(account_key="42")
    opened.context.cookies.return_value = authenticated_douyin_cookies()
    opened.page.goto.return_value.status = 404
    opened.page.url = "https://www.douyin.com/"

    result = await provider.verify(opened)

    assert result.authenticated is False
    assert result.protected_page_ok is False


@pytest.mark.asyncio
async def test_verify_rejects_failed_homepage_request(
    tmp_path,
    fake_chromium,
):
    provider = DouyinInteractiveLoginProvider(
        fake_chromium,
        data_root=tmp_path,
    )
    opened = await provider.open(account_key="42")
    opened.context.cookies.return_value = authenticated_douyin_cookies()
    opened.page.goto.side_effect = RuntimeError("network failure")

    result = await provider.verify(opened)

    assert result.authenticated is False
    assert result.protected_page_ok is False


@pytest.mark.asyncio
async def test_verify_rejects_external_redirect(
    tmp_path,
    fake_chromium,
):
    provider = DouyinInteractiveLoginProvider(
        fake_chromium,
        data_root=tmp_path,
    )
    opened = await provider.open(account_key="42")
    opened.context.cookies.return_value = authenticated_douyin_cookies()
    opened.page.url = "https://evil.example/"

    result = await provider.verify(opened)

    assert result.authenticated is False
    assert result.protected_page_ok is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "redirect_url",
    [
        "https://passport.douyin.com/",
        "https://login.douyin.com/",
    ],
)
async def test_verify_rejects_login_or_passport_hostname_redirect(
    tmp_path,
    fake_chromium,
    redirect_url,
):
    provider = DouyinInteractiveLoginProvider(
        fake_chromium,
        data_root=tmp_path,
    )
    opened = await provider.open(account_key="42")
    opened.context.cookies.return_value = authenticated_douyin_cookies()
    opened.page.url = redirect_url

    result = await provider.verify(opened)

    assert result.authenticated is False
    assert result.protected_page_ok is False


@pytest.mark.asyncio
async def test_verify_rejects_authenticated_cookie_from_external_domain(
    tmp_path,
    fake_chromium,
):
    provider = DouyinInteractiveLoginProvider(
        fake_chromium,
        data_root=tmp_path,
    )
    opened = await provider.open(account_key="42")
    opened.context.cookies.return_value = authenticated_douyin_cookies(
        domain=".evil.example",
    )

    result = await provider.verify(opened)

    assert result.authenticated is False
    assert result.has_authenticated_cookie is False


@pytest.mark.asyncio
async def test_verify_rejects_expired_authenticated_cookie(
    tmp_path,
    fake_chromium,
):
    provider = DouyinInteractiveLoginProvider(
        fake_chromium,
        data_root=tmp_path,
    )
    opened = await provider.open(account_key="42")
    opened.context.cookies.return_value = authenticated_douyin_cookies(
        expires=1,
    )

    result = await provider.verify(opened)

    assert result.authenticated is False
    assert result.has_authenticated_cookie is False


@pytest.mark.asyncio
async def test_verify_accepts_session_cookie_without_login_status(
    tmp_path,
    fake_chromium,
):
    """抖音 PC web 已登录时不下发 LOGIN_STATUS，缺失不能判为未登录。

    权威判据是服务端探针；Cookie 只复核域、有效期和会话标记。
    """

    provider = DouyinInteractiveLoginProvider(
        fake_chromium,
        data_root=tmp_path,
    )
    opened = await provider.open(account_key="42")
    opened.context.cookies.return_value = authenticated_douyin_cookies(
        include_login_status=False,
    )

    result = await provider.verify(opened)

    assert result.has_authenticated_cookie is True
    assert result.authenticated is True
    assert result.diagnostic_code == ""


@pytest.mark.asyncio
async def test_verify_rejects_explicit_logged_out_login_status(
    tmp_path,
    fake_chromium,
):
    """LOGIN_STATUS 存在且为 "0" 是平台明确的未登录信号。"""

    provider = DouyinInteractiveLoginProvider(
        fake_chromium,
        data_root=tmp_path,
    )
    opened = await provider.open(account_key="42")
    cookies = authenticated_douyin_cookies(include_login_status=False)
    cookies.append(
        {
            "name": "LOGIN_STATUS",
            "value": "0",
            "domain": ".douyin.com",
            "expires": -1,
        }
    )
    opened.context.cookies.return_value = cookies

    result = await provider.verify(opened)

    assert result.has_authenticated_cookie is False
    assert result.authenticated is False
    assert result.diagnostic_code == "cookie_consistency_failed"


@pytest.mark.asyncio
async def test_verify_reads_only_homepage_scoped_cookies(
    tmp_path,
    fake_chromium,
):
    provider = DouyinInteractiveLoginProvider(
        fake_chromium,
        data_root=tmp_path,
    )
    opened = await provider.open(account_key="42")
    opened.context.cookies.return_value = authenticated_douyin_cookies()

    await provider.verify(opened)

    opened.context.cookies.assert_awaited_once_with(
        ["https://www.douyin.com/"],
    )


@pytest.mark.asyncio
async def test_persist_requests_indexed_db_storage_state(
    tmp_path,
    fake_chromium,
):
    provider = DouyinInteractiveLoginProvider(
        fake_chromium,
        data_root=tmp_path,
    )
    opened = await provider.open(account_key="42")

    await provider.persist(opened)

    opened.context.storage_state.assert_awaited_once_with(indexed_db=True)


@pytest.mark.asyncio
async def test_persist_reverifies_session_before_reading_storage_state(
    tmp_path,
    fake_chromium,
    monkeypatch,
):
    provider = DouyinInteractiveLoginProvider(
        fake_chromium,
        data_root=tmp_path,
    )
    opened = await provider.open(account_key="42")
    verification = AuthVerification(
        authenticated=True,
        has_authenticated_cookie=True,
        protected_page_ok=True,
        identity_probe_ok=True,
    )
    events = []

    async def verified(_session):
        events.append("verify")
        return verification

    async def read_storage_state(**kwargs):
        events.append("storage_state")
        return {"cookies": [], "origins": []}

    verify = AsyncMock(side_effect=verified)
    opened.context.storage_state.side_effect = read_storage_state
    monkeypatch.setattr(provider, "verify", verify)

    await provider.persist(opened)

    verify.assert_awaited_once_with(opened)
    assert events == ["verify", "storage_state"]


@pytest.mark.asyncio
async def test_persist_rejects_unverified_session_without_reading_or_writing_state(
    tmp_path,
    fake_chromium,
    monkeypatch,
):
    provider = DouyinInteractiveLoginProvider(
        fake_chromium,
        data_root=tmp_path,
    )
    opened = await provider.open(account_key="42")
    verification = AuthVerification(
        authenticated=False,
        has_authenticated_cookie=True,
        protected_page_ok=True,
        identity_probe_ok=False,
        diagnostic_code="profile_not_logged_in",
    )
    verify = AsyncMock(return_value=verification)
    writer = MagicMock()
    monkeypatch.setattr(provider, "verify", verify)
    monkeypatch.setattr(
        browser_providers,
        "atomic_write_private_json",
        writer,
    )

    with pytest.raises(
        RuntimeError,
        match="interactive_login_not_authenticated",
    ):
        await provider.persist(opened)

    verify.assert_awaited_once_with(opened)
    opened.context.storage_state.assert_not_awaited()
    writer.assert_not_called()
    assert not opened.storage_state_path.exists()


@pytest.mark.asyncio
async def test_persist_atomically_replaces_storage_state_file(
    tmp_path,
    fake_chromium,
    monkeypatch,
):
    provider = DouyinInteractiveLoginProvider(
        fake_chromium,
        data_root=tmp_path,
    )
    opened = await provider.open(account_key="42")
    state = {
        "cookies": [{"name": "sessionid", "value": "private-cookie"}],
        "origins": [{"origin": "https://www.douyin.com", "localStorage": []}],
    }
    opened.context.storage_state.return_value = state
    real_replace = interactive_login.os.replace
    replacements: list[tuple[Path, Path]] = []

    def record_replace(source, destination):
        replacements.append((Path(source), Path(destination)))
        real_replace(source, destination)

    monkeypatch.setattr(interactive_login.os, "replace", record_replace)

    await provider.persist(opened)

    assert len(replacements) == 1
    temporary_path, destination_path = replacements[0]
    assert temporary_path.parent == opened.storage_state_path.parent
    assert destination_path == opened.storage_state_path
    assert json.loads(opened.storage_state_path.read_text("utf-8")) == state
    assert not temporary_path.exists()


@pytest.mark.asyncio
async def test_persist_returns_metadata_without_cookie_value_in_repr(
    tmp_path,
    fake_chromium,
    caplog,
):
    provider = DouyinInteractiveLoginProvider(
        fake_chromium,
        data_root=tmp_path,
    )
    opened = await provider.open(account_key="42")
    opened.context.storage_state.return_value = {
        "cookies": [{"name": "sessionid", "value": "private-cookie"}],
        "origins": [{"origin": "https://www.douyin.com"}],
    }

    persisted = await provider.persist(opened)

    assert persisted.cookies == [
        {"name": "sessionid", "value": "private-cookie"},
    ]
    assert persisted.cookie_count == 1
    assert persisted.origin_count == 1
    assert persisted.storage_state_path == opened.storage_state_path
    assert "private-cookie" not in repr(persisted)
    assert "private-cookie" not in caplog.text


def test_private_directory_posix_uses_mode_0700(
    monkeypatch,
):
    secure_directory = getattr(
        interactive_login,
        "secure_private_directory",
        None,
    )
    assert secure_directory is not None
    directory = MagicMock()
    monkeypatch.setattr(interactive_login.os, "name", "posix")

    secure_directory(directory)

    directory.mkdir.assert_called_once_with(parents=True, exist_ok=True)
    directory.chmod.assert_called_once_with(0o700)


def test_private_directory_windows_uses_process_token_user_icacls(
    monkeypatch,
):
    secure_directory = getattr(
        interactive_login,
        "secure_private_directory",
        None,
    )
    assert secure_directory is not None
    directory = MagicMock()
    directory.__str__.return_value = r"C:\private"
    runner = MagicMock()
    monkeypatch.setattr(interactive_login.os, "name", "nt")
    monkeypatch.setattr(
        interactive_login,
        "subprocess",
        MagicMock(run=runner),
        raising=False,
    )
    monkeypatch.setattr(
        interactive_login,
        "getpass",
        MagicMock(
            getuser=MagicMock(return_value="ROG"),
        ),
        raising=False,
    )
    token_user = MagicMock(return_value="CodexSandboxOffline")
    monkeypatch.setattr(
        interactive_login,
        "_windows_current_user",
        token_user,
        raising=False,
    )

    secure_directory(directory)

    token_user.assert_called_once_with()
    runner.assert_called_once_with(
        [
            "icacls",
            r"C:\private",
            "/inheritance:r",
            "/grant:r",
            "CodexSandboxOffline:(OI)(CI)F",
        ],
        check=True,
        capture_output=True,
        text=True,
        shell=False,
    )


def test_windows_current_user_reads_sam_compatible_token_identity(
    monkeypatch,
):
    expected = r"LAPTOP\CodexSandboxOffline"

    def get_user_name(_name_format, buffer, size_pointer):
        if buffer is None:
            size_pointer._obj.value = len(expected) + 1
            return 0
        buffer.value = expected
        return 1

    monkeypatch.setattr(
        interactive_login.ctypes,
        "windll",
        SimpleNamespace(
            secur32=SimpleNamespace(GetUserNameExW=get_user_name),
        ),
    )

    assert interactive_login._windows_current_user() == expected


def test_private_directory_posix_permission_failure_warns(
    monkeypatch,
    caplog,
):
    secure_directory = getattr(
        interactive_login,
        "secure_private_directory",
        None,
    )
    assert secure_directory is not None
    directory = MagicMock()
    directory.__str__.return_value = "/private"
    directory.chmod.side_effect = OSError("denied")
    monkeypatch.setattr(interactive_login.os, "name", "posix")
    caplog.set_level(logging.WARNING)

    secure_directory(directory)

    assert "私密目录权限收紧失败" in caplog.text
    assert "/private" in caplog.text


def test_private_directory_windows_acl_failure_warns(
    monkeypatch,
    caplog,
):
    secure_directory = getattr(
        interactive_login,
        "secure_private_directory",
        None,
    )
    assert secure_directory is not None
    directory = MagicMock()
    directory.__str__.return_value = r"C:\private"
    runner = MagicMock(side_effect=OSError("icacls unavailable"))
    monkeypatch.setattr(interactive_login.os, "name", "nt")
    monkeypatch.setattr(
        interactive_login,
        "subprocess",
        MagicMock(run=runner),
        raising=False,
    )
    monkeypatch.setattr(
        interactive_login,
        "getpass",
        MagicMock(
            getuser=MagicMock(return_value="ROG"),
        ),
        raising=False,
    )
    caplog.set_level(logging.WARNING)

    secure_directory(directory)

    assert "Windows ACL 收紧失败" in caplog.text
    assert r"C:\private" in caplog.text


def test_private_file_uses_mode_0600_and_warns_on_failure(
    monkeypatch,
    caplog,
):
    secure_file = getattr(
        interactive_login,
        "secure_private_file",
        None,
    )
    assert secure_file is not None
    private_file = MagicMock()
    private_file.__str__.return_value = "/private/state.json"
    private_file.chmod.side_effect = OSError("denied")
    caplog.set_level(logging.WARNING)

    secure_file(private_file)

    private_file.chmod.assert_called_once_with(0o600)
    assert "私密文件权限收紧失败" in caplog.text


def test_atomic_write_secures_auth_directory(
    tmp_path,
    monkeypatch,
):
    secure_directory = MagicMock(
        side_effect=lambda directory: directory.mkdir(
            parents=True,
            exist_ok=True,
        )
    )
    monkeypatch.setattr(
        interactive_login,
        "secure_private_directory",
        secure_directory,
        raising=False,
    )
    destination = tmp_path / "auth_states" / "douyin" / "state.json"

    interactive_login.atomic_write_private_json(
        destination,
        {"cookies": [], "origins": []},
    )

    secure_directory.assert_called_once_with(destination.parent)


def test_interactive_browser_session_repr_hides_browser_and_private_paths():
    profile_dir = Path(r"C:\private\profile")
    storage_state_path = Path(r"C:\private\state.json")
    session = InteractiveBrowserSession(
        account_key="account-1",
        context=MagicMock(name="private-context"),
        page=MagicMock(name="private-page"),
        profile_dir=profile_dir,
        storage_state_path=storage_state_path,
    )

    rendered = repr(session)

    assert "private-context" not in rendered
    assert "private-page" not in rendered
    assert profile_dir.as_posix() not in rendered
    assert storage_state_path.as_posix() not in rendered


def test_persisted_auth_state_repr_hides_private_path_and_cookie_values():
    storage_state_path = Path(r"C:\private\state.json")
    persisted = PersistedAuthState(
        storage_state_path=storage_state_path,
        cookie_count=1,
        origin_count=1,
        cookies=[
            {"name": "sessionid", "value": "private-cookie"},
        ],
    )

    rendered = repr(persisted)

    assert storage_state_path.as_posix() not in rendered
    assert "private-cookie" not in rendered


@pytest.mark.asyncio
async def test_persist_rejects_storage_path_not_built_for_account(
    tmp_path,
    fake_chromium,
):
    provider = DouyinInteractiveLoginProvider(
        fake_chromium,
        data_root=tmp_path,
    )
    opened = await provider.open(account_key="42")
    outside_path = tmp_path / "frontend-selected.json"
    tampered = replace(opened, storage_state_path=outside_path)

    with pytest.raises(ValueError, match="storage state path"):
        await provider.persist(tampered)

    assert not outside_path.exists()


@pytest.mark.asyncio
async def test_same_account_cannot_hold_two_leases():
    leases = AccountLeaseManager()
    first = await leases.acquire("douyin", 1, owner="login:a")

    with pytest.raises(AccountBusyError):
        await leases.acquire("douyin", 1, owner="pipeline:b")

    await first.release()


@pytest.mark.asyncio
async def test_concurrent_acquire_is_atomic():
    leases = AccountLeaseManager()
    start = asyncio.Event()

    async def compete(owner: str):
        await start.wait()
        return await leases.acquire("douyin", 1, owner=owner)

    attempts = [
        asyncio.create_task(compete("login:a")),
        asyncio.create_task(compete("pipeline:b")),
    ]
    await asyncio.sleep(0)
    start.set()
    results = await asyncio.gather(*attempts, return_exceptions=True)

    acquired = [result for result in results if not isinstance(result, BaseException)]
    rejected = [result for result in results if isinstance(result, AccountBusyError)]
    assert len(acquired) == 1
    assert len(rejected) == 1

    await acquired[0].release()


@pytest.mark.asyncio
async def test_numeric_key_concurrency_normalizes_id_and_zero_padded_aliases():
    leases = AccountLeaseManager()
    start = asyncio.Event()

    async def compete(account_key: int | str, owner: str):
        await start.wait()
        return await leases.acquire("douyin", account_key, owner=owner)

    attempts = [
        asyncio.create_task(compete(7, "login:a")),
        asyncio.create_task(compete("7", "pipeline:b")),
        asyncio.create_task(compete("007", "check:c")),
        asyncio.create_task(compete(" 007 ", "login:d")),
    ]
    await asyncio.sleep(0)
    start.set()
    results = await asyncio.gather(*attempts, return_exceptions=True)

    acquired = [result for result in results if not isinstance(result, BaseException)]
    rejected = [result for result in results if isinstance(result, AccountBusyError)]
    for lease in acquired:
        await lease.release()

    assert len(acquired) == 1
    assert len(rejected) == 3


@pytest.mark.asyncio
async def test_lease_platform_is_normalized_for_account_key():
    leases = AccountLeaseManager()
    first = await leases.acquire(" DY ", 1, owner="login:a")

    with pytest.raises(AccountBusyError):
        await leases.acquire("douyin", 1, owner="pipeline:b")

    await first.release()


@pytest.mark.asyncio
async def test_lease_rejects_invalid_platform():
    leases = AccountLeaseManager()

    with pytest.raises(ValueError):
        await leases.acquire("instagram", 1, owner="pipeline:a")


@pytest.mark.asyncio
async def test_new_account_alias_cannot_conflict_with_account_id_key():
    leases = AccountLeaseManager()
    first = await leases.acquire("douyin", 7, owner="pipeline:a")

    with pytest.raises(AccountBusyError):
        await leases.acquire("douyin", " 7 ", owner="login:b")

    await first.release()


@pytest.mark.asyncio
async def test_busy_error_owner_security_redacts_owner_details():
    leases = AccountLeaseManager()
    first = await leases.acquire(
        "douyin",
        1,
        owner="login:current-secret-token",
    )

    with pytest.raises(AccountBusyError) as error:
        await leases.acquire(
            "douyin",
            1,
            owner="pipeline:requested-secret-token",
        )

    assert error.value.owner == "pipeline"
    assert error.value.current_owner == "login"
    public_error = f"{error.value!s} {error.value!r}"
    assert "current-secret-token" not in public_error
    assert "requested-secret-token" not in public_error
    await first.release()


@pytest.mark.asyncio
async def test_lease_owner_security_rejects_unknown_purpose():
    leases = AccountLeaseManager()

    with pytest.raises(ValueError):
        await leases.acquire("douyin", 1, owner="maintenance:secret-token")


@pytest.mark.asyncio
async def test_lease_release_is_idempotent():
    leases = AccountLeaseManager()
    first = await leases.acquire("douyin", 1, owner="login:a")

    await first.release()
    await first.release()

    replacement = await leases.acquire("douyin", 1, owner="pipeline:b")
    await replacement.release()


@pytest.mark.asyncio
async def test_lease_immutable_identity_prevents_original_key_leak():
    leases = AccountLeaseManager()
    lease = await leases.acquire("douyin", 1, owner="login:a")

    with pytest.raises(FrozenInstanceError):
        lease.account_key = "2"

    await lease.release()
    replacement = await leases.acquire("douyin", 1, owner="pipeline:b")
    await replacement.release()


@pytest.mark.asyncio
async def test_lease_async_context_manager_releases_account():
    leases = AccountLeaseManager()

    async with await leases.acquire("douyin", 1, owner="login:a"):
        with pytest.raises(AccountBusyError):
            await leases.acquire("douyin", 1, owner="pipeline:b")

    replacement = await leases.acquire("douyin", 1, owner="pipeline:b")
    await replacement.release()


def test_login_session_state_transitions():
    session = LoginSession.new("douyin", "marketing_01")
    session.transition("waiting_user")
    session.transition("verifying")
    session.transition("persisted")
    session.authenticated = True
    session.transition("confirmed")

    assert session.status == "confirmed"


def test_login_session_rejects_invalid_transition():
    session = LoginSession.new("douyin", "marketing_01")

    with pytest.raises(InvalidLoginTransition):
        session.transition("confirmed")


@pytest.mark.parametrize("terminal", ["confirmed", "failed", "expired", "cancelled"])
def test_login_session_terminal_state_cannot_transition(terminal):
    session = LoginSession.new("douyin", "marketing_01")
    session.status = terminal

    with pytest.raises(InvalidLoginTransition):
        session.transition("failed")


def test_login_session_uses_strict_allowed_transitions():
    assert ALLOWED_TRANSITIONS == {
        "launching": {"waiting_user", "failed", "cancelled"},
        "waiting_user": {"verifying", "failed", "expired", "cancelled"},
        "verifying": {
            "waiting_user",
            "persisted",
            "failed",
            "expired",
            "cancelled",
        },
        "persisted": {"confirmed", "failed"},
        "confirmed": set(),
        "failed": set(),
        "expired": set(),
        "cancelled": set(),
    }


def test_login_session_invariant_expired_session_cannot_advance():
    session = LoginSession.new("douyin", "marketing_01")
    session.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

    with pytest.raises(interactive_login.SessionExpiredError):
        session.transition("waiting_user")

    assert session.status == "expired"


def test_login_session_invariant_entering_persisted_sets_flag():
    session = LoginSession.new("douyin", "marketing_01")
    session.transition("waiting_user")
    session.transition("verifying")

    session.transition("persisted")

    assert session.persisted is True


def test_login_session_invariant_confirmed_requires_authenticated():
    session = LoginSession.new("douyin", "marketing_01")
    session.status = "persisted"
    session.persisted = True

    with pytest.raises(InvalidLoginTransition):
        session.transition("confirmed")


def test_login_session_invariant_confirmed_requires_persisted():
    session = LoginSession.new("douyin", "marketing_01")
    session.status = "persisted"
    session.authenticated = True

    with pytest.raises(InvalidLoginTransition):
        session.transition("confirmed")


def test_login_session_new_records_initial_state():
    session = LoginSession.new(
        " DY ",
        " marketing_01 ",
        account_id=42,
    )

    assert session.platform == "douyin"
    assert session.account_alias == "marketing_01"
    assert session.account_id == 42
    assert session.status == "launching"
    assert session.authenticated is False
    assert session.persisted is False
    assert session.started_at.tzinfo == timezone.utc
    assert session.expires_at > session.started_at


def test_login_session_new_defaults_provider_and_error_fields_to_empty_strings():
    session = LoginSession.new("douyin", "marketing_01")

    assert session.browser_provider == ""
    assert session.browser_profile_id == ""
    assert session.error_code == ""
    assert session.error_message == ""


def test_login_session_new_preserves_provider_and_error_fields():
    session = LoginSession.new(
        "douyin",
        "marketing_01",
        browser_provider="playwright",
        browser_profile_id="douyin-profile-42",
        error_code="browser_launch_failed",
        error_message="Browser could not be opened",
    )

    assert session.browser_provider == "playwright"
    assert session.browser_profile_id == "douyin-profile-42"
    assert session.error_code == "browser_launch_failed"
    assert session.error_message == "Browser could not be opened"


def test_login_session_new_generates_random_token():
    first = LoginSession.new("douyin", "marketing_01")
    second = LoginSession.new("douyin", "marketing_01")

    assert first.token
    assert first.token != second.token


def test_login_session_new_rejects_invalid_platform():
    with pytest.raises(ValueError):
        LoginSession.new("instagram", "marketing_01")


class FakeInteractiveLoginProvider:
    provider_name = "fake-interactive"

    def __init__(
        self,
        *,
        browser_kind: str = "playwright-persistent",
        authenticated: bool = True,
        persist_error: BaseException | None = None,
        open_error: BaseException | None = None,
        open_gate: asyncio.Event | None = None,
    ) -> None:
        self.interactive_browser_kind = browser_kind
        self.authenticated = authenticated
        self.persist_error = persist_error
        self.open_error = open_error
        self.open_gate = open_gate
        self.check_interactive_available = AsyncMock(
            return_value=browser_providers.BrowserAvailability(True)
        )
        self.open_interactive_login = AsyncMock(side_effect=self._open)
        self.verify_interactive_login = AsyncMock(side_effect=self._verify)
        self.persist_interactive_login = AsyncMock(side_effect=self._persist)
        self.close_interactive_login = AsyncMock(side_effect=self._close)
        self.opened = InteractiveBrowserSession(
            account_key="account",
            context=MagicMock(name="context"),
            page=MagicMock(name="page"),
            profile_dir=Path("private-profile"),
            storage_state_path=Path("private-state.json"),
        )

    async def _open(self, *, account_key, account):
        if self.open_gate is not None:
            await self.open_gate.wait()
        if self.open_error is not None:
            raise self.open_error
        return replace(self.opened, account_key=str(account_key))

    async def _verify(self, session):
        return AuthVerification(
            authenticated=self.authenticated,
            has_authenticated_cookie=self.authenticated,
            protected_page_ok=self.authenticated,
            identity_probe_ok=self.authenticated,
            diagnostic_code="" if self.authenticated else "not_logged_in",
        )

    async def _persist(self, session):
        if self.persist_error is not None:
            raise self.persist_error
        return PersistedAuthState(
            storage_state_path=session.storage_state_path,
            cookie_count=1,
            origin_count=1,
            cookies=[{"name": "sessionid", "value": "private-cookie"}],
        )

    async def _close(self, session):
        return None


class FakeFingerprintInteractiveLoginProvider(
    FakeInteractiveLoginProvider,
    FingerprintInteractiveLoginProvider,
):
    provider_id = "fake-fingerprint"

    def __init__(self, **kwargs):
        super().__init__(
            browser_kind="fingerprint",
            **kwargs,
        )


def make_interactive_login_service(
    provider,
    *,
    platform="douyin",
    leases=None,
    account_updater=None,
    timeout_seconds=300,
    operation_cancel_grace_seconds=0.05,
):
    registry = BrowserProviderRegistry(
        interactive_providers={platform: provider},
    )
    return InteractiveLoginService(
        providers=registry,
        leases=leases or AccountLeaseManager(),
        account_updater=account_updater,
        timeout_seconds=timeout_seconds,
        operation_cancel_grace_seconds=(
            operation_cancel_grace_seconds
        ),
    )


@pytest.mark.asyncio
async def test_tiktok_login_requires_registered_fingerprint_provider():
    douyin = FakeInteractiveLoginProvider()
    registry = BrowserProviderRegistry(
        interactive_providers={"douyin": douyin},
    )
    service = InteractiveLoginService(
        providers=registry,
        leases=AccountLeaseManager(),
    )

    with pytest.raises(LoginUnavailableError) as error:
        await service.start(platform="tiktok", account_alias="tk_01")

    assert error.value.code == "fingerprint_provider_unavailable"
    douyin.open_interactive_login.assert_not_awaited()
    await service.aclose()


@pytest.mark.asyncio
async def test_registered_tiktok_interactive_provider_never_calls_douyin():
    douyin = FakeInteractiveLoginProvider()
    tiktok = FakeFingerprintInteractiveLoginProvider()
    registry = BrowserProviderRegistry(
        interactive_providers={
            "douyin": douyin,
            "tiktok": tiktok,
        },
    )
    service = InteractiveLoginService(
        providers=registry,
        leases=AccountLeaseManager(),
        account_resolver=lambda platform, alias: {
            "id": 7,
            "platform": platform,
            "username": alias,
            "browser_provider": "fake-fingerprint",
            "browser_profile_id": "profile-7",
        },
    )

    session = await service.start(
        platform="tiktok",
        account_alias="tk_01",
    )

    assert session.platform == "tiktok"
    tiktok.open_interactive_login.assert_awaited_once()
    douyin.open_interactive_login.assert_not_awaited()
    await service.cancel(session.token)
    await service.aclose()


@pytest.mark.asyncio
async def test_tiktok_rejects_registered_non_fingerprint_provider():
    ordinary_playwright = FakeInteractiveLoginProvider(
        browser_kind="fingerprint",
    )
    service = make_interactive_login_service(
        ordinary_playwright,
        platform="tiktok",
    )

    with pytest.raises(LoginUnavailableError) as error:
        await service.start(platform="tiktok", account_alias="tk_01")

    assert error.value.code == "fingerprint_provider_unavailable"
    ordinary_playwright.open_interactive_login.assert_not_awaited()
    await service.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("browser_provider", "browser_profile_id"),
    [
        ("fake-fingerprint", ""),
        ("wrong-provider", "profile-7"),
    ],
)
async def test_tiktok_fingerprint_account_binding_fails_closed(
    browser_provider,
    browser_profile_id,
):
    provider = FakeFingerprintInteractiveLoginProvider()
    registry = BrowserProviderRegistry(
        interactive_providers={"tiktok": provider},
    )
    service = InteractiveLoginService(
        providers=registry,
        leases=AccountLeaseManager(),
        account_resolver=lambda platform, alias: {
            "id": 7,
            "platform": platform,
            "username": alias,
            "browser_provider": browser_provider,
            "browser_profile_id": browser_profile_id,
        },
    )

    with pytest.raises(LoginUnavailableError) as error:
        await service.start(platform="tiktok", account_alias="tk_01")

    assert error.value.code == "fingerprint_provider_unavailable"
    provider.open_interactive_login.assert_not_awaited()
    await service.aclose()


@pytest.mark.asyncio
async def test_nominal_tiktok_fingerprint_adapter_with_bound_profile_opens():
    provider = FakeFingerprintInteractiveLoginProvider()
    registry = BrowserProviderRegistry()
    registry.register_tiktok_fingerprint_interactive(provider)
    service = InteractiveLoginService(
        providers=registry,
        leases=AccountLeaseManager(),
        account_resolver=lambda platform, alias: {
            "id": 7,
            "platform": platform,
            "username": alias,
            "browser_provider": "fake-fingerprint",
            "browser_profile_id": "profile-7",
        },
    )

    session = await service.start(
        platform="tiktok",
        account_alias="tk_01",
    )

    assert session.status == "waiting_user"
    provider.open_interactive_login.assert_awaited_once()
    await service.cancel(session.token)
    await service.aclose()


def test_registry_rejects_non_nominal_tiktok_fingerprint_adapter():
    registry = BrowserProviderRegistry()
    spoofed = FakeInteractiveLoginProvider(
        browser_kind="fingerprint",
    )

    with pytest.raises(TypeError):
        registry.register_tiktok_fingerprint_interactive(spoofed)


@pytest.mark.asyncio
async def test_login_false_verification_returns_to_waiting_without_persist():
    provider = FakeInteractiveLoginProvider(authenticated=False)
    service = make_interactive_login_service(provider)
    session = await service.start(
        platform="douyin",
        account_alias="dy_01",
    )

    result = await service.verify(session.token)

    assert result.status == "waiting_user"
    assert result.authenticated is False
    assert result.persisted is False
    provider.persist_interactive_login.assert_not_awaited()
    provider.close_interactive_login.assert_not_awaited()
    await service.cancel(session.token)
    await service.aclose()


@pytest.mark.asyncio
async def test_verified_login_persists_and_updates_account_before_confirmed():
    events = []
    provider = FakeInteractiveLoginProvider()

    async def update_account(account, persisted, verification):
        assert persisted.cookie_count == 1
        assert verification.authenticated is True
        events.append("account_updated")

    async def record_persist(session):
        return await _record_persist(provider, session, events)

    provider.persist_interactive_login.side_effect = record_persist
    service = make_interactive_login_service(
        provider,
        account_updater=update_account,
    )
    session = await service.start(
        platform="douyin",
        account_alias="dy_01",
    )

    result = await service.verify(session.token)

    assert result.status == "confirmed"
    assert result.authenticated is True
    assert result.persisted is True
    assert events == ["persisted", "account_updated"]
    provider.close_interactive_login.assert_awaited_once()
    await service.aclose()


async def _record_persist(provider, session, events):
    events.append("persisted")
    return await provider._persist(session)


@pytest.mark.asyncio
async def test_persist_failure_never_confirms_and_closes_and_releases():
    leases = AccountLeaseManager()
    provider = FakeInteractiveLoginProvider(
        persist_error=RuntimeError("private persistence detail")
    )
    service = make_interactive_login_service(
        provider,
        leases=leases,
        account_updater=lambda *_args: None,
    )
    session = await service.start(
        platform="douyin",
        account_alias="dy_01",
    )

    with pytest.raises(LoginOperationError) as error:
        await service.verify(session.token)

    assert error.value.code == "persistence_failed"
    status = await service.status(session.token)
    assert status.status == "failed"
    assert status.persisted is False
    provider.close_interactive_login.assert_awaited_once()
    replacement = await leases.acquire(
        "douyin",
        "dy_01",
        owner="pipeline:test",
    )
    await replacement.release()
    await service.aclose()


@pytest.mark.asyncio
async def test_account_update_failure_never_confirms_and_releases():
    leases = AccountLeaseManager()
    provider = FakeInteractiveLoginProvider()

    async def fail_update(*_args):
        raise RuntimeError("database unavailable")

    service = make_interactive_login_service(
        provider,
        leases=leases,
        account_updater=fail_update,
    )
    session = await service.start(
        platform="douyin",
        account_alias="dy_01",
    )

    with pytest.raises(LoginOperationError) as error:
        await service.verify(session.token)

    assert error.value.code == "account_update_failed"
    status = await service.status(session.token)
    assert status.status == "failed"
    assert status.status != "confirmed"
    provider.close_interactive_login.assert_awaited_once()
    replacement = await leases.acquire(
        "douyin",
        "dy_01",
        owner="pipeline:test",
    )
    await replacement.release()
    await service.aclose()


@pytest.mark.asyncio
async def test_missing_account_updater_never_confirms():
    provider = FakeInteractiveLoginProvider()
    service = make_interactive_login_service(provider)
    session = await service.start(
        platform="douyin",
        account_alias="dy_01",
    )

    with pytest.raises(LoginOperationError) as error:
        await service.verify(session.token)

    assert error.value.code == "account_update_failed"
    assert (await service.status(session.token)).status == "failed"
    provider.close_interactive_login.assert_awaited_once()
    await service.aclose()


@pytest.mark.asyncio
async def test_confirm_releases_lease_for_pipeline():
    leases = AccountLeaseManager()
    provider = FakeInteractiveLoginProvider()
    service = make_interactive_login_service(
        provider,
        leases=leases,
        account_updater=lambda *_args: None,
    )
    session = await service.start(
        platform="douyin",
        account_alias="dy_01",
    )

    await service.verify(session.token)

    replacement = await leases.acquire(
        "douyin",
        "dy_01",
        owner="pipeline:test",
    )
    await replacement.release()
    await service.aclose()


@pytest.mark.asyncio
async def test_cancel_is_idempotent_and_closes_and_releases_once():
    leases = AccountLeaseManager()
    provider = FakeInteractiveLoginProvider()
    service = make_interactive_login_service(provider, leases=leases)
    session = await service.start(
        platform="douyin",
        account_alias="dy_01",
    )

    first = await service.cancel(session.token)
    second = await service.cancel(session.token)
    status = await service.status(session.token)

    assert first.status == "cancelled"
    assert second.status == "cancelled"
    assert status.status == "cancelled"
    provider.close_interactive_login.assert_awaited_once()
    replacement = await leases.acquire(
        "douyin",
        "dy_01",
        owner="pipeline:test",
    )
    await replacement.release()
    await service.aclose()


@pytest.mark.asyncio
async def test_timeout_expires_session_and_closes_and_releases():
    leases = AccountLeaseManager()
    provider = FakeInteractiveLoginProvider()
    service = make_interactive_login_service(
        provider,
        leases=leases,
        timeout_seconds=0.01,
    )
    session = await service.start(
        platform="douyin",
        account_alias="dy_01",
    )

    await asyncio.sleep(0.03)
    result = await service.status(session.token)

    assert result.status == "expired"
    provider.close_interactive_login.assert_awaited_once()
    replacement = await leases.acquire(
        "douyin",
        "dy_01",
        owner="pipeline:test",
    )
    await replacement.release()
    await service.aclose()


@pytest.mark.asyncio
async def test_open_failure_releases_lease_without_close_of_missing_session():
    leases = AccountLeaseManager()
    provider = FakeInteractiveLoginProvider(
        open_error=RuntimeError("browser startup detail")
    )
    service = make_interactive_login_service(provider, leases=leases)

    with pytest.raises(LoginOperationError) as error:
        await service.start(platform="douyin", account_alias="dy_01")

    assert error.value.code == "browser_open_failed"
    provider.close_interactive_login.assert_not_awaited()
    replacement = await leases.acquire(
        "douyin",
        "dy_01",
        owner="pipeline:test",
    )
    await replacement.release()
    await service.aclose()


@pytest.mark.asyncio
async def test_open_timeout_releases_lease():
    leases = AccountLeaseManager()
    provider = FakeInteractiveLoginProvider(open_gate=asyncio.Event())
    service = make_interactive_login_service(
        provider,
        leases=leases,
        timeout_seconds=0.01,
    )

    with pytest.raises(LoginOperationError) as error:
        await service.start(platform="douyin", account_alias="dy_01")

    assert error.value.code == "browser_open_timeout"
    replacement = await leases.acquire(
        "douyin",
        "dy_01",
        owner="pipeline:test",
    )
    await replacement.release()
    await service.aclose()


@pytest.mark.asyncio
async def test_verify_exception_fails_closes_and_releases():
    leases = AccountLeaseManager()
    provider = FakeInteractiveLoginProvider()
    provider.verify_interactive_login.side_effect = RuntimeError(
        "private verification detail"
    )
    service = make_interactive_login_service(provider, leases=leases)
    session = await service.start(
        platform="douyin",
        account_alias="dy_01",
    )

    with pytest.raises(LoginOperationError) as error:
        await service.verify(session.token)

    assert error.value.code == "verification_failed"
    assert (await service.status(session.token)).status == "failed"
    provider.close_interactive_login.assert_awaited_once()
    replacement = await leases.acquire(
        "douyin",
        "dy_01",
        owner="pipeline:test",
    )
    await replacement.release()
    await service.aclose()


@pytest.mark.asyncio
async def test_concurrent_verify_persists_only_once():
    provider = FakeInteractiveLoginProvider()
    verify_gate = asyncio.Event()

    async def delayed_verify(session):
        await verify_gate.wait()
        return await provider._verify(session)

    provider.verify_interactive_login.side_effect = delayed_verify
    service = make_interactive_login_service(
        provider,
        account_updater=lambda *_args: None,
    )
    session = await service.start(
        platform="douyin",
        account_alias="dy_01",
    )

    attempts = [
        asyncio.create_task(service.verify(session.token)),
        asyncio.create_task(service.verify(session.token)),
    ]
    await asyncio.sleep(0)
    verify_gate.set()
    results = await asyncio.gather(*attempts)

    assert [result.status for result in results] == [
        "confirmed",
        "confirmed",
    ]
    provider.persist_interactive_login.assert_awaited_once()
    provider.close_interactive_login.assert_awaited_once()
    await service.aclose()


@pytest.mark.asyncio
async def test_service_aclose_cleans_timeout_tasks_resources_and_leases():
    leases = AccountLeaseManager()
    provider = FakeInteractiveLoginProvider()
    service = make_interactive_login_service(
        provider,
        leases=leases,
        timeout_seconds=60,
    )
    session = await service.start(
        platform="douyin",
        account_alias="dy_01",
    )

    await service.aclose()

    assert not service._timeout_tasks
    assert (await service.status(session.token)).status == "cancelled"
    provider.close_interactive_login.assert_awaited_once()
    replacement = await leases.acquire(
        "douyin",
        "dy_01",
        owner="pipeline:test",
    )
    await replacement.release()


@pytest.mark.asyncio
async def test_hanging_verify_is_cancelled_by_timeout_without_locking_status():
    leases = AccountLeaseManager()
    provider = FakeInteractiveLoginProvider()
    verify_started = asyncio.Event()
    never_finish = asyncio.Event()

    async def hanging_verify(_session):
        verify_started.set()
        await never_finish.wait()

    provider.verify_interactive_login.side_effect = hanging_verify
    service = make_interactive_login_service(
        provider,
        leases=leases,
        timeout_seconds=0.02,
    )
    session = await service.start(
        platform="douyin",
        account_alias="dy_01",
    )
    verify_task = asyncio.create_task(service.verify(session.token))
    await asyncio.wait_for(verify_started.wait(), timeout=0.2)

    await asyncio.sleep(0.03)
    result = await asyncio.wait_for(
        service.status(session.token),
        timeout=0.2,
    )
    verify_result = await asyncio.wait_for(verify_task, timeout=0.2)

    assert result.status == "expired"
    assert verify_result.status == "expired"
    provider.close_interactive_login.assert_awaited_once()
    replacement = await leases.acquire(
        "douyin",
        "dy_01",
        owner="pipeline:test",
    )
    await replacement.release()
    await service.aclose()


@pytest.mark.asyncio
async def test_cancel_interrupts_hanging_verify_and_cleans_once():
    leases = AccountLeaseManager()
    provider = FakeInteractiveLoginProvider()
    verify_started = asyncio.Event()
    never_finish = asyncio.Event()

    async def hanging_verify(_session):
        verify_started.set()
        await never_finish.wait()

    provider.verify_interactive_login.side_effect = hanging_verify
    service = make_interactive_login_service(
        provider,
        leases=leases,
    )
    session = await service.start(
        platform="douyin",
        account_alias="dy_01",
    )
    verify_task = asyncio.create_task(service.verify(session.token))
    await asyncio.wait_for(verify_started.wait(), timeout=0.2)

    cancelled = await asyncio.wait_for(
        service.cancel(session.token),
        timeout=0.2,
    )
    verify_result = await asyncio.wait_for(verify_task, timeout=0.2)

    assert cancelled.status == "cancelled"
    assert verify_result.status == "cancelled"
    provider.close_interactive_login.assert_awaited_once()
    replacement = await leases.acquire(
        "douyin",
        "dy_01",
        owner="pipeline:test",
    )
    await replacement.release()
    await service.aclose()


@pytest.mark.asyncio
async def test_aclose_interrupts_hanging_verify_without_task_leaks():
    leases = AccountLeaseManager()
    provider = FakeInteractiveLoginProvider()
    verify_started = asyncio.Event()
    never_finish = asyncio.Event()

    async def hanging_verify(_session):
        verify_started.set()
        await never_finish.wait()

    provider.verify_interactive_login.side_effect = hanging_verify
    service = make_interactive_login_service(
        provider,
        leases=leases,
    )
    session = await service.start(
        platform="douyin",
        account_alias="dy_01",
    )
    verify_task = asyncio.create_task(service.verify(session.token))
    await asyncio.wait_for(verify_started.wait(), timeout=0.2)

    await asyncio.wait_for(service.aclose(), timeout=0.2)
    verify_result = await asyncio.wait_for(verify_task, timeout=0.2)

    assert verify_result.status == "cancelled"
    assert not service._timeout_tasks
    provider.close_interactive_login.assert_awaited_once()
    replacement = await leases.acquire(
        "douyin",
        "dy_01",
        owner="pipeline:test",
    )
    await replacement.release()


@pytest.mark.asyncio
async def test_aclose_cancels_start_blocked_in_account_resolver():
    provider = FakeInteractiveLoginProvider()
    resolver_started = asyncio.Event()
    never_finish = asyncio.Event()

    async def resolver(_platform, _alias):
        resolver_started.set()
        await never_finish.wait()

    service = InteractiveLoginService(
        providers=BrowserProviderRegistry(
            interactive_providers={"douyin": provider},
        ),
        leases=AccountLeaseManager(),
        account_resolver=resolver,
    )
    start_task = asyncio.create_task(
        service.start(platform="douyin", account_alias="dy_01")
    )
    await asyncio.wait_for(resolver_started.wait(), timeout=0.2)

    await asyncio.wait_for(service.aclose(), timeout=0.2)
    start_result = await asyncio.gather(
        start_task,
        return_exceptions=True,
    )

    assert isinstance(start_result[0], asyncio.CancelledError)
    assert not service._timeout_tasks
    provider.open_interactive_login.assert_not_awaited()
    with pytest.raises(LoginOperationError) as error:
        await service.start(platform="douyin", account_alias="dy_01")
    assert error.value.code == "login_service_closed"


@pytest.mark.asyncio
async def test_aclose_cancels_start_blocked_in_provider_availability():
    provider = FakeInteractiveLoginProvider()
    availability_started = asyncio.Event()
    never_finish = asyncio.Event()

    async def availability(_account):
        availability_started.set()
        await never_finish.wait()

    provider.check_interactive_available.side_effect = availability
    service = make_interactive_login_service(provider)
    start_task = asyncio.create_task(
        service.start(platform="douyin", account_alias="dy_01")
    )
    await asyncio.wait_for(availability_started.wait(), timeout=0.2)

    await asyncio.wait_for(service.aclose(), timeout=0.2)
    start_result = await asyncio.gather(
        start_task,
        return_exceptions=True,
    )

    assert isinstance(start_result[0], asyncio.CancelledError)
    assert not service._timeout_tasks
    provider.open_interactive_login.assert_not_awaited()


@pytest.mark.asyncio
async def test_aclose_closes_late_browser_returned_by_cancelled_open():
    leases = AccountLeaseManager()
    provider = FakeInteractiveLoginProvider()
    open_started = asyncio.Event()
    allow_late_return = asyncio.Event()

    async def cancellation_resistant_open(*, account_key, account):
        open_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await allow_late_return.wait()
        return replace(provider.opened, account_key=str(account_key))

    provider.open_interactive_login.side_effect = (
        cancellation_resistant_open
    )
    service = make_interactive_login_service(
        provider,
        leases=leases,
    )
    start_task = asyncio.create_task(
        service.start(platform="douyin", account_alias="dy_01")
    )
    await asyncio.wait_for(open_started.wait(), timeout=0.2)

    close_task = asyncio.create_task(service.aclose())
    await asyncio.sleep(0)
    allow_late_return.set()
    await asyncio.wait_for(close_task, timeout=0.2)
    start_result = await asyncio.gather(
        start_task,
        return_exceptions=True,
    )

    assert isinstance(
        start_result[0],
        (asyncio.CancelledError, LoginOperationError),
    )
    provider.close_interactive_login.assert_awaited_once()
    assert not service._timeout_tasks
    replacement = await leases.acquire(
        "douyin",
        "dy_01",
        owner="pipeline:test",
    )
    await replacement.release()


@pytest.mark.asyncio
async def test_concurrent_false_verify_shares_one_provider_operation():
    provider = FakeInteractiveLoginProvider(authenticated=False)
    verify_started = asyncio.Event()
    allow_verify = asyncio.Event()

    async def delayed_verify(session):
        verify_started.set()
        await allow_verify.wait()
        return await provider._verify(session)

    provider.verify_interactive_login.side_effect = delayed_verify
    service = make_interactive_login_service(provider)
    session = await service.start(
        platform="douyin",
        account_alias="dy_01",
    )
    first = asyncio.create_task(service.verify(session.token))
    await asyncio.wait_for(verify_started.wait(), timeout=0.2)
    second = asyncio.create_task(service.verify(session.token))
    await asyncio.sleep(0)
    allow_verify.set()

    results = await asyncio.gather(first, second)

    assert [result.status for result in results] == [
        "waiting_user",
        "waiting_user",
    ]
    provider.verify_interactive_login.assert_awaited_once()
    provider.persist_interactive_login.assert_not_awaited()
    await service.cancel(session.token)
    await service.aclose()


@pytest.mark.asyncio
async def test_concurrent_verify_exception_is_shared_once_and_cleaned():
    provider = FakeInteractiveLoginProvider()
    verify_started = asyncio.Event()
    allow_verify = asyncio.Event()

    async def failing_verify(_session):
        verify_started.set()
        await allow_verify.wait()
        raise RuntimeError("private provider detail")

    provider.verify_interactive_login.side_effect = failing_verify
    service = make_interactive_login_service(provider)
    session = await service.start(
        platform="douyin",
        account_alias="dy_01",
    )
    first = asyncio.create_task(service.verify(session.token))
    await asyncio.wait_for(verify_started.wait(), timeout=0.2)
    second = asyncio.create_task(service.verify(session.token))
    await asyncio.sleep(0)
    allow_verify.set()
    results = await asyncio.gather(
        first,
        second,
        return_exceptions=True,
    )

    assert all(
        isinstance(result, LoginOperationError)
        and result.code == "verification_failed"
        for result in results
    )
    provider.verify_interactive_login.assert_awaited_once()
    provider.close_interactive_login.assert_awaited_once()
    await service.aclose()


@pytest.mark.asyncio
async def test_cancelled_verify_waiter_does_not_cancel_shared_operation():
    provider = FakeInteractiveLoginProvider(authenticated=False)
    verify_started = asyncio.Event()
    allow_verify = asyncio.Event()

    async def delayed_verify(session):
        verify_started.set()
        await allow_verify.wait()
        return await provider._verify(session)

    provider.verify_interactive_login.side_effect = delayed_verify
    service = make_interactive_login_service(provider)
    session = await service.start(
        platform="douyin",
        account_alias="dy_01",
    )
    surviving_waiter = asyncio.create_task(
        service.verify(session.token)
    )
    await asyncio.wait_for(verify_started.wait(), timeout=0.2)
    cancelled_waiter = asyncio.create_task(
        service.verify(session.token)
    )
    await asyncio.sleep(0)
    cancelled_waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled_waiter

    allow_verify.set()
    result = await asyncio.wait_for(
        surviving_waiter,
        timeout=0.2,
    )

    assert result.status == "waiting_user"
    provider.verify_interactive_login.assert_awaited_once()
    await service.cancel(session.token)
    await service.aclose()


@pytest.mark.asyncio
async def test_unauthenticated_verify_exposes_provider_diagnostic_code():
    """验证未通过时会话必须带上原因，否则前端只能显示"不通过"。

    会话仍退回 waiting_user 供用户继续登录，但 error_code 说明卡在哪一步。
    """

    provider = FakeInteractiveLoginProvider(authenticated=False)
    service = make_interactive_login_service(provider)
    session = await service.start(
        platform="douyin",
        account_alias="dy_01",
    )

    result = await service.verify(session.token)

    assert result.status == "waiting_user"
    assert result.authenticated is False
    assert result.error_code == "not_logged_in"

    await service.cancel(session.token)
    await service.aclose()


@pytest.mark.asyncio
async def test_successful_verify_clears_stale_diagnostic_code():
    """上一次失败的诊断码不能残留到成功会话上。"""

    provider = FakeInteractiveLoginProvider(authenticated=False)
    service = make_interactive_login_service(
        provider,
        account_updater=lambda *_args: None,
    )
    session = await service.start(
        platform="douyin",
        account_alias="dy_01",
    )

    failed = await service.verify(session.token)
    assert failed.error_code == "not_logged_in"

    provider.authenticated = True
    confirmed = await service.verify(session.token)

    assert confirmed.status == "confirmed"
    assert confirmed.error_code == ""

    await service.aclose()


@pytest.mark.asyncio
async def test_cleanup_close_failure_keeps_lease_until_retry_succeeds():
    leases = AccountLeaseManager()
    provider = FakeInteractiveLoginProvider()
    provider.close_interactive_login.side_effect = [
        RuntimeError("private close detail"),
        None,
    ]
    service = make_interactive_login_service(
        provider,
        leases=leases,
    )
    session = await service.start(
        platform="douyin",
        account_alias="dy_01",
    )

    first = await service.cancel(session.token)

    assert first.status == "failed"
    assert first.error_code == "browser_cleanup_failed"
    with pytest.raises(AccountBusyError):
        await leases.acquire(
            "douyin",
            "dy_01",
            owner="pipeline:test",
        )

    second = await service.cancel(session.token)

    assert second.status == "failed"
    assert provider.close_interactive_login.await_count == 2
    replacement = await leases.acquire(
        "douyin",
        "dy_01",
        owner="pipeline:test",
    )
    await replacement.release()
    await service.aclose()


def test_login_session_repr_hides_token():
    session = LoginSession.new("douyin", "dy_01")

    assert session.token not in repr(session)


@pytest.mark.asyncio
async def test_managed_session_repr_and_timeout_task_name_hide_token_and_paths():
    provider = FakeInteractiveLoginProvider()
    service = make_interactive_login_service(provider)
    session = await service.start(
        platform="douyin",
        account_alias="dy_01",
    )
    managed = service._sessions[session.token]

    rendered = repr(managed)
    task_names = {
        task.get_name()
        for task in service._timeout_tasks
    }

    assert session.token not in rendered
    assert session.token[:8] not in rendered
    assert "private-profile" not in rendered
    assert "private-state.json" not in rendered
    assert all(session.token[:8] not in name for name in task_names)
    await service.cancel(session.token)
    await service.aclose()


@pytest.mark.asyncio
async def test_cancel_racing_persist_cancels_operation_without_double_effects():
    provider = FakeInteractiveLoginProvider()
    persist_started = asyncio.Event()
    never_finish = asyncio.Event()
    account_updater = AsyncMock()

    async def hanging_persist(_session):
        persist_started.set()
        await never_finish.wait()

    provider.persist_interactive_login.side_effect = hanging_persist
    service = make_interactive_login_service(
        provider,
        account_updater=account_updater,
    )
    session = await service.start(
        platform="douyin",
        account_alias="dy_01",
    )
    verify_task = asyncio.create_task(service.verify(session.token))
    await asyncio.wait_for(persist_started.wait(), timeout=0.2)

    cancel_result = await asyncio.wait_for(
        service.cancel(session.token),
        timeout=0.2,
    )
    verify_result = await asyncio.wait_for(verify_task, timeout=0.2)

    assert cancel_result.status == "cancelled"
    assert verify_result.status == "cancelled"
    provider.persist_interactive_login.assert_awaited_once()
    account_updater.assert_not_awaited()
    provider.close_interactive_login.assert_awaited_once()
    await service.aclose()


@pytest.mark.asyncio
async def test_timeout_racing_account_update_commit_wins_once():
    provider = FakeInteractiveLoginProvider()
    update_started = asyncio.Event()
    allow_commit = asyncio.Event()
    update_calls = 0

    async def hanging_update(*_args):
        nonlocal update_calls
        update_calls += 1
        update_started.set()
        await allow_commit.wait()

    service = make_interactive_login_service(
        provider,
        account_updater=hanging_update,
        timeout_seconds=0.02,
    )
    session = await service.start(
        platform="douyin",
        account_alias="dy_01",
    )
    verify_task = asyncio.create_task(service.verify(session.token))
    await asyncio.wait_for(update_started.wait(), timeout=0.2)

    await asyncio.sleep(0.03)
    assert not verify_task.done()
    allow_commit.set()
    verify_result = await asyncio.wait_for(verify_task, timeout=0.3)
    status = await service.status(session.token)

    assert verify_result.status == "confirmed"
    assert status.status == "confirmed"
    assert update_calls == 1
    provider.persist_interactive_login.assert_awaited_once()
    provider.close_interactive_login.assert_awaited_once()
    await service.aclose()


@pytest.mark.asyncio
async def test_aclose_and_cancel_race_share_one_cleanup():
    leases = AccountLeaseManager()
    provider = FakeInteractiveLoginProvider()
    service = make_interactive_login_service(
        provider,
        leases=leases,
    )
    session = await service.start(
        platform="douyin",
        account_alias="dy_01",
    )

    cancel_result, close_result = await asyncio.gather(
        service.cancel(session.token),
        service.aclose(),
    )

    assert cancel_result.status == "cancelled"
    assert close_result is None
    provider.close_interactive_login.assert_awaited_once()
    replacement = await leases.acquire(
        "douyin",
        "dy_01",
        owner="pipeline:test",
    )
    await replacement.release()


@pytest.mark.asyncio
async def test_verify_self_expiry_returns_snapshot_after_cleanup():
    leases = AccountLeaseManager()
    provider = FakeInteractiveLoginProvider()
    service = make_interactive_login_service(
        provider,
        leases=leases,
        timeout_seconds=60,
    )
    session = await service.start(
        platform="douyin",
        account_alias="dy_01",
    )

    async def expire_while_verifying(browser_session):
        service._sessions[session.token].session.expires_at = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        )
        return await provider._verify(browser_session)

    provider.verify_interactive_login.side_effect = expire_while_verifying

    result = await service.verify(session.token)

    assert result.status == "expired"
    provider.persist_interactive_login.assert_not_awaited()
    provider.close_interactive_login.assert_awaited_once()
    replacement = await leases.acquire(
        "douyin",
        "dy_01",
        owner="pipeline:test",
    )
    await replacement.release()
    await service.aclose()


@pytest.mark.asyncio
async def test_persist_self_expiry_returns_snapshot_after_cleanup():
    leases = AccountLeaseManager()
    provider = FakeInteractiveLoginProvider()
    account_updater = AsyncMock()
    service = make_interactive_login_service(
        provider,
        leases=leases,
        account_updater=account_updater,
        timeout_seconds=60,
    )
    session = await service.start(
        platform="douyin",
        account_alias="dy_01",
    )

    async def expire_after_persist(browser_session):
        persisted = await provider._persist(browser_session)
        service._sessions[session.token].session.expires_at = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        )
        return persisted

    provider.persist_interactive_login.side_effect = expire_after_persist

    result = await service.verify(session.token)

    assert result.status == "expired"
    account_updater.assert_not_awaited()
    provider.close_interactive_login.assert_awaited_once()
    replacement = await leases.acquire(
        "douyin",
        "dy_01",
        owner="pipeline:test",
    )
    await replacement.release()
    await service.aclose()


@pytest.mark.asyncio
async def test_cancel_waits_for_commit_winner_and_returns_confirmed():
    provider = FakeInteractiveLoginProvider()
    update_started = asyncio.Event()
    allow_commit = asyncio.Event()
    committed = []

    async def commit_update(*_args):
        committed.append("account-committed")
        update_started.set()
        await allow_commit.wait()

    service = make_interactive_login_service(
        provider,
        account_updater=commit_update,
    )
    session = await service.start(
        platform="douyin",
        account_alias="dy_01",
    )
    verify_task = asyncio.create_task(service.verify(session.token))
    await asyncio.wait_for(update_started.wait(), timeout=0.2)

    cancel_task = asyncio.create_task(service.cancel(session.token))
    await asyncio.sleep(0.02)
    assert not cancel_task.done()
    allow_commit.set()
    verify_result, cancel_result = await asyncio.gather(
        verify_task,
        cancel_task,
    )

    assert committed == ["account-committed"]
    assert verify_result.status == "confirmed"
    assert cancel_result.status == "confirmed"
    provider.persist_interactive_login.assert_awaited_once()
    provider.close_interactive_login.assert_awaited_once()
    await service.aclose()


@pytest.mark.asyncio
async def test_timeout_after_commit_started_yields_confirmed_not_expired():
    provider = FakeInteractiveLoginProvider()
    update_started = asyncio.Event()
    allow_commit = asyncio.Event()
    update_calls = 0

    async def commit_update(*_args):
        nonlocal update_calls
        update_calls += 1
        update_started.set()
        await allow_commit.wait()

    service = make_interactive_login_service(
        provider,
        account_updater=commit_update,
        timeout_seconds=0.02,
    )
    session = await service.start(
        platform="douyin",
        account_alias="dy_01",
    )
    verify_task = asyncio.create_task(service.verify(session.token))
    await asyncio.wait_for(update_started.wait(), timeout=0.2)
    await asyncio.sleep(0.03)

    assert not verify_task.done()
    allow_commit.set()
    result = await asyncio.wait_for(verify_task, timeout=0.2)

    assert update_calls == 1
    assert result.status == "confirmed"
    assert (await service.status(session.token)).status == "confirmed"
    provider.close_interactive_login.assert_awaited_once()
    await service.aclose()


@pytest.mark.asyncio
async def test_aclose_reports_incomplete_while_commit_is_pending_then_retries():
    leases = AccountLeaseManager()
    provider = FakeInteractiveLoginProvider()
    update_started = asyncio.Event()
    allow_commit = asyncio.Event()

    async def commit_update(*_args):
        update_started.set()
        await allow_commit.wait()

    service = make_interactive_login_service(
        provider,
        leases=leases,
        account_updater=commit_update,
        operation_cancel_grace_seconds=0.01,
    )
    session = await service.start(
        platform="douyin",
        account_alias="dy_01",
    )
    verify_task = asyncio.create_task(service.verify(session.token))
    await asyncio.wait_for(update_started.wait(), timeout=0.2)

    with pytest.raises(LoginCleanupIncompleteError) as error:
        await asyncio.wait_for(service.aclose(), timeout=0.2)

    assert error.value.code == "login_cleanup_incomplete"
    with pytest.raises(AccountBusyError):
        await leases.acquire(
            "douyin",
            "dy_01",
            owner="pipeline:test",
        )
    allow_commit.set()
    assert (await verify_task).status == "confirmed"

    await asyncio.wait_for(service.aclose(), timeout=0.2)

    replacement = await leases.acquire(
        "douyin",
        "dy_01",
        owner="pipeline:test",
    )
    await replacement.release()


@pytest.mark.asyncio
async def test_aclose_reports_cleanup_incomplete_after_browser_close_failure():
    provider = FakeInteractiveLoginProvider()
    provider.close_interactive_login.side_effect = [
        RuntimeError("private close detail"),
        None,
    ]
    service = make_interactive_login_service(provider)
    session = await service.start(
        platform="douyin",
        account_alias="dy_01",
    )

    with pytest.raises(LoginCleanupIncompleteError) as error:
        await service.aclose()

    assert error.value.code == "login_cleanup_incomplete"
    assert (await service.status(session.token)).error_code == (
        "browser_cleanup_failed"
    )
    with pytest.raises(LoginOperationError) as start_error:
        await service.start(platform="douyin", account_alias="other")
    assert start_error.value.code == "login_service_closed"

    await service.aclose()
    assert provider.close_interactive_login.await_count == 2


@pytest.mark.asyncio
async def test_never_returning_open_uses_reaper_and_bounded_incomplete_close():
    leases = AccountLeaseManager()
    provider = FakeInteractiveLoginProvider()
    open_started = asyncio.Event()
    allow_late_return = asyncio.Event()
    browser_closed = asyncio.Event()

    async def never_returning_open(*, account_key, account):
        open_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await allow_late_return.wait()
        return replace(provider.opened, account_key=str(account_key))

    async def record_close(_session):
        browser_closed.set()

    provider.open_interactive_login.side_effect = never_returning_open
    provider.close_interactive_login.side_effect = record_close
    service = make_interactive_login_service(
        provider,
        leases=leases,
        timeout_seconds=0.01,
        operation_cancel_grace_seconds=0.01,
    )

    with pytest.raises(LoginOperationError) as start_error:
        await asyncio.wait_for(
            service.start(platform="douyin", account_alias="dy_01"),
            timeout=0.2,
        )

    assert start_error.value.code == "browser_open_timeout"
    await asyncio.wait_for(open_started.wait(), timeout=0.2)
    with pytest.raises(AccountBusyError):
        await leases.acquire(
            "douyin",
            "dy_01",
            owner="pipeline:test",
        )
    with pytest.raises(LoginCleanupIncompleteError) as close_error:
        await asyncio.wait_for(service.aclose(), timeout=0.2)
    assert close_error.value.code == "login_cleanup_incomplete"

    allow_late_return.set()
    await asyncio.wait_for(browser_closed.wait(), timeout=0.2)
    replacement = await leases.acquire(
        "douyin",
        "dy_01",
        owner="pipeline:test",
    )
    await replacement.release()
    await asyncio.wait_for(service.aclose(), timeout=0.2)
    assert not service._reaper_tasks


@pytest.mark.asyncio
async def test_sync_account_updater_exception_fails_and_cleans_resources():
    leases = AccountLeaseManager()
    provider = FakeInteractiveLoginProvider()

    def failing_sync_updater(*_args):
        raise RuntimeError("private synchronous update detail")

    service = make_interactive_login_service(
        provider,
        leases=leases,
        account_updater=failing_sync_updater,
    )
    session = await service.start(
        platform="douyin",
        account_alias="dy_01",
    )

    with pytest.raises(LoginOperationError) as error:
        await service.verify(session.token)

    assert error.value.code == "account_update_failed"
    status = await service.status(session.token)
    assert status.status == "failed"
    assert status.error_code == "account_update_failed"
    provider.close_interactive_login.assert_awaited_once()
    replacement = await leases.acquire(
        "douyin",
        "dy_01",
        owner="pipeline:test",
    )
    await replacement.release()
    await service.aclose()


@pytest.mark.asyncio
async def test_sync_account_updater_success_confirms_once():
    provider = FakeInteractiveLoginProvider()
    update_calls = []

    def successful_sync_updater(*_args):
        update_calls.append("committed")
        return None

    service = make_interactive_login_service(
        provider,
        account_updater=successful_sync_updater,
    )
    session = await service.start(
        platform="douyin",
        account_alias="dy_01",
    )

    result = await service.verify(session.token)

    assert result.status == "confirmed"
    assert update_calls == ["committed"]
    provider.persist_interactive_login.assert_awaited_once()
    provider.close_interactive_login.assert_awaited_once()
    await service.aclose()


@pytest.mark.asyncio
async def test_cancelled_sole_verify_waiter_self_expiry_still_cleans():
    leases = AccountLeaseManager()
    provider = FakeInteractiveLoginProvider()
    verify_started = asyncio.Event()
    allow_verify = asyncio.Event()
    browser_closed = asyncio.Event()

    async def delayed_verify(browser_session):
        verify_started.set()
        await allow_verify.wait()
        return await provider._verify(browser_session)

    async def record_close(_session):
        browser_closed.set()

    provider.verify_interactive_login.side_effect = delayed_verify
    provider.close_interactive_login.side_effect = record_close
    service = make_interactive_login_service(
        provider,
        leases=leases,
        timeout_seconds=60,
    )
    session = await service.start(
        platform="douyin",
        account_alias="dy_01",
    )
    waiter = asyncio.create_task(service.verify(session.token))
    await asyncio.wait_for(verify_started.wait(), timeout=0.2)
    managed = service._sessions[session.token]
    operation = managed.operation_task

    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    managed.session.expires_at = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    )
    allow_verify.set()
    operation_result = await asyncio.wait_for(operation, timeout=0.2)
    await asyncio.wait_for(browser_closed.wait(), timeout=0.2)

    assert operation_result.status == "expired"
    assert (await service.status(session.token)).status == "expired"
    provider.persist_interactive_login.assert_not_awaited()
    provider.close_interactive_login.assert_awaited_once()
    replacement = await leases.acquire(
        "douyin",
        "dy_01",
        owner="pipeline:test",
    )
    await replacement.release()
    await service.aclose()
