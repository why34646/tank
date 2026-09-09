"""
成就场景（占位）
==============

当前版本：只显示标题 + 返回按钮 + 占位文字。
后续版本（V2 扩展）：接入独立成就档案 achievements.json、
解锁条件检测、实时弹提示、网格/列表化成就展示。
"""

from __future__ import annotations

from typing import Optional

import pygame
import pygame_gui
import pygame_gui.elements.ui_button as ui_button
import pygame_gui.elements.ui_label as ui_label

from .base_scene import Scene


class AchievementScene(Scene):
    """成就页（当前为占位）。"""

    def __init__(self) -> None:
        super().__init__()
        self._title: Optional[ui_label.UILabel] = None
        self._back_btn: Optional[ui_button.UIButton] = None
        self._placeholder: Optional[ui_label.UILabel] = None

    # -------------------------------------------------
    def on_enter(self) -> None:
        assert self.ctx is not None
        s = self.ctx.settings
        gui = self.ctx.gui_manager
        w, h = s.WINDOW_WIDTH, s.WINDOW_HEIGHT

        self._title = ui_label.UILabel(
            relative_rect=pygame.Rect((w // 2 - 200, 60), (400, 60)),
            text="我的成就",
            manager=gui,
            object_id="#page_title",
        )
        self._back_btn = ui_button.UIButton(
            relative_rect=pygame.Rect((60, 60), (180, 54)),
            text="← 返回主菜单",
            manager=gui,
        )
        self._placeholder = ui_label.UILabel(
            relative_rect=pygame.Rect((w // 2 - 300, h // 2 - 40), (600, 80)),
            text="成就系统建设中…敬请期待！",
            manager=gui,
            object_id="#form_label",
        )

    def on_exit(self) -> None:
        for el in [self._title, self._back_btn, self._placeholder]:
            if el is not None:
                try:
                    el.kill()
                except Exception:  # noqa: BLE001
                    pass
        self._title = self._back_btn = self._placeholder = None

    # -------------------------------------------------
    def handle_event(self, event: pygame.event.Event) -> None:
        if event.type != pygame_gui.UI_BUTTON_PRESSED:
            return
        assert self.ctx is not None and self.ctx.scene_manager is not None
        if event.ui_element is self._back_btn:
            from .main_menu_scene import MainMenuScene
            self.ctx.scene_manager.switch(MainMenuScene)
