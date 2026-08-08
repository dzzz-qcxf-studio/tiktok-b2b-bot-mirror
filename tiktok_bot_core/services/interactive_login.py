"""交互式登录会话的进程内状态模型。"""

from __future__ import annotations

import asyncio
import ctypes
import getpass
import inspect
import json
import logging
import os
import secrets
import subprocess
import tempfile
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping

from tiktok_bot_core.platforms import PlatformType
from tiktok_bot_core.services.account_leases import (
    AccountLease,
    AccountLeaseManager,
)
from tiktok_bot_core.services.auth_service import (
    AccountLimitReachedError,
    normalize_account_alias,
)

logger = logging.getLogger(__name__)


ALLOWED_TRANSITIONS: dict[str, set[str]] = {
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

_TERMINAL_STATUSES = frozenset({"confirmed", "failed", "expired", "cancelled"})
_WINDOWS_NAME_SAM_COMPATIBLE = 2


@dataclass(frozen=True, slots=True)
class InteractiveBrowserSession:
    account_key: str
    context: Any = field(repr=False)
    page: Any = field(repr=False)
    profile_dir: Path = field(repr=False)
    storage_state_path: Path = field(repr=False)


@dataclass(frozen=True, slots=True)
class AuthVerification:
    authenticated: bool
    has_authenticated_cookie: bool
    protected_page_ok: bool
    local_storage_login_detected: bool = False
    identity_probe_ok: bool = False
    diagnostic_code: str = ""
    nickname: str = ""
    avatar_url: str = ""
    follower_count: int | None = None


@dataclass(slots=True)
class PersistedAuthState:
    storage_state_path: Path = field(repr=False)
    cookie_count: int
    origin_count: int
    cookies: list[dict[str, Any]] = field(repr=False)


def atomic_write_private_json(
    path: Path,
    payload: Mapping[str, Any],
) -> None:
    destination = Path(path)
    secure_private_directory(destination.parent)

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(payload, temporary, ensure_ascii=False)
            temporary.flush()
            os.fsync(temporary.fileno())
        secure_private_file(temporary_path)
        os.replace(temporary_path, destination)
        temporary_path = None
        secure_private_file(destination)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _windows_current_user() -> str:
    """Return the Windows identity attached to the current process token."""

    try:
        get_user_name = ctypes.windll.secur32.GetUserNameExW
        size = ctypes.c_ulong(0)
        get_user_name(
            _WINDOWS_NAME_SAM_COMPATIBLE,
            None,
            ctypes.byref(size),
        )
        if size.value:
            buffer = ctypes.create_unicode_buffer(size.value)
            if get_user_name(
                _WINDOWS_NAME_SAM_COMPATIBLE,
                buffer,
                ctypes.byref(size),
            ):
                current_user = buffer.value.strip()
                if current_user:
                    return current_user
    except (AttributeError, OSError, ValueError):
        pass
    return getpass.getuser()


def secure_private_directory(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        try:
            current_user = _windows_current_user()
            subprocess.run(
                [
                    "icacls",
                    str(directory),
                    "/inheritance:r",
                    "/grant:r",
                    f"{current_user}:(OI)(CI)F",
                ],
                check=True,
                capture_output=True,
                text=True,
                shell=False,
            )
        except Exception as exc:
            logger.warning(
                "Windows ACL 收紧失败，私密目录处于安全降级状态: %s (%s)",
                directory,
                exc,
            )
        return

    try:
        directory.chmod(0o700)
    except OSError as exc:
        logger.warning(
            "私密目录权限收紧失败，目录处于安全降级状态: %s (%s)",
            directory,
            exc,
        )


def secure_private_file(path: Path) -> None:
    try:
        path.chmod(0o600)
    except OSError as exc:
        logger.warning(
            "私密文件权限收紧失败，文件处于安全降级状态: %s (%s)",
            path,
            exc,
        )


class InvalidLoginTransition(RuntimeError):
    """登录会话尝试了状态机未允许的转移。"""

    def __init__(self, from_status: str, to_status: str) -> None:
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(
            f"Invalid login session transition: {from_status} -> {to_status}"
        )


class SessionExpiredError(RuntimeError):
    """登录会话已超过允许的交互时间。"""

    def __init__(self) -> None:
        super().__init__("Login session has expired")


@dataclass(slots=True)
class LoginSession:
    token: str = field(repr=False)
    platform: str
    account_alias: str
    account_id: int | None
    status: str
    browser_provider: str
    browser_profile_id: str
    started_at: datetime
    expires_at: datetime
    error_code: str
    error_message: str
    authenticated: bool
    persisted: bool

    @classmethod
    def new(
        cls,
        platform: str | PlatformType,
        account_alias: str,
        account_id: int | None = None,
        *,
        browser_provider: str = "",
        browser_profile_id: str = "",
        error_code: str = "",
        error_message: str = "",
        timeout_seconds: float = 300,
    ) -> "LoginSession":
        normalized_platform = PlatformType.parse(platform).value
        normalized_alias = _normalize_alias(account_alias)
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be greater than zero")
        started_at = datetime.now(timezone.utc)
        return cls(
            token=secrets.token_urlsafe(32),
            platform=normalized_platform,
            account_alias=normalized_alias,
            account_id=account_id,
            status="launching",
            browser_provider=browser_provider,
            browser_profile_id=browser_profile_id,
            started_at=started_at,
            expires_at=started_at + timedelta(seconds=timeout_seconds),
            error_code=error_code,
            error_message=error_message,
            authenticated=False,
            persisted=False,
        )

    def transition(
        self,
        next_status: str,
        *,
        enforce_expiry: bool = True,
    ) -> None:
        if (
            enforce_expiry
            and self.status not in _TERMINAL_STATUSES
            and datetime.now(timezone.utc) >= self.expires_at
        ):
            self.status = "expired"
            raise SessionExpiredError()
        if next_status not in ALLOWED_TRANSITIONS.get(self.status, set()):
            raise InvalidLoginTransition(self.status, next_status)
        if next_status == "confirmed" and not (
            self.authenticated and self.persisted
        ):
            raise InvalidLoginTransition(self.status, next_status)
        self.status = next_status
        if next_status == "persisted":
            self.persisted = True


class InteractiveLoginError(RuntimeError):
    """Base error with a stable public code and no credential values."""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        self.message = message or code
        super().__init__(f"{code}: {self.message}")


class LoginUnavailableError(InteractiveLoginError):
    """The selected platform has no usable interactive provider."""


class LoginOperationError(InteractiveLoginError):
    """An interactive login operation failed after it was accepted."""


class LoginCleanupIncompleteError(InteractiveLoginError):
    def __init__(self) -> None:
        super().__init__("login_cleanup_incomplete")


class LoginSessionNotFoundError(InteractiveLoginError):
    def __init__(self) -> None:
        super().__init__("login_session_not_found")


@dataclass(slots=True)
class _ManagedLoginSession:
    session: LoginSession = field(repr=False)
    provider: Any = field(repr=False)
    account: Any = field(repr=False)
    lease: AccountLease = field(repr=False)
    browser_session: InteractiveBrowserSession | None = field(
        default=None,
        repr=False,
    )
    lock: asyncio.Lock = field(
        default_factory=asyncio.Lock,
        repr=False,
    )
    timeout_task: asyncio.Task[None] | None = field(
        default=None,
        repr=False,
    )
    operation_task: asyncio.Task[LoginSession] | None = field(
        default=None,
        repr=False,
    )
    cleanup_task: asyncio.Task[bool] | None = field(
        default=None,
        repr=False,
    )
    commit_task: asyncio.Task[Any] | None = field(
        default=None,
        repr=False,
    )
    commit_won: bool = field(default=False, repr=False)
    open_reaper_task: asyncio.Task[None] | None = field(
        default=None,
        repr=False,
    )
    browser_cleanup_done: bool = field(default=False, repr=False)
    lease_released: bool = field(default=False, repr=False)


class InteractiveLoginService:
    """Own interactive login sessions, resources, leases and timeouts."""

    def __init__(
        self,
        *,
        providers: Any,
        leases: AccountLeaseManager,
        account_resolver: Callable[[str, str], Any] | None = None,
        account_updater: Callable[
            [Any, PersistedAuthState, AuthVerification],
            Any,
        ]
        | None = None,
        timeout_seconds: float = 300,
        operation_cancel_grace_seconds: float = 0.05,
    ) -> None:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be greater than zero")
        if (
            isinstance(operation_cancel_grace_seconds, bool)
            or not isinstance(
                operation_cancel_grace_seconds,
                (int, float),
            )
            or operation_cancel_grace_seconds <= 0
        ):
            raise ValueError(
                "operation_cancel_grace_seconds must be greater than zero"
            )
        self._providers = providers
        self._leases = leases
        self._account_resolver = account_resolver
        self._account_updater = account_updater
        self._timeout_seconds = float(timeout_seconds)
        self._operation_cancel_grace_seconds = float(
            operation_cancel_grace_seconds
        )
        self._sessions: dict[str, _ManagedLoginSession] = {}
        self._timeout_tasks: set[asyncio.Task[None]] = set()
        self._start_tasks: set[asyncio.Task[Any]] = set()
        self._reaper_tasks: set[asyncio.Task[None]] = set()
        self._maintenance_tasks: set[asyncio.Task[None]] = set()
        self._lifecycle_lock = asyncio.Lock()
        self._close_task: asyncio.Task[None] | None = None
        self._closing = False
        self._closed = False

    async def start(
        self,
        *,
        platform: str | PlatformType,
        account_alias: str,
    ) -> LoginSession:
        current = asyncio.current_task()
        if current is None:
            raise LoginOperationError("login_start_task_missing")
        async with self._lifecycle_lock:
            if self._closing or self._closed:
                raise LoginOperationError("login_service_closed")
            self._start_tasks.add(current)
        try:
            return await self._start_registered(
                platform=platform,
                account_alias=account_alias,
            )
        finally:
            async with self._lifecycle_lock:
                self._start_tasks.discard(current)

    async def _start_registered(
        self,
        *,
        platform: str | PlatformType,
        account_alias: str,
    ) -> LoginSession:
        managed: _ManagedLoginSession | None = None

        normalized_platform = PlatformType.parse(platform).value
        normalized_alias = _normalize_alias(account_alias)
        account = await self._resolve_account(
            normalized_platform,
            normalized_alias,
        )
        account_id = _account_value(account, "id")
        browser_provider = str(
            _account_value(account, "browser_provider") or ""
        )
        browser_profile_id = str(
            _account_value(account, "browser_profile_id") or ""
        )
        session = LoginSession.new(
            normalized_platform,
            normalized_alias,
            account_id=_coerce_optional_int(account_id),
            browser_provider=browser_provider,
            browser_profile_id=browser_profile_id,
            timeout_seconds=self._timeout_seconds,
        )

        try:
            if normalized_platform == "tiktok":
                provider = (
                    self._providers.get_tiktok_fingerprint_interactive()
                )
            else:
                provider = self._providers.get_interactive(
                    normalized_platform
                )
        except (KeyError, ValueError) as exc:
            code = (
                "fingerprint_provider_unavailable"
                if normalized_platform == "tiktok"
                else "interactive_provider_unavailable"
            )
            raise LoginUnavailableError(code) from exc

        unavailable_code = (
            "fingerprint_provider_unavailable"
            if normalized_platform == "tiktok"
            else "interactive_provider_unavailable"
        )
        if normalized_platform == "tiktok":
            binding_validator = getattr(
                provider,
                "validates_account_binding",
                None,
            )
            try:
                binding_is_valid = (
                    callable(binding_validator)
                    and bool(binding_validator(account))
                )
            except BaseException as exc:
                if isinstance(exc, asyncio.CancelledError):
                    raise
                raise LoginUnavailableError(unavailable_code) from exc
            if not binding_is_valid:
                raise LoginUnavailableError(unavailable_code)

        availability = await self._check_available(
            provider,
            account,
            unavailable_code=unavailable_code,
        )
        if not bool(getattr(availability, "available", False)):
            code = _normalize_provider_unavailable_code(
                getattr(availability, "code", ""),
                fallback=unavailable_code,
            )
            raise LoginUnavailableError(code)

        configured_browser_key = _account_value(
            account,
            "browser_account_key",
        )
        account_key = (
            str(configured_browser_key).strip()
            if configured_browser_key is not None
            and str(configured_browser_key).strip()
            else normalized_alias
        )
        lease = await self._leases.acquire(
            normalized_platform,
            account_key,
            owner=f"login:{session.token}",
        )
        managed = _ManagedLoginSession(
            session=session,
            provider=provider,
            account=account,
            lease=lease,
        )
        self._sessions[session.token] = managed

        open_task: asyncio.Task[Any] | None = None
        deferred_open_cleanup = False
        try:
            remaining_seconds = max(
                0.0,
                (
                    session.expires_at
                    - datetime.now(timezone.utc)
                ).total_seconds(),
            )
            open_task = asyncio.create_task(
                provider.open_interactive_login(
                    account_key=str(account_key),
                    account=account,
                ),
                name="interactive-login-browser-open",
            )
            open_task.add_done_callback(_consume_task_result)
            try:
                opened = await asyncio.wait_for(
                    asyncio.shield(open_task),
                    timeout=remaining_seconds,
                )
            except BaseException:
                late_opened, deferred_open_cleanup = (
                    await self._cancel_open_or_reap(
                        managed,
                        open_task,
                    )
                )
                if late_opened is not None:
                    async with managed.lock:
                        managed.browser_session = late_opened
                raise
            if not isinstance(
                opened,
                InteractiveBrowserSession,
            ):
                raise TypeError(
                    "interactive provider returned an invalid session"
                )
            async with managed.lock:
                managed.browser_session = opened
            if await self._is_closing():
                async with managed.lock:
                    session.status = "cancelled"
                    session.error_code = "login_service_closed"
                    session.error_message = "login service is closing"
                await self._ensure_cleanup(managed)
                raise LoginOperationError("login_service_closed")
            async with managed.lock:
                session.transition("waiting_user")
        except asyncio.TimeoutError as exc:
            async with managed.lock:
                self._mark_terminal_locked(
                    managed,
                    status="expired",
                    code="browser_open_timeout",
                    message="interactive browser open timed out",
                )
            if not deferred_open_cleanup:
                await self._ensure_cleanup(managed)
            raise LoginOperationError("browser_open_timeout") from exc
        except asyncio.CancelledError:
            async with managed.lock:
                self._mark_terminal_locked(
                    managed,
                    status="cancelled",
                    code="login_start_cancelled",
                    message="login start was cancelled",
                )
            if not deferred_open_cleanup:
                await self._ensure_cleanup(managed)
            raise
        except LoginOperationError:
            raise
        except BaseException as exc:
            async with managed.lock:
                self._mark_terminal_locked(
                    managed,
                    status="failed",
                    code="browser_open_failed",
                    message="interactive browser could not be opened",
                )
            if not deferred_open_cleanup:
                await self._ensure_cleanup(managed)
            raise LoginOperationError("browser_open_failed") from exc

        async with self._lifecycle_lock:
            should_abort = self._closing or self._closed
            if not should_abort:
                timeout_task = asyncio.create_task(
                    self._expire_when_due(managed),
                    name="interactive-login-timeout",
                )
                managed.timeout_task = timeout_task
                self._timeout_tasks.add(timeout_task)
                timeout_task.add_done_callback(
                    self._timeout_tasks.discard
                )
        if should_abort:
            async with managed.lock:
                self._mark_terminal_locked(
                    managed,
                    status="cancelled",
                    code="login_service_closed",
                    message="login service is closing",
                )
            await self._ensure_cleanup(managed)
            raise LoginOperationError("login_service_closed")
        return _session_snapshot(session)

    async def status(self, token: str) -> LoginSession:
        managed = self._get_managed(token)
        newly_expired = False
        async with managed.lock:
            if (
                managed.commit_task is None
                and self._is_due_locked(managed)
            ):
                self._mark_terminal_locked(
                    managed,
                    status="expired",
                    code="login_session_expired",
                    message="login session expired",
                )
                newly_expired = True
            snapshot = _session_snapshot(managed.session)
            cleanup_task = managed.cleanup_task
        if newly_expired:
            await self._ensure_cleanup(managed)
            async with managed.lock:
                snapshot = _session_snapshot(managed.session)
        elif cleanup_task is not None and not cleanup_task.done():
            await asyncio.shield(cleanup_task)
            async with managed.lock:
                snapshot = _session_snapshot(managed.session)
        return snapshot

    async def verify(self, token: str) -> LoginSession:
        managed = self._get_managed(token)
        newly_expired = False
        async with managed.lock:
            session = managed.session
            if self._is_due_locked(managed):
                self._mark_terminal_locked(
                    managed,
                    status="expired",
                    code="login_session_expired",
                    message="login session expired",
                )
                newly_expired = True
            if session.status in _TERMINAL_STATUSES:
                operation = None
                snapshot = _session_snapshot(session)
            elif (
                managed.operation_task is not None
                and not managed.operation_task.done()
            ):
                operation = managed.operation_task
                snapshot = None
            elif session.status == "waiting_user":
                session.transition("verifying")
                operation = asyncio.create_task(
                    self._run_verify(managed),
                    name="interactive-login-verify",
                )
                operation.add_done_callback(
                    lambda task, target=managed: (
                        self._on_operation_done(target, task)
                    )
                )
                managed.operation_task = operation
                snapshot = None
            else:
                raise LoginOperationError(
                    "login_session_not_verifiable"
                )
        if newly_expired:
            await self._ensure_cleanup(managed)
            async with managed.lock:
                return _session_snapshot(managed.session)
        if operation is None:
            return snapshot
        result = await asyncio.shield(operation)
        async with managed.lock:
            cleanup_task = managed.cleanup_task
            needs_cleanup = (
                result.status in _TERMINAL_STATUSES
                and not managed.lease_released
            )
        if needs_cleanup:
            await self._ensure_cleanup(managed)
            async with managed.lock:
                result = _session_snapshot(managed.session)
        elif cleanup_task is not None and not cleanup_task.done():
            await asyncio.shield(cleanup_task)
            async with managed.lock:
                result = _session_snapshot(managed.session)
        return result

    async def cancel(self, token: str) -> LoginSession:
        managed = self._get_managed(token)
        async with managed.lock:
            session = managed.session
            commit_operation = (
                managed.operation_task
                if (
                    managed.commit_task is not None
                    and session.status not in _TERMINAL_STATUSES
                )
                else None
            )
            if (
                commit_operation is None
                and session.status not in _TERMINAL_STATUSES
            ):
                self._mark_terminal_locked(
                    managed,
                    status="cancelled",
                    code="login_cancelled",
                    message="login session cancelled",
                )
        if commit_operation is not None:
            try:
                await asyncio.shield(commit_operation)
            except LoginOperationError:
                pass
        await self._ensure_cleanup(managed)
        async with managed.lock:
            return _session_snapshot(session)

    async def aclose(self) -> None:
        async with self._lifecycle_lock:
            self._closing = True
            if self._closed and self._all_resources_cleaned():
                return
            if self._close_task is None or self._close_task.done():
                self._close_task = asyncio.create_task(
                    self._run_close(),
                    name="interactive-login-service-close",
                )
                self._close_task.add_done_callback(
                    _consume_task_result
                )
            close_task = self._close_task
        await asyncio.shield(close_task)

    async def _run_close(self) -> None:
        incomplete = False
        cleanup_failure: BaseException | None = None
        try:
            async with self._lifecycle_lock:
                start_tasks = [
                    task
                    for task in self._start_tasks
                    if task is not asyncio.current_task()
                ]
            for task in start_tasks:
                task.cancel()
            if start_tasks:
                _, pending_starts = await asyncio.wait(
                    start_tasks,
                    timeout=self._operation_cancel_grace_seconds,
                )
                incomplete = incomplete or bool(pending_starts)

            managed_sessions = list(self._sessions.values())
            commit_operations: list[asyncio.Task[Any]] = []
            for managed in managed_sessions:
                async with managed.lock:
                    operation = managed.operation_task
                    commit_pending = (
                        managed.commit_task is not None
                        and operation is not None
                        and not operation.done()
                    )
                    if commit_pending:
                        commit_operations.append(operation)
                        continue
                    if (
                        managed.session.status
                        not in _TERMINAL_STATUSES
                    ):
                        self._mark_terminal_locked(
                            managed,
                            status="cancelled",
                            code="login_service_closed",
                            message="login service is closing",
                        )
            if commit_operations:
                _, pending_commits = await asyncio.wait(
                    commit_operations,
                    timeout=self._operation_cancel_grace_seconds,
                )
                incomplete = incomplete or bool(pending_commits)

            reapers = [
                task
                for task in self._reaper_tasks
                if not task.done()
            ]
            if reapers:
                _, pending_reapers = await asyncio.wait(
                    reapers,
                    timeout=self._operation_cancel_grace_seconds,
                )
                incomplete = incomplete or bool(pending_reapers)

            cleanup_waiters: list[asyncio.Task[bool]] = []
            for managed in managed_sessions:
                async with managed.lock:
                    commit_pending = (
                        managed.commit_task is not None
                        and managed.operation_task is not None
                        and not managed.operation_task.done()
                    )
                    reaper_pending = (
                        managed.open_reaper_task is not None
                        and not managed.open_reaper_task.done()
                    )
                if commit_pending or reaper_pending:
                    incomplete = True
                    continue
                waiter = asyncio.create_task(
                    self._ensure_cleanup(managed),
                    name="interactive-login-close-waiter",
                )
                waiter.add_done_callback(_consume_task_result)
                cleanup_waiters.append(waiter)
            if cleanup_waiters:
                done_cleanup, pending_cleanup = await asyncio.wait(
                    cleanup_waiters,
                    timeout=self._operation_cancel_grace_seconds,
                )
                incomplete = incomplete or bool(pending_cleanup)
                for task in done_cleanup:
                    try:
                        incomplete = incomplete or not task.result()
                    except BaseException:
                        incomplete = True
                for task in pending_cleanup:
                    task.cancel()
                if pending_cleanup:
                    await asyncio.gather(
                        *pending_cleanup,
                        return_exceptions=True,
                    )

            maintenance_tasks = [
                task
                for task in self._maintenance_tasks
                if not task.done()
            ]
            if maintenance_tasks:
                _, pending_maintenance = await asyncio.wait(
                    maintenance_tasks,
                    timeout=self._operation_cancel_grace_seconds,
                )
                incomplete = incomplete or bool(pending_maintenance)

            remaining = list(self._timeout_tasks)
            for task in remaining:
                task.cancel()
            if remaining:
                await asyncio.gather(
                    *remaining,
                    return_exceptions=True,
                )
            self._timeout_tasks.clear()
            incomplete = (
                incomplete
                or any(
                    not task.done()
                    for task in self._reaper_tasks
                )
                or not self._all_resources_cleaned()
            )
        except BaseException as exc:
            incomplete = True
            cleanup_failure = exc
        finally:
            async with self._lifecycle_lock:
                self._closed = not incomplete
        if incomplete:
            raise LoginCleanupIncompleteError() from cleanup_failure

    async def _run_verify(
        self,
        managed: _ManagedLoginSession,
    ) -> LoginSession:
        current = asyncio.current_task()
        try:
            try:
                verification = (
                    await managed.provider.verify_interactive_login(
                        managed.browser_session
                    )
                )
                if not isinstance(verification, AuthVerification):
                    raise TypeError(
                        "interactive provider returned invalid verification"
                    )
            except asyncio.CancelledError:
                return await self._cancelled_operation_snapshot(managed)
            except BaseException as exc:
                await self._fail_and_cleanup(
                    managed,
                    code="verification_failed",
                    message="login verification failed",
                )
                raise LoginOperationError(
                    "verification_failed"
                ) from exc

            async with managed.lock:
                if managed.session.status in _TERMINAL_STATUSES:
                    return _session_snapshot(managed.session)
                if self._is_due_locked(managed):
                    self._mark_terminal_locked(
                        managed,
                        status="expired",
                        code="login_session_expired",
                        message="login session expired",
                    )
                    expired = True
                else:
                    expired = False
                if not expired and not verification.authenticated:
                    managed.session.authenticated = False
                    # 会话退回 waiting_user 让用户继续登录，但必须带上失败原因，
                    # 否则前端只能显示"不通过"而无法定位卡在哪一步。
                    managed.session.error_code = str(
                        verification.diagnostic_code or ""
                    )
                    managed.session.transition("waiting_user")
                    return _session_snapshot(managed.session)
                if not expired:
                    managed.session.authenticated = True
                    # 成功后清掉上一次尝试残留的诊断码。
                    managed.session.error_code = ""
            if expired:
                async with managed.lock:
                    return _session_snapshot(managed.session)

            if self._account_updater is None:
                await self._fail_and_cleanup(
                    managed,
                    code="account_update_failed",
                    message="account updater is not configured",
                )
                raise LoginOperationError("account_update_failed")

            try:
                persisted = (
                    await managed.provider.persist_interactive_login(
                        managed.browser_session
                    )
                )
                if not isinstance(persisted, PersistedAuthState):
                    raise TypeError(
                        "interactive provider returned invalid auth state"
                    )
            except asyncio.CancelledError:
                return await self._cancelled_operation_snapshot(managed)
            except BaseException as exc:
                await self._fail_and_cleanup(
                    managed,
                    code="persistence_failed",
                    message="login state persistence failed",
                )
                raise LoginOperationError("persistence_failed") from exc

            async with managed.lock:
                if managed.session.status in _TERMINAL_STATUSES:
                    return _session_snapshot(managed.session)
                if self._is_due_locked(managed):
                    self._mark_terminal_locked(
                        managed,
                        status="expired",
                        code="login_session_expired",
                        message="login session expired",
                    )
                    expired = True
                else:
                    expired = False
            if expired:
                async with managed.lock:
                    return _session_snapshot(managed.session)

            async with managed.lock:
                if managed.session.status in _TERMINAL_STATUSES:
                    return _session_snapshot(managed.session)
                commit_task = asyncio.create_task(
                    _invoke_callback_maybe_async(
                        self._account_updater,
                        managed.account,
                        persisted,
                        verification,
                    ),
                    name="interactive-login-account-commit",
                )
                commit_task.add_done_callback(_consume_task_result)
                managed.commit_task = commit_task
            try:
                await _await_task_uninterruptibly(commit_task)
            except asyncio.CancelledError as exc:
                await self._fail_and_cleanup(
                    managed,
                    code="account_update_failed",
                    message="account update task was cancelled",
                )
                raise LoginOperationError(
                    "account_update_failed"
                ) from exc
            except AccountLimitReachedError as exc:
                await self._fail_and_cleanup(
                    managed,
                    code="account_limit_reached",
                    message="account capacity reached",
                )
                raise LoginOperationError(
                    "account_limit_reached"
                ) from exc
            except BaseException as exc:
                await self._fail_and_cleanup(
                    managed,
                    code="account_update_failed",
                    message="account authentication update failed",
                )
                raise LoginOperationError(
                    "account_update_failed"
                ) from exc

            async with managed.lock:
                managed.commit_won = True
                managed.session.transition(
                    "persisted",
                    enforce_expiry=False,
                )
                managed.session.transition(
                    "confirmed",
                    enforce_expiry=False,
                )
            await self._ensure_cleanup(managed)
            async with managed.lock:
                return _session_snapshot(managed.session)
        finally:
            async with managed.lock:
                if managed.operation_task is current:
                    managed.operation_task = None

    async def _cancelled_operation_snapshot(
        self,
        managed: _ManagedLoginSession,
    ) -> LoginSession:
        needs_cleanup = False
        async with managed.lock:
            if managed.session.status not in _TERMINAL_STATUSES:
                self._mark_terminal_locked(
                    managed,
                    status="failed",
                    code="login_operation_cancelled",
                    message="login operation was cancelled",
                )
                needs_cleanup = True
            snapshot = _session_snapshot(managed.session)
        if needs_cleanup:
            await self._ensure_cleanup(managed)
            async with managed.lock:
                snapshot = _session_snapshot(managed.session)
        return snapshot

    def _on_operation_done(
        self,
        managed: _ManagedLoginSession,
        operation: asyncio.Task[LoginSession],
    ) -> None:
        _consume_task_result(operation)
        maintenance = asyncio.create_task(
            self._cleanup_terminal_after_operation(managed),
            name="interactive-login-terminal-cleanup",
        )
        self._maintenance_tasks.add(maintenance)
        maintenance.add_done_callback(_consume_task_result)
        maintenance.add_done_callback(self._maintenance_tasks.discard)

    async def _cleanup_terminal_after_operation(
        self,
        managed: _ManagedLoginSession,
    ) -> None:
        async with managed.lock:
            needs_cleanup = (
                managed.session.status in _TERMINAL_STATUSES
                and not managed.lease_released
            )
        if needs_cleanup:
            await self._ensure_cleanup(managed)

    async def _cancel_open_or_reap(
        self,
        managed: _ManagedLoginSession,
        open_task: asyncio.Task[Any],
    ) -> tuple[InteractiveBrowserSession | None, bool]:
        open_task.cancel()
        done, _ = await asyncio.wait(
            {open_task},
            timeout=self._operation_cancel_grace_seconds,
        )
        if done:
            try:
                result = open_task.result()
            except BaseException:
                return None, False
            if isinstance(result, InteractiveBrowserSession):
                return result, False
            return None, False

        reaper = asyncio.create_task(
            self._reap_open_result(managed, open_task),
            name="interactive-login-open-reaper",
        )
        reaper.add_done_callback(_consume_task_result)
        reaper.add_done_callback(self._reaper_tasks.discard)
        managed.open_reaper_task = reaper
        self._reaper_tasks.add(reaper)
        return None, True

    async def _reap_open_result(
        self,
        managed: _ManagedLoginSession,
        open_task: asyncio.Task[Any],
    ) -> None:
        try:
            result = await open_task
        except BaseException:
            result = None
        if isinstance(result, InteractiveBrowserSession):
            async with managed.lock:
                if managed.browser_session is None:
                    managed.browser_session = result
        await self._ensure_cleanup(managed)

    async def _resolve_account(
        self,
        platform: str,
        account_alias: str,
    ) -> Any:
        if self._account_resolver is None:
            return SimpleNamespace(
                id=None,
                platform=platform,
                username=account_alias,
                browser_provider="",
                browser_profile_id="",
            )
        try:
            account = await _invoke_callback_maybe_async(
                self._account_resolver,
                platform,
                account_alias,
            )
        except asyncio.CancelledError:
            raise
        except LoginOperationError:
            raise
        except BaseException as exc:
            raise LoginOperationError(
                "account_resolution_failed"
            ) from exc
        if account is None:
            raise LoginOperationError("account_not_found")
        account_platform = PlatformType.parse(
            _account_value(account, "platform")
        ).value
        account_username = _normalize_alias(
            str(_account_value(account, "username"))
        )
        if account_platform != platform or account_username != account_alias:
            raise LoginOperationError("account_identity_mismatch")
        return account

    async def _check_available(
        self,
        provider: Any,
        account: Any,
        *,
        unavailable_code: str,
    ) -> Any:
        checker = getattr(
            provider,
            "check_interactive_available",
            None,
        )
        if checker is None or not callable(checker):
            raise LoginUnavailableError(unavailable_code)
        try:
            return await _maybe_await(checker(account))
        except LoginUnavailableError as exc:
            raise LoginUnavailableError(
                _normalize_provider_unavailable_code(
                    exc.code,
                    fallback=unavailable_code,
                )
            ) from exc
        except BaseException as exc:
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise LoginUnavailableError(unavailable_code) from exc

    def _get_managed(self, token: str) -> _ManagedLoginSession:
        if not isinstance(token, str) or not token:
            raise LoginSessionNotFoundError()
        try:
            return self._sessions[token]
        except KeyError as exc:
            raise LoginSessionNotFoundError() from exc

    async def _expire_when_due(
        self,
        managed: _ManagedLoginSession,
    ) -> None:
        try:
            delay = (
                managed.session.expires_at
                - datetime.now(timezone.utc)
            ).total_seconds()
            if delay > 0:
                await asyncio.sleep(delay)
            async with managed.lock:
                if (
                    managed.session.status
                    not in _TERMINAL_STATUSES
                ):
                    commit_operation = (
                        managed.operation_task
                        if managed.commit_task is not None
                        else None
                    )
                    if commit_operation is None:
                        self._mark_terminal_locked(
                            managed,
                            status="expired",
                            code="login_session_expired",
                            message="login session expired",
                        )
                        should_cleanup = True
                    else:
                        should_cleanup = False
                else:
                    commit_operation = None
                    should_cleanup = not managed.lease_released
            if commit_operation is not None:
                try:
                    await asyncio.shield(commit_operation)
                except LoginOperationError:
                    pass
            if should_cleanup:
                await self._ensure_cleanup(managed)
        except asyncio.CancelledError:
            return

    def _is_due_locked(
        self,
        managed: _ManagedLoginSession,
    ) -> bool:
        return (
            managed.session.status not in _TERMINAL_STATUSES
            and datetime.now(timezone.utc)
            >= managed.session.expires_at
        )

    def _mark_terminal_locked(
        self,
        managed: _ManagedLoginSession,
        *,
        status: str,
        code: str,
        message: str,
    ) -> None:
        if managed.session.status not in _TERMINAL_STATUSES:
            managed.session.status = status
        managed.session.error_code = code
        managed.session.error_message = message

    async def _fail_and_cleanup(
        self,
        managed: _ManagedLoginSession,
        *,
        code: str,
        message: str,
    ) -> None:
        async with managed.lock:
            self._mark_terminal_locked(
                managed,
                status="failed",
                code=code,
                message=message,
            )
        await self._ensure_cleanup(managed)

    async def _ensure_cleanup(
        self,
        managed: _ManagedLoginSession,
    ) -> bool:
        async with managed.lock:
            if managed.lease_released:
                return True
            cleanup_task = managed.cleanup_task
            if cleanup_task is None or cleanup_task.done():
                if cleanup_task is not None:
                    try:
                        previous_success = cleanup_task.result()
                    except BaseException:
                        previous_success = False
                    if previous_success:
                        return True
                cleanup_task = asyncio.create_task(
                    self._cleanup_resources(managed),
                    name="interactive-login-resource-cleanup",
                )
                cleanup_task.add_done_callback(_consume_task_result)
                managed.cleanup_task = cleanup_task
        try:
            return await asyncio.shield(cleanup_task)
        except asyncio.CancelledError:
            raise

    async def _cleanup_resources(
        self,
        managed: _ManagedLoginSession,
    ) -> bool:
        async with managed.lock:
            operation = managed.operation_task
            timeout_task = managed.timeout_task
            should_cancel_operation = (
                operation is not None
                and not operation.done()
                and managed.commit_task is None
                and managed.session.status
                in {"cancelled", "expired"}
            )
        if should_cancel_operation:
            operation.cancel()
            await asyncio.gather(
                operation,
                return_exceptions=True,
            )
        if (
            timeout_task is not None
            and timeout_task is not asyncio.current_task()
            and not timeout_task.done()
        ):
            timeout_task.cancel()
            await asyncio.gather(
                timeout_task,
                return_exceptions=True,
            )

        async with managed.lock:
            browser_session = managed.browser_session
            browser_done = managed.browser_cleanup_done
        if not browser_done:
            if browser_session is not None:
                try:
                    await managed.provider.close_interactive_login(
                        browser_session
                    )
                except BaseException:
                    async with managed.lock:
                        managed.session.status = "failed"
                        managed.session.error_code = (
                            "browser_cleanup_failed"
                        )
                        managed.session.error_message = (
                            "interactive browser cleanup failed"
                        )
                    logger.warning(
                        "交互登录浏览器关闭失败，保留账号租约等待重试"
                    )
                    return False
            async with managed.lock:
                managed.browser_cleanup_done = True
                managed.browser_session = None

        async with managed.lock:
            lease_released = managed.lease_released
        if not lease_released:
            try:
                await managed.lease.release()
            except BaseException:
                async with managed.lock:
                    managed.session.status = "failed"
                    managed.session.error_code = "lease_cleanup_failed"
                    managed.session.error_message = (
                        "account lease cleanup failed"
                    )
                logger.warning("交互登录账号租约释放失败，等待重试")
                return False
            async with managed.lock:
                managed.lease_released = True
        return True

    async def _is_closing(self) -> bool:
        async with self._lifecycle_lock:
            return self._closing or self._closed

    def _all_resources_cleaned(self) -> bool:
        return (
            not any(
                not task.done()
                for task in self._start_tasks
            )
            and not any(
                not task.done()
                for task in self._reaper_tasks
            )
            and not any(
                not task.done()
                for task in self._maintenance_tasks
            )
            and all(
                managed.lease_released
                and not (
                    managed.commit_task is not None
                    and not managed.commit_task.done()
                )
                for managed in self._sessions.values()
            )
        )


def _session_snapshot(session: LoginSession) -> LoginSession:
    return replace(session)


def _consume_task_result(task: asyncio.Task[Any]) -> None:
    try:
        task.exception()
    except asyncio.CancelledError:
        return
    except BaseException:
        return


async def _await_task_uninterruptibly(
    task: asyncio.Task[Any],
) -> Any:
    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            continue


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def _invoke_callback_maybe_async(
    callback: Callable[..., Any],
    *args: Any,
) -> Any:
    return await _maybe_await(callback(*args))


def _account_value(account: Any, name: str) -> Any:
    if isinstance(account, Mapping):
        return account.get(name)
    return getattr(account, name, None)


def _coerce_optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("account id must not be boolean")
    return int(value)


def _normalize_provider_unavailable_code(
    value: Any,
    *,
    fallback: str,
) -> str:
    allowed = {
        "fingerprint_provider_unavailable",
        "interactive_provider_unavailable",
    }
    normalized_fallback = (
        fallback
        if fallback in allowed
        else "interactive_provider_unavailable"
    )
    candidate = str(value or "").strip()
    return candidate if candidate in allowed else normalized_fallback


def _normalize_alias(account_alias: str) -> str:
    return normalize_account_alias(account_alias)
