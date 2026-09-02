"""
坦克实体
===========

职责：
- 保存位置、朝向（角度）、速度、弹药数、击杀等状态
- 每帧根据输入（InputState）更新位置与朝向，调用工具做墙体碰撞
- 弹药补充计时、是否可开火
- 提供旋转 OBB（主体 + 炮管）用于 SAT 碰撞检测
- 加载彩色贴图、旋转适配、按地图 cell_size 缩放绘制

不负责：
- 炮弹发射后的生命周期（交给 Projectile / BattleEngine）
- 击毁爆炸表现（交给 BattleEngine）
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import pygame

from ..config.settings import Settings
from ..core.constants import MouseKeyMode
from ..utils.collision import obb_vertices, slide_collision_obb, obb_vs_aabb_collide


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


# ---------------------------------------------------------------------------
# 贴图常量（medium 地图 cell_size=100 为基准，后续按 size_scale 缩放）
# ---------------------------------------------------------------------------
# 贴图原始尺寸（源图，炮口向右 = angle=0 方向）
_TEX_SRC_W = 60
_TEX_SRC_H = 40
# 主体碰撞体：50×40，不额外外扩
_BODY_COLLIDE_W = 50
_BODY_COLLIDE_H = 40
# 炮管碰撞体：10×10，不额外外扩
_BARREL_COLLIDE_W = 10
_BARREL_COLLIDE_H = 10
# 炮管 OBB 中心相对于坦克中心的偏移（沿 angle 方向，angle=0 → 右侧 30）
_BARREL_OFFSET = 30.0
# 炮口位置（发射点）相对于坦克中心的偏移：对齐贴图炮口边缘
_BARREL_MUZZLE_OFFSET = 34.0
# 碰撞体整体相对于贴图中心的本地偏移（随坦克旋转）
# 负数 = angle=0 时往左
_COLLIDE_OFFSET_X = -4.0


class Tank:
    """坦克实体。"""

    # 类级贴图缓存：颜色 RGB 已预旋转缩放好的 Surface
    _tex_cache: Dict[Tuple[int, int, int], pygame.Surface] = {}

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
        self.size_scale: float = 1.0   # 由 BattleEngine 按地图 cell_size 设置
        self.fire_cooldown: float = 0.0  # 开火冷却剩余时间（秒）

        # 位置（中心）、朝向角度（弧度，0=向右，PI/2=向下）
        self.x = float(spawn_x)
        self.y = float(spawn_y)
        self.angle: float = 0.0

        # 状态
        self.alive: bool = True
        self.ammo: int = settings.INITIAL_AMMO
        self._reload_timers: list[float] = []  # 每颗已发射炮弹的独立补充倒计时
        self.kills: int = 0
        self.round_survived: int = 0  # 每局存活计数，一局结算时 alive=True 就 +1；跨局累计

        # 每帧 OBB 顶点缓存（set_size_scale 后再计算）
        self._body_obb: List[Tuple[float, float]] = []
        self._barrel_obb: List[Tuple[float, float]] = []

        # 发射动画状态
        self._shoot_frames: List[pygame.Surface] = []  # 由 engine 按颜色注入
        self._shoot_anim_elapsed: float = -1.0         # <0 = 未播放
        self._shoot_anim_duration: float = 0.15         # 与 engine 保持一致
        self._has_pending_fire: bool = False           # 有延时发射排队中（防重复触发）

    # -------------------------------------------------
    # 公共 API
    # -------------------------------------------------
    def set_size_scale(self, scale: float) -> None:
        """由 BattleEngine 在开局时调用，设置地图缩放系数。"""
        self.size_scale = scale

    @property
    def rect(self) -> pygame.Rect:
        """返回主体碰撞体的 AABB 包围盒（给外部粗粒度碰撞检测用）。"""
        bw = max(1, round(_BODY_COLLIDE_W * self.size_scale))
        bh = max(1, round(_BODY_COLLIDE_H * self.size_scale))
        return pygame.Rect(int(self.x - bw / 2), int(self.y - bh / 2), bw, bh)

    def can_fire(self) -> bool:
        return (
            self.alive
            and self.ammo > 0
            and self.fire_cooldown <= 0.0
            and not self._has_pending_fire  # 有延时发射排队中则暂不允许
        )

    def set_shoot_frames(self, frames: List[pygame.Surface]) -> None:
        """由 BattleEngine 注入该颜色的发射动画帧列表。"""
        self._shoot_frames = frames

    def trigger_shoot_anim(self) -> None:
        """开始播放发射动画（若已有动画在播则忽略）。"""
        if self._shoot_frames and self._shoot_anim_elapsed < 0:
            self._shoot_anim_elapsed = 0.0
            self._has_pending_fire = True

    def update_shoot_anim(self, dt: float) -> None:
        """推进发射动画计时，结束后重置。由 engine 每帧调用。"""
        if self._shoot_anim_elapsed < 0:
            return
        self._shoot_anim_elapsed += dt
        if self._shoot_anim_elapsed >= self._shoot_anim_duration:
            self._shoot_anim_elapsed = -1.0
            self._has_pending_fire = False

    def consume_ammo(self) -> None:
        """发射时扣一发，并为这颗炮弹启动独立的补充计时。"""
        if self.ammo > 0:
            self.ammo -= 1
            self._reload_timers.append(self.settings.AMMO_RELOAD_INTERVAL)

    def reset_for_new_round(self, spawn_x: float, spawn_y: float) -> None:
        """新一局重置（保留击杀统计）。"""
        self.x = float(spawn_x)
        self.y = float(spawn_y)
        self.angle = 0.0
        self.alive = True
        self.ammo = self.settings.INITIAL_AMMO
        self._reload_timers.clear()
        self._shoot_anim_elapsed = -1.0
        self._has_pending_fire = False

    def get_muzzle_pos(self) -> Tuple[float, float]:
        """返回炮口（发射点）的世界坐标，按 size_scale 缩放。"""
        offset = _BARREL_MUZZLE_OFFSET * self.size_scale
        return (
            self.x + math.cos(self.angle) * offset,
            self.y + math.sin(self.angle) * offset,
        )

    # -------------------------------------------------
    # OBB 碰撞体计算
    # -------------------------------------------------
    def get_obb_list(self, angle_override: Optional[float] = None) -> List[List[Tuple[float, float]]]:
        """
        返回当前坦克的两个 OBB（主体 + 炮管），顶点已按 angle 旋转到世界坐标。
        angle_override: 不传用 self.angle；传入则用指定角度（用于旋转碰撞检测）。
        """
        s = self.size_scale
        bw = max(1, round(_BODY_COLLIDE_W * s))
        bh = max(1, round(_BODY_COLLIDE_H * s))
        barrel_w = max(1, round(_BARREL_COLLIDE_W * s))
        barrel_h = max(1, round(_BARREL_COLLIDE_H * s))
        barrel_offset = _BARREL_OFFSET * s
        angle = angle_override if angle_override is not None else self.angle

        # 本地偏移旋转到世界坐标（随坦克旋转）
        ox = _COLLIDE_OFFSET_X * s * math.cos(angle)
        oy = _COLLIDE_OFFSET_X * s * math.sin(angle)

        # 主体 OBB
        body_obb = obb_vertices(self.x + ox, self.y + oy, bw, bh, angle)

        # 炮管中心：沿 angle 方向偏移 barrel_offset，再加本地偏移
        bcx = self.x + math.cos(angle) * barrel_offset + ox
        bcy = self.y + math.sin(angle) * barrel_offset + oy
        barrel_obb = obb_vertices(bcx, bcy, barrel_w, barrel_h, angle)

        return [body_obb, barrel_obb]

    def get_bullet_hit_rect(self) -> pygame.Rect:
        """给炮弹击中检测用的 AABB 近似包围盒（整个坦克含炮管）。"""
        s = self.size_scale
        bw = max(1, round(_BODY_COLLIDE_W * s))
        bh = max(1, round(_BODY_COLLIDE_H * s))
        barrel_w = max(1, round(_BARREL_COLLIDE_W * s))
        barrel_h = max(1, round(_BARREL_COLLIDE_H * s))
        barrel_offset = _BARREL_OFFSET * s + barrel_h / 2 + bh / 2
        # 最远延伸方向
        diag = math.hypot(bw / 2 + barrel_w / 2, barrel_offset)
        total_side = int(max(bw, bh) + 2 * diag * 0.5)
        return pygame.Rect(
            int(self.x - total_side / 2), int(self.y - total_side / 2),
            total_side, total_side,
        )

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

        # ---- 1) 计算目标角度 ----
        target_angle = self.angle
        if self.control_mode == MouseKeyMode.MOUSE and input_.aim_angle is not None:
            target_angle = float(input_.aim_angle)
        else:
            # 键盘模式：左右键转向
            if input_.move_x != 0:
                target_angle += input_.move_x * self.settings.TANK_TURN_SPEED * dt

        # ---- 2) 旋转碰撞检测 ----
        # 只有目标角度和当前不同时才检测；不撞墙才允许旋转
        if abs(target_angle - self.angle) > 1e-6:
            target_obbs = self.get_obb_list(target_angle)
            hit = any(
                obb_vs_aabb_collide(obb, w)
                for obb in target_obbs
                for w in walls
            )
            if not hit:
                self.angle = target_angle
            # 如果撞墙，保持原 angle（不旋转）
        else:
            self.angle = target_angle

        # ---- 3) 计算移动向量 ----
        dx = float(input_.move_x)
        dy = float(input_.move_y)
        if self.control_mode == MouseKeyMode.MOUSE:
            if dx != 0.0 or dy != 0.0:
                m = math.hypot(dx, dy)
                dx /= m
                dy /= m
            speed = self.settings.TANK_SPEED * self.size_scale * dt
            raw_dx = dx * speed
            raw_dy = dy * speed
        else:
            raw_dx = 0.0
            raw_dy = 0.0
            if input_.move_y != 0:
                if input_.move_y > 0:
                    speed = self.settings.TANK_SPEED * self.size_scale
                else:
                    speed = self.settings.TANK_SPEED * self.size_scale * self.settings.TANK_REVERSE_FACTOR
                step = speed * dt
                raw_dx = math.cos(self.angle) * step * input_.move_y
                raw_dy = math.sin(self.angle) * step * input_.move_y

        # ---- 4) OBB 滑动碰撞 ----
        obbs = self.get_obb_list()
        actual_dx, actual_dy = slide_collision_obb(obbs, raw_dx, raw_dy, walls)
        self.x += actual_dx
        self.y += actual_dy

        # 5) 弹药补充计时
        self._tick_reload(dt)

    # -------------------------------------------------
    # 绘制
    # -------------------------------------------------
    def draw(self, screen: pygame.Surface) -> None:
        """画彩色贴图（含发射动画帧覆盖）。"""
        if not self.alive:
            return

        s = self.size_scale
        cx, cy = int(self.x), int(self.y)

        tw = max(1, round(_TEX_SRC_W * s))
        th = max(1, round(_TEX_SRC_H * s))

        # ---- 优先用发射动画帧 ----
        shoot_tex = self._get_scaled_shoot_frame(tw, th)
        if shoot_tex is not None:
            rotated = pygame.transform.rotate(shoot_tex, -math.degrees(self.angle))
            rect = rotated.get_rect(center=(cx, cy))
            screen.blit(rotated, rect.topleft)
            return

        # ---- 否则用常规贴图 ----
        tex = self._get_scaled_tex(tw, th)
        if tex is None:
            self._draw_fallback(screen, s)
            return

        rotated = pygame.transform.rotate(tex, -math.degrees(self.angle))
        rect = rotated.get_rect(center=(cx, cy))
        screen.blit(rotated, rect.topleft)

    def _get_scaled_shoot_frame(self, tw: int, th: int) -> Optional[pygame.Surface]:
        """
        从发射动画中根据当前 elapsed 取对应帧，缩放到 (tw, th)。
        返回 None 表示动画未在播或帧列表为空。
        """
        if self._shoot_anim_elapsed < 0 or not self._shoot_frames:
            return None
        n = len(self._shoot_frames)
        if n == 0:
            return None
        # 线性选帧：elapsed / duration → 0..1 → 0..n-1
        t = min(1.0, max(0.0, self._shoot_anim_elapsed / self._shoot_anim_duration))
        idx = min(n - 1, int(t * n))
        frame = self._shoot_frames[idx]
        if frame.get_size() == (tw, th):
            return frame
        # 缓存 key 复用 shoot 帧
        cache_key = ("shoot", self.color, idx, tw, th)
        if cache_key not in self._tex_cache:
            self._tex_cache[cache_key] = pygame.transform.smoothscale(frame, (tw, th))
        return self._tex_cache[cache_key]

    def _get_scaled_tex(self, tw: int, th: int) -> Optional[pygame.Surface]:
        """
        获取当前坦克的贴图：按颜色索引加载源图 → 预旋转炮管朝右 → 缩放到 (tw, th)。
        带类级缓存：(color, tw, th) → Surface。
        """
        cache_key = (self.color, tw, th)
        if cache_key in self._tex_cache:
            return self._tex_cache[cache_key]

        # 颜色索引 → 文件名
        color_idx = -1
        from ..config.settings import Settings as _S
        _s_inst = _S()
        for i, rgb in enumerate(_s_inst.PLAYER_COLORS):
            if rgb == self.color:
                color_idx = i
                break
        if color_idx < 0:
            return None
        _fname_map = ["red", "blue", "green", "yellow", "purple", "cyan"]
        fname = _fname_map[color_idx] + ".png"

        try:
            src = pygame.image.load(
                os.path.join(_s_inst.tank_move_dir, fname)
            ).convert_alpha()
        except Exception:  # noqa: BLE001
            return None

        # 源图炮口已朝右（angle=0 方向），直接缩放到目标尺寸
        scaled = pygame.transform.smoothscale(src, (tw, th))
        self._tex_cache[cache_key] = scaled
        return scaled

    def _draw_fallback(self, screen: pygame.Surface, s: float) -> None:
        """贴图不可用时的几何图形 fallback。"""
        bw = max(1, round(_BODY_COLLIDE_W * s / max(1, (42 / 40))))
        bh = max(1, round(_BODY_COLLIDE_H * s / max(1, (52 / 50))))
        body_rect = pygame.Rect(0, 0, bw, bh)
        body_rect.center = (int(self.x), int(self.y))
        pygame.draw.rect(screen, self.color, body_rect, border_radius=4)
        pygame.draw.rect(screen, (30, 30, 40), body_rect, width=2, border_radius=4)
        barrel_len = int(bh * 0.6)
        barrel_w = max(4, bw // 6)
        cx, cy = int(self.x), int(self.y)
        ex = cx + math.cos(self.angle) * barrel_len
        ey = cy + math.sin(self.angle) * barrel_len
        pygame.draw.line(screen, (40, 40, 50), (cx, cy), (ex, ey), width=barrel_w)
        pygame.draw.line(screen, (220, 220, 230), (cx, cy), (ex, ey), width=max(2, barrel_w // 3))

    # -------------------------------------------------
    # 内部
    # -------------------------------------------------
    def _tick_reload(self, dt: float) -> None:
        self.fire_cooldown = max(0.0, self.fire_cooldown - dt)
        # 每颗炮弹独立计时：到 6s 补充这一颗
        ready = 0
        for i in range(len(self._reload_timers)):
            self._reload_timers[i] -= dt
            if self._reload_timers[i] <= 0.0:
                ready += 1
        if ready > 0:
            # 移除已到期的计时器
            self._reload_timers = [t for t in self._reload_timers if t > 0.0]
            # 补充弹药，不超过上限
            self.ammo = min(self.ammo + ready, self.settings.INITIAL_AMMO)
