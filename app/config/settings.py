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

    # -------- 垂直三栏布局（总高 70 + 900 + 230 = 1200）--------
    # 顶部栏：标题、状态信息、分数总览等
    TOP_BAR_HEIGHT: int = 70
    # 战斗区域：地图、坦克、炮弹等所有战斗渲染都在此区内
    BATTLE_AREA_WIDTH: int = 1700
    BATTLE_AREA_HEIGHT: int = 900
    BATTLE_AREA_TOP: int = 70   # 战斗区在窗口中的 y 起点（= TOP_BAR_HEIGHT）
    # 底部栏：HUD、弹药、击杀、聊天等
    BOTTOM_BAR_HEIGHT: int = 230
    BOTTOM_BAR_TOP: int = 970  # = TOP_BAR_HEIGHT + BATTLE_AREA_HEIGHT
    # 底部栏 —— 坦克数据卡片：每个坦克一个 280x230 的方块，从左到右排列
    # 最多 6 辆：6 × 280 = 1680，距右边 20px 留白
    BOTTOM_PANEL_TILE_W: int = 280
    BOTTOM_PANEL_TILE_H: int = 230
    BOTTOM_PANEL_IMAGE_W: int = 200   # 卡片左侧头像预留区（暂空，后续放坦克图片）
    BOTTOM_PANEL_STATS_W: int = 80    # 卡片右侧统计区
    # 底部栏颜色
    BOTTOM_BAR_BG_COLOR: Tuple[int, int, int] = (189, 189, 189)
    BOTTOM_PANEL_TILE_BG: Tuple[int, int, int] = (189, 189, 189)
    BOTTOM_PANEL_TILE_BORDER: Tuple[int, int, int] = (189, 189, 189)
    BOTTOM_PANEL_STATS_BORDER: Tuple[int, int, int] = (189, 189, 189)

    # -------- 路径（运行时动态生成，不要在 dataclass 默认值里调用函数）--------
    PROJECT_ROOT: Path = field(default_factory=_get_project_root)
    APPDATA_DIR: Path = field(default_factory=_get_appdata_dir)

    # -------- 战斗数值参数（来自立项文档十一、十二：已确认）--------
    # 坦克移动速度 (px/s)
    TANK_SPEED: float = 200.0
    # 炮弹速度 (px/s) — 以此为 small(cell_size=212) 的基准值，其余地图按 cell_size 等比缩放
    PROJECTILE_SPEED: float = 460.0
    # 初始弹药数
    INITIAL_AMMO: int = 5
    # 弹药补满等待时长 (秒)：弹药不满时开始计时，到时一次性补满
    AMMO_RELOAD_INTERVAL: float = 6.0
    # 炮弹存在时间 (秒)
    PROJECTILE_LIFETIME: float = 10.0
    # 炮弹存在时间上限（秒）：大地图缩放后不超过此值
    PROJECTILE_LIFETIME_MAX: float = 25.0
    # 坦克尺寸（正方形边长，像素）
    TANK_SIZE: int = 36
    # 炮弹尺寸（直径/正方形边长）
    PROJECTILE_SIZE: int = 12
    # 开火冷却间隔（秒）：两次发射之间的最短间隔
    FIRE_COOLDOWN: float = 0.1
    # 坦克转向速度（弧度/秒）
    TANK_TURN_SPEED: float = 3.0
    # 后退速度系数（后退 = 前进 × 此值）
    TANK_REVERSE_FACTOR: float = 2.0 / 3.0
    # 每局开始冻结时长（秒）：0.2s 内所有人/AI 都不能动，等人反应过来
    ROUND_START_FREEZE: float = 0.2
    # 对局结束条件触发后，战斗继续时长（秒）：这段时间战斗正常进行，之后才真正停止进入下一局
    ROUND_POST_END_CONTINUE: float = 3.0

    # -------- 音效 --------
    SFX_VOLUME: float = 0.7

    # -------- 地图 --------
    # 4 种大小档位：(迷宫列数, 迷宫行数)
    # 宽高比都贴近 1700:900 ≈ 1.889:1，保证格子正方形铺满战斗区
    # cell_size 由 maze.generate_maze 动态计算：min(BATTLE_AREA_WIDTH/cols, BATTLE_AREA_HEIGHT/rows)
    MAZE_PRESETS: dict = field(default_factory=lambda: {
        "small":  (8,  4),    # 32格  比例 2.0   cell_size=212
        "medium": (13, 7),    # 91格  比例 1.857 cell_size=128
        "large":  (17, 9),    # 153格 比例 1.889 cell_size=100
        "huge":   (24, 13),   # 312格 比例 1.846 cell_size=69
    })
    # 个人游戏设置页【地图生成】默认勾选：默认仅勾新版=破壁凿洞式，保持与历史行为一致
    # 可选值集合：{"classic", "carve"}；两者都勾=每次开局随机二选一
    DEFAULT_MAP_GEN_MODES: list = field(default_factory=lambda: ["carve"])

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
    BG_COLOR: Tuple[int, int, int] = (23, 23, 23)          # 背景深色
    WALL_COLOR: Tuple[int, int, int] = (33,33,33)         # 墙体
    FLOOR_COLOR: Tuple[int, int, int] = (189,189,189)       # 地面
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

    def sound_dir_path(self) -> str:
        """音效目录路径。"""
        return str(self.PROJECT_ROOT / "app" / "assets" / "sound")

    @staticmethod
    def find_chinese_font_path() -> str | None:
        """
        查找系统可用的中文字体文件路径。

        pygame_gui 的 theme.json 中 font.name 会直接传给 pygame.font.Font(name, size)，
        而 Font() 只接受文件路径或 None，不接受字体名。
        所以必须用 match_font() 找到实际文件路径，写入 theme 的 regular_path。

        按优先级尝试：微软雅黑 > 黑体 > 宋体 > 冬青黑体。
        """
        import pygame  # 延迟导入，避免非 Pygame 环境报错
        candidates = [
            "microsoftyahei",  # 微软雅黑（最美观）
            "simhei",          # 黑体
            "simsun",          # 宋体
            "dengxian",        # 冬青黑体
            "fangsong",        # 仿宋
            "kaiti",           # 楷体
        ]
        for name in candidates:
            path = pygame.font.match_font(name)
            if path:
                return path
        return None

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
