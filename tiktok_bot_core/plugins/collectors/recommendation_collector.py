"""推荐流搜集器 — 双平台"""

import logging

from tiktok_bot_core.extensions.registry import CollectorPlugin
from tiktok_bot_core.browser.client import get_browser
from tiktok_bot_core.platforms import PlatformType, get_platform

logger = logging.getLogger(__name__)


def _parse_username_from_href(href: str, platform: PlatformType) -> str:
    """双平台 username 解析

    - TikTok: /@alice → alice
    - 抖音:    /user/MS4wLjABAAAA... → MS4wLjABAAAA...
    """
    if not href:
        return ""
    for prefix in ("/@", "/user/"):
        if prefix in href:
            return href.split(prefix)[1].split("?")[0].strip("/")
    return ""


class RecommendationCollector(CollectorPlugin):
    """推荐用户搜集（双平台）"""

    name = "recommendation"

    async def collect(self, config: dict) -> list[dict]:
        """根据种子用户搜集推荐

        Args:
            config: {
                "seed_usernames": list[str],
                "max_per_seed": int,
                "platform": str,    # "tiktok" / "douyin"
            }
        """
        seeds = config.get("seed_usernames", [])
        max_per_seed = config.get("max_per_seed", 10)
        platform_name = config.get("platform", "tiktok")
        pt = PlatformType.parse(platform_name)
        pf = get_platform(pt)

        if not seeds:
            logger.warning("seed_usernames 为空")
            return []

        browser = await get_browser()
        all_users = []

        for username in seeds:
            try:
                users = await self._fetch_recommend(browser, username, max_per_seed, pf, pt)
                for u in users:
                    u["platform"] = pt.value
                    u["source"] = "recommendation"
                    u["source_keyword"] = username
                all_users.extend(users)
                logger.info(f"[RecommendationCollector:{pt.value}] @{username} 推荐了 {len(users)} 个用户")
            except Exception as e:
                logger.error(f"[RecommendationCollector:{pt.value}] {username} 失败: {e}")
                continue

        return all_users

    async def _fetch_recommend(
        self, browser, username: str, max_results: int, pf, platform: PlatformType
    ) -> list[dict]:
        await browser.navigate(pf.user_profile_url(username))
        await browser.wait(2500)

        for _ in range(4):
            await browser.scroll_down(800)
            await browser.wait(800)

        # 用平台对应的选择器
        card_sel = pf.selectors.get("user_card", "")
        link_sel = pf.selectors.get("user_link", "")

        cards = await browser.query_all(card_sel) if card_sel else []
        if not cards and link_sel:
            cards = await browser.query_all(link_sel)

        users = []
        seen = set()

        for card in cards[:max_results]:
            try:
                link = await card.query_selector(link_sel)
                href = await link.get_attribute("href") if link else ""
                uname = _parse_username_from_href(href or "", platform)
                if not uname or uname == username or uname in seen:
                    continue
                seen.add(uname)
                users.append({
                    "tiktok_id": f"{platform.value}:{uname}",
                    "username": uname,
                    "nickname": "",
                    "bio": "",
                    "follower_count": 0,
                })
            except Exception:
                continue

        return users
