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
    """对战模式：各自为战 / 分两队 / 分三队

    UI 直接用枚举 value 做下拉选项文字。
    """
    FREE_FOR_ALL = "各自为战"
    TEAMS_2 = "分两队"
    TEAMS_3 = "分三队"


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
    PROJECTILE_BOUNCE = auto()          # 炮弹撞墙反弹（网络同步用）
    PROJECTILE_HIT_TANK = auto()        # 炮弹击中坦克（网络同步用）
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
    ALL = "all"   # 全部地图：每次生成时从四种大小随机挑一个


# ============================================================
# 地图生成模式
# ============================================================
class MapGenMode(Enum):
    """地图生成算法模式

    - CLASSIC ("原版")：隔间砌墙式 — 从空场地开始正向加墙 + U 形开口房间模板，允许多环路，空地被切成许多独立小间
    - CARVE   ("新版")：破壁凿洞式 — 先全内墙封住 → DFS 破壁凿出完美迷宫 → 再随机砸 30% 墙增阔（现有逻辑）
    """
    CLASSIC = "classic"
    CARVE = "carve"


# ============================================================
# 房间状态
# ============================================================
class RoomState(Enum):
    """房间状态机"""
    WAITING = "waiting"       # 等待加入
    PREPARING = "preparing"   # 等待玩家准备
    PLAYING = "playing"       # 对局中
