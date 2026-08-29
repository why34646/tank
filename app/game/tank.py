"""
坦克实体
===========

职责：
- 保存位置、朝向（角度）、速度、弹药数、击杀等状态
- 每帧根据输入（InputState）更新位置与朝向，调用工具做墙体碰撞
- 弹药补充计时、是否可开火

不负责：
- 炮弹发射后的生命周期（交给 Projectile / BattleEngine）
- 击毁爆炸表现（交给 BattleEngine）
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

import pygame

from ..config.settings import Settings
from ..core.constants import MouseKeyMode
from ..utils.collision import rect_from_tank, slide_collision


@dataclass
class TankInput:
    """
    单帧玩家/AI 输入。
    方向：-1/0/1；fire: bool
    aim_angle: 仅 MOUSE 模式使用，弧度
    """
    move_x: int = 0          # -1 / 0 / 1（A/D 或 ←/→）
    move_y: int = 0          # -1 / 0 / 1（W/S 或 ↑/↓）
    fire: bool = False
    aim_angle: Optional[float] = None  # None 表示按 movement 方向


class Tank:
    """坦克实体。"""

    def __init__(
        self,
        tank_id: int,
        codename: str,
        color: Tuple[int, int, int],
        spawn_x: float,
        spawn_y: float,
        team: int,
        settings: Settings,
        control_mode: MouseKeyMode = MouseKeyMode.WASDQ,
        is_ai: bool = False,
    ) -> None:
        self.id = tank_id
        self.codename = codename
        self.color = color
        self.team = team                 # 0=FFA，其余为队伍号 1/2/3
        self.is_ai = is_ai
        self.control_mode = control_mode

        self.settings = settings
        self.size: int = settings.TANK_SIZE

        # 位置（中心）、朝向角度（弧度，0=向右，PI/2=向下）
        self.x = float(spawn_x)
        self.y = float(spawn_y)
        self.angle: float = 0.0

        # 状态
        self.alive: bool = True
        self.ammo: int = settings.INITIAL_AMMO
        self._reload_timer: float = 0.0  # 弹药补充计时
        self.kills: int = 0

    # -------------------------------------------------
    # 公共 API
    # -------------------------------------------------
    @property
    def rect(self) -> pygame.Rect:
        """当前位置的碰撞矩形。"""
        return rect_from_tank(self.x, self.y, self.size)

    def can_fire(self) -> bool:
        return self.alive and self.ammo > 0

    def consume_ammo(self) -> None:
        """发射时扣一发。"""
        if self.ammo > 0:
            self.ammo -= 1

    def reset_for_new_round(self, spawn_x: float, spawn_y: float) -> None:
        """新一局重置（保留击杀统计）。"""
        self.x = float(spawn_x)
        self.y = float(spawn_y)
        self.angle = 0.0
        self.alive = True
        self.ammo = self.settings.INITIAL_AMMO
        self._reload_timer = 0.0

    # -------------------------------------------------
    # 每帧更新
    # -------------------------------------------------
    def update(self, dt: float, input_: TankInput, walls: List[pygame.Rect]) -> None:
        """
        根据输入更新一帧。

        Args:
            dt: 秒
            input_: 本帧输入
            walls: 迷宫所有墙体矩形
        """
        if not self.alive:
            # 死亡不更新位置，弹药仍补充（复活即满）
            self._tick_reload(dt)
            return

        # 1) 更新朝向
        if self.control_mode == MouseKeyMode.MOUSE and input_.aim_angle is not None:
            self.angle = float(input_.aim_angle)
        elif input_.move_x != 0 or input_.move_y != 0:
            # 按移动方向面向
            self.angle = math.atan2(input_.move_y, input_.move_x)

        # 2) 归一化方向向量（斜向不加速）
        dx = float(input_.move_x)
        dy = float(input_.move_y)
        if dx != 0.0 or dy != 0.0:
            m = math.hypot(dx, dy)
            dx /= m
            dy /= m
        step = self.settings.TANK_SPEED * dt
        raw_dx = dx * step
        raw_dy = dy * step

        # 3) 滑动碰撞，得到实际位移
        r = self.rect
        actual_dx, actual_dy = slide_collision(r, raw_dx, raw_dy, walls)
        self.x += actual_dx
        self.y += actual_dy

        # 4) 弹药补充计时
        self._tick_reload(dt)

    # -------------------------------------------------
    # 绘制
    # -------------------------------------------------
    def draw(self, screen: pygame.Surface) -> None:
        """
        用简单几何图形绘制坦克（长方形车身 + 炮管）。
        先占位，后续可替换为 Sprite 或美术资源。
        """
        if not self.alive:
            return
        s = self.size
        cx, cy = self.x, self.y

        # 车身：正方形（带颜色 + 深色描边）
        body_rect = pygame.Rect(0, 0, s, s)
        body_rect.center = (int(cx), int(cy))
        pygame.draw.rect(screen, self.color, body_rect, border_radius=4)
        pygame.draw.rect(screen, (30, 30, 40), body_rect, width=2, border_radius=4)

        # 炮管：从中心沿 angle 方向画一段矩形
        barrel_len = int(s * 0.85)
        barrel_w = max(4, s // 6)
        ex = cx + math.cos(self.angle) * barrel_len
        ey = cy + math.sin(self.angle) * barrel_len
        pygame.draw.line(screen, (40, 40, 50), (cx, cy), (ex, ey), width=barrel_w)
        pygame.draw.line(screen, (220, 220, 230), (cx, cy), (ex, ey), width=max(2, barrel_w // 3))

    # -------------------------------------------------
    # 内部
    # -------------------------------------------------
    def _tick_reload(self, dt: float) -> None:
        if self.ammo >= self.settings.INITIAL_AMMO:
            self._reload_timer = 0.0
            return
        self._reload_timer += dt
        while self._reload_timer >= self.settings.AMMO_RELOAD_INTERVAL:
            self._reload_timer -= self.settings.AMMO_RELOAD_INTERVAL
            if self.ammo < self.settings.INITIAL_AMMO:
                self.ammo += 1
            else:
                self._reload_timer = 0.0
                break
