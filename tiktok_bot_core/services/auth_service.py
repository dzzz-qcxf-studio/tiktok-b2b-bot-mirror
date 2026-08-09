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
import hashlib
import json
import logging
import re
import secrets
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Mapping, Optional

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tiktok_bot_core.models.entities import TikTokAccount
from tiktok_bot_core.platforms import Platform, PlatformType, get_platform
from tiktok_bot_core.services.account_avatar_cache import (
    delete_account_avatar,
    load_account_avatar_data_url,
    save_account_avatar,
)
from tiktok_bot_core.storage.database import get_db
from tiktok_bot_core.storage.sqlite_store import SqliteStore

logger = logging.getLogger(__name__)

# 账号上限常量
MAX_ACCOUNTS = 5


def _database_data_root(database) -> Path:
    """Keep runtime cache beside the active SQLite database when possible."""

    db_url = str(getattr(database, "db_url", "") or "")
    prefix = "sqlite:///"
    if db_url.startswith(prefix):
        raw_path = db_url[len(prefix):]
        if raw_path and raw_path != ":memory:":
            return Path(raw_path).resolve().parent
    return Path(__file__).resolve().parents[2] / "data"


class AccountAliasConflictError(ValueError):
    """Canonical account alias is already present or legacy-ambiguous."""

    code = "account_alias_conflict"

    def __init__(self) -> None:
        super().__init__("account_alias_conflict")


class AccountLimitReachedError(ValueError):
    """No new social account may be inserted at the global capacity limit."""

    code = "account_limit_reached"

    def __init__(self) -> None:
        super().__init__("account_limit_reached")


def normalize_account_alias(value: str) -> str:
    """Return the one canonical account alias used by every persistence path."""

    if not isinstance(value, str):
        raise ValueError("account alias must be a string")
    normalized = unicodedata.normalize("NFKC", value).strip()
    if not normalized:
        raise ValueError("account alias must not be empty")
    return normalized


def begin_immediate_account_write(session) -> None:
    """Serialize canonical account scans and writes across SQLite processes."""

    bind = session.get_bind()
    if bind.dialect.name == "sqlite":
        session.execute(text("BEGIN IMMEDIATE"))


def ensure_account_capacity(account_count: int) -> None:
    """Reject a new account using the single global account limit."""

    if account_count >= MAX_ACCOUNTS:
        raise AccountLimitReachedError()


def _matching_alias_accounts(accounts, alias: str):
    canonical = normalize_account_alias(alias)
    matches = []
    for account in accounts:
        try:
            existing = normalize_account_alias(account.username)
        except ValueError:
            continue
        if existing == canonical:
            matches.append(account)
    return matches


# 只接受服务端登录会话 Cookie。ttwid、msToken、uid_tt 等访客 Cookie
# 会在扫码前出现，不能作为登录成功依据。
AUTH_COOKIE_MARKERS: dict[PlatformType, tuple[str, ...]] = {
    PlatformType.TIKTOK: ("sessionid", "sessionid_ss", "sid_guard"),
    PlatformType.DOUYIN: ("sessionid", "sessionid_ss", "sid_guard"),
}


@dataclass(frozen=True)
class AuthPaths:
    profile_dir: Path = field(repr=False)
    storage_state: Path = field(repr=False)


def build_auth_paths(
    data_root: Path,
    platform: str,
    account_key: str,
) -> AuthPaths:
    if platform not in {"tiktok", "douyin"}:
        raise ValueError("invalid platform")

    try:
        normalized_key = normalize_account_alias(account_key)
    except ValueError as exc:
        raise ValueError("invalid account key") from exc

    readable_slug = re.sub(r"[^\w-]+", "-", normalized_key)
    readable_slug = readable_slug.strip("-_")[:48].rstrip("-_")
    if not readable_slug:
        readable_slug = "account"
    key_hash = hashlib.sha256(
        normalized_key.encode("utf-8")
    ).hexdigest()[:12]
    safe_key = f"{readable_slug}-{key_hash}"

    return AuthPaths(
        profile_dir=data_root / "browser_profiles" / platform / safe_key,
        storage_state=data_root / "auth_states" / platform / f"{safe_key}.json",
    )


def _has_authenticated_cookie(cookies, platform: str) -> bool:
    platform_type = PlatformType.parse(platform)
    markers = AUTH_COOKIE_MARKERS[platform_type]
    if platform_type is not PlatformType.DOUYIN:
        return any(
            cookie.get("name") in markers
            and bool(str(cookie.get("value") or "").strip())
            for cookie in cookies
        )

    valid_cookies = [
        cookie
        for cookie in cookies
        if _is_current_douyin_cookie(cookie)
    ]
    has_session_cookie = any(
        cookie.get("name") in markers
        and bool(str(cookie.get("value") or "").strip())
        for cookie in valid_cookies
    )
    # 抖音 PC web 已登录时并不下发 LOGIN_STATUS，缺失不能判为未登录；
    # 权威判据是服务端探针，Cookie 只复核域、有效期和会话标记。
    # 只有平台明确下发 LOGIN_STATUS=0 才是确定的未登录信号。
    explicitly_logged_out = any(
        cookie.get("name") == "LOGIN_STATUS"
        and str(cookie.get("value") or "").strip() == "0"
        for cookie in valid_cookies
    )
    return has_session_cookie and not explicitly_logged_out


def _is_current_douyin_cookie(cookie) -> bool:
    domain = str(cookie.get("domain") or "").strip().lower().lstrip(".")
    if domain != "douyin.com" and not domain.endswith(".douyin.com"):
        return False
    expires = cookie.get("expires")
    if expires in (None, "", -1, 0):
        return True
    try:
        return float(expires) > time.time()
    except (TypeError, ValueError):
        return False


class AuthService:
    """账号认证服务

    真实登录 + Cookie 持久化 + 多账号管理。
    """

    def __init__(self):
        self.db = get_db()
        self.store = SqliteStore()
        self.data_root = _database_data_root(self.db)
        self._active_sessions: dict[str, dict] = {}
        # session token -> {"platform": str, "username": str, "page": Page, "task": Task}

    def _avatar_data_root(self) -> Path:
        """Support lightweight test/service instances that bypass __init__."""

        configured = getattr(self, "data_root", None)
        if configured is not None:
            return Path(configured)
        return _database_data_root(self.db)

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
                    "display_name": a.display_name or "",
                    "nickname": a.nickname or f"@{a.username}",
                    "avatar_url": a.avatar_url or "",
                    "avatar_data_url": load_account_avatar_data_url(
                        self._avatar_data_root(),
                        platform=a.platform,
                        account_id=a.id,
                    ),
                    "status": a.status,
                    "login_method": a.login_method,
                    "browser_provider": a.browser_provider or "",
                    "browser_profile_id": a.browser_profile_id or "",
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

    def update_account_display_name(
        self,
        aid: int,
        display_name: str,
    ) -> dict | None:
        normalized = display_name.strip()
        if len(normalized) > 100:
            raise ValueError("display name must not exceed 100 characters")
        with self.db.session() as session:
            account = self.store.get_tiktok_account(session, aid)
            if account is None:
                return None
            account.display_name = normalized
            account.updated_at = datetime.utcnow()
            session.flush()
            return {
                "id": account.id,
                "platform": account.platform,
                "username": account.username,
                "display_name": account.display_name or "",
                "nickname": account.nickname or f"@{account.username}",
                "avatar_url": account.avatar_url or "",
                "avatar_data_url": load_account_avatar_data_url(
                    self._avatar_data_root(),
                    platform=account.platform,
                    account_id=account.id,
                ),
                "status": account.status,
                "login_method": account.login_method,
                "browser_provider": account.browser_provider or "",
                "browser_profile_id": account.browser_profile_id or "",
                "last_login_at": (
                    str(account.last_login_at)
                    if account.last_login_at
                    else None
                ),
                "updated_at": (
                    str(account.updated_at) if account.updated_at else ""
                ),
                "follower_count": account.follower_count or 0,
                "followers": account.follower_count or 0,
                "videos": 0,
                "likes": 0,
                "today": {
                    "comments": 0,
                    "dms": 0,
                    "replies": 0,
                    "currentTask": "等待轮询",
                },
                "statusKey": (
                    "on"
                    if account.status == "logged_in"
                    else "off"
                    if account.status in ("expired", "pending")
                    else "warn"
                ),
            }

    def _check_account_limit(self, session=None):
        """检查账号数量是否已达上限"""
        if session is None:
            with self.db.session() as managed_session:
                count = len(
                    self.store.get_tiktok_accounts(managed_session)
                )
        else:
            count = len(self.store.get_tiktok_accounts(session))
        ensure_account_capacity(count)

    def add_account(self, platform: str, username: str) -> dict:
        """仅添加账号元信息（还未登录）

        Returns: {"id": int, "platform": str, "username": str, "status": str}
        """
        # 平台合法性校验
        from tiktok_bot_core.platforms import PlatformType
        normalized_platform = PlatformType.parse(platform).value
        canonical_alias = normalize_account_alias(username)

        try:
            with self.db.session() as s:
                begin_immediate_account_write(s)
                platform_accounts = self.store.get_tiktok_accounts(
                    s,
                    platform=normalized_platform,
                )
                if _matching_alias_accounts(
                    platform_accounts,
                    canonical_alias,
                ):
                    raise AccountAliasConflictError()

                # Limit check belongs to the same transaction and runs after
                # canonical conflict detection so collisions have one stable
                # public error even when the account table is full.
                self._check_account_limit(s)
                account = TikTokAccount(
                    username=canonical_alias,
                    platform=normalized_platform,
                    status="pending",
                    login_method="",
                )
                s.add(account)
                s.flush()
                return {
                    "id": account.id,
                    "platform": account.platform,
                    "username": account.username,
                    "status": account.status,
                }
        except IntegrityError as exc:
            raise AccountAliasConflictError() from exc

    def delete_account(self, aid: int):
        removed: tuple[str, int] | None = None
        with self.db.session() as s:
            account = self.store.get_tiktok_account(s, aid)
            if account is not None:
                removed = (account.platform, account.id)
            self.store.delete_tiktok_account(s, aid)
        if removed is not None:
            delete_account_avatar(
                self._avatar_data_root(),
                platform=removed[0],
                account_id=removed[1],
            )

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
        is_logged_in = _has_authenticated_cookie(cookies, platform)

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
        """创建二维码登录会话。

        流程：
        1. 创建登录会话（返回 token）
        2. **调用方**负责启动后台登录任务（推荐 FastAPI BackgroundTasks,
           直接 asyncio.create_task 会被响应返回时取消）
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

        # 仅注册 session;真正启动后台任务由调用方通过 BackgroundTasks 注入。
        # 这样能保证 session 已就绪后再启动 task,避免响应未返回前 task 已开始
        # 访问 _active_sessions[token] 找不到 key。
        self._active_sessions[token] = {
            "platform": platform,
            "username": username,
            "task": None,                # 由 endpoint 注入 BackgroundTasks 后填回
            "started_at": time.time(),
            "qrcode_path": None,
            "qrcode_payload": None,
            "logged_in": False,
            "persisted": False,
            "status": "launching",
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
            self._active_sessions[token]["status"] = "waiting"
            pf = get_platform(platform)

            # 步骤1: 确保登录弹窗出现
            await self._ensure_login_dialog(page, pf, platform)

            # 步骤2: 点击切换到「二维码登录」Tab
            await self._switch_to_qr_tab(page, pf, platform)

            # 步骤3: 等待二维码出现
            await self._wait_for_qrcode(page, pf, platform)

            # 主路径:从 DOM 读源代码(canvas.toDataURL / img src),不截图
            qr_payload = await self._extract_qrcode_from_dom(page, platform)
            if qr_payload:
                self._active_sessions[token]["qrcode_payload"] = qr_payload
                logger.info(
                    f"[{platform}] 二维码已就绪 (type={qr_payload['type']}, "
                    f"size={len(qr_payload['value'])} bytes)"
                )
            else:
                # 兜底:截图保存为文件,API 通过文件路径返回
                logger.info(f"[{platform}] DOM 读源代码失败,转为截图兜底...")
                qrcode_path = await self._capture_qrcode(page, platform)
                if qrcode_path:
                    self._active_sessions[token]["qrcode_path"] = str(qrcode_path)
                    logger.info(f"[{platform}] 二维码截图兜底: {qrcode_path}")
                else:
                    # 最后兜底:整页截图
                    fallback_path = (
                        Path(__file__).parent.parent / "data" / "qrcodes"
                        / f"qr_{int(time.time())}.png"
                    )
                    fallback_path.parent.mkdir(parents=True, exist_ok=True)
                    await page.screenshot(path=str(fallback_path), full_page=False)
                    self._active_sessions[token]["qrcode_path"] = str(fallback_path)
                    logger.info(f"[{platform}] 整页截图兜底: {fallback_path}")

            # 步骤4: 轮询等待登录（最长 5 分钟，每 5s 检测一次）
            # The QR image being ready is not login success. Keep the modal open
            # until the authenticated session has been verified and stored.
            self._active_sessions[token]["status"] = "scanning"
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
        """主动点击登录按钮，触发登录弹窗出现。

        旧版被动等自动弹窗已不适用于 tiktok.com / 抖音 新版首页
        (默认不再弹登录框),改为"先点击,再等弹窗"。"""
        logger.info(f"[{platform}] 主动点击登录按钮...")
        clicked = await self._click_login_button(page, pf, platform)
        if clicked:
            return  # 已点击,等待弹窗出现由 _wait_for_qrcode 内部处理
        # 兜底:可能页面已经显示登录对话框(部分平台 SPA 直接渲染)
        dialog_selector = pf.selectors.get("login_dialog", "")
        if dialog_selector:
            try:
                await page.wait_for_selector(dialog_selector, timeout=8000)
                logger.info(f"[{platform}] 登录弹窗已出现（无需点击）")
            except Exception:
                logger.warning(f"[{platform}] 主动点击未匹配到任何登录按钮,弹窗也未自动出现")

    async def _click_login_button(self, page, pf, platform: str) -> bool:
        """尝试点击登录按钮。

        Returns:
            True  - 至少有一个选择器命中并点击成功
            False - 全部未命中
        """
        for sel in self._get_login_btn_selectors(pf):
            if not sel:
                continue
            btn = await page.query_selector(sel)
            if btn:
                await btn.click()
                logger.info(f"[{platform}] 已点击登录按钮: {sel}")
                await page.wait_for_timeout(2000)
                return True
        return False

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
        """只认非空服务端会话 Cookie，拒绝 visitor-only Cookie。"""
        cookies = await context.cookies()
        return _has_authenticated_cookie(cookies, platform)

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
        """认证 Cookie 是唯一成功判据，localStorage 只用于诊断。"""
        authenticated = await self._check_login_cookies(context, platform)
        if not authenticated and await self._check_login_local_storage(page, platform):
            logger.info("[%s] 检测到本地登录状态，继续等待认证 Cookie 写入", platform)
        return authenticated

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
                session = self._active_sessions[token]
                session["status"] = "verifying"
                try:
                    await self._save_login_cookies(context, platform, username)
                except Exception as exc:
                    logger.exception("[%s] 登录已验证但 Cookie 保存失败: %s", platform, exc)
                    session["status"] = "expired"
                    session["error"] = "登录已完成，但账号信息保存失败，请重试"
                    return False
                session["persisted"] = True
                session["logged_in"] = True
                session["status"] = "confirmed"
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

    async def _extract_qrcode_from_dom(self, page, platform: str) -> Optional[dict]:
        """直接从 DOM 读取二维码源数据，避免截图不精准。

        返回 dict(API 直接透传给前端渲染):
          - {"type": "data_url", "value": "data:image/png;base64,..."}  ← canvas
          - {"type": "data_url", "value": "data:image/png;base64,..."}  ← img src= data:
          - {"type": "remote_url", "value": "https://..."}                ← img src= http
          - {"type": "file", "value": "/abs/path/to.png"}                  ← 截图兜底

        读取顺序:canvas → img (data:) → img (http) → 截图兜底。
        """
        pf = get_platform(platform)
        qr_selector = pf.selectors.get("login_qrcode", "")
        if not qr_selector:
            return None

        try:
            qr_el = await page.wait_for_selector(qr_selector, timeout=8000, state="attached")
        except Exception:
            logger.info(f"[{platform}] QR 选择器未匹配,准备截图兜底")
            return None

        # 1. canvas:toDataURL(最精确,直接拿到 PNG base64)
        try:
            data_url = await qr_el.evaluate("""
                (el) => {
                    if (!el) return null;
                    if (el.tagName === 'CANVAS') {
                        try { return el.toDataURL('image/png'); } catch (e) { return null; }
                    }
                    return null;
                }
            """)
            if data_url and isinstance(data_url, str) and data_url.startswith('data:image/'):
                logger.info(f"[{platform}] QR 从 canvas.toDataURL() 获取 (data_url)")
                return {"type": "data_url", "value": data_url}
        except Exception as e:
            logger.info(f"[{platform}] canvas 读取失败: {e}")

        # 2. img src — 可能是 data: 或远程 URL
        try:
            src = await qr_el.evaluate("(el) => el.tagName === 'IMG' ? el.src : null")
            if src and isinstance(src, str):
                if src.startswith('data:image/'):
                    logger.info(f"[{platform}] QR 从 <img src=data:...> 获取 (data_url)")
                    return {"type": "data_url", "value": src}
                if src.startswith('http'):
                    logger.info(f"[{platform}] QR 从 <img src=远程> 获取 (remote_url)")
                    return {"type": "remote_url", "value": src}
        except Exception as e:
            logger.info(f"[{platform}] img src 读取失败: {e}")

        # 3. 截图兜底(写入文件,API 仍按 file 路径返回)
        logger.info(f"[{platform}] DOM 读源代码失败,截图兜底")
        return None

    async def _capture_qrcode(self, page, platform: str) -> Optional[Path]:
        """截屏登录二维码区域 — 作为 _extract_qrcode_from_dom 失败的兜底。

        主路径已经从 DOM 读源代码(canvas / img src),只有当 DOM 读失败
        时才退回截图。截图保存到 data/qrcodes/qr_<ts>.png。
        """
        pf = get_platform(platform)
        try:
            qrcode_path = Path(__file__).parent.parent / "data" / "qrcodes" / f"qr_{int(time.time())}.png"
            qrcode_path.parent.mkdir(parents=True, exist_ok=True)

            qr_selector = pf.selectors.get("login_qrcode", "")
            if qr_selector:
                try:
                    qr_el = await page.wait_for_selector(qr_selector, timeout=3000)
                    if qr_el:
                        await qr_el.screenshot(path=str(qrcode_path))
                        box = await qr_el.bounding_box()
                        logger.info(
                            f"[{platform}] 二维码元素截图已保存: {qrcode_path} "
                            f"(位置: x={box.x:.0f} y={box.y:.0f} w={box.width:.0f} h={box.height:.0f})"
                        )
                        return qrcode_path
                except Exception:
                    pass

            await page.screenshot(path=str(qrcode_path), full_page=False)
            page_url = page.url
            page_title = await page.title()
            logger.info(
                f"[{platform}] 整页截图兜底: {qrcode_path} "
                f"(URL: {page_url}, title: {page_title})"
            )
            return qrcode_path
        except Exception as e:
            logger.warning(f"[{platform}] 二维码截图失败: {e}")
        return None

    def get_qrcode_path(self, token: str) -> Optional[str]:
        """获取二维码截图路径(DOM 读源代码失败时的截图兜底)"""
        sess = self._active_sessions.get(token)
        return sess.get("qrcode_path") if sess else None

    def get_qrcode_payload(self, token: str) -> Optional[dict]:
        """获取二维码 payload(主路径返回)

        Returns:
            dict: {"type": "data_url" | "remote_url" | "file", "value": str}
            None: 二维码尚未生成
        """
        sess = self._active_sessions.get(token)
        return sess.get("qrcode_payload") if sess else None

    def check_login(self, token: str) -> dict:
        """检查登录状态

        Returns:
            {"status": "launching" / "waiting" / "scanning" / "verifying" / "confirmed" / "expired",
             "username": str | None,
             "platform": str | None,
             "error": str | None}
        """
        sess = self._active_sessions.get(token)
        if not sess:
            return {"status": "expired", "error": "会话不存在或已过期"}

        if sess.get("error"):
            return {"status": "expired", "error": sess["error"]}

        if sess.get("logged_in") and sess.get("persisted"):
            return {
                "status": "confirmed",
                "username": sess.get("username", ""),
                "platform": sess.get("platform", ""),
            }

        # 超时判断（5 分钟）
        if time.time() - sess["started_at"] > 300:
            return {"status": "expired", "error": "登录超时（5分钟未扫码）"}

        elapsed = time.time() - sess["started_at"]
        phase = sess.get("status")
        if phase in {"launching", "waiting", "scanning", "verifying"}:
            return {"status": phase}
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
        """通过请求受保护页面检查 cookie 是否还有效。

        权威判据是服务端探针 `aweme/v1/web/user/profile/self/`
        返回 status_code==0 且有非空 uid/sec_uid；只读主页 URL
        含 "login" 是过时做法，已登录会话也可能停在 SPA 子路由。
        """
        if PlatformType.parse(platform) is not PlatformType.DOUYIN:
            raise ValueError("session_check_unsupported")

        from tiktok_bot_core.browser.client import BrowserClient
        from tiktok_bot_core.browser.providers import (
            DouyinInteractiveLoginProvider,
            extract_douyin_profile_metadata,
            fetch_douyin_avatar_bytes,
        )

        # 在自己的 session 内拷贝需要的字段，避免把 ORM 对象带出去触发
        # DetachedInstanceError。
        with self.db.session() as s:
            acc = self.store.get_tiktok_account_by_username(
                s, username, platform
            )
            if not acc or not acc.cookies_json:
                return False
            account_id = acc.id
            cookies_json = acc.cookies_json

        browser = BrowserClient()
        try:
            await browser.init()
            try:
                cookies = json.loads(cookies_json)
            except json.JSONDecodeError:
                logger.error(f"账号 {username} cookies 格式损坏")
                return False
            if not isinstance(cookies, list):
                return False
            # 必须先导航到目标域再注入；Playwright 在
            # about:blank 下不接受 secure cookie，且探针 fetch
            # 不会被识别为同源请求。
            await browser._page.goto(
                "https://www.douyin.com/",
                wait_until="domcontentloaded",
            )
            await browser._context.add_cookies(cookies)

            probe = DouyinInteractiveLoginProvider._PROFILE_PROBE_URL
            result = await browser._page.evaluate(
                """
                async (url) => {
                    try {
                        const response = await window.fetch(url, {
                            credentials: "include",
                        });
                        if (!response.ok) {
                            return {ok: false, payload: null};
                        }
                        try {
                            return {ok: true, payload: await response.json()};
                        } catch (_error) {
                            return {ok: true, payload: null};
                        }
                    } catch (_error) {
                        return {ok: false, payload: null};
                    }
                }
                """,
                probe,
            )
            if not isinstance(result, Mapping) or not result.get("ok"):
                return False
            payload = result.get("payload")
            if not isinstance(payload, Mapping):
                return False
            if payload.get("status_code") != 0:
                return False
            user = payload.get("user")
            if not isinstance(user, Mapping):
                return False
            valid = any(
                isinstance(user.get(field), str)
                and bool(user.get(field).strip())
                for field in ("uid", "sec_uid")
            )
            if not valid:
                return False

            profile = extract_douyin_profile_metadata(user)
            avatar_bytes = b""
            if profile["avatar_url"]:
                avatar_bytes = await fetch_douyin_avatar_bytes(
                    browser._context,
                    profile["avatar_url"],
                )
            with self.db.session() as session:
                stored = self.store.get_tiktok_account_by_username(
                    session,
                    username,
                    platform,
                )
                if stored is not None:
                    if profile["nickname"]:
                        stored.nickname = profile["nickname"]
                    if profile["avatar_url"]:
                        stored.avatar_url = profile["avatar_url"]
                    follower_count = profile["follower_count"]
                    if follower_count is not None:
                        stored.follower_count = follower_count
                    stored.updated_at = datetime.utcnow()
            if avatar_bytes:
                await asyncio.to_thread(
                    save_account_avatar,
                    self._avatar_data_root(),
                    platform=platform,
                    account_id=account_id,
                    payload=avatar_bytes,
                )
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
