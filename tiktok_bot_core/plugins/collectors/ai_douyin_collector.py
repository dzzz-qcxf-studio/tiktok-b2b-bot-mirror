"""AI 抖音浏览搜集器。

在已登录的抖音推荐流中逐条读取公开视频信息，由 LLM 判断 B2B 相关性，
并把命中的作者转换成标准用户记录。仅浏览和采集公开信息，不执行互动。
"""

import json
import logging
from urllib.parse import urlparse

from tiktok_bot_core.browser.client import get_browser
from tiktok_bot_core.extensions.registry import CollectorPlugin
from tiktok_bot_core.llm.client import get_llm_client
from tiktok_bot_core.platforms import DOUYIN
from tiktok_bot_core.services.auth_service import get_auth_service

logger = logging.getLogger(__name__)


class AIDouyinCollector(CollectorPlugin):
    """用 LLM 驱动抖音推荐流浏览与潜客发现。"""

    name = "ai_douyin"

    async def collect(self, config: dict) -> list[dict]:
        max_videos = max(1, min(int(config.get("max_videos", 20)), 100))
        min_score = max(0.0, min(float(config.get("min_score", 0.65)), 1.0))
        interests = config.get("interests") or config.get("keywords") or []
        account = (config.get("account") or "").strip()

        browser = await get_browser()
        if account:
            await get_auth_service().load_cookies_to_browser(browser, "douyin", account)

        await browser.navigate(DOUYIN.home_url)
        await browser.wait(2500)

        results: list[dict] = []
        seen_videos: set[str] = set()
        seen_users: set[str] = set()

        for index in range(max_videos):
            observation = await self._observe_active_video(browser)
            video_key = observation.get("video_url") or observation.get("author_url") or observation.get("text")
            if video_key and video_key not in seen_videos:
                seen_videos.add(video_key)
                decision = await self._evaluate(observation, interests)
                username = self._username_from_url(observation.get("author_url", ""))
                if decision["is_relevant"] and decision["score"] >= min_score and username not in seen_users:
                    seen_users.add(username)
                    results.append({
                        "tiktok_id": f"douyin:{username}",
                        "username": username,
                        "nickname": observation.get("nickname", ""),
                        "bio": observation.get("text", "")[:1000],
                        "follower_count": 0,
                        "platform": "douyin",
                        "source": "ai_browse",
                        "source_keyword": ",".join(interests)[:255],
                        "profile_url": observation.get("author_url", ""),
                    })
                    logger.info("[AIDouyinCollector] 命中 @%s，评分 %.2f", username, decision["score"])

            if index < max_videos - 1:
                await browser.press_key("ArrowDown")
                await browser.wait(int(config.get("interval_ms", 1800)))

        return results

    async def _observe_active_video(self, browser) -> dict:
        card_selector = DOUYIN.selectors["video_card"]
        cards = await browser.query_all(card_selector)
        card = cards[0] if cards else None
        if not card:
            return {"text": "", "author_url": "", "video_url": browser.current_url, "nickname": ""}

        text_selector = DOUYIN.selectors["video_text"]
        author_selector = DOUYIN.selectors["author_link"]
        text_node = await card.query_selector(text_selector)
        author_node = await card.query_selector(author_selector)
        text = (await text_node.inner_text()).strip() if text_node else (await card.inner_text()).strip()
        author_url = await author_node.get_attribute("href") if author_node else ""
        nickname = (await author_node.inner_text()).strip() if author_node else ""
        return {
            "text": text[:2000],
            "author_url": author_url or "",
            "video_url": browser.current_url,
            "nickname": nickname[:100],
        }

    async def _evaluate(self, observation: dict, interests: list[str]) -> dict:
        if not observation.get("text") or not observation.get("author_url"):
            return {"is_relevant": False, "score": 0.0, "reason": "missing public metadata"}

        prompt = f"""判断这条抖音公开视频的作者是否可能是目标 B2B 客户。
目标行业或关键词：{json.dumps(interests, ensure_ascii=False)}
视频公开文本：{observation['text']}

只返回 JSON：
{{"is_relevant": true或false, "score": 0到1, "reason": "简短理由"}}
不要仅因出现单个关键词判定为相关；优先识别采购商、经销商、批发商、工程商和企业客户。"""
        try:
            result = await get_llm_client().json_completion(prompt)
            return {
                "is_relevant": bool(result.get("is_relevant", False)),
                "score": max(0.0, min(float(result.get("score", 0)), 1.0)),
                "reason": str(result.get("reason", "")),
            }
        except Exception as exc:
            logger.warning("AI 判断失败，跳过当前视频: %s", exc)
            return {"is_relevant": False, "score": 0.0, "reason": str(exc)}

    @staticmethod
    def _username_from_url(url: str) -> str:
        if not url:
            return ""
        path = urlparse(url).path
        if "/user/" not in path:
            return ""
        return path.split("/user/", 1)[1].split("/", 1)[0].strip()
