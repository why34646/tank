"""
战斗引擎
===========

BattleEngine 负责驱动一局战斗的每帧循环：
1. 处理输入（玩家 + AI）→ 更新坦克
2. 发射炮弹 → 加入炮弹列表
3. 更新所有炮弹 → 墙体反射
4. 炮弹 vs 坦克碰撞 → 击毁判定、击杀统计
5. 绘制：迷宫地面+墙体、坦克、炮弹、HUD 占位

架构阶段：保证最小可运行——能加载迷宫、生成 1 个玩家坦克、发射/反弹不崩溃。
"""

from __future__ import annotations

import math
from typing import Callable, Dict, List, Optional

import pygame

from ..config.settings import Settings
from ..core.event_bus import EventBus, EventType, GameEvent
from ..core.exceptions import GameStateError
from ..utils.collision import aabb_overlap
from ..utils.logger import get_logger
from .match import Match, MatchEndReason, MatchState
from .maze import Maze, draw_maze, generate_maze
from .projectile import Projectile
from .tank import Tank, TankInput

logger = get_logger(__name__)


class BattleEngine:
    """单局战斗引擎。"""

    def __init__(
        self,
        settings: Settings,
        match: Match,
        event_bus: Optional[EventBus] = None,
    ) -> None:
        self.settings = settings
        self.match = match
        self.event_bus = event_bus
        self.maze: Maze = generate_maze(settings, match.maze_size_key)

        # 炮弹列表
        self.projectiles: List[Projectile] = []
        self._next_projectile_id: int = 1

        # 外部每帧输入：tank_id -> TankInput
        self.input_overrides: Dict[int, TankInput] = {}

        # 初始化：把坦克放到出生点（顺序按 tank id，出生点不够循环取）
        spawns = self.maze.spawn_points
        for i, tank in enumerate(self.match.tanks.values()):
            sx, sy = spawns[i % len(spawns)]
            tank.x, tank.y = float(sx), float(sy)

    # -------------------------------------------------
    # 输入
    # -------------------------------------------------
    def set_tank_input(self, tank_id: int, inp: TankInput) -> None:
        """外部（玩家/AI/网络）设置某坦克本帧输入。下一次 update 生效后清空。"""
        self.input_overrides[tank_id] = inp

    # -------------------------------------------------
    # 生命周期
    # -------------------------------------------------
    def start(self) -> None:
        if self.match.state != MatchState.PREPARING:
            raise GameStateError(f"对局状态异常，无法 start: {self.match.state}")
        self.match.start()
        logger.info(f"对局开始 id={self.match.id} 模式={self.match.mode.value}")
        if self.event_bus:
            self.event_bus.publish(GameEvent(EventType.GAME_START, match=self.match))

    def stop(self, reason: MatchEndReason = MatchEndReason.MANUAL_STOP) -> None:
        if self.match.state == MatchState.ENDED:
            return
        self.match.state = MatchState.ENDED
        self.match.end_reason = reason
        logger.info(f"对局结束 id={self.match.id} 原因={reason.value}")
        if self.event_bus:
            self.event_bus.publish(GameEvent(EventType.GAME_END, match=self.match, reason=reason))

    # -------------------------------------------------
    # 每帧更新
    # -------------------------------------------------
    def update(self, dt: float, ai_provider: Optional[Callable[[Tank, "BattleEngine"], TankInput]] = None) -> None:
        """
        一帧更新。
        Args:
            dt: 秒
            ai_provider: AI 输入提供者（tank + engine -> TankInput）。None 表示本帧无 AI。
        """
        if self.match.state != MatchState.PLAYING:
            return

        self.match.tick(dt)

        walls = self.maze.walls
        settings = self.settings

        # 1) 坦克输入 + 更新
        for tank in self.match.tanks.values():
            inp = self.input_overrides.pop(tank.id, None)
            if inp is None:
                if tank.is_ai and ai_provider is not None:
                    inp = ai_provider(tank, self)
                else:
                    inp = TankInput()
            tank.update(dt, inp, walls)

            # 开火
            if inp.fire and tank.can_fire():
                self._fire(tank)

        # 2) 炮弹更新
        for p in self.projectiles:
            p.update(dt, walls)

        # 3) 炮弹 vs 坦克碰撞（击中即击毁）
        self._resolve_projectile_hits()

        # 4) 清理死亡炮弹
        self.projectiles = [p for p in self.projectiles if p.alive]

        # 5) 结束判定
        end_reason = self.match.check_end()
        if end_reason is not None:
            self.stop(end_reason)

    # -------------------------------------------------
    # 开火
    # -------------------------------------------------
    def _fire(self, tank: Tank) -> None:
        tank.consume_ammo()
        angle = tank.angle
        vx = math.cos(angle) * self.settings.PROJECTILE_SPEED
        vy = math.sin(angle) * self.settings.PROJECTILE_SPEED
        # 炮口位置：坦克中心 + 炮管长度
        offset = tank.size * 0.9
        x = tank.x + math.cos(angle) * offset
        y = tank.y + math.sin(angle) * offset
        p = Projectile(tank.id, x, y, vx, vy, self.settings)
        self.projectiles.append(p)
        if self.event_bus:
            self.event_bus.publish(GameEvent(EventType.PROJECTILE_FIRED, tank=tank, projectile=p))

    # -------------------------------------------------
    # 碰撞：炮弹 vs 坦克
    # -------------------------------------------------
    def _resolve_projectile_hits(self) -> None:
        for p in self.projectiles:
            if not p.alive:
                continue
            p_rect = p.rect
            for tank in self.match.tanks.values():
                if not tank.alive:
                    continue
                # 允许跳过"自己发射的炮弹近距离自残"——若炮弹是自己发射且距离 < 阈值，忽略
                # 简单规则：任何炮弹都能击中任何坦克（符合原版反弹致死的乐趣）
                if aabb_overlap(p_rect, tank.rect):
                    tank.alive = False
                    p.alive = False
                    # 击杀统计（自杀不算）
                    killer = self.match.tanks.get(p.owner_id)
                    if killer is not None and killer.id != tank.id:
                        killer.kills += 1
                    if self.event_bus:
                        self.event_bus.publish(GameEvent(
                            EventType.TANK_DESTROYED,
                            victim=tank,
                            killer=killer,
                        ))
                    break

    # -------------------------------------------------
    # 绘制
    # -------------------------------------------------
    def draw(self, screen: pygame.Surface) -> None:
        s = self.settings
        # 迷宫
        draw_maze(screen, self.maze, s.WALL_COLOR, s.FLOOR_COLOR)
        # 炮弹（先画，再画坦克盖住更自然）
        for p in self.projectiles:
            p.draw(screen)
        # 坦克
        for t in self.match.tanks.values():
            t.draw(screen)
        # HUD 占位：右上角存活/比分简要
        self._draw_hud(screen)

    def _draw_hud(self, screen: pygame.Surface) -> None:
        """简单 HUD：显示 FPS 占位 + 剩余人数。"""
        alive_count = sum(1 for t in self.match.tanks.values() if t.alive)
        try:
            font = pygame.font.SysFont("microsoftyahei,arial", 22)
            text = font.render(f"存活: {alive_count}/{len(self.match.tanks)}  时间: {int(self.match.duration_sec)}s",
                               True, self.settings.TEXT_COLOR)
            screen.blit(text, (screen.get_width() - text.get_width() - 24, 20))
        except Exception:  # noqa: BLE001
            pass
