"""平台抽象 — TikTok + 抖音双平台支持

两个平台虽类似但关键区别：
- 域名不同：tiktok.com vs douyin.com
- 搜索 URL 不同
- 登录方式有别（抖音支持手机号 + 验证码）
- 私信等操作选择器不同

通过 Platform 类统一封装，避免业务代码分支判断。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Union
from urllib.parse import quote


class PlatformType(str, Enum):
    TIKTOK = "tiktok"
    DOUYIN = "douyin"

    @classmethod
    def parse(cls, value: Union[str, "PlatformType"]) -> "PlatformType":
        if isinstance(value, cls):
            return value
        v = str(value).lower().strip()
        if v in ("tiktok", "tk", "抖音国际版"):
            return cls.TIKTOK
        if v in ("douyin", "dy", "抖音", "抖音中国版"):
            return cls.DOUYIN
        raise ValueError(f"未知平台: {value}")


@dataclass(frozen=True)
class Platform:
    """平台配置（不可变）

    不同平台的关键 URL 与登录入口不同。
    """
    name: str
    home_url: str
    search_user_url_tpl: str
    user_profile_url_tpl: str
    login_url: str
    # CSS 选择器差异（部分组件命名空间不同）
    selectors: dict[str, str]

    def search_user_url(self, keyword: str) -> str:
        return self.search_user_url_tpl.format(kw=quote(keyword))

    def user_profile_url(self, username: str) -> str:
        return self.user_profile_url_tpl.format(username=username)


# === 两个预定义平台 ===

TIKTOK = Platform(
    name="tiktok",
    home_url="https://www.tiktok.com/",
    search_user_url_tpl="https://www.tiktok.com/search?q={kw}&type=user",
    user_profile_url_tpl="https://www.tiktok.com/@{username}",
    login_url="https://www.tiktok.com/login",
    selectors={
        "user_card": '[data-e2e="search_user-card"]',
        "user_link": 'a[href*="/@"]',
        "video_card": '[data-e2e="user-post-item"]',
        "comment_input": '[data-e2e="comment-input"]',
        "comment_post": '[data-e2e="comment-post"]',
        "message_btn": '[data-e2e="message-btn"]',
        "message_input": 'div[contenteditable="true"]',
        "qrcode_img": '[data-e2e="qrcode-img"] img, img[alt*="qrcode" i]',
        # 登录相关
        "login_dialog": '#login-modal, [data-e2e="login-modal"], [class*="login-container"]',
        "login_btn": 'button:has-text("Log in"), [data-e2e="top-login-button"]',
        "login_tab_qrcode": 'button:has-text("QR code"), [data-e2e*="qrcode-login"], [class*="qrcode-tab"], div[class*="login-tab"]:has(img[alt*="qr"])',
        "login_qrcode": 'canvas[class*="qrcode" i], img[class*="qrcode" i], [data-e2e="qrcode"] img, [class*="qrcode"] img, #qrcode img',
        "user_info_check": '[data-e2e="user-info"]',  # 登录后出现的标识
    },
)

DOUYIN = Platform(
    name="douyin",
    home_url="https://www.douyin.com/",
    search_user_url_tpl="https://www.douyin.com/search/{kw}?type=user",
    user_profile_url_tpl="https://www.douyin.com/user/{username}",
    login_url="https://www.douyin.com/",
    selectors={
        # 抖音 Web 端选择器（参考 MediaCrawler + 实际 DOM 结构）
        "user_card": 'li[data-e2e="search-user-card"], div.user-card',
        "user_link": 'a[href*="/user/"]',
        "video_card": 'div[data-e2e="user-post-item"], li.user-post',
        "comment_input": 'div[contenteditable="true"]',
        "comment_post": 'button[data-e2e="comment-post"], button:has-text("发送")',
        "message_btn": 'button:has-text("私信"), button:has-text("发消息")',
        "message_input": 'div[contenteditable="true"], textarea',
        "qrcode_img": '#douyin_login_comp_scan_code img',
        # 登录相关（2026-07-19 浏览器实测：弹窗自动弹出，二维码默认显示）
        "login_dialog": '#login-panel-new',
        "login_btn": 'xpath=//p[text()="登录"]',  # 兜底：手动触发登录弹窗
        "login_tab_qrcode": '',  # 抖音默认显示二维码，无需点击 Tab
        "login_qrcode": '#douyin_login_comp_scan_code img',  # 178x178 base64 PNG
        "login_qrcode_container": '#animate_qrcode_container',
        "login_tab_password": '',  # 无密码 Tab（抖音默认扫码）
        "user_info_check": 'div.user-info, [class*="user-info" i]',
    },
)

_REGISTRY: dict[PlatformType, Platform] = {
    PlatformType.TIKTOK: TIKTOK,
    PlatformType.DOUYIN: DOUYIN,
}


def get_platform(name: str | PlatformType) -> Platform:
    """根据名称获取平台配置"""
    pt = PlatformType.parse(name)
    return _REGISTRY[pt]


def list_platforms() -> list[Platform]:
    """列出所有支持平台"""
    return list(_REGISTRY.values())
