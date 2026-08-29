"""
炮弹实体
===========

职责：
- 保存位置、速度向量、剩余生命时间、发射者 id
- 每帧更新位置，墙体 AABB 碰撞后反射（翻转 x/y 分量）
- 超出存活时间自动销毁
- 击中坦克的判定交给 BattleEngine（炮弹不直接引用 Tank 列表）
"""

from __future__ import annotations

from typing import List, Tuple

import pygame

from ..config.settings import Settings
from ..utils.collision import aabb_overlap, rect_from_projectile


class Projectile:
    """炮弹实体。"""

    def __init__(
        self,
        owner_tank_id: int,
        x: float,
        y: float,
        vx: float,
        vy: float,
        settings: Settings,
    ) -> None:
        self.owner_id = owner_tank_id
        self.x = float(x)
        self.y = float(y)
        self.vx = float(vx)
        self.vy = float(vy)
        self.size: int = settings.PROJECTILE_SIZE
        self.speed: float = settings.PROJECTILE_SPEED
        self.lifetime: float = settings.PROJECTILE_LIFETIME
        self.alive: bool = True
        self.color: Tuple[int, int, int] = (245, 230, 100)

        # 反弹次数上限（避免炮弹永远反弹，超过则销毁；默认无上限，保留字段）
        self.max_bounces: int = 999
        self._bounces: int = 0

    # -------------------------------------------------
    # 公共
    # -------------------------------------------------
    @property
    def rect(self) -> pygame.Rect:
        return rect_from_projectile(self.x, self.y, self.size)

    # -------------------------------------------------
    # 每帧更新
    # -------------------------------------------------
    def update(self, dt: float, walls: List[pygame.Rect]) -> None:
        """
        按帧移动：子步长更新，避免高速穿透（tunneling）。
        每个子步内先 x 后 y 分别做碰撞反射。
        """
        if not self.alive:
            return

        # 生命时间
        self.lifetime -= dt
        if self.lifetime <= 0.0:
            self.alive = False
            return

        # 本帧总位移长度 → 决定子步数，保证每子步位移不超过炮弹直径的 1/2（防穿墙）
        total_dx = self.vx * dt
        total_dy = self.vy * dt
        total_dist = (total_dx * total_dx + total_dy * total_dy) ** 0.5
        max_per_step = max(1.0, self.size * 0.5)
        import math as _m
        substeps = max(1, int(_m.ceil(total_dist / max_per_step)))
        sub_dt = dt / substeps

        for _ in range(substeps):
            if not self.alive:
                return

            # --- x 方向子步 ---
            sx = self.vx * sub_dt
            if sx != 0.0:
                old_x = self.x
                self.x += sx
                if any(aabb_overlap(self.rect, w) for w in walls):
                    self.x = old_x
                    self.vx = -self.vx
                    self._bounces += 1

            # --- y 方向子步 ---
            sy = self.vy * sub_dt
            if sy != 0.0:
                old_y = self.y
                self.y += sy
                if any(aabb_overlap(self.rect, w) for w in walls):
                    self.y = old_y
                    self.vy = -self.vy
                    self._bounces += 1

            if self._bounces > self.max_bounces:
                self.alive = False
                return

    # -------------------------------------------------
    # 绘制
    # -------------------------------------------------
    def draw(self, screen: pygame.Surface) -> None:
        if not self.alive:
            return
        r = self.rect
        pygame.draw.circle(
            screen,
            self.color,
            (r.centerx, r.centery),
            max(1, r.width // 2),
        )
        # 描边
        pygame.draw.circle(
            screen,
            (60, 60, 50),
            (r.centerx, r.centery),
            max(1, r.width // 2),
            width=1,
        )
