"""
场景基类 & 场景上下文
========================

每个场景（主菜单/设置/战斗/战绩 等）继承 Scene，并实现：
    handle_event(event)
    update(dt)
    draw(screen)
    enter(ctx)        进入场景时调用
    exit()            离开场景时调用

SceneContext 是场景管理器传给每个场景的共享上下文指针（window / gui / settings / logger / event_bus / records）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

import pygame
import pygame_gui

if TYPE_CHECKING:
    from ..config.settings import Settings
    from ..core.event_bus import EventBus
    from ..storage.records_store import RecordsStore
    from logging import Logger
    from .scene_manager import SceneManager


@dataclass
class SceneContext:
    """场景的共享环境。SceneManager 创建后传给每个 Scene。"""
    screen: pygame.Surface
    gui_manager: pygame_gui.UIManager
    settings: "Settings"
    logger: "Logger"
    event_bus: "EventBus"
    records_store: "RecordsStore"
    scene_manager: Optional["SceneManager"] = None  # 由 SceneManager 注入自身


class Scene:
    """所有场景的基类。"""

    def __init__(self) -> None:
        self.ctx: Optional[SceneContext] = None
        self._initialized = False

    # -------------------------------------------------
    # 生命周期
    # -------------------------------------------------
    def enter(self, ctx: SceneContext) -> None:
        """场景进入（可创建 GUI 元素等）。默认仅保存 ctx。"""
        self.ctx = ctx
        self.on_enter()
        self._initialized = True

    def exit(self) -> None:
        """场景退出。应清理 GUI 元素（kill UIButton 等）。"""
        self.on_exit()
        self.ctx = None
        self._initialized = False

    def on_enter(self) -> None:
        """子类重写：构建 UI。"""

    def on_exit(self) -> None:
        """子类重写：销毁 UI、取消事件订阅等。"""

    # -------------------------------------------------
    # 每帧
    # -------------------------------------------------
    def handle_event(self, event: pygame.event.Event) -> None:
        """处理 Pygame 事件。默认不做任何事。"""

    def update(self, dt: float) -> None:
        """每帧逻辑更新。默认不做任何事。"""

    def draw(self, screen: pygame.Surface) -> None:
        """绘制。pygame_gui 的 draw_ui 由主循环统一调用，此处只画游戏内图形。"""

    def post_gui_draw(self, screen: pygame.Surface) -> None:
        """在 gui_manager.draw_ui 之后绘制，用于在 GUI 控件之上叠加小标注。
        默认空实现；仅在确实需要覆盖 GUI 层的场景中重写。"""
