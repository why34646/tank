"""
全局配置管理
==============

集中维护：
- 版本号
- 窗口尺寸
- 路径常量（AppData、日志、主题文件等）
- 战斗参数（坦克速度、炮弹速度、弹药数等）
- 网络默认端口
- 颜色常量

所有数值来自立项文档的确认项，后续可调优。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Tuple


# ============================================================
# 辅助函数
# ============================================================
def _get_appdata_dir() -> Path:
    """获取应用数据目录：%APPDATA%/TankTrouble/，不存在则创建。"""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    else:
        # 非 Windows 兜底（虽然本项目只交付 Windows）
        base = str(Path.home() / ".config")
    path = Path(base) / "TankTrouble"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _get_project_root() -> Path:
    """获取项目根目录（源码运行时为 main.py 所在目录，打包时为 sys._MEIPASS）。"""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))).parent


# ============================================================
# Settings 类
# ============================================================
@dataclass
class Settings:
    """全局配置。使用 dataclass 保证 IDE 友好、类型清晰。"""

    # -------- 基础信息 --------
    VERSION: str = "1.0.0"
    APP_NAME: str = "坦克动荡"
    TARGET_FPS: int = 60

    # -------- 窗口（立项文档确认：固定 1700x1200，不可缩放）--------
    WINDOW_WIDTH: int = 1700
    WINDOW_HEIGHT: int = 1200

    # -------- 路径（运行时动态生成，不要在 dataclass 默认值里调用函数）--------
    PROJECT_ROOT: Path = field(default_factory=_get_project_root)
    APPDATA_DIR: Path = field(default_factory=_get_appdata_dir)

    # -------- 战斗数值参数（来自立项文档十一、十二：已确认）--------
    # 坦克移动速度 (px/s)
    TANK_SPEED: float = 180.0
    # 炮弹速度 (px/s)
    PROJECTILE_SPEED: float = 400.0
    # 初始弹药数
    INITIAL_AMMO: int = 5
    # 弹药补充间隔 (秒)
    AMMO_RELOAD_INTERVAL: float = 2.0
    # 炮弹存在时间 (秒)
    PROJECTILE_LIFETIME: float = 10.0
    # 坦克尺寸（正方形边长，像素）
    TANK_SIZE: int = 36
    # 炮弹尺寸（直径/正方形边长）
    PROJECTILE_SIZE: int = 8

    # -------- 地图 --------
    # 4 种大小档位：(迷宫列数, 迷宫行数, 每格像素大小)
    # 迷宫整体大小 = cols*cell_size x rows*cell_size
    MAZE_PRESETS: dict = field(default_factory=lambda: {
        "small":  (20, 14, 60),     # 1200 x 840
        "medium": (25, 18, 50),     # 1250 x 900
        "large":  (30, 22, 44),     # 1320 x 968
        "huge":   (35, 26, 40),     # 1400 x 1040
    })

    # -------- 网络 --------
    # TCP 监听端口（房主服务端）
    NETWORK_DEFAULT_PORT: int = 7788
    # UDP 广播发现端口
    NETWORK_DISCOVERY_PORT: int = 7789
    # 广播间隔（毫秒）
    DISCOVERY_BROADCAST_INTERVAL_MS: int = 2000
    # TCP 消息长度头字节数（4 字节大端）
    MESSAGE_LENGTH_HEADER_SIZE: int = 4
    # socket 超时（秒）
    SOCKET_TIMEOUT_SEC: float = 5.0

    # -------- 颜色 --------
    BG_COLOR: Tuple[int, int, int] = (20, 22, 30)          # 背景深色
    WALL_COLOR: Tuple[int, int, int] = (90, 98, 112)       # 墙体
    FLOOR_COLOR: Tuple[int, int, int] = (40, 44, 55)       # 地面
    TEXT_COLOR: Tuple[int, int, int] = (230, 232, 240)     # 文本

    # 玩家颜色池（6 个玩家的坦克颜色）
    PLAYER_COLORS: list = field(default_factory=lambda: [
        (220,  60,  60),   # 红
        (60,  140, 220),   # 蓝
        (60,  200,  80),   # 绿
        (230, 180,  40),   # 黄
        (200,  80, 200),   # 紫
        (60,  200, 210),   # 青
    ])

    # -------- 战绩文件 --------
    @property
    def records_file(self) -> Path:
        """战绩 JSON 文件路径。"""
        return self.APPDATA_DIR / "battle_records.json"

    @property
    def logs_dir(self) -> Path:
        """日志目录。"""
        d = self.APPDATA_DIR / "logs"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def theme_file_path(self) -> str:
        """pygame_gui 主题 JSON 路径。"""
        p = self.PROJECT_ROOT / "app" / "assets" / "theme.json"
        return str(p)

    # ============================================================
    # 工厂方法
    # ============================================================
    @classmethod
    def load(cls) -> "Settings":
        """
        加载配置。
        当前版本无需外部配置文件；如需覆盖，可在此读取 .env。
        """
        s = cls()
        # 可选：从环境变量覆盖
        log_level = os.environ.get("LOG_LEVEL")
        _ = log_level  # 保留扩展点
        return s
