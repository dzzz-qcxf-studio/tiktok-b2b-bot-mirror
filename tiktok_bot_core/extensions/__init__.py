"""扩展点 — 插件注册器（替代 ChopperBot 的 META-INF 机制）"""

from .registry import (
    ExtensionRegistry,
    CollectorPlugin,
    ChannelPlugin,
    FilterPlugin,
    StoragePlugin,
    register,
)

__all__ = [
    "ExtensionRegistry",
    "CollectorPlugin",
    "ChannelPlugin",
    "FilterPlugin",
    "StoragePlugin",
    "register",
]
