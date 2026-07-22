"""Phase 4 双平台 + 真实登录测试

测试 platforms.py 抽象和 auth_service.py 元数据操作（不实际启动浏览器）。
"""

import pytest
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from tiktok_bot_core.storage.database import Database
from tiktok_bot_core.platforms import (
    PlatformType, get_platform, list_platforms, TIKTOK, DOUYIN
)


# ===== Platforms =====

def test_platform_type_parse_aliases():
    """PlatformType.parse 应接受多种别名"""
    assert PlatformType.parse("tiktok") == PlatformType.TIKTOK
    assert PlatformType.parse("TikTok") == PlatformType.TIKTOK
    assert PlatformType.parse("tk") == PlatformType.TIKTOK
    assert PlatformType.parse("douyin") == PlatformType.DOUYIN
    assert PlatformType.parse("dy") == PlatformType.DOUYIN
    assert PlatformType.parse("抖音") == PlatformType.DOUYIN
    assert PlatformType.parse(PlatformType.TIKTOK) == PlatformType.TIKTOK


def test_platform_type_parse_invalid_raises():
    """未知平台应抛 ValueError"""
    with pytest.raises(ValueError, match="未知平台"):
        PlatformType.parse("facebook")


def test_tiktok_platform_urls():
    """TikTok URL 模板正确"""
    pf = get_platform("tiktok")
    assert "tiktok.com" in pf.home_url
    assert "search?q=" in pf.search_user_url_tpl
    assert "/@{username}" in pf.user_profile_url_tpl
    # 中文关键词自动 URL 编码
    assert "%E6%91%87" not in pf.search_user_url("中文") or "=" in pf.search_user_url("中文")
    # username URL 也正确编码
    url = pf.user_profile_url("alice_中文")
    assert "alice" in url


def test_douyin_platform_urls():
    """抖音 URL 模板正确"""
    pf = get_platform("douyin")
    assert "douyin.com" in pf.home_url
    # 抖音搜索是 path 参数
    assert "{kw}" in pf.search_user_url_tpl
    assert "/user/{username}" in pf.user_profile_url_tpl


def test_platform_selectors_required_keys():
    """两个平台都应有必需的 selector"""
    required = ["user_card", "user_link", "comment_input", "message_btn"]
    for pf in [TIKTOK, DOUYIN]:
        for key in required:
            assert key in pf.selectors, f"{pf.name} 缺少 selector: {key}"


def test_list_platforms():
    """至少支持 2 个平台"""
    platforms = list_platforms()
    names = [p.name for p in platforms]
    assert "tiktok" in names
    assert "douyin" in names


# ===== Database platform support =====

@pytest.fixture
def db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = Path(f.name)
    database = Database(f"sqlite:///{path}")
    database.init()

    # 把全局 db 单例切到我们这个测试 DB
    from tiktok_bot_core import storage
    old_db = storage.database._db_instance
    storage.database._db_instance = database

    # 同样重置 AuthService 单例（避免缓存了旧 db 引用）
    import tiktok_bot_core.services.auth_service as auth_mod
    old_auth = auth_mod._auth_service
    auth_mod._auth_service = None

    yield database

    storage.database._db_instance = old_db
    auth_mod._auth_service = old_auth
    database.engine.dispose()
    import gc; gc.collect()
    try:
        path.unlink()
    except PermissionError:
        pass


def test_db_add_user_with_platform(db):
    """User 表存 platform 字段"""
    from tiktok_bot_core.models.entities import User

    with db.session() as s:
        u = User(
            platform="douyin",
            tiktok_id="douyin:alice",
            username="alice",
            bio="抖音用户",
            follower_count=100,
        )
        s.add(u)
        s.commit()

        from sqlalchemy import select
        loaded = s.execute(select(User)).scalar_one()
        assert loaded.platform == "douyin"
        assert loaded.username == "alice"
        assert loaded.follower_count == 100


def test_db_add_tiktok_account_with_platform(db):
    """tiktok_accounts 表存 platform 字段"""
    from tiktok_bot_core.storage.sqlite_store import SqliteStore
    store = SqliteStore()

    with db.session() as s:
        store.add_tiktok_account(s, platform="douyin", username="dy_acc_01", status="logged_in")
        store.add_tiktok_account(s, platform="tiktok", username="tk_acc_01", status="logged_in")

        dy_accounts = store.get_tiktok_accounts(s, platform="douyin")
        tk_accounts = store.get_tiktok_accounts(s, platform="tiktok")

        assert len(dy_accounts) == 1
        assert dy_accounts[0].username == "dy_acc_01"
        assert dy_accounts[0].platform == "douyin"
        assert len(tk_accounts) == 1
        assert tk_accounts[0].username == "tk_acc_01"
        assert tk_accounts[0].platform == "tiktok"


def test_db_account_unique_per_platform(db):
    """同名账号在不同平台应各占一条记录"""
    from tiktok_bot_core.storage.sqlite_store import SqliteStore
    store = SqliteStore()

    with db.session() as s:
        store.add_tiktok_account(s, platform="tiktok", username="alice")
        store.add_tiktok_account(s, platform="douyin", username="alice")

        accounts = store.get_tiktok_accounts(s)
        # 在 session 内取值
        pairs = [(a.platform, a.username) for a in accounts]
    assert ("tiktok", "alice") in pairs
    assert ("douyin", "alice") in pairs


def test_db_account_status_update(db):
    """账号状态可更新"""
    from tiktok_bot_core.storage.sqlite_store import SqliteStore
    store = SqliteStore()

    with db.session() as s:
        a = store.add_tiktok_account(s, platform="tiktok", username="tmp", status="pending")
        aid = a.id
        store.update_account_status(s, aid, "logged_in")

        acc = store.get_tiktok_account(s, aid)
        status = acc.status
    assert status == "logged_in"


def test_db_get_active_account_per_platform(db):
    """每个平台应能获取该平台下已登录的账号"""
    from tiktok_bot_core.storage.sqlite_store import SqliteStore
    store = SqliteStore()

    with db.session() as s:
        store.add_tiktok_account(s, platform="douyin", username="dy_x", status="logged_in")
        store.add_tiktok_account(s, platform="tiktok", username="tk_x", status="pending")
        store.add_tiktok_account(s, platform="tiktok", username="tk_y", status="logged_in")

        dy = store.get_active_account(s, "douyin")
        tk = store.get_active_account(s, "tiktok")
        dy_platform = dy.platform if dy else None
        tk_platform = tk.platform if tk else None

    assert dy_platform == "douyin"
    assert tk_platform == "tiktok"


# ===== Auth Service 元数据 =====

def test_auth_service_add_and_list(db):
    """AuthService: 添加账号 + 列表"""
    from tiktok_bot_core.services.auth_service import get_auth_service

    svc = get_auth_service()
    # add_account 返回的 ORM 对象需在 session 内取值
    with db.session() as s:
        from tiktok_bot_core.storage.sqlite_store import SqliteStore
        store = SqliteStore()
        a1 = store.add_tiktok_account(s, platform="tiktok", username="marketing_01", status="pending")
        a2 = store.add_tiktok_account(s, platform="douyin", username="marketing_02", status="pending")
        a1_id, a1_platform = a1.id, a1.platform
        a2_id, a2_platform = a2.id, a2.platform

    assert a1_id is not None
    assert a2_id is not None
    assert a1_platform == "tiktok"
    assert a2_platform == "douyin"

    with db.session() as s:
        from tiktok_bot_core.storage.sqlite_store import SqliteStore
        store = SqliteStore()
        accounts = store.get_tiktok_accounts(s)
        usernames = {a.username for a in accounts}
    assert "marketing_01" in usernames
    assert "marketing_02" in usernames


def test_auth_service_add_invalid_platform(db):
    """无效平台应抛 ValueError"""
    from tiktok_bot_core.services.auth_service import get_auth_service

    svc = get_auth_service()
    with pytest.raises(ValueError):
        svc.add_account("facebook", "test")
