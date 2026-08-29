"""
UDP 广播房间发现
==================

两端角色：
- **房主（服务端）**：周期性 UDP 广播自己的房间信息（端口、人数、密码有无...）
- **客户端**：在指定端口监听 UDP 广播，收集并去重当前局域网房间列表

架构阶段：
- DiscoveryBroadcaster：房主侧广播器（start/stop，后台线程）
- DiscoveryListener：客户端侧监听器（start/stop，后台线程 + 房间列表快照）
- 广播地址：<broadcast>:NETWORK_DISCOVERY_PORT
"""

from __future__ import annotations

import json
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..config.settings import Settings
from ..utils.logger import get_logger

logger = get_logger(__name__)


# ============================================================
# 房间广播数据
# ============================================================
@dataclass
class RoomBroadcastInfo:
    room_name: str
    host_codename: str
    tcp_port: int
    total_slots: int
    current_players: int
    has_password: bool
    maze_sizes: List[str] = field(default_factory=list)
    # 额外辅助字段（收到时填充，不会被广播）
    sender_ip: str = ""
    last_seen_ts: float = 0.0

    def to_json(self) -> Dict:
        return {
            "room_name": self.room_name,
            "host_codename": self.host_codename,
            "tcp_port": self.tcp_port,
            "total_slots": self.total_slots,
            "current_players": self.current_players,
            "has_password": self.has_password,
            "maze_sizes": list(self.maze_sizes),
        }

    @classmethod
    def from_json(cls, d: Dict, sender_ip: str) -> "RoomBroadcastInfo":
        return cls(
            room_name=str(d.get("room_name", "")),
            host_codename=str(d.get("host_codename", "")),
            tcp_port=int(d.get("tcp_port", 0)),
            total_slots=int(d.get("total_slots", 0)),
            current_players=int(d.get("current_players", 0)),
            has_password=bool(d.get("has_password", False)),
            maze_sizes=list(d.get("maze_sizes", [])),
            sender_ip=sender_ip,
            last_seen_ts=time.time(),
        )


# ============================================================
# 房主：广播器
# ============================================================
class DiscoveryBroadcaster:
    """
    房主侧：按 settings.DISCOVERY_BROADCAST_INTERVAL_MS 周期
    向 <broadcast>:NETWORK_DISCOVERY_PORT 发送 UDP JSON。
    """

    def __init__(self, settings: Settings, info_provider) -> None:
        """
        Args:
            settings: Settings
            info_provider: 可调用，返回 RoomBroadcastInfo 或 None（None 时本周期不发送）
        """
        self._settings = settings
        self._info_provider = info_provider
        self._port = settings.NETWORK_DISCOVERY_PORT
        self._interval = settings.DISCOVERY_BROADCAST_INTERVAL_MS / 1000.0

        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    # -------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._thread = threading.Thread(target=self._run, name="DiscoveryBroadcast", daemon=True)
        self._thread.start()
        logger.info(f"UDP 房间广播已启动，端口={self._port}")

    def stop(self) -> None:
        self._stop_event.set()
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        logger.info("UDP 房间广播已停止")

    # -------------------------------------------------
    def _run(self) -> None:
        assert self._sock is not None
        while not self._stop_event.is_set():
            try:
                info = self._info_provider()
                if info is not None:
                    payload = json.dumps(info.to_json(), ensure_ascii=False).encode("utf-8")
                    self._sock.sendto(payload, ("<broadcast>", self._port))
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"UDP 广播异常: {exc}")
            # 分段 sleep，响应 stop 更快
            slept = 0.0
            while slept < self._interval and not self._stop_event.is_set():
                step = min(0.1, self._interval - slept)
                time.sleep(step)
                slept += step


# ============================================================
# 客户端：监听器
# ============================================================
class DiscoveryListener:
    """
    客户端侧：监听 UDP 广播，维护房间列表（按 (ip, port) 去重 + 超时淘汰）。
    """

    # 房间信息超时：3 * broadcast interval 没收到就剔除
    ROOM_TIMEOUT_SEC: float = 8.0

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._port = settings.NETWORK_DISCOVERY_PORT

        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._rooms: Dict[tuple, RoomBroadcastInfo] = {}  # key=(sender_ip, tcp_port)

    # -------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # 允许多个程序绑定同端口（Windows SO_REUSEADDR 语义略不同，此处尽量）
        try:
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        except OSError:
            pass
        try:
            self._sock.bind(("", self._port))
        except OSError as exc:
            logger.warning(f"UDP 发现端口 {self._port} 绑定失败: {exc}")
            try:
                self._sock.close()
            except OSError:
                pass
            return
        self._sock.settimeout(1.0)
        self._thread = threading.Thread(target=self._run, name="DiscoveryListen", daemon=True)
        self._thread.start()
        logger.info(f"UDP 房间发现监听已启动，端口={self._port}")

    def stop(self) -> None:
        self._stop_event.set()
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        logger.info("UDP 房间发现监听已停止")

    # -------------------------------------------------
    def snapshot(self) -> List[RoomBroadcastInfo]:
        """返回当前房间列表快照（同时移除超时房间）。"""
        now = time.time()
        with self._lock:
            expired_keys = [k for k, v in self._rooms.items()
                            if now - v.last_seen_ts > self.ROOM_TIMEOUT_SEC]
            for k in expired_keys:
                self._rooms.pop(k, None)
            return list(self._rooms.values())

    def clear(self) -> None:
        with self._lock:
            self._rooms.clear()

    # -------------------------------------------------
    def _run(self) -> None:
        assert self._sock is not None
        buf_size = 4096
        while not self._stop_event.is_set():
            try:
                data, addr = self._sock.recvfrom(buf_size)
            except socket.timeout:
                continue
            except OSError:
                if self._stop_event.is_set():
                    break
                continue
            try:
                sender_ip = addr[0]
                d = json.loads(data.decode("utf-8"))
                info = RoomBroadcastInfo.from_json(d, sender_ip)
                if info.tcp_port <= 0:
                    continue
                key = (sender_ip, info.tcp_port)
                with self._lock:
                    self._rooms[key] = info
            except Exception as exc:  # noqa: BLE001
                logger.info(f"UDP 广播解析失败: {exc}")
