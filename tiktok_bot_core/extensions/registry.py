"""扩展点注册器

借鉴 ChopperBot 的插件化思想，但用 Python 的 dataclass + 字典实现：
- CollectorPlugin: 用户搜集（关键词/推荐/竞品分析）
- ChannelPlugin: 触达渠道（comment/dm/follow）
- FilterPlugin: 筛选维度（关键词/国家/粉丝数）
- StoragePlugin: 存储后端（sqlite/postgres/chroma）
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, ClassVar

from tiktok_bot_core.models.entities import User, Strategy, Message

logger = logging.getLogger(__name__)


class CollectorPlugin(ABC):
    """用户搜集插件基类"""

    name: ClassVar[str] = ""

    @abstractmethod
    async def collect(self, config: dict) -> list[dict]:
        """搜集用户，返回用户字典列表（待入库）"""


class ChannelPlugin(ABC):
    """触达渠道插件基类（评论/私信/关注）"""

    name: ClassVar[str] = ""
    channel_type: ClassVar[str] = ""  # "comment" / "dm" / "follow"

    @abstractmethod
    async def execute(self, target: str, content: str, config: dict) -> bool:
        """执行触达，返回是否成功"""


class FilterPlugin(ABC):
    """筛选维度插件基类"""

    name: ClassVar[str] = ""

    @abstractmethod
    async def evaluate(self, user: User, config: dict) -> dict:
        """评估用户，返回 {score: 0-1, reason: str, extra: dict}"""


class StoragePlugin(ABC):
    """存储后端插件基类"""

    name: ClassVar[str] = ""

    @abstractmethod
    def save(self, data: Any) -> bool: ...
    @abstractmethod
    def query(self, filters: dict) -> list: ...


@dataclass
class ExtensionRegistry:
    """全局扩展注册表"""

    collectors: dict[str, CollectorPlugin] = field(default_factory=dict)
    channels: dict[str, ChannelPlugin] = field(default_factory=dict)
    filters: dict[str, FilterPlugin] = field(default_factory=dict)
    storages: dict[str, StoragePlugin] = field(default_factory=dict)

    def register_collector(self, plugin: CollectorPlugin) -> None:
        self.collectors[plugin.name] = plugin
        logger.info(f"注册 Collector: {plugin.name}")

    def register_channel(self, plugin: ChannelPlugin) -> None:
        self.channels[plugin.name] = plugin
        logger.info(f"注册 Channel: {plugin.name} ({plugin.channel_type})")

    def register_filter(self, plugin: FilterPlugin) -> None:
        self.filters[plugin.name] = plugin
        logger.info(f"注册 Filter: {plugin.name}")

    def register_storage(self, plugin: StoragePlugin) -> None:
        self.storages[plugin.name] = plugin
        logger.info(f"注册 Storage: {plugin.name}")

    def get_collector(self, name: str) -> CollectorPlugin | None:
        return self.collectors.get(name)

    def get_channel(self, name: str) -> ChannelPlugin | None:
        return self.channels.get(name)

    def list_plugins(self) -> dict[str, list[str]]:
        return {
            "collectors": list(self.collectors.keys()),
            "channels": list(self.channels.keys()),
            "filters": list(self.filters.keys()),
            "storages": list(self.storages.keys()),
        }


# 全局注册表
_registry: ExtensionRegistry | None = None


def register(registry: ExtensionRegistry | None = None) -> ExtensionRegistry:
    """获取全局注册表"""
    global _registry
    if _registry is None:
        _registry = ExtensionRegistry()
    if registry is not None:
        _registry = registry
    return _registry
