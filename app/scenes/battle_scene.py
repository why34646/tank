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
import queue
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

import pygame
import pygame_gui
import pygame_gui.elements.ui_button as ui_button
import pygame_gui.elements.ui_label as ui_label

from ..ai.ai_controller import AIController, make_ai_controller
from ..config.settings import Settings
from ..core.constants import AIDifficulty, GameMode, MazeSize, MouseKeyMode, MapGenMode
from ..core.event_bus import EventType, GameEvent
from ..game.game_engine import BattleEngine
from ..game.match import Match, MatchEndReason, MatchState
from ..game.tank import Tank, TankInput
from ..network.client import GameClient
from ..network.protocol import MessageType
from ..network.server import ClientPeer, GameServer
from ..storage.records_store import BattleRecord
from .base_scene import Scene


def _lerp(a: float, b: float, t: float) -> float:
    """线性插值。"""
    return a + (b - a) * t


def _lerp_angle(a: float, b: float, t: float) -> float:
    """角度插值（取最短路径，处理 -π~π 环绕）。"""
    diff = (b - a + math.pi) % (2.0 * math.pi) - math.pi  # [-π, π] 最短有向差
    return a + diff * t


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
        map_gen_modes: list[str] | None = None,
        player_color_idx: int = 0,       # 玩家坦克颜色索引（settings.PLAYER_COLORS）
        # ===== 联机参数 =====
        as_host: bool = False,
        server: Optional[GameServer] = None,    # 房主端
        client: Optional[GameClient] = None,    # 客户端端
        my_player_id: int = 0,                   # 自己在房间成员的 player_id（房主=0）
        maze_seed: int = 0,                       # 房主生成迷宫的种子（客户端用同 seed 重建）
        initial_tanks: Optional[List[Dict[str, Any]]] = None,  # [{id,codename,team,is_ai,player_id}]
        codename: str = "",
        # ===== 单机跨局复用：保留上一局 Tank 对象以累计 kills / round_survived =====
        persistent_tanks: Optional[List[Tank]] = None,
        # ===== 单机跨局队伍胜场 =====
        inherit_team_wins: Optional[Dict[int, int]] = None,
        # ===== 单机跨局 session 继承（整场一场，不是单局）=====
        inherit_session_started_at: Optional[str] = None,
        inherit_session_start_ts: float = 0.0,
    ) -> None:
        super().__init__()
        self.total_players = total_players
        self.ai_difficulty = ai_difficulty
        self.mode = mode
        self.maze_size = maze_size
        self.is_online = is_online
        self.endless_round = endless_round
        self.player_color_idx = player_color_idx
        # 地图生成候选模式：None 时回退 settings.DEFAULT_MAP_GEN_MODES（在 Match 中再做二次兜底）
        if map_gen_modes is None:
            # 取 settings.DEFAULT_MAP_GEN_MODES；若不可得则兜底 ["carve"]
            try:
                from ..config.settings import Settings as _S
                _tmp = _S.load()
                default = list(getattr(_tmp, "DEFAULT_MAP_GEN_MODES", ["carve"]))
            except Exception:  # noqa: BLE001
                default = [MapGenMode.CARVE.value]
            valid = {MapGenMode.CLASSIC.value, MapGenMode.CARVE.value}
            filtered = [m for m in default if m in valid]
            self.map_gen_modes: list[str] = filtered if filtered else [MapGenMode.CARVE.value]
        else:
            valid = {MapGenMode.CLASSIC.value, MapGenMode.CARVE.value}
            filtered = [m for m in map_gen_modes if m in valid]
            self.map_gen_modes = filtered if filtered else [MapGenMode.CARVE.value]

        # 联机
        self._as_host = as_host
        self._server = server
        self._client = client
        self._my_player_id = my_player_id
        self._maze_seed = maze_seed
        self._initial_tanks = initial_tanks or []
        self._codename = codename
        self._persistent_tanks: Optional[List[Tank]] = persistent_tanks
        self._inherit_team_wins: Optional[Dict[int, int]] = inherit_team_wins

        # ===== session 开始时间（整场级别，单机无尽跨局时继承上一局的值）=====
        self._session_started_at: str = (
            inherit_session_started_at if inherit_session_started_at else ""
        )
        self._session_start_ts: float = (
            inherit_session_start_ts if inherit_session_start_ts > 0 else 0.0
        )

        # 运行时
        self._engine: Optional[BattleEngine] = None
        self._ai_controllers: Dict[int, AIController] = {}
        self._player_tank_id: Optional[int] = None  # 自己的 tank_id

        # 输入状态（每帧基于按键构建 TankInput）
        self._keys_now: Dict[int, bool] = {}
        self._fire_held: bool = False  # 开火键边沿检测：按一下只发一颗

        # GUI
        self._btn_quit: Optional[ui_button.UIButton] = None
        self._round_lbl: Optional[ui_label.UILabel] = None

        # 结束后的短暂延时再进下一局
        self._end_delay: float = 0.0
        self._end_handled: bool = False

        # 联机：消息队列（后台线程 -> 主线程）
        self._net_queue: "queue.Queue[Tuple[str, Any]]" = queue.Queue()
        # 房主：状态广播定时器（30Hz ≈ 33ms）
        self._sync_timer: float = 0.0
        self._sync_interval_ms: float = 33.0
        self._sync_interval_sec: float = 0.033   # 客户端插值周期（与广播频率一致）
        # 房主：各客户端坦克最近已处理输入序号 {tank_id: seq}（用于 GAME_STATE_SYNC 回显 ack）
        self._last_input_seqs: Dict[int, int] = {}
        # 客户端：预测回滚 —— 已发未确认输入队列 (seq, dt, TankInput)
        #   seq = 本地预测序号(=PLAYER_INPUT.frame)；dt = 该帧真实步长(重放时复用，保证位移一致)
        self._pending_inputs: Deque[Tuple[int, float, TankInput]] = deque()
        self._pending_max: int = 30          # 上限：~500ms @60fps，防异常堆积
        self._predict_seq: int = 0           # 本地预测序号
        self._last_ack_seq: int = 0          # 服务器最近确认到的 seq
        # 客户端：远程实体快照插值缓冲（远程坦克/炮弹在两快照间线性插值，消除 30Hz 跳变）
        self._snap_prev: Dict[str, list] = {}   # 上一快照 {"tanks":[...], "projectiles":[...]}
        self._snap_cur: Dict[str, list] = {}    # 当前快照
        self._snap_alpha: float = 1.0           # 插值进度 0->1
        # 联机结束流程：房主广播 GAME_END_NOTIFY 一次；客户端收通知后停引擎
        self._end_notified: bool = False
        # 客户端：与房主连接丢失标志（update 检测后退回主菜单）
        self._host_lost: bool = False

        # ===== 联机 round 循环：一局 round 结束后，房主不退出而是重建 engine 进入下一局 =====
        self._end_round_notified: bool = False   # round 结束时发送 GAME_ROUND_START 的 guard
        # ===== 联机 session 结束（"场"结束）：收到 GAME_END_NOTIFY 或房主/客户端 ESC =====
        #   "场"结束时 switch scene；"回合"结束时只 reset engine =====
        self._end_session_notified: bool = False  # session end 已发送/处理 guard
        self._end_session_reason: Optional[str] = None  # "host_exit" | "player_exit" | "player_disconnect" ...
        self._end_session_leave_pid: int = -1    # reason=player_* 时 leave_player_id

        # ===== 联机暂停（断连等待重连）=====
        self._paused: bool = False
        self._suspend_timer: float = 0.0
        self._suspend_player_ids: set[int] = set()  # 房主：断连占位 player_id（6s 后判场结束）
        self._suspend_reason: str = ""  # 提示文案片段
        self._pause_lbl: Optional[ui_label.UILabel] = None  # 暂停遮罩 UI
        # 客户端：收到 GAME_PAUSE_NOTIFY 时 set；收到 GAME_RESUME_NOTIFY 或 6s 后判场结束
        self._client_suspend_timer: float = 0.0
        self._client_waiting_host: bool = False  # 客户端断连等待房主重连

        # ===== 联机 session 回房间时：保留 server/client 不关掉 =====
        self._keep_network_alive: bool = False

    # -------------------------------------------------
    # 进入
    # -------------------------------------------------
    def on_enter(self) -> None:
        assert self.ctx is not None
        s = self.ctx.settings
        gui = self.ctx.gui_manager

        # session 开始时间（整场级别，只在首次进入/非继承时记录）
        if self._session_start_ts == 0.0:
            import time as _t
            from datetime import datetime as _dt
            self._session_start_ts = _t.time()
            self._session_started_at = _dt.now().strftime("%Y-%m-%d %H:%M")

        # 1) 构造 / 复用 tanks
        tanks: List[Tank] = []
        if self._persistent_tanks is not None:
            # 跨局复用：保留 kills / round_survived，只 reset 每局重置项
            for t in self._persistent_tanks:
                t.reset_for_new_round(0, 0)
                tanks.append(t)
                # 玩家 tank_id 也需要重新挂引用
                if not t.is_ai:
                    self._player_tank_id = t.id
                else:
                    if t.id not in self._ai_controllers:
                        self._ai_controllers[t.id] = make_ai_controller(self.ai_difficulty)
        elif self.is_online:
            tanks = self._build_online_tanks(s)
        else:
            tanks = self._build_solo_tanks(s)

        # 2) Match + BattleEngine（联机用 maze_seed 生成与房主相同的迷宫）
        match = Match(
            match_id=self.endless_round,
            mode=self.mode,
            tanks=tanks,
            maze_size_key=self.maze_size.value,
            ai_difficulty=self.ai_difficulty,
            map_gen_modes=self.map_gen_modes,
            inherit_team_wins=self._inherit_team_wins,
        )
        self._engine = BattleEngine(
            s, match, self.ctx.event_bus,
            maze_seed=(self._maze_seed if self.is_online else None),
        )
        self._engine.start()

        # 3) 联机：把 server/client 的消息路由 + 断连路由切到本场景
        if self.is_online:
            if self._as_host and self._server is not None:
                self._server.set_message_handler(self._on_server_message)
                self._server.set_disconnect_handler(self._on_server_disconnect)
            elif (not self._as_host) and self._client is not None:
                self._client.set_message_handler(self._on_client_message)
                self._client.set_disconnect_handler(self._on_client_disconnect)

        # 4) GUI：退出按钮 + 局数
        self._btn_quit = ui_button.UIButton(
            relative_rect=pygame.Rect((20, 20), (140, 48)),
            text="← 退出", manager=gui,
        )
        title = f"第 {self.endless_round} 局  {self.ai_difficulty.value}"
        if self.is_online:
            title += f"  [{'房主' if self._as_host else '客户端'}]"
        self._round_lbl = ui_label.UILabel(
            relative_rect=pygame.Rect((20, 80), (300, 36)),
            text=title, manager=gui, object_id="#form_label",
        )

    # -------------------------------------------------
    # 坦克构造
    # -------------------------------------------------
    def _build_solo_tanks(self, s: Settings) -> List[Tank]:
        """单机：1 玩家 + (total_players-1) AI。

        队伍分配算法（非 FFA 模式）：
          actual_teams = min(N_teams, total_players)
          base = total_players // actual_teams
          rem  = total_players % actual_teams
          前 rem 队 base+1 人，其余 base 人；玩家永远在 team_id=1
        """
        tanks: List[Tank] = []
        ai_count = max(0, self.total_players - 1)

        # 玩家 tank
        if self.mode == GameMode.FREE_FOR_ALL:
            player_team = 0
        else:
            player_team = 1
        player = Tank(
            tank_id=1, codename="玩家", color=s.PLAYER_COLORS[self.player_color_idx],
            spawn_x=0, spawn_y=0,
            team=player_team,
            settings=s, control_mode=MouseKeyMode.WASDQ, is_ai=False,
        )
        tanks.append(player)
        self._player_tank_id = player.id

        # AI 队伍分配
        if self.mode == GameMode.FREE_FOR_ALL:
            # 各自为战：所有 AI team=0，互相打都算击杀
            ai_team_ids = [0] * ai_count
        else:
            # 分两队 / 分三队
            n_teams = 2 if self.mode == GameMode.TEAMS_2 else 3
            actual = min(n_teams, self.total_players)
            base = self.total_players // actual
            rem = self.total_players % actual
            team_sizes = [base + (1 if i < rem else 0) for i in range(actual)]
            # 玩家在 team=1 已占 1 人，剩余配额：team_sizes[0]-1 个 AI 去队1，其余全部 AI 去各自队
            # 构造: [1]*qty_for_team1 + [2]*qty_for_team2 + ...
            ai_team_ids: list[int] = []
            for i in range(actual):
                need = team_sizes[i] - (1 if i == 0 else 0)
                ai_team_ids.extend([i + 1] * need)
            # 安全兜底：如果 AI 配额加起来 != ai_count（整数除法边界），把多余的补到 team=2
            while len(ai_team_ids) < ai_count:
                ai_team_ids.append(2)
            ai_team_ids = ai_team_ids[:ai_count]

        # AI 颜色：排除玩家已选颜色，从剩余颜色依次取
        ai_colors = [c for i, c in enumerate(s.PLAYER_COLORS) if i != self.player_color_idx]
        for i in range(ai_count):
            team_id = ai_team_ids[i]
            ai_tank = Tank(
                tank_id=100 + i, codename=f"AI_{i+1}",
                color=ai_colors[i % len(ai_colors)],
                spawn_x=0, spawn_y=0, team=team_id, settings=s, is_ai=True,
            )
            tanks.append(ai_tank)
            self._ai_controllers[ai_tank.id] = make_ai_controller(self.ai_difficulty)
        return tanks

    def _build_online_tanks(self, s: Settings) -> List[Tank]:
        """联机：用房主广播的 initial_tanks 配置建坦克（房主客户端用同一份）。"""
        tanks: List[Tank] = []
        n_colors = len(s.PLAYER_COLORS)
        for td in self._initial_tanks:
            tid = int(td.get("id", 0))
            color_idx = int(td.get("color_idx", tid)) % n_colors
            t = Tank(
                tank_id=tid,
                codename=str(td.get("codename", "")),
                color=s.PLAYER_COLORS[color_idx],
                spawn_x=0, spawn_y=0,
                team=int(td.get("team", 0)),
                settings=s,
                control_mode=MouseKeyMode.WASDQ,
                is_ai=bool(td.get("is_ai", False)),
            )
            # 记录所属 player_id（联机断连/离开时定位坦克用；房主=0）
            t._owner_player_id = int(td.get("player_id", 0))  # type: ignore[attr-defined]
            tanks.append(t)
            # 只有房主端跑 AI（客户端不跑，由权威状态同步）
            if t.is_ai and self._as_host:
                self._ai_controllers[t.id] = make_ai_controller(self.ai_difficulty)
        # 找自己的 tank_id（player_id 匹配）
        for td in self._initial_tanks:
            if not td.get("is_ai", False) and int(td.get("player_id", -1)) == self._my_player_id:
                self._player_tank_id = int(td["id"])
                break
        return tanks

    def on_exit(self) -> None:
        if self._engine is not None:
            try:
                if self._engine.match.end_reason is None:
                    self._engine.stop(MatchEndReason.MANUAL_STOP)
            except Exception:  # noqa: BLE001
                pass
            self._engine = None
        self._ai_controllers.clear()
        # 联机：若 _keep_network_alive=True（回 RoomScene 而非主菜单），则保留 server/client；否则关闭
        if self.is_online and not self._keep_network_alive:
            if self._server is not None:
                try:
                    self._server.shutdown()
                except Exception:  # noqa: BLE001
                    pass
                self._server = None
            if self._client is not None:
                try:
                    self._client.disconnect()
                except Exception:  # noqa: BLE001
                    pass
                self._client = None
        # 暂停标签清理
        for el in [self._btn_quit, self._round_lbl, self._pause_lbl]:
            if el is not None:
                try: el.kill()
                except Exception: pass  # noqa: E722
        self._btn_quit = self._round_lbl = self._pause_lbl = None

    # -------------------------------------------------
    # 事件：按钮 + 按键
    # -------------------------------------------------
    def handle_event(self, event: pygame.event.Event) -> None:
        assert self.ctx is not None
        sm = self.ctx.scene_manager
        assert sm is not None

        if event.type == pygame_gui.UI_BUTTON_PRESSED:
            if event.ui_element is self._btn_quit:
                self._user_request_exit()
                return

        if event.type == pygame.KEYDOWN:
            self._keys_now[event.key] = True
            if event.key == pygame.K_ESCAPE:
                self._user_request_exit()
                return
        elif event.type == pygame.KEYUP:
            self._keys_now[event.key] = False

    def _user_request_exit(self) -> None:
        """玩家点「退出」按钮或按 ESC — 联机分级处理 / 单机直接退出。"""
        assert self.ctx is not None
        if self.is_online:
            log = self.ctx.logger
            if self._as_host:
                # ===== 房主 ESC =====
                log.info("房主主动 ESC → 广播 GAME_END_NOTIFY(host_exit) → 全员回主菜单")
                self._save_record_if_needed()
                if self._server is not None:
                    try:
                        self._server.broadcast(MessageType.GAME_END_NOTIFY, {
                            "reason": "host_exit",
                            "leave_player_id": 0,
                        })
                    except Exception:  # noqa: BLE001
                        pass
                self._end_session_notified = True
                self._end_session_reason = "host_exit"
                from .main_menu_scene import MainMenuScene
                self.ctx.scene_manager.switch(MainMenuScene)
            else:
                # ===== 客户端 ESC =====
                log.info(f"客户端 player_id={self._my_player_id} 主动 ESC → 发 PLAYER_LEAVE → 自己回主菜单")
                self._save_record_if_needed()
                # 发 PLAYER_LEAVE（房主收到后会广播 GAME_END_NOTIFY 让其他客户端回房间）
                if self._client is not None:
                    try:
                        self._client.send(MessageType.PLAYER_LEAVE, {
                            "player_id": self._my_player_id,
                        })
                    except Exception:  # noqa: BLE001
                        pass
                # 自己立即回主菜单（on_exit 会 disconnect client；房主那边的 PLAYER_LEAVE 处理后 switch RoomScene）
                from .main_menu_scene import MainMenuScene
                self.ctx.scene_manager.switch(MainMenuScene)
        else:
            self._save_record_if_needed(manual_exit=True)
            self._exit_to_menu()

    def _exit_to_menu(self) -> None:
        """退出战斗：联机回主菜单，单机回单机设置。"""
        assert self.ctx is not None
        sm = self.ctx.scene_manager
        assert sm is not None
        if self.is_online:
            from .main_menu_scene import MainMenuScene
            sm.switch(MainMenuScene)
        else:
            from .solo_setup_scene import SoloSetupScene
            sm.switch(SoloSetupScene)

    # =====================================================
    # 联机：网络消息处理（后台线程 -> 队列 -> 主线程）
    # =====================================================
    def _on_server_message(self, peer: ClientPeer, msg: dict) -> None:
        """房主侧后台 recv 线程回调：入队，主线程消费。"""
        self._net_queue.put(("server_msg", (peer, msg)))

    def _on_client_message(self, msg: dict) -> None:
        """客户端侧后台 recv 线程回调：入队，主线程消费。"""
        self._net_queue.put(("client_msg", msg))

    def _on_server_disconnect(self, peer: ClientPeer, exc: Optional[Exception]) -> None:
        """房主侧：某客户端断连（后台线程回调，入队主线程处理）。"""
        self._net_queue.put(("server_disconnect", (peer, exc)))

    def _on_client_disconnect(self, exc: Optional[Exception]) -> None:
        """客户端侧：与房主连接断开（后台线程回调，入队主线程处理）。"""
        self._net_queue.put(("client_disconnect", exc))

    def _drain_net_queue(self) -> None:
        """主线程每帧消费网络队列。"""
        while not self._net_queue.empty():
            try:
                evt_type, payload = self._net_queue.get_nowait()
            except queue.Empty:
                break
            try:
                if evt_type == "server_msg":
                    # payload = (peer, msg)
                    self._process_server_msg(payload[0], payload[1])
                elif evt_type == "client_msg":
                    self._process_client_msg(payload)
                elif evt_type == "server_disconnect":
                    # payload = (peer, exc)
                    self._handle_server_disconnect(payload[0])
                elif evt_type == "client_disconnect":
                    # payload = exc
                    self._host_lost = True
            except Exception:  # noqa: BLE001
                if self.ctx is not None:
                    self.ctx.logger.exception("处理网络消息异常")

    # -------------------------------------------------
    # 房主侧：客户端断连处理 —— 将其坦克标记死亡，避免卡局
    # -------------------------------------------------
    def _handle_server_disconnect(self, peer: ClientPeer) -> None:
        """房主侧：某客户端断连 —— 暂停对局，6s 等重连。"""
        if self._engine is None or self._paused:
            return
        pid = peer.player_id
        if self.ctx is not None:
            self.ctx.logger.info(f"联机玩家 player_id={pid} 断连，进入 6s 暂停等待")
        self._suspend_player_ids = {pid}
        self._suspend_timer = 6.0
        self._paused = True
        # 广播 GAME_PAUSE_NOTIFY 给所有人（含那个断连客户端 —— 但它已离线无所谓）
        if self._server is not None:
            try:
                self._server.broadcast(MessageType.GAME_PAUSE_NOTIFY, {
                    "reconnecting": [pid],
                    "wait_sec": 6.0,
                })
            except Exception:  # noqa: BLE001
                pass

    # -------------------------------------------------
    # 房主侧消息处理（主线程）
    # -------------------------------------------------
    def _process_server_msg(self, peer: ClientPeer, msg: dict) -> None:
        if self._engine is None:
            return
        msg_type = msg.get("type", "")
        data = msg.get("data", {})

        if msg_type == MessageType.PLAYER_INPUT.value:
            # 客户端发来的本帧输入 -> 注入引擎 input_overrides（下一帧 update 消费）
            tank_id = int(data.get("tank_id", -1))
            inp_data = data.get("input", {}) or {}
            inp = TankInput(
                move_x=int(inp_data.get("move_x", 0)),
                move_y=int(inp_data.get("move_y", 0)),
                fire=bool(inp_data.get("fire", False)),
            )
            # 只接受属于真实玩家坦克的输入（防伪造 AI/他人坦克输入）
            tank = self._engine.match.tanks.get(tank_id)
            owner_pid = getattr(tank, "_owner_player_id", -1) if tank is not None else -1
            if tank is not None and not tank.is_ai and owner_pid == peer.player_id:
                self._engine.set_tank_input(tank_id, inp)
                # 记录该坦克最近已处理输入序号（供下次 GAME_STATE_SYNC 回显 ack）
                self._last_input_seqs[tank_id] = int(data.get("frame", 0))
            return

        if msg_type == MessageType.PING.value:
            if self._server is not None and peer.player_id != 0:
                self._server.send_to(peer.player_id, MessageType.PONG, {})
            return

        if msg_type == MessageType.PLAYER_LEAVE.value:
            # 玩家主动离开（ESC 主动退出）：
            # 1) 立刻触发 session end（带 reason=player_exit）
            # 2) 广播 GAME_END_NOTIFY 给所有人（包括要退出的那个，但他已 switch 主菜单可能收不到 —— 没关系）
            # 3) 本方法内部触发 _end_session_reason，由 update 主循环在合适时机走 switch
            leave_pid = int(data.get("player_id", peer.player_id))
            log = self.ctx.logger if self.ctx is not None else None
            if log:
                log.info(f"房主收到 PLAYER_LEAVE：player_id={leave_pid}")
            if not self._end_session_notified:
                # 标记该玩家 tank 死亡（可选：让其他人看到他被"击杀"—— 但他自己的场景已经在 switch 了；暂不改 alive）
                self._end_session_notified = True
                self._end_session_reason = "player_exit"
                self._end_session_leave_pid = leave_pid
                self._save_record_if_needed()  # 房主自己先存
                # 广播（让其他客户端也存 + 回主菜单）
                if self._server is not None:
                    try:
                        self._server.broadcast(MessageType.GAME_END_NOTIFY, {
                            "reason": "player_exit",
                            "leave_player_id": leave_pid,
                        })
                    except Exception:  # noqa: BLE001
                        pass
                # 房主自己也回主菜单 —— 但此时 engine 还在 PLAYING 状态。
                # 我们需要让 update 主循环在下一帧看到 _end_session_reason，然后在 _handle_match_end 处理。
                # 问题是 _handle_match_end 只在 MatchState.ENDED 时被触发。
                # 简化：直接在这里走 switch（而不是等 match end）。
                self._switch_online_session_end()
            return

    # -------------------------------------------------
    # 客户端侧消息处理（主线程）
    # -------------------------------------------------
    def _process_client_msg(self, msg: dict) -> None:
        if self._engine is None:
            return
        msg_type = msg.get("type", "")
        data = msg.get("data", {})

        if msg_type == MessageType.GAME_STATE_SYNC.value:
            # 暂停中也收到 state（房主发 paused=0 的 heartbeat 也行；实际房主在暂停时跳过 broadcast，
            # 但客户端此时可能 6s 已经超时需要自己决定 exit。这里继续 apply 也无妨）
            self._apply_state_sync(data)
            return

        if msg_type == MessageType.GAME_ROUND_START.value:
            # 房主：新一局 round 开始 —— 客户端丢弃旧 engine，重建 shadow engine
            self._client_reset_for_round(data)
            return

        if msg_type == MessageType.GAME_END_NOTIFY.value:
            self._apply_game_end_with_reason(data)
            return

        if msg_type == MessageType.GAME_PAUSE_NOTIFY.value:
            self._paused = True
            reconnect_ids = data.get("reconnecting", []) or []
            wait_sec = float(data.get("wait_sec", 6.0))
            if self._pause_lbl is None and self.ctx is not None:
                self._pause_lbl = ui_label.UILabel(
                    relative_rect=pygame.Rect((0, 0), (self.ctx.settings.WINDOW_WIDTH, 80)),
                    text=f"⚠️ 对局暂停：等待玩家重连…",
                    manager=self.ctx.gui_manager, object_id="#page_title",
                )
            self._suspend_reason = ",".join(str(x) for x in reconnect_ids)
            self._client_suspend_timer = wait_sec
            return

        if msg_type == MessageType.GAME_RESUME_NOTIFY.value:
            self._paused = False
            self._client_suspend_timer = 0.0
            if self._pause_lbl is not None:
                try:
                    self._pause_lbl.set_text("")
                except Exception:  # noqa: BLE001
                    pass
            return

        if msg_type == MessageType.PONG.value:
            return

        if msg_type == MessageType.ERROR.value:
            if self.ctx is not None:
                self.ctx.logger.info(f"联机战斗收到 ERROR: {data.get('reason', '')}")
            return

    # -------------------------------------------------
    # 客户端：收到带 reason 的 GAME_END_NOTIFY → 立即 session end（不等 engine 结束）
    # -------------------------------------------------
    def _apply_game_end_with_reason(self, data: dict) -> None:
        reason = str(data.get("reason", "host_exit"))
        leave_pid = int(data.get("leave_player_id", -1))
        log = self.ctx.logger if self.ctx is not None else None
        if log:
            log.info(f"客户端收到 GAME_END_NOTIFY reason={reason} leave_pid={leave_pid}")

        self._end_session_notified = True
        self._end_session_reason = reason
        self._end_session_leave_pid = leave_pid
        self._save_record_if_needed()

        self_self_exit = reason in ("player_exit", "player_disconnect", "player_disconnect_timeout") \
            and self._my_player_id == leave_pid

        if reason in ("host_exit", "host_left"):
            # 房主退出 → 客户端回主菜单
            from .main_menu_scene import MainMenuScene
            if self.ctx is not None:
                self.ctx.logger.info("客户端：房主退出 → switch MainMenuScene")
                self.ctx.scene_manager.switch(MainMenuScene)
            return
        if self_self_exit:
            # 自己退出（兜底，理论不会走到这里）
            from .main_menu_scene import MainMenuScene
            if self.ctx is not None:
                self.ctx.scene_manager.switch(MainMenuScene)
            return
        # 其他玩家退出/断连 → 统一回主菜单（不再回房间）
        from .main_menu_scene import MainMenuScene
        if self.ctx is not None:
            self.ctx.logger.info(f"客户端 player_id={self._my_player_id}：player_id={leave_pid} 退出 → switch MainMenuScene")
            self.ctx.scene_manager.switch(MainMenuScene)
        return

    # -------------------------------------------------
    # 客户端：应用权威快照 + ack 对账回放未确认输入 + 更新插值缓冲
    # -------------------------------------------------
    def _apply_state_sync(self, snapshot: dict) -> None:
        assert self._engine is not None

        # 1) 更新远程实体插值缓冲：prev <- 旧 cur, cur <- 新快照, alpha 重置 0
        #    第一个快照：prev 仍空，alpha 设 1.0（直接用 cur，无插值对象）
        if self._snap_cur:
            self._snap_prev = self._snap_cur
            self._snap_alpha = 0.0
        else:
            self._snap_alpha = 1.0
        # 只保留渲染所需的 tank/projectile 位置列表（浅拷贝列表即可，快照 dict 用完即弃）
        self._snap_cur = {
            "tanks": list(snapshot.get("tanks", [])),
            "projectiles": list(snapshot.get("projectiles", [])),
        }

        # 2) 用权威快照覆盖引擎状态（含本地坦克位置 -> 校正为服务器权威位置）
        self._engine.apply_state_snapshot(snapshot)

        # 3) ack 对账：读服务器最近确认到的本地输入 seq，丢弃已确认输入，重放未确认输入
        ack_map = snapshot.get("last_input_seqs", {}) or {}
        # JSON 序列化后 key 为字符串；兼容 int/str 两种
        ack_seq = ack_map.get(str(self._player_tank_id),
                              ack_map.get(self._player_tank_id, self._last_ack_seq))
        ack_seq = int(ack_seq)
        self._last_ack_seq = max(self._last_ack_seq, ack_seq)
        # 丢弃已确认（seq <= ack_seq）的待回放输入
        while self._pending_inputs and self._pending_inputs[0][0] <= ack_seq:
            self._pending_inputs.popleft()
        # 重放剩余未确认输入（用各自原始 dt，位移量与原始预测逐帧一致 -> 根治回拉）
        if self._player_tank_id is not None:
            for _seq, d, inp in list(self._pending_inputs):
                self._engine.predict_local_tank(self._player_tank_id, inp, d)

    # -------------------------------------------------
    # 客户端：收到对局结束通知
    # -------------------------------------------------
    def _apply_game_end(self, data: dict) -> None:
        assert self._engine is not None
        if self._engine.match.end_reason is not None:
            return  # 已结束，避免重复处理
        # 用 LAST_TEAM_STANDING 作为远端结束原因；winner 信息由 data 携带
        self._engine.match.winner_team_id = data.get("winner_team")
        self._engine.stop(MatchEndReason.LAST_TEAM_STANDING)
        # update() 的结束分支会处理 2s 展示后退回主菜单

    # -------------------------------------------------
    # 每帧更新
    # -------------------------------------------------
    def update(self, dt: float) -> None:
        if self._engine is None:
            return

        # 0) 客户端：与房主连接丢失 → 进入暂停等待（6s 等房主重连；超时判房主丢失）
        if self.is_online and (not self._as_host) and self._host_lost \
                and not self._client_waiting_host:
            if self.ctx is not None:
                self.ctx.logger.info("客户端检测到与房主连接断开，进入 6s 等待重连")
            self._client_waiting_host = True
            self._client_suspend_timer = 6.0
            self._paused = True
            if self._pause_lbl is None and self.ctx is not None:
                self._pause_lbl = ui_label.UILabel(
                    relative_rect=pygame.Rect((0, 0), (self.ctx.settings.WINDOW_WIDTH, 80)),
                    text="⚠️ 房主连接丢失，等待房主重连…（6s 超时）",
                    manager=self.ctx.gui_manager, object_id="#page_title",
                )
            else:
                try:
                    self._pause_lbl.set_text("⚠️ 房主连接丢失，等待房主重连…（6s 超时）")
                except Exception:  # noqa: BLE001
                    pass

        # 1) 消费网络队列（联机：后台线程 -> 主线程）
        if self.is_online:
            self._drain_net_queue()

        # 1.1) drain_net_queue 内部可能触发 switch（GAME_END_NOTIFY / PLAYER_LEAVE / host_exit 等）。
        #      SceneManager.switch 是延迟到下一帧生效的，但 on_exit 已经在当前帧被调用（engine=None）。
        #      所以这里必须 return，不能再往下跑到 _update_host / _update_client / _update_solo。
        if self._end_session_notified:
            return

        # 2) 客户端：房主仍在等但 6s 超时 → 房主真的丢了 → 回主菜单
        if self._client_waiting_host:
            self._client_suspend_timer -= dt
            if self._client_suspend_timer <= 0.0:
                if self.ctx is not None:
                    self.ctx.logger.warning("客户端 6s 等房主重连超时 → 房主真丢了")
                    self._end_session_notified = True
                    self._end_session_reason = "host_exit"
                    self._save_record_if_needed()
                    from .main_menu_scene import MainMenuScene
                    self.ctx.logger.info("客户端 6s 等房主重连超时 → switch MainMenuScene")
                    self.ctx.scene_manager.switch(MainMenuScene)
                return
            # 暂停中：不跑 engine.update，只定期更新标签倒计时
            if self._pause_lbl is not None:
                try:
                    self._pause_lbl.set_text(
                        f"⚠️ 房主连接丢失，等待房主重连…（{max(0, int(self._client_suspend_timer))}s 超时）"
                    )
                except Exception:  # noqa: BLE001
                    pass
            return

        # 3) 房主：6s 暂停等待某断连客户端 → 倒计时；超时判 player_disconnect
        if self.is_online and self._as_host and self._paused:
            self._suspend_timer -= dt
            # 暂停中不跑 engine.update（但可广播 state 让客户端看到暂停状态）
            if self._suspend_timer <= 0.0:
                # 超时：触发 session end
                leave_pid = next(iter(self._suspend_player_ids), -1)
                if self.ctx is not None:
                    self.ctx.logger.warning(f"房主 6s 等重连超时 player_id={leave_pid} → session end")
                self._paused = False
                self._end_session_notified = True
                self._end_session_reason = "player_disconnect_timeout"
                self._end_session_leave_pid = leave_pid
                self._save_record_if_needed()
                if self._server is not None:
                    try:
                        self._server.broadcast(MessageType.GAME_END_NOTIFY, {
                            "reason": "player_disconnect_timeout",
                            "leave_player_id": leave_pid,
                        })
                    except Exception:  # noqa: BLE001
                        pass
                self._switch_online_session_end()
                return
            # 更新暂停标签倒计时
            if self._pause_lbl is not None:
                try:
                    self._pause_lbl.set_text(
                        f"⚠️ 对局暂停：等待玩家重连…（{max(0, int(self._suspend_timer))}s 超时）"
                    )
                except Exception:  # noqa: BLE001
                    pass
            return

        # 4) 分支：联机房主 / 联机客户端 / 单机
        if self.is_online and self._as_host:
            self._update_host(dt)
        elif self.is_online and not self._as_host:
            self._update_client(dt)
        else:
            self._update_solo(dt)

        # 5) 对局结束处理：引擎真正 ENDED 后 2s 冻结 → 走 _handle_match_end
        #    联机 round 正常结束时房主会在 _handle_match_end 里进入下一局；
        #    联机 session 结束时（_end_session_reason 非空）也走统一分发。
        if self._engine.match.state == MatchState.ENDED and not self._end_handled:
            self._end_delay += dt
            if self._end_delay >= 2.0:
                self._end_handled = True
                self._handle_match_end()

    # -------------------------------------------------
    # 单机分支
    # -------------------------------------------------
    def _update_solo(self, dt: float) -> None:
        # 玩家输入 -> TankInput
        if self._player_tank_id is not None:
            inp = self._build_player_input()
            self._engine.set_tank_input(self._player_tank_id, inp)

        # AI 输入 provider
        def ai_provider(tank: Tank, engine: BattleEngine) -> TankInput:
            c = self._ai_controllers.get(tank.id)
            if c is None:
                return TankInput()
            return c.think(tank, engine)

        self._engine.update(dt, ai_provider=ai_provider)

    # -------------------------------------------------
    # 联机房主分支：权威引擎 + 30Hz 状态广播
    # -------------------------------------------------
    def _update_host(self, dt: float) -> None:
        assert self._engine is not None
        # 1) 房主自己的玩家输入（本地，不走网络）
        if self._player_tank_id is not None:
            inp = self._build_player_input()
            self._engine.set_tank_input(self._player_tank_id, inp)

        # 2) AI 输入（房主负责跑 AI；客户端不跑，由快照同步）
        def ai_provider(tank: Tank, engine: BattleEngine) -> TankInput:
            c = self._ai_controllers.get(tank.id)
            if c is None:
                return TankInput()
            return c.think(tank, engine)

        # 3) 权威更新（消耗 input_overrides，含客户端 PLAYER_INPUT 注入的输入）
        self._engine.update(dt, ai_provider=ai_provider)

        # 4) 周期性广播 GAME_STATE_SYNC（30Hz）
        self._sync_timer += dt * 1000.0
        if self._sync_timer >= self._sync_interval_ms and self._server is not None:
            self._sync_timer = 0.0
            try:
                snapshot = self._engine.get_state_snapshot()
                # 注入 ack：各客户端坦克最近已处理输入序号（key 用字符串，JSON 序列化一致）
                snapshot["last_input_seqs"] = {
                    str(tid): seq for tid, seq in self._last_input_seqs.items()
                }
                self._server.broadcast(MessageType.GAME_STATE_SYNC, snapshot)
            except Exception:  # noqa: BLE001
                if self.ctx is not None:
                    self.ctx.logger.exception("房主广播 GAME_STATE_SYNC 失败")

        # 5) 对局结束（房主视角）：不在此处广播 GAME_END_NOTIFY。
        #    round 正常结束 → update 主循环检测到 MatchState.ENDED 后 2s → _handle_match_end() → _host_start_next_round()（发 GAME_ROUND_START）
        #    session 结束（host_exit/player_exit/disconnect） → 在 ESC / PLAYER_LEAVE / GAME_PAUSE 超时路径里提前设 _end_session_reason + 广播 GAME_END_NOTIFY + switch scene
        #    此处不再做任何广播，统一由 update → _handle_match_end → 分发

    # -------------------------------------------------
    # 联机客户端分支：本地预测 + 发送 PLAYER_INPUT + 快照校正回滚
    # -------------------------------------------------
    def _update_client(self, dt: float) -> None:
        assert self._engine is not None
        if self._player_tank_id is None:
            return
        # 0) 推进远程实体插值进度（与广播周期一致；超时则停在 1.0 等下一快照）
        self._snap_alpha = min(1.0, self._snap_alpha + dt / self._sync_interval_sec)

        # 1) 构建本帧输入
        inp = self._build_player_input()

        # 2) 本地预测：立即移动自己的坦克（仅位移 + 墙体碰撞，不开火/不推进全局 engine）
        self._engine.predict_local_tank(self._player_tank_id, inp, dt)

        # 3) 入队待回放：(seq, dt, inp) —— 重放时复用 dt，保证位移量与原始预测一致
        self._predict_seq += 1
        self._pending_inputs.append((self._predict_seq, dt, inp))
        while len(self._pending_inputs) > self._pending_max:
            self._pending_inputs.popleft()

        # 4) 发送 PLAYER_INPUT 给房主（frame=seq，房主回显 ack 用于精确对账）
        if self._client is not None:
            try:
                self._client.send(MessageType.PLAYER_INPUT, {
                    "tank_id": self._player_tank_id,
                    "input": {
                        "move_x": inp.move_x,
                        "move_y": inp.move_y,
                        "fire": inp.fire,
                    },
                    "frame": self._predict_seq,
                })
            except Exception:  # noqa: BLE001
                # 发送失败通常意味着连接已断开，记录但不打断预测
                if self.ctx is not None:
                    self.ctx.logger.info("客户端发送 PLAYER_INPUT 失败（连接异常？）")
        # 注意：客户端不调用 engine.update —— AI/炮弹/碰撞/结束判定全部由房主权威快照驱动

    # -------------------------------------------------
    # 客户端渲染：远程实体插值（本地坦克用预测位置，不插值）
    # -------------------------------------------------
    def _draw_client_interpolated(self, screen: pygame.Surface) -> None:
        assert self._engine is not None
        alpha = self._snap_alpha
        prev_tanks = self._snap_prev.get("tanks", []) if self._snap_prev else []
        cur_tanks = self._snap_cur.get("tanks", []) if self._snap_cur else []
        prev_p = self._snap_prev.get("projectiles", []) if self._snap_prev else []
        cur_p = self._snap_cur.get("projectiles", []) if self._snap_cur else []

        prev_tank_by_id = {int(td.get("id", 0)): td for td in prev_tanks}
        cur_tank_by_id = {int(td.get("id", 0)): td for td in cur_tanks}

        # 1) 临时改写远程坦克的 x/y/angle 为插值位置；本地坦克保持预测位置不动
        saved_tank: Dict[int, Tuple[float, float, float]] = {}
        for tid, t in self._engine.match.tanks.items():
            if tid == self._player_tank_id:
                continue  # 本地坦克用预测位置，不改
            saved_tank[tid] = (t.x, t.y, t.angle)
            prev_td = prev_tank_by_id.get(tid)
            cur_td = cur_tank_by_id.get(tid)
            if cur_td is not None and prev_td is not None and alpha < 1.0:
                t.x = _lerp(float(prev_td.get("x", t.x)), float(cur_td.get("x", t.x)), alpha)
                t.y = _lerp(float(prev_td.get("y", t.y)), float(cur_td.get("y", t.y)), alpha)
                t.angle = _lerp_angle(float(prev_td.get("angle", t.angle)),
                                      float(cur_td.get("angle", t.angle)), alpha)
            elif cur_td is not None:
                t.x = float(cur_td.get("x", t.x))
                t.y = float(cur_td.get("y", t.y))
                t.angle = float(cur_td.get("angle", t.angle))

        # 2) 临时改写炮弹 x/y 为插值位置（按列表索引配对）
        saved_proj: List[Tuple[float, float]] = [(p.x, p.y) for p in self._engine.projectiles]
        for i, p in enumerate(self._engine.projectiles):
            cp = cur_p[i] if i < len(cur_p) else None
            pp = prev_p[i] if i < len(prev_p) else None
            if cp is not None and pp is not None and alpha < 1.0:
                p.x = _lerp(float(pp.get("x", p.x)), float(cp.get("x", p.x)), alpha)
                p.y = _lerp(float(pp.get("y", p.y)), float(cp.get("y", p.y)), alpha)
            elif cp is not None:
                p.x = float(cp.get("x", p.x))
                p.y = float(cp.get("y", p.y))

        # 3) 绘制（引擎内部用上述临时位置画远程实体；本地坦克用其预测位置）
        try:
            self._engine.draw(screen)
        finally:
            # 恢复远程实体真实位置（逻辑层不受渲染插值影响）
            for tid, (x, y, a) in saved_tank.items():
                tk = self._engine.match.tanks.get(tid)
                if tk is not None:
                    tk.x, tk.y, tk.angle = x, y, a
            for i, p in enumerate(self._engine.projectiles):
                if i < len(saved_proj):
                    p.x, p.y = saved_proj[i]

    # -------------------------------------------------
    # 绘制
    # -------------------------------------------------
    def draw(self, screen: pygame.Surface) -> None:
        if self._engine is None:
            return
        # 客户端：远程坦克/炮弹用两快照间插值位置渲染，消除 30Hz 跳变
        if self.is_online and not self._as_host:
            self._draw_client_interpolated(screen)
        else:
            self._engine.draw(screen)

        # （无结束遮罩文字 —— 结束条件触发后战斗继续 3s，冻结后直接进下一局）

    # =====================================================
    # 内部
    # =====================================================
    def _build_player_input(self) -> TankInput:
        move_x = 0
        move_y = 0
        # WASD
        if self._keys_now.get(pygame.K_a): move_x -= 1
        if self._keys_now.get(pygame.K_d): move_x += 1
        if self._keys_now.get(pygame.K_w): move_y += 1   # W 前进
        if self._keys_now.get(pygame.K_s): move_y -= 1   # S 后退
        # 方向键也可用
        if self._keys_now.get(pygame.K_LEFT): move_x -= 1
        if self._keys_now.get(pygame.K_RIGHT): move_x += 1
        if self._keys_now.get(pygame.K_UP): move_y += 1   # ↑ 前进
        if self._keys_now.get(pygame.K_DOWN): move_y -= 1  # ↓ 后退
        # 射击：Q / 空格（边沿触发：按一下只发一颗，一直按住不连发）
        fire_key = bool(self._keys_now.get(pygame.K_q) or self._keys_now.get(pygame.K_SPACE))
        fire = fire_key and not self._fire_held
        self._fire_held = fire_key
        return TankInput(move_x=move_x, move_y=move_y, fire=fire)

    def _save_record_if_needed(self, manual_exit: bool = False) -> None:
        """保存整场战绩（单机 ESC 退出 / 联机一局结束时调用）。"""
        if self._engine is None or self.ctx is None:
            return
        player = self._engine.match.tanks.get(self._player_tank_id or -1)
        if player is None:
            return
        match = self._engine.match

        import time as _t
        # 整场时长（秒）：session 开始到现在
        if self._session_start_ts > 0:
            duration_sec = int(_t.time() - self._session_start_ts)
        else:
            duration_sec = int(match.duration_sec)

        # 队伍胜场：Team 模式才有意义；FFA 存 0
        from ..core.constants import GameMode
        if match.mode == GameMode.FREE_FOR_ALL:
            team_wins = 0
        else:
            team_wins = sum(match.team_wins.values())

        rec = BattleRecord(
            game_scope="联机" if self.is_online else "个人",
            mode=match.mode.value,
            total_rounds=self.endless_round,        # endless_round 已经是总局数
            kills=player.kills,                      # 整场累计击杀
            survived=player.round_survived,          # 整场累计存活局数
            team_wins=team_wins,
            duration_sec=max(0, duration_sec),
            started_at=self._session_started_at,
        )
        try:
            self.ctx.records_store.add(rec)
            if self.ctx.event_bus:
                self.ctx.event_bus.publish(GameEvent(EventType.RECORD_SAVED, record=rec))
        except Exception:  # noqa: BLE001
            self.ctx.logger.exception("保存战绩失败")

    def _handle_match_end(self) -> None:
        assert self.ctx is not None and self.ctx.scene_manager is not None
        log = self.ctx.logger

        # ===== 联机 session 结束：switch scene（场结束存一次战绩）=====
        if self.is_online and self._end_session_reason is not None:
            self._save_record_if_needed()  # 整场 session 存一次
            log.info(f"联机 session 结束 reason={self._end_session_reason}")
            self._switch_online_session_end()
            return

        # ===== 联机 round 正常结束：不存战绩，房主发 GAME_ROUND_START，客户端等房主指令 =====
        if self.is_online:
            if self._as_host:
                log.info("联机 round 结束，房主准备进入下一局")
                self._host_start_next_round()
            else:
                log.info("联机 round 结束（客户端），等待房主 GAME_ROUND_START")
            return

        # ===== 单机无尽模式：整场由玩家 ESC 才存，round 结束不存；直接 switch 下一局（传 persistent tanks 累计）=====
        log.info(
            f"无尽下一局：round={self.endless_round + 1}, 难度 {self.ai_difficulty.value}"
        )
        assert self._engine is not None
        self.ctx.scene_manager.switch(
            BattleScene,
            total_players=self.total_players,
            ai_difficulty=self.ai_difficulty,
            mode=self.mode,
            maze_size=self.maze_size,
            is_online=False,
            endless_round=self.endless_round + 1,
            map_gen_modes=list(self.map_gen_modes),
            player_color_idx=self.player_color_idx,
            persistent_tanks=list(self._engine.match.tanks.values()),
            inherit_team_wins=dict(self._engine.match.team_wins),
            inherit_session_started_at=self._session_started_at,
            inherit_session_start_ts=self._session_start_ts,
        )

    # =====================================================
    # 联机：session 结束 → 统一回主菜单（不保留房间）
    # =====================================================
    def _switch_online_session_end(self) -> None:
        reason = self._end_session_reason or "host_exit"
        sm = self.ctx.scene_manager
        assert sm is not None
        log = self.ctx.logger

        leave_pid = self._end_session_leave_pid
        from .main_menu_scene import MainMenuScene
        if self._as_host:
            log.info(f"房主：session 结束（reason={reason}, leave_pid={leave_pid}）→ 回主菜单")
            # 房主退出或任一客户端退出/断连 → 房主回主菜单（server 在 on_exit 被 shutdown）
            sm.switch(MainMenuScene)
        else:
            if reason in ("host_exit", "host_left"):
                # 房主退出 → 客户端回主菜单（client 在 on_exit 被 disconnect）
                sm.switch(MainMenuScene, _toast="房主退出，返回主菜单")
            else:
                # 其他玩家退出/断连，或自己退出 → 客户端回主菜单
                log.info(f"客户端 player_id={self._my_player_id}：session 结束（reason={reason}）→ 回主菜单")
                sm.switch(MainMenuScene)

    def _collect_remaining_pids(self, exclude_player_id: int) -> List[int]:
        """收集 session end 后应该保留的 player_id 列表（房主从 engine.tanks 推导）。"""
        if self._engine is None:
            return []
        pids: List[int] = []
        for t in self._engine.match.tanks.values():
            if t.is_ai:
                continue
            owner = getattr(t, "_owner_player_id", -1)
            if owner != exclude_player_id:
                pids.append(int(owner))
        # 保证房主 (pid=0) 始终在列表里
        if self._as_host and 0 not in pids:
            pids.insert(0, 0)
        return pids

    def _assemble_room_reenter_payload(
        self, leave_pid: int
    ) -> Tuple[int, Dict[str, Any], List[Dict[str, Any]]]:
        """打包回房间所需的状态：(my_player_id, 房间设置dict, 真人成员列表)。

        - my_player_id：客户端自己的 player_id（房主恒为0）
        - 房间设置：total_slots / ai_difficulty / game_mode / maze_size / map_gen_ui
        - 真人成员：从 _initial_tanks 筛选，排除已离开的玩家，保留颜色/队伍选择
        """
        # 1) 自己的 player_id
        my_pid = int(self._my_player_id)

        # 2) 房间设置（map_gen_modes 列表反向映射回 UI 选项名）
        _modes_to_ui: Dict[tuple, str] = {
            tuple(sorted([MapGenMode.CLASSIC.value])): "原版(隔间砌墙)",
            tuple(sorted([MapGenMode.CARVE.value])): "新版(破壁凿洞)",
            tuple(sorted([MapGenMode.CLASSIC.value, MapGenMode.CARVE.value])): "原版+新版(随机切换)",
        }
        map_ui = _modes_to_ui.get(tuple(sorted(self.map_gen_modes)), "新版(破壁凿洞)")
        settings_dict: Dict[str, Any] = {
            "total_slots": int(self.total_players),
            "ai_difficulty": self.ai_difficulty.value,
            "game_mode": self.mode.value,
            "maze_size": self.maze_size.value,
            "map_gen_ui": map_ui,
        }

        # 3) 真人成员列表（排除已离开的玩家 + AI）
        remaining_pids = set(self._collect_remaining_pids(exclude_player_id=leave_pid))
        members: List[Dict[str, Any]] = []
        for td in self._initial_tanks:
            if bool(td.get("is_ai", False)):
                continue
            pid = int(td.get("player_id", -1))
            if pid < 0 or pid not in remaining_pids:
                continue
            members.append({
                "id": pid,
                "codename": str(td.get("codename", "?")),
                "color_idx": int(td.get("color_idx", 0)),
                "team": int(td.get("team", 0)),
                "ready": bool(pid == 0),
                "is_host": bool(pid == 0),
            })
        return my_pid, settings_dict, members

    # =====================================================
    # 联机：房主进入下一 round
    # =====================================================
    def _host_start_next_round(self) -> None:
        """房主：round 正常结束后，不退出而是重建 engine + 新 maze + AI 难度递进，然后广播 GAME_ROUND_START。"""
        assert self.ctx is not None
        log = self.ctx.logger

        # 1) 生成新迷宫种子
        import random as _r
        new_seed = _r.randint(0, 2**31 - 1)
        self._maze_seed = new_seed
        self.endless_round += 1

        # 2) AI 难度递进：每 round 升一级（到 NIGHTMARE 封顶）
        ai_order = list(AIDifficulty)
        cur_idx = ai_order.index(self.ai_difficulty) if self.ai_difficulty in ai_order else 0
        self.ai_difficulty = ai_order[min(cur_idx + 1, len(ai_order) - 1)]

        # 3) 持久化当前 tanks（保留 kills / round_survived / color_idx / player_id / team），
        #    让它们进入下一局作为 persistent_tanks 重建 Match + BattleEngine。
        persistent = list(self._engine.match.tanks.values()) if self._engine else []
        self._persistent_tanks = persistent
        self._inherit_team_wins = dict(self._engine.match.team_wins) if self._engine else {}

        # 4) 清空旧 engine 并重建（与单机 endless 的 switch 类似，但联机用同一 BattleScene 实例）
        if self._engine is not None:
            self._engine = None

        # 重建 tanks（复用 persistent：reset_for_new_round 清空 alive/角度/ammo 等 per-round 字段，
        # 但 kills / round_survived 保留；team/color/codename 等静态字段不变）
        new_tanks: List[Tank] = []
        for t in persistent:
            t.reset_for_new_round(0, 0)
            new_tanks.append(t)
            # 房主跑的 AI 控制器必须重绑（因为 persistent_tanks 是同一批对象）
            if t.is_ai:
                self._ai_controllers[t.id] = make_ai_controller(self.ai_difficulty)
        # 找新 session 的 player_tank_id
        for t in new_tanks:
            if (not t.is_ai) and getattr(t, "_owner_player_id", -1) == self._my_player_id:
                self._player_tank_id = t.id
                break

        # 重建 Match + BattleEngine
        match = Match(
            match_id=self.endless_round,
            mode=self.mode,
            tanks=new_tanks,
            maze_size_key=self.maze_size.value,
            ai_difficulty=self.ai_difficulty,
            map_gen_modes=self.map_gen_modes,
            inherit_team_wins=self._inherit_team_wins,
        )
        self._engine = BattleEngine(
            self.ctx.settings, match, self.ctx.event_bus, maze_seed=new_seed,
        )
        self._engine.start()

        # 5) 重置 round 相关 guard + UI 标签
        self._end_delay = 0.0
        self._end_handled = False
        self._end_round_notified = False
        self._sync_timer = 0.0
        self._last_input_seqs.clear()
        self._pending_inputs.clear()
        self._snap_prev = {}
        self._snap_cur = {}
        self._snap_alpha = 1.0
        self._predict_seq = 0
        self._last_ack_seq = 0

        # 6) 更新标题 UI
        title = f"第 {self.endless_round} 局  {self.ai_difficulty.value}  [房主]"
        if self._round_lbl is not None:
            try:
                self._round_lbl.set_text(title)
            except Exception:  # noqa: BLE001
                pass

        # 7) 广播 GAME_ROUND_START（所有客户端收到后重建自己的 shadow engine）
        if self._server is not None:
            try:
                tank_snapshots = []
                for t in new_tanks:
                    tank_snapshots.append({
                        "id": t.id, "codename": t.codename,
                        "team": int(t.team), "is_ai": bool(t.is_ai),
                        "player_id": int(getattr(t, "_owner_player_id", -1)),
                        "color_idx": -1,  # 颜色保持初始，客户端直接取；这里只给 player_id 映射
                    })
                self._server.broadcast(MessageType.GAME_ROUND_START, {
                    "maze_seed": new_seed,
                    "endless_round": self.endless_round,
                    "ai_difficulty": self.ai_difficulty.value,
                    "mode": self.mode.value,
                    "maze_size": self.maze_size.value,
                    "map_gen_modes": list(self.map_gen_modes),
                    "initial_tanks": tank_snapshots,
                })
            except Exception:  # noqa: BLE001
                pass

        log.info(f"房主进入 round {self.endless_round}, seed={new_seed}, 难度={self.ai_difficulty.value}")

    # =====================================================
    # 联机：客户端收到 GAME_ROUND_START → 重建 shadow engine
    # =====================================================
    def _client_reset_for_round(self, data: dict) -> None:
        assert self.ctx is not None
        log = self.ctx.logger
        new_seed = int(data.get("maze_seed", 0))
        round_num = int(data.get("endless_round", self.endless_round + 1))
        ai_diff_val = str(data.get("ai_difficulty", self.ai_difficulty.value))
        try:
            self.ai_difficulty = AIDifficulty(ai_diff_val)
        except ValueError:
            pass
        self.endless_round = round_num
        self._maze_seed = new_seed

        # 从 GAME_ROUND_START 里的 initial_tanks 重建 persistent_tanks（保留 kills 等，
        # 但客户端 kill 由房主快照持续刷新，这里直接从权威快照里拉。
        # 实际客户端不做 persistent 重建，而是用房主每次的 snapshot 覆盖。）
        # 客户端：丢弃旧 engine，用新 seed + 原 _initial_tanks 建 Match + BattleEngine
        if self._engine is not None:
            self._engine = None

        tanks = self._build_online_tanks(self.ctx.settings)
        match = Match(
            match_id=self.endless_round,
            mode=self.mode,
            tanks=tanks,
            maze_size_key=self.maze_size.value,
            ai_difficulty=self.ai_difficulty,
            map_gen_modes=self.map_gen_modes,
            inherit_team_wins=None,
        )
        self._engine = BattleEngine(
            self.ctx.settings, match, self.ctx.event_bus, maze_seed=new_seed,
        )
        self._engine.start()

        # 重置 round 相关 guard
        self._end_delay = 0.0
        self._end_handled = False
        self._end_round_notified = False
        self._sync_timer = 0.0
        self._pending_inputs.clear()
        self._snap_prev = {}
        self._snap_cur = {}
        self._snap_alpha = 1.0
        self._predict_seq = 0
        self._last_ack_seq = 0
        self._host_lost = False
        self._client_waiting_host = False
        self._paused = False

        # 更新标题
        title = f"第 {self.endless_round} 局  {self.ai_difficulty.value}  [客户端]"
        if self._round_lbl is not None:
            try:
                self._round_lbl.set_text(title)
            except Exception:  # noqa: BLE001
                pass

        log.info(f"客户端收到 GAME_ROUND_START → round {self.endless_round}, seed={new_seed}")
