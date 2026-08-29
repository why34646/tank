"""
联机 - 代号输入场景
=======================

两个大按钮：
- [ 创建房间 ]：需要代号。若输入合法，进入 RoomScene（以房主身份）
- [ 加入游戏 ]：需要代号。若输入合法，进入 LobbyScene（房间大厅，搜索并选择房间）

额外约束：
- 代号 2-12 字，禁止特殊字符（仅中文、英文、数字、下划线）
- 底部"返回主菜单"按钮

架构阶段：UI 完整，点击按钮可切换到下一个空场景（创建/加入的实际逻辑后续实现）。
"""

from __future__ import annotations

import re
from typing import Optional

import pygame
import pygame_gui
import pygame_gui.elements.ui_button as ui_button
import pygame_gui.elements.ui_label as ui_label
import pygame_gui.elements.ui_text_entry_line as ui_entry

from ..base_scene import Scene

# 允许的字符：中文、英文、数字、下划线
CODENAME_RE = re.compile(r"^[\u4e00-\u9fa5A-Za-z0-9_]{2,12}$")


class CodenameScene(Scene):
    """联机代号输入页。"""

    def __init__(self) -> None:
        super().__init__()
        self._title: Optional[ui_label.UILabel] = None
        self._back_btn: Optional[ui_button.UIButton] = None
        self._hint_lbl: Optional[ui_label.UILabel] = None
        self._codename_entry: Optional[ui_entry.UITextEntryLine] = None
        self._btn_create: Optional[ui_button.UIButton] = None
        self._btn_join: Optional[ui_button.UIButton] = None
        self._err_lbl: Optional[ui_label.UILabel] = None

    # -------------------------------------------------
    def on_enter(self) -> None:
        assert self.ctx is not None
        s = self.ctx.settings
        gui = self.ctx.gui_manager
        w, h = s.WINDOW_WIDTH, s.WINDOW_HEIGHT

        self._title = ui_label.UILabel(
            relative_rect=pygame.Rect((w // 2 - 240, 100), (480, 60)),
            text="联机游戏 - 输入代号",
            manager=gui, object_id="#page_title",
        )
        self._back_btn = ui_button.UIButton(
            relative_rect=pygame.Rect((60, 60), (180, 54)),
            text="← 返回主菜单", manager=gui,
        )
        self._hint_lbl = ui_label.UILabel(
            relative_rect=pygame.Rect((w // 2 - 280, h // 2 - 140), (560, 36)),
            text="请输入代号（2-12位，中文/英文/数字/下划线）：",
            manager=gui, object_id="#form_label",
        )
        self._codename_entry = ui_entry.UITextEntryLine(
            relative_rect=pygame.Rect((w // 2 - 260, h // 2 - 80), (520, 60)),
            manager=gui,
            placeholder_text="例如：坦克手_01",
        )
        self._codename_entry.set_text_length_limit(12)

        btn_w, btn_h = 240, 70
        cx = w // 2
        gap = 60
        self._btn_create = ui_button.UIButton(
            relative_rect=pygame.Rect((cx - btn_w - gap // 2, h // 2 + 40), (btn_w, btn_h)),
            text="创建房间", manager=gui, object_id="#primary_btn",
        )
        self._btn_join = ui_button.UIButton(
            relative_rect=pygame.Rect((cx + gap // 2, h // 2 + 40), (btn_w, btn_h)),
            text="加入游戏", manager=gui,
        )

        self._err_lbl = ui_label.UILabel(
            relative_rect=pygame.Rect((w // 2 - 260, h // 2 + 140), (520, 36)),
            text="", manager=gui,
        )

    def on_exit(self) -> None:
        for el in [self._title, self._back_btn, self._hint_lbl, self._codename_entry,
                   self._btn_create, self._btn_join, self._err_lbl]:
            if el is not None:
                try: el.kill()
                except Exception: pass  # noqa: E722
        self._title = self._back_btn = self._hint_lbl = self._codename_entry = None
        self._btn_create = self._btn_join = self._err_lbl = None

    # -------------------------------------------------
    def handle_event(self, event: pygame.event.Event) -> None:
        assert self.ctx is not None and self.ctx.scene_manager is not None
        sm = self.ctx.scene_manager

        if event.type == pygame_gui.UI_BUTTON_PRESSED:
            if event.ui_element is self._back_btn:
                from ..main_menu_scene import MainMenuScene
                sm.switch(MainMenuScene)
                return

            # 创建 / 加入前都校验代号
            codename = self._validated_codename()
            if codename is None:
                return

            if event.ui_element is self._btn_create:
                from .room_scene import RoomScene
                sm.switch(RoomScene, codename=codename, as_host=True)
            elif event.ui_element is self._btn_join:
                from .lobby_scene import LobbyScene
                sm.switch(LobbyScene, codename=codename)

    # -------------------------------------------------
    def _validated_codename(self) -> Optional[str]:
        text = ""
        if self._codename_entry is not None:
            text = (self._codename_entry.get_text() or "").strip()
        if not CODENAME_RE.match(text):
            if self._err_lbl is not None:
                self._err_lbl.set_text("❌ 代号不合法：必须 2-12 位，且只能是中文/英文/数字/下划线")
            return None
        if self._err_lbl is not None:
            self._err_lbl.set_text("")
        return text
