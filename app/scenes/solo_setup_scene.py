"""
个人游戏设置场景
==================

设置项（对应立项文档模块三 P0）：
- 总人数（2-6）
- AI 难度（简单/普通/大师/噩梦）
- 对战模式（各自为战/2v2/3v3）
- 地图大小（小/中/大/极大）

按钮：开始游戏、返回主菜单

架构阶段：
- 所有控件渲染，默认值正确
- 点击"开始游戏"进入 BattleScene（单人+AI 的占位战斗，AI 暂不动）
- 点击"返回主菜单"回到 MainMenuScene
"""

from __future__ import annotations

from typing import Dict, Optional

import pygame
import pygame_gui
import pygame_gui.elements.ui_button as ui_button
import pygame_gui.elements.ui_label as ui_label
import pygame_gui.elements.ui_drop_down_menu as ui_drop
import pygame_gui.windows.ui_confirmation_dialog as ui_dialog  # noqa: F401

from ..core.constants import AIDifficulty, GameMode, MazeSize, MapGenMode
from .base_scene import Scene


# 【地图生成】UI 选项 → 实际候选模式列表（classic/carve 组合）
_MAP_GEN_UI_TO_MODES: dict[str, list[str]] = {
    "原版(隔间砌墙)":         [MapGenMode.CLASSIC.value],
    "新版(破壁凿洞)":         [MapGenMode.CARVE.value],
    "原版+新版(随机切换)":   [MapGenMode.CLASSIC.value, MapGenMode.CARVE.value],
}

# 【坦克颜色】下拉选项名 → PLAYER_COLORS 索引（红/蓝/绿/黄/紫/青）
_TANK_COLOR_NAMES: list[str] = ["红", "蓝", "绿", "黄", "紫", "青"]


class SoloSetupScene(Scene):
    """个人游戏设置页。"""

    def __init__(self) -> None:
        super().__init__()
        self._title: Optional[ui_label.UILabel] = None
        self._back_btn: Optional[ui_button.UIButton] = None
        self._start_btn: Optional[ui_button.UIButton] = None

        self._labels: Dict[str, ui_label.UILabel] = {}
        self._dropdowns: Dict[str, ui_drop.UIDropDownMenu] = {}

        # 设置值（默认值）
        self.total_players: int = 4
        self.ai_difficulty: AIDifficulty = AIDifficulty.NORMAL
        self.mode: GameMode = GameMode.FREE_FOR_ALL
        self.maze_size: MazeSize = MazeSize.ALL  # 默认"全部地图"：每次随机选一种大小
        # 地图生成 UI 选项（字符串）；启动时转换成 map_gen_modes 列表
        self.map_gen_ui: str = "新版(破壁凿洞)"
        # 坦克颜色索引（对应 settings.PLAYER_COLORS）
        self.player_color_idx: int = 0  # 默认红

    # -------------------------------------------------
    def on_enter(self) -> None:
        assert self.ctx is not None
        s = self.ctx.settings
        gui = self.ctx.gui_manager
        w, h = s.WINDOW_WIDTH, s.WINDOW_HEIGHT

        # 标题 + 返回
        self._title = ui_label.UILabel(
            relative_rect=pygame.Rect((w // 2 - 200, 80), (400, 60)),
            text="个人游戏 - 设置",
            manager=gui, object_id="#page_title",
        )
        self._back_btn = ui_button.UIButton(
            relative_rect=pygame.Rect((60, 60), (160, 54)),
            text="← 返回主菜单", manager=gui,
        )
        self._start_btn = ui_button.UIButton(
            relative_rect=pygame.Rect((w // 2 - 120, h - 140), (240, 70)),
            text="开始游戏", manager=gui, object_id="#primary_btn",
        )

        # 设置项：每行 (label, dropdown)
        rows = [
            ("total_players",  "总参战人数", [str(x) for x in range(2, 7)], str(self.total_players)),
            ("ai_difficulty",  "AI 难度",    [e.value for e in AIDifficulty],  self.ai_difficulty.value),
            ("mode",           "对战模式",   [e.value for e in GameMode],      self.mode.value),
            ("maze_size",      "地图大小",   [e.value for e in MazeSize],      self.maze_size.value),
            ("map_gen",        "地图生成",   list(_MAP_GEN_UI_TO_MODES.keys()), self.map_gen_ui),
            ("tank_color",     "坦克颜色",   _TANK_COLOR_NAMES,                _TANK_COLOR_NAMES[self.player_color_idx]),
        ]
        lbl_w, dd_w, row_h = 240, 320, 64
        start_x = w // 2 - (lbl_w + dd_w + 40) // 2
        start_y = 240
        for i, (key, title, options, default) in enumerate(rows):
            y = start_y + i * (row_h + 16)
            lbl = ui_label.UILabel(
                relative_rect=pygame.Rect((start_x, y), (lbl_w, row_h)),
                text=title, manager=gui, object_id="#form_label",
            )
            dd = ui_drop.UIDropDownMenu(
                options_list=options,
                starting_option=default,
                relative_rect=pygame.Rect((start_x + lbl_w + 20, y), (dd_w, row_h)),
                manager=gui,
            )
            self._labels[key] = lbl
            self._dropdowns[key] = dd

    def on_exit(self) -> None:
        for lbl in self._labels.values():
            try: lbl.kill()
            except Exception: pass  # noqa: E722
        for dd in self._dropdowns.values():
            try: dd.kill()
            except Exception: pass  # noqa: E722
        self._labels.clear()
        self._dropdowns.clear()
        for el in [self._title, self._back_btn, self._start_btn]:
            if el is not None:
                try: el.kill()
                except Exception: pass  # noqa: E722
        self._title = self._back_btn = self._start_btn = None

    # -------------------------------------------------
    def handle_event(self, event: pygame.event.Event) -> None:
        assert self.ctx is not None
        sm = self.ctx.scene_manager
        assert sm is not None

        if event.type == pygame_gui.UI_BUTTON_PRESSED:
            if event.ui_element is self._back_btn:
                from .main_menu_scene import MainMenuScene
                sm.switch(MainMenuScene)
            elif event.ui_element is self._start_btn:
                from app.game.sound_manager import SoundManager
                SoundManager.instance().play("kada")
                self._apply_dropdowns()
                from .battle_scene import BattleScene
                # 把 UI 选项转换成实际的 map_gen_modes 候选列表
                map_gen_modes = _MAP_GEN_UI_TO_MODES.get(
                    self.map_gen_ui, [MapGenMode.CARVE.value]
                )
                # 传递设置参数
                sm.switch(
                    BattleScene,
                    total_players=self.total_players,
                    ai_difficulty=self.ai_difficulty,
                    mode=self.mode,
                    maze_size=self.maze_size,
                    is_online=False,
                    map_gen_modes=list(map_gen_modes),
                    player_color_idx=self.player_color_idx,
                )

        elif event.type == pygame_gui.UI_DROP_DOWN_MENU_CHANGED:
            # 立即同步值（避免点击开始时再读，出现"最后一个下拉没应用"的小概率问题）
            ui = event.ui_element
            for key, dd in self._dropdowns.items():
                if ui is dd:
                    self._sync_value(key, event.text)
                    break

    # -------------------------------------------------
    def _apply_dropdowns(self) -> None:
        for key, dd in self._dropdowns.items():
            self._sync_value(key, dd.selected_option)

    def _sync_value(self, key: str, text: str) -> None:
        text = str(text)
        if key == "total_players":
            try:
                self.total_players = max(2, min(6, int(text)))
            except ValueError:
                pass
        elif key == "ai_difficulty":
            try:
                self.ai_difficulty = AIDifficulty(text)
            except ValueError:
                pass
        elif key == "mode":
            try:
                self.mode = GameMode(text)
            except ValueError:
                pass
        elif key == "maze_size":
            try:
                self.maze_size = MazeSize(text)
            except ValueError:
                pass
        elif key == "map_gen":
            # 只接受 _MAP_GEN_UI_TO_MODES 中的合法键
            if text in _MAP_GEN_UI_TO_MODES:
                self.map_gen_ui = text
        elif key == "tank_color":
            if text in _TANK_COLOR_NAMES:
                self.player_color_idx = _TANK_COLOR_NAMES.index(text)
