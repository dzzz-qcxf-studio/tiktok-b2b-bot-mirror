"""浏览器抽象层 — Playwright 封装"""

from .client import BrowserClient, get_browser, close_browser

__all__ = ["BrowserClient", "get_browser", "close_browser"]
