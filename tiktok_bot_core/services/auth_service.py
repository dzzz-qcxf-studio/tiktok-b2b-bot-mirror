"""认证服务 — 真实登录（Playwright QR Code 扫码登录）

替代原来"存储 cookies_json 字符串"的 fake 实现。
实际流程：
1. 启动浏览器 → 跳转到登录页
2. 截屏二维码 → 推送给前端/CLI
3. 轮询 cookie 是否出现 sessionid/ttwid 等关键标识
4. 登录成功 → 持久化 cookies 到数据库
5. 后续业务 → 用保存的 cookies 恢复登录状态

支持 TikTok + 抖音双平台。
"""

import asyncio
import json
import logging
import secrets
import time
from pathlib import Path
from typing import Optional

from tiktok_bot_core.platforms import Platform, PlatformType, get_platform
from tiktok_bot_core.storage.database import get_db
from tiktok_bot_core.storage.sqlite_store import SqliteStore

logger = logging.getLogger(__name__)

# 账号上限常量
MAX_ACCOUNTS = 5


# 登录成功的 cookie 标识（不同平台字段名不同）
LOGIN_MARKERS = {
    PlatformType.TIKTOK: ["sessionid", "ttwid", "msToken"],
    PlatformType.DOUYIN: ["sessionid", "ttwid", "uid_tt", "LOGIN_STATUS"],
}


class AuthService:
    """账号认证服务

    真实登录 + Cookie 持久化 + 多账号管理。
    """

    def __init__(self):
        self.db = get_db()
        self.store = SqliteStore()
        self._active_sessions: dict[str, dict] = {}
        # session token -> {"platform": str, "username": str, "page": Page, "task": Task}

    # ============ 多账号 CRUD（真实） ============

    def list_accounts(self, platform: Optional[str] = None) -> list[dict]:
        """列出账号（返回 dict 列表，session 安全）

        返回字段与前端 Account 接口对齐：
        nickname / followers / today / statusKey 等计算字段。
        """
        with self.db.session() as s:
            accounts = self.store.get_tiktok_accounts(s, platform=platform)
            # 在 session 内取值
            return [
                {
                    "id": a.id,
                    "platform": a.platform,
                    "username": a.username,
                    "nickname": a.nickname or f"@{a.username}",
                    "status": a.status,
                    "login_method": a.login_method,
                    "last_login_at": str(a.last_login_at) if a.last_login_at else None,
                    "updated_at": str(a.updated_at) if a.updated_at else "",
                    "follower_count": a.follower_count or 0,
                    "followers": a.follower_count or 0,    # 兼容前端 Account 接口
                    "videos": 0,
                    "likes": 0,
                    "today": {
                        "comments": 0,
                        "dms": 0,
                        "replies": 0,
                        "currentTask": "等待轮询",
                    },
                    "statusKey": "on" if a.status == "logged_in" else (
                        "off" if a.status in ("expired", "pending") else "warn"
                    ),
                }
                for a in accounts
            ]

    def _check_account_limit(self):
        """检查账号数量是否已达上限"""
        with self.db.session() as s:
            count = len(self.store.get_tiktok_accounts(s))
        if count >= MAX_ACCOUNTS:
            raise ValueError(f"账号数量已达上限（{MAX_ACCOUNTS} 个），请先删除不用的账号")

    def add_account(self, platform: str, username: str) -> dict:
        """仅添加账号元信息（还未登录）

        Returns: {"id": int, "platform": str, "username": str, "status": str}
        """
        # 平台合法性校验
        from tiktok_bot_core.platforms import PlatformType
        PlatformType.parse(platform)  # 抛 ValueError if invalid

        # 账号上限校验
        self._check_account_limit()

        with self.db.session() as s:
            a = self.store.add_tiktok_account(
                s, username=username, platform=platform,
                status="pending", login_method="",
            )
            # 在 session 内取值，避免 DetachedInstanceError
            return {
                "id": a.id,
                "platform": a.platform,
                "username": a.username,
                "status": a.status,
            }

    def delete_account(self, aid: int):
        with self.db.session() as s:
            self.store.delete_tiktok_account(s, aid)

    def get_account(self, aid: int):
        with self.db.session() as s:
            return self.store.get_tiktok_account(s, aid)

    # ============ Cookie 持久化（真实） ============

    async def load_cookies_to_browser(self, browser, platform: str, username: Optional[str] = None):
        """从数据库加载已登录账号的 cookies 到浏览器

        Args:
            browser: BrowserClient
            platform: 平台名
            username: 指定账号（None = 用最近活跃的）
        """
        with self.db.session() as s:
            if username:
                acc = self.store.get_tiktok_account_by_username(s, username, platform)
            else:
                acc = self.store.get_active_account(s, platform)

        if not acc or not acc.cookies_json:
            logger.warning(f"平台 {platform} 无可用已登录账号（username={username}）")
            return None

        try:
            cookies = json.loads(acc.cookies_json)
        except json.JSONDecodeError:
            logger.error(f"账号 {acc.username} cookies 格式损坏")
            return None

        # 注入到浏览器 context
        if browser._context:
            await browser._context.add_cookies(cookies)
            logger.info(f"已为 @{acc.username} 注入 cookies（{len(cookies)} 个）")
            return acc.username
        return None

    async def save_cookies_from_browser(self, browser, platform: str, username: str):
        """从浏览器提取当前 cookies 并持久化"""
        if not browser._context:
            raise RuntimeError("浏览器未启动")

        cookies = await browser._context.cookies()
        cookies_json = json.dumps(cookies, ensure_ascii=False)

        # 检测登录状态
        cookie_names = {c["name"] for c in cookies}
        markers = LOGIN_MARKERS[PlatformType.parse(platform)]
        is_logged_in = any(m in cookie_names for m in markers)

        status = "logged_in" if is_logged_in else "expired"

        with self.db.session() as s:
            self.store.add_tiktok_account(
                s,
                username=username,
                platform=platform,
                cookies_json=cookies_json,
                status=status,
                login_method="qrcode",
            )

        logger.info(f"账号 @{username} 状态: {status}（{len(cookies)} cookies）")
        return {"status": status, "cookies_count": len(cookies)}

    # ============ QR Code 登录 ============

    async def start_qrcode_login(self, platform: str, username: str) -> str:
        """启动二维码登录流程

        流程：
        1. 创建登录会话（返回 token）
        2. 后台启动浏览器，截图二维码
        3. 前端调用 get_qrcode(token) 拿到截图路径
        4. 前端轮询 check_login(token) 等待已登录
        5. 登录成功后才在数据库创建账号记录（避免残留）

        Args:
            platform: "tiktok" 或 "douyin"
            username: 此账号的标识名（你自己起的，比如 "marketing_01"）

        Returns:
            session token 字符串
        """
        from tiktok_bot_core.platforms import PlatformType

        token = secrets.token_urlsafe(16)
        PlatformType.parse(platform)

        # 账号上限校验
        self._check_account_limit()

        # 不再预先创建 pending 账号，等登录成功后再创建
        # 启动后台登录任务
        task = asyncio.create_task(self._qrcode_login_task(token, platform, username))
        self._active_sessions[token] = {
            "platform": platform,
            "username": username,
            "task": task,
            "started_at": time.time(),
            "qrcode_path": None,
            "logged_in": False,
        }

        logger.info(f"[{platform}] 启动 QR 登录，会话 token={token[:8]}...")
        return token

    async def _qrcode_login_task(self, token: str, platform: str, username: str):
        """后台任务：打开浏览器 → 截 QR → 等待登录 → 保存 cookies

        所有异常都被捕获，标记会话状态，避免后台崩溃影响 API 响应。
        """
        p = None
        browser = None
        try:
            p, browser, context, page = await self._launch_browser(platform)
            pf = get_platform(platform)

            # 步骤1: 确保登录弹窗出现
            await self._ensure_login_dialog(page, pf, platform)

            # 步骤2: 点击切换到「二维码登录」Tab
            await self._switch_to_qr_tab(page, pf, platform)

            # 步骤3: 等待二维码图片出现并截屏（尽快截屏给前端展示）
            await self._wait_for_qrcode(page, pf, platform)
            logger.info(f"[{platform}] 开始截屏二维码...")
            qrcode_path = await self._capture_qrcode(page, platform)
            if qrcode_path:
                self._active_sessions[token]["qrcode_path"] = str(qrcode_path)
                logger.info(f"[{platform}] 二维码截图已就绪: {qrcode_path}")
            else:
                # 兜底：直接截全页
                fallback_path = Path(__file__).parent.parent / "data" / "qrcodes" / f"qr_{int(time.time())}.png"
                fallback_path.parent.mkdir(parents=True, exist_ok=True)
                await page.screenshot(path=str(fallback_path), full_page=False)
                self._active_sessions[token]["qrcode_path"] = str(fallback_path)
                logger.info(f"[{platform}] 兜底全页截图已保存: {fallback_path}")

            # 步骤4: 轮询等待登录（最长 5 分钟，每 5s 检测一次）
            logged_in = await self._poll_login_status(context, page, platform, username, token)

            if not logged_in:
                cookies = await context.cookies()
                logger.warning(f"[{platform}] @{username} 登录超时（5分钟未扫码），当前 cookies: {[c['name'] for c in cookies]}")

        except Exception as e:
            logger.exception(f"[{platform}] 登录任务失败: {e}")
            self._active_sessions[token]["error"] = str(e)
        finally:
            await self._cleanup_browser(browser, p, platform, username)

    async def _launch_browser(self, platform: str):
        """启动 Playwright 浏览器（带反检测配置）"""
        from playwright.async_api import async_playwright
        from tiktok_bot_core.settings import get_settings

        settings = get_settings()
        p = await async_playwright().start()
        browser = await p.chromium.launch(
            headless=settings.browser_headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
            locale="zh-CN" if platform == "douyin" else "en-US",
            timezone_id="Asia/Shanghai" if platform == "douyin" else "America/New_York",
            permissions=[],
        )
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => false });
            Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh'] });
            Window.prototype.chrome = { runtime: {} };
        """)
        page = await context.new_page()

        pf = get_platform(platform)
        logger.info(f"[{platform}] 正在打开登录页: {pf.login_url}")
        await page.goto(pf.login_url, wait_until="domcontentloaded")
        # 抖音需要 5-8 秒才弹出登录弹窗
        await page.wait_for_timeout(7000)

        return p, browser, context, page

    def _get_login_btn_selectors(self, pf) -> list[str]:
        """获取登录按钮选择器列表（优先平台配置，兜底通用）"""
        return [
            pf.selectors.get("login_btn", ""),
            'xpath=//button[contains(text(), "登录")]',
            'button:has-text("Log in")',
            '[data-e2e="top-login-button"]',
            'a:has-text("登录")',
        ]

    async def _ensure_login_dialog(self, page, pf, platform: str):
        """确保登录弹窗出现，若未自动弹出则手动触发"""
        dialog_selector = pf.selectors.get("login_dialog", "")
        if not dialog_selector:
            return
        try:
            await page.wait_for_selector(dialog_selector, timeout=8000)
            logger.info(f"[{platform}] 登录弹窗已出现")
        except Exception:
            logger.warning(f"[{platform}] 登录弹窗未自动弹出，尝试手动触发...")
            await self._click_login_button(page, pf, platform)

    async def _click_login_button(self, page, pf, platform: str):
        """尝试点击登录按钮"""
        for sel in self._get_login_btn_selectors(pf):
            if not sel:
                continue
            btn = await page.query_selector(sel)
            if btn:
                await btn.click()
                logger.info(f"[{platform}] 已点击登录按钮: {sel}")
                await page.wait_for_timeout(2000)
                return

    async def _switch_to_qr_tab(self, page, pf, platform: str):
        """点击切换到二维码登录 Tab"""
        qr_tab_selector = pf.selectors.get("login_tab_qrcode", "")
        if not qr_tab_selector:
            logger.info(f"[{platform}] 未配置二维码 Tab 选择器，跳过点击")
            return
        try:
            qr_tab = await page.wait_for_selector(qr_tab_selector, timeout=8000)
            if qr_tab:
                await qr_tab.click()
                logger.info(f"[{platform}] 已切换到二维码登录 Tab")
                await page.wait_for_timeout(2000)
        except Exception:
            logger.warning(f"[{platform}] 二维码 Tab 未找到或不可点击（可能已默认显示二维码）")

    async def _wait_for_qrcode(self, page, pf, platform: str):
        """等待二维码元素出现（2 次重试，每次 4s）"""
        qr_selector = pf.selectors.get("login_qrcode", "")
        if not qr_selector:
            logger.warning(f"[{platform}] 未配置二维码选择器")
            return
        qr_tab_selector = pf.selectors.get("login_tab_qrcode", "")
        for retry in range(2):
            try:
                await page.wait_for_selector(qr_selector, timeout=4000)
                await page.wait_for_timeout(500)
                logger.info(f"[{platform}] 二维码元素已找到 (selector={qr_selector})")
                return
            except Exception:
                logger.info(f"[{platform}] 二维码选择器未在 4s 内匹配（第 {retry+1}/2 次），重试...")
                if qr_tab_selector and retry < 1:
                    try:
                        tab = await page.query_selector(qr_tab_selector)
                        if tab:
                            await tab.click()
                            await page.wait_for_timeout(1000)
                    except Exception:
                        pass
        logger.warning(f"[{platform}] 二维码元素未在 8s 内找到，将使用整页截图")

    async def _check_login_cookies(self, context, platform: str) -> bool:
        """检查 cookies 中的登录标记"""
        cookies = await context.cookies()
        cookie_names = {c["name"] for c in cookies}
        markers = LOGIN_MARKERS[PlatformType.parse(platform)]
        return any(m in cookie_names for m in markers)

    async def _check_login_local_storage(self, page, platform: str) -> bool:
        """检查 localStorage 中的登录标记（参考 MediaCrawler: HasUserLogin=1）"""
        if platform != "douyin":
            return False
        try:
            local_storage = await page.evaluate("() => window.localStorage")
            return local_storage.get("HasUserLogin", "") == "1"
        except Exception:
            return False

    async def _is_logged_in(self, context, page, platform: str) -> bool:
        """检测是否已登录（cookies + localStorage 双重检查）"""
        return (
            await self._check_login_cookies(context, platform)
            or await self._check_login_local_storage(page, platform)
        )

    async def _save_login_cookies(self, context, platform: str, username: str):
        """登录成功后保存 cookies 到数据库"""
        cookies = await context.cookies()
        cookies_json = json.dumps(cookies, ensure_ascii=False)
        with self.db.session() as s:
            self.store.add_tiktok_account(
                s,
                username=username,
                platform=platform,
                cookies_json=cookies_json,
                status="logged_in",
                login_method="qrcode",
                qrcode_token="",
            )
        logger.info(f"[{platform}] @{username} 登录成功！已保存 {len(cookies)} 个 cookies")

    async def _poll_login_status(self, context, page, platform: str, username: str, token: str) -> bool:
        """轮询等待用户扫码登录，最长 5 分钟（每 5s 检测一次）"""
        for i in range(60):
            await page.wait_for_timeout(5000)
            logged_in = await self._is_logged_in(context, page, platform)
            if i % 6 == 0:
                logger.info(f"[{platform}] 轮询 #{i+1}: 登录状态={logged_in}")
            if logged_in:
                self._active_sessions[token]["logged_in"] = True
                await self._save_login_cookies(context, platform, username)
                return True
        return False

    async def _cleanup_browser(self, browser, p, platform: str, username: str):
        """确保浏览器和 Playwright 进程总是被关闭，防止资源泄漏"""
        if browser:
            try:
                await browser.close()
            except Exception:
                pass
        if p:
            try:
                await p.stop()
            except Exception:
                pass
        logger.info(f"[{platform}] 登录任务结束（{username}）")

    async def _capture_qrcode(self, page, platform: str) -> Optional[Path]:
        """截屏登录二维码区域"""
        pf = get_platform(platform)
        try:
            qrcode_path = Path(__file__).parent.parent / "data" / "qrcodes" / f"qr_{int(time.time())}.png"
            qrcode_path.parent.mkdir(parents=True, exist_ok=True)

            # 尝试找到 QR 元素并截图其区域
            qr_selector = pf.selectors.get("login_qrcode", "")
            if qr_selector:
                try:
                    qr_el = await page.wait_for_selector(qr_selector, timeout=5000)
                    if qr_el:
                        await qr_el.screenshot(path=str(qrcode_path))
                        box = await qr_el.bounding_box()
                        logger.info(
                            f"[{platform}] 二维码元素截图已保存: {qrcode_path} "
                            f"(位置: x={box.x:.0f} y={box.y:.0f} w={box.width:.0f} h={box.height:.0f})"
                        )
                        return qrcode_path
                except Exception:
                    logger.info(f"[{platform}] QR 选择器 '{qr_selector}' 未匹配到元素，使用整页截图兜底")

            # 兜底：整页截图（此时可能页面没有 QR 码，但还是截下来供调试）
            await page.screenshot(path=str(qrcode_path), full_page=False)
            page_url = page.url
            page_title = await page.title()
            logger.info(
                f"[{platform}] 二维码页面截图已保存: {qrcode_path} "
                f"(页面URL: {page_url}, 标题: {page_title})"
            )
            return qrcode_path
        except Exception as e:
            logger.warning(f"[{platform}] 二维码截图失败: {e}")
        return None

    def get_qrcode_path(self, token: str) -> Optional[str]:
        """获取二维码截图路径"""
        sess = self._active_sessions.get(token)
        return sess.get("qrcode_path") if sess else None

    def check_login(self, token: str) -> dict:
        """检查登录状态

        Returns:
            {"status": "launching" / "waiting" / "scanning" / "confirmed" / "expired",
             "username": str | None,
             "platform": str | None,
             "error": str | None}
        """
        sess = self._active_sessions.get(token)
        if not sess:
            return {"status": "expired", "error": "会话不存在或已过期"}

        if sess.get("error"):
            return {"status": "expired", "error": sess["error"]}

        if sess.get("logged_in"):
            return {
                "status": "confirmed",
                "username": sess.get("username", ""),
                "platform": sess.get("platform", ""),
            }

        # 超时判断（5 分钟）
        if time.time() - sess["started_at"] > 300:
            return {"status": "expired", "error": "登录超时（5分钟未扫码）"}

        elapsed = time.time() - sess["started_at"]
        # 有截图路径就立即显示二维码
        if sess.get("qrcode_path"):
            return {"status": "scanning"}
        # 8s 内: 浏览器正在启动 + 页面加载
        if elapsed < 8:
            return {"status": "launching"}
        # 8-15s: 等待二维码渲染
        if elapsed < 15:
            return {"status": "waiting"}
        # 15s+: 扫码中（即使截图失败也进入扫码状态）
        return {"status": "scanning"}

    # ============ Cookie 过期检测 ============

    async def check_session_valid(self, platform: str, username: str) -> bool:
        """通过请求受保护页面检查 cookie 是否还有效"""
        from tiktok_bot_core.browser.client import BrowserClient
        pf = get_platform(platform)

        browser = BrowserClient()
        try:
            await browser.init()
            loaded = await self.load_cookies_to_browser(browser, platform, username)
            if not loaded:
                return False

            # 访问主页，检查是否被重定向到登录页
            await browser.navigate(pf.home_url)
            await browser.wait(3000)

            url = browser._page.url if browser._page else ""
            # 若 URL 包含 login/login-modal 等关键字，说明已掉线
            if "login" in url.lower():
                return False
            return True
        except Exception as e:
            logger.warning(f"会话检测失败: {e}")
            return False
        finally:
            await browser.close()


# 单例
_auth_service: AuthService | None = None


def get_auth_service() -> AuthService:
    global _auth_service
    if _auth_service is None:
        _auth_service = AuthService()
    return _auth_service
