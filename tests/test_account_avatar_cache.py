from __future__ import annotations

from pathlib import Path

import pytest

from tiktok_bot_core.services.account_avatar_cache import (
    delete_account_avatar,
    load_account_avatar_data_url,
    save_account_avatar,
)


JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"avatar-bytes"


def test_avatar_cache_survives_expiring_remote_url(tmp_path: Path):
    assert save_account_avatar(
        tmp_path,
        platform="douyin",
        account_id=7,
        payload=JPEG_BYTES,
    ) is True

    data_url = load_account_avatar_data_url(
        tmp_path,
        platform="douyin",
        account_id=7,
    )

    assert data_url.startswith("data:image/jpeg;base64,")
    assert "https://" not in data_url


@pytest.mark.parametrize(
    "payload",
    [
        b"not-an-image",
        b"\xff\xd8\xff" + (b"x" * (1024 * 1024 + 1)),
    ],
    ids=["invalid-format", "oversized"],
)
def test_avatar_cache_rejects_invalid_or_oversized_payload(
    tmp_path: Path,
    payload: bytes,
):
    assert save_account_avatar(
        tmp_path,
        platform="douyin",
        account_id=7,
        payload=payload,
    ) is False
    assert load_account_avatar_data_url(
        tmp_path,
        platform="douyin",
        account_id=7,
    ) == ""


def test_avatar_cache_is_removed_with_account(tmp_path: Path):
    assert save_account_avatar(
        tmp_path,
        platform="douyin",
        account_id=7,
        payload=JPEG_BYTES,
    ) is True

    delete_account_avatar(
        tmp_path,
        platform="douyin",
        account_id=7,
    )

    assert load_account_avatar_data_url(
        tmp_path,
        platform="douyin",
        account_id=7,
    ) == ""
