"""进程内账号租约。

交互式登录、登录态检测和 Pipeline 浏览器会话必须共享同一个 manager，
以保证同一平台账号在任一时刻只有一个使用者。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from tiktok_bot_core.platforms import PlatformType


_ALLOWED_OWNER_PURPOSES = frozenset({"login", "pipeline", "check"})


class AccountBusyError(RuntimeError):
    """账号已经被另一个用途持有。"""

    def __init__(
        self,
        *,
        platform: str,
        account_key: str,
        owner: str,
        current_owner: str,
    ) -> None:
        owner = _normalize_owner(owner)
        current_owner = _normalize_owner(current_owner)
        self.platform = platform
        self.account_key = account_key
        self.owner = owner
        self.current_owner = current_owner
        super().__init__(
            f"Account {platform}:{account_key} is busy "
            f"(owner={owner}, current_owner={current_owner})"
        )


@dataclass(frozen=True, slots=True)
class AccountLease:
    """一次账号租约；可显式释放，也可作为异步上下文管理器使用。"""

    platform: str
    account_key: str
    owner: str
    _manager: "AccountLeaseManager" = field(repr=False)
    _lease_id: object = field(repr=False)

    async def release(self) -> None:
        """释放租约；重复调用不产生副作用。"""

        await self._manager._release(
            (self.platform, self.account_key),
            self._lease_id,
        )

    async def __aenter__(self) -> "AccountLease":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: Any,
    ) -> None:
        await self.release()


class AccountLeaseManager:
    """原子管理进程内的账号租约。"""

    def __init__(self) -> None:
        self._leases: dict[tuple[str, str], tuple[str, object]] = {}
        self._lock = asyncio.Lock()

    async def acquire(
        self,
        platform: str | PlatformType,
        account_id_or_alias: int | str,
        *,
        owner: str,
    ) -> AccountLease:
        """原子申请一个账号租约。

        account id 与新账号 alias 使用同一规范化键空间，避免数字 alias
        绕过已有账号 id 的互斥。
        """

        normalized_platform = PlatformType.parse(platform).value
        account_key = _normalize_account_key(account_id_or_alias)
        normalized_owner = _normalize_owner(owner)
        key = (normalized_platform, account_key)
        lease_id = object()

        async with self._lock:
            current = self._leases.get(key)
            if current is not None:
                current_owner, _ = current
                raise AccountBusyError(
                    platform=normalized_platform,
                    account_key=account_key,
                    owner=normalized_owner,
                    current_owner=current_owner,
                )
            self._leases[key] = (normalized_owner, lease_id)

        return AccountLease(
            platform=normalized_platform,
            account_key=account_key,
            owner=normalized_owner,
            _manager=self,
            _lease_id=lease_id,
        )

    async def _release(self, key: tuple[str, str], lease_id: object) -> None:
        async with self._lock:
            current = self._leases.get(key)
            if current is not None and current[1] is lease_id:
                del self._leases[key]


def _normalize_account_key(account_id_or_alias: int | str) -> str:
    if isinstance(account_id_or_alias, bool):
        raise ValueError("account id or alias must not be boolean")
    if isinstance(account_id_or_alias, int):
        return str(account_id_or_alias)
    if isinstance(account_id_or_alias, str):
        normalized = account_id_or_alias.strip().casefold()
        if normalized:
            if normalized.isdecimal():
                return str(int(normalized))
            return normalized
    raise ValueError("account id or alias must be a non-empty string or integer")


def _normalize_owner(owner: str) -> str:
    if not isinstance(owner, str) or not owner.strip():
        raise ValueError("owner must be a non-empty string")
    purpose = owner.strip().partition(":")[0].casefold()
    if purpose not in _ALLOWED_OWNER_PURPOSES:
        allowed = ", ".join(sorted(_ALLOWED_OWNER_PURPOSES))
        raise ValueError(f"owner purpose must be one of: {allowed}")
    return purpose
