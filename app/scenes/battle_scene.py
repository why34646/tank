"""
战斗场景
==========

负责：
- 实例化 Tank 列表（1 个玩家 + N 个 AI）
- 实例化 Match + BattleEngine 并 start()
- 捕获玩家键盘输入 -> TankInput -> engine.set_tank_input()
- AI provider: 调用 AI 控制器 think()
- 提供退出/暂停按钮
- 对局结束后：保存战绩、自动进入下一局（无尽模式难度递增）

架构阶段：
- 玩家坦克使用 WASDQ 控制，能正常移动、发射、撞墙、炮弹反弹
- 右上角 HUD 显示存活/时间
- ESC 或点击"退出"返回 SoloSetupScene
- 结束后保存空战绩记录（不崩溃）
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional

import pygame
import pygame_gui
import pygame_gui.elements.ui_button as ui_button
import pygame_gui.elements.ui_label as ui_label

from ..ai.ai_controller import AIController, make_ai_controller
from ..config.settings import Settings
from ..core.constants import AIDifficulty, GameMode, MazeSize, MouseKeyMode
from ..core.event_bus import EventType
from ..game.game_engine import BattleEngine
from ..game.match import Match, MatchEndReason
from ..game.tank import Tank, TankInput
from ..storage.records_store import BattleRecord
from .base_scene import Scene


class BattleScene(Scene):
    """战斗场景。"""

    def __init__(
        self,
        total_players: int = 4,
        ai_difficulty: AIDifficulty = AIDifficulty.NORMAL,
        mode: GameMode = GameMode.FREE_FOR_ALL,
        maze_size: MazeSize = MazeSize.MEDIUM,
        is_online: bool = False,
        endless_round: int = 1,          # 第几局（无尽递进）
    ) -> None:
        super().__init__()
        self.total_players = total_players
        self.ai_difficulty = ai_difficulty
        self.mode = mode
        self.maze_size = maze_size
        self.is_online = is_online
        self.endless_round = endless_round

        # 运行时
        self._engine: Optional[BattleEngine] = None
        self._ai_controllers: Dict[int, AIController] = {}
        self._player_tank_id: Optional[int] = None

        # 输入状态（每帧基于按键构建 TankInput）
        self._keys_now: Dict[int, bool] = {}

        # GUI
        self._btn_quit: Optional[ui_button.UIButton] = None
        self._round_lbl: Optional[ui_label.UILabel] = None

        # 结束后的短暂延时再进下一局
        self._end_delay: float = 0.0
        self._end_handled: bool = False

    # -------------------------------------------------
    # 进入
    # -------------------------------------------------
    def on_enter(self) -> None:
        assert self.ctx is not None
        s = self.ctx.settings
        gui = self.ctx.gui_manager
        w = s.WINDOW_WIDTH

        # 1) 构造 tanks：玩家 1 个 + (total_players - 1) AI
        tanks: List[Tank] = []
        ai_count = max(0, self.total_players - 1)

        # 玩家
        player = Tank(
            tank_id=1,
            codename="玩家",
            color=s.PLAYER_COLORS[0],
            spawn_x=0, spawn_y=0,  # 后续 engine 初始化会覆盖
            team=0 if self.mode == GameMode.FREE_FOR_ALL else 1,
            settings=s,
            control_mode=MouseKeyMode.WASDQ,
            is_ai=False,
        )
        tanks.append(player)
        self._player_tank_id = player.id

        # AI
        for i in range(ai_count):
            idx_color = (i + 1) % len(s.PLAYER_COLORS)
            team_id = 0
            if self.mode == GameMode.TEAM_2V2:
                team_id = 1 if (i % 2 == 0) else 2
            elif self.mode == GameMode.TEAM_3V3:
                team_id = 1 if (i % 2 == 0) else 2
            ai_tank = Tank(
                tank_id=100 + i,
                codename=f"AI_{i+1}",
                color=s.PLAYER_COLORS[idx_color],
                spawn_x=0, spawn_y=0,
                team=team_id,
                settings=s,
                is_ai=True,
            )
            tanks.append(ai_tank)
            self._ai_controllers[ai_tank.id] = make_ai_controller(self.ai_difficulty)

        # 2) Match + BattleEngine
        match = Match(
            match_id=self.endless_round,
            mode=self.mode,
            tanks=tanks,
            maze_size_key=self.maze_size.value,
            ai_difficulty=self.ai_difficulty,
        )
        self._engine = BattleEngine(s, match, self.ctx.event_bus)
        self._engine.start()

        # 3) GUI：退出按钮 + 局数
        self._btn_quit = ui_button.UIButton(
            relative_rect=pygame.Rect((20, 20), (140, 48)),
            text="← 退出",
            manager=gui,
        )
        self._round_lbl = ui_label.UILabel(
            relative_rect=pygame.Rect((20, w - 200 if False else 80), (220, 36)),
            text=f"第 {self.endless_round} 局  {self.ai_difficulty.value}",
            manager=gui,
            object_id="#form_label",
        )

    def on_exit(self) -> None:
        if self._engine is not None:
            try:
                if self._engine.match.end_reason is None:
                    self._engine.stop(MatchEndReason.MANUAL_STOP)
            except Exception:  # noqa: BLE001
                pass
            self._engine = None
        self._ai_controllers.clear()
        for el in [self._btn_quit, self._round_lbl]:
            if el is not None:
                try: el.kill()
                except Exception: pass  # noqa: E722
        self._btn_quit = self._round_lbl = None

    # -------------------------------------------------
    # 事件：按钮 + 按键
    # -------------------------------------------------
    def handle_event(self, event: pygame.event.Event) -> None:
        assert self.ctx is not None
        sm = self.ctx.scene_manager
        assert sm is not None

        if event.type == pygame_gui.UI_BUTTON_PRESSED:
            if event.ui_element is self._btn_quit:
                # 保存战绩（若已结束则可能已经保存，此处兜底）
                self._save_record_if_needed(manual_exit=True)
                from .solo_setup_scene import SoloSetupScene
                sm.switch(SoloSetupScene)
                return

        if event.type == pygame.KEYDOWN:
            self._keys_now[event.key] = True
            if event.key == pygame.K_ESCAPE:
                self._save_record_if_needed(manual_exit=True)
                from .solo_setup_scene import SoloSetupScene
                sm.switch(SoloSetupScene)
                return
        elif event.type == pygame.KEYUP:
            self._keys_now[event.key] = False

    # -------------------------------------------------
    # 每帧更新
    # -------------------------------------------------
    def update(self, dt: float) -> None:
        if self._engine is None:
            return

        # 1) 玩家输入 -> TankInput
        if self._player_tank_id is not None:
            inp = self._build_player_input()
            self._engine.set_tank_input(self._player_tank_id, inp)

        # 2) AI 输入 provider
        def ai_provider(tank: Tank, engine: BattleEngine) -> TankInput:
            c = self._ai_controllers.get(tank.id)
            if c is None:
                return TankInput()
            return c.think(tank, engine)

        # 3) 更新 engine
        self._engine.update(dt, ai_provider=ai_provider)

        # 4) 对局结束处理
        if self._engine.match.end_reason is not None and not self._end_handled:
            self._end_delay += dt
            # 2 秒展示后保存并进入下一局（无尽模式）
            if self._end_delay >= 2.0:
                self._end_handled = True
                self._handle_match_end()

    # -------------------------------------------------
    # 绘制
    # -------------------------------------------------
    def draw(self, screen: pygame.Surface) -> None:
        if self._engine is None:
            return
        self._engine.draw(screen)

        # 结束遮罩 + 文字
        if self._engine.match.end_reason is not None:
            s = screen.get_size()
            overlay = pygame.Surface(s, pygame.SRCALPHA)
            overlay.fill((0, 0, 0, 140))
            screen.blit(overlay, (0, 0))
            try:
                font = pygame.font.SysFont("microsoftyahei,arial", 56)
                txt = font.render("本局结束", True, (240, 240, 250))
                screen.blit(txt, (s[0] // 2 - txt.get_width() // 2, s[1] // 2 - 60))
                reason = self._engine.match.end_reason.value
                sub = pygame.font.SysFont("microsoftyahei,arial", 26).render(
                    f"将自动进入下一局... ({reason})", True, (220, 220, 230)
                )
                screen.blit(sub, (s[0] // 2 - sub.get_width() // 2, s[1] // 2 + 10))
            except Exception:  # noqa: BLE001
                pass

    # =====================================================
    # 内部
    # =====================================================
    def _build_player_input(self) -> TankInput:
        move_x = 0
        move_y = 0
        # WASD
        if self._keys_now.get(pygame.K_a): move_x -= 1
        if self._keys_now.get(pygame.K_d): move_x += 1
        if self._keys_now.get(pygame.K_w): move_y -= 1
        if self._keys_now.get(pygame.K_s): move_y += 1
        # 方向键也可用
        if self._keys_now.get(pygame.K_LEFT): move_x -= 1
        if self._keys_now.get(pygame.K_RIGHT): move_x += 1
        if self._keys_now.get(pygame.K_UP): move_y -= 1
        if self._keys_now.get(pygame.K_DOWN): move_y += 1
        # 射击：Q / 空格
        fire = bool(self._keys_now.get(pygame.K_q) or self._keys_now.get(pygame.K_SPACE))
        return TankInput(move_x=move_x, move_y=move_y, fire=fire)

    def _save_record_if_needed(self, manual_exit: bool = False) -> None:
        if self._engine is None or self.ctx is None:
            return
        player = self._engine.match.tanks.get(self._player_tank_id or -1)
        if player is None:
            return
        match = self._engine.match
        # 是否胜利
        if match.winner_team_id is None:
            win = False
        elif match.mode == GameMode.FREE_FOR_ALL:
            win = (match.winner_team_id == player.id)
        else:
            win = (match.winner_team_id == player.team)
        # 对手 / 队友代号（仅 FFA 对手=所有人；组队时再分）
        teammates = [t.codename for t in match.tanks.values()
                     if t.id != player.id and t.team == player.team]
        opponents = [t.codename for t in match.tanks.values()
                     if t.id != player.id and t.team != player.team]

        rec = BattleRecord(
            win=win,
            kills=player.kills,
            survival=1 if player.alive else 0,
            max_streak=0,  # 占位，后续连杀统计
            duration_sec=int(match.duration_sec),
            mode=match.mode.value,
            is_online=self.is_online,
            teammates=teammates,
            opponents=opponents,
        )
        try:
            self.ctx.records_store.add(rec)
            if self.ctx.event_bus:
                self.ctx.event_bus.publish(EventType.RECORD_SAVED, record=rec)
        except Exception:  # noqa: BLE001
            self.ctx.logger.exception("保存战绩失败")

    def _handle_match_end(self) -> None:
        # 保存战绩
        self._save_record_if_needed()
        assert self.ctx is not None and self.ctx.scene_manager is not None
        # 无尽模式：下一局，难度递增
        next_diff = Match.next_difficulty(self.ai_difficulty)
        self.ctx.logger.info(
            f"无尽下一局：round={self.endless_round + 1}, 难度 {self.ai_difficulty.value} -> {next_diff.value}"
        )
        self.ctx.scene_manager.switch(
            BattleScene,
            total_players=self.total_players,
            ai_difficulty=next_diff,
            mode=self.mode,
            maze_size=self.maze_size,
            is_online=self.is_online,
            endless_round=self.endless_round + 1,
        )
