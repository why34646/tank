"""
TCP 消息封装
===============

解决 TCP 粘包的核心工具：

    消息格式（字节流）：
        [4 字节大端无符号整数 = N] [UTF-8 编码的 JSON 字节 = N 字节]

接口：
    send_message(sock, msg_dict)  -> None
    recv_message(sock)            -> dict | None（对端正常断开返回 None）

异常：
    MessageFormatError：长度头非法 / JSON 解析失败
    socket.timeout / ConnectionError：按网络异常抛出

**项目约定：所有 TCP 通信统一走本模块，禁止直接 sock.send / sock.recv。**
"""

from __future__ import annotations

import json
import socket
import struct
from typing import Optional

from ..config.settings import Settings
from ..core.exceptions import NetworkError

HEADER_FMT = ">I"       # 4 字节大端无符号整数
HEADER_SIZE = struct.calcsize(HEADER_FMT)

# 单条消息大小上限（16MB），防止恶意/损坏消息撑爆内存
MAX_MESSAGE_BYTES = 16 * 1024 * 1024


class MessageFormatError(NetworkError):
    """TCP 消息格式错误（长度非法、JSON 解析失败等）。"""


def send_message(sock: socket.socket, payload: dict) -> None:
    """
    发送一条 JSON 消息（完整写入，含长度头）。

    Raises:
        MessageFormatError: 序列化失败
        NetworkError: 发送失败
    """
    try:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise MessageFormatError("消息 JSON 序列化失败", cause=exc)

    if len(body) > MAX_MESSAGE_BYTES:
        raise MessageFormatError(f"消息体过大 {len(body)} > {MAX_MESSAGE_BYTES}")

    header = struct.pack(HEADER_FMT, len(body))
    data = header + body

    try:
        # 完整发送
        sock.sendall(data)
    except OSError as exc:
        raise NetworkError("send_message 发送失败", cause=exc)


def recv_message(sock: socket.socket) -> Optional[dict]:
    """
    接收一条 JSON 消息。

    Returns:
        dict 消息，或 None（对端正常关闭连接，未读完长度头时）。

    Raises:
        MessageFormatError: 长度头非法、消息体过大、JSON 解析失败
        NetworkError: 对端中途断连（recv 返回 0 且未读完）
    """
    # 1) 读长度头
    header_buf = _recv_exact(sock, HEADER_SIZE)
    if header_buf is None:
        return None

    (body_len,) = struct.unpack(HEADER_FMT, header_buf)
    if body_len == 0:
        raise MessageFormatError("消息体长度为 0")
    if body_len > MAX_MESSAGE_BYTES:
        raise MessageFormatError(f"消息体长度超限 {body_len} > {MAX_MESSAGE_BYTES}")

    # 2) 读消息体
    body_buf = _recv_exact(sock, body_len)
    if body_buf is None:
        raise NetworkError("对端在消息体中途断开")

    try:
        return json.loads(body_buf.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MessageFormatError("消息体 JSON 解析失败", cause=exc)


# ============================================================
# 内部：精确读取 n 字节
# ============================================================
def _recv_exact(sock: socket.socket, n: int) -> Optional[bytes]:
    """
    从 sock 精确读取 n 字节。
    - 读到 0 字节（正常关闭）且尚未读取任何数据时返回 None
    - 中途关闭抛出 NetworkError
    """
    buf = bytearray()
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except OSError as exc:
            raise NetworkError("_recv_exact recv 失败", cause=exc)
        if not chunk:
            if len(buf) == 0:
                return None
            raise NetworkError(f"对端关闭连接：需要 {n} 字节，仅收到 {len(buf)}")
        buf.extend(chunk)
    return bytes(buf)
