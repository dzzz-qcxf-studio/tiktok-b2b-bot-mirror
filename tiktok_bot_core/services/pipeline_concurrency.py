"""Unified platform-capacity and account-exclusivity control."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass
class ConcurrencyLease:
    platform: str
    account_id: int
    _manager: "PipelineConcurrencyManager" = field(repr=False)
    _released: bool = field(default=False, init=False, repr=False)
    _release_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock,
        init=False,
        repr=False,
    )

    async def release(self) -> None:
        async with self._release_lock:
            if self._released:
                return
            release_task = asyncio.create_task(
                self._manager._release(self.platform, self.account_id)
            )
            cancelled = False
            while not release_task.done():
                try:
                    await asyncio.shield(release_task)
                except asyncio.CancelledError:
                    cancelled = True
            await release_task
            self._released = True
            if cancelled:
                raise asyncio.CancelledError

    async def __aenter__(self) -> "ConcurrencyLease":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.release()


class PipelineConcurrencyManager:
    """Atomically enforce platform limits and one job per social account."""

    def __init__(
        self,
        *,
        douyin_limit: int = 1,
        platform_limits: Mapping[str, int] | None = None,
    ) -> None:
        if douyin_limit < 0:
            raise ValueError("douyin_limit 不能为负数")
        limits = {"douyin": douyin_limit, "tiktok": 0}
        if platform_limits:
            limits.update(
                {
                    _platform_name(platform): int(limit)
                    for platform, limit in platform_limits.items()
                }
            )
        if any(limit < 0 for limit in limits.values()):
            raise ValueError("平台并发限制不能为负数")

        self._limits = limits
        self._active_by_platform: dict[str, int] = {}
        self._active_accounts: set[tuple[str, int]] = set()
        self._condition = asyncio.Condition()

    async def acquire(
        self,
        platform: Any,
        account_id: int,
    ) -> ConcurrencyLease:
        name = _platform_name(platform)
        limit = self._limits.get(name)
        if limit is None or limit <= 0:
            raise PipelineConcurrencyUnavailableError(name, limit)
        account_key = (name, int(account_id))
        async with self._condition:
            await self._condition.wait_for(
                lambda: self._can_acquire(name, account_key)
            )
            self._active_by_platform[name] = self.active_count(name) + 1
            self._active_accounts.add(account_key)
        return ConcurrencyLease(
            platform=name,
            account_id=int(account_id),
            _manager=self,
        )

    async def try_acquire(
        self,
        platform: Any,
        account_id: int,
    ) -> ConcurrencyLease | None:
        """Acquire immediately or return ``None`` without consuming a slot."""

        name = _platform_name(platform)
        limit = self._limits.get(name)
        if limit is None or limit <= 0:
            return None
        account_key = (name, int(account_id))
        async with self._condition:
            if not self._can_acquire(name, account_key):
                return None
            self._active_by_platform[name] = self.active_count(name) + 1
            self._active_accounts.add(account_key)
        return ConcurrencyLease(
            platform=name,
            account_id=int(account_id),
            _manager=self,
        )

    def active_count(self, platform: Any) -> int:
        return self._active_by_platform.get(_platform_name(platform), 0)

    def is_account_active(self, platform: Any, account_id: int) -> bool:
        return (_platform_name(platform), int(account_id)) in self._active_accounts

    def _can_acquire(
        self,
        platform: str,
        account_key: tuple[str, int],
    ) -> bool:
        limit = self._limits.get(platform, 0)
        return (
            limit > 0
            and self.active_count(platform) < limit
            and account_key not in self._active_accounts
        )

    async def _release(self, platform: str, account_id: int) -> None:
        account_key = (platform, account_id)
        async with self._condition:
            if account_key not in self._active_accounts:
                return
            self._active_accounts.remove(account_key)
            remaining = self.active_count(platform) - 1
            if remaining:
                self._active_by_platform[platform] = remaining
            else:
                self._active_by_platform.pop(platform, None)
            self._condition.notify_all()


def _platform_name(platform: Any) -> str:
    value = getattr(platform, "value", platform)
    return str(value).strip().lower()


class PipelineConcurrencyUnavailableError(RuntimeError):
    code = "platform_concurrency_unavailable"

    def __init__(self, platform: str, limit: int | None) -> None:
        self.platform = platform
        self.limit = limit
        reason = "未配置" if limit is None else f"并发上限为 {limit}"
        super().__init__(f"{self.code}: {platform} {reason}")


__all__ = [
    "ConcurrencyLease",
    "PipelineConcurrencyManager",
    "PipelineConcurrencyUnavailableError",
]
