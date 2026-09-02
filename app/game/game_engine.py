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
import os
from typing import Any, Callable, Dict, List, Optional

import pygame

from ..config.settings import Settings
from ..core.event_bus import EventBus, EventType, GameEvent
from ..core.exceptions import GameStateError
from ..utils.collision import circle_vs_obb_collide
from ..utils.logger import get_logger
from .match import Match, MatchEndReason, MatchState
from .maze import Maze, draw_maze, generate_maze
from .particles import ParticleManager
from .projectile import Projectile
from .sound_manager import SoundManager
from .tank import Tank, TankInput

logger = get_logger(__name__)


class BattleEngine:
    """单局战斗引擎。"""

    def __init__(
        self,
        settings: Settings,
        match: Match,
        event_bus: Optional[EventBus] = None,
        maze_seed: int | None = None,
    ) -> None:
        self.settings = settings
        self.match = match
        self.event_bus = event_bus
        self.maze: Maze = generate_maze(
            settings, match.maze_size_key, seed=maze_seed,
            map_gen_modes=getattr(match, "map_gen_modes", None),
        )

        # 按地图 cell_size 等比缩放尺寸/速度
        # 尺寸：以 cell_size=100 为基准（坦克/炮弹同步）
        # 全局坦克缩放：所有地图的坦克（贴图+碰撞体+速度）统一缩小到 0.9 倍
        _size_scale = self.maze.cell_size / 100 * 0.9
        self.scaled_tank_size: int = max(12, round(settings.TANK_SIZE * _size_scale))
        self.scaled_proj_size: int = max(4, round(settings.PROJECTILE_SIZE * _size_scale))
        # 坦克速度：以 cell_size=100 为基准
        self.scaled_tank_speed: float = settings.TANK_SPEED * _size_scale
        # 极大地图（huge）额外速度倍率 —— 只影响 huge 地图，其他地图不变
        if self.maze.size_key == "huge":
            self.scaled_tank_speed *= 1.3   # ← 改这个系数（比如 1.3 就是加速 30%）
        # small 的 cell_size 作为炮弹速度/存在时间的缩放基准（动态计算，改 small 自动联动）
        _s_cols, _s_rows = settings.MAZE_PRESETS["small"]
        _small_cell = min(settings.BATTLE_AREA_WIDTH // _s_cols, settings.BATTLE_AREA_HEIGHT // _s_rows)
        # 炮弹速度：以 small cell_size 为基准
        self.scaled_proj_speed: float = settings.PROJECTILE_SPEED * (self.maze.cell_size / _small_cell)
        # 炮弹存在时间：以 small 为基准反比缩放（大地图更长），上限 PROJECTILE_LIFETIME_MAX
        self.scaled_proj_lifetime: float = min(
            settings.PROJECTILE_LIFETIME * (_small_cell / self.maze.cell_size),
            settings.PROJECTILE_LIFETIME_MAX,
        )

        # 炮弹列表
        self.projectiles: List[Projectile] = []
        self._next_projectile_id: int = 1
        # 每个 owner 的炮弹序号计数器（联机快照精确匹配用；房主/客户端各自维护，相同输入下自动一致）
        self._proj_seq_counter: Dict[int, int] = {}

        # 外部每帧输入：tank_id -> TankInput
        self.input_overrides: Dict[int, TankInput] = {}

        # 帧计数（联机同步用：房主广播 GAME_STATE_SYNC 带 frame，客户端据此判断已确认输入）
        self.frame: int = 0

        # 开局冻结倒计时（秒）：冻结期间坦克/炮弹/结束判定都不更新
        self._start_freeze: float = settings.ROUND_START_FREEZE
        # 结束条件触发后继续战斗倒计时（秒）：期间战斗正常进行，不再重复检查结束条件
        # 为 -1 表示未触发；>=0 表示正在倒计时
        self._post_end_timer: float = -1.0

        # 先加载发射动画帧（坦克初始化时就要用）
        _color_files = ["red", "blue", "green", "yellow", "purple", "cyan"]
        self._shoot_frames: Dict[tuple[int, int, int], list[pygame.Surface]] = {}
        for _idx, _cname in enumerate(_color_files):
            _frames: list[pygame.Surface] = []
            for _fi in range(1, 7):
                try:
                    _s = pygame.image.load(
                        os.path.join(
                            self.settings.shoot_frames_dir, _cname, f"{_cname}{_fi}.png"
                        )
                    ).convert_alpha()
                    _frames.append(_s)
                except Exception:  # noqa: BLE001
                    logger.warning("Failed to load shoot frame: %s/%s%d.png", _cname, _cname, _fi)
            if _frames:
                _rgb = self.settings.PLAYER_COLORS[_idx]
                self._shoot_frames[_rgb] = _frames
        # 延时发射队列 + 动画时长
        self._pending_fires: list[tuple[int, float]] = []
        self._FIRE_ANIM_DURATION: float = 0.15

        # 爆炸粒子系统（按地图 cell_size 缩放，small 为基准）
        _particle_scale = self.maze.cell_size / 212.0
        self._particles = ParticleManager(scale=_particle_scale)

        # 初始化：把坦克放到出生点（顺序按 tank id，出生点不够循环取）
        # 同时按地图尺寸缩放坦克碰撞盒
        spawns = self.maze.spawn_points
        for i, tank in enumerate(self.match.tanks.values()):
            sx, sy = spawns[i % len(spawns)]
            tank.x, tank.y = float(sx), float(sy)
            tank.set_size_scale(_size_scale)
            # 注入该颜色的发射动画帧（若该颜色没加载到则传空列表）
            tank.set_shoot_frames(self._shoot_frames.get(tank.color, []))

        # 预置烟雾特效 baseline（所有 tank 就位后立刻记，避免客户端收到初始快照时误触发）
        self._prev_stats: Dict[int, tuple[int, int]] = {}
        for tank in self.match.tanks.values():
            self._prev_stats[tank.id] = (tank.kills, tank.round_survived)

        # 加载底部状态栏坦克头像图片缓存（颜色索引 -> Surface）
        self._tank_below_images: Dict[int, pygame.Surface] = {}
        _color_files = ["red", "blue", "green", "yellow", "purple", "cyan"]
        for idx, fname in enumerate(_color_files):
            try:
                surf = pygame.image.load(
                    os.path.join(self.settings.tank_below_dir, f"{fname}.png")
                ).convert_alpha()
                self._tank_below_images[idx] = surf
            except Exception:  # noqa: BLE001
                logger.warning("Failed to load tank_below image: %s.png", fname)

        # 加载数值变化烟雾动画帧（30 张，原始 60×80，放大到 120×160）
        self._smoke_frames: list[pygame.Surface] = []
        for i in range(30):
            try:
                surf = pygame.image.load(
                    os.path.join(self.settings.smoke_dir, f"smoke_{i:02d}.png")
                ).convert_alpha()
                surf = pygame.transform.smoothscale(surf, (120, 160))
                self._smoke_frames.append(surf)
            except Exception:  # noqa: BLE001
                logger.warning("Failed to load smoke frame: smoke_%02d.png", i)

        # 烟雾特效状态：活跃特效列表 + 播放时长常量
        self._active_effects: list[dict] = []
        self._SMOKE_DURATION: float = 0.67
        # 客户端新对局烟雾修复：首个权威快照到来前不做基线重置
        self._first_snapshot_applied: bool = False

    # -------------------------------------------------
    # 输入
    # -------------------------------------------------
    def set_tank_input(self, tank_id: int, inp: TankInput) -> None:
        """外部（玩家/AI/网络）设置某坦克本帧输入。下一次 update 生效后清空。"""
        self.input_overrides[tank_id] = inp

    def predict_local_tank(self, tank_id: int, inp: TankInput, dt: float) -> None:
        """
        客户端本地预测：只移动指定坦克（不开火/不碰撞判定/不更新炮弹）。
        用于 client-side prediction：本地立即响应输入，权威状态由 GAME_STATE_SYNC 校正。
        """
        tank = self.match.tanks.get(tank_id)
        if tank is not None and tank.alive:
            tank.update(dt, inp, self.maze.walls)

    # -------------------------------------------------
    # 状态快照（联机同步用）
    # -------------------------------------------------
    def get_state_snapshot(self) -> Dict[str, Any]:
        """
        导出当前权威状态快照（房主广播 GAME_STATE_SYNC 用）。
        结构与 protocol.py 的 GAME_STATE_SYNC 文档一致。
        """
        tanks = []
        for t in self.match.tanks.values():
            tanks.append({
                "id": t.id, "x": t.x, "y": t.y, "angle": t.angle,
                "alive": t.alive, "ammo": t.ammo, "kills": t.kills,
                "round_survived": t.round_survived, "team": t.team,
            })
        projectiles = []
        for p in self.projectiles:
            if not p.alive:
                continue
            projectiles.append({
                "x": p.x, "y": p.y, "vx": p.vx, "vy": p.vy,
                "owner": p.owner_id, "seq": p.seq,
            })
        return {
            "match_id": self.match.id,
            "frame": self.frame,
            "duration": self.match.duration_sec,
            "tanks": tanks,
            "projectiles": projectiles,
        }

    def apply_state_snapshot(self, snapshot: Dict[str, Any]) -> None:
        """
        从权威快照恢复状态（客户端 reconcile 用）。

        架构：**事件驱动 + 精确覆盖**（保持混合架构，房主/客户端都跑完整 engine.update）。
          Tank：x/y/angle/alive/ammo/kills/round_survived → 全部硬写（权威）
          Tank alive True→False → 爆炸粒子 + boom（diff 触发）
          **Projectile：apply_state_snapshot 完全不碰**。
            反弹/击中/消失这三个关键时刻的精确同步靠 EVENT_NOTIFY 即时消息（房主事件触发时立即广播，
            客户端收到后按事件类型精确覆盖对应字段），不靠周期性快照比对。

        执行时机：**engine.update(dt) 之后**。
          engine.update 先推进物理 → 特效自然触发
          本函数再权威校准 tank + alive diff 特效
        """
        # ===== 0) 存旧 tank 离散状态（diff 用） =====
        old_tank_state: Dict[int, Dict[str, Any]] = {}
        for tid, t in self.match.tanks.items():
            old_tank_state[tid] = {
                "alive": t.alive, "ammo": t.ammo,
                "kills": t.kills, "round_survived": t.round_survived,
            }

        # ===== 1) 对局时长（权威） =====
        if "duration" in snapshot:
            self.match.duration_sec = float(snapshot["duration"])

        # ===== 2) Tank：位置硬写 + 离散值硬写 =====
        tanks_data = snapshot.get("tanks", [])
        for td in tanks_data:
            tid = int(td.get("id", 0))
            t = self.match.tanks.get(tid)
            if t is None:
                continue
            t.x = float(td.get("x", t.x))
            t.y = float(td.get("y", t.y))
            t.angle = float(td.get("angle", t.angle))
            t.alive = bool(td.get("alive", t.alive))
            t.ammo = int(td.get("ammo", t.ammo))
            t.kills = int(td.get("kills", t.kills))
            t.round_survived = int(td.get("round_survived", t.round_survived))

        # ===== 3) 新对局第一次快照：设置烟雾检测 baseline =====
        if not self._first_snapshot_applied:
            for td in tanks_data:
                tid = int(td.get("id", 0))
                self._prev_stats[tid] = (
                    int(td.get("kills", 0)), int(td.get("round_survived", 0)),
                )
            self._first_snapshot_applied = True

        # ===== 4) Tank alive diff：True→False → 爆炸 + boom =====
        for td in tanks_data:
            tid = int(td.get("id", 0))
            t = self.match.tanks.get(tid)
            if t is None:
                continue
            old_s = old_tank_state.get(tid)
            if old_s is None:
                continue
            if old_s["alive"] and not t.alive:
                self._particles.spawn_explosion(
                    t.x, t.y, t.color, self.maze.walls
                )
                SoundManager.instance().play("boom")

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
        # 结算：
        #   - 存活数：alive 坦克 +1（跨局累计，MANUAL_STOP 不计）
        #   - 队伍胜场（Team 模式）：结算时仍有 alive 的队伍 +1
        if reason != MatchEndReason.MANUAL_STOP:
            alive_teams: set[int] = set()
            for t in self.match.tanks.values():
                if t.alive:
                    t.round_survived += 1
                    alive_teams.add(t.team)
            if alive_teams:
                for tid in alive_teams:
                    if tid != 0:  # FFA 模式 team=0，不参与 team_wins
                        self.match.team_wins[tid] = self.match.team_wins.get(tid, 0) + 1
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

        # ---- 开局冻结：0.2s 内什么都不更新 ----
        if self._start_freeze > 0:
            self._start_freeze -= dt
            return

        self.frame += 1
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
            tank.update_shoot_anim(dt)  # 推进发射动画计时

            # 开火：按下键 → 播动画 0.1s → 0.1s 后才生成炮弹
            if inp.fire and tank.can_fire():
                tank.trigger_shoot_anim()
                tank.consume_ammo()
                tank.fire_cooldown = self.settings.FIRE_COOLDOWN
                self._pending_fires.append((tank.id, self._FIRE_ANIM_DURATION))
                SoundManager.instance().play("shoot")

        # 1.5) 延时发射：0.1s 到点就生成炮弹
        new_pending: list[tuple[int, float]] = []
        for _tid, _rem in self._pending_fires:
            _rem -= dt
            if _rem <= 0:
                _t = self.match.tanks.get(_tid)
                if _t and _t.alive:
                    self._spawn_projectile(_t)
            else:
                new_pending.append((_tid, _rem))
        self._pending_fires = new_pending

        # 2) 炮弹更新 + 反弹事件
        for p in self.projectiles:
            p.update(dt, walls)
            # 反弹事件（房主端 publish → 订阅者广播 EVENT_NOTIFY 给客户端）
            if p.just_bounced and self.event_bus:
                self.event_bus.publish(GameEvent(
                    EventType.PROJECTILE_BOUNCE,
                    owner=p.owner_id, seq=p.seq,
                    vx=p.vx, vy=p.vy, x=p.x, y=p.y,
                ))
            p.just_bounced = False  # 清标记

        # 3) 炮弹 vs 坦克碰撞（击中即击毁）
        self._resolve_projectile_hits()

        # 粒子系统更新（爆炸碎片/烟雾/尾迹）
        self._particles.update(dt)

        # 4) 寿命耗尽的炮弹 → 消失动画（pop）+ disappear 音效
        for p in self.projectiles:
            if not p.alive and p.lifetime <= 0.0:
                self._particles.spawn_projectile_pop(
                    x=p.x, y=p.y,
                    vx=p.vx, vy=p.vy,
                    proj_size=p.size,
                    color=p.color,
                )
                SoundManager.instance().play("disappear")

        # 5) 清理死亡炮弹
        self.projectiles = [p for p in self.projectiles if p.alive]

        # 6) 结束判定 + 结束后继续倒计时
        if self._post_end_timer < 0:
            # 正常阶段：检查结束条件
            end_reason = self.match.check_end()
            if end_reason is not None:
                if self.match.enable_post_end_continue:
                    # 单机：进入"继续战斗 3s"阶段
                    self._post_end_timer = self.settings.ROUND_POST_END_CONTINUE
                    logger.info(f"对局结束条件触发 id={self.match.id}，继续 {self._post_end_timer:.1f}s 后停止")
                else:
                    # 联机：立即停止（V1 联机不做 post-end continue，避免房主/客户端不同步）
                    self.stop(end_reason)
        else:
            # 结束后继续阶段：战斗正常进行但不再重复检查结束条件
            self._post_end_timer -= dt
            if self._post_end_timer <= 0:
                self._post_end_timer = -1.0
                self.stop(self.match.end_reason or MatchEndReason.LAST_TEAM_STANDING)

    # -------------------------------------------------
    # 开火（延时阶段才真正生成炮弹）
    # -------------------------------------------------
    def _spawn_projectile(self, tank: Tank) -> None:
        """真正生成炮弹的那一下——在动画 0.1s 结束后调用。"""
        angle = tank.angle
        vx = math.cos(angle) * self.scaled_proj_speed
        vy = math.sin(angle) * self.scaled_proj_speed
        # 炮口位置
        x, y = tank.get_muzzle_pos()
        # 分配 seq：每个 owner 独立计数器，保证房主/客户端各自递增时 seq 一致
        seq = self._proj_seq_counter.get(tank.id, 0) + 1
        self._proj_seq_counter[tank.id] = seq
        p = Projectile(tank.id, x, y, vx, vy, self.settings, seq=seq)
        p.size = self.scaled_proj_size
        p.lifetime = self.scaled_proj_lifetime
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
            radius = p.size * 0.5
            for tank in self.match.tanks.values():
                if not tank.alive:
                    continue
                # 炮弹圆 vs 坦克 body/barrel 任一 OBB 相交就算击中
                if any(
                    circle_vs_obb_collide(p.x, p.y, radius, obb)
                    for obb in tank.get_obb_list()
                ):
                    tank.alive = False
                    p.alive = False
                    # 爆炸粒子 + boom 音效
                    self._particles.spawn_explosion(
                        tank.x, tank.y, tank.color, self.maze.walls
                    )
                    SoundManager.instance().play("boom")
                    # 击中事件（房主端 publish → 订阅者广播 EVENT_NOTIFY 给客户端）
                    if self.event_bus:
                        self.event_bus.publish(GameEvent(
                            EventType.PROJECTILE_HIT_TANK,
                            owner=p.owner_id, seq=p.seq,
                            target_tank_id=tank.id,
                        ))
                    # 击杀统计（只在真的撞上时才 +1）：
                    #   - 自杀不算（killer.id == tank.id）
                    #   - 组队模式打队友不算（team 相同且 team != 0）
                    killer = self.match.tanks.get(p.owner_id)
                    if killer is not None and killer.id != tank.id:
                        if killer.team == 0 or tank.team == 0 or killer.team != tank.team:
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
        # 爆炸粒子（画在所有游戏元素之上、UI 之下）
        self._particles.draw(screen)
        # HUD 占位：右上角存活/比分简要
        self._draw_hud(screen)
        # 底部栏：坦克数据卡片（击杀 + 存活）
        self._draw_bottom_bar(screen)

    def _draw_hud(self, screen: pygame.Surface) -> None:
        """HUD：右上角 存活数 + 时间；Team 模式在其左侧显示队伍胜场。"""
        alive_count = sum(1 for t in self.match.tanks.values() if t.alive)
        try:
            font = pygame.font.SysFont("microsoftyahei,arial", 22)
            # 右段：存活数 + 时间
            right_text = font.render(
                f"存活: {alive_count}/{len(self.match.tanks)}  时间: {int(self.match.duration_sec)}s",
                True, self.settings.TEXT_COLOR,
            )
            right_rect = right_text.get_rect(topright=(screen.get_width() - 24, 20))
            screen.blit(right_text, right_rect)

            # 左段（Team 模式）：队伍胜场 —— 紧贴右段左侧
            if self.match.team_wins:
                parts = []
                for tid in sorted(self.match.team_wins):
                    label = chr(ord("A") + tid - 1)  # team 1->A, 2->B, 3->C
                    parts.append(f"队伍{label}：{self.match.team_wins[tid]}")
                left_text = font.render("  ".join(parts), True, self.settings.TEXT_COLOR)
                left_rect = left_text.get_rect(right=right_rect.left - 16, top=20)
                screen.blit(left_text, left_rect)
        except Exception:  # noqa: BLE001
            pass

    def _draw_bottom_bar(self, screen: pygame.Surface) -> None:
        """
        底部栏 (1700×230) —— 按坦克排列数据卡片：
        每个坦克一个 280×230 的方块；
        左侧 200×230 暂空（后续放坦克图片），右侧 80×230 上下平分显示击杀数 / 存活数（只显示数字）。
        FFA 模式：按 tank.id 升序紧挨着排列。
        Team 模式：先按 team 分组，team 内按 id 升序；不同队伍之间留 10×230 的空白间隔。
        """
        from ..core.constants import GameMode
        s = self.settings

        # 1) 底部栏背景
        bar_rect = pygame.Rect(0, s.BOTTOM_BAR_TOP, s.WINDOW_WIDTH, s.BOTTOM_BAR_HEIGHT)
        pygame.draw.rect(screen, s.BOTTOM_BAR_BG_COLOR, bar_rect)

        # 2) 决定坦克排序
        all_tanks = list(self.match.tanks.values())
        if self.match.mode == GameMode.FREE_FOR_ALL:
            ordered = sorted(all_tanks, key=lambda t: t.id)
            group_gaps: list[int] = []  # 无队伍间隔
        else:
            # Team 模式：先按 team 分组，组内按 id；team 按 team id 升序
            from collections import OrderedDict
            groups: "OrderedDict[int, list]" = OrderedDict()
            for t in sorted(all_tanks, key=lambda x: x.id):
                groups.setdefault(t.team, []).append(t)
            ordered = []
            for tid in sorted(groups):
                for t in groups[tid]:
                    ordered.append(t)
            # 组间空白：每跨过一个组边界，tile_x 额外 +10
            # 记录"哪些索引之后需要 +10 偏移"
            group_ends = []
            acc = 0
            for tid in sorted(groups):
                acc += len(groups[tid])
                group_ends.append(acc - 1)  # 这是最后一个 tank 的下标
            # 当渲染到下标 group_ends[i] 后，tile_x +10
            # 但最后一个组不需要 +10，去掉末尾
            group_ends.pop()
            group_gaps = group_ends

        # 3) 渲染
        tile_x = 0
        tile_y = s.BOTTOM_BAR_TOP
        try:
            font = pygame.font.SysFont("microsoftyahei,arial", 36, bold=True)
        except Exception:  # noqa: BLE001
            font = None

        for idx, tank in enumerate(ordered):
            tile_rect = pygame.Rect(tile_x, tile_y, s.BOTTOM_PANEL_TILE_W, s.BOTTOM_PANEL_TILE_H)
            pygame.draw.rect(screen, s.BOTTOM_PANEL_TILE_BG, tile_rect)
            pygame.draw.rect(screen, s.BOTTOM_PANEL_TILE_BORDER, tile_rect, width=2)

            # 绘制对应颜色的坦克图片（200×200，上边留空30px）
            try:
                _ci = s.PLAYER_COLORS.index(tank.color)
                _img = self._tank_below_images.get(_ci)
                if _img is not None:
                    screen.blit(_img, (tile_x, tile_y + 30))
            except (ValueError, Exception):  # noqa: BLE001
                pass

            # 右侧统计区分上下两块
            stats_x = tile_x + s.BOTTOM_PANEL_IMAGE_W
            stats_w = s.BOTTOM_PANEL_STATS_W
            stats_h = s.BOTTOM_PANEL_TILE_H // 2
            kill_rect = pygame.Rect(stats_x, tile_y, stats_w, stats_h)
            pygame.draw.rect(screen, s.BOTTOM_PANEL_TILE_BG, kill_rect)
            pygame.draw.rect(screen, s.BOTTOM_PANEL_STATS_BORDER, kill_rect, width=1)
            survive_rect = pygame.Rect(stats_x, tile_y + stats_h, stats_w, s.BOTTOM_PANEL_TILE_H - stats_h)
            pygame.draw.rect(screen, s.BOTTOM_PANEL_TILE_BG, survive_rect)
            pygame.draw.rect(screen, s.BOTTOM_PANEL_STATS_BORDER, survive_rect, width=1)

            # 检测数值变化 → push 烟雾特效（全部包 try-except，不让渲染崩溃）
            try:
                _prev = self._prev_stats.get(tank.id)
                if _prev is None:
                    self._prev_stats[tank.id] = (tank.kills, tank.round_survived)
                elif self._smoke_frames:
                    _dk = tank.kills - _prev[0]
                    _ds = tank.round_survived - _prev[1]
                    for _ in range(max(0, _dk)):
                        self._active_effects.append({
                            "x": kill_rect.centerx - 60, "y": kill_rect.centery - 120,
                            "elapsed": 0.0, "duration": self._SMOKE_DURATION,
                        })
                    for _ in range(max(0, _ds)):
                        self._active_effects.append({
                            "x": survive_rect.centerx - 60, "y": survive_rect.centery - 120,
                            "elapsed": 0.0, "duration": self._SMOKE_DURATION,
                        })
                    self._prev_stats[tank.id] = (tank.kills, tank.round_survived)
            except Exception:  # noqa: BLE001
                logger.exception("Error detecting smoke trigger for tank %s", tank.id)

            # 只写数字（无标签）
            if font is not None:
                num_color = (0, 0, 0)
                kill_num = font.render(str(tank.kills), True, num_color)
                survive_num = font.render(str(tank.round_survived), True, num_color)
                screen.blit(kill_num, kill_num.get_rect(center=kill_rect.center))
                screen.blit(survive_num, survive_num.get_rect(center=survive_rect.center))

            # 死亡坦克：30% 黑色遮罩
            if not tank.alive:
                dark = pygame.Surface((s.BOTTOM_PANEL_TILE_W, s.BOTTOM_PANEL_TILE_H), pygame.SRCALPHA)
                dark.fill((0, 0, 0, 100))
                screen.blit(dark, (tile_x, tile_y))

            # 下一张卡片（Team 模式组间 +10 空白）
            tile_x += s.BOTTOM_PANEL_TILE_W
            if idx in group_gaps:
                tile_x += 10

        # === 烟雾特效：渲染帧 + 推进 + 清理（包 try-except）===
        if self._smoke_frames and self._active_effects:
            try:
                _tf = len(self._smoke_frames)
                for eff in self._active_effects:
                    _d = eff.get("duration", 0)
                    if _d <= 0:
                        continue
                    _idx = min(_tf - 1, int(eff["elapsed"] / _d * _tf))
                    screen.blit(self._smoke_frames[_idx], (eff.get("x", 0), eff.get("y", 0)))
            except Exception:  # noqa: BLE001
                logger.exception("Error rendering smoke effects")

            try:
                for eff in self._active_effects:
                    eff["elapsed"] += 1 / 60.0
                self._active_effects = [
                    e for e in self._active_effects
                    if e["elapsed"] < e.get("duration", self._SMOKE_DURATION)
                ]
            except Exception:  # noqa: BLE001
                logger.exception("Error advancing smoke effects")
