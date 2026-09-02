"""
战绩查看场景
===============

- 读取 RecordsStore.load_all()
- 显示为一个滚动列表（pygame_gui.UISelectionList）
- 顶部显示"总场次：N"
- "返回主菜单"按钮

架构阶段：保证即使没有战绩也能正常显示"暂无战绩"提示，不崩溃。
"""

from __future__ import annotations

from typing import List, Optional

import pygame
import pygame_gui
import pygame_gui.elements.ui_button as ui_button
import pygame_gui.elements.ui_label as ui_label
import pygame_gui.elements.ui_selection_list as ui_list

from ..storage.records_store import BattleRecord
from .base_scene import Scene


class RecordsScene(Scene):
    """战绩查看页。"""

    def __init__(self) -> None:
        super().__init__()
        self._title: Optional[ui_label.UILabel] = None
        self._back_btn: Optional[ui_button.UIButton] = None
        self._stat_lbl: Optional[ui_label.UILabel] = None
        self._list: Optional[ui_list.UISelectionList] = None
        self._empty_lbl: Optional[ui_label.UILabel] = None

    # -------------------------------------------------
    def on_enter(self) -> None:
        assert self.ctx is not None
        s = self.ctx.settings
        gui = self.ctx.gui_manager
        w, h = s.WINDOW_WIDTH, s.WINDOW_HEIGHT

        self._title = ui_label.UILabel(
            relative_rect=pygame.Rect((w // 2 - 200, 60), (400, 60)),
            text="历史战绩",
            manager=gui, object_id="#page_title",
        )
        self._back_btn = ui_button.UIButton(
            relative_rect=pygame.Rect((60, 60), (180, 54)),
            text="← 返回主菜单", manager=gui,
        )

        # 读数据
        records: List[BattleRecord] = self.ctx.records_store.load_all()
        total = len(records)
        self._stat_lbl = ui_label.UILabel(
            relative_rect=pygame.Rect((w // 2 - 240, 140), (480, 40)),
            text=f"总场次：{total}",
            manager=gui, object_id="#form_label",
        )

        list_w, list_h = 1200, 800
        list_x = w // 2 - list_w // 2
        list_y = 220

        if records:
            items = [self._record_to_text(r) for r in records]
            self._list = ui_list.UISelectionList(
                relative_rect=pygame.Rect((list_x, list_y), (list_w, list_h)),
                item_list=items,
                manager=gui,
                allow_multi_select=False,
            )
        else:
            self._empty_lbl = ui_label.UILabel(
                relative_rect=pygame.Rect((list_x, list_y + 200), (list_w, 60)),
                text="暂无战绩，先去玩一局吧！",
                manager=gui,
            )

    def on_exit(self) -> None:
        for el in [self._title, self._back_btn, self._stat_lbl, self._list, self._empty_lbl]:
            if el is not None:
                try: el.kill()
                except Exception: pass  # noqa: E722
        self._title = self._back_btn = self._stat_lbl = self._list = self._empty_lbl = None

    # -------------------------------------------------
    def handle_event(self, event: pygame.event.Event) -> None:
        if event.type != pygame_gui.UI_BUTTON_PRESSED:
            return
        assert self.ctx is not None and self.ctx.scene_manager is not None
        if event.ui_element is self._back_btn:
            from .main_menu_scene import MainMenuScene
            self.ctx.scene_manager.switch(MainMenuScene)

    # -------------------------------------------------
    @staticmethod
    def _record_to_text(r: BattleRecord) -> str:
        """按新格式拼一行文字。

        例：#001 个人 分两队 总局数19 击杀5 存活5 队伍胜场6 时长240s 2026-08-30 21:40
        FFA 模式不带【队伍胜场】字段。
        """
        # 是否 Team 模式：team_wins > 0 或 mode 包含 "队"
        is_team = "队" in r.mode or r.team_wins > 0
        parts = [
            f"#{r.id:03d}",
            r.game_scope,
            r.mode,
            f"总局数{r.total_rounds}",
            f"击杀{r.kills}",
            f"存活{r.survived}",
        ]
        if is_team:
            parts.append(f"队伍胜场{r.team_wins}")
        parts.append(f"时长{r.duration_sec}s")
        parts.append(r.started_at)
        return "  ".join(parts)
