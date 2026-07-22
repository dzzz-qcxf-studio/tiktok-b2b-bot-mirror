"""Phase 2 插件层验证测试

针对插件层基本行为做验证：
1. 插件可注册到 registry
2. Filter 的预筛逻辑正确（不调真实 LLM）
3. BrowserClient 接口可用（在不实际启动浏览器的情况下）
4. 默认插件全部加载成功
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from tiktok_bot_core.models.entities import User
from tiktok_bot_core.plugins.filters.composite_filter import KeywordPreFilter, CompositeFilter
from tiktok_bot_core.plugins.filters.llm_filter import LLMFilter
from tiktok_bot_core.extensions.registry import ExtensionRegistry


@pytest.mark.asyncio
async def test_keyword_pre_filter_hits_business_words():
    """关键词预筛：bio 命中商业关键词 → 视为潜在"""
    f = KeywordPreFilter()
    u = User(tiktok_id="alice", username="alice",
             bio="professional electronics importer from USA")
    r = await f.evaluate(u, {})
    assert r["is_potential"] is True
    assert "importer" in r["reason"]


@pytest.mark.asyncio
async def test_keyword_pre_filter_skips_non_business():
    """关键词预筛：bio 无商业关键词 → 淘汰"""
    f = KeywordPreFilter()
    u = User(tiktok_id="bob", username="bob",
             bio="just love dancing and sharing daily life")
    r = await f.evaluate(u, {})
    assert r["is_potential"] is False
    assert r["score"] == 0.0


@pytest.mark.asyncio
async def test_keyword_pre_filter_handles_chinese():
    """关键词预筛：支持中文"""
    f = KeywordPreFilter()
    u = User(tiktok_id="c", username="c",
             bio="深圳电子元器件批发厂家，承接OEM")
    r = await f.evaluate(u, {})
    assert r["is_potential"] is True


@pytest.mark.asyncio
async def test_composite_filter_pre_screens_unrelated():
    """复合筛选：无关用户被预筛淘汰"""
    cf = CompositeFilter()
    u = User(tiktok_id="d", username="dancer42", bio="just for fun")
    r = await cf.evaluate(u, {})
    assert r["is_potential"] is False
    assert "预筛淘汰" in r["reason"]


@pytest.mark.asyncio
async def test_llm_filter_uses_llm():
    """LLMFilter 调用真实 LLM（用 mock 避免消耗 token）"""
    mock_response = {
        "is_potential": True,
        "confidence": 0.85,
        "category": "buyer",
        "reason": "Clear B2B intent from bio",
    }
    with patch("tiktok_bot_core.plugins.filters.llm_filter.get_llm_client") as m:
        client = MagicMock()
        client.json_completion = AsyncMock(return_value=mock_response)
        m.return_value = client

        f = LLMFilter()
        u = User(tiktok_id="e", username="e", bio="wholesale supplier")
        r = await f.evaluate(u, {})

    assert r["is_potential"] is True
    assert r["score"] == 0.85
    assert r["category"] == "buyer"
    assert client.json_completion.called


def test_register_default_plugins_loads_all():
    """默认插件全部注册"""
    from tiktok_bot_core.plugins import register_default_plugins
    from tiktok_bot_core.extensions.registry import ExtensionRegistry

    reg = ExtensionRegistry()
    reg = register_default_plugins(reg)

    # 3 个 collector
    assert "keyword" in reg.list_plugins()["collectors"]
    assert "recommendation" in reg.list_plugins()["collectors"]
    assert "competitor" in reg.list_plugins()["collectors"]

    # 2 个 channel
    assert "comment" in reg.list_plugins()["channels"]
    assert "dm" in reg.list_plugins()["channels"]

    # 2 个 filter
    assert "llm" in reg.list_plugins()["filters"]
    assert "composite" in reg.list_plugins()["filters"]


def test_channel_plugin_metadata():
    """Channel 插件声明 channel_type"""
    from tiktok_bot_core.plugins.channels.comment_channel import CommentChannel
    from tiktok_bot_core.plugins.channels.dm_channel import DMChannel

    assert CommentChannel.channel_type == "comment"
    assert DMChannel.channel_type == "dm"


@pytest.mark.asyncio
async def test_keyword_collector_with_mock_browser():
    """KeywordCollector 用 mock browser 不实际启动"""
    from tiktok_bot_core.plugins.collectors.keyword_collector import KeywordCollector

    mock_browser = MagicMock()
    mock_browser.navigate = AsyncMock()
    mock_browser.wait = AsyncMock()
    mock_browser.scroll_down = AsyncMock()
    # 模拟返回 2 个链接
    fake_card1 = MagicMock()
    fake_card1.get_attribute = AsyncMock(return_value="https://www.tiktok.com/@alice")
    fake_card2 = MagicMock()
    fake_card2.get_attribute = AsyncMock(return_value="https://www.tiktok.com/@bob")
    mock_browser.query_all = AsyncMock(return_value=[fake_card1, fake_card2])

    with patch("tiktok_bot_core.plugins.collectors.keyword_collector.get_browser",
               new=AsyncMock(return_value=mock_browser)):
        c = KeywordCollector()
        users = await c.collect({"keywords": ["wholesale"], "max_per_keyword": 10})

    assert len(users) == 2
    assert users[0]["username"] == "alice"
    assert users[0]["source"] == "keyword_search"
    assert users[0]["source_keyword"] == "wholesale"
