"""事件总线 — 借鉴 ChopperBot Exchange 模式但用 asyncio"""

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    """事件类型"""

    # Pipeline 阶段完成事件
    COLLECT_DONE = "collect.done"
    FILTER_DONE = "filter.done"
    STRATEGY_DONE = "strategy.done"
    OUTREACH_DONE = "outreach.done"
    REPORT_DONE = "report.done"
    ITERATE_DONE = "iterate.done"

    # 用户相关
    USER_DISCOVERED = "user.discovered"
    USER_QUALIFIED = "user.qualified"
    USER_REJECTED = "user.rejected"
    USER_CONTACTED = "user.contacted"
    USER_REPLIED = "user.replied"

    # 错误事件
    ERROR_OCCURRED = "error.occurred"

    # 系统事件
    PIPELINE_START = "pipeline.start"
    PIPELINE_END = "pipeline.end"

    # Hermes 浏览器 Agent
    BROWSE_STEP = "browse.step"
    BROWSE_DONE = "browse.done"


@dataclass
class Event:
    """事件对象"""

    type: EventType
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.utcnow)
    source: str = ""


EventHandler = Callable[[Event], Awaitable[None]]


class EventBus:
    """异步事件总线

    用法：
        bus = EventBus()
        bus.subscribe(EventType.USER_QUALIFIED, my_handler)
        await bus.publish(Event(EventType.USER_QUALIFIED, {"user_id": 1}))
    """

    def __init__(self):
        self._subscribers: dict[EventType, list[EventHandler]] = defaultdict(list)
        self._history: list[Event] = []
        self._max_history = 1000
        self._pending: list[asyncio.Task[Any]] = []

    def subscribe(self, event_type: EventType, handler: EventHandler) -> None:
        self._subscribers[event_type].append(handler)
        logger.debug(f"订阅事件 {event_type}: {handler.__name__}")

    def unsubscribe(self, event_type: EventType, handler: EventHandler) -> None:
        try:
            self._subscribers[event_type].remove(handler)
        except ValueError:
            pass

    async def publish(self, event: Event) -> None:
        """发布事件，异步调用所有订阅者"""
        self._history.append(event)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

        handlers = self._subscribers.get(event.type, [])
        if not handlers:
            return

        logger.debug(f"发布事件 {event.type}，{len(handlers)} 个订阅者")
        await asyncio.gather(*[h(event) for h in handlers], return_exceptions=True)

    def history(self, event_type: EventType | None = None, limit: int = 100) -> list[Event]:
        """获取事件历史"""
        items = [e for e in self._history if event_type is None or e.type == event_type]
        return items[-limit:]

    def append_history(self, event: Event) -> None:
        """同步、容错地把事件追加到 history（不发订阅者）。

        Agent 循环里每一步都调用，避免阻塞主循环；订阅者可改用
        `history(EventType.BROWSE_STEP)` 拉取。
        """
        try:
            self._history.append(event)
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history:]
        except Exception:
            logger.warning("append_history failed", exc_info=True)

    def fire(self, event: Event) -> None:
        """fire-and-forget：同步追加到 history，同时后台触发订阅者。

        用于高频事件（BROWSE_STEP），主循环不等待订阅者完成。
        派发任务被记入 _pending，调用方可以在循环末尾 gather 它们。
        """
        self.append_history(event)
        handlers = self._subscribers.get(event.type, [])
        if not handlers:
            return

        async def _dispatch() -> None:
            await asyncio.gather(
                *[h(event) for h in handlers], return_exceptions=True
            )

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            logger.debug("fire dispatch skipped: no event loop")
            return
        if not loop.is_running():
            loop.run_until_complete(_dispatch())
            return
        task = loop.create_task(_dispatch())
        self._pending.append(task)

    def clear_history(self) -> None:
        self._history.clear()


# 全局单例
_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus
