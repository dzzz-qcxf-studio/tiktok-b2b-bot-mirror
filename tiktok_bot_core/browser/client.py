"""Playwright 浏览器客户端，单例管理 browser 实例。

提供 TikTok 常用操作的便捷方法。
"""

import asyncio
import logging
from pathlib import Path
from typing import Any, Optional

from tiktok_bot_core.settings import get_settings

logger = logging.getLogger(__name__)


class BrowserClient:
    """Playwright 浏览器客户端（异步）"""

    def __init__(self):
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._settings = get_settings()

    async def init(self) -> None:
        """启动浏览器"""
        try:
            from playwright.async_api import async_playwright
        except ImportError as e:
            raise RuntimeError("需要安装 Playwright: pip install playwright && playwright install chromium") from e

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self._settings.browser_headless,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        self._context = await self._browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent=self._settings.browser_user_agent,
        )
        self._page = await self._context.new_page()
        logger.info("浏览器已启动")

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        self._browser = None
        self._page = None
        logger.info("浏览器已关闭")

    # ===== 通用操作 =====

    async def navigate(self, url: str) -> None:
        await self._page.goto(url, wait_until="domcontentloaded")
        await asyncio.sleep(1.5)

    async def wait(self, ms: int) -> None:
        await asyncio.sleep(ms / 1000)

    async def query(self, selector: str) -> Any:
        """查询单个元素"""
        return await self._page.query_selector(selector)

    async def query_all(self, selector: str) -> list:
        return await self._page.query_selector_all(selector)

    async def query_all_limited(self, selector: str, limit: int) -> list:
        """Return at most ``limit`` elements without materializing the full DOM set."""
        normalized_limit = max(0, int(limit))
        if normalized_limit == 0:
            return []
        locator = self._page.locator(selector)
        count = min(await locator.count(), normalized_limit)
        handles = []
        for index in range(count):
            handle = await locator.nth(index).element_handle()
            if handle is not None:
                handles.append(handle)
        return handles

    async def fill(self, selector: str, text: str) -> None:
        el = await self._page.query_selector(selector)
        if el:
            await el.fill(text)

    async def click(self, selector: str) -> None:
        el = await self._page.query_selector(selector)
        if el:
            await el.click()

    async def scroll_down(self, px: int = 500) -> None:
        await self._page.mouse.wheel(0, px)
        await asyncio.sleep(0.5)

    async def text(self, selector: str) -> str:
        el = await self._page.query_selector(selector)
        return await el.inner_text() if el else ""

    async def attr(self, selector: str, name: str) -> str | None:
        el = await self._page.query_selector(selector)
        return await el.get_attribute(name) if el else None

    async def screenshot(self, full_page: bool = False) -> bytes:
        """截取当前页面，供视觉模型分析。"""
        if not self._page:
            raise RuntimeError("浏览器尚未初始化")
        return await self._page.screenshot(type="jpeg", quality=75, full_page=full_page)

    @property
    def current_url(self) -> str:
        return self._page.url if self._page else ""

    async def press_key(self, key: str) -> None:
        await self._page.keyboard.press(key)

    # ===== 上下文管理 =====

    async def __aenter__(self):
        await self.init()
        return self

    async def __aexit__(self, *exc):
        await self.close()


# 全局单例（首次调用时懒加载）
_browser: BrowserClient | None = None


async def get_browser() -> BrowserClient:
    global _browser
    if _browser is None:
        _browser = BrowserClient()
        await _browser.init()
    return _browser


async def close_browser() -> None:
    global _browser
    if _browser:
        await _browser.close()
        _browser = None
