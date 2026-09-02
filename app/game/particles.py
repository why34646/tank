"""坦克爆炸粒子系统。

三类粒子：
1. DebrisFragment — 不规则多边形碎片，碰墙停住，带旋转
2. MainSmoke      — 静止圆形烟雾（坦克主体附近）
3. TrailSmoke     — 静止圆形小烟雾（碎片移动尾迹）
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import ClassVar, List, Optional, Tuple

import pygame

Vec2 = Tuple[float, float]
Color = Tuple[int, int, int]


# ============================================================
# 工具函数
# ============================================================

def _lerp_color(c1: Color, c2: Color, t: float) -> Color:
    """t ∈ [0,1]：0→c1, 1→c2"""
    t = max(0.0, min(1.0, t))
    return (
        int(c1[0] + (c2[0] - c1[0]) * t),
        int(c1[1] + (c2[1] - c1[1]) * t),
        int(c1[2] + (c2[2] - c1[2]) * t),
    )


def _polygon_area(vertices: List[Vec2]) -> float:
    """鞋带公式算多边形面积（顶点顺序无关）。"""
    n = len(vertices)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x1, y1 = vertices[i]
        x2, y2 = vertices[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) * 0.5


def _generate_irregular_polygon(
    n_sides: int,
    target_area: float,
    jitter_radius: float = 0.3,
    jitter_angle: float = 0.25,
) -> List[Vec2]:
    """
    生成一个不规则多边形，绕原点中心，总面积接近 target_area。
    n_sides: 3 / 4 / 5
    target_area: 像素面积
    返回顶点列表（相对原点）。
    """
    # 1) 先按正多边形生成顶点
    #    正 n 边形面积 = (1/4) * n * r^2 * sin(2π/n)
    if n_sides == 3:
        base_angle = math.pi * 2 / 3  # 120°
        # 正三角形内接圆半径 r = sqrt(area / (3 * sqrt(3) / 4)) * ...
        # 简化：先设 r=1，扰动，再缩放面积
        r0 = 1.0
        angles = [-math.pi / 2 + i * base_angle for i in range(3)]  # 顶端开始
    elif n_sides == 4:
        base_angle = math.pi / 2  # 90°
        r0 = 1.0
        angles = [-math.pi / 4 + i * base_angle for i in range(4)]  # 菱形
    else:  # 5
        base_angle = math.pi * 2 / 5
        r0 = 1.0
        angles = [-math.pi / 2 + i * base_angle for i in range(5)]

    # 2) 扰动每个顶点
    verts: List[Vec2] = []
    for a in angles:
        r = r0 * (1.0 + random.uniform(-jitter_radius, jitter_radius))
        aa = a + random.uniform(-jitter_angle, jitter_angle)
        verts.append((r * math.cos(aa), r * math.sin(aa)))

    # 3) 算当前面积，缩放顶点半径到目标面积
    cur_area = _polygon_area(verts)
    if cur_area > 0:
        scale = math.sqrt(target_area / cur_area)
        verts = [(v[0] * scale, v[1] * scale) for v in verts]

    return verts


# ============================================================
# 粒子类
# ============================================================

@dataclass
class DebrisFragment:
    """不规则多边形碎片。"""

    # 类常量：移动超过此距离才生成一个尾迹（单位像素，按 ParticleManager.scale 缩放）
    TRAIL_MIN_DIST: ClassVar[float] = 5.0

    x: float
    y: float
    vx: float
    vy: float
    color: Color
    vertices: List[Vec2]  # 相对中心的顶点
    angle: float = 0.0           # 当前旋转角
    angular_vel: float = 0.0     # 角速度 rad/s
    life: float = 2.0
    max_life: float = 2.0
    damping: float = 0.96        # 每帧阻尼系数（乘速度）
    stopped: bool = False        # 碰墙停住
    _last_trail_dist: float = 0.0  # 尾迹触发用

    @property
    def alive(self) -> bool:
        return self.life > 0.0

    def update(self, dt: float, walls: List[pygame.Rect]) -> None:
        self.life -= dt
        if self.stopped:
            return

        # 速度阻尼
        self.vx *= self.damping
        self.vy *= self.damping

        # 旋转
        self.angle += self.angular_vel * dt

        # 移动 + 墙碰撞（简单 AABB 检测，用碎片外接圆粗估）
        new_x = self.x + self.vx * dt
        new_y = self.y + self.vy * dt

        # 粗估碰撞半径（最大顶点距离中心）
        if not hasattr(self, "_collision_radius"):
            self._collision_radius = max(
                math.hypot(v[0], v[1]) for v in self.vertices
            ) * 0.7  # 稍保守，避免卡在墙角
        r = self._collision_radius

        rect = pygame.Rect(
            new_x - r, new_y - r, r * 2, r * 2
        )
        for w in walls:
            if rect.colliderect(w):
                self.stopped = True
                self.vx = 0.0
                self.vy = 0.0
                # 碰墙即停，不做精确贴墙（装饰性粒子无需滑动碰撞）
                self.x, self.y = new_x, new_y
                return

        # 记录位移用于尾迹触发
        self._last_trail_dist += math.hypot(
            (new_x - self.x), (new_y - self.y)
        )
        self.x = new_x
        self.y = new_y

    def consume_trail_trigger(self) -> bool:
        """返回 True 表示应该生成一个尾迹小烟雾，同时重置计数器。"""
        if self._last_trail_dist >= self.TRAIL_MIN_DIST:
            self._last_trail_dist = 0.0
            return True
        return False

    def get_draw_color(self) -> Color:
        """根据剩余生命返回当前颜色（坦克色 → (189,189,189)）。"""
        elapsed = self.max_life - self.life
        # 1.3s 后开始渐变（剩余 0.7s）
        fade_start = 1.3
        if elapsed < fade_start:
            return self.color
        t = min(1.0, (elapsed - fade_start) / (self.max_life - fade_start))
        return _lerp_color(self.color, (189, 189, 189), t)

    def get_rotated_vertices(self) -> List[Vec2]:
        """返回世界坐标下的旋转后顶点。"""
        cos_a = math.cos(self.angle)
        sin_a = math.sin(self.angle)
        result: List[Vec2] = []
        for v in self.vertices:
            wx = self.x + v[0] * cos_a - v[1] * sin_a
            wy = self.y + v[0] * sin_a + v[1] * cos_a
            result.append((wx, wy))
        return result

    def draw(self, screen: pygame.Surface) -> None:
        verts = self.get_rotated_vertices()
        color = self.get_draw_color()
        if len(verts) >= 3:
            pygame.draw.polygon(screen, color, verts)
            pygame.draw.polygon(screen, (0, 0, 0), verts, 1)


@dataclass
class MainSmoke:
    """静止圆形烟雾（坦克主体附近）——带 alpha 透明渐变。"""

    x: float
    y: float
    diameter: float = 120.0
    life: float = 1.5
    max_life: float = 1.5
    _surface: Optional[pygame.Surface] = None  # 缓存 SRCALPHA 画好的圆

    @property
    def alive(self) -> bool:
        return self.life > 0.0

    def update(self, dt: float) -> None:
        self.life -= dt

    def _get_rgba(self) -> Tuple[int, int, int, int]:
        """返回带 alpha 的颜色 (R,G,B,A)。"""
        base = (55,55,55)
        target = (189, 189, 189)
        elapsed = self.max_life - self.life
        fade_color_start = 1.0
        # 颜色渐变
        if elapsed < fade_color_start:
            rgb = base
        else:
            t = min(1.0, (elapsed - fade_color_start) / (self.max_life - fade_color_start))
            rgb = _lerp_color(base, target, t)
        # alpha 渐变：从一开始就带半透 (180)，到最终 0
        # 让 alpha 在整个生命周期内线性衰减
        progress = min(1.0, elapsed / self.max_life)  # 0→1 全程
        alpha = int(180 * (1.0 - progress))             # 180→0
        return (rgb[0], rgb[1], rgb[2], max(0, alpha))

    def draw(self, screen: pygame.Surface) -> None:
        d = int(self.diameter)
        rgba = self._get_rgba()
        if rgba[3] <= 0:
            return
        # 每帧重建 surface（烟雾 alpha/颜色每帧都在变，无法有效缓存）
        surf = pygame.Surface((d, d), pygame.SRCALPHA)
        pygame.draw.circle(surf, rgba, (d // 2, d // 2), d // 2)
        screen.blit(surf, (int(self.x) - d // 2, int(self.y) - d // 2))


@dataclass
class ProjectilePop:
    """炮弹寿命耗尽消失动画——圆形冲击波，从炮弹大小膨胀到 30，带 alpha + 颜色渐变。"""

    x: float
    y: float
    vx: float
    vy: float
    diameter_start: float = 14.0   # 初始 = 炮弹直径
    diameter_end: float = 45.0     # 最终
    color: Color = (0, 0, 0)       # 炮弹颜色
    life: float = 0.6
    max_life: float = 0.6
    damping: float = 0.95          # 让炮弹速度快速归零

    @property
    def alive(self) -> bool:
        return self.life > 0.0

    def update(self, dt: float) -> None:
        self.life -= dt
        # 阻尼 + 移动
        self.vx *= self.damping
        self.vy *= self.damping
        self.x += self.vx * dt
        self.y += self.vy * dt

    def _get_current_diameter(self) -> float:
        """线性从 start 膨胀到 end。"""
        progress = min(1.0, (self.max_life - self.life) / self.max_life)
        return self.diameter_start + (self.diameter_end - self.diameter_start) * progress

    def _get_rgba(self) -> Tuple[int, int, int, int]:
        """颜色从炮弹色渐变到 (189,189,189)，alpha 从 200 线性到 0。"""
        progress = min(1.0, (self.max_life - self.life) / self.max_life)
        # 颜色全程渐变
        rgb = _lerp_color(self.color, (189, 189, 189), progress)
        # alpha 线性衰减
        alpha = int(200 * (1.0 - progress))
        return (rgb[0], rgb[1], rgb[2], max(0, alpha))

    def draw(self, screen: pygame.Surface) -> None:
        rgba = self._get_rgba()
        if rgba[3] <= 0:
            return
        d = int(self._get_current_diameter())
        if d < 2:
            return
        surf = pygame.Surface((d, d), pygame.SRCALPHA)
        pygame.draw.circle(surf, rgba, (d // 2, d // 2), d // 2)
        screen.blit(surf, (int(self.x) - d // 2, int(self.y) - d // 2))


@dataclass
class TrailSmoke:
    """静止圆形小烟雾（碎片尾迹）——带 alpha 透明渐变。"""

    x: float
    y: float
    diameter: float = 10.0
    life: float = 0.6
    max_life: float = 0.6

    @property
    def alive(self) -> bool:
        return self.life > 0.0

    def update(self, dt: float) -> None:
        self.life -= dt

    def _get_rgba(self) -> Tuple[int, int, int, int]:
        base = (55, 55, 55)
        target = (189, 189, 189)
        elapsed = self.max_life - self.life
        fade_color_start = 0.3
        if elapsed < fade_color_start:
            rgb = base
        else:
            t = min(1.0, (elapsed - fade_color_start) / (self.max_life - fade_color_start))
            rgb = _lerp_color(base, target, t)
        # alpha 渐变：初始 140，线性衰减到 0
        progress = min(1.0, elapsed / self.max_life)
        alpha = int(140 * (1.0 - progress))
        return (rgb[0], rgb[1], rgb[2], max(0, alpha))

    def draw(self, screen: pygame.Surface) -> None:
        d = int(self.diameter)
        rgba = self._get_rgba()
        if rgba[3] <= 0:
            return
        surf = pygame.Surface((d, d), pygame.SRCALPHA)
        pygame.draw.circle(surf, rgba, (d // 2, d // 2), d // 2)
        screen.blit(surf, (int(self.x) - d // 2, int(self.y) - d // 2))


# ============================================================
# 粒子管理器
# ============================================================

class ParticleManager:
    """统一管理所有爆炸粒子。"""

    # 基准地图 cell_size（small）——当前参数在该地图上合适
    _BASE_CELL_SIZE: float = 212.0

    def __init__(self, scale: float = 1.0) -> None:
        """
        scale: 地图适配系数 = 当前 cell_size / _BASE_CELL_SIZE。
        所有粒子尺寸、速度按此缩放。
        """
        self.scale = scale
        self.debris: List[DebrisFragment] = []
        self.main_smoke: List[MainSmoke] = []
        self.trail_smoke: List[TrailSmoke] = []
        self.projectile_pops: List[ProjectilePop] = []

    # ----- 生成 -----

    def spawn_explosion(
        self,
        x: float,
        y: float,
        color: Color,
        walls: List[pygame.Rect],
    ) -> None:
        """
        在 (x, y) 生成一次坦克爆炸的全部粒子。
        walls 只给碎片做墙碰撞检测用。
        所有尺寸/速度按 self.scale 缩放。
        """
        s = self.scale
        # --- 碎片 4~9 ---
        n_debris = random.randint(4, 9)
        for _ in range(n_debris):
            # 形状选择
            r = random.random()
            if r < 0.7:
                n_sides = 3
            elif r < 0.9:
                n_sides = 4
            else:
                n_sides = 5

            # 面积 150~260 px² 按 s 缩放（面积缩放 = s²）
            target_area = random.uniform(150.0, 260.0) * (s * s)
            verts = _generate_irregular_polygon(n_sides, target_area)

            # 速度：从中心向外散射，带随机方向 + 随机大小
            angle = random.uniform(0, math.pi * 2)
            speed = random.uniform(320.0, 750.0) * s  # px/s 按 s 缩放
            vx = math.cos(angle) * speed
            vy = math.sin(angle) * speed

            # 角速度 ±90°/s（不缩放，视觉转速一致）
            angular_vel = random.uniform(-math.pi / 2, math.pi / 2)

            frag = DebrisFragment(
                x=x, y=y,
                vx=vx, vy=vy,
                color=color,
                vertices=verts,
                angular_vel=angular_vel,
            )
            self.debris.append(frag)

        # --- 主体烟雾 5~7，散在坦克中心周围 ---
        n_smoke = random.randint(5, 7)
        for _ in range(n_smoke):
            # 散布半径 0~50 * s
            dist = random.uniform(0, 50.0) * s
            a = random.uniform(0, math.pi * 2)
            sx = x + math.cos(a) * dist
            sy = y + math.sin(a) * dist
            smoke = MainSmoke(x=sx, y=sy)
            smoke.diameter *= s
            self.main_smoke.append(smoke)

        # walls 参数暂存一下，给 debris update 用
        self._walls = walls

    def spawn_projectile_pop(
        self,
        x: float,
        y: float,
        vx: float,
        vy: float,
        proj_size: float,
        color: Color,
    ) -> None:
        """炮弹寿命耗尽消失动画。"""
        s = self.scale
        pop = ProjectilePop(
            x=x, y=y,
            vx=vx, vy=vy,
            diameter_start=proj_size,       # 炮弹实际大小（已含地图缩放）
            diameter_end=45.0 * s,          # 按地图缩放最终直径
            color=color,
        )
        self.projectile_pops.append(pop)

    # ----- 更新 -----

    def update(self, dt: float) -> None:
        # 碎片
        walls = getattr(self, "_walls", [])
        new_trails: List[TrailSmoke] = []
        for d in self.debris:
            d.update(dt, walls)
            if d.alive and d.consume_trail_trigger():
                trail = TrailSmoke(x=d.x, y=d.y)
                trail.diameter *= self.scale
                new_trails.append(trail)

        # 主体烟雾
        for s in self.main_smoke:
            s.update(dt)

        # 小烟雾尾迹
        for t in self.trail_smoke:
            t.update(dt)

        # 炮弹消失圈
        for p in self.projectile_pops:
            p.update(dt)

        # 合并新尾迹并清理死亡
        self.trail_smoke.extend(new_trails)
        self.debris = [d for d in self.debris if d.alive]
        self.main_smoke = [s for s in self.main_smoke if s.alive]
        self.trail_smoke = [t for t in self.trail_smoke if t.alive]
        self.projectile_pops = [p for p in self.projectile_pops if p.alive]

    # ----- 绘制 -----

    def draw(self, screen: pygame.Surface) -> None:
        """画在最顶层。"""
        # 顺序：主体烟雾（最下）→ 碎片（中间）→ 尾迹小烟雾 → 炮弹消失圈（最上）
        for s in self.main_smoke:
            s.draw(screen)
        for d in self.debris:
            d.draw(screen)
        for t in self.trail_smoke:
            t.draw(screen)
        for p in self.projectile_pops:
            p.draw(screen)

    # ----- 工具 -----

    def clear(self) -> None:
        self.debris.clear()
        self.main_smoke.clear()
        self.trail_smoke.clear()
        self.projectile_pops.clear()

    @property
    def alive_count(self) -> int:
        return (len(self.debris) + len(self.main_smoke)
                + len(self.trail_smoke) + len(self.projectile_pops))
