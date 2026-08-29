"""
场景管理器
=============

- 栈结构：switch / push / pop
- 统一管理场景的 enter/exit 生命周期
- 统一持有 SceneContext，并在 switch 时注入
- 统一清理 pygame_gui 元素：Scene.exit 中应调用 ui_element.kill()，
  为了兜底，每次场景切换后调用 gui_manager.clear_and_reset()（轻量安全）

注意：
    对于 BattleScene，会自行持有 BattleEngine / 大量资源，exit 时必须释放。
"""

from __future__ import annotations

from typing import List, Type

import pygame
import pygame_gui
from logging import Logger

from ..config.settings import Settings
from ..core.event_bus import EventBus, EventType, GameEvent
from ..storage.records_store import RecordsStore
from ..utils.logger import get_logger
from .base_scene import Scene, SceneContext


class SceneManager:
    """场景栈管理器。"""

    def __init__(
        self,
        screen: pygame.Surface,
        gui_manager: pygame_gui.UIManager,
        settings: Settings,
        logger: Logger,
        event_bus: EventBus,
        records_store: RecordsStore,
    ) -> None:
        self.screen = screen
        self.gui_manager = gui_manager
        self.settings = settings
        self.logger = logger
        self.event_bus = event_bus
        self.records_store = records_store

        self._stack: List[Scene] = []
        self._ctx = SceneContext(
            screen=screen,
            gui_manager=gui_manager,
            settings=settings,
            logger=logger,
            event_bus=event_bus,
            records_store=records_store,
            scene_manager=self,
        )

    # -------------------------------------------------
    # 对外 API
    # -------------------------------------------------
    @property
    def current(self) -> Scene | None:
        return self._stack[-1] if self._stack else None

    def switch(self, scene_cls: Type[Scene], **kwargs) -> Scene:
        """清空栈并切换到新场景。推荐使用。"""
        # 退出所有旧场景（LIFO）
        while self._stack:
            old = self._stack.pop()
            try:
                old.exit()
            except Exception:  # noqa: BLE001
                self.logger.exception("场景 exit 异常")
        # 清理 GUI
        self.gui_manager.clear_and_reset()
        # 进入新场景
        scene = scene_cls(**kwargs)
        scene.enter(self._ctx)
        self._stack.append(scene)
        self.logger.info(f"[Scene] 切换 -> {scene.__class__.__name__}")
        self.event_bus.publish(GameEvent(EventType.SCENE_SWITCH, scene_name=scene.__class__.__name__))
        return scene

    def push(self, scene_cls: Type[Scene], **kwargs) -> Scene:
        """压入新场景，保留底下场景（当前下一个版本用，保留 API）。"""
        current = self.current
        scene = scene_cls(**kwargs)
        scene.enter(self._ctx)
        self._stack.append(scene)
        self.logger.info(f"[Scene] push -> {scene.__class__.__name__}")
        self.event_bus.publish(GameEvent(EventType.SCENE_PUSH, scene_name=scene.__class__.__name__))
        return scene

    def pop(self) -> Scene | None:
        """弹出当前场景，回到下一层。"""
        if len(self._stack) <= 1:
            self.logger.warning("[Scene] pop 失败：栈中只剩一个场景")
            return None
        old = self._stack.pop()
        try:
            old.exit()
        except Exception:  # noqa: BLE001
            self.logger.exception("场景 exit 异常")
        # 清理 GUI 并让新顶层重建（新的 on_enter 不会自动被调用，我们手动触发：先 exit 再 enter 太麻烦，
        # 简化策略：pop 后重新 enter 当前顶层，调用其 on_enter 之前先保存 ctx 避免清空）
        current = self.current
        if current is not None:
            ctx = current.ctx
            try:
                current.on_exit()
            except Exception:  # noqa: BLE001
                self.logger.exception("场景 on_exit 异常")
            self.gui_manager.clear_and_reset()
            try:
                current.on_enter()
            except Exception:  # noqa: BLE001
                self.logger.exception("场景 on_enter 异常")
            current.ctx = ctx
        self.logger.info(f"[Scene] pop -> 回到 {current.__class__.__name__ if current else 'None'}")
        self.event_bus.publish(GameEvent(EventType.SCENE_POP))
        return old

    # -------------------------------------------------
    # 每帧：由 main.py 主循环调用
    # -------------------------------------------------
    def handle_event(self, event: pygame.event.Event) -> None:
        s = self.current
        if s is not None:
            s.handle_event(event)

    def update(self, dt: float) -> None:
        s = self.current
        if s is not None:
            s.update(dt)

    def draw(self, screen: pygame.Surface) -> None:
        s = self.current
        if s is not None:
            s.draw(screen)

    # -------------------------------------------------
    # 全局关闭
    # -------------------------------------------------
    def shutdown(self) -> None:
        """退出时清理所有场景。"""
        while self._stack:
            s = self._stack.pop()
            try:
                s.exit()
            except Exception:  # noqa: BLE001
                self.logger.exception("shutdown 时场景 exit 异常")
        try:
            self.gui_manager.clear_and_reset()
        except Exception:  # noqa: BLE001
            pass
