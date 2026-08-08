"""Unified browser providers for pipeline jobs.

Platform-specific browser behaviour belongs in providers.  Callers use the
same registry interface and TikTok deliberately has no Playwright fallback.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlsplit

from tiktok_bot_core.browser.client import BrowserClient
from tiktok_bot_core.services.auth_service import (
    _has_authenticated_cookie,
    build_auth_paths,
)
from tiktok_bot_core.services.interactive_login import (
    AuthVerification,
    InteractiveBrowserSession,
    PersistedAuthState,
    atomic_write_private_json,
    secure_private_directory,
)

logger = logging.getLogger(__name__)


def extract_douyin_profile_metadata(user: Mapping[str, Any]) -> dict[str, Any]:
    """Extract the small, display-safe profile subset used by account cards."""

    raw_nickname = user.get("nickname")
    nickname = (
        raw_nickname.strip()[:200]
        if isinstance(raw_nickname, str)
        else ""
    )

    follower_count = user.get("follower_count")
    if (
        isinstance(follower_count, bool)
        or not isinstance(follower_count, int)
        or follower_count < 0
    ):
        follower_count = None

    avatar_url = ""
    for field_name in ("avatar_thumb", "avatar_medium", "avatar_larger"):
        avatar = user.get(field_name)
        if not isinstance(avatar, Mapping):
            continue
        candidates = avatar.get("url_list")
        if not isinstance(candidates, list):
            continue
        for candidate in candidates:
            if not isinstance(candidate, str):
                continue
            normalized = candidate.strip()
            parsed = urlsplit(normalized)
            if (
                parsed.scheme == "https"
                and bool(parsed.netloc)
                and len(normalized) <= 1000
            ):
                avatar_url = normalized
                break
        if avatar_url:
            break

    return {
        "nickname": nickname,
        "avatar_url": avatar_url,
        "follower_count": follower_count,
    }


@dataclass(frozen=True)
class BrowserAvailability:
    available: bool
    code: str = ""
    message: str = ""


@dataclass
class BrowserSession:
    platform: str
    account_id: int
    client: Any
    _released: bool = field(default=False, init=False, repr=False)
    _release_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock,
        init=False,
        repr=False,
    )


class BrowserProviderUnavailableError(RuntimeError):
    def __init__(self, availability: BrowserAvailability):
        self.code = availability.code
        self.availability = availability
        message = availability.message or availability.code
        super().__init__(f"{availability.code}: {message}")


class InteractiveLoginNotAuthenticatedError(RuntimeError):
    """Refuse to persist browser state that has not passed verification."""

    code = "interactive_login_not_authenticated"

    def __init__(self, diagnostic_code: str = "") -> None:
        self.diagnostic_code = diagnostic_code
        message = self.code
        if diagnostic_code:
            message = f"{message}: {diagnostic_code}"
        super().__init__(message)


class BrowserProvider(Protocol):
    async def check_available(self, account: Any) -> BrowserAvailability:
        ...

    async def acquire(self, account: Any) -> BrowserSession:
        ...

    async def release(self, session: BrowserSession) -> None:
        ...


class InteractiveLoginProvider(Protocol):
    """Provider contract for a user-driven login browser lifecycle.

    This is intentionally separate from :class:`BrowserProvider`: a pipeline
    browser is job-owned while an interactive browser remains open for a user.
    """

    provider_name: str
    interactive_browser_kind: str

    async def check_interactive_available(
        self,
        account: Any,
    ) -> BrowserAvailability:
        ...

    async def open_interactive_login(
        self,
        *,
        account_key: str,
        account: Any,
    ) -> InteractiveBrowserSession:
        ...

    async def verify_interactive_login(
        self,
        session: InteractiveBrowserSession,
    ) -> AuthVerification:
        ...

    async def persist_interactive_login(
        self,
        session: InteractiveBrowserSession,
    ) -> PersistedAuthState:
        ...

    async def close_interactive_login(
        self,
        session: InteractiveBrowserSession,
    ) -> None:
        ...


class FingerprintInteractiveLoginProvider:
    """Nominal marker required for every TikTok fingerprint adapter.

    Structural attributes are intentionally insufficient here: an adapter
    must explicitly inherit this class before the registry will expose it to
    TikTok interactive login.
    """

    provider_id: str

    def validates_account_binding(self, account: Any) -> bool:
        if isinstance(account, Mapping):
            configured_provider = account.get("browser_provider")
            profile_id = account.get("browser_profile_id")
        else:
            configured_provider = getattr(
                account,
                "browser_provider",
                "",
            )
            profile_id = getattr(account, "browser_profile_id", "")
        provider_id = str(getattr(self, "provider_id", "") or "")
        return bool(
            provider_id
            and str(configured_provider or "") == provider_id
            and str(profile_id or "").strip()
        )


class DouyinPlaywrightProvider:
    """Create one independent Playwright client/context for every job."""

    def __init__(
        self,
        client_factory: Callable[[], BrowserClient] = BrowserClient,
    ) -> None:
        self._client_factory = client_factory

    async def check_available(self, account: Any) -> BrowserAvailability:
        if _platform_name(getattr(account, "platform", "")) != "douyin":
            return BrowserAvailability(
                available=False,
                code="platform_account_mismatch",
                message="抖音浏览器 Provider 只能用于抖音账号",
            )
        return BrowserAvailability(available=True)

    async def acquire(self, account: Any) -> BrowserSession:
        availability = await self.check_available(account)
        if not availability.available:
            raise BrowserProviderUnavailableError(availability)

        account_id = int(account.id)
        client: BrowserClient | None = None
        try:
            client = self._client_factory()
            await client.init()
            cookies = _decode_cookies(getattr(account, "cookies_json", ""))
            if cookies:
                await client._context.add_cookies(cookies)
        except BaseException:
            if client is not None:
                try:
                    await _complete_despite_cancellation(client.close())
                except BaseException:
                    logger.warning(
                        "浏览器获取失败后的清理也失败",
                        exc_info=True,
                    )
            raise

        if client is None:
            raise RuntimeError("browser client factory returned no client")
        return BrowserSession(
            platform="douyin",
            account_id=account_id,
            client=client,
        )

    async def release(self, session: BrowserSession) -> None:
        async with session._release_lock:
            if session._released:
                return
            cancelled = await _complete_despite_cancellation(
                session.client.close()
            )
            session._released = True
            if cancelled:
                raise asyncio.CancelledError


class DouyinInteractiveLoginProvider:
    """Open an account-scoped headed browser for manual Douyin login."""

    provider_name = "douyin-playwright-persistent"
    interactive_browser_kind = "playwright-persistent"
    _HOME_URL = "https://www.douyin.com/"
    # 抖音要求 webapp 客户端参数，缺参时接口对已登录会话也返回
    # status_code=8「用户未登录」，会把真实登录判成失败。
    _PROFILE_PROBE_URL = (
        "https://www.douyin.com/aweme/v1/web/user/profile/self/"
        "?device_platform=webapp&aid=6383&channel=channel_pc_web"
        "&publish_video_strategy_type=2&version_code=170400"
        "&version_name=17.4.0&pc_client_type=1"
    )
    _PROFILE_PROBE_SCRIPT = """
        async (url) => {
            try {
                const response = await window.fetch(url, {
                    credentials: "include",
                });
                if (!response.ok) {
                    return {
                        ok: false,
                        status: response.status,
                        payload: null,
                        error_code: "http_error",
                    };
                }
                try {
                    return {
                        ok: true,
                        status: response.status,
                        payload: await response.json(),
                        error_code: "",
                    };
                } catch (_error) {
                    return {
                        ok: true,
                        status: response.status,
                        payload: null,
                        error_code: "invalid_json",
                    };
                }
            } catch (_error) {
                return {
                    ok: false,
                    status: null,
                    payload: null,
                    error_code: "fetch_failed",
                };
            }
        }
    """

    def __init__(self, chromium: Any, *, data_root: Path) -> None:
        self._chromium = chromium
        self._data_root = Path(data_root)

    async def check_interactive_available(
        self,
        account: Any,
    ) -> BrowserAvailability:
        if _platform_name(getattr(account, "platform", "")) != "douyin":
            return BrowserAvailability(
                available=False,
                code="platform_account_mismatch",
                message="抖音交互登录 Provider 只能用于抖音账号",
            )
        return BrowserAvailability(available=True)

    async def open_interactive_login(
        self,
        *,
        account_key: str,
        account: Any,
    ) -> InteractiveBrowserSession:
        availability = await self.check_interactive_available(account)
        if not availability.available:
            raise BrowserProviderUnavailableError(availability)
        return await self.open(account_key=account_key)

    async def open(self, *, account_key: str) -> InteractiveBrowserSession:
        normalized_account_key = str(account_key)
        paths = build_auth_paths(
            self._data_root,
            "douyin",
            normalized_account_key,
        )
        secure_private_directory(paths.profile_dir)
        context = await self._chromium.launch_persistent_context(
            user_data_dir=paths.profile_dir,
            headless=False,
        )
        try:
            pages = context.pages
            page = pages[0] if pages else await context.new_page()
            await page.goto(self._HOME_URL, wait_until="domcontentloaded")
        except BaseException:
            try:
                await _complete_despite_cancellation(context.close())
            except BaseException:
                logger.warning(
                    "交互式登录启动失败后的 Context 清理也失败",
                    exc_info=True,
                )
            raise
        return InteractiveBrowserSession(
            account_key=normalized_account_key,
            context=context,
            page=page,
            profile_dir=paths.profile_dir,
            storage_state_path=paths.storage_state,
        )

    async def verify(
        self,
        session: InteractiveBrowserSession,
    ) -> AuthVerification:
        protected_page_ok = False
        homepage_diagnostic = ""
        try:
            response = await session.page.goto(
                self._HOME_URL,
                wait_until="domcontentloaded",
            )
            current_url = str(session.page.url or "")
            status = getattr(response, "status", None)
            protected_page_ok = (
                isinstance(status, int)
                and 200 <= status < 400
                and _is_douyin_authenticated_page(current_url)
            )
        except Exception:
            homepage_diagnostic = "homepage_navigation_failed"

        if not protected_page_ok and not homepage_diagnostic:
            homepage_diagnostic = "homepage_not_available"

        (
            identity_probe_ok,
            identity_diagnostic,
            profile_metadata,
        ) = await self._probe_identity(session.page)

        # Read the cookie snapshot after the server probe so a session that the
        # server invalidates during that request cannot pass on stale cookies.
        cookies = await session.context.cookies([self._HOME_URL])
        has_authenticated_cookie = _has_authenticated_cookie(
            cookies,
            "douyin",
        )

        local_storage_login_detected = False
        try:
            local_storage = await session.page.evaluate(
                "() => window.localStorage"
            )
            if isinstance(local_storage, Mapping):
                local_storage_login_detected = (
                    local_storage.get("HasUserLogin") == "1"
                )
        except Exception:
            pass

        if not protected_page_ok:
            diagnostic_code = homepage_diagnostic
        elif not identity_probe_ok:
            diagnostic_code = identity_diagnostic
        elif not has_authenticated_cookie:
            diagnostic_code = "cookie_consistency_failed"
        else:
            diagnostic_code = ""

        return AuthVerification(
            authenticated=(
                has_authenticated_cookie
                and protected_page_ok
                and identity_probe_ok
            ),
            has_authenticated_cookie=has_authenticated_cookie,
            protected_page_ok=protected_page_ok,
            local_storage_login_detected=local_storage_login_detected,
            identity_probe_ok=identity_probe_ok,
            diagnostic_code=diagnostic_code,
            nickname=profile_metadata["nickname"],
            avatar_url=profile_metadata["avatar_url"],
            follower_count=profile_metadata["follower_count"],
        )

    async def verify_interactive_login(
        self,
        session: InteractiveBrowserSession,
    ) -> AuthVerification:
        return await self.verify(session)

    async def _probe_identity(
        self,
        page: Any,
    ) -> tuple[bool, str, dict[str, Any]]:
        empty_profile = extract_douyin_profile_metadata({})
        try:
            result = await page.evaluate(
                self._PROFILE_PROBE_SCRIPT,
                self._PROFILE_PROBE_URL,
            )
        except Exception:
            return False, "profile_probe_failed", empty_profile

        if not isinstance(result, Mapping):
            return False, "profile_probe_failed", empty_profile

        probe_error = result.get("error_code")
        if probe_error == "invalid_json":
            return False, "profile_probe_invalid_json", empty_profile
        if result.get("ok") is not True:
            if probe_error == "http_error":
                return False, "profile_probe_http_error", empty_profile
            return False, "profile_probe_failed", empty_profile

        payload = result.get("payload")
        if not isinstance(payload, Mapping):
            return False, "profile_probe_invalid_json", empty_profile

        profile_status = payload.get("status_code")
        if profile_status == 8:
            return False, "profile_not_logged_in", empty_profile
        if profile_status != 0:
            return False, "profile_status_unknown", empty_profile

        user = payload.get("user")
        if not isinstance(user, Mapping):
            return False, "profile_identity_missing", empty_profile
        has_identity = any(
            isinstance(user.get(field), str) and bool(user.get(field).strip())
            for field in ("uid", "sec_uid")
        )
        if not has_identity:
            return False, "profile_identity_missing", empty_profile
        return True, "", extract_douyin_profile_metadata(user)

    async def persist(
        self,
        session: InteractiveBrowserSession,
    ) -> PersistedAuthState:
        paths = build_auth_paths(
            self._data_root,
            "douyin",
            session.account_key,
        )
        if session.storage_state_path != paths.storage_state:
            raise ValueError("storage state path was not built for account")
        if session.profile_dir != paths.profile_dir:
            raise ValueError("profile path was not built for account")

        verification = await self.verify(session)
        if not verification.authenticated:
            raise InteractiveLoginNotAuthenticatedError(
                verification.diagnostic_code
            )

        state = await session.context.storage_state(indexed_db=True)
        if not isinstance(state, Mapping):
            raise ValueError("browser storage state must be a mapping")
        cookies = state.get("cookies", [])
        origins = state.get("origins", [])
        if not isinstance(cookies, list):
            raise ValueError("browser storage state cookies must be a list")
        if not isinstance(origins, list):
            raise ValueError("browser storage state origins must be a list")

        atomic_write_private_json(session.storage_state_path, state)
        return PersistedAuthState(
            storage_state_path=session.storage_state_path,
            cookie_count=len(cookies),
            origin_count=len(origins),
            cookies=cookies,
        )

    async def persist_interactive_login(
        self,
        session: InteractiveBrowserSession,
    ) -> PersistedAuthState:
        return await self.persist(session)

    async def close_interactive_login(
        self,
        session: InteractiveBrowserSession,
    ) -> None:
        cancelled = await _complete_despite_cancellation(
            session.context.close()
        )
        if cancelled:
            raise asyncio.CancelledError


class UnavailableFingerprintProvider(
    FingerprintInteractiveLoginProvider,
):
    """Default TikTok provider until a concrete fingerprint adapter exists."""

    provider_name = "unavailable-fingerprint-provider"
    provider_id = "unavailable"
    interactive_browser_kind = "fingerprint"
    _availability = BrowserAvailability(
        available=False,
        code="fingerprint_provider_unavailable",
        message="TikTok 指纹浏览器 Provider 尚未配置",
    )

    async def check_available(self, account: Any) -> BrowserAvailability:
        return self._availability

    async def acquire(self, account: Any) -> BrowserSession:
        raise BrowserProviderUnavailableError(self._availability)

    async def release(self, session: BrowserSession) -> None:
        return None

    async def check_interactive_available(
        self,
        account: Any,
    ) -> BrowserAvailability:
        return self._availability

    async def open_interactive_login(
        self,
        *,
        account_key: str,
        account: Any,
    ) -> InteractiveBrowserSession:
        raise BrowserProviderUnavailableError(self._availability)

    async def verify_interactive_login(
        self,
        session: InteractiveBrowserSession,
    ) -> AuthVerification:
        raise BrowserProviderUnavailableError(self._availability)

    async def persist_interactive_login(
        self,
        session: InteractiveBrowserSession,
    ) -> PersistedAuthState:
        raise BrowserProviderUnavailableError(self._availability)

    async def close_interactive_login(
        self,
        session: InteractiveBrowserSession,
    ) -> None:
        return None


class BrowserProviderRegistry:
    """Separate provider lookup for pipeline and interactive lifecycles."""

    def __init__(
        self,
        providers: Mapping[str, BrowserProvider] | None = None,
        *,
        interactive_providers: (
            Mapping[str, InteractiveLoginProvider] | None
        ) = None,
    ) -> None:
        defaults: dict[str, BrowserProvider] = {
            "douyin": DouyinPlaywrightProvider(),
            "tiktok": UnavailableFingerprintProvider(),
        }
        if providers is not None:
            defaults.update(
                {_platform_name(name): provider for name, provider in providers.items()}
            )
        self._providers = defaults
        interactive_defaults: dict[str, InteractiveLoginProvider] = {
            "tiktok": UnavailableFingerprintProvider(),
        }
        if interactive_providers is not None:
            interactive_defaults.update(
                {
                    _platform_name(name): provider
                    for name, provider in interactive_providers.items()
                }
            )
        self._interactive_providers = interactive_defaults

    def register(self, platform: Any, provider: BrowserProvider) -> None:
        self._providers[_platform_name(platform)] = provider

    def get(self, platform: Any) -> BrowserProvider:
        name = _platform_name(platform)
        try:
            return self._providers[name]
        except KeyError as exc:
            raise ValueError(f"未知浏览器平台: {name}") from exc

    def register_interactive(
        self,
        platform: Any,
        provider: InteractiveLoginProvider,
    ) -> None:
        self._interactive_providers[_platform_name(platform)] = provider

    def register_tiktok_fingerprint_interactive(
        self,
        provider: FingerprintInteractiveLoginProvider,
    ) -> None:
        if not isinstance(
            provider,
            FingerprintInteractiveLoginProvider,
        ):
            raise TypeError(
                "TikTok interactive provider must inherit "
                "FingerprintInteractiveLoginProvider"
            )
        self._interactive_providers["tiktok"] = provider

    def get_interactive(
        self,
        platform: Any,
    ) -> InteractiveLoginProvider:
        name = _platform_name(platform)
        try:
            return self._interactive_providers[name]
        except KeyError as exc:
            raise ValueError(
                f"未注册交互登录浏览器 Provider: {name}"
            ) from exc

    def get_tiktok_fingerprint_interactive(
        self,
    ) -> FingerprintInteractiveLoginProvider:
        provider = self.get_interactive("tiktok")
        if not isinstance(
            provider,
            FingerprintInteractiveLoginProvider,
        ):
            raise ValueError("TikTok 指纹浏览器 Provider 尚未配置")
        return provider


def _platform_name(platform: Any) -> str:
    value = getattr(platform, "value", platform)
    return str(value).strip().lower()


def _decode_cookies(raw: Any) -> list[dict[str, Any]]:
    if not raw:
        return []
    cookies = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(cookies, list):
        raise ValueError("账号 cookies_json 必须是 JSON 数组")
    return cookies


def _is_douyin_authenticated_page(url: str) -> bool:
    parsed = urlsplit(url.strip())
    hostname = (parsed.hostname or "").lower()
    normalized_path = parsed.path.lower()
    return (
        parsed.scheme == "https"
        and (
            hostname == "douyin.com"
            or hostname.endswith(".douyin.com")
        )
        and "login" not in hostname
        and "passport" not in hostname
        and "login" not in normalized_path
        and "passport" not in normalized_path
    )


def require_browser_client(
    config: Mapping[str, Any],
    *,
    platform: str,
) -> Any:
    """Resolve the job-owned browser client or fail closed."""

    session = config.get("browser_session")
    if not isinstance(session, BrowserSession):
        raise ValueError("browser_session is required")
    if session._released:
        raise ValueError("browser_session is already released")
    if session.client is None:
        raise ValueError("browser_session client is missing")
    if session.platform != _platform_name(platform):
        raise ValueError("browser_session platform mismatch")
    account_id = config.get("account_id")
    if account_id is not None and int(account_id) != session.account_id:
        raise ValueError("browser_session account mismatch")
    return session.client


async def _complete_despite_cancellation(operation: Any) -> bool:
    """Finish a resource cleanup once started.

    Returns ``True`` when the caller was cancelled while cleanup was running.
    Operation errors still propagate so callers can keep the resource
    retryable.
    """

    task = asyncio.create_task(operation)
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    await task
    return cancelled


__all__ = [
    "BrowserAvailability",
    "BrowserProvider",
    "BrowserProviderRegistry",
    "BrowserProviderUnavailableError",
    "BrowserSession",
    "DouyinInteractiveLoginProvider",
    "DouyinPlaywrightProvider",
    "FingerprintInteractiveLoginProvider",
    "InteractiveLoginNotAuthenticatedError",
    "InteractiveLoginProvider",
    "require_browser_client",
    "UnavailableFingerprintProvider",
]
