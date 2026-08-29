"""
联机 - 房间内场景
===================

负责：
- 房主：创建 GameServer（TCP） + DiscoveryBroadcaster（UDP 广播房间）
- 客户端：连接房主
- 成员列表 + 准备按钮（非房主）+ 开始按钮（房主，全部准备后可用）
- 聊天输入框 + 聊天记录
- 房主端：房间设置（人数/AI/地图大小多选/AI难度/键鼠模式多选）——架构阶段只读展示，不做真实设置逻辑

架构阶段：
- UI 完整（面板、列表、按钮、聊天区域）
- 房主场景会实际启动 GameServer 和 DiscoveryBroadcaster（端口默认 7788/7789）
- 点击"开始游戏"和聊天发送等业务动作仅写日志，不推进到联机战斗（后续迭代）
- "返回上一页"按钮负责清理网络资源
"""

from __future__ import annotations

from typing import List, Optional

import pygame
import pygame_gui
import pygame_gui.elements.ui_button as ui_button
import pygame_gui.elements.ui_label as ui_label
import pygame_gui.elements.ui_panel as ui_panel
import pygame_gui.elements.ui_text_box as ui_tb
import pygame_gui.elements.ui_text_entry_line as ui_entry
import pygame_gui.elements.ui_selection_list as ui_list

from ...core.constants import AIDifficulty, GameMode, MouseKeyMode, MazeSize
from ...network.discovery import DiscoveryBroadcaster, RoomBroadcastInfo
from ...network.server import ClientPeer, GameServer
from ...network.client import GameClient
from ...network.protocol import MessageType, MessageProtocol
from ..base_scene import Scene


class RoomScene(Scene):
    """房间内场景（房主 / 客户端）。"""

    def __init__(
        self,
        codename: str,
        as_host: bool,
        host_ip: str = "",
        host_port: int = 0,
        has_password: bool = False,
    ) -> None:
        super().__init__()
        self.codename = codename
        self.as_host = as_host
        self.host_ip = host_ip
        self.host_port = host_port
        self.has_password = has_password

        # 网络对象
        self._server: Optional[GameServer] = None
        self._broadcaster: Optional[DiscoveryBroadcaster] = None
        self._client: Optional[GameClient] = None

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
        self._settings_lbl: Optional[ui_label.UILabel] = None

        # UI 引用列表（销毁用）
        self._ui_refs: list = []

    # -------------------------------------------------
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

        # --- 左：成员面板（设置 + 成员列表 + 准备/开始）---
        left_w = 560
        left_x = 60
        top_y = 160
        left_h = h - 220
        self._panel_left = ui_panel.UIPanel(
            relative_rect=pygame.Rect((left_x, top_y), (left_w, left_h)),
            manager=gui,
        )
        self._ui_refs.append(self._panel_left)

        # 设置（只读展示占位）
        mode_txt = f"对战模式：{GameMode.FREE_FOR_ALL.value}    总人数：4\n"
        diff_txt = f"AI 难度：{AIDifficulty.NORMAL.value}    AI 数量：1\n"
        maze_txt = f"地图大小（多选）：{', '.join(m.value for m in MazeSize)}\n"
        key_txt = f"键鼠模式（多选）：{', '.join(k.value for k in MouseKeyMode)}"
        self._settings_lbl = ui_label.UILabel(
            relative_rect=pygame.Rect((20, 20), (left_w - 40, 140)),
            text="【房间设置（架构阶段只读，后续可在房主面板修改）】\n" + mode_txt + diff_txt + maze_txt + key_txt,
            manager=gui, container=self._panel_left,
        )
        self._ui_refs.append(self._settings_lbl)

        ui_label.UILabel(
            relative_rect=pygame.Rect((20, 170), (left_w - 40, 28)),
            text="房间成员：",
            manager=gui, container=self._panel_left, object_id="#form_label",
        )
        self._members_list = ui_list.UISelectionList(
            relative_rect=pygame.Rect((20, 200), (left_w - 40, left_h - 360)),
            item_list=[f"[房主] {self.codename}  ✓已准备"],
            manager=gui, container=self._panel_left, allow_multi_select=False,
        )
        self._ui_refs.append(self._members_list)

        # 准备 / 开始按钮
        if self.as_host:
            self._start_btn = ui_button.UIButton(
                relative_rect=pygame.Rect((left_x + 20, top_y + left_h - 80), (left_w - 40, 60)),
                text="开始游戏（架构阶段占位）",
                manager=gui, object_id="#primary_btn",
            )
            self._ui_refs.append(self._start_btn)
            self._start_btn.enable()  # 架构阶段暂不做"全部准备"校验
        else:
            self._ready_btn = ui_button.UIButton(
                relative_rect=pygame.Rect((left_x + 20, top_y + left_h - 80), (left_w - 40, 60)),
                text="准备 / 取消准备（架构阶段占位）",
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
            placeholder_text="输入聊天内容，回车发送",
        )
        self._chat_entry.set_text_length_limit(200)
        self._ui_refs.append(self._chat_entry)
        self._chat_send_btn = ui_button.UIButton(
            relative_rect=pygame.Rect((right_w - 170, left_h - 80), (150, 54)),
            text="发送", manager=gui, container=self._panel_right,
        )
        self._ui_refs.append(self._chat_send_btn)

        # --- 启动网络 ---
        if self.as_host:
            self._start_as_host()
        else:
            self._start_as_client()

    def on_exit(self) -> None:
        # 网络清理
        if self._broadcaster is not None:
            self._broadcaster.stop()
            self._broadcaster = None
        if self._server is not None:
            self._server.shutdown()
            self._server = None
        if self._client is not None:
            self._client.disconnect()
            self._client = None
        # UI 清理：panel.kill 会连带销毁其内部控件
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

    # -------------------------------------------------
    # 网络：房主 / 客户端
    # -------------------------------------------------
    def _start_as_host(self) -> None:
        assert self.ctx is not None
        s = self.ctx.settings
        log = self.ctx.logger

        def srv_on_msg(peer: ClientPeer, msg: dict) -> None:
            log.info(f"[Server] 收到消息 from player={peer.player_id}: {msg.get('type')}")
            # 架构阶段：统一回 PONG（如 PING）
            if msg.get("type") == MessageType.PING.value:
                self._server and self._server.send_to(peer.player_id, MessageType.PONG, {})

        def srv_on_disconnect(peer: ClientPeer, exc):
            log.info(f"[Server] player={peer.player_id} 断开: {exc}")

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

        # 广播
        def info_provider() -> Optional[RoomBroadcastInfo]:
            return RoomBroadcastInfo(
                room_name=f"{self.codename}的房间",
                host_codename=self.codename,
                tcp_port=self.host_port,
                total_slots=4,
                current_players=1,
                has_password=self.has_password,
                maze_sizes=[m.value for m in MazeSize],
                last_seen_ts=0,
            )

        self._broadcaster = DiscoveryBroadcaster(s, info_provider)
        self._broadcaster.start()

        if self._info_lbl is not None:
            self._info_lbl.set_text(f"✅ 房主服务端已启动（TCP {self.host_port}，UDP 广播 {s.NETWORK_DISCOVERY_PORT}）")
        log.info(f"房主启动：TCP {self.host_port}, UDP 广播 {s.NETWORK_DISCOVERY_PORT}")

    def _start_as_client(self) -> None:
        assert self.ctx is not None
        log = self.ctx.logger

        def cli_on_msg(msg: dict) -> None:
            log.info(f"[Client] 收到消息: {msg.get('type')}")

        def cli_on_disconnect(exc):
            log.info(f"[Client] 断开: {exc}")

        self._client = GameClient(s=self.ctx.settings, on_message=cli_on_msg, on_disconnect=cli_on_disconnect)
        # 架构阶段：只尝试连接一次，失败不崩溃，仅在 label 提示
        try:
            self._client.connect(self.host_ip, self.host_port or self.ctx.settings.NETWORK_DEFAULT_PORT)
            msg = MessageProtocol.wrap(MessageType.ROOM_JOIN_REQ, {"codename": self.codename, "password": ""})
            # 直接发送占位（server 侧尚未处理 ROOM_JOIN_REQ，架构阶段仅测试 message 模块）
            from ...network.message import send_message
            # 此处用内部 sock 发送仅用于测试；实际应走 client.send()
            # 这里走 client.send()：
            self._client.send(MessageType.ROOM_JOIN_REQ, {"codename": self.codename, "password": ""})
            ok = True
        except Exception as exc:  # noqa: BLE001
            log.warning(f"连接房主失败: {exc}")
            ok = False
        if self._info_lbl is not None:
            if ok:
                self._info_lbl.set_text(f"✅ 已连接房主 {self.host_ip}:{self._client.port}（架构阶段占位）")
            else:
                self._info_lbl.set_text("⚠️ 连接房主失败（架构阶段仍可查看 UI，后续补全联网逻辑）")

    # -------------------------------------------------
    def handle_event(self, event: pygame.event.Event) -> None:
        assert self.ctx is not None and self.ctx.scene_manager is not None
        sm = self.ctx.scene_manager
        log = self.ctx.logger

        if event.type == pygame_gui.UI_BUTTON_PRESSED:
            if event.ui_element is self._back_btn:
                if self.as_host:
                    from .codename_scene import CodenameScene
                    sm.switch(CodenameScene)
                else:
                    from .lobby_scene import LobbyScene
                    sm.switch(LobbyScene, codename=self.codename)
                return

            if event.ui_element is self._chat_send_btn:
                self._send_chat()
                return

            if event.ui_element is self._ready_btn:
                log.info("【占位】准备状态切换")
                return

            if event.ui_element is self._start_btn:
                log.info("【占位】房主开始游戏")
                return

        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_RETURN:
                # 聊天框回车发送
                if self._chat_entry is not None and self._chat_entry.is_focused:
                    self._send_chat()

    # -------------------------------------------------
    def _send_chat(self) -> None:
        text = ""
        if self._chat_entry is not None:
            text = (self._chat_entry.get_text() or "").strip()
        if not text:
            return
        # 架构阶段：仅本地显示
        if self._chat_box is not None:
            html = f"<b>{self.codename}</b>: {html_escape(text)}<br>"
            try:
                self._chat_box.append_html_text(html)
            except Exception:  # noqa: BLE001
                pass
        if self._chat_entry is not None:
            try:
                self._chat_entry.set_text("")
            except Exception:  # noqa: BLE001
                pass


# 简单 HTML 转义（聊天用）
def html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
