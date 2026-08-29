"""
碰撞与反弹工具
================

提供：
1. AABB 矩形重叠检测
2. 坦克/炮弹对应的 Pygame Rect 构造
3. 炮弹撞墙向量反射（按水平/垂直墙翻转 x/y 分量）
4. 坦克与墙体滑动碰撞（撞墙不卡角，沿允许的分量继续移动）

注意：本游戏不需要物理引擎，这里是轻量自实现，不引入 Pymunk。
"""

from __future__ import annotations

import math
from typing import List, Tuple

import pygame


# ============================================================
# AABB 重叠
# ============================================================
def aabb_overlap(a: pygame.Rect, b: pygame.Rect) -> bool:
    """两个矩形是否重叠（AABB 碰撞）。"""
    return (
        a.x < b.x + b.width
        and a.x + a.width > b.x
        and a.y < b.y + b.height
        and a.y + a.height > b.y
    )


# ============================================================
# Rect 构造
# ============================================================
def rect_from_tank(x: float, y: float, size: int) -> pygame.Rect:
    """根据坦克中心 (x, y) 与正方形边长 size，返回碰撞用 Rect。"""
    half = size / 2
    return pygame.Rect(int(x - half), int(y - half), size, size)


def rect_from_projectile(x: float, y: float, size: int) -> pygame.Rect:
    """根据炮弹中心 (x, y) 与直径/边长 size，返回碰撞用 Rect。"""
    half = size / 2
    return pygame.Rect(int(x - half), int(y - half), size, size)


# ============================================================
# 炮弹反弹
# ============================================================
def reflect_vector(
    vx: float, vy: float, horizontal_wall: bool, vertical_wall: bool
) -> Tuple[float, float]:
    """
    炮弹撞墙后按物理规则反弹：
    - 撞到水平墙（上下表面）：翻转 y 分量
    - 撞到垂直墙（左右表面）：翻转 x 分量
    - 同时撞到（比如正好撞在墙角）：两个分量都翻转

    Args:
        vx, vy: 炮弹速度向量
        horizontal_wall: 是否撞到水平墙
        vertical_wall: 是否撞到垂直墙

    Returns:
        反弹后的 (vx, vy)
    """
    nvx, nvy = vx, vy
    if vertical_wall:
        nvx = -vx
    if horizontal_wall:
        nvy = -vy
    return nvx, nvy


# ============================================================
# 坦克滑动碰撞（分轴尝试移动）
# ============================================================
def slide_collision(
    tank_rect: pygame.Rect,
    dx: float, dy: float,
    walls: List[pygame.Rect],
) -> Tuple[float, float]:
    """
    计算坦克移动 (dx, dy) 后，与墙体碰撞且不卡角的实际位移。

    策略：
        1. 先尝试 x 方向移动整段，若撞墙则撤销 x 分量
        2. 再尝试 y 方向移动整段，若撞墙则撤销 y 分量
        3. 返回最终实际位移

    这样坦克沿墙移动时不会被"卡"住。

    Returns:
        (actual_dx, actual_dy) 实际可移动的位移
    """
    actual_dx = dx
    actual_dy = dy

    # x 方向
    if dx != 0:
        r = tank_rect.move(int(round(dx)), 0)
        for w in walls:
            if aabb_overlap(r, w):
                actual_dx = 0.0
                break

    # y 方向（基于新的 x）
    if dy != 0:
        r = tank_rect.move(int(round(actual_dx)), int(round(dy)))
        for w in walls:
            if aabb_overlap(r, w):
                actual_dy = 0.0
                break

    return actual_dx, actual_dy


# ============================================================
# 工具：两点距离、向量归一
# ============================================================
def distance(ax: float, ay: float, bx: float, by: float) -> float:
    """两点欧氏距离。"""
    return math.hypot(ax - bx, ay - by)


def normalize(vx: float, vy: float, epsilon: float = 1e-6) -> Tuple[float, float]:
    """向量归一。零向量返回 (0,0)。"""
    m = math.hypot(vx, vy)
    if m < epsilon:
        return 0.0, 0.0
    return vx / m, vy / m
