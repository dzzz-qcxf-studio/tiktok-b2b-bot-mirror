"""用真实 BrowserClient + 假 LLM router 跑一次 e2e，确认驱动顺序真实可行。

不写真实 LLM key。pytest 默认运行；可手动调 `python -m pytest -s -k test_browse_e2e`
让 fake router 强制返回 done 后查看 history。
"""

import asyncio

import pytest


@pytest.mark.asyncio
async def test_real_browser_client_drives_through_douyin_with_done():
    from unittest.mock import AsyncMock, MagicMock

    from tiktok_bot_core.events.bus import EventBus, EventType
    from tiktok_bot_core.services.browse_agent import BrowseAgent
    from tiktok_bot_core.browser.client import BrowserClient

    router = MagicMock()
    router.json_completion = AsyncMock(
        return_value={
            "action": "done",
            "payload": {"summary": "真实驱动闭环"},
            "rationale": "smoke test",
        }
    )
    bus = EventBus()

    # 用真实 BrowserClient，但 headless 模式快速退场，避免抢资源。
    from tiktok_bot_core.settings import reload_settings

    settings = reload_settings()
    settings.browser_headless = True
    agent = BrowseAgent(
        router=router,
        bus=bus,
        browser_factory=BrowserClient,
        max_steps=2,
    )

    try:
        result = await agent.run(
            goal="smoke",
            platform="douyin",
            account_id=1,
        )
    finally:
        pass

    assert result.status == "done"
    assert result.summary == "真实驱动闭环"
    assert result.steps >= 1
    assert any(
        e.payload.get("action") == "done"
        for e in bus.history(EventType.BROWSE_STEP)
    )