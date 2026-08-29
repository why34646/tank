"""
公共常量枚举
==============

统一所有枚举、状态码、消息类型常量，避免魔法字符串。
"""

from __future__ import annotations

from enum import Enum, auto


# ============================================================
# 对战模式
# ============================================================
class GameMode(Enum):
    """对战模式：各自为战 / 2v2 / 3v3"""
    FREE_FOR_ALL = "free_for_all"   # 各自为战
    TEAM_2V2 = "2v2"                 # 2v2
    TEAM_3V3 = "3v3"                 # 3v3


# ============================================================
# AI 难度
# ============================================================
class AIDifficulty(Enum):
    """AI 难度分级"""
    EASY = "easy"           # 简单
    NORMAL = "normal"       # 普通
    MASTER = "master"       # 大师
    NIGHTMARE = "nightmare" # 噩梦


# ============================================================
# 键鼠操作模式
# ============================================================
class MouseKeyMode(Enum):
    """玩家坦克操作模式"""
    WASDQ = "wasdq"                     # W/A/S/D 移动，Q 射击
    ARROWS_SPACE = "arrows_space"       # 方向键移动，空格射击
    MOUSE = "mouse"                     # 鼠标：左键射击，WASD/方向键移动


# ============================================================
# 事件总线 - 事件类型
# ============================================================
class EventType(Enum):
    """全局事件总线的事件类型"""
    # 场景切换
    SCENE_SWITCH = auto()
    SCENE_PUSH = auto()
    SCENE_POP = auto()

    # 游戏流程
    GAME_START = auto()
    GAME_END = auto()
    PAUSE_TOGGLE = auto()

    # 战斗事件
    TANK_DESTROYED = auto()
    PROJECTILE_FIRED = auto()
    AMMO_RELOADED = auto()

    # 网络事件
    NET_CONNECTED = auto()
    NET_DISCONNECTED = auto()
    NET_MESSAGE_RECEIVED = auto()
    ROOM_CREATED = auto()
    ROOM_JOINED = auto()
    PLAYER_JOINED = auto()
    PLAYER_LEFT = auto()
    PLAYER_READY_CHANGED = auto()
    CHAT_MESSAGE = auto()

    # 战绩
    RECORD_SAVED = auto()


# ============================================================
# 迷宫大小档位
# ============================================================
class MazeSize(Enum):
    """迷宫大小档位"""
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    HUGE = "huge"


# ============================================================
# 房间状态
# ============================================================
class RoomState(Enum):
    """房间状态机"""
    WAITING = "waiting"       # 等待加入
    PREPARING = "preparing"   # 等待玩家准备
    PLAYING = "playing"       # 对局中
