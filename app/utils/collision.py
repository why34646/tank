"""
碰撞与反弹工具
================

提供：
1. AABB 矩形重叠检测
2. 坦克/炮弹对应的 Pygame Rect 构造
3. 炮弹撞墙向量反射（按水平/垂直墙翻转 x/y 分量）
4. 坦克与墙体滑动碰撞（撞墙不卡角，沿允许的分量继续移动）
5. OBB（旋转矩形）vs AABB SAT 碰撞检测（坦克主体+炮管）

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
        1. 先处理当前 rect 与墙的微小重叠（去重叠 depenetration），
           沿最小重叠方向把 rect 推出墙外，累计推出偏移。
           —— 这是"被墙吸住"的根因修复：rect_from_tank 用 int 取整，
              贴墙时常有 1-2px 重叠，导致后续沿墙方向的移动也被误判为撞墙。
        2. 基于已去重叠的 rect 做分轴滑动：先试 x，撞墙撤销 x；再试 y，撞墙撤销 y。
           由于 rect 已不重叠，沿墙方向的分量可以正常移动 → 擦墙滑动。
        3. 返回 (推出偏移 + 实际移动偏移) 的总位移，让调用方一次性应用。

    Returns:
        (actual_dx, actual_dy) 实际可移动的总位移（含去重叠）
    """
    actual_dx = 0.0
    actual_dy = 0.0

    # ---- 1) 去重叠：把 rect 从当前与墙的重叠中推出 ----
    dep_dx = 0.0
    dep_dy = 0.0
    r = tank_rect.copy()
    # 多次迭代：一次推出可能在新位置又和其他墙重叠（虽然极少见）
    for _ in range(3):
        dep_x = dep_y = 0.0
        for w in walls:
            if r.colliderect(w):
                overlap_left = r.right - w.left     # 向右推
                overlap_right = w.right - r.left    # 向左推
                overlap_top = r.bottom - w.top      # 向下推
                overlap_bottom = w.bottom - r.top   # 向上推
                mn = min(overlap_left, overlap_right, overlap_top, overlap_bottom)
                if mn == overlap_left:
                    r.left -= overlap_left
                    dep_x -= overlap_left
                elif mn == overlap_right:
                    r.left += overlap_right
                    dep_x += overlap_right
                elif mn == overlap_top:
                    r.top -= overlap_top
                    dep_y -= overlap_top
                else:
                    r.top += overlap_bottom
                    dep_y += overlap_bottom
        if dep_x == 0.0 and dep_y == 0.0:
            break
        dep_dx += dep_x
        dep_dy += dep_y

    # ---- 2) 分轴滑动（基于已去重叠的 r）----
    # x 方向
    if dx != 0:
        r_new = r.move(int(round(dx)), 0)
        hit = False
        for w in walls:
            if r_new.colliderect(w):
                hit = True
                break
        if not hit:
            actual_dx = dx
            r = r_new

    # y 方向（基于新的 x）
    if dy != 0:
        r_new = r.move(0, int(round(dy)))
        hit = False
        for w in walls:
            if r_new.colliderect(w):
                hit = True
                break
        if not hit:
            actual_dy = dy
            r = r_new

    return dep_dx + actual_dx, dep_dy + actual_dy


# ============================================================
# OBB（旋转矩形）vs AABB SAT 碰撞检测
# ============================================================
def obb_vertices(
    cx: float, cy: float, w: float, h: float, angle: float,
) -> List[Tuple[float, float]]:
    """
    构造以 (cx,cy) 为中心、宽 w、高 h、旋转 angle 弧度的 OBB 4 个顶点。
    angle=0 时矩形是 AABB（w 沿 x 轴，h 沿 y 轴）。
    顶点顺序：左上→右上→右下→左下（CCW）。
    """
    hw, hh = w / 2.0, h / 2.0
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    local = [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]
    verts = []
    for lx, ly in local:
        rx = lx * cos_a - ly * sin_a + cx
        ry = lx * sin_a + ly * cos_a + cy
        verts.append((rx, ry))
    return verts


def obb_vs_aabb_collide(
    obb: List[Tuple[float, float]], aabb: pygame.Rect,
) -> bool:
    """
    SAT 检测 OBB（4 顶点）与 AABB 是否重叠。
    返回 True=碰撞，False=分离。
    """
    aabb_v = [
        (float(aabb.left), float(aabb.top)),
        (float(aabb.right), float(aabb.top)),
        (float(aabb.right), float(aabb.bottom)),
        (float(aabb.left), float(aabb.bottom)),
    ]
    # 收集两个多边形的 8 根边法线
    axes: List[Tuple[float, float]] = []
    for verts in [obb, aabb_v]:
        for i in range(4):
            v1, v2 = verts[i], verts[(i + 1) % 4]
            ex, ey = v2[0] - v1[0], v2[1] - v1[1]
            el = math.hypot(ex, ey) or 1.0
            axes.append((ey / el, -ex / el))

    for ax, ay in axes:
        mn1, mx1 = float("inf"), float("-inf")
        for vx, vy in obb:
            p = vx * ax + vy * ay
            mn1 = min(mn1, p)
            mx1 = max(mx1, p)
        mn2, mx2 = float("inf"), float("-inf")
        for vx, vy in aabb_v:
            p = vx * ax + vy * ay
            mn2 = min(mn2, p)
            mx2 = max(mx2, p)
        if mx1 <= mn2 or mx2 <= mn1:
            return False
    return True


def slide_collision_obb(
    obbs: List[List[Tuple[float, float]]],
    dx: float, dy: float,
    walls: List[pygame.Rect],
) -> Tuple[float, float]:
    """
    把多个 OBB 整体移动 (dx, dy)，分轴滑动（和 slide_collision 策略一致）。
    只要任何一个 OBB 与任何一堵墙碰撞，该轴就被撤销。
    返回实际位移 (actual_dx, actual_dy)。

    注意：不做预去重叠（正常帧间移动步长小，不会穿进墙）。
    如果需要处理已在墙里的情况，建议用更小步长迭代多次。
    """
    actual_dx, actual_dy = 0.0, 0.0

    if dx != 0:
        new_obbs = [[(vx + dx, vy) for vx, vy in obb] for obb in obbs]
        hit = any(obb_vs_aabb_collide(obb, w) for obb in new_obbs for w in walls)
        if not hit:
            actual_dx = dx
            obbs[:] = new_obbs

    if dy != 0:
        new_obbs = [[(vx, vy + dy) for vx, vy in obb] for obb in obbs]
        hit = any(obb_vs_aabb_collide(obb, w) for obb in new_obbs for w in walls)
        if not hit:
            actual_dy = dy
            obbs[:] = new_obbs

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


def circle_vs_aabb_collide(
    cx: float, cy: float, radius: float, aabb: pygame.Rect
) -> bool:
    """圆 vs 轴对齐矩形碰撞检测。"""
    # 找到矩形上离圆心最近的点
    closest_x = max(aabb.left, min(cx, aabb.right))
    closest_y = max(aabb.top, min(cy, aabb.bottom))
    dx = cx - closest_x
    dy = cy - closest_y
    return (dx * dx + dy * dy) <= radius * radius


def _point_in_poly(px: float, py: float, poly: List[Tuple[float, float]]) -> bool:
    """射线法判断点是否在凸/凹多边形内。"""
    n = len(poly)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > py) != (yj > py)) and (
            px < (xj - xi) * (py - yi) / (yj - yi) + xi
        ):
            inside = not inside
        j = i
    return inside


def circle_vs_obb_collide(
    cx: float, cy: float, radius: float, obb: List[Tuple[float, float]]
) -> bool:
    """圆 vs 任意凸四边形（OBB 顶点列表）碰撞检测。"""
    # 圆心在多边形内 → 碰撞
    if _point_in_poly(cx, cy, obb):
        return True
    # 圆心在多边形外 → 检查圆心到每条边的最小距离
    r2 = radius * radius
    n = len(obb)
    for i in range(n):
        ax, ay = obb[i]
        bx, by = obb[(i + 1) % n]
        # 线段上离圆心最近的点
        abx = bx - ax
        aby = by - ay
        t = ((cx - ax) * abx + (cy - ay) * aby) / (abx * abx + aby * aby)
        t = max(0.0, min(1.0, t))
        px = ax + t * abx
        py = ay + t * aby
        dx = cx - px
        dy = cy - py
        if dx * dx + dy * dy <= r2:
            return True
    return False
