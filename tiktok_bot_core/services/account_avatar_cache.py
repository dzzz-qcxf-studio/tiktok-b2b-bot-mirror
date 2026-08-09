"""Durable local cache for short-lived social-platform avatar URLs."""

from __future__ import annotations

import base64
import os
import tempfile
from pathlib import Path


MAX_ACCOUNT_AVATAR_BYTES = 1024 * 1024


def account_avatar_mime(payload: bytes) -> str:
    """Return a safe image MIME for known avatar bytes, or an empty string."""

    if not isinstance(payload, bytes) or not payload:
        return ""
    if payload.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if (
        len(payload) >= 12
        and payload.startswith(b"RIFF")
        and payload[8:12] == b"WEBP"
    ):
        return "image/webp"
    return ""


def is_supported_account_avatar(payload: bytes) -> bool:
    return bool(
        len(payload) <= MAX_ACCOUNT_AVATAR_BYTES
        and account_avatar_mime(payload)
    )


def _avatar_path(
    data_root: Path,
    *,
    platform: str,
    account_id: int,
) -> Path:
    normalized_platform = str(platform).strip().lower()
    if normalized_platform not in {"douyin", "tiktok"}:
        raise ValueError("unsupported account avatar platform")
    normalized_id = int(account_id)
    if normalized_id <= 0:
        raise ValueError("account id must be positive")
    return (
        Path(data_root)
        / "account_avatars"
        / f"{normalized_platform}-{normalized_id}.img"
    )


def save_account_avatar(
    data_root: Path,
    *,
    platform: str,
    account_id: int,
    payload: bytes,
) -> bool:
    """Atomically persist validated image bytes without trusting an extension."""

    if not is_supported_account_avatar(payload):
        return False
    destination = _avatar_path(
        data_root,
        platform=platform,
        account_id=account_id,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, destination)
        temporary_path = None
        return True
    except OSError:
        return False
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def load_account_avatar_data_url(
    data_root: Path,
    *,
    platform: str,
    account_id: int,
) -> str:
    """Return cached bytes as a browser-safe data URL, or an empty string."""

    path = _avatar_path(
        data_root,
        platform=platform,
        account_id=account_id,
    )
    try:
        payload = path.read_bytes()
    except OSError:
        return ""
    if not is_supported_account_avatar(payload):
        return ""
    mime = account_avatar_mime(payload)
    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def delete_account_avatar(
    data_root: Path,
    *,
    platform: str,
    account_id: int,
) -> None:
    path = _avatar_path(
        data_root,
        platform=platform,
        account_id=account_id,
    )
    path.unlink(missing_ok=True)
