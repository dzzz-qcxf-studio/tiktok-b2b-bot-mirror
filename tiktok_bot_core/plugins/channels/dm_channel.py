"""私信渠道 — 双平台（TikTok + 抖音）"""

import logging

from tiktok_bot_core.extensions.registry import ChannelPlugin
from tiktok_bot_core.browser.client import get_browser
from tiktok_bot_core.platforms import PlatformType, get_platform

logger = logging.getLogger(__name__)


class DMChannel(ChannelPlugin):
    """私信触达（双平台）"""

    name = "dm"
    channel_type = "dm"

    async def execute(self, target: str, content: str, config: dict) -> bool:
        platform = config.get("platform", "tiktok")
        pt = PlatformType.parse(platform)
        pf = get_platform(pt)

        browser = await get_browser()
        try:
            await browser.navigate(pf.user_profile_url(target))
            await browser.wait(2500)

            # 双平台私信按钮文案不同
            msg_btn = await browser.query(pf.selectors.get("message_btn", ""))
            if not msg_btn:
                msg_btn = await browser.query('button:has-text("Message"), button:has-text("私信")')
            if not msg_btn:
                logger.warning(f"@{target} [{pt.value}] 未找到 Message 按钮（可能无私信权限）")
                return False

            await msg_btn.click()
            await browser.wait(2500)

            msg_input = await browser.query(pf.selectors.get("message_input", ""))
            if not msg_input:
                msg_input = await browser.query('div[contenteditable="true"]')
            if not msg_input:
                logger.warning(f"[DMChannel:{pt.value}] 未找到消息输入框")
                return False

            await msg_input.fill(content)
            await browser.wait(1000)
            await browser.press_key("Enter")
            await browser.wait(2000)

            logger.info(f"[DMChannel:{pt.value}] @{target}: {content[:30]}...")
            return True

        except Exception as e:
            logger.error(f"[DMChannel:{pt.value}] @{target} 失败: {e}")
            return False
