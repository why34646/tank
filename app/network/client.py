"""
TCP 客户端（玩家侧）
======================

架构阶段提供骨架：
- 连接指定 (host, port)
- 后台线程循环 recv_message 并回调 on_message
- send() 统一封装长度头

具体业务消息处理（ROOM_JOIN_REQ 等）交给联机场景。
"""

from __future__ import annotations

import socket
import threading
from typing import Callable, Optional

from ..config.settings import Settings
from ..core.exceptions import NetworkError
from ..utils.logger import get_logger
from .message import send_message, recv_message, MessageFormatError
from .protocol import MessageProtocol, MessageType

logger = get_logger(__name__)


class GameClient:
    """TCP 客户端。"""

    def __init__(
        self,
        settings: Settings,
        on_message: Callable[[dict], None],
        on_disconnect: Callable[[Optional[Exception]], None] | None = None,
    ) -> None:
        self._s = settings
        self._on_message = on_message
        self._on_disconnect = on_disconnect

        self._sock: Optional[socket.socket] = None
        self._recv_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._connected = False
        self._host: str = ""
        self._port: int = 0

    # -------------------------------------------------
    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    # -------------------------------------------------
    # 生命周期
    # -------------------------------------------------
    def connect(self, host: str, port: int, timeout_sec: float | None = None) -> None:
        if self._connected:
            raise NetworkError("客户端已连接，先 disconnect")
        timeout = timeout_sec or self._s.SOCKET_TIMEOUT_SEC
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect((host, port))
        except OSError as exc:
            try:
                sock.close()
            except OSError:
                pass
            raise NetworkError(f"连接 {host}:{port} 失败", cause=exc)
        sock.settimeout(self._s.SOCKET_TIMEOUT_SEC)
        self._sock = sock
        self._host = host
        self._port = port
        self._connected = True
        self._stop_event.clear()
        self._recv_thread = threading.Thread(target=self._recv_loop, name="GameClientRecv", daemon=True)
        self._recv_thread.start()
        logger.info(f"TCP 客户端已连接 {host}:{port}")

    def disconnect(self) -> None:
        if not self._connected:
            return
        self._stop_event.set()
        self._connected = False
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        if self._recv_thread:
            self._recv_thread.join(timeout=2.0)
            self._recv_thread = None
        logger.info("TCP 客户端已断开")

    # -------------------------------------------------
    # 发送
    # -------------------------------------------------
    def send(self, type_: MessageType, data: dict) -> None:
        if not self._connected or self._sock is None:
            raise NetworkError("客户端未连接")
        payload = MessageProtocol.wrap(type_, data)
        try:
            send_message(self._sock, payload)
        except Exception as exc:  # noqa: BLE001
            logger.info(f"客户端发送失败: {exc}")
            self._fire_disconnect(exc)
            raise

    # -------------------------------------------------
    # 内部
    # -------------------------------------------------
    def _recv_loop(self) -> None:
        assert self._sock is not None
        try:
            while not self._stop_event.is_set() and self._connected:
                msg = recv_message(self._sock)
                if msg is None:
                    self._fire_disconnect(None)
                    return
                try:
                    self._on_message(msg)
                except Exception:  # noqa: BLE001
                    logger.exception("客户端 on_message 回调异常")
        except (MessageFormatError, NetworkError, OSError) as exc:
            self._fire_disconnect(exc)

    def _fire_disconnect(self, exc: Optional[Exception]) -> None:
        was = self._connected
        self._connected = False
        if was and self._on_disconnect:
            try:
                self._on_disconnect(exc)
            except Exception:  # noqa: BLE001
                logger.exception("on_disconnect 回调异常")
