"""Phase 3 Pipeline 编排验证测试

PipelineService 测试策略：
1. 不触发真实 browser/LLM — mock 所有外部依赖
2. 使用临时 SQLite（fixture db）
3. 注入 mock Plugin Registry（防止 register_default_plugins 触发网络）
"""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from tiktok_bot_core.storage.database import Database
from tiktok_bot_core.events.bus import EventBus, EventType


@pytest.fixture
def db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = Path(f.name)
    database = Database(f"sqlite:///{path}")
    database.init()
    yield database
    database.engine.dispose()
    import gc; gc.collect()
    try:
        path.unlink()
    except PermissionError:
        pass


def patch_global_db(db):
    from tiktok_bot_core import storage
    storage.database._db_instance = db


def inject_mock_llm():
    mock = MagicMock()
    mock.json_completion = AsyncMock(return_value={
        "is_potential": True, "confidence": 0.9, "category": "buyer",
        "persona": "buyer", "strategy_type": "soft_sell",
        "comment_template": "Great!", "dm_template": "Hi!",
        "priority": 3, "action_plan": "test",
        "top_keywords": ["wholesale"],
        "drop_keywords": [],
        "strategy_suggestions": ["improve outreach timing"],
        "summary": "good week",
    })
    mock.chat = AsyncMock(return_value="{}")
    import tiktok_bot_core.llm.client as llm_mod
    saved = llm_mod._client
    llm_mod._client = mock
    return mock, saved


def restore_llm(saved):
    import tiktok_bot_core.llm.client as llm_mod
    llm_mod._client = saved


# ===== Pipeline tests with full mocking =====


@pytest.mark.asyncio
async def test_pipeline_empty_stages_ok(db):
    patch_global_db(db)
    mock_llm, saved = inject_mock_llm()
    from tiktok_bot_core.extensions.registry import register as get_registry
    reg = get_registry()
    reg.collectors["keyword"] = MagicMock(collect=AsyncMock(return_value=[{"tiktok_id": "x", "username": "x"}]))
    reg.channels["comment"] = MagicMock(execute=AsyncMock(return_value=True))
    reg.channels["dm"] = MagicMock(execute=AsyncMock(return_value=True))

    try:
        from tiktok_bot_core.services.pipeline import PipelineService
        service = PipelineService()
        results = []
        async for r in service.run(stages=[]):
            results.append(r)
        assert results == []
    finally:
        restore_llm(saved)
        reg.collectors.clear()
        reg.channels.clear()


@pytest.mark.asyncio
async def test_pipeline_collect_stage(db):
    patch_global_db(db)
    mock_llm, saved = inject_mock_llm()
    from tiktok_bot_core.extensions.registry import register as get_registry
    reg = get_registry()
    reg.collectors["keyword"] = MagicMock(collect=AsyncMock(return_value=[
        {"tiktok_id": "alice", "username": "alice", "bio": "importer", "follower_count": 1000,
         "source": "keyword_search", "source_keyword": "test"},
        {"tiktok_id": "bob", "username": "bob", "bio": "distributor", "follower_count": 500,
         "source": "keyword_search", "source_keyword": "test"},
    ]))
    reg.channels["comment"] = MagicMock(execute=AsyncMock(return_value=True))
    reg.channels["dm"] = MagicMock(execute=AsyncMock(return_value=True))

    # patch VectorStore 避免 ChromaDB 下载模型
    with patch("tiktok_bot_core.storage.vector_store.VectorStore", autospec=True) as mock_vs:
        try:
            from tiktok_bot_core.services.pipeline import PipelineService, _ensure_registered
            service = PipelineService()
            results = []
            async for r in service.run(stages=["collect"],
                                        collection_config={"keywords": ["test"], "max_per_keyword": 5}):
                results.append(r)
            assert len(results) == 1
            assert results[0]["status"] == "ok"
            assert results[0]["result"]["total"] == 2
        finally:
            restore_llm(saved)
            reg.collectors.clear()
            reg.channels.clear()


@pytest.mark.asyncio
async def test_pipeline_unknown_stage(db):
    patch_global_db(db)
    mock_llm, saved = inject_mock_llm()
    from tiktok_bot_core.extensions.registry import register as get_registry
    reg = get_registry()
    reg.collectors["keyword"] = MagicMock(collect=AsyncMock(return_value=[]))
    reg.channels["comment"] = MagicMock(execute=AsyncMock(return_value=True))
    reg.channels["dm"] = MagicMock(execute=AsyncMock(return_value=True))
    try:
        from tiktok_bot_core.services.pipeline import PipelineService
        service = PipelineService()
        results = []
        async for r in service.run(stages=["unknown_stage"]):
            results.append(r)
        assert len(results) == 1
        assert results[0]["status"] == "error"
    finally:
        restore_llm(saved)
        reg.collectors.clear()
        reg.channels.clear()


@pytest.mark.asyncio
async def test_pipeline_filter_stage(db):
    patch_global_db(db)
    mock_llm, saved = inject_mock_llm()
    from tiktok_bot_core.extensions.registry import register as get_registry
    reg = get_registry()
    reg.collectors["keyword"] = MagicMock(collect=AsyncMock(return_value=[]))
    reg.channels["comment"] = MagicMock(execute=AsyncMock(return_value=True))
    reg.channels["dm"] = MagicMock(execute=AsyncMock(return_value=True))
    try:
        from tiktok_bot_core.services.pipeline import PipelineService
        service = PipelineService()
        with db.session() as s:
            service.store.add_user(s, tiktok_id="a1", username="a1", bio="importer")
            service.store.add_user(s, tiktok_id="a2", username="a2", bio="just for fun")
            service.store.add_user(s, tiktok_id="a3", username="a3", bio="wholesaler factory")

        results = []
        async for r in service.run(stages=["filter"]):
            results.append(r)

        assert len(results) == 1
        r = results[0]["result"]
        assert r["total"] == 3
        assert r["qualified"] == 2
        assert r["rejected"] == 1
    finally:
        restore_llm(saved)
        reg.collectors.clear()
        reg.channels.clear()


@pytest.mark.asyncio
async def test_pipeline_report_stage(db):
    patch_global_db(db)
    mock_llm, saved = inject_mock_llm()
    from tiktok_bot_core.extensions.registry import register as get_registry
    reg = get_registry()
    reg.collectors["keyword"] = MagicMock(collect=AsyncMock(return_value=[]))
    reg.channels["comment"] = MagicMock(execute=AsyncMock(return_value=True))
    reg.channels["dm"] = MagicMock(execute=AsyncMock(return_value=True))
    try:
        from tiktok_bot_core.services.pipeline import PipelineService
        from datetime import date
        service = PipelineService()
        results = []
        async for r in service.run(stages=["report"]):
            results.append(r)

        assert len(results) == 1
        assert results[0]["stage"] == "report"
        with db.session() as s:
            reports = service.store.list_daily_reports(s, days=1)
        assert len(reports) == 1
    finally:
        restore_llm(saved)
        reg.collectors.clear()
        reg.channels.clear()
