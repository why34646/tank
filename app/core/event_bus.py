"""
事件总线
=========

发布-订阅模式，解耦各模块：
- 场景切换由场景管理器订阅 SCENE_SWITCH 事件
- 网络模块发布 NET_* 事件，UI 场景订阅以更新视图
- 战斗模块发布战斗事件，战绩/UI 模块订阅

使用方式：
    bus = EventBus()

    # 订阅
    def on_switch(evt):
        print("切换到", evt.data["scene"])
    bus.subscribe(EventType.SCENE_SWITCH, on_switch)

    # 发布
    bus.publish(GameEvent(EventType.SCENE_SWITCH, scene=MainMenuScene))
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List

from .constants import EventType


# ============================================================
# 事件对象
# ============================================================
@dataclass
class GameEvent:
    """单个事件。type 指定类型，data 为可变参数字典。"""
    type: EventType
    data: Dict[str, Any] = field(default_factory=dict)

    def __init__(self, type_: EventType, **kwargs: Any) -> None:
        self.type = type_
        self.data = kwargs

    def __getattr__(self, item: str) -> Any:
        """通过属性访问 data 中的字段（更优雅）。"""
        if item in ("type", "data"):
            raise AttributeError(item)
        if item in self.data:
            return self.data[item]
        raise AttributeError(f"事件 {self.type.name} 不含字段 '{item}'")


# ============================================================
# 事件总线
# ============================================================
class EventBus:
    """轻量级同步事件总线。"""

    def __init__(self) -> None:
        self._subscribers: Dict[EventType, List[Callable[[GameEvent], None]]] = {}

    # -------------------------------------------------
    # 订阅 / 取消订阅
    # -------------------------------------------------
    def subscribe(self, event_type: EventType, handler: Callable[[GameEvent], None]) -> None:
        """订阅指定事件类型。"""
        self._subscribers.setdefault(event_type, []).append(handler)

    def unsubscribe(self, event_type: EventType, handler: Callable[[GameEvent], None]) -> None:
        """取消订阅。handler 必须是之前 subscribe 传入的同一个对象。"""
        if event_type in self._subscribers and handler in self._subscribers[event_type]:
            self._subscribers[event_type].remove(handler)

    # -------------------------------------------------
    # 发布
    # -------------------------------------------------
    def publish(self, event: GameEvent) -> None:
        """同步发布事件。逐个调用订阅者；单个异常不影响其他订阅者。"""
        handlers = self._subscribers.get(event.type, [])
        for h in list(handlers):  # 复制一份，避免回调中修改列表
            try:
                h(event)
            except Exception:  # noqa: BLE001
                # 单个 handler 异常不影响整体，此处 print 兜底（调用方应自行记录）
                import traceback
                traceback.print_exc()
