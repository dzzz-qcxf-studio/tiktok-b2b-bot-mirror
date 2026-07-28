"""交互式登录会话的进程内状态模型。"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from tiktok_bot_core.platforms import PlatformType


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


class InvalidLoginTransition(RuntimeError):
    """登录会话尝试了状态机未允许的转移。"""

    def __init__(self, from_status: str, to_status: str) -> None:
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(
            f"Invalid login session transition: {from_status} -> {to_status}"
        )


@dataclass(slots=True)
class LoginSession:
    token: str
    platform: str
    account_alias: str
    account_id: int | None
    status: str
    started_at: datetime
    expires_at: datetime
    error: str | None
    authenticated: bool
    persisted: bool

    @classmethod
    def new(
        cls,
        platform: str | PlatformType,
        account_alias: str,
        account_id: int | None = None,
    ) -> "LoginSession":
        normalized_platform = PlatformType.parse(platform).value
        normalized_alias = _normalize_alias(account_alias)
        started_at = datetime.now(timezone.utc)
        return cls(
            token=secrets.token_urlsafe(32),
            platform=normalized_platform,
            account_alias=normalized_alias,
            account_id=account_id,
            status="launching",
            started_at=started_at,
            expires_at=started_at + timedelta(minutes=5),
            error=None,
            authenticated=False,
            persisted=False,
        )

    def transition(self, next_status: str) -> None:
        if next_status not in ALLOWED_TRANSITIONS.get(self.status, set()):
            raise InvalidLoginTransition(self.status, next_status)
        self.status = next_status


def _normalize_alias(account_alias: str) -> str:
    if not isinstance(account_alias, str) or not account_alias.strip():
        raise ValueError("account alias must be a non-empty string")
    return account_alias.strip()
