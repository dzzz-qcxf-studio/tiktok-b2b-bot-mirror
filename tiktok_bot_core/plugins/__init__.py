"""插件层 — Collector / Channel / Filter 三类插件实现"""

# 注册中心
from tiktok_bot_core.extensions.registry import (
    CollectorPlugin,
    ChannelPlugin,
    FilterPlugin,
    ExtensionRegistry,
    register,
)

# 浏览器
from tiktok_bot_core.browser.client import BrowserClient, get_browser, close_browser

# 三个具体 Collector
from .collectors.keyword_collector import KeywordCollector
from .collectors.recommendation_collector import RecommendationCollector
from .collectors.competitor_collector import CompetitorCollector

# 两个具体 Channel
from .channels.comment_channel import CommentChannel
from .channels.dm_channel import DMChannel

# 两个具体 Filter
from .filters.llm_filter import LLMFilter
from .filters.composite_filter import CompositeFilter


def register_default_plugins(reg: ExtensionRegistry | None = None) -> ExtensionRegistry:
    """注册所有内置插件"""
    if reg is None:
        reg = register()

    # Collectors
    reg.register_collector(KeywordCollector())
    reg.register_collector(RecommendationCollector())
    reg.register_collector(CompetitorCollector())

    # Channels
    reg.register_channel(CommentChannel())
    reg.register_channel(DMChannel())

    # Filters
    reg.register_filter(LLMFilter())
    reg.register_filter(CompositeFilter())

    logger = __import__("logging").getLogger(__name__)
    logger.info(f"已注册默认插件: {reg.list_plugins()}")
    return reg


__all__ = [
    "CollectorPlugin",
    "ChannelPlugin",
    "FilterPlugin",
    "ExtensionRegistry",
    "register",
    "register_default_plugins",
    "BrowserClient",
    "get_browser",
    "close_browser",
    # 具体插件
    "KeywordCollector",
    "RecommendationCollector",
    "CompetitorCollector",
    "CommentChannel",
    "DMChannel",
    "LLMFilter",
    "CompositeFilter",
]
