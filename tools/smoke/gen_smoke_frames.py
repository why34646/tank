"""
烟雾粒子帧生成脚本（开发工具，非游戏运行时代码）

用途：数字变化特效的半透明灰色烟雾动画，生成 30 帧透明背景 PNG。
输出：tools/output/smoke_00.png ~ smoke_29.png

运行方式：python tools/smoke/gen_smoke_frames.py
依赖：pygame（项目已有依赖）
"""

import os
import random

import pygame

# ============================================================
# 配置
# ============================================================
FRAME_COUNT = 30          # 总帧数
FRAME_WIDTH = 60          # 烟雾画布宽
FRAME_HEIGHT = 80         # 烟雾画布高
FPS = 30                  # 播放帧率（仅用于计算 dt）

# 数字区域（烟雾发源地）：底部中央 30x40
NUM_WIDTH = 30
NUM_HEIGHT = 40
NUM_X = (FRAME_WIDTH - NUM_WIDTH) // 2
NUM_Y = FRAME_HEIGHT - NUM_HEIGHT

# 发射配置
EMIT_FRAME_COUNT = 8      # 前 N 帧持续发射粒子
PARTICLES_PER_FRAME = 3   # 每帧发射粒子数
POOL_SIZE = 60            # 对象池上限

# 粒子外观
SMOKE_COLOR = (145, 145, 145)   # 半灰
INITIAL_ALPHA = 170
RADIUS_MIN = 2
RADIUS_MAX = 4

# 粒子运动
SPEED_Y_MIN = -28.0   # 向上（y 减小）
SPEED_Y_MAX = -14.0
SPEED_X_MIN = -7.0
SPEED_X_MAX = 7.0
LIFE_MIN = 0.55
LIFE_MAX = 0.95
GROW_RATE = 3.5       # 半径每秒增长像素
DRAG_X = 0.96         # 水平阻力（每帧）
DRAG_Y = 0.985        # 垂直阻力（每帧）

# 路径
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "output"))


# ============================================================
# 粒子
# ============================================================
class SmokeParticle:
    """单个烟雾粒子，使用对象池复用。"""

    def __init__(self):
        self.active = False

    def spawn(self):
        """激活并重置到数字区域内随机位置。"""
        self.active = True
        self.x = NUM_X + random.uniform(0, NUM_WIDTH)
        self.y = NUM_Y + random.uniform(0, NUM_HEIGHT)
        self.vx = random.uniform(SPEED_X_MIN, SPEED_X_MAX)
        self.vy = random.uniform(SPEED_Y_MIN, SPEED_Y_MAX)
        self.radius = random.uniform(RADIUS_MIN, RADIUS_MAX)
        self.max_life = random.uniform(LIFE_MIN, LIFE_MAX)
        self.life = self.max_life
        self.alpha = INITIAL_ALPHA

    def update(self, dt):
        """更新一帧，返回是否仍存活。"""
        self.life -= dt
        if self.life <= 0:
            self.active = False
            return False

        self.x += self.vx * dt
        self.y += self.vy * dt
        self.vx *= DRAG_X
        self.vy *= DRAG_Y
        self.radius += GROW_RATE * dt

        life_ratio = self.life / self.max_life
        self.alpha = int(INITIAL_ALPHA * life_ratio)
        return True

    def draw(self, surface):
        """绘制到目标 Surface，支持半透明。"""
        if self.alpha <= 0:
            return
        r = max(1, int(self.radius))
        size = r * 2 + 2
        s = pygame.Surface((size, size), pygame.SRCALPHA)
        color = (*SMOKE_COLOR, self.alpha)
        pygame.draw.circle(s, color, (size // 2, size // 2), r)
        surface.blit(s, (int(self.x - size // 2), int(self.y - size // 2)))


# ============================================================
# 主流程
# ============================================================
def main():
    # 无头模式，不弹窗口
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    pygame.init()
    pygame.display.set_mode((1, 1))

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 对象池
    pool = [SmokeParticle() for _ in range(POOL_SIZE)]

    dt = 1.0 / FPS

    for frame_idx in range(FRAME_COUNT):
        # 1. 发射期内持续生成新粒子
        if frame_idx < EMIT_FRAME_COUNT:
            for _ in range(PARTICLES_PER_FRAME):
                for p in pool:
                    if not p.active:
                        p.spawn()
                        break

        # 2. 新建透明画布
        canvas = pygame.Surface((FRAME_WIDTH, FRAME_HEIGHT), pygame.SRCALPHA)
        canvas.fill((0, 0, 0, 0))

        # 3. 更新并绘制所有活跃粒子
        for p in pool:
            if p.active:
                p.update(dt)
                p.draw(canvas)

        # 4. 保存帧
        filename = f"smoke_{frame_idx:02d}.png"
        filepath = os.path.join(OUTPUT_DIR, filename)
        pygame.image.save(canvas, filepath)
        print(f"[{frame_idx + 1:02d}/{FRAME_COUNT}] saved {filename}")

    pygame.quit()
    print(f"\n完成：{FRAME_COUNT} 帧已导出到 {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
