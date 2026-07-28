from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import pytest

import tiktok_bot_core.services.interactive_login as interactive_login
from tiktok_bot_core.services.account_leases import (
    AccountBusyError,
    AccountLeaseManager,
)
from tiktok_bot_core.services.interactive_login import (
    ALLOWED_TRANSITIONS,
    InvalidLoginTransition,
    LoginSession,
)


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
