from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tiktok_bot_core.browser.client import BrowserClient


@pytest.mark.asyncio
async def test_navigation_waits_for_commit_on_streaming_video_platform_pages():
    browser = BrowserClient()
    browser._page = MagicMock()
    browser._page.goto = AsyncMock()

    with patch("tiktok_bot_core.browser.client.asyncio.sleep", new=AsyncMock()):
        await browser.navigate("https://www.douyin.com/search/%E5%B9%BF%E8%A5%BF?type=video")

    browser._page.goto.assert_awaited_once_with(
        "https://www.douyin.com/search/%E5%B9%BF%E8%A5%BF?type=video",
        wait_until="commit",
    )
