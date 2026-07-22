"""Phase 1 Core 层验证测试

目标：验证 models / storage / events / extensions / settings / llm 模块基本可用。
不依赖网络/LLM 服务（LLM 测试需要 API key，单独 mock）。
"""

import asyncio
import tempfile
from pathlib import Path

import pytest

from tiktok_bot_core.storage.database import Database
from tiktok_bot_core.storage.sqlite_store import SqliteStore
from tiktok_bot_core.storage.vector_store import VectorStore
from tiktok_bot_core.events.bus import EventBus, Event, EventType
from tiktok_bot_core.extensions.registry import (
    ExtensionRegistry,
    CollectorPlugin,
    ChannelPlugin,
)
from tiktok_bot_core.models.entities import User, DailyReport
from datetime import date


@pytest.fixture
def db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = Path(f.name)
    database = Database(f"sqlite:///{path}")
    database.init()
    yield database
    # Windows 上 SQLite 不立即释放文件句柄，GC 后再删
    database.engine.dispose()
    import gc; gc.collect()
    try:
        path.unlink()
    except PermissionError:
        pass  # Windows 临时文件锁，忽略


def test_db_init_creates_tables(db):
    """数据库初始化应创建所有表"""
    with db.session() as s:
        from sqlalchemy import inspect
        tables = inspect(db.engine).get_table_names()
        for expected in ["users", "strategies", "messages", "replies",
                         "daily_reports", "experience_rules", "config_records"]:
            assert expected in tables, f"缺少表: {expected}"


def test_store_user_crud(db):
    """SqliteStore 用户增删改查"""
    store = SqliteStore()
    with db.session() as s:
        u = store.add_user(s, tiktok_id="alice", username="alice",
                          nickname="Alice", bio="importer", follower_count=1000)
        assert u.id is not None

        u2 = store.get_user(s, u.id)
        assert u2.username == "alice"

        store.update_user_status(s, u.id, "qualified", "buyer")
        u3 = store.get_user(s, u.id)
        assert u3.status == "qualified"
        assert u3.category == "buyer"


def test_store_strategy_message(db):
    """策略和消息的基本流程"""
    store = SqliteStore()
    with db.session() as s:
        u = store.add_user(s, tiktok_id="bob", username="bob")
        st = store.add_strategy(s, user_id=u.id, persona="buyer",
                                comment_template="Great content!", dm_template="Hi!")
        assert st.id is not None

        m = store.add_message(s, user_id=u.id, message_type="comment",
                              content="Great content!", status="sent")
        assert m.id is not None
        assert store.count_messages(s, "comment") == 1


def test_daily_report_upsert(db):
    """日报 UPSERT"""
    store = SqliteStore()
    with db.session() as s:
        r1 = store.upsert_daily_report(s, report_date=date(2026, 6, 8),
                                       new_users_found=10, reply_rate=0.15)
        r2 = store.upsert_daily_report(s, report_date=date(2026, 6, 8),
                                       new_users_found=15, reply_rate=0.20)
        # 应是同一条记录
        assert r1.id == r2.id
        assert r2.new_users_found == 15


def test_keyword_effectiveness(db):
    """关键词效果统计"""
    store = SqliteStore()
    with db.session() as s:
        for i in range(3):
            store.add_user(s, tiktok_id=f"k_a_{i}", username=f"k_a_{i}",
                          source="keyword_search", source_keyword="wholesale",
                          status="contacted")
        for i in range(2):
            store.add_user(s, tiktok_id=f"k_b_{i}", username=f"k_b_{i}",
                          source="keyword_search", source_keyword="import",
                          status="contacted")
        result = store.get_keyword_effectiveness(s)
        assert len(result) == 2
        # wholesale 转化 3/3=100%，import 转化 2/2=100% 也都是 1.0
        # 但排序应稳定
        assert any(r["keyword"] == "wholesale" for r in result)


@pytest.mark.asyncio
async def test_event_bus_basic():
    """事件总线发布订阅"""
    bus = EventBus()
    received = []

    async def handler(e: Event):
        received.append(e)

    bus.subscribe(EventType.USER_QUALIFIED, handler)
    await bus.publish(Event(EventType.USER_QUALIFIED, {"user_id": 1}))
    await bus.publish(Event(EventType.USER_QUALIFIED, {"user_id": 2}))

    assert len(received) == 2
    assert received[0].payload["user_id"] == 1


@pytest.mark.asyncio
async def test_event_bus_handler_error_isolated():
    """一个 handler 抛错不应影响其他 handler"""
    bus = EventBus()
    received = []

    async def bad_handler(e):
        raise ValueError("oops")

    async def good_handler(e):
        received.append(e)

    bus.subscribe(EventType.USER_QUALIFIED, bad_handler)
    bus.subscribe(EventType.USER_QUALIFIED, good_handler)
    await bus.publish(Event(EventType.USER_QUALIFIED))

    assert len(received) == 1


def test_extension_registry():
    """扩展注册器"""
    class MyCollector(CollectorPlugin):
        name = "my_collector"
        async def collect(self, config):
            return []

    reg = ExtensionRegistry()
    c = MyCollector()
    reg.register_collector(c)

    assert reg.get_collector("my_collector") is c
    assert "my_collector" in reg.list_plugins()["collectors"]


def test_settings_defaults():
    """默认配置可用"""
    from tiktok_bot_core.settings import Settings
    s = Settings()
    assert s.llm_provider == "deepseek"
    assert isinstance(s.tiktok_keywords, list)
    assert len(s.tiktok_keywords) > 0


def test_llm_json_extraction():
    """LLM JSON 提取（不调真实 API）"""
    from tiktok_bot_core.llm.client import LLMClient

    # 直接测试 _extract_json 内部方法
    # 1. 纯 JSON
    assert LLMClient._extract_json('{"a": 1}') == {"a": 1}
    # 2. Markdown 包裹
    assert LLMClient._extract_json('```json\n{"b": 2}\n```') == {"b": 2}
    # 3. 混合文本提取
    assert LLMClient._extract_json('结果是 {"c": 3} 谢谢') == {"c": 3}
