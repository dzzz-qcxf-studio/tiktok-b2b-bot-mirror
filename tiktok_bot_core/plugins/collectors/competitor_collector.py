"""竞品分析搜集器 — 双平台"""

import logging

from tiktok_bot_core.extensions.registry import CollectorPlugin
from tiktok_bot_core.browser.client import get_browser
from tiktok_bot_core.platforms import PlatformType, get_platform

logger = logging.getLogger(__name__)


class CompetitorCollector(CollectorPlugin):
    """从竞品粉丝/关注中挖潜在客户（双平台）"""

    name = "competitor"

    async def collect(self, config: dict) -> list[dict]:
        competitors = config.get("competitor_usernames", [])
        max_per = config.get("max_per_competitor", 50)
        from_type = config.get("from", "followers")
        platform_name = config.get("platform", "tiktok")
        pt = PlatformType.parse(platform_name)

        if not competitors:
            logger.warning("competitor_usernames 为空")
            return []

        browser = await get_browser()
        all_users = []

        for username in competitors:
            try:
                users = await self._fetch(browser, username, from_type, max_per, pt)
                for u in users:
                    u["platform"] = pt.value
                    u["source"] = "competitor"
                    u["source_keyword"] = username
                all_users.extend(users)
                logger.info(f"[CompetitorCollector:{pt.value}] @{username} {from_type}: {len(users)} 个")
            except Exception as e:
                logger.error(f"[CompetitorCollector:{pt.value}] {username} 失败: {e}")
                continue

        return all_users

    @staticmethod
    def _parse_username(href: str) -> str:
        """解析双平台 username（/@xxx 或 /user/xxx）"""
        for prefix in ("/@", "/user/"):
            if prefix in href:
                return href.split(prefix)[1].split("?")[0].strip("/")
        return ""

    @staticmethod
    def _build_url(pt: PlatformType, username: str, from_type: str) -> str:
        """构建 followers/following 页面 URL"""
        if pt == PlatformType.TIKTOK:
            path = "followers" if from_type == "followers" else "following"
            return f"https://www.tiktok.com/@{username}/{path}"
        return f"https://www.douyin.com/user/{username}"

    async def _fetch(self, browser, username: str, from_type: str, max_results: int, pt: PlatformType):
        pf = get_platform(pt)
        url = self._build_url(pt, username, from_type)

        await browser.navigate(url)
        await browser.wait(2500)

        for _ in range(5):
            await browser.scroll_down(700)
            await browser.wait(800)

        card_sel = pf.selectors.get("user_card", "")
        link_sel = pf.selectors.get("user_link", "")
        cards = await browser.query_all(card_sel) if card_sel else []
        if not cards and link_sel:
            cards = await browser.query_all(link_sel)

        users, seen = [], set()
        for card in cards[:max_results]:
            try:
                link = await card.query_selector(link_sel)
                href = await link.get_attribute("href") if link else ""
                uname = self._parse_username(href or "")
                if not uname or uname in seen:
                    continue
                seen.add(uname)
                users.append({
                    "tiktok_id": f"{pt.value}:{uname}",
                    "username": uname,
                    "nickname": "",
                    "bio": "",
                    "follower_count": 0,
                })
            except Exception:
                continue
        return users
