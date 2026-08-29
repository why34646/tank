"""
战绩查看场景
===============

- 读取 RecordsStore.load_all()
- 显示为一个滚动列表（pygame_gui.UISelectionList）
- 顶部显示总场次 / 胜场统计
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
        wins = sum(1 for r in records if r.win)
        self._stat_lbl = ui_label.UILabel(
            relative_rect=pygame.Rect((w // 2 - 240, 140), (480, 40)),
            text=f"总场次：{total}    胜场：{wins}    胜率：{self._rate(wins, total)}",
            manager=gui, object_id="#form_label",
        )

        list_w, list_h = 1100, 800
        list_x = w // 2 - list_w // 2
        list_y = 220

        if records:
            items = [self._record_to_text(i + 1, r) for i, r in enumerate(records)]
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
    def _rate(wins: int, total: int) -> str:
        if total == 0:
            return "0%"
        return f"{int(wins * 100 / total)}%"

    @staticmethod
    def _record_to_text(idx: int, r: BattleRecord) -> str:
        win_tag = "胜" if r.win else "负"
        online_tag = " 联机" if r.is_online else ""
        return (
            f"#{idx:03d}  [{win_tag}]{online_tag}  击杀{r.kills}  存活{r.survival}  "
            f"时长{r.duration_sec}s  模式={r.mode}  {r.timestamp}"
        )
