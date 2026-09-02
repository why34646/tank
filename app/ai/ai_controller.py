"""
AI 控制器
===========

所有 AI 共享同一套基础移动/寻路/瞄准机制（WASDQ 键盘模式，和玩家完全一样的操控手感）。
难度差异只通过 9 个行为参数体现。

9 维难度参数（见 AIParams）：
1. 决策间隔：多久重新评估一次策略
2. 瞄准容差：炮口指向目标允许的最大角度偏差（弧度）
3. 子弹感知范围：能"看到"来袭炮弹的距离（0=瞎子，inf=全图）
4. 闪避策略：发现炮弹后怎么躲 ("random"|"perpendicular")
5. 闪避延迟：发现炮弹后多久开始躲（秒），<0=不闪避
6. 开火条件：在什么条件下开火 ("sight"|"angle"|"random")
7. 开火冷却：两次开火之间的最小间隔（秒）
8. 弹药保留：低于此值不开火（留给紧急情况）
9. 目标选择：选谁当目标 ("nearest"|"weakest"|"threat"|"random")

核心思路：
  - 全图墙体感知：预构建格子级 nav 网格 + BFS 最短路（全局最优）
  - 每帧 16 方向 AABB 精确探测（和引擎 slide_collision 完全一致）
  - 候选评分 = BFS 最短路长度下降量（old_len - new_len），选下降最多的方向
    → 确保 AI 会主动绕墙（因为"绕路后总 pathLen 下降 > 局部朝目标蹭但不变"）
  - 卡住：把失败方向记入禁忌记忆 + 沿墙滑行 0.8s
  - LOS 存在时直接直线冲刺（跳过 BFS，快）
"""

from __future__ import annotations

import math
import random
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, List, Optional, Set, Tuple

import pygame

from ..core.constants import AIDifficulty, MouseKeyMode
from ..game.tank import Tank, TankInput
from ..utils.collision import aabb_overlap, rect_from_tank, slide_collision_obb

if TYPE_CHECKING:
    from ..game.game_engine import BattleEngine


# ============================================================
# 难度参数（9 维）
# ============================================================
@dataclass
class AIParams:
    """9 维行为参数 —— 所有 AI 共享同一套寻路/移动/瞄准机制。"""
    decision_interval: float          # 决策间隔（秒）：多久重新算一次移动方向
    aim_tolerance_rad: float          # 瞄准容差（弧度）：炮口指向目标允许的最大偏差
    bullet_detect_range: float        # 子弹感知范围（px），0=瞎子，inf=全图
    evasion_type: str                 # "random"|"perpendicular"|""=不闪避
    evasion_delay: float              # 闪避延迟（秒），<0=不闪避
    fire_condition: str               # "sight"=有视线就开 | "angle"=角度+视线 | "random"=瞎开
    fire_cooldown: float              # 开火冷却（秒）
    ammo_reserve: int                 # 保留弹药数（低于此值不开火）
    target_type: str                  # "nearest"|"weakest"|"threat"|"random"


# ============================================================
# 辅助函数
# ============================================================
def _has_los(x1: float, y1: float, x2: float, y2: float,
             walls: List[pygame.Rect], check_size: int) -> bool:
    dx = x2 - x1; dy = y2 - y1; dist = math.hypot(dx, dy)
    if dist < 1: return True
    n = max(3, int(dist / (check_size * 0.6)))
    half = check_size / 2
    for i in range(1, n):
        t = i / n
        px = x1 + dx * t; py = y1 + dy * t
        rect = pygame.Rect(int(px - half), int(py - half), check_size, check_size)
        for w in walls:
            if aabb_overlap(rect, w): return False
    return True


def _angle_diff(a: float, b: float) -> float:
    """返回 (a - b) 的最短有向角，范围 (-pi, pi]。正值 = a 在 b 顺时针方向。"""
    d = (a - b) % (2 * math.pi)
    if d > math.pi: d -= 2 * math.pi
    return d


# ============================================================
# 导航网格预构建（缓存复用）
# ============================================================
def _build_nav(maze, tank_size: int) -> dict:
    """
    构建格子级导航网格：
    grid[r][c] = True 表示 (c,r) 格中心能放下坦克
    adj[(c,r)] = 可通行邻居列表
    """
    cols, rows = maze.cols, maze.rows
    ox = maze.playfield_rect.x
    oy = maze.playfield_rect.y
    cs = maze.cell_size
    walls = maze.walls

    grid = [[True] * cols for _ in range(rows)]
    for r in range(rows):
        for c in range(cols):
            cx = ox + (c + 0.5) * cs
            cy = oy + (r + 0.5) * cs
            rect = rect_from_tank(cx, cy, tank_size)
            for w in walls:
                if aabb_overlap(rect, w):
                    grid[r][c] = False
                    break

    adj: Dict[Tuple[int, int], List[Tuple[int, int]]] = {}
    dirs = [(0, -1), (0, 1), (-1, 0), (1, 0)]
    for r in range(rows):
        for c in range(cols):
            if not grid[r][c]:
                continue
            neighbors = []
            for dc, dr in dirs:
                nc, nr = c + dc, r + dr
                if not (0 <= nc < cols and 0 <= nr < rows):
                    continue
                if not grid[nr][nc]:
                    continue
                # 检查两格中心连线中点是否被墙挡住（防止墙很薄对角线穿越）
                wx1 = ox + (c + 0.5) * cs
                wy1 = oy + (r + 0.5) * cs
                wx2 = ox + (nc + 0.5) * cs
                wy2 = oy + (nr + 0.5) * cs
                mid_rect = rect_from_tank((wx1 + wx2) / 2, (wy1 + wy2) / 2, tank_size)
                blocked = False
                for w in walls:
                    if aabb_overlap(mid_rect, w):
                        blocked = True
                        break
                if not blocked:
                    neighbors.append((nc, nr))
            adj[(c, r)] = neighbors

    return {
        "grid": grid, "adj": adj,
        "cols": cols, "rows": rows,
        "offset_x": ox, "offset_y": oy, "cell_size": cs,
    }


def _bfs_path_len(nav: dict, sc: int, sr: int, tc: int, tr: int) -> float:
    """
    返回从 (sc,sr) 到 (tc,tr) 的最短 BFS 路径长度（格数 × cell_size 作为代价）。
    不可达返回 99999。
    """
    adj = nav["adj"]
    cs = nav["cell_size"]
    grid = nav["grid"]
    cols, rows = nav["cols"], nav["rows"]

    # 钳位源/目标到合法范围
    sc = max(0, min(cols - 1, sc))
    sr = max(0, min(rows - 1, sr))
    tc = max(0, min(cols - 1, tc))
    tr = max(0, min(rows - 1, tr))

    # 如果源格被墙占了（贴墙边界），找最近可行格
    if not grid[sr][sc]:
        best_d = 999; best_g = (sc, sr)
        for r in range(max(0, sr - 2), min(rows, sr + 3)):
            for c in range(max(0, sc - 2), min(cols, sc + 3)):
                if grid[r][c]:
                    d = abs(c - sc) + abs(r - sr)
                    if d < best_d:
                        best_d = d; best_g = (c, r)
        sc, sr = best_g

    if sc == tc and sr == tr:
        return 0.0

    visited = {(sc, sr)}
    queue = deque([(sc, sr, 0)])  # (c, r, steps)
    while queue:
        cx, cy, steps = queue.popleft()
        for nx, ny in adj.get((cx, cy), []):
            if (nx, ny) not in visited:
                visited.add((nx, ny))
                if nx == tc and ny == tr:
                    return (steps + 1) * cs
                queue.append((nx, ny, steps + 1))
    return 99999.0


def _probe_dir(tank: "Tank", angle: float, step: float,
               walls: List[pygame.Rect]) -> Tuple[float, float, bool]:
    """
    模拟坦克往 angle 方向走 step 像素，返回
    (actual_dx, actual_dy, fully_blocked)
    fully_blocked = True 表示连 1px 都动不了
    用 OBB 滑动碰撞（与引擎坦克移动完全一致）。
    """
    dx = math.cos(angle) * step
    dy = math.sin(angle) * step
    obbs = tank.get_obb_list()
    ax, ay = slide_collision_obb(obbs, dx, dy, walls)
    moved = math.hypot(ax, ay)
    return ax, ay, moved < 0.5 and step > 0.5


# ============================================================
# AI 控制器（所有难度共享此实现，差异仅在 params）
# ============================================================
class AIController:
    """AI 控制器基类。子类只覆盖 params，逻辑共享。"""

    difficulty: AIDifficulty = AIDifficulty.NORMAL
    params: AIParams = AIParams(
        decision_interval=0.25, aim_tolerance_rad=math.radians(18),
        bullet_detect_range=200, evasion_type="random", evasion_delay=0.3,
        fire_condition="sight", fire_cooldown=1.2, ammo_reserve=1, target_type="nearest",
    )

    N_DIRS: int = 16
    PROBE_STEP: float = 30.0

    def __init__(self) -> None:
        # nav 缓存
        self._nav: Optional[dict] = None
        self._nav_key: Optional[tuple] = None

        # 决策状态
        self._last_decision: float = -999.0
        self._target_dir: Tuple[float, float] = (0.0, 0.0)
        self._last_fire: float = -999.0
        self._threat_start: Optional[float] = None
        self._last_pos: Optional[Tuple[float, float]] = None
        self._stuck_counter: float = 0.0
        self._last_think_time: float = -999.0

        # 禁忌记忆：最近失败的方向索引
        self._blocked_angle_indexes: Set[int] = set()
        self._blocked_history: List[int] = []
        self._MAX_BLOCKED_MEM: int = 6

        # 沿墙滑行状态
        self._wall_follow_until: float = 0.0

    # -------------------------------------------------
    def think(self, tank: Tank, engine: "BattleEngine") -> TankInput:
        """每帧调用，返回本帧输入。"""
        if not tank.alive:
            self._last_pos = None
            self._last_think_time = -999.0
            self._blocked_angle_indexes.clear()
            self._blocked_history.clear()
            self._wall_follow_until = 0.0
            return TankInput()

        if tank.control_mode != MouseKeyMode.WASDQ:
            tank.control_mode = MouseKeyMode.WASDQ

        self._ensure_nav(engine)

        now = engine.match.duration_sec
        dt = 1.0 / 60.0
        if self._last_think_time > -900:
            dt = max(0.001, now - self._last_think_time)
        self._last_think_time = now
        p = self.params

        # --- 碰墙检测 ---
        if self._last_pos is not None:
            lp_x, lp_y = self._last_pos
            actual_dx = tank.x - lp_x
            actual_dy = tank.y - lp_y
            moved = math.hypot(actual_dx, actual_dy)
            if moved < 1.5 and self._target_dir != (0.0, 0.0):
                self._stuck_counter += dt
            else:
                self._stuck_counter = 0.0
                self._blocked_angle_indexes.clear()
                self._blocked_history.clear()
        self._last_pos = (tank.x, tank.y)

        # --- 检测来袭炮弹 ---
        threats = self._detect_threats(tank, engine)
        evasive_move_dir: Optional[Tuple[float, float]] = None

        if threats and p.evasion_type and p.evasion_delay >= 0:
            if self._threat_start is None:
                self._threat_start = now
            if now - self._threat_start >= p.evasion_delay:
                evasive_move_dir = self._compute_evasion(tank, threats)
        else:
            self._threat_start = None

        # --- 正常决策 ---
        if now > self._wall_follow_until:
            self._wall_follow_until = 0.0

        if evasive_move_dir is not None:
            self._target_dir = evasive_move_dir
            self._last_decision = now
        elif self._stuck_counter > 0.4:
            self._handle_stuck(tank, engine, now)
        elif self._wall_follow_until > 0 and abs(now - self._last_decision) < 0.15:
            pass
        elif now - self._last_decision >= p.decision_interval:
            self._last_decision = now
            self._replan_move(tank, engine)

        # --- 转向 + 前进 ---
        aim_dir = self._target_dir
        dx = aim_dir[0]; dy = aim_dir[1]
        desired_angle = math.atan2(dy, dx) if (dx != 0 or dy != 0) else tank.angle

        angle_diff = _angle_diff(desired_angle, tank.angle)
        max_turn = engine.settings.TANK_TURN_SPEED * dt
        move_x = 0
        if abs(angle_diff) > max_turn:
            move_x = 1 if angle_diff > 0 else -1

        move_y = 0
        if abs(angle_diff) < math.radians(25):
            move_y = 1
        elif abs(angle_diff) > math.radians(155):
            move_y = -1

        # --- 开火 ---
        target = self._select_target(tank, engine)
        fire = self._decide_fire(tank, target, engine, now, desired_angle)

        return TankInput(move_x=move_x, move_y=move_y, fire=fire)

    # -------------------------------------------------
    # Nav 缓存
    # -------------------------------------------------
    def _ensure_nav(self, engine: "BattleEngine") -> None:
        maze = engine.maze
        key = (maze.cols, maze.rows, maze.cell_size,
               maze.playfield_rect.x, maze.playfield_rect.y,
               engine.scaled_tank_size)
        if self._nav_key != key:
            self._nav = _build_nav(maze, engine.scaled_tank_size)
            self._nav_key = key

    def _world_to_grid(self, x: float, y: float) -> Tuple[int, int]:
        nav = self._nav
        c = int((x - nav["offset_x"]) / nav["cell_size"])
        r = int((y - nav["offset_y"]) / nav["cell_size"])
        c = max(0, min(c, nav["cols"] - 1))
        r = max(0, min(r, nav["rows"] - 1))
        return c, r

    def _path_to_target(self, tank: Tank, target: Tank) -> float:
        """当前位置到目标的 BFS 最短路径长度（像素）。"""
        if self._nav is None:
            return 99999.0
        sc, sr = self._world_to_grid(tank.x, tank.y)
        tc, tr = self._world_to_grid(target.x, target.y)
        return _bfs_path_len(self._nav, sc, sr, tc, tr)

    # -------------------------------------------------
    # 核心：全局最短路评分移动规划
    # -------------------------------------------------
    def _replan_move(self, tank: Tank, engine: "BattleEngine") -> None:
        target = self._select_target(tank, engine)
        if target is None:
            self._target_dir = (0.0, 0.0)
            return

        walls = engine.maze.walls
        proj_size = engine.scaled_proj_size

        # 如果有 LOS → 直接直线冲刺（最快）
        if _has_los(tank.x, tank.y, target.x, target.y, walls, proj_size):
            dx = target.x - tank.x
            dy = target.y - tank.y
            d = math.hypot(dx, dy)
            if d > 1:
                self._target_dir = (dx / d, dy / d)
                return

        # 先算当前位置到目标的 BFS pathLen（基准）
        nav = self._nav
        sc, sr = self._world_to_grid(tank.x, tank.y)
        tc, tr = self._world_to_grid(target.x, target.y)
        old_path_len = _bfs_path_len(nav, sc, sr, tc, tr)

        # 16 方向 AABB 精确探测
        candidates = []  # (score, angle)
        for i in range(self.N_DIRS):
            ang = (2 * math.pi * i) / self.N_DIRS
            ax, ay, blocked = _probe_dir(tank, ang, self.PROBE_STEP, walls)
            moved = math.hypot(ax, ay)
            if blocked or moved < 2:
                continue
            if i in self._blocked_angle_indexes:
                continue

            # 模拟位移后的新位置 → 算新 pathLen
            nx = tank.x + ax
            ny = tank.y + ay
            nc, nr = self._world_to_grid(nx, ny)
            new_path_len = _bfs_path_len(nav, nc, nr, tc, tr)

            # 评分 = pathLen 下降量 + 小权重鼓励朝向目标方向
            gain = old_path_len - new_path_len  # 正 = pathLen 下降（好）
            # 附加：move 越多越好 + 角度差小的小加分
            target_ang = math.atan2(target.y - tank.y, target.x - tank.x)
            da_bonus = 0.5 * math.cos(_angle_diff(ang, target_ang))
            score = gain + da_bonus + moved * 0.02

            candidates.append((score, ang))

        if not candidates:
            # 非禁忌方向都不行 → 允许禁忌方向再试一次（但禁忌方向大扣分）
            for i in range(self.N_DIRS):
                ang = (2 * math.pi * i) / self.N_DIRS
                ax, ay, blocked = _probe_dir(tank, ang, self.PROBE_STEP, walls)
                moved = math.hypot(ax, ay)
                if blocked or moved < 2:
                    continue
                nx = tank.x + ax
                ny = tank.y + ay
                nc, nr = self._world_to_grid(nx, ny)
                new_path_len = _bfs_path_len(nav, nc, nr, tc, tr)
                gain = old_path_len - new_path_len
                target_ang = math.atan2(target.y - tank.y, target.x - tank.x)
                da_bonus = 0.5 * math.cos(_angle_diff(ang, target_ang))
                score = gain + da_bonus + moved * 0.02
                if i in self._blocked_angle_indexes:
                    score -= 50.0  # 禁忌方向大扣分
                candidates.append((score, ang))

        if not candidates:
            # 真全堵了 → 直接朝目标直线（slide_collision 会帮擦墙走）
            dx = target.x - tank.x
            dy = target.y - tank.y
            d = math.hypot(dx, dy)
            if d > 1:
                self._target_dir = (dx / d, dy / d)
            else:
                self._target_dir = (0.0, 0.0)
            return

        # 选最高分方向
        candidates.sort(key=lambda c: c[0], reverse=True)
        _, best_ang = candidates[0]
        self._target_dir = (math.cos(best_ang), math.sin(best_ang))

    # -------------------------------------------------
    # 卡住处理：全局最短路评分 + 禁忌记忆 + 沿墙滑行
    # -------------------------------------------------
    def _handle_stuck(self, tank: Tank, engine: "BattleEngine", now: float) -> None:
        walls = engine.maze.walls

        # 1. 把当前 _target_dir 记入禁忌（防止再撞同一堵墙）
        cur_angle = math.atan2(self._target_dir[1], self._target_dir[0]) if self._target_dir != (0, 0) else tank.angle
        cur_idx = self._angle_to_index(cur_angle)
        self._mark_blocked(cur_idx)

        target = self._select_target(tank, engine)
        if target is None:
            self._target_dir = (0.0, 0.0)
            return

        # 用同样的全局最短路评分来选脱困方向
        nav = self._nav
        sc, sr = self._world_to_grid(tank.x, tank.y)
        tc, tr = self._world_to_grid(target.x, target.y)
        old_path_len = _bfs_path_len(nav, sc, sr, tc, tr)

        candidates = []
        for i in range(self.N_DIRS):
            ang = (2 * math.pi * i) / self.N_DIRS
            ax, ay, blocked = _probe_dir(tank, ang, self.PROBE_STEP, walls)
            moved = math.hypot(ax, ay)
            if blocked or moved < 2:
                continue
            if i in self._blocked_angle_indexes:
                continue
            nx = tank.x + ax
            ny = tank.y + ay
            nc, nr = self._world_to_grid(nx, ny)
            new_path_len = _bfs_path_len(nav, nc, nr, tc, tr)
            gain = old_path_len - new_path_len
            target_ang = math.atan2(target.y - tank.y, target.x - tank.x)
            da_bonus = 0.5 * math.cos(_angle_diff(ang, target_ang))
            score = gain + da_bonus + moved * 0.02
            candidates.append((score, ang))

        if not candidates:
            # 非禁忌方向全无 → 允许禁忌方向
            for i in range(self.N_DIRS):
                ang = (2 * math.pi * i) / self.N_DIRS
                ax, ay, blocked = _probe_dir(tank, ang, self.PROBE_STEP, walls)
                moved = math.hypot(ax, ay)
                if blocked or moved < 2:
                    continue
                nx = tank.x + ax
                ny = tank.y + ay
                nc, nr = self._world_to_grid(nx, ny)
                new_path_len = _bfs_path_len(nav, nc, nr, tc, tr)
                gain = old_path_len - new_path_len
                target_ang = math.atan2(target.y - tank.y, target.x - tank.x)
                da_bonus = 0.5 * math.cos(_angle_diff(ang, target_ang))
                score = gain + da_bonus + moved * 0.02
                if i in self._blocked_angle_indexes:
                    score -= 50.0
                candidates.append((score, ang))

        if not candidates:
            # 彻底没救
            self._target_dir = (0.0, 0.0)
            return

        candidates.sort(key=lambda c: c[0], reverse=True)
        _, best_ang = candidates[0]
        self._target_dir = (math.cos(best_ang), math.sin(best_ang))
        self._wall_follow_until = now + 0.8
        self._stuck_counter = 0.0
        self._last_decision = now

    # -------------------------------------------------
    def _angle_to_index(self, angle: float) -> int:
        a = angle % (2 * math.pi)
        step = 2 * math.pi / self.N_DIRS
        idx = int(round(a / step)) % self.N_DIRS
        return idx

    def _mark_blocked(self, idx: int) -> None:
        if idx in self._blocked_angle_indexes:
            return
        self._blocked_angle_indexes.add(idx)
        self._blocked_history.append(idx)
        while len(self._blocked_history) > self._MAX_BLOCKED_MEM:
            old = self._blocked_history.pop(0)
            self._blocked_angle_indexes.discard(old)

    # -------------------------------------------------
    # 子弹检测 + 闪避
    # -------------------------------------------------
    def _detect_threats(self, tank: Tank, engine: "BattleEngine") -> List[tuple]:
        p = self.params
        if p.bullet_detect_range <= 0:
            return []
        threats = []
        for proj in engine.projectiles:
            if not proj.alive or proj.owner_id == tank.id:
                continue
            dist = math.hypot(proj.x - tank.x, proj.y - tank.y)
            if p.bullet_detect_range != float("inf") and dist > p.bullet_detect_range:
                continue
            vmag_sq = proj.vx ** 2 + proj.vy ** 2
            if vmag_sq < 1e-6: continue
            wx = tank.x - proj.x; wy = tank.y - proj.y
            t = (wx * proj.vx + wy * proj.vy) / vmag_sq
            if t < 0: continue
            closest_x = proj.x + t * proj.vx; closest_y = proj.y + t * proj.vy
            min_dist = math.hypot(closest_x - tank.x, closest_y - tank.y)
            if min_dist < tank.rect.width * 1.8:
                threats.append((proj, min_dist, t))
        return threats

    def _compute_evasion(self, tank: Tank, threats: List[tuple]) -> Tuple[float, float]:
        p = self.params
        if p.evasion_type == "random" or not p.evasion_type:
            a = random.uniform(0, 2 * math.pi)
            return (math.cos(a), math.sin(a))
        proj, _, _ = min(threats, key=lambda x: x[1])
        perp_x = -proj.vy; perp_y = proj.vx
        vmag_sq = proj.vx ** 2 + proj.vy ** 2
        t = ((tank.x - proj.x) * proj.vx + (tank.y - proj.y) * proj.vy) / vmag_sq
        cl_x = proj.x + t * proj.vx; cl_y = proj.y + t * proj.vy
        away_x = tank.x - cl_x; away_y = tank.y - cl_y
        if perp_x * away_x + perp_y * away_y < 0:
            perp_x, perp_y = -perp_x, -perp_y
        m = math.hypot(perp_x, perp_y) or 1
        return (perp_x / m, perp_y / m)

    # -------------------------------------------------
    # 目标选择（严格排除队友）
    # -------------------------------------------------
    def _select_target(self, tank: Tank, engine: "BattleEngine") -> Optional[Tank]:
        if tank.team == 0:
            candidates = [t for t in engine.match.tanks.values()
                          if t.alive and t.id != tank.id]
        else:
            candidates = [t for t in engine.match.tanks.values()
                          if t.alive and t.id != tank.id and t.team != tank.team]
        if not candidates:
            return None

        tp = self.params.target_type
        if tp == "random":
            return random.choice(candidates)
        if tp == "nearest":
            return min(candidates, key=lambda e: math.hypot(e.x - tank.x, e.y - tank.y))
        if tp == "weakest":
            return min(candidates, key=lambda e: e.ammo)
        if tp == "threat":
            return min(candidates, key=lambda e: math.hypot(e.x - tank.x, e.y - tank.y) / 80 - e.ammo)
        return candidates[0]

    # -------------------------------------------------
    # 开火决策
    # -------------------------------------------------
    def _decide_fire(self, tank: Tank, target: Optional[Tank],
                     engine: "BattleEngine", now: float,
                     desired_angle: float) -> bool:
        p = self.params
        if not tank.can_fire(): return False
        if tank.ammo <= p.ammo_reserve: return False
        if now - self._last_fire < p.fire_cooldown: return False

        fc = p.fire_condition
        if fc == "random":
            if random.random() < 0.1:
                self._last_fire = now
                return True
            return False

        if target is None: return False

        walls = engine.maze.walls
        proj_size = engine.scaled_proj_size
        los = _has_los(tank.x, tank.y, target.x, target.y, walls, proj_size)

        if fc == "sight":
            if los:
                self._last_fire = now
                return True
            return False

        if fc == "angle":
            angle_to_target = math.atan2(target.y - tank.y, target.x - tank.x)
            if abs(_angle_diff(angle_to_target, tank.angle)) > p.aim_tolerance_rad:
                return False
            if los:
                self._last_fire = now
                return True
            return False

        return False


# ============================================================
# 四档难度子类 —— 只覆盖 params
# ============================================================
class EasyAIController(AIController):
    difficulty = AIDifficulty.EASY
    params = AIParams(
        decision_interval=0.60, aim_tolerance_rad=math.radians(45),
        bullet_detect_range=0,
        evasion_type="", evasion_delay=-1,
        fire_condition="random", fire_cooldown=2.5,
        ammo_reserve=0, target_type="random",
    )


class NormalAIController(AIController):
    difficulty = AIDifficulty.NORMAL
    params = AIParams(
        decision_interval=0.30, aim_tolerance_rad=math.radians(22),
        bullet_detect_range=250,
        evasion_type="random", evasion_delay=0.35,
        fire_condition="sight", fire_cooldown=1.2,
        ammo_reserve=1, target_type="nearest",
    )


class MasterAIController(AIController):
    difficulty = AIDifficulty.MASTER
    params = AIParams(
        decision_interval=0.15, aim_tolerance_rad=math.radians(10),
        bullet_detect_range=500,
        evasion_type="perpendicular", evasion_delay=0.18,
        fire_condition="angle", fire_cooldown=0.6,
        ammo_reserve=2, target_type="weakest",
    )


class NightmareAIController(AIController):
    difficulty = AIDifficulty.NIGHTMARE
    params = AIParams(
        decision_interval=0.08, aim_tolerance_rad=math.radians(4),
        bullet_detect_range=float("inf"),
        evasion_type="perpendicular", evasion_delay=0.08,
        fire_condition="angle", fire_cooldown=0.35,
        ammo_reserve=0, target_type="threat",
    )


# ============================================================
# 工厂
# ============================================================
_DIFFICULTY_MAP = {
    AIDifficulty.EASY: EasyAIController,
    AIDifficulty.NORMAL: NormalAIController,
    AIDifficulty.MASTER: MasterAIController,
    AIDifficulty.NIGHTMARE: NightmareAIController,
}


def make_ai_controller(difficulty: AIDifficulty) -> AIController:
    cls = _DIFFICULTY_MAP.get(difficulty, NormalAIController)
    return cls()
