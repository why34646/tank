"""
联机 - 房间大厅（发现房间）
==============================

功能：
- 显示玩家代号
- 按钮：[ 刷新房间 ]、[ 返回上一页 ]
- 列表显示当前发现到的房间（房主代号、总人数、当前人数、是否有密码、地图大小多选数）
- 列表中选中房间后，"加入房间"按钮可用；若房间有密码，弹出密码输入框

架构阶段：
- UI 完整
- DiscoveryListener 占位启动与停止（实际此时可能没有任何房间广播，但列表显示为"正在监听..." / "未发现房间"）
- 点击"加入房间"：验证代号 + 密码（若有）后，切到 RoomScene(as_host=False, host_addr=...)
"""

from __future__ import annotations

import re
from typing import List, Optional

import pygame
import pygame_gui
import pygame_gui.elements.ui_button as ui_button
import pygame_gui.elements.ui_label as ui_label
import pygame_gui.elements.ui_selection_list as ui_list

from ...network.discovery import DiscoveryListener, RoomBroadcastInfo
from ..base_scene import Scene


class LobbyScene(Scene):
    """房间大厅（发现房间）。"""

    def __init__(self, codename: str) -> None:
        super().__init__()
        self.codename = codename

        self._listener: Optional[DiscoveryListener] = None

        # UI
        self._title: Optional[ui_label.UILabel] = None
        self._back_btn: Optional[ui_button.UIButton] = None
        self._info_lbl: Optional[ui_label.UILabel] = None
        self._refresh_btn: Optional[ui_button.UIButton] = None
        self._join_btn: Optional[ui_button.UIButton] = None
        self._list: Optional[ui_list.UISelectionList] = None
        self._status_lbl: Optional[ui_label.UILabel] = None

        # 保存列表项 -> RoomBroadcastInfo 的映射
        self._items: List[RoomBroadcastInfo] = []
        # 上次构建列表用的房间 key 集合（仅增删房间时才重建，避免清空选中态）
        self._last_room_keys: List[tuple] = []

    # -------------------------------------------------
    def on_enter(self) -> None:
        assert self.ctx is not None
        s = self.ctx.settings
        gui = self.ctx.gui_manager
        w, h = s.WINDOW_WIDTH, s.WINDOW_HEIGHT

        self._title = ui_label.UILabel(
            relative_rect=pygame.Rect((w // 2 - 200, 60), (400, 60)),
            text="联机大厅 - 搜索房间",
            manager=gui, object_id="#page_title",
        )
        self._info_lbl = ui_label.UILabel(
            relative_rect=pygame.Rect((60, 140), (600, 30)),
            text=f"玩家代号：{self.codename}    （正在监听局域网广播...）",
            manager=gui, object_id="#form_label",
        )
        self._back_btn = ui_button.UIButton(
            relative_rect=pygame.Rect((60, 60), (200, 54)),
            text="← 返回代号输入", manager=gui,
        )
        self._refresh_btn = ui_button.UIButton(
            relative_rect=pygame.Rect((w - 380, 60), (150, 54)),
            text="🔄 刷新", manager=gui,
        )
        self._join_btn = ui_button.UIButton(
            relative_rect=pygame.Rect((w - 210, 60), (150, 54)),
            text="加入房间", manager=gui, object_id="#primary_btn",
        )
        self._join_btn.disable()

        list_w, list_h = 1200, 780
        self._list = ui_list.UISelectionList(
            relative_rect=pygame.Rect((w // 2 - list_w // 2, 200), (list_w, list_h)),
            item_list=["（未发现房间）"],
            manager=gui, allow_multi_select=False,
        )

        self._status_lbl = ui_label.UILabel(
            relative_rect=pygame.Rect((w // 2 - 300, h - 80), (600, 30)),
            text="提示：如果找不到房间，请确保房主和你在同一局域网。",
            manager=gui,
        )

        # 启动监听
        self._listener = DiscoveryListener(s)
        self._listener.start()

    def on_exit(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        for el in [self._title, self._info_lbl, self._back_btn, self._refresh_btn,
                   self._join_btn, self._list, self._status_lbl]:
            if el is not None:
                try: el.kill()
                except Exception: pass  # noqa: E722
        self._title = self._info_lbl = self._back_btn = self._refresh_btn = None
        self._join_btn = self._list = self._status_lbl = None
        self._items.clear()
        self._last_room_keys.clear()

    # -------------------------------------------------
    def update(self, dt: float) -> None:
        # 定期刷新列表（每帧都读 snapshot 即可，无房间不耗性能）
        if self._listener is None or self._list is None or self.ctx is None:
            return
        rooms: List[RoomBroadcastInfo] = self._listener.snapshot()
        # 以 (ip, port) 为 key 构建
        new_items = sorted(rooms, key=lambda r: (r.sender_ip, r.tcp_port))
        # 仅当房间集合（增/删）变化时才重建列表，避免每帧重建清空选中态
        new_keys = [(r.sender_ip, r.tcp_port) for r in new_items]
        if new_keys != self._last_room_keys:
            new_texts = [self._room_to_text(i + 1, r) for i, r in enumerate(new_items)] or ["（未发现房间）"]
            try:
                self._list.set_item_list(new_texts)
            except Exception:  # noqa: BLE001
                # pygame_gui 某些版本下 API 略不同，忽略
                pass
            self._last_room_keys = new_keys
        self._items = new_items
        # 加入按钮：有选中且有房间才可用
        if self._join_btn is not None:
            sel = self._list.get_single_selection()
            if new_items and sel is not None:
                self._join_btn.enable()
            else:
                self._join_btn.disable()

    def handle_event(self, event: pygame.event.Event) -> None:
        assert self.ctx is not None and self.ctx.scene_manager is not None
        sm = self.ctx.scene_manager

        if event.type == pygame_gui.UI_BUTTON_PRESSED:
            if event.ui_element is self._back_btn:
                from .codename_scene import CodenameScene
                sm.switch(CodenameScene)
                return
            if event.ui_element is self._refresh_btn:
                if self._listener:
                    self._listener.clear()
                return
            if event.ui_element is self._join_btn:
                room = self._selected_room()
                if room is None:
                    return
                # 架构阶段：不校验密码直接进入 RoomScene（房主 client 连接逻辑后续）
                from .room_scene import RoomScene
                sm.switch(
                    RoomScene,
                    codename=self.codename,
                    as_host=False,
                    host_ip=room.sender_ip,
                    host_port=room.tcp_port,
                    has_password=room.has_password,
                )
                return

        if event.type == pygame_gui.UI_SELECTION_LIST_NEW_SELECTION:
            if self._join_btn is not None:
                self._join_btn.enable()

    # -------------------------------------------------
    def _selected_room(self) -> Optional[RoomBroadcastInfo]:
        if self._list is None or not self._items:
            return None
        sel = self._list.get_single_selection()
        if sel is None:
            return None
        # 兼容不同 pygame_gui 版本：sel 可能是 str 或 SelectionListTextLine 对象
        sel_text = str(getattr(sel, "text", sel))
        # 从文本开头 #NN 提取序号（房间文本形如 "#01 [..] 房主=..."）
        m = re.match(r"#(\d+)", sel_text)
        if not m:
            return None
        idx = int(m.group(1)) - 1
        if 0 <= idx < len(self._items):
            return self._items[idx]
        return None

    @staticmethod
    def _room_to_text(idx: int, r: RoomBroadcastInfo) -> str:
        pwd = "🔒" if r.has_password else "　"
        sizes = ",".join(r.maze_sizes) if r.maze_sizes else "-"
        return (
            f"#{idx:02d} [{pwd}] 房主={r.host_codename:<10} "
            f"人数={r.current_players}/{r.total_slots}  地图={sizes}  "
            f"地址={r.sender_ip}:{r.tcp_port}"
        )
