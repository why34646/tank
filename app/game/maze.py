"""
随机迷宫生成
===============

俯视视角，4 种大小档位（立项文档）：
- small  20x14  cells
- medium 25x18  cells
- large  30x22  cells
- huge   35x26  cells

生成算法：
    基于随机 Prim / DFS 的迷宫生成，再做少量"打通"操作避免房间过碎。
    输出：墙体矩形列表（pygame.Rect）、出生点列表（中心坐标）、战斗区域矩形。

架构阶段：提供最小可运行骨架——按网格生成规则外墙 + 少量内墙占位，
          真正的随机算法在战斗阶段填充。
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import List, Tuple

import pygame

from ..config.settings import Settings
from ..core.constants import MazeSize
from ..utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class Maze:
    """迷宫数据。"""
    size_key: str                          # small/medium/large/huge
    cols: int
    rows: int
    cell_size: int
    walls: List[pygame.Rect]               # 所有墙体的世界坐标矩形
    spawn_points: List[Tuple[float, float]]  # 出生点中心坐标（世界坐标）
    playfield_rect: pygame.Rect            # 战斗区域世界坐标矩形（居中于窗口）

    @property
    def world_width(self) -> int:
        return self.cols * self.cell_size

    @property
    def world_height(self) -> int:
        return self.rows * self.cell_size


def generate_maze(
    settings: Settings,
    size_key: str | MazeSize,
    seed: int | None = None,
    window_w: int | None = None,
    window_h: int | None = None,
) -> Maze:
    """
    生成迷宫。

    Args:
        settings: 全局配置（读 MAZE_PRESETS、WALL_COLOR 等）
        size_key: 迷宫大小档位
        seed: 随机种子（None 表示完全随机）
        window_w: 窗口宽，默认 settings.WINDOW_WIDTH
        window_h: 窗口高，默认 settings.WINDOW_HEIGHT

    Returns:
        Maze 对象
    """
    if isinstance(size_key, MazeSize):
        size_key = size_key.value
    preset = settings.MAZE_PRESETS[size_key]
    cols, rows, cell_size = preset
    window_w = window_w or settings.WINDOW_WIDTH
    window_h = window_h or settings.WINDOW_HEIGHT

    rng = random.Random(seed)

    # 战斗区域在窗口中居中
    world_w = cols * cell_size
    world_h = rows * cell_size
    offset_x = (window_w - world_w) // 2
    offset_y = (window_h - world_h) // 2
    playfield_rect = pygame.Rect(offset_x, offset_y, world_w, world_h)

    # =====================================================
    # 占位算法：
    #   1. 外墙：四周一圈
    #   2. 内墙：在每格边界按概率生成一段墙（保证连通性占位）
    #   真正的迷宫算法后续迭代填入
    # =====================================================
    walls: List[pygame.Rect] = []
    WALL_THICK = max(4, cell_size // 12)

    # 外墙：4 条矩形
    walls.append(pygame.Rect(offset_x, offset_y, world_w, WALL_THICK))                       # 上
    walls.append(pygame.Rect(offset_x, offset_y + world_h - WALL_THICK, world_w, WALL_THICK))  # 下
    walls.append(pygame.Rect(offset_x, offset_y, WALL_THICK, world_h))                       # 左
    walls.append(pygame.Rect(offset_x + world_w - WALL_THICK, offset_y, WALL_THICK, world_h))  # 右

    # 内墙占位：随机抽若干行/列生成半段墙，避免画面太空白
    for r in range(1, rows - 1):
        if rng.random() < 0.35:
            start_c = rng.randint(1, cols // 2)
            end_c = rng.randint(cols // 2, cols - 2)
            cy = offset_y + r * cell_size
            walls.append(pygame.Rect(
                offset_x + start_c * cell_size,
                cy - WALL_THICK // 2,
                (end_c - start_c) * cell_size,
                WALL_THICK,
            ))
    for c in range(1, cols - 1):
        if rng.random() < 0.25:
            start_r = rng.randint(1, rows // 2)
            end_r = rng.randint(rows // 2, rows - 2)
            cx = offset_x + c * cell_size
            walls.append(pygame.Rect(
                cx - WALL_THICK // 2,
                offset_y + start_r * cell_size,
                WALL_THICK,
                (end_r - start_r) * cell_size,
            ))

    # 出生点：四象限 + 左右中
    # 实际分配策略后续再做，这里先给 6 个位置（够 6 人）
    spawn_positions = [
        (offset_x + cell_size * 2.5,              offset_y + cell_size * 2.5),
        (offset_x + world_w - cell_size * 2.5,    offset_y + cell_size * 2.5),
        (offset_x + cell_size * 2.5,              offset_y + world_h - cell_size * 2.5),
        (offset_x + world_w - cell_size * 2.5,    offset_y + world_h - cell_size * 2.5),
        (offset_x + world_w / 2,                  offset_y + cell_size * 2.5),
        (offset_x + world_w / 2,                  offset_y + world_h - cell_size * 2.5),
    ]

    maze = Maze(
        size_key=size_key,
        cols=cols,
        rows=rows,
        cell_size=cell_size,
        walls=walls,
        spawn_points=spawn_positions,
        playfield_rect=playfield_rect,
    )
    logger.info(f"迷宫生成完毕: {size_key} {cols}x{rows}, 墙体={len(walls)}, 出生点={len(spawn_positions)}")
    return maze


def draw_maze(screen: pygame.Surface, maze: Maze, wall_color, floor_color) -> None:
    """
    绘制迷宫（地面 + 所有墙体）。
    """
    # 地面
    pygame.draw.rect(screen, floor_color, maze.playfield_rect)
    # 墙体
    for w in maze.walls:
        pygame.draw.rect(screen, wall_color, w)
