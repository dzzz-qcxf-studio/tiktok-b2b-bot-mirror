"""评论渠道 — 双平台（TikTok + 抖音）"""

import logging
import random

from tiktok_bot_core.extensions.registry import ChannelPlugin
from tiktok_bot_core.browser.client import get_browser
from tiktok_bot_core.platforms import PlatformType, get_platform

logger = logging.getLogger(__name__)


class CommentChannel(ChannelPlugin):
    """评论触达（双平台）"""

    name = "comment"
    channel_type = "comment"

    async def execute(self, target: str, content: str, config: dict) -> bool:
        """在用户最新视频下发评论

        Args:
            target: username
            content: 评论内容
            config: {
                "platform": "tiktok" / "douyin",
                "interval_range": (min, max) seconds,
            }
        """
        platform = config.get("platform", "tiktok")
        pt = PlatformType.parse(platform)
        pf = get_platform(pt)

        browser = await get_browser()
        try:
            await browser.navigate(pf.user_profile_url(target))
            await browser.wait(2000)

            # 双平台视频卡片选择器不同
            video_sel = pf.selectors.get("video_card", "")
            video_link = await browser.query(f'{video_sel} a') if video_sel else None
            if not video_link:
                video_link = await browser.query('a[href*="/video/"]')
            if not video_link:
                logger.warning(f"@{target} [{pt.value}] 未找到视频")
                return False
            video_url = await video_link.get_attribute("href")
            if not video_url:
                return False

            await browser.navigate(video_url)
            await browser.wait(2500)

            # 评论输入框
            sel_input = pf.selectors.get("comment_input", "")
            comment_input = await browser.query(sel_input) if sel_input else None
            if not comment_input:
                comment_input = await browser.query('div[contenteditable="true"]')
            if not comment_input:
                logger.warning(f"[CommentChannel:{pt.value}] 未找到评论输入框")
                return False

            await comment_input.click()
            await browser.wait(500)
            await comment_input.fill(content)
            await browser.wait(800)

            sel_post = pf.selectors.get("comment_post", "")
            send_btn = await browser.query(sel_post) if sel_post else None
            if not send_btn:
                send_btn = await browser.query('button:has-text("Post"), button:has-text("发送")')
            if send_btn:
                await send_btn.click()
                await browser.wait(2000)
                logger.info(f"[CommentChannel:{pt.value}] @{target}: {content[:30]}...")
                return True
            return False

        except Exception as e:
            logger.error(f"[CommentChannel:{pt.value}] @{target} 失败: {e}")
            return False
        finally:
            # 反封号行为模拟
            if random.random() < 0.3:
                await browser.navigate(get_platform(pt).home_url)
                await browser.wait(random.randint(2, 5))
