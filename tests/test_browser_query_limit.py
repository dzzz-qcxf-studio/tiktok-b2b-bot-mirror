from unittest.mock import AsyncMock, MagicMock

import pytest

from tiktok_bot_core.browser.client import BrowserClient


@pytest.mark.asyncio
async def test_query_all_limited_materializes_only_requested_locators():
    page = MagicMock()
    locator = MagicMock()
    locator.count = AsyncMock(return_value=10_000)
    items = []
    for index in range(3):
        item = MagicMock()
        item.element_handle = AsyncMock(return_value=f"handle-{index}")
        items.append(item)
    locator.nth = MagicMock(side_effect=items)
    page.locator.return_value = locator
    browser = BrowserClient()
    browser._page = page

    result = await browser.query_all_limited("article", 3)

    assert result == ["handle-0", "handle-1", "handle-2"]
    page.query_selector_all.assert_not_called()
    assert locator.nth.call_count == 3
