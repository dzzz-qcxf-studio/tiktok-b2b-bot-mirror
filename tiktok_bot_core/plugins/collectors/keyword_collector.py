"""关键词搜集器 — 双平台（TikTok + 抖音）

通过 Platform 抽象自动切换 URL 与选择器。
"""

import logging

from tiktok_bot_core.extensions.registry import CollectorPlugin
from tiktok_bot_core.browser.client import get_browser
from tiktok_bot_core.platforms import PlatformType, get_platform
from tiktok_bot_core.services.auth_service import get_auth_service

logger = logging.getLogger(__name__)


class KeywordCollector(CollectorPlugin):
    """关键词搜集（双平台通用）"""

    name = "keyword"

    async def collect(self, config: dict) -> list[dict]:
        """搜集用户

        Args:
            config: {
                "keywords": list[str],         # 搜索关键词
                "max_per_keyword": int,        # 每个关键词最多返回数
                "type": str,                    # "user" / "video"
                "platform": str,                # "tiktok" / "douyin"（默认 tiktok）
                "account": str,                 # 可选：指定登录账号 username
            }

        Returns:
            [{"tiktok_id", "username", "nickname", "bio", "follower_count", "platform", "source", "source_keyword"}, ...]
        """
        keywords = config.get("keywords", [])
        max_per_keyword = config.get("max_per_keyword", 20)
        platform_name = config.get("platform", "tiktok")

        pt = PlatformType.parse(platform_name)
        pf = get_platform(pt)

        if not keywords:
            logger.warning("keywords 为空，跳过关键词搜集")
            return []

        # 若有已登录账号，先注入 cookies
        browser = await get_browser()
        account = config.get("account")
        if account:
            await get_auth_service().load_cookies_to_browser(browser, pt.value, account)

        all_users = []
        for kw in keywords:
            try:
                users = await self._search_one(browser, kw, max_per_keyword, pf, pt)
                for u in users:
                    u["platform"] = pt.value
                    u["source"] = "keyword_search"
                    u["source_keyword"] = kw
                all_users.extend(users)
                logger.info(f"[KeywordCollector:{pt.value}] '{kw}' 找到 {len(users)} 个用户")
            except Exception as e:
                logger.error(f"[KeywordCollector:{pt.value}] 搜索 '{kw}' 失败: {e}")
                continue

        return all_users

    async def _search_one(
        self,
        browser,
        keyword: str,
        max_results: int,
        pf,
        platform: PlatformType,
    ) -> list[dict]:
        """搜索单个关键词（使用对应平台 URL）"""
        url = pf.search_user_url(keyword)
        await browser.navigate(url)
        await browser.wait(2500)

        for _ in range(3):
            await browser.scroll_down(500)
            await browser.wait(800)

        # 用平台对应的选择器
        card_sel = pf.selectors.get("user_card", "")
        link_sel = pf.selectors.get("user_link", "")

        cards = await browser.query_all(card_sel) if card_sel else []
        if not cards:
            cards = await browser.query_all(link_sel) if link_sel else []

        users = []
        seen = set()

        for card in cards[:max_results]:
            try:
                username = await self._extract_username(card, platform)
                if not username or username in seen:
                    continue
                seen.add(username)

                users.append({
                    "tiktok_id": f"{platform.value}:{username}",  # 复合主键避免跨平台冲突
                    "username": username,
                    "nickname": "",
                    "bio": "",
                    "follower_count": 0,
                })
            except Exception:
                continue

        return users

    async def _extract_username(self, card, platform: PlatformType) -> str:
        """从卡片元素提取 username（双平台兼容）"""
        # 通用：尝试链接
        href = await card.get_attribute("href") if hasattr(card, "get_attribute") else None
        if href:
            # TikTok: /@xxx
            # Douyin: /user/xxx
            for prefix in ("/@", "/user/"):
                if prefix in href:
                    return href.split(prefix)[1].split("?")[0].strip("/")

        # 兜底
        link_sel = get_platform(platform).selectors.get("user_link", "a[href]")
        link = await card.query_selector(link_sel)
        if link:
            href = await link.get_attribute("href")
            if href:
                for prefix in ("/@", "/user/"):
                    if prefix in href:
                        return href.split(prefix)[1].split("?")[0].strip("/")
        return ""
