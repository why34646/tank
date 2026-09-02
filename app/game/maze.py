"""
随机迷宫生成
===============

俯视视角，4 种大小档位（按比例递进，铺满 1700×900 战斗区）：
- small   9×5   cells  （格子最大，放大视角）
- medium 17×9   cells
- large  24×13  cells
- huge   32×17  cells   （格子最小，缩小视角）

所有档位的迷宫都完整放在：
    战斗区域 = x∈[0, 1700], y∈[70, 970]  （1700 × 900）

生成算法：
    DFS 递归回溯生成迷宫骨架 → 随机打通部分内墙增加开阔性 → 合并连续墙为长矩形。

墙模型：
    墙不是"格子本身"，而是格子之间的"边界线"。
    - h_walls[y][x]：格子 (x,y) 与 (x,y+1) 之间的水平墙
    - v_walls[y][x]：格子 (x,y) 与 (x+1,y) 之间的垂直墙
    DFS 打通的是这些边界墙，保证格子全连通。
    打通额外内墙（ratio≈30%）使地图更开阔，符合坦克动荡风格。

输出：墙体矩形列表（pygame.Rect）、出生点列表、战斗区域矩形。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import List, Tuple

import pygame

from ..config.settings import Settings
from ..core.constants import MapGenMode, MazeSize
from ..utils.logger import get_logger

logger = get_logger(__name__)


# ============================================================
# Maze 数据类
# ============================================================
@dataclass
class Maze:
    """迷宫数据。"""
    size_key: str                          # small/medium/large/huge
    cols: int
    rows: int
    cell_size: int
    walls: List[pygame.Rect]               # 所有墙体的世界坐标矩形
    spawn_points: List[Tuple[float, float]]  # 出生点中心坐标（世界坐标）
    playfield_rect: pygame.Rect             # 战斗区域世界坐标矩形（居中于窗口）
    # 格子网格（True=墙线残留，供 AI 寻路参考；当前未直接使用，预留）
    cell_grid: List[List[bool]] = field(default_factory=list)
    # 生成时使用的随机种子（联机时房主广播给客户端，客户端用同 seed 重建相同迷宫）
    seed: int = 0

    @property
    def world_width(self) -> int:
        return self.cols * self.cell_size

    @property
    def world_height(self) -> int:
        return self.rows * self.cell_size


# ============================================================
# DFS 迷宫生成核心
# ============================================================
def _dfs_maze(cols: int, rows: int, rng: random.Random) -> Tuple[List[List[bool]], List[List[bool]]]:
    """
    DFS 递归回溯生成迷宫。

    返回:
        h_walls: h_walls[y][x] = True 表示格子 (x,y) 与 (x,y+1) 之间有墙
                 维度 rows-1 行 × cols 列
        v_walls: v_walls[y][x] = True 表示格子 (x,y) 与 (x+1,y) 之间有墙
                 维度 rows 行 × cols-1 列

    初始时所有内墙都存在，DFS 逐个打通保证全连通。
    """
    # 初始：所有内墙都存在
    h_walls: List[List[bool]] = [[True] * cols for _ in range(rows - 1)]
    v_walls: List[List[bool]] = [[True] * (cols - 1) for _ in range(rows)]

    visited: List[List[bool]] = [[False] * cols for _ in range(rows)]

    # DFS 栈（迭代版，避免 Python 递归深度限制）
    stack: List[Tuple[int, int]] = [(0, 0)]
    visited[0][0] = True

    # 四方向：上 下 左 右（步长 1 格）
    directions = [(0, -1), (0, 1), (-1, 0), (1, 0)]

    while stack:
        cx, cy = stack[-1]
        dirs = list(directions)
        rng.shuffle(dirs)

        moved = False
        for dx, dy in dirs:
            nx, ny = cx + dx, cy + dy
            if 0 <= nx < cols and 0 <= ny < rows and not visited[ny][nx]:
                # 打通当前格与新格之间的墙
                if dx == 1:       # 向右：打通 v_walls[cy][cx]
                    v_walls[cy][cx] = False
                elif dx == -1:    # 向左：打通 v_walls[cy][cx-1]
                    v_walls[cy][cx - 1] = False
                elif dy == 1:     # 向下：打通 h_walls[cy][cx]
                    h_walls[cy][cx] = False
                elif dy == -1:    # 向上：打通 h_walls[cy-1][cx]
                    h_walls[cy - 1][cx] = False

                visited[ny][nx] = True
                stack.append((nx, ny))
                moved = True
                break

        if not moved:
            stack.pop()

    return h_walls, v_walls


def _open_extra_walls(
    h_walls: List[List[bool]],
    v_walls: List[List[bool]],
    cols: int,
    rows: int,
    rng: random.Random,
    ratio: float = 0.30,
) -> None:
    """
    随机打通部分内墙，增加地图开阔性。
    ratio=0.30 表示打通 30% 的残余内墙。
    外墙不参与（外墙由后续单独添加）。
    """
    for y in range(rows - 1):
        for x in range(cols):
            if h_walls[y][x] and rng.random() < ratio:
                h_walls[y][x] = False
    for y in range(rows):
        for x in range(cols - 1):
            if v_walls[y][x] and rng.random() < ratio:
                v_walls[y][x] = False


# ============================================================
# 原版隔间砌墙式（经典）生成器
# ============================================================
def _classic_walls(
    cols: int,
    rows: int,
    rng: random.Random,
    *,
    base_density: float = 0.58,
    room_ratio: float = 0.20,
) -> Tuple[List[List[bool]], List[List[bool]]]:
    """
    原版《坦克动荡》风格 —— 从空场地开始"正向加墙"：

    1) 初始化：所有内墙全部 False（没有任何墙）。
    2) Phase A 随机边界墙：对所有内部水平/垂直边界线按 base_density 独立概率画墙，
       先获得一个密度约 58% 的基础墙骨架（多环路、多短段）。
    3) Phase B U 形开口房间：生成若干房间（房间总数 ≈ 总格子数 * room_ratio）。
       每个房间大小在 2~3 格 × 2~3 格之间随机。对每个候选房间：
       - 画它对应的 4 条边界墙（覆盖整段），形成"盒子"。
       - 在 4 条边界中随机挑 1 条，并在该边界正中间位置留 1 格宽的"门洞"
         （把该格线拆掉 = False），保证房间至少有一个出入口。
    4) Phase C 连通性保证：从 (0,0) 起 BFS 访问，如果存在未连通块，就迭代地在
       "已访问块边界"上随机选一段与未访问块相邻的墙打通，直到全体格子连通。
       此步不追求完美迷宫，只保证出生点/玩家可行走全图，允许大量环路保留。

    返回：(h_walls, v_walls)，维度与 _dfs_maze 完全一致：
        h_walls: (rows-1) 行 × cols 列
        v_walls: rows 行 × (cols-1) 列
    """
    # 1) 空墙
    h_walls: List[List[bool]] = [[False] * cols for _ in range(rows - 1)]
    v_walls: List[List[bool]] = [[False] * (cols - 1) for _ in range(rows)]

    # 2) Phase A：随机加基础密度墙
    for y in range(rows - 1):
        for x in range(cols):
            if rng.random() < base_density:
                h_walls[y][x] = True
    for y in range(rows):
        for x in range(cols - 1):
            if rng.random() < base_density:
                v_walls[y][x] = True

    # 3) Phase B：U 形开口房间
    total_cells = cols * rows
    n_rooms = max(2, int(total_cells * room_ratio))
    for _ in range(n_rooms * 3):  # 多尝试几次，跳过超界/重叠不惩罚
        if n_rooms <= 0:
            break
        w = rng.randint(2, 3)  # 房间宽（格）
        h = rng.randint(2, 3)  # 房间高（格）
        tx = rng.randint(0, cols - w)
        ty = rng.randint(0, rows - h)
        # 4 条边界：顶水平墙线 ty、底水平墙线 ty+h、左垂直墙线 tx、右垂直墙线 tx+w
        # 画满这 4 条上对应段的所有墙
        # 顶 h_walls[ty-1][tx..tx+w-1]
        if ty > 0:
            for xi in range(tx, tx + w):
                h_walls[ty - 1][xi] = True
        # 底 h_walls[ty+h-1][tx..tx+w-1]
        if ty + h - 1 < rows - 1:
            for xi in range(tx, tx + w):
                h_walls[ty + h - 1][xi] = True
        # 左 v_walls[ty..ty+h-1][tx-1]
        if tx > 0:
            for yi in range(ty, ty + h):
                v_walls[yi][tx - 1] = True
        # 右 v_walls[ty..ty+h-1][tx+w-1]
        if tx + w - 1 < cols - 1:
            for yi in range(ty, ty + h):
                v_walls[yi][tx + w - 1] = True

        # 在 4 条边上随机选一条，留 1 格"门洞"（1格宽）
        side = rng.randint(0, 3)
        if side == 0 and ty > 0:
            # 顶边：门洞在 tx..tx+w-1 范围内随机一格水平墙处
            gx2 = tx + rng.randint(0, w - 1)
            h_walls[ty - 1][gx2] = False
        elif side == 1 and ty + h - 1 < rows - 1:
            gx2 = tx + rng.randint(0, w - 1)
            h_walls[ty + h - 1][gx2] = False
        elif side == 2 and tx > 0:
            # 左边：门洞在 ty..ty+h-1 范围内随机一格垂直墙处
            gy2 = ty + rng.randint(0, h - 1)
            v_walls[gy2][tx - 1] = False
        elif side == 3 and tx + w - 1 < cols - 1:
            gy2 = ty + rng.randint(0, h - 1)
            v_walls[gy2][tx + w - 1] = False
        # 若那条边因为贴边不存在（比如 tx==0 选了左边），就不开门（虽然是封死的小盒子，
        # 但 Phase C 连通性保证会把它凿通到主区域，不会出死角）
        n_rooms -= 1

    # 4) Phase C：连通性修复
    _ensure_connected(h_walls, v_walls, cols, rows, rng)

    return h_walls, v_walls


def _ensure_connected(
    h_walls: List[List[bool]],
    v_walls: List[List[bool]],
    cols: int,
    rows: int,
    rng: random.Random,
) -> None:
    """
    全图连通性保证：以 (0,0) 为起点做 BFS，若存在未访问子图，则在该子图与已访问区域
    的交界处随机选一道墙打通；循环直到所有格都被访问。只拆最少的墙。
    """
    # 返回每个 cell 的 visited + 连通分量ID（0 表示未访问）
    comp_id = [[0] * cols for _ in range(rows)]
    visited = [[False] * cols for _ in range(rows)]
    q: List[Tuple[int, int]] = [(0, 0)]
    visited[0][0] = True
    count = 1
    while q:
        cx, cy = q.pop(0)
        # 上
        if cy > 0 and not h_walls[cy - 1][cx] and not visited[cy - 1][cx]:
            visited[cy - 1][cx] = True
            count += 1
            q.append((cx, cy - 1))
        # 下
        if cy < rows - 1 and not h_walls[cy][cx] and not visited[cy + 1][cx]:
            visited[cy + 1][cx] = True
            count += 1
            q.append((cx, cy + 1))
        # 左
        if cx > 0 and not v_walls[cy][cx - 1] and not visited[cy][cx - 1]:
            visited[cy][cx - 1] = True
            count += 1
            q.append((cx - 1, cy))
        # 右
        if cx < cols - 1 and not v_walls[cy][cx] and not visited[cy][cx + 1]:
            visited[cy][cx + 1] = True
            count += 1
            q.append((cx + 1, cy))
    if count == cols * rows:
        return

    # 没全连通：循环找未访问细胞 c，枚举它与 visited 区域的 4 条边界墙候选，随机敲一道
    total = cols * rows
    while count < total:
        # 找一个未访问的 cell
        found = False
        ux = uy = -1
        for y in range(rows):
            for x in range(cols):
                if not visited[y][x]:
                    ux, uy = x, y
                    found = True
                    break
            if found:
                break
        if not found:
            break
        # 找该未访问子图的边界（BFS 扩展未访问区域，收集它与 visited 的相邻墙）
        frontiers: List[Tuple[str, int, int]] = []
        stack = [(ux, uy)]
        comp_visited = set()
        comp_visited.add((ux, uy))
        while stack:
            cx, cy = stack.pop()
            # 上
            if cy > 0:
                if visited[cy - 1][cx]:
                    if h_walls[cy - 1][cx]:
                        frontiers.append(("h", cy - 1, cx))
                elif (cx, cy - 1) not in comp_visited and not h_walls[cy - 1][cx]:
                    comp_visited.add((cx, cy - 1))
                    stack.append((cx, cy - 1))
            # 下
            if cy < rows - 1:
                if visited[cy + 1][cx]:
                    if h_walls[cy][cx]:
                        frontiers.append(("h", cy, cx))
                elif (cx, cy + 1) not in comp_visited and not h_walls[cy][cx]:
                    comp_visited.add((cx, cy + 1))
                    stack.append((cx, cy + 1))
            # 左
            if cx > 0:
                if visited[cy][cx - 1]:
                    if v_walls[cy][cx - 1]:
                        frontiers.append(("v", cy, cx - 1))
                elif (cx - 1, cy) not in comp_visited and not v_walls[cy][cx - 1]:
                    comp_visited.add((cx - 1, cy))
                    stack.append((cx - 1, cy))
            # 右
            if cx < cols - 1:
                if visited[cy][cx + 1]:
                    if v_walls[cy][cx]:
                        frontiers.append(("v", cy, cx))
                elif (cx + 1, cy) not in comp_visited and not v_walls[cy][cx]:
                    comp_visited.add((cx + 1, cy))
                    stack.append((cx + 1, cy))
        if not frontiers:
            # 极罕见：子图完全被已访问区域包围但没有墙（理论不可能有这种不连通）
            # 做保护：把第一个墙强制打掉，避免死循环
            if uy > 0 and h_walls[uy - 1][ux]:
                h_walls[uy - 1][ux] = False
            elif ux > 0 and v_walls[uy][ux - 1]:
                v_walls[uy][ux - 1] = False
            elif uy < rows - 1 and h_walls[uy][ux]:
                h_walls[uy][ux] = False
            elif ux < cols - 1 and v_walls[uy][ux]:
                v_walls[uy][ux] = False
        else:
            # 随机挑一道边界墙打通
            t, wy, wx = rng.choice(frontiers)
            if t == "h":
                h_walls[wy][wx] = False
            else:
                v_walls[wy][wx] = False
        # 重新做 BFS 统计（简单实现即可，总格子数 <= 32*17=544，性能没问题）
        visited = [[False] * cols for _ in range(rows)]
        q = [(0, 0)]
        visited[0][0] = True
        count = 1
        while q:
            cx, cy = q.pop(0)
            if cy > 0 and not h_walls[cy - 1][cx] and not visited[cy - 1][cx]:
                visited[cy - 1][cx] = True; count += 1; q.append((cx, cy - 1))
            if cy < rows - 1 and not h_walls[cy][cx] and not visited[cy + 1][cx]:
                visited[cy + 1][cx] = True; count += 1; q.append((cx, cy + 1))
            if cx > 0 and not v_walls[cy][cx - 1] and not visited[cy][cx - 1]:
                visited[cy][cx - 1] = True; count += 1; q.append((cx - 1, cy))
            if cx < cols - 1 and not v_walls[cy][cx] and not visited[cy][cx + 1]:
                visited[cy][cx + 1] = True; count += 1; q.append((cx + 1, cy))


# ============================================================
# 墙合并：连续墙线 → 长矩形
# ============================================================
def _merge_h_walls(
    h_walls: List[List[bool]],
    v_walls: List[List[bool]],
    cols: int,
    rows: int,
    offset_x: int,
    offset_y: int,
    cell_size: int,
    wall_thick: int,
) -> List[pygame.Rect]:
    """
    水平墙合并：同一行中连续的 h_walls 合并为一个长矩形。
    墙的世界 y 坐标 = offset_y + (y+1)*cell_size - wall_thick//2（在格子 y 和 y+1 的边界中线上）。

    方案 A（拐角无突起）：
      每段水平墙只在"对侧真的有垂直墙/外墙会与它在端点相交"时，才在对应端
      伸出半墙厚的补量，保证 T 字/十字/外墙交接无缝，而 L 形空侧零突起。
      - 左端（格线 start）：若 start==0 贴左外墙，或 v_walls[y][start-1] 存在垂直墙
        → wx 向左移 wall_thick//2
      - 右端（格线 end+1）：若 end==cols-1 贴右外墙，或 v_walls[y][end] 存在垂直墙
        → ww 额外加 (wall_thick - wall_thick//2)
    """
    walls: List[pygame.Rect] = []
    for y in range(rows - 1):
        x = 0
        while x < cols:
            if not h_walls[y][x]:
                x += 1
                continue
            start = x
            while x + 1 < cols and h_walls[y][x + 1]:
                x += 1
            end = x

            ww_basic = (end - start + 1) * cell_size
            wx = offset_x + start * cell_size
            wy = offset_y + (y + 1) * cell_size - wall_thick // 2

            # 左端延伸判定
            left_ext = 0
            if start == 0:
                left_ext = wall_thick // 2                 # 贴左外墙
            elif v_walls[y][start - 1]:
                left_ext = wall_thick // 2                 # 左端格线处有垂直墙T接

            # 右端延伸判定
            right_ext = 0
            if end == cols - 1:
                right_ext = wall_thick - wall_thick // 2   # 贴右外墙
            elif v_walls[y][end]:
                right_ext = wall_thick - wall_thick // 2   # 右端格线处有垂直墙T接

            wx -= left_ext
            ww = ww_basic + left_ext + right_ext
            walls.append(pygame.Rect(wx, wy, ww, wall_thick))
            x += 1
    return walls


def _merge_v_walls(
    v_walls: List[List[bool]],
    h_walls: List[List[bool]],
    cols: int,
    rows: int,
    offset_x: int,
    offset_y: int,
    cell_size: int,
    wall_thick: int,
) -> List[pygame.Rect]:
    """
    垂直墙合并：同一列中连续的 v_walls 合并为一个长矩形。
    墙的世界 x 坐标 = offset_x + (x+1)*cell_size - wall_thick//2。

    方案 A（拐角无突起，对称实现见 _merge_h_walls 注释）：
      - 上端（格线 start）：贴顶外墙或 h_walls[start-1][x] 有水平墙
        → wy 向上移 wall_thick//2
      - 下端（格线 end+1）：贴下外墙或 h_walls[end][x] 有水平墙
        → wh 额外加 (wall_thick - wall_thick//2)
    """
    walls: List[pygame.Rect] = []
    for x in range(cols - 1):
        y = 0
        while y < rows:
            if not v_walls[y][x]:
                y += 1
                continue
            start = y
            while y + 1 < rows and v_walls[y + 1][x]:
                y += 1
            end = y

            wh_basic = (end - start + 1) * cell_size
            wx = offset_x + (x + 1) * cell_size - wall_thick // 2
            wy = offset_y + start * cell_size

            # 上端延伸判定
            up_ext = 0
            if start == 0:
                up_ext = wall_thick // 2                   # 贴上外墙
            elif h_walls[start - 1][x]:
                up_ext = wall_thick // 2                   # 上端格线处有水平墙T接

            # 下端延伸判定
            down_ext = 0
            if end == rows - 1:
                down_ext = wall_thick - wall_thick // 2    # 贴下外墙
            elif h_walls[end][x]:
                down_ext = wall_thick - wall_thick // 2    # 下端格线处有水平墙T接

            wy -= up_ext
            wh = wh_basic + up_ext + down_ext
            walls.append(pygame.Rect(wx, wy, wall_thick, wh))
            y += 1
    return walls


# ============================================================
# 出生点
# ============================================================
def _select_spawn_points(
    cols: int,
    rows: int,
    offset_x: int,
    offset_y: int,
    cell_size: int,
) -> List[Tuple[float, float]]:
    """
    在格子中心选取 6 个分散的出生点。
    位置：四角 + 上中 + 下中，保证相互分散。
    坐标为格子中心世界坐标。
    """
    def cell_center(col: int, row: int) -> Tuple[float, float]:
        return (
            offset_x + (col + 0.5) * cell_size,
            offset_y + (row + 0.5) * cell_size,
        )

    # 四角向内缩 1 格（避免紧贴外墙）
    cl = 1                     # 最左格
    cr = cols - 2              # 最右格
    ct = 1                     # 最上格
    cb = rows - 2              # 最下格
    cm = cols // 2             # 中间列

    return [
        cell_center(cl, ct),    # 左上
        cell_center(cr, ct),    # 右上
        cell_center(cl, cb),    # 左下
        cell_center(cr, cb),    # 右下
        cell_center(cm, ct),    # 上中
        cell_center(cm, cb),    # 下中
    ]


# ============================================================
# 主函数
# ============================================================
def generate_maze(
    settings: Settings,
    size_key: str | MazeSize,
    seed: int | None = None,
    window_w: int | None = None,
    window_h: int | None = None,
    map_gen_modes: list[str] | None = None,
) -> Maze:
    """
    生成迷宫。

    战斗区域固定为：宽 settings.BATTLE_AREA_WIDTH（1700）×
                    高 settings.BATTLE_AREA_HEIGHT（900），
                    左上角在 (0, settings.BATTLE_AREA_TOP) = (0, 70)。

    cell_size 动态计算：取 min(战斗区宽/cols, 战斗区高/rows) 的整数下限，
    保证格子为正方形且不溢出战斗区，迷宫整体再在战斗区内居中留少量边距。

    地图生成模式（map_gen_modes，由 MapGenMode 枚举值构成的列表）：
        - ["classic"]          → 每次都用"原版 隔间砌墙式"
        - ["carve"]            → 每次都用"新版 破壁凿洞式"（历史行为，默认）
        - ["classic","carve"]  → 每次开局随机二选一
        - None / 空列表        → 回退为 settings.DEFAULT_MAP_GEN_MODES（默认 ["carve"]）

    流程（新版 / 原版分别对应不同的内墙生成阶段，其余阶段完全共享）：
        1. 读取档位预设 → cols, rows
        2. 按战斗区域尺寸动态计算 cell_size → offset
        3. 按 map_gen_modes 选出一种模式 → 生成 h_walls / v_walls：
             - CARVE:   DFS 递归回溯（保证全连通） → 随机打通 30% 内墙增阔
             - CLASSIC: 空场地 → 58% 随机加墙 → U 形开口房间 → 连通性修复
        4. 合并连续墙为长矩形（方案 A：条件化延伸端点消除拐角突起）
        5. 添加四周外墙 + 裁剪到世界边界
        6. L 形拐角补丁：填充段中段相交时留下的半墙厚级小缺口
        7. 选取 6 个分散出生点

    Args:
        settings: 全局配置
        size_key: 迷宫大小档位（也支持 MazeSize.ALL="all"，每次从四种大小随机选一个）
        seed: 随机种子（None=完全随机）
        window_w: 保留，未使用（战斗区始终取 settings.BATTLE_AREA_WIDTH）
        window_h: 保留，未使用（战斗区始终取 settings.BATTLE_AREA_HEIGHT）
        map_gen_modes: 可选的模式列表（元素 ∈ {"classic","carve"}），
                       None 时回退 settings.DEFAULT_MAP_GEN_MODES；
                       多选时随机抽一种模式应用到本次生成。

    Returns:
        Maze 对象
    """
    if isinstance(size_key, MazeSize):
        size_key = size_key.value

    # seed：None 时随机生成一个（联机房主端可从 maze.seed 取出广播给客户端）
    if seed is None:
        seed = random.randint(0, 2**31 - 1)
    actual_seed = int(seed)

    rng = random.Random(actual_seed)

    # "全部地图"：从四种大小随机选一个（基于 seed，保证联机房主/客户端一致）
    if size_key == MazeSize.ALL.value:
        _all_size_keys = [MazeSize.SMALL.value, MazeSize.MEDIUM.value,
                          MazeSize.LARGE.value, MazeSize.HUGE.value]
        size_key = rng.choice(_all_size_keys)

    preset = settings.MAZE_PRESETS[size_key]
    cols, rows = preset  # 二元组：(cols, rows)，无固定 cell_size

    # 战斗区域：始终取 settings 常量（1700 × 900，起点 y=70）
    _ = window_w, window_h  # 显式标记未使用（兼容旧调用签名）
    playfield_w = settings.BATTLE_AREA_WIDTH
    playfield_h = settings.BATTLE_AREA_HEIGHT
    playfield_origin_x = 0
    playfield_origin_y = settings.BATTLE_AREA_TOP  # = 70

    # 动态计算正方形 cell_size（向下取整，保证不溢出）
    cell_size = int(min(playfield_w // cols, playfield_h // rows))
    # 实际迷宫世界大小（可能略小于战斗区，做居中补偿）
    world_w = cols * cell_size
    world_h = rows * cell_size
    offset_x = playfield_origin_x + (playfield_w - world_w) // 2
    offset_y = playfield_origin_y + (playfield_h - world_h) // 2
    playfield_rect = pygame.Rect(playfield_origin_x, playfield_origin_y,
                                 playfield_w, playfield_h)

    WALL_THICK = max(4, cell_size // 10)

    # 0) 解析并选定本次使用的地图生成模式
    modes_candidates: list[str] = list(map_gen_modes) if map_gen_modes else []
    if not modes_candidates:
        modes_candidates = list(getattr(settings, "DEFAULT_MAP_GEN_MODES", ["carve"]))
    # 过滤有效值（只保留 classic / carve）；全部无效时默认回退 carve
    valid_set = {MapGenMode.CLASSIC.value, MapGenMode.CARVE.value}
    modes_candidates = [m for m in modes_candidates if m in valid_set]
    if not modes_candidates:
        modes_candidates = [MapGenMode.CARVE.value]
    selected_mode = rng.choice(modes_candidates)

    # 1) 按选中的模式生成内墙骨架（h_walls / v_walls）
    if selected_mode == MapGenMode.CLASSIC.value:
        # 原版：隔间砌墙式 — 空场地 + 随机加墙 + U 形开口房间 + 连通性修复
        h_walls, v_walls = _classic_walls(cols, rows, rng)
    else:
        # 新版：破壁凿洞式 — 先全内墙封住 → DFS 打通完美迷宫 → 再砸 30% 墙增阔
        h_walls, v_walls = _dfs_maze(cols, rows, rng)
        _open_extra_walls(h_walls, v_walls, cols, rows, rng, ratio=0.30)

    # 3) 合并连续墙为长矩形（方案 A：两端只在有对向墙时延伸，消除拐角突起）
    walls: List[pygame.Rect] = []
    walls.extend(_merge_h_walls(h_walls, v_walls, cols, rows, offset_x, offset_y, cell_size, WALL_THICK))
    walls.extend(_merge_v_walls(v_walls, h_walls, cols, rows, offset_x, offset_y, cell_size, WALL_THICK))

    # 4) 外墙：四周一圈（包裹实际世界大小 world_w × world_h）
    walls.append(pygame.Rect(offset_x, offset_y, world_w, WALL_THICK))
    walls.append(pygame.Rect(offset_x, offset_y + world_h - WALL_THICK, world_w, WALL_THICK))
    walls.append(pygame.Rect(offset_x, offset_y, WALL_THICK, world_h))
    walls.append(pygame.Rect(offset_x + world_w - WALL_THICK, offset_y, WALL_THICK, world_h))

    # 4.5) 裁剪所有墙体到世界边界内（合并墙在两端向外延伸 wall_thick 以衔接墙角，
    #      最外层时会略微超出 world_w / world_h，裁剪后仍能贴紧外墙不漏缝）
    world_rect = pygame.Rect(offset_x, offset_y, world_w, world_h)
    walls = [w.clip(world_rect) for w in walls if w.clip(world_rect).width > 0 and w.clip(world_rect).height > 0]

    # 4.9) 填充 L 形拐角的半墙厚级小缺口（方案 A 的条件化延伸只对段端点生效，
    #      拐角发生在"段中段相交"时，正交两面墙延伸量都为 0，内凹对角会留下
    #      (wt - wt//2) × wt//2 的小正方形缺口；遍历所有格点补矩形补丁即可，
    #      奇数 wt 时补丁略微超出已有墙体 1~2 像素，视觉与碰撞无害）
    half = WALL_THICK // 2
    comp = WALL_THICK - half  # 补偿侧尺寸 = wt - wt//2
    for gy in range(rows + 1):
        for gx in range(cols + 1):
            # 先给外墙侧打默认：只由"同方向"的外墙赋 True。
            #   - Top 外墙 (gy==0) / Bottom 外墙 (gy==rows)：水平横贯 → 水平侧 hL / hR = True
            #   - Left 外墙 (gx==0) / Right 外墙 (gx==cols)：垂直纵贯 → 垂直侧 vU / vD = True
            hL = (gy == 0) or (gy == rows)
            hR = (gy == 0) or (gy == rows)
            vU = (gx == 0) or (gx == cols)
            vD = (gx == 0) or (gx == cols)
            # 再叠加内墙真实状态（仅在数组下标合法范围内判断；存在=覆盖该侧=等效 True）
            #   hL = 水平墙线 gy 上、gx 左侧（格子 (gx-1, gy-1) 的下格线墙）
            #     h_walls 维度：(rows-1) 行 × cols 列，下标合法 gy-1∈[0,rows-2], gx-1∈[0,cols-1]
            if 0 < gy < rows and 0 < gx <= cols and h_walls[gy - 1][gx - 1]:
                hL = True
            #   hR = 水平墙线 gy 上、gx 右侧（格子 (gx, gy-1) 的下格线墙）
            #     下标合法 gy-1∈[0,rows-2], gx∈[0,cols-1]
            if 0 < gy < rows and 0 <= gx < cols and h_walls[gy - 1][gx]:
                hR = True
            #   vU = 垂直墙线 gx 上、gy 上方（格子 (gx-1, gy-1) 的右格线墙）
            #     v_walls 维度：rows 行 × (cols-1) 列，下标合法 gy-1∈[0,rows-1], gx-1∈[0,cols-2]
            if 0 < gy <= rows and 0 < gx < cols and v_walls[gy - 1][gx - 1]:
                vU = True
            #   vD = 垂直墙线 gx 上、gy 下方（格子 (gx-1, gy) 的右格线墙）
            #     下标合法 gy∈[0,rows-1], gx-1∈[0,cols-2]
            if 0 <= gy < rows and 0 < gx < cols and v_walls[gy][gx - 1]:
                vD = True

            Px = offset_x + gx * cell_size
            Py = offset_y + gy * cell_size

            # 4 种 L 形拐角 → 对应象限正方形内的半墙厚缺口补丁
            cases: List[Tuple[int, int, int, int]] = []
            if hL and vU:
                # 左上 L 形：补丁在 (Px-wt, Py-wt) 正方形内右上象限
                sx, sy = Px - WALL_THICK, Py - WALL_THICK
                cases.append((sx + comp, sy, half, half))
            if hR and vU:
                # 右上 L 形：补丁在 (Px, Py-wt) 正方形内左上象限
                sx, sy = Px, Py - WALL_THICK
                cases.append((sx, sy, half, half))
            if hL and vD:
                # 左下 L 形：补丁在 (Px-wt, Py) 正方形内右下象限
                sx, sy = Px - WALL_THICK, Py
                cases.append((sx + comp, sy + comp, half, half))
            if hR and vD:
                # 右下 L 形：补丁在 (Px, Py) 正方形内左下象限
                sx, sy = Px, Py
                cases.append((sx, sy + comp, half, half))

            for (rx, ry, rw, rh) in cases:
                if rw <= 0 or rh <= 0:
                    continue
                rect = pygame.Rect(rx, ry, rw, rh)
                rect = rect.clip(world_rect)
                if rect.width > 0 and rect.height > 0:
                    walls.append(rect)

    # 5) 出生点
    spawn_points = _select_spawn_points(cols, rows, offset_x, offset_y, cell_size)

    maze = Maze(
        size_key=size_key,
        cols=cols,
        rows=rows,
        cell_size=cell_size,
        walls=walls,
        spawn_points=spawn_points,
        playfield_rect=playfield_rect,
        cell_grid=[],  # 预留，当前不使用
        seed=actual_seed,
    )
    mode_label = "原版(隔间砌墙)" if selected_mode == MapGenMode.CLASSIC.value else "新版(破壁凿洞)"
    logger.info(
        f"迷宫生成完毕: 模式={mode_label}, {size_key} {cols}x{rows}, cell_size={cell_size}, "
        f"墙体={len(walls)}, 出生点={len(spawn_points)}, "
        f"战斗区={playfield_w}x{playfield_h}@y={playfield_origin_y}, 候选模式={modes_candidates}"
    )
    return maze


# ============================================================
# 绘制
# ============================================================
def draw_maze(screen: pygame.Surface, maze: Maze, wall_color, floor_color) -> None:
    """
    绘制迷宫（地面 + 间隙补墙 + 所有墙体）。
    """
    # 地面
    pygame.draw.rect(screen, floor_color, maze.playfield_rect)

    # 间隙补墙：cell_size 向下取整导致 world 尺寸略小于 playfield，
    # 用墙色填平四周露白，让非 large 地图也铺满战斗区视觉。
    pf = maze.playfield_rect
    ww = maze.world_width
    wh = maze.world_height
    if ww < pf.w or wh < pf.h:
        ox = pf.x + (pf.w - ww) // 2
        oy = pf.y + (pf.h - wh) // 2
        # 左
        if ox > pf.x:
            pygame.draw.rect(screen, wall_color, (pf.x, pf.y, ox - pf.x, pf.h))
        # 右
        rx = ox + ww
        if rx < pf.right:
            pygame.draw.rect(screen, wall_color, (rx, pf.y, pf.right - rx, pf.h))
        # 上
        if oy > pf.y:
            pygame.draw.rect(screen, wall_color, (ox, pf.y, ww, oy - pf.y))
        # 下
        by = oy + wh
        if by < pf.bottom:
            pygame.draw.rect(screen, wall_color, (ox, by, ww, pf.bottom - by))

    # 墙体
    for w in maze.walls:
        pygame.draw.rect(screen, wall_color, w)
