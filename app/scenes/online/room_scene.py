"""
联机 - 房间内场景
===================

负责：
- 房主：创建 GameServer（TCP） + DiscoveryBroadcaster（UDP 广播房间）
- 客户端：连接房主，发送 ROOM_JOIN_REQ
- 成员列表 + 准备按钮（非房主）+ 开始按钮（房主，全部准备后可用）
- 聊天输入框 + 聊天记录（TCP 广播）

线程安全：
- 网络回调在后台线程运行，通过 queue.Queue 传递事件到主线程
- 主线程在 update() 中消费队列，更新 UI
- 禁止从后台线程直接操作 pygame_gui 控件
"""

from __future__ import annotations

import queue
import random
import time
from typing import Any, Dict, List, Optional, Tuple

import pygame
import pygame_gui
import pygame_gui.elements.ui_button as ui_button
import pygame_gui.elements.ui_label as ui_label
import pygame_gui.elements.ui_panel as ui_panel
import pygame_gui.elements.ui_text_box as ui_tb
import pygame_gui.elements.ui_text_entry_line as ui_entry
import pygame_gui.elements.ui_selection_list as ui_list
import pygame_gui.elements.ui_drop_down_menu as ui_drop

from ...config.settings import Settings
from ...core.constants import AIDifficulty, GameMode, MapGenMode, MouseKeyMode, MazeSize
from ...network.discovery import DiscoveryBroadcaster, RoomBroadcastInfo
from ...network.server import ClientPeer, GameServer
from ...network.client import GameClient
from ...network.protocol import MessageType
from ..base_scene import Scene


# 【地图生成】UI 选项 → 实际候选模式列表（与 solo_setup_scene 保持一致）
_MAP_GEN_UI_TO_MODES: dict[str, list[str]] = {
    "原版(隔间砌墙)":         [MapGenMode.CLASSIC.value],
    "新版(破壁凿洞)":         [MapGenMode.CARVE.value],
    "原版+新版(随机切换)":   [MapGenMode.CLASSIC.value, MapGenMode.CARVE.value],
}

# 反向映射：模式列表 → UI 选项名（BattleScene 回房间时恢复 _map_gen_ui 用）
_MODES_TO_MAP_GEN_UI: dict[tuple[str, ...], str] = {
    tuple(v): k for k, v in _MAP_GEN_UI_TO_MODES.items()
}


def map_gen_modes_to_ui(modes: list[str]) -> str:
    """把 map_gen_modes 列表转回 UI 选项名；匹配不到则兜底「新版(破壁凿洞)」。"""
    key = tuple(sorted(modes)) if modes else ()
    # 先精确匹配（顺序敏感），再排序匹配
    for k, name in _MODES_TO_MAP_GEN_UI.items():
        if tuple(modes) == k:
            return name
    for k, name in _MODES_TO_MAP_GEN_UI.items():
        if sorted(k) == sorted(modes):
            return name
    return "新版(破壁凿洞)"

# 6 种坦克颜色名字（与 Settings.PLAYER_COLORS 下标一一对应）
_COLOR_NAMES: List[str] = ["红色", "蓝色", "绿色", "黄色", "紫色", "青色"]
_COLOR_LABELS: List[Tuple[int, str]] = list(enumerate(_COLOR_NAMES))   # (idx, name)

# 队伍常量：team_id = 1/2/3；FFA 时 team_id = 0
_TEAM_NAMES: List[str] = ["队A", "队B", "队C"]
_TEAM_NAME_TO_ID: Dict[str, int] = {"队A": 1, "队B": 2, "队C": 3}
_ID_TO_TEAM_NAME: Dict[int, str] = {1: "队A", 2: "队B", 3: "队C"}


class RoomScene(Scene):
    """房间内场景（房主 / 客户端）。"""

    def __init__(
        self,
        codename: str,
        as_host: bool,
        host_ip: str = "",
        host_port: int = 0,
        has_password: bool = False,
        # ===== 从联机战斗返回时的参数 =====
        existing_server: Optional[GameServer] = None,
        existing_client: Optional[GameClient] = None,
        reenter_from_battle: bool = False,
        _reenter_member_pids: Optional[List[int]] = None,
        # ===== 战斗返回时恢复房间状态（BattleScene 打包传回）=====
        reenter_my_player_id: int = 0,                          # 客户端自己的 player_id（房主恒为0）
        reenter_settings: Optional[Dict[str, Any]] = None,      # 战斗前的房间设置
        reenter_members: Optional[List[Dict[str, Any]]] = None,  # 真人成员快照（id/codename/color_idx/team）
    ) -> None:
        super().__init__()
        self.codename = codename
        self.as_host = as_host
        self.host_ip = host_ip
        self.host_port = host_port
        self.has_password = has_password
        self._reenter_from_battle = bool(reenter_from_battle)

        # 网络对象
        if reenter_from_battle:
            # 战斗返回：接管现有 server/client
            self._server = existing_server
            self._broadcaster = None  # 战斗期间我们停了广播器，RoomScene.on_enter 会重新启
            self._client = existing_client
        else:
            self._server: Optional[GameServer] = None
            self._broadcaster: Optional[DiscoveryBroadcaster] = None
            self._client: Optional[GameClient] = None
        self._reenter_member_pids = _reenter_member_pids or []
        self._reenter_my_player_id = int(reenter_my_player_id)
        self._reenter_settings = reenter_settings
        self._reenter_members = reenter_members

        # 线程安全队列：网络线程 → 主线程
        self._net_queue: queue.Queue = queue.Queue()

        # 房间状态
        # 房主 player_id=0；客户端 player_id 由服务端分配
        self._my_player_id: int = 0
        self._room_members: List[Dict[str, Any]] = []  # [{id, codename, ready, color_idx}]
        self._my_ready: bool = False        # 客户端准备状态
        self._join_result: Optional[str] = None  # None=等待, "ok"=成功, 其他=失败原因
        self._pending_clear: Optional[str] = None  # 发送后待清空的文本（防 IME TEXTINPUT 覆盖 set_text）
        self._clear_frames: int = 0                   # 待清空检测的剩余帧数

        # 房间设置（房主权威；客户端跟随 ROOM_SETTINGS_CHANGE 同步）
        self._total_slots: int = 4                     # 总人数 2~6
        self._ai_difficulty: AIDifficulty = AIDifficulty.NORMAL
        self._game_mode: GameMode = GameMode.FREE_FOR_ALL
        self._maze_size: MazeSize = MazeSize.MEDIUM
        self._map_gen_ui: str = "新版(破壁凿洞)"       # 与 solo_setup_scene 默认一致

        # UI
        self._title: Optional[ui_label.UILabel] = None
        self._back_btn: Optional[ui_button.UIButton] = None
        self._panel_left: Optional[ui_panel.UIPanel] = None
        self._panel_right: Optional[ui_panel.UIPanel] = None
        self._members_list: Optional[ui_list.UISelectionList] = None
        self._ready_btn: Optional[ui_button.UIButton] = None
        self._start_btn: Optional[ui_button.UIButton] = None
        self._chat_box: Optional[ui_tb.UITextBox] = None
        self._chat_entry: Optional[ui_entry.UITextEntryLine] = None
        self._chat_send_btn: Optional[ui_button.UIButton] = None
        self._info_lbl: Optional[ui_label.UILabel] = None
        self._settings_lbl: Optional[ui_label.UILabel] = None   # 已替换为下拉控件；保留以防老路径引用
        # 设置下拉控件（房主侧） / 值标签（客户端侧）
        self._set_labels: Dict[str, ui_label.UILabel] = {}
        self._set_dds: Dict[str, ui_drop.UIDropDownMenu] = {}
        self._set_value_labels: Dict[str, ui_label.UILabel] = {}  # 客户端侧：只读值展示
        # 坦克颜色：每人一个（自己可改）+ 列表色块
        self._my_color_dd: Optional[ui_drop.UIDropDownMenu] = None
        self._my_color_lbl: Optional[ui_label.UILabel] = None
        # 队伍：每人一个（自己可改）+ 列表标注 + 战斗状态栏
        self._my_team_dd: Optional[ui_drop.UIDropDownMenu] = None
        self._my_team_lbl: Optional[ui_label.UILabel] = None
        # 成员列表旁的颜色块（surface blit）— 画在 draw 层

        # UI 引用列表（销毁用）
        self._ui_refs: list = []

        # 防止重复刷新成员列表
        self._last_member_texts: List[str] = []

        # 进入战斗时为 True：on_exit 不关闭 server/client，所有权转交 BattleScene
        self._keep_network_alive: bool = False
        # 客户端收到 GAME_START_NOTIFY 后暂存载荷，update 末尾再切场景（避免队列循环内重入）
        self._pending_battle: Optional[dict] = None

    # ============================================================
    # 场景生命周期
    # ============================================================
    def on_enter(self) -> None:
        assert self.ctx is not None
        s = self.ctx.settings
        gui = self.ctx.gui_manager
        w, h = s.WINDOW_WIDTH, s.WINDOW_HEIGHT

        # --- 顶栏 ---
        self._title = ui_label.UILabel(
            relative_rect=pygame.Rect((w // 2 - 240, 40), (480, 54)),
            text=f"房间 - {self.codename}  ({'房主' if self.as_host else '成员'})",
            manager=gui, object_id="#page_title",
        )
        self._back_btn = ui_button.UIButton(
            relative_rect=pygame.Rect((40, 40), (200, 54)),
            text="← 返回上一页", manager=gui,
        )
        self._info_lbl = ui_label.UILabel(
            relative_rect=pygame.Rect((40, 110), (900, 28)),
            text="", manager=gui, object_id="#form_label",
        )

        # --- 左：成员面板 ---
        left_w = 560
        left_x = 60
        top_y = 160
        left_h = h - 220
        self._panel_left = ui_panel.UIPanel(
            relative_rect=pygame.Rect((left_x, top_y), (left_w, left_h)),
            manager=gui,
        )
        self._ui_refs.append(self._panel_left)

        # 设置：5 行下拉（总人数 / AI难度 / 对战模式 / 地图大小 / 地图生成）
        self._build_settings_panel(gui, self._panel_left, left_w, s)

        # 玩家自定义：坦克颜色选择下拉（自己可改自己的）
        self._build_color_selector(gui, self._panel_left, left_w, s)

        # 队伍选择（所有人可改自己的队伍；FFA 模式禁用）
        self._build_team_selector(gui, self._panel_left, left_w)

        # 成员列表（下方显示，留出设置+颜色+队伍的空间）
        mlist_top = 20 + 6 * 42 + 30 + 40  # 设置区5行 + 颜色1行 + 队伍1行 + 标题
        mlist_h = left_h - mlist_top - 120  # 下方按钮区留 120
        ui_label.UILabel(
            relative_rect=pygame.Rect((20, mlist_top), (left_w - 40, 28)),
            text="房间成员：",
            manager=gui, container=self._panel_left, object_id="#form_label",
        )
        self._members_list = ui_list.UISelectionList(
            relative_rect=pygame.Rect((20, mlist_top + 30), (left_w - 40, mlist_h)),
            item_list=[],
            manager=gui, container=self._panel_left, allow_multi_select=False,
        )
        self._ui_refs.append(self._members_list)

        # 准备 / 开始按钮
        if self.as_host:
            self._start_btn = ui_button.UIButton(
                relative_rect=pygame.Rect((left_x + 20, top_y + left_h - 80), (left_w - 40, 60)),
                text="开始游戏（等待全部准备）",
                manager=gui, object_id="#primary_btn",
            )
            self._ui_refs.append(self._start_btn)
            self._start_btn.disable()
        else:
            self._ready_btn = ui_button.UIButton(
                relative_rect=pygame.Rect((left_x + 20, top_y + left_h - 80), (left_w - 40, 60)),
                text="准备",
                manager=gui,
            )
            self._ui_refs.append(self._ready_btn)

        # --- 右：聊天面板 ---
        right_x = left_x + left_w + 40
        right_w = w - right_x - 60
        self._panel_right = ui_panel.UIPanel(
            relative_rect=pygame.Rect((right_x, top_y), (right_w, left_h)),
            manager=gui,
        )
        self._ui_refs.append(self._panel_right)

        self._chat_box = ui_tb.UITextBox(
            html_text="欢迎来到房间！<br><br>",
            relative_rect=pygame.Rect((20, 20), (right_w - 40, left_h - 110)),
            manager=gui, container=self._panel_right,
        )
        self._ui_refs.append(self._chat_box)
        self._chat_entry = ui_entry.UITextEntryLine(
            relative_rect=pygame.Rect((20, left_h - 80), (right_w - 200, 54)),
            manager=gui, container=self._panel_right,
        )
        self._chat_entry.set_text_length_limit(200)
        self._ui_refs.append(self._chat_entry)
        self._chat_send_btn = ui_button.UIButton(
            relative_rect=pygame.Rect((right_w - 170, left_h - 80), (150, 54)),
            text="发送", manager=gui, container=self._panel_right,
        )
        self._ui_refs.append(self._chat_send_btn)

        # --- 初始化房间状态 ---
        if self.as_host:
            if self._reenter_from_battle and self._server is not None:
                # 优先用 BattleScene 传回的成员快照（保留玩家战斗前选的颜色/队伍）
                if self._reenter_members:
                    members: List[Dict[str, Any]] = []
                    for m in self._reenter_members:
                        pid = int(m.get("id", 0))
                        members.append({
                            "id": pid,
                            "codename": str(m.get("codename", "?")),
                            "ready": bool(pid == 0),   # 房主已准备；其他人回房间后重置为未准备
                            "is_host": bool(pid == 0),
                            "color_idx": int(m.get("color_idx", 0)),
                            "team": int(m.get("team", 0)),
                        })
                    self._room_members = members
                    self._my_player_id = 0
                else:
                    # 兜底：从 server._clients 重建 + 重分配颜色/队伍（原逻辑）
                    active_pids = set(self._reenter_member_pids) if self._reenter_member_pids else {0}
                    if not active_pids:
                        try:
                            active_pids = set(self._server._clients.keys())  # type: ignore[attr-defined]
                        except Exception:  # noqa: BLE001
                            active_pids = {0}
                    active_pids.add(0)
                    members = []
                    for pid in sorted(active_pids):
                        is_host = (pid == 0)
                        try:
                            peer = self._server._clients.get(pid)  # type: ignore[attr-defined]
                            codename = peer.codename if peer is not None else self.codename
                        except Exception:  # noqa: BLE001
                            codename = self.codename
                        members.append({
                            "id": pid, "codename": codename,
                            "ready": bool(is_host),
                            "is_host": is_host,
                            "color_idx": 0,
                        })
                    assigned_colors: set[int] = {0}
                    for m in members:
                        if m["id"] == 0:
                            continue
                        cidx = 0
                        for c in range(len(_COLOR_NAMES)):
                            if c not in assigned_colors:
                                cidx = c
                                break
                        m["color_idx"] = cidx
                        assigned_colors.add(cidx)
                    members[0]["team"] = 1
                    for m in members[1:]:
                        m["team"] = self._pick_initial_team(members, preferred=1)
                    self._room_members = members
                    self._my_player_id = 0

                # 恢复战斗前的房间设置（BattleScene 打包传回）
                if self._reenter_settings:
                    try:
                        self._apply_settings_payload(self._reenter_settings)
                    except Exception:  # noqa: BLE001
                        pass

                # 房主端刷新下拉、颜色列表、队伍下拉
                self._rollback_color_dd(int(self._room_members[0].get("color_idx", 0)))
                self._rollback_team_dd(int(self._room_members[0].get("team", 1)))
                self._update_total_label()
                self._refresh_member_list()
            else:
                self._room_members = [
                    {"id": 0, "codename": self.codename, "ready": True, "is_host": True, "color_idx": 0, "team": 1},
                ]
                self._my_player_id = 0
                # 房主自初始：同步自己的颜色下拉、队伍下拉 + 初始化 AI 数显示
                self._rollback_color_dd(0)
                self._rollback_team_dd(1)
                self._update_total_label()
                self._refresh_member_list()
        else:
            # ===== 客户端分支：新加入 or 战斗返回 =====
            # 房间状态靠 ROOM_JOIN_RESP（新加入）或房主主动 broadcast ROOM_STATE_SYNC（战斗返回）填充。
            # 先给一个临时占位，等消息到达后刷新。
            if self._reenter_from_battle:
                # 战斗返回：用 BattleScene 传回的 player_id（GameClient 对象本身不保存 player_id）
                self._my_player_id = self._reenter_my_player_id
                # 临时占位成员列表，等房主 ROOM_STATE_SYNC 到达后覆盖
                if self._reenter_members:
                    self._room_members = [dict(m) for m in self._reenter_members]
                else:
                    self._room_members = []
                self._my_ready = False
            else:
                self._room_members = []
                self._my_player_id = -1
                self._my_ready = False

        # --- 启动网络（只有"新建房间"才启；战斗返回时 server/client 已存在，只重接管 handler）---
        if self._reenter_from_battle:
            # 重接管消息/disconnect handler —— 让 BattleScene 留下的 server/client 重新指向 RoomScene
            if self.as_host and self._server is not None:
                self._server.set_message_handler(self._on_server_message)
                self._server.set_disconnect_handler(self._on_server_disconnect)
                self.host_port = self._server.port
                self.ctx.logger.info(f"房主：战斗返回，重接管 server handler (port={self.host_port})")
            elif (not self.as_host) and self._client is not None:
                self._client.set_message_handler(self._on_client_message)
                self._client.set_disconnect_handler(self._on_client_disconnect)
                self.ctx.logger.info("客户端：战斗返回，重接管 client handler")
                # 立即发一次 ROOM_STATE_SYNC 订阅（其实 server 不会因为 client 回来就主动发；
                # 但 client 在等待房主发 ROOM_STATE_SYNC —— 房主有周期性广播吗？看 server 代码没有主动发。
                # 客户端回来后手动发 PLAYER_READY? no, 我们刚回来处于未准备。
                # 简化：让房主在 RoomScene.on_enter 时主动发一次 ROOM_STATE_SYNC 全员广播（下面 _broadcast_room_state 做）
            # 让房主主动广播一次最新房间状态（告诉回来的成员当前成员列表 + 设置）
            if self.as_host and self._server is not None:
                try:
                    self._server.broadcast(MessageType.ROOM_STATE_SYNC, self._build_room_snapshot())
                except Exception:  # noqa: BLE001
                    pass
                # 重启 UDP 房间广播器（战斗期间停了，回房间后恢复大厅可见性）
                if self._broadcaster is None:
                    self._start_broadcaster()
        else:
            if self.as_host:
                self._start_as_host()
            else:
                self._start_as_client()

    # ============================================================
    # UI 构建：设置下拉 / 颜色选择
    # ============================================================
    def _build_settings_panel(
        self,
        gui: pygame_gui.UIManager,
        container: ui_panel.UIPanel,
        panel_w: int,
        s: Settings,
    ) -> None:
        """左侧"房间设置"：总人数(2-6) / AI难度 / 对战模式 / 地图大小 / 地图生成。房主可编辑；成员禁用。"""
        total = int(self._total_slots)
        human = 1 if len(self._room_members) == 0 else len(self._room_members)
        ai_count = max(0, total - human)
        rows = [
            ("total_slots",  f"总人数（AI: {ai_count}）", [str(x) for x in range(2, 7)], str(total)),
            ("ai_difficulty", "AI 难度",     [e.value for e in AIDifficulty], self._ai_difficulty.value),
            ("mode",          "对战模式",    [e.value for e in GameMode],      self._game_mode.value),
            ("maze_size",     "地图大小",    [e.value for e in MazeSize],       self._maze_size.value),
            ("map_gen",       "地图生成",    list(_MAP_GEN_UI_TO_MODES.keys()), self._map_gen_ui),
        ]
        lbl_w = 180
        val_w = panel_w - lbl_w - 60  # 左侧 20 起始 -> 留 20 右侧
        row_h = 38
        for i, (key, title, options, default) in enumerate(rows):
            y = 16 + i * (row_h + 4)
            lbl = ui_label.UILabel(
                relative_rect=pygame.Rect((20, y), (lbl_w, row_h)),
                text=title, manager=gui, container=container, object_id="#form_label",
            )
            if self.as_host:
                # 房主：下拉可编辑
                dd = ui_drop.UIDropDownMenu(
                    options_list=options,
                    starting_option=default,
                    relative_rect=pygame.Rect((20 + lbl_w, y), (val_w, row_h)),
                    manager=gui, container=container,
                )
                self._set_labels[key] = lbl
                self._set_dds[key] = dd
            else:
                # 客户端：只读一行标签，房主改设置后我们只需 set_text 就行
                val_lbl = ui_label.UILabel(
                    relative_rect=pygame.Rect((20 + lbl_w, y), (val_w, row_h)),
                    text=str(default),
                    manager=gui, container=container, object_id="#form_label",
                )
                self._set_labels[key] = lbl
                # 复用 _set_dds 存这个"值标签"，用 None 区分（客户端无下拉）
                # 但更干净的做法：单独一个 _set_value_labels
                self._set_value_labels[key] = val_lbl

    def _update_total_label(self) -> None:
        """总人数或成员数变化时：刷新"总人数（AI: x）"标签文本（房主侧）/ 值标签（客户端侧）。"""
        total = self._total_slots
        human = max(1, len(self._room_members))
        ai_count = max(0, total - human)
        # 房主侧：左边标题刷 AI 数（原来的逻辑）
        lbl = self._set_labels.get("total_slots")
        if lbl is not None:
            try:
                lbl.set_text(f"总人数（AI: {ai_count}）")
            except Exception:  # noqa: BLE001
                pass
        # 客户端侧：右边的"值标签"也要刷（显示总人数和 AI 数）
        vbl = self._set_value_labels.get("total_slots")
        if vbl is not None:
            try:
                vbl.set_text(f"{total} 人 （AI: {ai_count}）")
            except Exception:  # noqa: BLE001
                pass

    def _build_color_selector(
        self,
        gui: pygame_gui.UIManager,
        container: ui_panel.UIPanel,
        panel_w: int,
        s: Settings,
    ) -> None:
        """颜色选择：自己专属一行，下拉显示「自己当前色 + 全体未被占用色」，避免点到别人已选色。"""
        # 找到初始颜色：房主用 0（红色）；成员加入时房主分配未占用颜色
        my_color_idx = 0
        if len(self._room_members) > 0:
            for m in self._room_members:
                if m.get("id", -1) == self._my_player_id:
                    my_color_idx = int(m.get("color_idx", 0))
                    break
        y = 16 + 5 * 42 + 10  # 设置区5行之后
        self._my_color_lbl = ui_label.UILabel(
            relative_rect=pygame.Rect((20, y), (180, 38)),
            text="我的坦克颜色：",
            manager=gui, container=container, object_id="#form_label",
        )
        self._my_color_dd = ui_drop.UIDropDownMenu(
            options_list=_COLOR_NAMES,
            starting_option=_COLOR_NAMES[my_color_idx % len(_COLOR_NAMES)],
            relative_rect=pygame.Rect((200, y), (panel_w - 220, 38)),
            manager=gui, container=container,
        )
        # 按当前成员占用态重算可选颜色
        self._refresh_color_dd_options()

    # ============================================================
    # 颜色下拉可选列表：每人仅能看见 自己当前色 + 未被别人占用色
    # ============================================================
    def _refresh_color_dd_options(self) -> None:
        """根据 _room_members + 自己的 player_id，重算 my_color_dd 的可选列表。
        规则：
          - 自己当前颜色：永远可选（保持选项中排在第一个，与 6 色顺序一致）
          - 其他颜色：若未被任何成员占用 → 可选
          - 被其他成员占用 → 不出现在下拉
        """
        if self._my_color_dd is None:
            return
        # 找到自己的颜色
        me = self._room_members_by_pid(self._my_player_id)
        my_idx = int(me.get("color_idx", 0)) if me is not None else 0
        if not (0 <= my_idx < len(_COLOR_NAMES)):
            my_idx = 0
        # 被占用颜色集合（排除自己）
        used_by_others = set()
        for m in self._room_members:
            if m.get("id", -1) == self._my_player_id:
                continue
            cidx = int(m.get("color_idx", -1))
            if 0 <= cidx < len(_COLOR_NAMES):
                used_by_others.add(cidx)
        # 可选：按 6 色顺序，是自己 或 未被他人占用（顺序保持与 _COLOR_NAMES 一致，方便用户识别）
        options: List[str] = []
        for i, name in enumerate(_COLOR_NAMES):
            if i == my_idx or i not in used_by_others:
                options.append(name)
        # 若自己的颜色不在 options 顶部排第一位（上面循环按顺序已经包含它在原位），保留顺序即可
        try:
            self._my_color_dd.set_available_options_and_current_selection(
                options_list=options,
                current_selection=_COLOR_NAMES[my_idx],
            )
        except Exception:  # noqa: BLE001
            pass

    # ============================================================
    # 房主设置变更：本地 + 广播 + 刷新"AI数"标签 + 重置非房主准备
    # ============================================================
    def _host_change_setting(self, key: str, value: str) -> None:
        assert self.as_host
        old = None
        if key == "total_slots":
            old = self._total_slots
            self._total_slots = max(2, min(6, int(value)))
            if self._total_slots < len(self._room_members):
                # 选值小于现有玩家数：钳制，把值改回原值
                self._total_slots = len(self._room_members)
                if old != self._total_slots:
                    self._sync_setting_dds_to_state()
                return
        elif key == "ai_difficulty":
            try:
                self._ai_difficulty = AIDifficulty(value)
            except ValueError:
                return
        elif key == "mode":
            try:
                self._game_mode = GameMode(value)
            except ValueError:
                return
        elif key == "maze_size":
            try:
                self._maze_size = MazeSize(value)
            except ValueError:
                return
        elif key == "map_gen":
            if value in _MAP_GEN_UI_TO_MODES:
                self._map_gen_ui = value
            else:
                return

        # 房主改设置：清空【非房主】成员的准备状态（设置变更，需其他玩家重新确认）；房主本人始终保持已准备
        if self.ctx is not None:
            self.ctx.logger.info(f"房主修改房间设置 {key}={value} -> 重置非房主准备状态")
        for m in self._room_members:
            if m.get("is_host"):
                m["ready"] = True
            else:
                m["ready"] = False
        # 客户端自身的准备标记（房主视角下 _my_ready 无意义；仅兼容存储）
        self._my_ready = True  # 房主端：保持已准备

        # 模式或总人数变更：队伍容量变化 -> 重平衡队伍
        if key in ("mode", "total_slots"):
            self._rebalance_teams()

        # 刷新本地 AI 数文本 + 队伍下拉 + 发送 ROOM_SETTINGS_CHANGE 给全员
        self._update_total_label()
        self._refresh_team_dd_options()
        self._broadcast_room_settings()
        # 同时发 ROOM_STATE_SYNC（含成员准备状态 + 重平衡后的 team）
        if self._server is not None:
            self._server.broadcast(MessageType.ROOM_STATE_SYNC, self._build_room_snapshot())

    def _broadcast_room_settings(self) -> None:
        if self._server is None:
            return
        self._server.broadcast(MessageType.ROOM_SETTINGS_CHANGE, self._build_settings_payload())

    def _build_settings_payload(self) -> dict:
        return {
            "total_slots": self._total_slots,
            "ai_difficulty": self._ai_difficulty.value,
            "game_mode": self._game_mode.value,
            "maze_size": self._maze_size.value,
            "map_gen_ui": self._map_gen_ui,
        }

    def _apply_settings_payload(self, data: dict) -> None:
        """客户端收到 ROOM_SETTINGS_CHANGE：更新本地状态 + 同步下拉展示值（不改内部选中禁用状态）。"""
        ts = int(data.get("total_slots", self._total_slots))
        ts = max(2, min(6, ts))
        self._total_slots = ts
        try:
            self._ai_difficulty = AIDifficulty(str(data.get("ai_difficulty", self._ai_difficulty.value)))
        except ValueError:
            pass
        try:
            self._game_mode = GameMode(str(data.get("game_mode", self._game_mode.value)))
        except ValueError:
            pass
        try:
            mz = str(data.get("maze_size", self._maze_size.value))
            self._maze_size = MazeSize(mz)
        except ValueError:
            pass
        if str(data.get("map_gen_ui", "")) in _MAP_GEN_UI_TO_MODES:
            self._map_gen_ui = str(data["map_gen_ui"])
        self._sync_setting_dds_to_state()
        self._update_total_label()
        # 模式/总人数变化：刷新队伍下拉（可能变 FFA 禁用，或队数变了）
        self._refresh_team_dd_options()

    def _sync_setting_dds_to_state(self) -> None:
        """设置控件跟随当前状态：房主侧刷下拉；客户端侧刷值标签（只改文字，最简单最稳）。"""
        mapping = {
            "total_slots": str(self._total_slots),
            "ai_difficulty": self._ai_difficulty.value,
            "mode": self._game_mode.value,
            "maze_size": self._maze_size.value,
            "map_gen": self._map_gen_ui,
        }
        if self.as_host:
            for k, v in mapping.items():
                dd = self._set_dds.get(k)
                if dd is None:
                    continue
                try:
                    # 用官方 API 同步下拉当前值（current_selection 传纯字符串），
                    # 不要直接改 selected_option 属性，否则展开时 default 找不到会抛 ValueError
                    dd.set_available_options_and_current_selection(
                        options_list=self._setting_dd_options(k),
                        current_selection=v,
                    )
                except Exception:  # noqa: BLE001
                    pass
        else:
            # 客户端侧：只改标签文字
            for k, v in mapping.items():
                lbl = self._set_value_labels.get(k)
                if lbl is None:
                    continue
                try:
                    lbl.set_text(str(v))
                except Exception:  # noqa: BLE001
                    pass

    def _setting_dd_options(self, key: str) -> List[str]:
        """返回某个设置下拉 key 的完整选项列表（与 _build_settings_panel 保持一致）。"""
        if key == "total_slots":
            return [str(x) for x in range(2, 7)]
        if key == "ai_difficulty":
            return [e.value for e in AIDifficulty]
        if key == "mode":
            return [e.value for e in GameMode]
        if key == "maze_size":
            return [e.value for e in MazeSize]
        if key == "map_gen":
            return list(_MAP_GEN_UI_TO_MODES.keys())
        return []

    # ============================================================
    # 颜色分配与变更
    # ============================================================
    def _first_available_color_idx(self, exclude_list: Optional[List[int]] = None) -> int:
        """找到第一个未被现有成员占用的颜色下标。exclude_list 表示跳过已算的自己。"""
        exclude = set(exclude_list or [])
        used = set(int(m.get("color_idx", 0)) for m in self._room_members if int(m.get("id", -1)) not in exclude)
        for i in range(len(_COLOR_NAMES)):
            if i not in used:
                return i
        return 0  # 兜底

    def _on_my_color_changed(self, color_name: str) -> None:
        """本地点击颜色下拉：房主直接校验+变更；客户端发 PLAYER_COLOR_CHANGE 给房主。"""
        if color_name not in _COLOR_NAMES:
            return
        new_idx = _COLOR_NAMES.index(color_name)
        if self.as_host:
            # 房主端：本地校验（不与其他成员重复）+ 广播全员
            for m in self._room_members:
                if m.get("id") == self._my_player_id:
                    continue
                if int(m.get("color_idx", -1)) == new_idx:
                    # 颜色冲突：回滚下拉
                    my_own = self._room_members_by_pid(self._my_player_id)
                    my_idx = int(my_own.get("color_idx", 0)) if my_own else 0
                    self._rollback_color_dd(my_idx)
                    if self.ctx is not None:
                        self.ctx.logger.info("房主颜色变更被拒绝：已被他人占用")
                    return
            me = self._room_members_by_pid(self._my_player_id)
            if me is not None:
                me["color_idx"] = new_idx
            self._broadcast_color_change(self._my_player_id, new_idx)
            if self._server is not None:
                self._server.broadcast(MessageType.ROOM_STATE_SYNC, self._build_room_snapshot())
            # 若我是客户端视角且房主修改成功，也需要把自己的下拉滚到新值（同 me）
            if new_idx != self._my_color_idx_current_from_dd():
                self._rollback_color_dd(new_idx)
        else:
            # 客户端：直接发送请求；房主回 PLAYER_COLOR_CHANGE 确认成功，或忽略（保持原色）
            if self._client is not None:
                try:
                    self._client.send(MessageType.PLAYER_COLOR_CHANGE, {
                        "color_idx": new_idx,
                    })
                except Exception:  # noqa: BLE001
                    pass

    def _my_color_idx_current_from_dd(self) -> int:
        if self._my_color_dd is None:
            return 0
        txt = self._my_color_dd.selected_option
        return _COLOR_NAMES.index(txt) if txt in _COLOR_NAMES else 0

    def _rollback_color_dd(self, idx: int) -> None:
        """把自己颜色下拉选项+选中值 对齐到 idx。
        注意：先按占用态刷新可用颜色（保证新值在选项列表中），再设置当前值；因此可替代 rollback/设置为新颜色两流程。"""
        # 强制把自己的颜色先写入成员态（若我存在于成员列表），这样 _refresh_color_dd_options 就会把它放进去
        if self._my_color_dd is None:
            return
        if not (0 <= idx < len(_COLOR_NAMES)):
            return
        me = self._room_members_by_pid(self._my_player_id)
        if me is not None:
            me["color_idx"] = int(idx)
        # 先刷新可用颜色：自己当前色+未占用色；_refresh_color_dd_options 内部会把 current_selection 设为 idx 对应颜色名
        self._refresh_color_dd_options()

    def _room_members_by_pid(self, pid: int) -> Optional[Dict[str, Any]]:
        for m in self._room_members:
            if m.get("id") == pid:
                return m
        return None

    def _broadcast_color_change(self, player_id: int, color_idx: int) -> None:
        if self._server is None:
            return
        self._server.broadcast(MessageType.PLAYER_COLOR_CHANGE, {
            "player_id": player_id,
            "color_idx": color_idx,
        })

    # ============================================================
    # 队伍：容量计算 / 初始分配 / 重平衡 / UI
    # ============================================================
    def _capacity_for_team(self, team_id: int) -> int:
        """给定 team_id(1/2/3)，返回该队伍当前最多可容纳人数（基于当前模式与 total_slots）。"""
        mode = self._game_mode
        total = int(self._total_slots)
        if mode == GameMode.FREE_FOR_ALL:
            return total  # FFA 不限制
        if mode == GameMode.TEAMS_2:
            if team_id not in (1, 2):
                return 0
            n_teams = 2
        else:  # TEAMS_3
            if team_id not in (1, 2, 3):
                return 0
            n_teams = 3
        actual = min(n_teams, total)
        base = total // actual
        rem = total % actual
        # 前 rem 队 base+1 人，其余 base 人
        idx = team_id - 1
        if idx >= actual:
            return 0
        return base + (1 if idx < rem else 0)

    def _is_team_available(self, team_id: int, members: Optional[List[Dict[str, Any]]] = None) -> bool:
        """队伍是否还有空位（当前 team 人数 < 容量上限）。"""
        members = members if members is not None else self._room_members
        cap = self._capacity_for_team(team_id)
        if cap <= 0:
            return False
        count = sum(1 for m in members if int(m.get("team", 0)) == team_id)
        return count < cap

    def _pick_initial_team(self, members: List[Dict[str, Any]], preferred: int = 1) -> int:
        """为新成员挑选第一个未满的队伍号（按 preferred→1→2→3 顺序尝试）。FFA 固定返回 0。"""
        if self._game_mode == GameMode.FREE_FOR_ALL:
            return 0
        # 先尝试 preferred
        order = [preferred] + [t for t in (1, 2, 3) if t != preferred]
        for t in order:
            if self._capacity_for_team(t) <= 0:
                continue
            # 直接用现有成员列表算容量；未满就能加（加一个后 count+1 <= cap）
            if self._is_team_available(t, members):
                return t
        # 兜底：返回第一个有效队伍
        for t in (1, 2, 3):
            if self._capacity_for_team(t) > 0:
                return t
        return 1

    def _rebalance_teams(self) -> None:
        """模式/总人数变更后，重新平衡队伍：
        - FFA：全部置 0
        - 团队模式：尽量保留原有 team；超出容量的成员从队尾溢出到空队伍。
        """
        if self._game_mode == GameMode.FREE_FOR_ALL:
            for m in self._room_members:
                m["team"] = 0
            return
        # 收集各队现有成员，按 player_id 升序
        bucket: Dict[int, List[Dict[str, Any]]] = {}
        for m in sorted(self._room_members, key=lambda x: int(x.get("id", 0))):
            t = int(m.get("team", 0))
            # 如果当前 team 不再有效（例如切到 TEAMS_2 但有人在队C），先塞到 bucket 0
            if self._capacity_for_team(t) <= 0:
                bucket.setdefault(0, []).append(m)
            else:
                bucket.setdefault(t, []).append(m)

        # 每队容量内的成员保留原队，溢出的收集到 overflow
        overflow: List[Dict[str, Any]] = []
        final_team: Dict[int, List[Dict[str, Any]]] = {}
        for t, ms in bucket.items():
            cap = self._capacity_for_team(t)
            if cap <= 0:
                # 原队伍不存在了，全部进 overflow
                overflow.extend(ms)
                continue
            keep = ms[:cap]
            extra = ms[cap:]
            final_team[t] = keep
            overflow.extend(extra)

        # 把 overflow 成员依次塞进未满队伍
        for m in overflow:
            placed = False
            for t in (1, 2, 3):
                if self._capacity_for_team(t) <= 0:
                    continue
                if len(final_team.get(t, [])) < self._capacity_for_team(t):
                    m["team"] = t
                    final_team.setdefault(t, []).append(m)
                    placed = True
                    break
            if not placed:
                # 兜底塞到队1（容量检查过，肯定有）
                m["team"] = 1
                final_team.setdefault(1, []).append(m)

    # ============================================================
    # UI：队伍选择下拉
    # ============================================================
    def _build_team_selector(
        self,
        gui: pygame_gui.UIManager,
        container: ui_panel.UIPanel,
        panel_w: int,
    ) -> None:
        """队伍选择下拉：所有人都可改自己的队伍；FFA 模式禁用。"""
        my_team = 1
        if len(self._room_members) > 0:
            for m in self._room_members:
                if m.get("id", -1) == self._my_player_id:
                    my_team = int(m.get("team", 1))
                    break
        y = 16 + 5 * 42 + 10 + 42  # 颜色行之后
        self._my_team_lbl = ui_label.UILabel(
            relative_rect=pygame.Rect((20, y), (180, 38)),
            text="我的队伍：",
            manager=gui, container=container, object_id="#form_label",
        )
        self._my_team_dd = ui_drop.UIDropDownMenu(
            options_list=_TEAM_NAMES,
            starting_option=_ID_TO_TEAM_NAME.get(my_team, "队A"),
            relative_rect=pygame.Rect((200, y), (panel_w - 220, 38)),
            manager=gui, container=container,
        )
        self._refresh_team_dd_options()

    def _refresh_team_dd_options(self) -> None:
        """根据当前成员 + 模式 + 容量，刷新队伍下拉可选项。
        规则：
          - FFA 模式：下拉禁用，不可选
          - 其他模式：
            * 只显示当前模式下有效的队伍（TEAMS_2 只有队A/队B；TEAMS_3 全显示）
            * 自己当前队伍：永远可选
            * 其他队伍：未满时可选；已满则不出现在下拉
        """
        if self._my_team_dd is None:
            return
        me = self._room_members_by_pid(self._my_player_id)
        my_team = int(me.get("team", 0)) if me is not None else 0

        if self._game_mode == GameMode.FREE_FOR_ALL:
            # FFA：下拉禁用，无意义
            try:
                self._my_team_dd.disable()
            except Exception:  # noqa: BLE001
                pass
            return
        try:
            self._my_team_dd.enable()
        except Exception:  # noqa: BLE001
            pass

        # 可选队伍：只保留当前模式有效 + (自己当前 或 未满)
        mode_n_teams = 2 if self._game_mode == GameMode.TEAMS_2 else 3
        options: List[str] = []
        for tid in range(1, mode_n_teams + 1):
            name = _ID_TO_TEAM_NAME[tid]
            if tid == my_team:
                options.append(name)
            elif self._is_team_available(tid):
                options.append(name)
        # 兜底：至少要有一个选项
        if not options:
            options = [_ID_TO_TEAM_NAME[t] for t in range(1, mode_n_teams + 1)]

        try:
            self._my_team_dd.set_available_options_and_current_selection(
                options_list=options,
                current_selection=_ID_TO_TEAM_NAME.get(my_team, options[0]),
            )
        except Exception:  # noqa: BLE001
            pass

    def _on_my_team_changed(self, team_name: str) -> None:
        """本地点击队伍下拉：房主直接校验+变更；客户端发 PLAYER_TEAM_CHANGE 给房主。"""
        if team_name not in _TEAM_NAME_TO_ID:
            return
        new_team = _TEAM_NAME_TO_ID[team_name]
        if self._game_mode == GameMode.FREE_FOR_ALL:
            # FFA 模式不操作，回滚
            self._rollback_team_dd(0)
            return

        if self.as_host:
            # 房主端：本地校验（队伍未满）+ 广播全员
            # 把自己从房间成员里排除，算新队伍的剩余容量
            me = self._room_members_by_pid(self._my_player_id)
            old_team = int(me.get("team", 1)) if me else 1
            if me is not None:
                others = [m for m in self._room_members if m.get("id") != self._my_player_id]
            else:
                others = list(self._room_members)
            # 直接查 others 里新队伍的 count < cap 就是可加
            if not self._is_team_available(new_team, others):
                # 回滚
                self._rollback_team_dd(old_team)
                if self.ctx is not None:
                    self.ctx.logger.info(f"房主队伍变更被拒绝：队{chr(ord('A')+new_team-1)} 已满")
                return
            if me is not None:
                me["team"] = new_team
            self._refresh_team_dd_options()
            self._server.broadcast(MessageType.ROOM_STATE_SYNC, self._build_room_snapshot())
        else:
            if self._client is not None:
                try:
                    self._client.send(MessageType.PLAYER_TEAM_CHANGE, {
                        "team": new_team,
                    })
                except Exception:  # noqa: BLE001
                    pass

    def _rollback_team_dd(self, team_id: int) -> None:
        """把自己队伍下拉选项+选中值对齐到 team_id。"""
        if self._my_team_dd is None:
            return
        # 强制把自己的 team 先写入成员态（若我存在于成员列表），这样 _refresh_team_dd_options 就会把它放进去
        me = self._room_members_by_pid(self._my_player_id)
        if me is not None:
            me["team"] = int(team_id)
        self._refresh_team_dd_options()

    def _broadcast_team_change(self) -> None:
        """队伍变动后广播 ROOM_STATE_SYNC（成员快照已包含 team 字段）。"""
        if self._server is not None:
            self._server.broadcast(MessageType.ROOM_STATE_SYNC, self._build_room_snapshot())

    # ============================================================
    # AI 数量：基于 total_slots 与成员数
    # ============================================================
    def _ai_count(self) -> int:
        """当前 AI 人数 = max(0, total_slots - 房间成员数)。"""
        return max(0, self._total_slots - max(1, len(self._room_members)))

    def on_exit(self) -> None:
        # 客户端：正常退出房间才通知房主离开（进入战斗时不通知，连接保留给战斗用）
        if not self._keep_network_alive and not self.as_host \
                and self._client is not None and self._client.connected:
            try:
                self._client.send(MessageType.PLAYER_LEAVE, {})
            except Exception:  # noqa: BLE001
                pass

        # 广播器（房间发现 UDP）始终停止：战斗期间不再接受新成员加入
        if self._broadcaster is not None:
            self._broadcaster.stop()
            self._broadcaster = None

        if self._keep_network_alive:
            # 进入战斗：保留 server/client 活体，所有权转交 BattleScene
            # （此处仅解除 RoomScene 的引用，BattleScene.on_exit 负责最终清理）
            self._server = None
            self._client = None
        else:
            if self._server is not None:
                self._server.shutdown()
                self._server = None
            if self._client is not None:
                self._client.disconnect()
                self._client = None

        # 清空队列
        while not self._net_queue.empty():
            try:
                self._net_queue.get_nowait()
            except queue.Empty:
                break

        # UI 清理
        for el in self._ui_refs:
            try: el.kill()
            except Exception: pass  # noqa: E722
        self._ui_refs.clear()
        for el in [self._title, self._back_btn, self._info_lbl]:
            if el is not None:
                try: el.kill()
                except Exception: pass  # noqa: E722
        self._title = self._back_btn = self._info_lbl = None
        self._members_list = self._ready_btn = self._start_btn = None
        self._chat_box = self._chat_entry = self._chat_send_btn = None
        self._settings_lbl = self._my_color_lbl = self._my_color_dd = None
        self._my_team_lbl = self._my_team_dd = None
        for lbl in self._set_labels.values():
            try: lbl.kill()
            except Exception: pass  # noqa: E722
        for lbl in self._set_value_labels.values():
            try: lbl.kill()
            except Exception: pass  # noqa: E722
        for dd in self._set_dds.values():
            try: dd.kill()
            except Exception: pass  # noqa: E722
        self._set_labels.clear()
        self._set_value_labels.clear()
        self._set_dds.clear()
        self._pending_clear = None
        self._clear_frames = 0

    # ============================================================
    # 每帧更新：消费网络队列 + 刷新 UI
    # ============================================================
    def update(self, dt: float) -> None:
        # 0) IME 兜底清空：发送后若 TEXTINPUT 覆盖了 set_text 的清空，这里再清一次
        #    内容匹配：仅当残留文本 == 刚发送文本时才清，不影响用户新输入
        if self._pending_clear is not None and self._chat_entry is not None \
                and self._clear_frames > 0:
            cur = self._chat_entry.get_text() or ""
            if cur == self._pending_clear:
                try:
                    self._chat_entry.set_text("")
                except Exception:  # noqa: BLE001
                    pass
                self._pending_clear = None
            else:
                self._clear_frames -= 1
                if self._clear_frames <= 0:
                    self._pending_clear = None

        # 1) 消费网络队列
        while not self._net_queue.empty():
            try:
                evt_type, payload = self._net_queue.get_nowait()
            except queue.Empty:
                break
            if evt_type == "server_msg":
                self._handle_server_msg(payload[0], payload[1])
            elif evt_type == "client_msg":
                self._handle_client_msg(payload)
            elif evt_type == "client_disconnect":
                self._handle_disconnect_client(payload)
            elif evt_type == "server_disconnect":
                self._handle_disconnect_server(payload)

        # 2) 刷新成员列表
        self._refresh_member_list()

        # 3) 更新按钮状态
        self._update_button_states()

        # 4) 客户端：收到房主 GAME_START_NOTIFY -> 切到战斗场景（队列循环外执行，避免重入）
        if self._pending_battle is not None:
            data = self._pending_battle
            self._pending_battle = None
            self._enter_battle_as_client(data)

    # ============================================================
    # 客户端：进入战斗场景
    # ============================================================
    def _enter_battle_as_client(self, data: dict) -> None:
        assert self.ctx is not None and self.ctx.scene_manager is not None
        log = self.ctx.logger
        maze_seed = int(data.get("maze_seed", 0))
        initial_tanks = list(data.get("initial_tanks", []))

        # 解析枚举（容错：无效值回退默认）
        try:
            mode = GameMode(str(data.get("mode", GameMode.FREE_FOR_ALL.value)))
        except ValueError:
            mode = GameMode.FREE_FOR_ALL
        try:
            ai_difficulty = AIDifficulty(str(data.get("ai_difficulty", AIDifficulty.NORMAL.value)))
        except ValueError:
            ai_difficulty = AIDifficulty.NORMAL
        try:
            maze_size = MazeSize(str(data.get("maze_size", MazeSize.MEDIUM.value)))
        except ValueError:
            maze_size = MazeSize.MEDIUM
        # 地图生成模式：房主发来的 list[str]，过滤合法值，空则兜底 carve
        raw_modes = list(data.get("map_gen_modes", []))
        valid = {MapGenMode.CLASSIC.value, MapGenMode.CARVE.value}
        map_gen_modes = [m for m in raw_modes if m in valid] or [MapGenMode.CARVE.value]

        if log:
            log.info(f"客户端进入战斗：seed={maze_seed}, tanks={len(initial_tanks)}, my_pid={self._my_player_id}")

        # 保留网络连接，所有权转交 BattleScene
        self._keep_network_alive = True
        from ..battle_scene import BattleScene
        self.ctx.scene_manager.switch(
            BattleScene,
            total_players=len(initial_tanks),
            ai_difficulty=ai_difficulty,
            mode=mode,
            maze_size=maze_size,
            is_online=True,
            endless_round=1,
            map_gen_modes=map_gen_modes,
            as_host=False,
            client=self._client,
            my_player_id=self._my_player_id,
            maze_seed=maze_seed,
            initial_tanks=initial_tanks,
            codename=self.codename,
        )

    # ============================================================
    # 网络：房主
    # ============================================================
    def _start_as_host(self) -> None:
        assert self.ctx is not None
        s = self.ctx.settings
        log = self.ctx.logger

        def srv_on_msg(peer: ClientPeer, msg: dict) -> None:
            """后台线程回调：把消息放入队列，主线程处理。"""
            self._net_queue.put(("server_msg", (peer, msg)))

        def srv_on_disconnect(peer: ClientPeer, exc) -> None:
            log.info(f"[Server] player={peer.player_id} 断开: {exc}")
            self._net_queue.put(("server_disconnect", (peer, exc)))

        self._server = GameServer(
            settings=s,
            on_message=srv_on_msg,
            on_client_disconnect=srv_on_disconnect,
        )
        try:
            self._server.start()
            self.host_port = self._server.port
        except Exception as exc:  # noqa: BLE001
            log.error(f"启动房主服务端失败: {exc}")
            if self._info_lbl is not None:
                self._info_lbl.set_text(f"⚠️ 启动服务端失败: {exc}")
            return

        # UDP 广播（抽成独立方法，战斗返回回房间时也可复用）
        self._start_broadcaster()

        if self._info_lbl is not None:
            self._info_lbl.set_text(
                f"✅ 房主服务端已启动（TCP {self.host_port}，UDP 广播 {s.NETWORK_DISCOVERY_PORT}）"
            )
        log.info(f"房主启动：TCP {self.host_port}, UDP 广播 {s.NETWORK_DISCOVERY_PORT}")

    def _start_broadcaster(self) -> None:
        """启动 UDP 房间广播器（新建房间 / 战斗返回回房间时复用）。"""
        assert self.ctx is not None
        s = self.ctx.settings

        def info_provider() -> Optional[RoomBroadcastInfo]:
            return RoomBroadcastInfo(
                room_name=f"{self.codename}的房间",
                host_codename=self.codename,
                tcp_port=self.host_port,
                total_slots=int(self._total_slots),
                current_players=len(self._room_members),
                has_password=self.has_password,
                maze_sizes=[m.value for m in MazeSize if m != MazeSize.ALL],
                last_seen_ts=0,
            )

        self._broadcaster = DiscoveryBroadcaster(s, info_provider)
        self._broadcaster.start()

    def _build_room_snapshot(self) -> dict:
        """构造房间快照（用于 ROOM_STATE_SYNC / ROOM_JOIN_RESP）。"""
        return {
            "members": [dict(m) for m in self._room_members],
            "total_slots": int(self._total_slots),
            "settings": self._build_settings_payload(),
        }

    # ============================================================
    # 网络：客户端
    # ============================================================
    def _start_as_client(self) -> None:
        assert self.ctx is not None
        log = self.ctx.logger

        def cli_on_msg(msg: dict) -> None:
            self._net_queue.put(("client_msg", msg))

        def cli_on_disconnect(exc) -> None:
            self._net_queue.put(("client_disconnect", exc))

        self._client = GameClient(
            settings=self.ctx.settings,
            on_message=cli_on_msg,
            on_disconnect=cli_on_disconnect,
        )
        try:
            port = self.host_port or self.ctx.settings.NETWORK_DEFAULT_PORT
            self._client.connect(self.host_ip, port)
            self._client.send(MessageType.ROOM_JOIN_REQ, {
                "codename": self.codename,
                "password": "",
            })
            if self._info_lbl is not None:
                self._info_lbl.set_text(f"连接中... {self.host_ip}:{port}")
        except Exception as exc:  # noqa: BLE001
            log.warning(f"连接房主失败: {exc}")
            if self._info_lbl is not None:
                self._info_lbl.set_text(f"⚠️ 连接房主失败: {exc}")

    # ============================================================
    # 消息处理：房主侧（主线程执行，从队列消费）
    # ============================================================
    def _handle_server_msg(self, peer: ClientPeer, msg: dict) -> None:
        assert self._server is not None
        log = self.ctx.logger if self.ctx else None
        msg_type = msg.get("type", "")
        data = msg.get("data", {})

        # --- ROOM_JOIN_REQ：未注册客户端（player_id=0）发来加入请求 ---
        if msg_type == MessageType.ROOM_JOIN_REQ.value and peer.player_id == 0:
            codename = str(data.get("codename", "")).strip()
            if not codename:
                # peer 尚未注册，不能用 send_to(0)；走 send_to_sock
                self._server.send_to_sock(
                    peer.sock, MessageType.ERROR, {"reason": "代号为空"},
                )
                return

            # 检查房间是否已满（按房主设置的总人数上限）
            if len(self._room_members) >= self._total_slots:
                self._server.send_to_sock(
                    peer.sock, MessageType.ROOM_JOIN_RESP,
                    {"ok": False, "reason": "房间已满", "player_id": 0, "room_snapshot": {}},
                )
                return

            # 注册客户端
            real_peer = self._server.register_client(peer.sock, peer.addr, codename)
            new_color = self._first_available_color_idx()
            new_team = self._pick_initial_team(self._room_members, preferred=1)
            member = {
                "id": real_peer.player_id, "codename": codename,
                "ready": False, "is_host": False, "color_idx": new_color,
                "team": new_team,
            }
            self._room_members.append(member)

            # 回复加入者：ROOM_JOIN_RESP
            self._server.send_to(real_peer.player_id, MessageType.ROOM_JOIN_RESP, {
                "ok": True,
                "reason": "",
                "player_id": real_peer.player_id,
                "room_snapshot": self._build_room_snapshot(),
                "settings": self._build_settings_payload(),
            })

            # 广播 ROOM_STATE_SYNC 给所有人（含新成员）
            self._server.broadcast(MessageType.ROOM_STATE_SYNC, self._build_room_snapshot())
            # 新成员加入后 AI 数变化，通知房主客户端刷新；房主端可选颜色减少
            self._update_total_label()
            self._refresh_color_dd_options()
            self._refresh_team_dd_options()

            # 聊天通知
            notify_text = f"{codename} 加入了房间"
            self._server.broadcast(MessageType.CHAT_MSG_BROADCAST, {
                "from_codename": "系统",
                "text": notify_text,
                "ts_ms": int(time.time() * 1000),
            })
            # 房主本地也看到
            self._append_chat("系统", notify_text)

            if log:
                log.info(f"玩家 {codename} 加入房间 player_id={real_peer.player_id}")
            return

        # --- 以下消息需要已注册的 peer ---
        if peer.player_id == 0:
            return  # 未注册且非 JOIN_REQ，忽略

        # --- PLAYER_READY ---
        if msg_type == MessageType.PLAYER_READY.value:
            ready = bool(data.get("ready", False))
            for m in self._room_members:
                if m["id"] == peer.player_id:
                    m["ready"] = ready
                    break
            # 广播新状态
            self._server.broadcast(MessageType.ROOM_STATE_SYNC, self._build_room_snapshot())
            if log:
                log.info(f"player={peer.player_id} 准备状态={ready}")
            return

        # --- CHAT_MSG_SEND ---
        if msg_type == MessageType.CHAT_MSG_SEND.value:
            text = str(data.get("text", "")).strip()
            if not text:
                return
            # 查找发送者代号
            from_name = peer.codename or "?"
            broadcast_data = {
                "from_codename": from_name,
                "text": text,
                "ts_ms": int(time.time() * 1000),
            }
            # 广播给所有客户端（含发送者）
            self._server.broadcast(MessageType.CHAT_MSG_BROADCAST, broadcast_data)
            # 房主本地也显示
            self._append_chat(from_name, text)
            return

        # --- PLAYER_COLOR_CHANGE ---
        if msg_type == MessageType.PLAYER_COLOR_CHANGE.value:
            new_idx = int(data.get("color_idx", -1))
            if 0 <= new_idx < len(_COLOR_NAMES):
                # 校验：不能与其他成员重复
                conflict = False
                for m in self._room_members:
                    if m.get("id", -1) == peer.player_id:
                        continue
                    if int(m.get("color_idx", -1)) == new_idx:
                        conflict = True
                        break
                if not conflict:
                    for m in self._room_members:
                        if m.get("id", -1) == peer.player_id:
                            m["color_idx"] = new_idx
                            break
                    # 房主端：占用态变化 → 刷新自己下拉可选颜色
                    self._refresh_color_dd_options()
                    # 广播全员确认（发送方的下拉选择已暂变，需要此消息确认生效）
                    self._broadcast_color_change(peer.player_id, new_idx)
                    self._server.broadcast(MessageType.ROOM_STATE_SYNC, self._build_room_snapshot())
            return

        # --- PLAYER_TEAM_CHANGE ---
        if msg_type == MessageType.PLAYER_TEAM_CHANGE.value:
            # FFA 模式忽略队伍变更
            if self._game_mode == GameMode.FREE_FOR_ALL:
                return
            new_team = int(data.get("team", 0))
            # 校验 team_id 有效性
            if new_team < 1 or self._capacity_for_team(new_team) <= 0:
                return
            # 容量校验：把请求者暂时移出原 team 计算
            requestor = self._room_members_by_pid(peer.player_id)
            if requestor is None:
                return
            other_members = [m for m in self._room_members if m.get("id") != peer.player_id]
            if not self._is_team_available(new_team, other_members):
                # 队伍已满，忽略
                if self.ctx is not None:
                    self.ctx.logger.info(f"服务器拒绝玩家 {peer.player_id} 队伍变更到 team={new_team}：已满")
                return
            requestor["team"] = new_team
            # 刷新房主自己的队伍下拉（可能新队伍现在满了/空了）
            self._refresh_team_dd_options()
            # 广播全员（通过 ROOM_STATE_SYNC 带成员快照）
            self._server.broadcast(MessageType.ROOM_STATE_SYNC, self._build_room_snapshot())
            return

        # --- PLAYER_LEAVE ---
        if msg_type == MessageType.PLAYER_LEAVE.value:
            self._remove_member(peer.player_id, peer.codename)
            return

        # --- PING ---
        if msg_type == MessageType.PING.value:
            self._server.send_to(peer.player_id, MessageType.PONG, {})
            return

    def _handle_disconnect_server(self, payload: tuple) -> None:
        """服务端检测到客户端断开（主线程）。"""
        peer, exc = payload
        self._remove_member(peer.player_id, peer.codename)

    def _remove_member(self, player_id: int, codename: str) -> None:
        """移除成员并广播（房主侧）。"""
        removed = None
        for i, m in enumerate(self._room_members):
            if m["id"] == player_id:
                removed = self._room_members.pop(i)
                break
        if removed is None:
            return
        # 关闭连接
        if self._server:
            self._server.remove_client(player_id)
        # 释放颜色 → 房主端可选颜色增加
        self._refresh_color_dd_options()
        # 释放队伍空位 → 房主端队伍下拉可选队伍变化
        self._refresh_team_dd_options()
        # 广播
        if self._server:
            self._server.broadcast(MessageType.ROOM_STATE_SYNC, self._build_room_snapshot())
            notify_text = f"{removed.get('codename', codename)} 离开了房间"
            self._server.broadcast(MessageType.CHAT_MSG_BROADCAST, {
                "from_codename": "系统",
                "text": notify_text,
                "ts_ms": int(time.time() * 1000),
            })
        self._append_chat("系统", f"{removed.get('codename', codename)} 离开了房间")

    # ============================================================
    # 消息处理：客户端侧（主线程执行）
    # ============================================================
    def _handle_client_msg(self, msg: dict) -> None:
        msg_type = msg.get("type", "")
        data = msg.get("data", {})

        # --- ROOM_JOIN_RESP ---
        if msg_type == MessageType.ROOM_JOIN_RESP.value:
            ok = bool(data.get("ok", False))
            reason = str(data.get("reason", ""))
            if ok:
                self._my_player_id = int(data.get("player_id", 0))
                snapshot = data.get("room_snapshot", {})
                self._room_members = list(snapshot.get("members", []))
                # 房主设置初始值
                settings = data.get("settings")
                if settings:
                    self._apply_settings_payload(settings)
                # 同步自己的颜色下拉 + 队伍下拉（按 ROOM_JOIN_RESP 成员列表）
                for m in self._room_members:
                    if m.get("id") == self._my_player_id:
                        self._rollback_color_dd(int(m.get("color_idx", 0)))
                        self._rollback_team_dd(int(m.get("team", 1)))
                        break
                self._join_result = "ok"
                if self._info_lbl is not None:
                    self._info_lbl.set_text(f"✅ 已加入房间（玩家ID: {self._my_player_id}）")
                self._append_chat("系统", "你已加入房间")
            else:
                self._join_result = reason or "加入失败"
                if self._info_lbl is not None:
                    self._info_lbl.set_text(f"❌ 加入失败: {reason}")
            return

        # --- ROOM_SETTINGS_CHANGE：房主改了设置 -> 同步本地状态 + 下拉 + AI数 ---
        if msg_type == MessageType.ROOM_SETTINGS_CHANGE.value:
            self._apply_settings_payload(data)
            return

        # --- ROOM_STATE_SYNC ---
        if msg_type == MessageType.ROOM_STATE_SYNC.value:
            members = data.get("members", [])
            self._room_members = list(members)
            # 同步房主的房间设置（战斗返回后对齐设置；新加入时 ROOM_JOIN_RESP 已带 settings）
            snap_settings = data.get("settings")
            if snap_settings:
                try:
                    self._apply_settings_payload(snap_settings)
                except Exception:  # noqa: BLE001
                    pass
            # 更新自己的准备状态 + 颜色下拉 + 队伍下拉 + AI数
            my_color = 0
            my_team = 1
            for m in self._room_members:
                if m.get("id") == self._my_player_id:
                    self._my_ready = bool(m.get("ready", False))
                    my_color = int(m.get("color_idx", 0))
                    my_team = int(m.get("team", 1))
                    break
            # 先写入自己的颜色态再刷新：保证下拉当前值正确 + 可选颜色=自己色+未占用色
            self._rollback_color_dd(my_color)
            self._rollback_team_dd(my_team)
            self._update_total_label()
            self._refresh_member_list()
            self._update_button_states()
            return

        # --- PLAYER_COLOR_CHANGE：全员颜色变更广播（房主确认后 -> 下拉刷新）---
        if msg_type == MessageType.PLAYER_COLOR_CHANGE.value:
            pid = int(data.get("player_id", -1))
            cidx = int(data.get("color_idx", 0))
            for m in self._room_members:
                if m.get("id") == pid:
                    m["color_idx"] = cidx
                    break
            # 不管是不是自己：占用态都变了 → 重算「自己当前色+未占用色」
            self._refresh_color_dd_options()
            return

        # --- CHAT_MSG_BROADCAST ---
        if msg_type == MessageType.CHAT_MSG_BROADCAST.value:
            from_name = str(data.get("from_codename", "?"))
            text = str(data.get("text", ""))
            self._append_chat(from_name, text)
            return

        # --- GAME_START_NOTIFY：房主开始游戏 -> 暂存载荷，update 末尾切到战斗场景 ---
        if msg_type == MessageType.GAME_START_NOTIFY.value:
            from app.game.sound_manager import SoundManager
            SoundManager.instance().play("kada")
            self._pending_battle = data
            return

        # --- PONG ---
        if msg_type == MessageType.PONG.value:
            return

        # --- ERROR ---
        if msg_type == MessageType.ERROR.value:
            reason = str(data.get("reason", "未知错误"))
            if self._info_lbl is not None:
                self._info_lbl.set_text(f"❌ 错误: {reason}")
            return

    def _handle_disconnect_client(self, exc) -> None:
        """客户端检测到连接断开（主线程）。"""
        if self._info_lbl is not None:
            self._info_lbl.set_text("⚠️ 与房主的连接已断开")
        self._append_chat("系统", "与房主的连接已断开")

    # ============================================================
    # UI 更新
    # ============================================================
    def post_gui_draw(self, screen: pygame.Surface) -> None:
        """GUI 绘制之后：在成员列表每行最左侧画颜色方块（覆盖 GUI 层之上）。
        方块画在行高 1/2 处，最靠左内边距 6（UISelectionList 默认每行有内边距）。"""
        if self._members_list is None or not self._room_members:
            return
        try:
            abs_rect = self._members_list.get_absolute_rect()
        except Exception:  # noqa: BLE001
            return
        try:
            row_ht = self._members_list.row_height
        except Exception:  # noqa: BLE001
            row_ht = 36
        if self.ctx is None:
            return
        colors = self.ctx.settings.PLAYER_COLORS
        for i, m in enumerate(self._room_members):
            cidx = int(m.get("color_idx", 0)) % len(colors)
            col = colors[cidx]
            y = abs_rect.y + i * row_ht + (row_ht - 20) // 2
            x = abs_rect.x + 6
            rect = pygame.Rect(x, y, 20, 20)
            pygame.draw.rect(screen, col, rect, border_radius=4)
            pygame.draw.rect(screen, (20, 22, 30), rect, width=2, border_radius=4)

    def _refresh_member_list(self) -> None:
        """刷新成员列表 UI（文本含颜色名、队伍、房主/成员标签、准备状态）；颜色方块由 post_gui_draw() 画在最左 26px 之上，因此每行文本前加空格预留。"""
        if self._members_list is None:
            return
        texts = []
        for m in self._room_members:
            tag = "[房主]" if m.get("is_host") else "[成员]"
            cidx = int(m.get("color_idx", 0))
            cname = _COLOR_NAMES[cidx] if 0 <= cidx < len(_COLOR_NAMES) else "?"
            ready = "✓已准备" if m.get("ready") else "✗未准备"
            # 队伍：FFA 不显示；团队模式显示队A/队B/队C
            tid = int(m.get("team", 0))
            if self._game_mode != GameMode.FREE_FOR_ALL and tid >= 1:
                tname = f"[{_ID_TO_TEAM_NAME.get(tid, '队?')}]"
            else:
                tname = ""
            # 前置空格：给左侧 26px 的颜色方块让出位置（否则会被方块盖住开头文字）
            texts.append(f"        {tag} [{cname}] {tname} {m.get('codename', '?')}  {ready}".rstrip())
        if texts != self._last_member_texts:
            self._last_member_texts = texts
            try:
                self._members_list.set_item_list(texts if texts else ["（暂无成员）"])
            except Exception:  # noqa: BLE001
                pass

    def _update_button_states(self) -> None:
        """更新准备/开始按钮状态。"""
        if self.as_host and self._start_btn is not None:
            # 房主：全部准备才能开始
            all_ready = (len(self._room_members) > 1 and
                         all(m.get("ready") for m in self._room_members))
            if all_ready:
                self._start_btn.enable()
                if "等待" in (self._start_btn.text or ""):
                    self._start_btn.set_text("开始游戏")
            else:
                self._start_btn.disable()
                self._start_btn.set_text(f"开始游戏（等待全部准备 {sum(1 for m in self._room_members if m.get('ready'))}/{len(self._room_members)}）")
        elif not self.as_host and self._ready_btn is not None:
            if self._my_ready:
                self._ready_btn.set_text("取消准备")
            else:
                self._ready_btn.set_text("准备")

    def _append_chat(self, from_name: str, text: str) -> None:
        """
        追加聊天消息到 UI（仅主线程调用）。

        - 用户正在底部：允许 pygame_gui 自动滚到底跟进新消息（符合看最新习惯）
        - 用户正在查看历史：恢复原滚动位置不打断阅读

        关键机制（见 pygame_gui 源码）：
        - 文本滚动只认 scroll_bar.start_percentage（0~1，文本顶部露出比例）
        - 滚到底 = start_percentage == 1.0 - visible_percentage
        - 恢复必须用 set_scroll_from_start_percentage（同步滑块位置）+ redraw_from_text_block 强制重绘
        """
        if self._chat_box is None:
            return
        safe_name = html_escape(from_name)
        safe_text = html_escape(text)
        html = f"<b>{safe_name}</b>: {safe_text}<br>"

        # append 前记录 start_percentage + 判断是否在底部
        sb = self._chat_box.scroll_bar
        old_start_pct = 0.0
        at_bottom = True
        if sb is not None and sb.scrollable_height > 0:
            old_start_pct = sb.start_percentage
            # 滚到底时 start_percentage = 1.0 - visible_percentage
            at_bottom = old_start_pct >= (1.0 - sb.visible_percentage) - 0.01

        try:
            self._chat_box.append_html_text(html)
        except Exception:  # noqa: BLE001
            return

        # 用户在看历史：恢复到原位置（同步滑块 + 强制重绘，避免闪烁）
        if not at_bottom:
            sb_after = self._chat_box.scroll_bar
            if sb_after is not None and sb_after.scrollable_height > 0:
                try:
                    sb_after.set_scroll_from_start_percentage(old_start_pct)
                    self._chat_box.redraw_from_text_block()
                except Exception:  # noqa: BLE001
                    pass

    # ============================================================
    # 事件处理
    # ============================================================
    def handle_event(self, event: pygame.event.Event) -> None:
        assert self.ctx is not None and self.ctx.scene_manager is not None
        sm = self.ctx.scene_manager
        log = self.ctx.logger

        if event.type == pygame_gui.UI_BUTTON_PRESSED:
            if event.ui_element is self._back_btn:
                self._go_back()
                return

            if event.ui_element is self._chat_send_btn:
                self._send_chat()
                return

            if event.ui_element is self._ready_btn:
                self._toggle_ready()
                return

            if event.ui_element is self._start_btn:
                self._try_start_game()
                return

        # 房间设置下拉变更（房主：更新设置+广播；成员：此路径不会触发，因为下拉被 disable()）
        if event.type == pygame_gui.UI_DROP_DOWN_MENU_CHANGED:
            for key, dd in self._set_dds.items():
                if event.ui_element is dd:
                    if self.as_host:
                        self._host_change_setting(key, event.text)
                    else:
                        # 防御：成员端本该不能改，被触发就回滚展示值
                        self._sync_setting_dds_to_state()
                    return
            if event.ui_element is self._my_color_dd:
                self._on_my_color_changed(event.text)
                return
            if event.ui_element is self._my_team_dd:
                self._on_my_team_changed(event.text)
                return

        # 回车发送聊天：用 KEYUP 而非 KEYDOWN
        # 原因：中文输入法（IME）下，KEYDOWN K_RETURN 后会紧跟一个 TEXTINPUT 事件
        # （IME 确认候选产生的字符），pygame_gui 处理 TEXTINPUT 时会向输入框插入字符，
        # 覆盖 _send_chat 里 set_text("") 的清空 → 字不消失。
        # KEYUP 在 TEXTINPUT 之后，此时清空不会被覆盖。
        # （pygame_gui 的 UI_TEXT_ENTRY_FINISHED 在 IME 下因 text_entered 标志不产生，故不用）
        if event.type == pygame.KEYUP:
            if event.key == pygame.K_RETURN and self._chat_entry is not None \
                    and self._chat_entry.is_focused:
                self._send_chat()
                return

    # ============================================================
    # 业务动作
    # ============================================================
    def _go_back(self) -> None:
        """返回上一页（清理网络资源由 on_exit 处理）。"""
        assert self.ctx is not None and self.ctx.scene_manager is not None
        if self.as_host:
            from .codename_scene import CodenameScene
            self.ctx.scene_manager.switch(CodenameScene)
        else:
            from .lobby_scene import LobbyScene
            self.ctx.scene_manager.switch(LobbyScene, codename=self.codename)

    def _send_chat(self) -> None:
        """发送聊天消息。"""
        if self._chat_entry is None:
            return
        text = (self._chat_entry.get_text() or "").strip()
        if not text:
            return
        # 清空输入框（IME 下 TEXTINPUT 可能覆盖此清空，update 里兜底再清）
        self._chat_entry.set_text("")
        self._pending_clear = text
        self._clear_frames = 5

        if self.as_host:
            # 房主：本地显示 + 广播给所有客户端
            self._append_chat(self.codename, text)
            if self._server is not None:
                self._server.broadcast(MessageType.CHAT_MSG_BROADCAST, {
                    "from_codename": self.codename,
                    "text": text,
                    "ts_ms": int(time.time() * 1000),
                })
        else:
            # 客户端：发送给房主，由房主广播
            if self._client is not None and self._client.connected:
                try:
                    self._client.send(MessageType.CHAT_MSG_SEND, {"text": text})
                except Exception as exc:  # noqa: BLE001
                    self._append_chat("系统", f"发送失败: {exc}")

    def _toggle_ready(self) -> None:
        """切换准备状态（客户端）。"""
        from app.game.sound_manager import SoundManager
        SoundManager.instance().play("kada")
        self._my_ready = not self._my_ready
        if self._client is not None and self._client.connected:
            try:
                self._client.send(MessageType.PLAYER_READY, {"ready": self._my_ready})
            except Exception as exc:  # noqa: BLE001
                self._my_ready = not self._my_ready  # 回退
                if self._info_lbl is not None:
                    self._info_lbl.set_text(f"⚠️ 准备失败: {exc}")

    def _build_initial_tanks(self) -> List[Dict[str, Any]]:
        """
        构造战斗初始坦克列表（房主/客户端共用同一份配置，保证双方 tank_id/team/颜色一致）。

        规则：
        - 真人成员按 player_id 升序（房主=0 在前），tank_id = 1, 2, ...
        - 真人坦克 color_idx = 成员表中分配的 color_idx（房主/玩家已选定且不重复）
        - 真人坦克 team = 成员表中选择的 team（FFA 时全员 team=0）
        - 不足 _total_slots 的位置用 AI 补齐（tank_id = 101, 102, ...）
          AI 颜色：从 6 色中未占用下标循环取
          AI 队伍：按队伍容量上限依次填充剩余空位
        """
        tanks: List[Dict[str, Any]] = []
        humans = sorted(self._room_members, key=lambda m: m.get("id", 0))
        # 已占用颜色（真人）
        used_colors = set(int(m.get("color_idx", 0)) for m in humans)
        n_colors = len(_COLOR_NAMES)
        # 取所有可用未占用颜色（6 色池中没被人占的），AI 依次复用顺序
        free_colors: List[int] = [c for c in range(n_colors) if c not in used_colors]
        if not free_colors:
            free_colors = list(range(n_colors))
        free_ptr = 0

        mode = self._game_mode

        for m in humans:
            cidx = int(m.get("color_idx", 0))
            if not (0 <= cidx < n_colors):
                cidx = 0
            # 真人 team：FFA → 0；团队模式 → 成员选择的 team
            if mode == GameMode.FREE_FOR_ALL:
                team = 0
            else:
                team = int(m.get("team", 1))
            tanks.append({
                "id": len(tanks) + 1,
                "codename": str(m.get("codename", "?")),
                "team": team,
                "is_ai": False,
                "player_id": int(m.get("id", 0)),
                "color_idx": cidx,
            })

        # AI 队伍分配：按容量补齐（真人已占的队伍位要跳过）
        # 先统计每个队伍已占的人数（真人）
        team_used: Dict[int, int] = {}
        for t in tanks:
            tid = int(t["team"])
            if tid > 0:  # FFA 不参与
                team_used[tid] = team_used.get(tid, 0) + 1

        num_ai = max(0, int(self._total_slots) - len(humans))
        ai_teams: List[int] = []
        if mode == GameMode.FREE_FOR_ALL:
            ai_teams = [0] * num_ai
        else:
            # 从 team=1,2,3 依次填充剩余容量
            for tid in (1, 2, 3):
                if self._capacity_for_team(tid) <= 0:
                    continue
                cap = self._capacity_for_team(tid)
                used = team_used.get(tid, 0)
                remaining = cap - used
                ai_teams.extend([tid] * max(0, remaining))
                if len(ai_teams) >= num_ai:
                    break
            # 兜底：如果容量算下来不够（极端边界），塞到队1
            while len(ai_teams) < num_ai:
                ai_teams.append(1)
            ai_teams = ai_teams[:num_ai]

        for i in range(num_ai):
            cidx = free_colors[free_ptr % len(free_colors)]
            free_ptr += 1
            tanks.append({
                "id": 101 + i,
                "codename": f"AI_{i + 1}",
                "team": ai_teams[i],
                "is_ai": True,
                "player_id": -1,
                "color_idx": cidx,
            })
        return tanks

    def _try_start_game(self) -> None:
        """房主：尝试开始游戏（检查全部准备）-> 广播 GAME_START_NOTIFY 并切到战斗场景。"""
        assert self.ctx is not None
        log = self.ctx.logger
        all_ready = all(m.get("ready") for m in self._room_members)
        if not all_ready or len(self._room_members) < 2:
            if self._info_lbl is not None:
                self._info_lbl.set_text("⚠️ 需要所有成员准备才能开始")
            return

        # 对团队模式：坦克总数不足整队时提示（soft 警告但不阻塞，允许少于配置的情况由 BattleEngine 处理）
        if self._game_mode == GameMode.TEAMS_2 and int(self._total_slots) < 2:
            if self._info_lbl is not None:
                self._info_lbl.set_text("⚠️ 分两队模式总人数至少 = 2")
            return
        if self._game_mode == GameMode.TEAMS_3 and int(self._total_slots) < 2:
            if self._info_lbl is not None:
                self._info_lbl.set_text("⚠️ 分三队模式总人数至少 = 2")
            return

        # 1) 生成迷宫种子 + 初始坦克配置（房主客户端共用）
        maze_seed = random.randint(0, 2**31 - 1)
        initial_tanks = self._build_initial_tanks()
        mode = self._game_mode
        ai_difficulty = self._ai_difficulty
        maze_size = self._maze_size
        map_gen_modes = list(_MAP_GEN_UI_TO_MODES.get(self._map_gen_ui, [MapGenMode.CARVE.value]))

        # 2) 房主本地先播 kada（和客户端收到 GAME_START_NOTIFY 时同步）
        from app.game.sound_manager import SoundManager
        SoundManager.instance().play("kada")

        # 3) 广播 GAME_START_NOTIFY（含完整战斗参数，客户端据此切场景）
        if self._server is not None:
            self._server.broadcast(MessageType.GAME_START_NOTIFY, {
                "maze_seed": maze_seed,
                "initial_tanks": initial_tanks,
                "mode": mode.value,
                "ai_difficulty": ai_difficulty.value,
                "maze_size": maze_size.value,
                "map_gen_modes": map_gen_modes,
                "total_slots": int(self._total_slots),
            })
        if log:
            log.info(f"房主开始游戏：seed={maze_seed}, tanks={len(initial_tanks)}, 已广播 GAME_START_NOTIFY")

        # 3) 标记保留网络连接，切到 BattleScene（房主端）
        self._keep_network_alive = True
        from ..battle_scene import BattleScene
        self.ctx.scene_manager.switch(
            BattleScene,
            total_players=len(initial_tanks),
            ai_difficulty=ai_difficulty,
            mode=mode,
            maze_size=maze_size,
            is_online=True,
            endless_round=1,
            map_gen_modes=map_gen_modes,
            as_host=True,
            server=self._server,
            my_player_id=0,
            maze_seed=maze_seed,
            initial_tanks=initial_tanks,
            codename=self.codename,
        )


# 简单 HTML 转义（聊天用）
def html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
