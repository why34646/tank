"""
统一异常定义
==============

所有业务异常继承自 TankTroubleError，方便主循环统一捕获与降级。
"""

from __future__ import annotations


class TankTroubleError(Exception):
    """游戏异常基类。所有自定义异常都应继承此类。"""

    def __init__(self, message: str = "", cause: Exception | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.cause = cause

    def __str__(self) -> str:
        s = f"[{self.__class__.__name__}] {self.message}"
        if self.cause:
            s += f" (原因: {self.cause})"
        return s


class NetworkError(TankTroubleError):
    """网络通信异常：连接失败、超时、断开、消息解析错误等。"""


class GameStateError(TankTroubleError):
    """游戏状态异常：状态机非法跳转、场景不存在等。"""


class StorageError(TankTroubleError):
    """本地存储异常：JSON 读写失败、文件损坏等。"""
