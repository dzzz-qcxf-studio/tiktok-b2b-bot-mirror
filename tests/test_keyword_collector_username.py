"""锁定 KeywordCollector._extract_user_meta 的契约。

修复前：抖音搜索卡片 href 是 /user/<sec_uid>，直接当 username 入库，
        nickname/bio/follower_count 全为空；profile_url 也指向 sec_uid。
修复后：sec_uid 当 stable key 入 tiktok_id，username 取 @unique_id（缺失回退
        sec_uid），nickname / follower_count / bio 由 DOM 抽取。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from tiktok_bot_core.platforms import PlatformType
from tiktok_bot_core.plugins.collectors.keyword_collector import KeywordCollector


class FakeCard:
    """模拟一张抖音 / TikTok 搜索结果卡片。"""

    def __init__(self, href: str, text: str) -> None:
        self._href = href
        self._text = text

    async def get_attribute(self, name: str) -> str | None:
        if name == "href":
            return self._href
        return None

    async def query_selector(self, selector: str) -> Any:
        return None

    async def inner_text(self) -> str:
        return self._text


@pytest.mark.asyncio
async def test_douyin_card_extracts_nickname_handle_and_followers():
    """抖音搜索卡片：href=/user/<sec_uid>，DOM 含昵称 + @handle + 粉丝数 + 签名。"""

    collector = KeywordCollector()
    card = FakeCard(
        href="/user/MS4wLjABAAAAsec_uid_xxx",
        text=(
            "探蜜专供店\n"
            "@tammy_supply\n"
            "1.2万 粉丝\n"
            "源头供应链 · 1688 实力档口"
        ),
    )
    meta = await collector._extract_user_meta(
        card, PlatformType.DOUYIN, fallback_id="MS4wLjABAAAAsec_uid_xxx"
    )

    assert meta["nickname"] == "探蜜专供店"
    assert meta["username"] == "tammy_supply"  # @handle 优先，sec_uid 兜底
    assert meta["follower_count"] == 12000  # 1.2万
    assert "1688" in meta["bio"]


@pytest.mark.asyncio
async def test_douyin_card_without_handle_falls_back_to_sec_uid():
    """用户没设置 unique_id 时，username 仍要可定位该用户。"""

    collector = KeywordCollector()
    card = FakeCard(
        href="/user/MS4wLjABAAAAsec_uid_xxx",
        text="探蜜专供店\n320 粉丝\n源头供应链",
    )
    meta = await collector._extract_user_meta(
        card, PlatformType.DOUYIN, fallback_id="MS4wLjABAAAAsec_uid_xxx"
    )

    assert meta["nickname"] == "探蜜专供店"
    assert meta["username"] == "MS4wLjABAAAAsec_uid_xxx"  # 回退 sec_uid
    assert meta["follower_count"] == 320


@pytest.mark.asyncio
async def test_tiktok_card_extracts_handle_and_followers():
    """TikTok 卡片：href=/@handle，DOM 含昵称 + 粉丝数。"""

    collector = KeywordCollector()
    card = FakeCard(
        href="/@tammy_supply",
        text=(
            "Tammy Supply\n"
            "@tammy_supply\n"
            "12.5K followers\n"
            "Wholesale LED supplier"
        ),
    )
    meta = await collector._extract_user_meta(
        card, PlatformType.TIKTOK, fallback_id="tammy_supply"
    )

    assert meta["username"] == "tammy_supply"
    assert meta["follower_count"] == 12500  # 12.5K
    assert meta["nickname"] == "Tammy Supply"


@pytest.mark.asyncio
async def test_extract_username_returns_sec_uid_not_handle_for_douyin():
    """抖音 href 里只有 sec_uid；_extract_username 仍必须返回它当 stable key。"""

    collector = KeywordCollector()
    card = FakeCard(
        href="/user/MS4wLjABAAAAsec_uid_xxx",
        text="探蜜专供店\n@tammy_supply\n1.2万 粉丝",
    )
    username = await collector._extract_username(card, PlatformType.DOUYIN)

    # sec_uid 当 stable key（不能拿 handle 替换，handle 会变）
    assert username == "MS4wLjABAAAAsec_uid_xxx"


@pytest.mark.asyncio
async def test_empty_card_returns_fallback_without_crashing():
    """DOM 抓不到任何文本时不能抛异常，meta 至少含 fallback。"""

    collector = KeywordCollector()
    card = FakeCard(href="", text="")
    meta = await collector._extract_user_meta(
        card, PlatformType.DOUYIN, fallback_id="MS4wLjABAAAA"
    )

    assert meta["username"] == "MS4wLjABAAAA"
    assert meta["nickname"] == ""
    assert meta["follower_count"] == 0
    assert meta["bio"] == ""