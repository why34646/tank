"""
主菜单场景
=============

显示内容：
- 大标题 "坦克动荡"
- 三个按钮：个人游戏、联机游戏、查看战绩
- 右下角：版本号

按钮点击：
- 个人游戏 -> 切换到 SoloSetupScene
- 联机游戏 -> 切换到 CodenameScene（联机子场景）
- 查看战绩 -> 切换到 RecordsScene
"""

from __future__ import annotations

from typing import Optional

import pygame
import pygame_gui
import pygame_gui.elements.ui_button as ui_button
import pygame_gui.elements.ui_label as ui_label

from .base_scene import Scene


class MainMenuScene(Scene):
    """主菜单。"""

    def __init__(self) -> None:
        super().__init__()
        self._title_lbl: Optional[ui_label.UILabel] = None
        self._btn_solo: Optional[ui_button.UIButton] = None
        self._btn_online: Optional[ui_button.UIButton] = None
        self._btn_records: Optional[ui_button.UIButton] = None
        self._version_lbl: Optional[ui_label.UILabel] = None

    # -------------------------------------------------
    def on_enter(self) -> None:
        assert self.ctx is not None
        s = self.ctx.settings
        gui = self.ctx.gui_manager
        w, h = s.WINDOW_WIDTH, s.WINDOW_HEIGHT

        # 标题
        self._title_lbl = ui_label.UILabel(
            relative_rect=pygame.Rect((w // 2 - 300, h // 4), (600, 120)),
            text="坦克动荡",
            manager=gui,
            object_id="#main_title",
        )

        # 三个按钮，垂直居中排列
        btn_w, btn_h = 360, 80
        start_y = h // 2 - btn_h
        spacing = 28
        cx = w // 2 - btn_w // 2

        self._btn_solo = ui_button.UIButton(
            relative_rect=pygame.Rect((cx, start_y), (btn_w, btn_h)),
            text="个人游戏",
            manager=gui,
            object_id="#menu_btn",
        )
        self._btn_online = ui_button.UIButton(
            relative_rect=pygame.Rect((cx, start_y + btn_h + spacing), (btn_w, btn_h)),
            text="联机游戏",
            manager=gui,
            object_id="#menu_btn",
        )
        self._btn_records = ui_button.UIButton(
            relative_rect=pygame.Rect((cx, start_y + (btn_h + spacing) * 2), (btn_w, btn_h)),
            text="查看战绩",
            manager=gui,
            object_id="#menu_btn",
        )

        # 版本号（右下角）
        self._version_lbl = ui_label.UILabel(
            relative_rect=pygame.Rect((w - 200, h - 60), (180, 40)),
            text=f"v{s.VERSION}",
            manager=gui,
            object_id="#version_lbl",
        )

    def on_exit(self) -> None:
        for el in [self._title_lbl, self._btn_solo, self._btn_online, self._btn_records, self._version_lbl]:
            if el is not None:
                try:
                    el.kill()
                except Exception:  # noqa: BLE001
                    pass
        self._title_lbl = self._btn_solo = self._btn_online = self._btn_records = self._version_lbl = None

    # -------------------------------------------------
    def handle_event(self, event: pygame.event.Event) -> None:
        if event.type != pygame_gui.UI_BUTTON_PRESSED:
            return
        assert self.ctx is not None
        sm = self.ctx.scene_manager
        assert sm is not None

        if event.ui_element is self._btn_solo:
            from .solo_setup_scene import SoloSetupScene
            sm.switch(SoloSetupScene)
        elif event.ui_element is self._btn_online:
            from .online.codename_scene import CodenameScene
            sm.switch(CodenameScene)
        elif event.ui_element is self._btn_records:
            from .records_scene import RecordsScene
            sm.switch(RecordsScene)

    def draw(self, screen: pygame.Surface) -> None:
        # 占位：在标题下方画一条装饰线
        if self.ctx is None:
            return
        s = self.ctx.settings
        w, h = s.WINDOW_WIDTH, s.WINDOW_HEIGHT
        line_rect = pygame.Rect(w // 2 - 260, h // 4 + 130, 520, 3)
        pygame.draw.rect(screen, (120, 160, 220), line_rect, border_radius=2)
