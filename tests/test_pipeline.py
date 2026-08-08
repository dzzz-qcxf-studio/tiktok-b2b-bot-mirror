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


def create_run_context(
    db,
    *,
    stages,
    platform="douyin",
    account_id=1,
    account_username="pipeline-account",
):
    from tiktok_bot_core.services.pipeline import PipelineRunContext
    from tiktok_bot_core.storage.pipeline_job_store import PipelineJobStore

    with db.session() as session:
        job = PipelineJobStore().create_job(
            session,
            platform=platform,
            account_mode="specified",
            account_id=None,
            stages=stages,
        )
        job_id = job.id
    browser_session = make_browser_session(platform, account_id)
    return PipelineRunContext(
        job_id=job_id,
        platform=platform,
        account_id=account_id,
        account_username=account_username,
        browser_session=browser_session,
    )


def make_browser_session(platform="douyin", account_id=1, client=None):
    from tiktok_bot_core.browser.providers import BrowserSession

    return BrowserSession(
        platform=platform,
        account_id=account_id,
        client=client or MagicMock(),
    )


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
        context = create_run_context(db, stages=["collect"])
        results = []
        async for r in service.run(stages=[], context=context):
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
            context = create_run_context(db, stages=["collect"])
            results = []
            async for r in service.run(stages=["collect"],
                                        collection_config={"keywords": ["test"], "max_per_keyword": 5},
                                        context=context):
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
        context = create_run_context(db, stages=["collect"])
        results = []
        async for r in service.run(stages=["unknown_stage"], context=context):
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
        context = create_run_context(db, stages=["filter"])
        with db.session() as s:
            users = [
                service.store.add_user(s, tiktok_id="a1", username="a1", bio="importer", platform="douyin"),
                service.store.add_user(s, tiktok_id="a2", username="a2", bio="just for fun", platform="douyin"),
                service.store.add_user(s, tiktok_id="a3", username="a3", bio="wholesaler factory", platform="douyin"),
            ]
            from tiktok_bot_core.storage.pipeline_job_store import PipelineJobStore
            for user in users:
                PipelineJobStore().link_user(s, context.job_id, user.id, "collect")

        results = []
        async for r in service.run(stages=["filter"], context=context):
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
        context = create_run_context(db, stages=["report"])
        results = []
        async for r in service.run(stages=["report"], context=context):
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


@pytest.mark.asyncio
async def test_pipeline_report_uses_utc_day_at_local_midnight(db, monkeypatch):
    """UTC timestamps must not disappear during the local/UTC date gap."""

    from datetime import date as RealDate, datetime as RealDateTime

    import tiktok_bot_core.services.pipeline as pipeline_module
    from tiktok_bot_core.extensions.registry import register as get_registry

    class FrozenUtcDateTime(RealDateTime):
        @classmethod
        def utcnow(cls):
            return cls(2026, 8, 4, 16, 30)

    class FrozenLocalDate(RealDate):
        @classmethod
        def today(cls):
            return cls(2026, 8, 5)

    patch_global_db(db)
    _, saved = inject_mock_llm()
    monkeypatch.setattr(pipeline_module, "datetime", FrozenUtcDateTime)
    monkeypatch.setattr(pipeline_module, "date", FrozenLocalDate)
    reg = get_registry()
    reg.collectors["keyword"] = MagicMock(
        collect=AsyncMock(return_value=[])
    )
    reg.channels["comment"] = MagicMock(
        execute=AsyncMock(return_value=True)
    )
    reg.channels["dm"] = MagicMock(execute=AsyncMock(return_value=True))
    try:
        service = pipeline_module.PipelineService()
        context = create_run_context(db, stages=["report"])

        async for _ in service.run(stages=["report"], context=context):
            pass

        with db.session() as session:
            report_date = service.store.list_daily_reports(
                session, days=1
            )[0].report_date
        assert report_date == RealDate(2026, 8, 4)
    finally:
        restore_llm(saved)
        reg.collectors.clear()
        reg.channels.clear()


@pytest.mark.asyncio
async def test_douyin_job_only_filters_linked_douyin_users(db):
    patch_global_db(db)
    mock_llm, saved = inject_mock_llm()
    from tiktok_bot_core.extensions.registry import register as get_registry
    from tiktok_bot_core.services.pipeline import PipelineRunContext, PipelineService
    from tiktok_bot_core.storage.pipeline_job_store import PipelineJobStore

    reg = get_registry()
    reg.collectors["keyword"] = MagicMock(collect=AsyncMock(return_value=[]))
    reg.channels["comment"] = MagicMock(execute=AsyncMock(return_value=True))
    reg.channels["dm"] = MagicMock(execute=AsyncMock(return_value=True))
    try:
        service = PipelineService()
        jobs = PipelineJobStore()
        with db.session() as session:
            linked_douyin = service.store.add_user(
                session,
                tiktok_id="douyin:linked",
                username="linked",
                platform="douyin",
                bio="电子产品批发商",
            )
            unlinked_douyin = service.store.add_user(
                session,
                tiktok_id="douyin:unlinked",
                username="unlinked",
                platform="douyin",
                bio="电子产品批发商",
            )
            linked_tiktok = service.store.add_user(
                session,
                tiktok_id="tiktok:linked",
                username="linked_tiktok",
                platform="tiktok",
                bio="electronics wholesaler",
            )
            job = jobs.create_job(
                session,
                platform="douyin",
                account_mode="specified",
                account_id=None,
                stages=["filter"],
            )
            jobs.link_user(session, job.id, linked_douyin.id, "collect")
            jobs.link_user(session, job.id, linked_tiktok.id, "collect")
            job_id = job.id
            linked_douyin_id = linked_douyin.id
            unlinked_douyin_id = unlinked_douyin.id
            linked_tiktok_id = linked_tiktok.id

        context = PipelineRunContext(
            job_id=job_id,
            platform="douyin",
            account_id=7,
            account_username="douyin-operator",
            browser_session=make_browser_session("douyin", 7),
        )
        results = [
            item
            async for item in service.run(stages=["filter"], context=context)
        ]

        assert results[0]["result"]["total"] == 1
        with db.session() as session:
            from tiktok_bot_core.models.entities import PipelineJobUser
            linked_state = session.get(
                PipelineJobUser, (job_id, linked_douyin_id)
            )
            tiktok_state = session.get(
                PipelineJobUser, (job_id, linked_tiktok_id)
            )
            assert linked_state.status == "qualified"
            assert tiktok_state.status == "pending"
            assert service.store.get_user(session, linked_douyin_id).status == "pending"
            assert service.store.get_user(session, unlinked_douyin_id).status == "pending"
            assert service.store.get_user(session, linked_tiktok_id).status == "pending"
    finally:
        restore_llm(saved)
        reg.collectors.clear()
        reg.channels.clear()


@pytest.mark.asyncio
async def test_outreach_receives_platform_account_and_browser_session(db):
    patch_global_db(db)
    mock_llm, saved = inject_mock_llm()
    from sqlalchemy import select

    from tiktok_bot_core.extensions.registry import register as get_registry
    from tiktok_bot_core.models.entities import Message
    from tiktok_bot_core.services.pipeline import PipelineRunContext, PipelineService
    from tiktok_bot_core.storage.pipeline_job_store import PipelineJobStore

    reg = get_registry()
    comment = MagicMock(execute=AsyncMock(return_value=True))
    dm = MagicMock(execute=AsyncMock(return_value=True))
    reg.collectors["keyword"] = MagicMock(collect=AsyncMock(return_value=[]))
    reg.channels["comment"] = comment
    reg.channels["dm"] = dm
    browser_session = make_browser_session("douyin", 9)
    try:
        service = PipelineService()
        jobs = PipelineJobStore()
        with db.session() as session:
            user = service.store.add_user(
                session,
                tiktok_id="douyin:outreach",
                username="outreach",
                platform="douyin",
                bio="电子产品批发商",
                status="qualified",
            )
            job = jobs.create_job(
                session,
                platform="douyin",
                account_mode="specified",
                account_id=None,
                stages=["outreach"],
            )
            jobs.link_user(session, job.id, user.id, "collect")
            jobs.update_job_user(
                session,
                job.id,
                user.id,
                status="qualified",
                category="buyer",
            )
            service.store.add_strategy(
                session,
                user_id=user.id,
                job_id=job.id,
                comment_template="欢迎了解",
                dm_template="您好",
                priority=1,
            )
            job_id = job.id

        context = PipelineRunContext(
            job_id=job_id,
            platform="douyin",
            account_id=9,
            account_username="douyin-sales",
            browser_session=browser_session,
        )
        results = [
            item
            async for item in service.run(stages=["outreach"], context=context)
        ]

        assert results[0]["status"] == "ok"
        config = comment.execute.await_args.kwargs["config"]
        assert config == {
            "platform": "douyin",
            "account_id": 9,
            "account": "douyin-sales",
            "browser_session": browser_session,
            "job_id": job_id,
        }
        with db.session() as session:
            messages = list(session.scalars(select(Message)))
            assert len(messages) == 2
            assert {message.message_type for message in messages} == {
                "comment",
                "dm",
            }
            assert all(message.job_id == job_id for message in messages)
    finally:
        restore_llm(saved)
        reg.collectors.clear()
        reg.channels.clear()


@pytest.mark.asyncio
async def test_collect_links_saved_users_to_job(db):
    patch_global_db(db)
    mock_llm, saved = inject_mock_llm()
    from sqlalchemy import select

    from tiktok_bot_core.extensions.registry import register as get_registry
    from tiktok_bot_core.models.entities import PipelineJobUser, User
    from tiktok_bot_core.services.pipeline import PipelineRunContext, PipelineService
    from tiktok_bot_core.storage.pipeline_job_store import PipelineJobStore

    reg = get_registry()
    collector = MagicMock(
        collect=AsyncMock(
            return_value=[
                {
                    "tiktok_id": "douyin:new-user",
                    "username": "new-user",
                    "bio": "批发商",
                    "source": "keyword_search",
                }
            ]
        )
    )
    reg.collectors["keyword"] = collector
    reg.channels["comment"] = MagicMock(execute=AsyncMock(return_value=True))
    reg.channels["dm"] = MagicMock(execute=AsyncMock(return_value=True))
    browser_session = make_browser_session("douyin", 11)
    try:
        service = PipelineService()
        jobs = PipelineJobStore()
        with db.session() as session:
            job = jobs.create_job(
                session,
                platform="douyin",
                account_mode="specified",
                account_id=None,
                stages=["collect"],
            )
            job_id = job.id

        context = PipelineRunContext(
            job_id=job_id,
            platform="douyin",
            account_id=11,
            account_username="douyin-collector",
            browser_session=browser_session,
        )
        results = [
            item
            async for item in service.run(
                stages=["collect"],
                collection_config={"keywords": ["批发商"]},
                context=context,
            )
        ]

        assert results[0]["result"]["saved"] == 1
        collector_config = collector.collect.await_args.args[0]
        assert collector_config["platform"] == "douyin"
        assert collector_config["account"] == "douyin-collector"
        assert collector_config["browser_session"] is browser_session
        with db.session() as session:
            user_id = session.scalar(
                select(User.id).where(User.tiktok_id == "douyin:new-user")
            )
            link = session.get(PipelineJobUser, (job_id, user_id))
            assert link is not None
            assert link.source_stage == "collect"
    finally:
        restore_llm(saved)
        reg.collectors.clear()
        reg.channels.clear()


@pytest.mark.asyncio
async def test_strategy_writes_job_id(db):
    patch_global_db(db)
    mock_llm, saved = inject_mock_llm()
    from sqlalchemy import select

    from tiktok_bot_core.extensions.registry import register as get_registry
    from tiktok_bot_core.models.entities import Strategy
    from tiktok_bot_core.services.pipeline import PipelineRunContext, PipelineService
    from tiktok_bot_core.storage.pipeline_job_store import PipelineJobStore

    reg = get_registry()
    reg.collectors["keyword"] = MagicMock(collect=AsyncMock(return_value=[]))
    reg.channels["comment"] = MagicMock(execute=AsyncMock(return_value=True))
    reg.channels["dm"] = MagicMock(execute=AsyncMock(return_value=True))
    try:
        service = PipelineService()
        jobs = PipelineJobStore()
        with db.session() as session:
            user = service.store.add_user(
                session,
                tiktok_id="douyin:strategy",
                username="strategy",
                platform="douyin",
                bio="电子产品批发商",
                status="qualified",
            )
            job = jobs.create_job(
                session,
                platform="douyin",
                account_mode="specified",
                account_id=None,
                stages=["strategy"],
            )
            jobs.link_user(session, job.id, user.id, "collect")
            jobs.update_job_user(
                session,
                job.id,
                user.id,
                status="qualified",
                category="buyer",
            )
            job_id = job.id

        context = PipelineRunContext(
            job_id=job_id,
            platform="douyin",
            account_id=13,
            account_username="douyin-strategy",
            browser_session=make_browser_session("douyin", 13),
        )
        results = [
            item
            async for item in service.run(stages=["strategy"], context=context)
        ]

        assert results[0]["result"]["strategies"] == 1
        with db.session() as session:
            strategy = session.scalar(select(Strategy))
            assert strategy.job_id == job_id
    finally:
        restore_llm(saved)
        reg.collectors.clear()
        reg.channels.clear()


@pytest.mark.asyncio
async def test_keyword_collector_uses_injected_browser_session():
    from tiktok_bot_core.plugins.collectors.keyword_collector import KeywordCollector

    browser = MagicMock()
    browser.navigate = AsyncMock()
    browser.wait = AsyncMock()
    browser.scroll_down = AsyncMock()
    browser.query_all = AsyncMock(return_value=[])
    browser_session = make_browser_session("douyin", 1, browser)

    users = await KeywordCollector().collect(
        {
            "keywords": ["批发"],
            "platform": "douyin",
            "browser_session": browser_session,
        }
    )

    assert users == []
    browser.navigate.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("module_name", "channel_class"),
    [
        (
            "tiktok_bot_core.plugins.channels.comment_channel",
            "CommentChannel",
        ),
        (
            "tiktok_bot_core.plugins.channels.dm_channel",
            "DMChannel",
        ),
    ],
)
async def test_channel_uses_injected_browser_session(module_name, channel_class):
    import importlib

    module = importlib.import_module(module_name)
    channel_type = getattr(module, channel_class)
    browser = MagicMock()
    browser.navigate = AsyncMock()
    browser.wait = AsyncMock()
    browser.query = AsyncMock(return_value=None)
    browser_session = make_browser_session("douyin", 1, browser)

    result = await channel_type().execute(
        "target-user",
        "hello",
        {
            "platform": "douyin",
            "browser_session": browser_session,
        },
    )

    assert result is False
    browser.navigate.assert_awaited()


@pytest.mark.asyncio
async def test_pipeline_rejects_missing_context(db):
    patch_global_db(db)
    from tiktok_bot_core.services.pipeline import PipelineService

    service = PipelineService()
    with pytest.raises(ValueError, match="PipelineRunContext"):
        async for _ in service.run(stages=["collect"], context=None):
            pass


@pytest.mark.asyncio
async def test_two_jobs_share_user_without_sharing_job_user_state(db):
    patch_global_db(db)
    mock_llm, saved = inject_mock_llm()
    from tiktok_bot_core.extensions.registry import register as get_registry
    from tiktok_bot_core.models.entities import PipelineJobUser
    from tiktok_bot_core.services.pipeline import PipelineService
    from tiktok_bot_core.storage.pipeline_job_store import PipelineJobStore

    reg = get_registry()
    reg.collectors["keyword"] = MagicMock(collect=AsyncMock(return_value=[]))
    reg.channels["comment"] = MagicMock(execute=AsyncMock(return_value=True))
    reg.channels["dm"] = MagicMock(execute=AsyncMock(return_value=True))
    try:
        service = PipelineService()
        first = create_run_context(db, stages=["filter"], account_id=21)
        second = create_run_context(db, stages=["filter"], account_id=22)
        with db.session() as session:
            user = service.store.add_user(
                session,
                tiktok_id="douyin:shared",
                username="shared",
                platform="douyin",
                bio="电子产品批发商",
            )
            PipelineJobStore().link_user(session, first.job_id, user.id, "collect")
            PipelineJobStore().link_user(session, second.job_id, user.id, "collect")
            user_id = user.id

        async for _ in service.run(stages=["filter"], context=first):
            pass

        with db.session() as session:
            first_link = session.get(PipelineJobUser, (first.job_id, user_id))
            second_link = session.get(PipelineJobUser, (second.job_id, user_id))
            assert first_link.status == "qualified"
            assert first_link.category == "buyer"
            assert second_link.status == "pending"
            assert second_link.category == "unknown"
            assert service.store.get_user(session, user_id).status == "pending"
    finally:
        restore_llm(saved)
        reg.collectors.clear()
        reg.channels.clear()


@pytest.mark.asyncio
async def test_report_and_iterate_are_isolated_by_job_and_platform(db):
    patch_global_db(db)
    mock_llm, saved = inject_mock_llm()
    from tiktok_bot_core.extensions.registry import register as get_registry
    from tiktok_bot_core.services.pipeline import PipelineService
    from tiktok_bot_core.storage.pipeline_job_store import PipelineJobStore

    reg = get_registry()
    reg.collectors["keyword"] = MagicMock(collect=AsyncMock(return_value=[]))
    reg.channels["comment"] = MagicMock(execute=AsyncMock(return_value=True))
    reg.channels["dm"] = MagicMock(execute=AsyncMock(return_value=True))
    try:
        service = PipelineService()
        first = create_run_context(
            db,
            stages=["report", "iterate"],
            account_id=31,
        )
        second = create_run_context(
            db,
            stages=["report", "iterate"],
            account_id=32,
        )
        with db.session() as session:
            first_user = service.store.add_user(
                session,
                tiktok_id="douyin:first-report",
                username="first-report",
                platform="douyin",
                source="keyword_search",
                source_keyword="本任务关键词",
            )
            second_user = service.store.add_user(
                session,
                tiktok_id="douyin:second-report",
                username="second-report",
                platform="douyin",
                source="keyword_search",
                source_keyword="其他任务关键词",
            )
            jobs = PipelineJobStore()
            jobs.link_user(session, first.job_id, first_user.id, "collect")
            jobs.link_user(session, second.job_id, second_user.id, "collect")
            jobs.update_job_user(
                session,
                first.job_id,
                first_user.id,
                status="qualified",
                category="buyer",
            )
            jobs.update_job_user(
                session,
                second.job_id,
                second_user.id,
                status="rejected",
                category="unknown",
            )
            first_message = service.store.add_message(
                session,
                job_id=first.job_id,
                user_id=first_user.id,
                message_type="comment",
                content="first",
                status="sent",
            )
            second_message = service.store.add_message(
                session,
                job_id=second.job_id,
                user_id=second_user.id,
                message_type="comment",
                content="second",
                status="sent",
            )
            service.store.add_reply(
                session,
                message_id=first_message.id,
                user_id=first_user.id,
                sentiment="positive",
                is_business_intent=True,
            )
            service.store.add_reply(
                session,
                message_id=second_message.id,
                user_id=second_user.id,
                sentiment="negative",
                is_business_intent=False,
            )

        results = [
            item
            async for item in service.run(
                stages=["report", "iterate"],
                context=first,
            )
        ]

        report = results[0]["result"]
        assert report["new_users"] == 1
        assert report["qualified"] == 1
        assert report["comments"] == 1
        assert report["replies"] == 1
        assert report["reply_rate"] == 1.0
        prompt = mock_llm.json_completion.await_args_list[-1].args[0]
        assert "本任务关键词" in prompt
        assert "其他任务关键词" not in prompt
        assert "抖音" in prompt
        assert "TikTok B2B" not in prompt
    finally:
        restore_llm(saved)
        reg.collectors.clear()
        reg.channels.clear()


@pytest.mark.asyncio
async def test_same_day_job_reports_keep_job_results_and_global_daily_rollup(db):
    patch_global_db(db)
    mock_llm, saved = inject_mock_llm()
    from tiktok_bot_core.extensions.registry import register as get_registry
    from tiktok_bot_core.services.pipeline import PipelineService
    from tiktok_bot_core.storage.pipeline_job_store import PipelineJobStore

    reg = get_registry()
    reg.collectors["keyword"] = MagicMock(collect=AsyncMock(return_value=[]))
    reg.channels["comment"] = MagicMock(execute=AsyncMock(return_value=True))
    reg.channels["dm"] = MagicMock(execute=AsyncMock(return_value=True))
    try:
        service = PipelineService()
        first = create_run_context(
            db, stages=["filter", "report"], account_id=41
        )
        second = create_run_context(
            db, stages=["filter", "report"], account_id=42
        )
        with db.session() as session:
            first_user = service.store.add_user(
                session,
                tiktok_id="douyin:first-daily",
                username="first-daily",
                platform="douyin",
                bio="电子产品进口批发商",
            )
            second_user = service.store.add_user(
                session,
                tiktok_id="douyin:second-daily",
                username="second-daily",
                platform="douyin",
                bio="只是分享日常生活",
            )
            jobs = PipelineJobStore()
            jobs.link_user(session, first.job_id, first_user.id, "collect")
            jobs.link_user(session, second.job_id, second_user.id, "collect")
            service.store.add_message(
                session,
                job_id=first.job_id,
                user_id=first_user.id,
                message_type="comment",
                content="first",
                status="sent",
            )
            service.store.add_message(
                session,
                job_id=first.job_id,
                user_id=first_user.id,
                message_type="dm",
                content="failed",
                status="failed",
            )
            service.store.add_message(
                session,
                job_id=second.job_id,
                user_id=second_user.id,
                message_type="dm",
                content="second",
                status="sent",
            )
            service.store.add_message(
                session,
                job_id=second.job_id,
                user_id=second_user.id,
                message_type="comment",
                content="uncertain",
                status="uncertain",
            )

        async for _ in service.run(stages=["filter"], context=first):
            pass
        async for _ in service.run(stages=["filter"], context=second):
            pass

        first_result = [
            item
            async for item in service.run(stages=["report"], context=first)
        ][0]["result"]
        second_result = [
            item
            async for item in service.run(stages=["report"], context=second)
        ][0]["result"]

        assert first_result["new_users"] == 1
        assert first_result["qualified"] == 1
        assert first_result["comments"] == 1
        assert first_result["dms"] == 0
        assert second_result["new_users"] == 1
        assert second_result["qualified"] == 0
        assert second_result["comments"] == 0
        assert second_result["dms"] == 1
        with db.session() as session:
            daily = service.store.list_daily_reports(session, days=1)[0]
            assert daily.new_users_found == 2
            assert daily.users_qualified == 1
            assert daily.users_rejected == 1
            assert daily.comments_sent == 1
            assert daily.dms_sent == 1
    finally:
        restore_llm(saved)
        reg.collectors.clear()
        reg.channels.clear()


@pytest.mark.asyncio
async def test_collect_rejects_cross_platform_results(db):
    patch_global_db(db)
    from sqlalchemy import select

    from tiktok_bot_core.extensions.registry import register as get_registry
    from tiktok_bot_core.models.entities import User
    from tiktok_bot_core.services.pipeline import PipelineService

    reg = get_registry()
    reg.collectors["keyword"] = MagicMock(
        collect=AsyncMock(
            return_value=[
                {
                    "tiktok_id": "tiktok:wrong",
                    "username": "wrong",
                    "platform": "tiktok",
                }
            ]
        )
    )
    reg.channels["comment"] = MagicMock(execute=AsyncMock(return_value=True))
    reg.channels["dm"] = MagicMock(execute=AsyncMock(return_value=True))
    try:
        service = PipelineService()
        context = create_run_context(db, stages=["collect"])
        results = [
            item
            async for item in service.run(stages=["collect"], context=context)
        ]
        assert results[0]["status"] == "error"
        with db.session() as session:
            assert list(session.scalars(select(User))) == []
    finally:
        reg.collectors.clear()
        reg.channels.clear()


@pytest.mark.asyncio
async def test_collect_normalizes_compound_user_id_to_context_platform(db):
    patch_global_db(db)
    from sqlalchemy import select

    from tiktok_bot_core.extensions.registry import register as get_registry
    from tiktok_bot_core.models.entities import User
    from tiktok_bot_core.services.pipeline import PipelineService

    reg = get_registry()
    reg.collectors["keyword"] = MagicMock(
        collect=AsyncMock(
            return_value=[
                {
                    "tiktok_id": "raw-provider-id",
                    "username": "normalized-user",
                }
            ]
        )
    )
    reg.channels["comment"] = MagicMock(execute=AsyncMock(return_value=True))
    reg.channels["dm"] = MagicMock(execute=AsyncMock(return_value=True))
    try:
        service = PipelineService()
        context = create_run_context(db, stages=["collect"])
        async for _ in service.run(stages=["collect"], context=context):
            pass
        with db.session() as session:
            user = session.scalar(select(User))
            assert user.tiktok_id == "douyin:raw-provider-id"
            assert user.platform == "douyin"
    finally:
        reg.collectors.clear()
        reg.channels.clear()


@pytest.mark.asyncio
async def test_strategy_retry_is_idempotent(db):
    patch_global_db(db)
    mock_llm, saved = inject_mock_llm()
    from sqlalchemy import func, select

    from tiktok_bot_core.extensions.registry import register as get_registry
    from tiktok_bot_core.models.entities import Strategy
    from tiktok_bot_core.services.pipeline import PipelineService
    from tiktok_bot_core.storage.pipeline_job_store import PipelineJobStore

    reg = get_registry()
    reg.collectors["keyword"] = MagicMock(collect=AsyncMock(return_value=[]))
    reg.channels["comment"] = MagicMock(execute=AsyncMock(return_value=True))
    reg.channels["dm"] = MagicMock(execute=AsyncMock(return_value=True))
    try:
        service = PipelineService()
        context = create_run_context(db, stages=["strategy"])
        with db.session() as session:
            user = service.store.add_user(
                session,
                tiktok_id="douyin:idempotent-strategy",
                username="idempotent-strategy",
                platform="douyin",
            )
            jobs = PipelineJobStore()
            jobs.link_user(session, context.job_id, user.id, "collect")
            jobs.update_job_user(
                session,
                context.job_id,
                user.id,
                status="qualified",
                category="buyer",
            )

        for _ in range(2):
            async for _ in service.run(stages=["strategy"], context=context):
                pass

        with db.session() as session:
            assert session.scalar(select(func.count(Strategy.id))) == 1
    finally:
        restore_llm(saved)
        reg.collectors.clear()
        reg.channels.clear()


@pytest.mark.asyncio
async def test_outreach_retry_is_at_most_once(db):
    patch_global_db(db)
    from sqlalchemy import func, select

    from tiktok_bot_core.extensions.registry import register as get_registry
    from tiktok_bot_core.models.entities import Message
    from tiktok_bot_core.services.pipeline import PipelineService
    from tiktok_bot_core.storage.pipeline_job_store import PipelineJobStore

    reg = get_registry()
    comment = MagicMock(execute=AsyncMock(return_value=True))
    reg.collectors["keyword"] = MagicMock(collect=AsyncMock(return_value=[]))
    reg.channels["comment"] = comment
    reg.channels["dm"] = MagicMock(execute=AsyncMock(return_value=True))
    try:
        service = PipelineService()
        context = create_run_context(db, stages=["outreach"])
        with db.session() as session:
            user = service.store.add_user(
                session,
                tiktok_id="douyin:idempotent-outreach",
                username="idempotent-outreach",
                platform="douyin",
            )
            jobs = PipelineJobStore()
            jobs.link_user(session, context.job_id, user.id, "collect")
            jobs.update_job_user(
                session,
                context.job_id,
                user.id,
                status="qualified",
                category="buyer",
            )
            service.store.add_strategy(
                session,
                job_id=context.job_id,
                user_id=user.id,
                comment_template="only once",
            )

        for _ in range(2):
            async for _ in service.run(
                stages=["outreach"],
                outreach_config={"comment_limit": 1, "dm_limit": 0},
                context=context,
            ):
                pass

        assert comment.execute.await_count == 1
        with db.session() as session:
            assert session.scalar(select(func.count(Message.id))) == 1
            message = session.scalar(select(Message))
            assert message.status == "sent"
    finally:
        reg.collectors.clear()
        reg.channels.clear()


@pytest.mark.asyncio
async def test_outreach_uses_fresh_job_state_for_dm_after_comment(db):
    patch_global_db(db)
    from tiktok_bot_core.extensions.registry import register as get_registry
    from tiktok_bot_core.services.pipeline import PipelineService
    from tiktok_bot_core.storage.pipeline_job_store import PipelineJobStore

    reg = get_registry()
    comment = MagicMock(execute=AsyncMock(return_value=True))
    dm = MagicMock(execute=AsyncMock(return_value=True))
    reg.collectors["keyword"] = MagicMock(collect=AsyncMock(return_value=[]))
    reg.channels["comment"] = comment
    reg.channels["dm"] = dm
    try:
        service = PipelineService()
        context = create_run_context(db, stages=["outreach"])
        with db.session() as session:
            user = service.store.add_user(
                session,
                tiktok_id="douyin:fresh-outreach-state",
                username="fresh-outreach-state",
                platform="douyin",
            )
            jobs = PipelineJobStore()
            jobs.link_user(session, context.job_id, user.id, "collect")
            jobs.update_job_user(
                session,
                context.job_id,
                user.id,
                status="qualified",
                category="buyer",
            )
            service.store.add_strategy(
                session,
                job_id=context.job_id,
                user_id=user.id,
                comment_template="comment",
                dm_template="dm",
            )

        async for _ in service.run(
            stages=["outreach"],
            outreach_config={"comment_limit": 1, "dm_limit": 1},
            context=context,
        ):
            pass

        comment.execute.assert_awaited_once()
        dm.execute.assert_awaited_once()
    finally:
        reg.collectors.clear()
        reg.channels.clear()


@pytest.mark.asyncio
async def test_outreach_retry_advances_past_already_reserved_users(db):
    patch_global_db(db)
    from sqlalchemy import func, select

    from tiktok_bot_core.extensions.registry import register as get_registry
    from tiktok_bot_core.models.entities import Message
    from tiktok_bot_core.services.pipeline import PipelineService
    from tiktok_bot_core.storage.pipeline_job_store import PipelineJobStore

    reg = get_registry()
    comment = MagicMock(execute=AsyncMock(return_value=True))
    reg.collectors["keyword"] = MagicMock(collect=AsyncMock(return_value=[]))
    reg.channels["comment"] = comment
    reg.channels["dm"] = MagicMock(execute=AsyncMock(return_value=True))
    try:
        service = PipelineService()
        context = create_run_context(db, stages=["outreach"])
        with db.session() as session:
            jobs = PipelineJobStore()
            for index in range(2):
                user = service.store.add_user(
                    session,
                    tiktok_id=f"douyin:advance-{index}",
                    username=f"advance-{index}",
                    platform="douyin",
                )
                jobs.link_user(session, context.job_id, user.id, "collect")
                jobs.update_job_user(
                    session,
                    context.job_id,
                    user.id,
                    status="qualified",
                    category="buyer",
                )
                service.store.add_strategy(
                    session,
                    job_id=context.job_id,
                    user_id=user.id,
                    comment_template=f"comment-{index}",
                    priority=index + 1,
                )

        for _ in range(2):
            async for _ in service.run(
                stages=["outreach"],
                outreach_config={"comment_limit": 1, "dm_limit": 0},
                context=context,
            ):
                pass

        assert comment.execute.await_count == 2
        with db.session() as session:
            assert session.scalar(select(func.count(Message.id))) == 2
    finally:
        reg.collectors.clear()
        reg.channels.clear()


@pytest.mark.asyncio
async def test_outreach_channel_exception_becomes_uncertain_without_retry(db):
    patch_global_db(db)
    from sqlalchemy import select

    from tiktok_bot_core.extensions.registry import register as get_registry
    from tiktok_bot_core.models.entities import Message
    from tiktok_bot_core.services.pipeline import PipelineService
    from tiktok_bot_core.storage.pipeline_job_store import PipelineJobStore

    reg = get_registry()
    comment = MagicMock(
        execute=AsyncMock(side_effect=RuntimeError("transport lost"))
    )
    reg.collectors["keyword"] = MagicMock(collect=AsyncMock(return_value=[]))
    reg.channels["comment"] = comment
    reg.channels["dm"] = MagicMock(execute=AsyncMock(return_value=True))
    try:
        service = PipelineService()
        context = create_run_context(db, stages=["outreach"])
        with db.session() as session:
            user = service.store.add_user(
                session,
                tiktok_id="douyin:uncertain",
                username="uncertain",
                platform="douyin",
            )
            jobs = PipelineJobStore()
            jobs.link_user(session, context.job_id, user.id, "collect")
            jobs.update_job_user(
                session,
                context.job_id,
                user.id,
                status="qualified",
                category="buyer",
            )
            service.store.add_strategy(
                session,
                job_id=context.job_id,
                user_id=user.id,
                comment_template="maybe sent",
            )

        first = [
            item
            async for item in service.run(
                stages=["outreach"],
                outreach_config={"comment_limit": 1, "dm_limit": 0},
                context=context,
            )
        ][0]
        comment.execute.side_effect = None
        comment.execute.return_value = True
        async for _ in service.run(
            stages=["outreach"],
            outreach_config={"comment_limit": 1, "dm_limit": 0},
            context=context,
        ):
            pass

        assert first["status"] == "ok"
        assert first["result"]["errors"] == 1
        assert comment.execute.await_count == 1
        with db.session() as session:
            message = session.scalar(select(Message))
            assert message.status == "uncertain"
            assert "transport lost" in message.error_msg
    finally:
        reg.collectors.clear()
        reg.channels.clear()


@pytest.mark.asyncio
async def test_iterate_persists_platform_and_job_dimensions(db):
    patch_global_db(db)
    mock_llm, saved = inject_mock_llm()
    from sqlalchemy import select

    from tiktok_bot_core.extensions.registry import register as get_registry
    from tiktok_bot_core.models.entities import ExperienceRule
    from tiktok_bot_core.services.pipeline import PipelineService

    reg = get_registry()
    reg.collectors["keyword"] = MagicMock(collect=AsyncMock(return_value=[]))
    reg.channels["comment"] = MagicMock(execute=AsyncMock(return_value=True))
    reg.channels["dm"] = MagicMock(execute=AsyncMock(return_value=True))
    try:
        service = PipelineService()
        service.vector = MagicMock()
        context = create_run_context(db, stages=["iterate"])

        async for _ in service.run(stages=["iterate"], context=context):
            pass

        with db.session() as session:
            rule = session.scalar(select(ExperienceRule))
            assert rule.platform == "douyin"
            assert rule.job_id == context.job_id
        metadata = service.vector.add_experience.call_args.kwargs["metadata"]
        assert metadata["platform"] == "douyin"
        assert metadata["job_id"] == context.job_id
    finally:
        restore_llm(saved)
        reg.collectors.clear()
        reg.channels.clear()


@pytest.mark.asyncio
async def test_iterate_llm_failure_marks_stage_error(db):
    patch_global_db(db)
    mock_llm, saved = inject_mock_llm()
    mock_llm.json_completion.side_effect = RuntimeError("llm unavailable")
    from tiktok_bot_core.extensions.registry import register as get_registry
    from tiktok_bot_core.services.pipeline import PipelineService

    reg = get_registry()
    reg.collectors["keyword"] = MagicMock(collect=AsyncMock(return_value=[]))
    reg.channels["comment"] = MagicMock(execute=AsyncMock(return_value=True))
    reg.channels["dm"] = MagicMock(execute=AsyncMock(return_value=True))
    try:
        service = PipelineService()
        context = create_run_context(db, stages=["iterate"])
        result = [
            item
            async for item in service.run(stages=["iterate"], context=context)
        ][0]
        assert result["status"] == "error"
        assert "llm unavailable" in result["result"]["error"]
    finally:
        restore_llm(saved)
        reg.collectors.clear()
        reg.channels.clear()


@pytest.mark.asyncio
async def test_pipeline_preflight_rejects_missing_required_plugin(db):
    patch_global_db(db)
    from tiktok_bot_core.extensions.registry import register as get_registry
    from tiktok_bot_core.services.pipeline import PipelineService

    reg = get_registry()
    reg.collectors["other"] = MagicMock()
    reg.collectors.pop("keyword", None)
    reg.channels["comment"] = MagicMock(execute=AsyncMock(return_value=True))
    reg.channels["dm"] = MagicMock(execute=AsyncMock(return_value=True))
    try:
        service = PipelineService()
        context = create_run_context(db, stages=["collect"])
        with pytest.raises(RuntimeError, match="keyword collector"):
            async for _ in service.run(stages=["collect"], context=context):
                pass
    finally:
        reg.collectors.clear()
        reg.channels.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("missing", "match"),
    [("comment", "comment channel"), ("dm", "dm channel")],
)
async def test_pipeline_preflight_rejects_missing_channel(db, missing, match):
    patch_global_db(db)
    from tiktok_bot_core.extensions.registry import register as get_registry
    from tiktok_bot_core.services.pipeline import PipelineService

    reg = get_registry()
    reg.collectors["keyword"] = MagicMock(collect=AsyncMock(return_value=[]))
    reg.channels["comment"] = MagicMock(execute=AsyncMock(return_value=True))
    reg.channels["dm"] = MagicMock(execute=AsyncMock(return_value=True))
    reg.channels.pop(missing)
    try:
        service = PipelineService()
        context = create_run_context(db, stages=["outreach"])
        with pytest.raises(RuntimeError, match=match):
            async for _ in service.run(stages=["outreach"], context=context):
                pass
    finally:
        reg.collectors.clear()
        reg.channels.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutate,match",
    [
        (
            lambda context: object.__setattr__(
                context, "browser_session", object()
            ),
            "invalid",
        ),
        (lambda context: setattr(context.browser_session, "_released", True), "released"),
        (lambda context: setattr(context.browser_session, "client", None), "client"),
        (lambda context: setattr(context.browser_session, "platform", "tiktok"), "platform"),
        (lambda context: setattr(context.browser_session, "account_id", 999), "account"),
    ],
)
async def test_pipeline_rejects_invalid_browser_session(db, mutate, match):
    patch_global_db(db)
    from tiktok_bot_core.services.pipeline import PipelineService

    service = PipelineService()
    context = create_run_context(db, stages=["report"])
    mutate(context)
    with pytest.raises(ValueError, match=match):
        async for _ in service.run(stages=["report"], context=context):
            pass


@pytest.mark.asyncio
async def test_plugins_fail_closed_without_browser_session():
    from tiktok_bot_core.plugins.channels.comment_channel import CommentChannel
    from tiktok_bot_core.plugins.channels.dm_channel import DMChannel
    from tiktok_bot_core.plugins.collectors.keyword_collector import KeywordCollector

    with pytest.raises(ValueError, match="browser_session"):
        await KeywordCollector().collect(
            {"keywords": ["批发"], "platform": "douyin"}
        )
    with pytest.raises(ValueError, match="browser_session"):
        await CommentChannel().execute(
            "target", "content", {"platform": "douyin"}
        )
    with pytest.raises(ValueError, match="browser_session"):
        await DMChannel().execute(
            "target", "content", {"platform": "douyin"}
        )


@pytest.mark.asyncio
async def test_comment_cleanup_error_does_not_override_success():
    from tiktok_bot_core.plugins.channels.comment_channel import CommentChannel

    browser = MagicMock()
    browser.navigate = AsyncMock(
        side_effect=[None, None, RuntimeError("cleanup failed")]
    )
    browser.wait = AsyncMock()
    video_link = MagicMock()
    video_link.get_attribute = AsyncMock(return_value="https://douyin/video/1")
    comment_input = MagicMock()
    comment_input.click = AsyncMock()
    comment_input.fill = AsyncMock()
    send_button = MagicMock()
    send_button.click = AsyncMock()
    browser.query = AsyncMock(
        side_effect=[video_link, comment_input, send_button]
    )
    session = make_browser_session("douyin", 1, browser)

    with patch(
        "tiktok_bot_core.plugins.channels.comment_channel.random.random",
        return_value=0.1,
    ):
        result = await CommentChannel().execute(
            "target",
            "content",
            {"platform": "douyin", "browser_session": session},
        )

    assert result is True
