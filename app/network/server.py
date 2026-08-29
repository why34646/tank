"""
TCP 服务端（房主侧）
======================

架构阶段提供骨架：
- 监听端口，accept 客户端连接
- 每个连接一条后台线程循环 recv_message
- 向所有连接广播消息的方法
- 房主断连/玩家离开时的回调钩子（具体逻辑由联机场景填充）

**注意**：消息收发统一走 message 模块（长度头防粘包）。
"""

from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from ..config.settings import Settings
from ..core.exceptions import NetworkError
from ..utils.logger import get_logger
from .message import send_message, recv_message, MessageFormatError
from .protocol import MessageProtocol, MessageType

logger = get_logger(__name__)


# ============================================================
# 数据类
# ============================================================
@dataclass
class ClientPeer:
    """一个已连接客户端。"""
    player_id: int
    sock: socket.socket
    addr: tuple                          # (ip, port)
    codename: str = ""
    ready: bool = False
    last_recv_ts: float = 0.0


# ============================================================
# 服务端
# ============================================================
class GameServer:
    """
    房主服务端。线程模型：
        - 1 条 accept 线程（循环 accept）
        - N 条 recv 线程（每个客户端一条，循环 recv_message）
    """

    def __init__(
        self,
        settings: Settings,
        on_message: Callable[[ClientPeer, dict], None],
        on_client_connect: Callable[[ClientPeer], None] | None = None,
        on_client_disconnect: Callable[[ClientPeer, Optional[Exception]], None] | None = None,
        port: int | None = None,
    ) -> None:
        self._s = settings
        self._port = port or settings.NETWORK_DEFAULT_PORT
        self._on_message = on_message
        self._on_connect = on_client_connect
        self._on_disconnect = on_client_disconnect

        self._listen_sock: Optional[socket.socket] = None
        self._accept_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        self._lock = threading.RLock()
        self._clients: Dict[int, ClientPeer] = {}    # player_id -> peer
        self._next_player_id = 1

    # -------------------------------------------------
    # 属性
    # -------------------------------------------------
    @property
    def port(self) -> int:
        return self._port

    @property
    def clients_count(self) -> int:
        with self._lock:
            return len(self._clients)

    def get_clients_snapshot(self) -> List[ClientPeer]:
        with self._lock:
            return list(self._clients.values())

    # -------------------------------------------------
    # 生命周期
    # -------------------------------------------------
    def start(self) -> None:
        if self._accept_thread and self._accept_thread.is_alive():
            return
        self._stop_event.clear()

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("0.0.0.0", self._port))
        except OSError as exc:
            raise NetworkError(f"TCP 服务端 bind 失败 port={self._port}", cause=exc)
        s.listen(8)
        s.settimeout(1.0)
        self._listen_sock = s

        self._accept_thread = threading.Thread(target=self._accept_loop, name="GameServerAccept", daemon=True)
        self._accept_thread.start()
        logger.info(f"TCP 服务端已启动 port={self._port}")

    def shutdown(self) -> None:
        self._stop_event.set()
        # 关闭所有客户端
        with self._lock:
            for peer in list(self._clients.values()):
                try:
                    peer.sock.close()
                except OSError:
                    pass
            self._clients.clear()
        if self._listen_sock:
            try:
                self._listen_sock.close()
            except OSError:
                pass
            self._listen_sock = None
        if self._accept_thread:
            self._accept_thread.join(timeout=2.0)
            self._accept_thread = None
        logger.info("TCP 服务端已关闭")

    # -------------------------------------------------
    # 广播 / 单播
    # -------------------------------------------------
    def broadcast(self, type_: MessageType, data: dict, except_player_ids: List[int] | None = None) -> None:
        payload = MessageProtocol.wrap(type_, data)
        exclude = set(except_player_ids or [])
        with self._lock:
            targets = [p for p in self._clients.values() if p.player_id not in exclude]
        for peer in targets:
            try:
                send_message(peer.sock, payload)
            except Exception as exc:  # noqa: BLE001
                logger.info(f"broadcast 失败 player={peer.player_id}: {exc}")
                self._handle_disconnect(peer, exc)

    def send_to(self, player_id: int, type_: MessageType, data: dict) -> bool:
        payload = MessageProtocol.wrap(type_, data)
        with self._lock:
            peer = self._clients.get(player_id)
        if peer is None:
            return False
        try:
            send_message(peer.sock, payload)
            return True
        except Exception as exc:  # noqa: BLE001
            self._handle_disconnect(peer, exc)
            return False

    # -------------------------------------------------
    # clients 管理（联机场景需要）
    # -------------------------------------------------
    def register_client(self, sock: socket.socket, addr: tuple, codename: str) -> ClientPeer:
        with self._lock:
            pid = self._next_player_id
            self._next_player_id += 1
            peer = ClientPeer(
                player_id=pid,
                sock=sock,
                addr=addr,
                codename=codename,
                last_recv_ts=time.time(),
            )
            self._clients[pid] = peer
            return peer

    def remove_client(self, player_id: int) -> Optional[ClientPeer]:
        with self._lock:
            peer = self._clients.pop(player_id, None)
        if peer is not None:
            try:
                peer.sock.close()
            except OSError:
                pass
        return peer

    def get_client(self, player_id: int) -> Optional[ClientPeer]:
        with self._lock:
            return self._clients.get(player_id)

    # -------------------------------------------------
    # 内部
    # -------------------------------------------------
    def _accept_loop(self) -> None:
        assert self._listen_sock is not None
        while not self._stop_event.is_set():
            try:
                csock, addr = self._listen_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                if self._stop_event.is_set():
                    break
                continue
            try:
                csock.settimeout(self._s.SOCKET_TIMEOUT_SEC)
            except OSError:
                pass
            # 先回调外部：外部应调用 register_client 确认后再启动 recv 线程
            # 此处我们先占位：生成一个未注册的 peer，外部 on_connect 会处理（若外部未处理，断开）
            t = threading.Thread(
                target=self._recv_loop,
                args=(csock, addr),
                name=f"ServerRecv-{addr}",
                daemon=True,
            )
            t.start()

    def _recv_loop(self, sock: socket.socket, addr: tuple) -> None:
        """
        客户端 recv 线程。
        约定：客户端必须先发 ROOM_JOIN_REQ，消息里含 codename；外部 on_connect 回调决定是否接纳。
        此处实现最小骨架：收到任何消息都转给 on_message，由联机场景完成实际加入流程。
        """
        # 先未注册
        peer: Optional[ClientPeer] = None
        try:
            while not self._stop_event.is_set():
                msg = recv_message(sock)
                if msg is None:
                    # 正常关闭
                    break
                if peer is None:
                    # 尝试在收到第一条消息后查找（外部可能已 register_client）
                    with self._lock:
                        for p in self._clients.values():
                            if p.sock is sock:
                                peer = p
                                break
                if peer is not None:
                    peer.last_recv_ts = time.time()
                    self._on_message(peer, msg)
                else:
                    # 未注册时收到消息：直接转给 on_message（由其决定是否注册/拒绝）
                    dummy = ClientPeer(player_id=0, sock=sock, addr=addr)
                    self._on_message(dummy, msg)
        except (MessageFormatError, NetworkError, OSError) as exc:
            if peer:
                logger.info(f"客户端 player={peer.player_id} 异常断开: {exc}")
            self._handle_disconnect(peer, exc)
            return
        # 正常断连
        if peer is not None:
            self._handle_disconnect(peer, None)
        else:
            try:
                sock.close()
            except OSError:
                pass

    def _handle_disconnect(self, peer: Optional[ClientPeer], exc: Optional[Exception]) -> None:
        if peer is None:
            return
        removed = self.remove_client(peer.player_id)
        if removed is not None and self._on_disconnect:
            try:
                self._on_disconnect(peer, exc)
            except Exception:  # noqa: BLE001
                logger.exception("on_client_disconnect 回调异常")
