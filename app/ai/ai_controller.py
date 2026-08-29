"""
AI 控制器
===========

架构阶段：提供 AI 控制框架 + 难度分级入口，每一帧 AI 返回 TankInput。

行为骨架（不写具体逻辑，按难度分级切换策略实例）：
- Easy：随机方向 + 低频射击
- Normal：会朝最近敌人移动并偶尔射击
- Master：会预判玩家位置、瞄准反弹路径（后续实现）
- Nightmare：反应更快、瞄更准（后续实现）

当前占位版本：所有难度均输出"原地不动、不射击"的空输入。
后续迭代在各子类中填充策略。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..core.constants import AIDifficulty
from ..game.tank import Tank, TankInput

if TYPE_CHECKING:
    from ..game.game_engine import BattleEngine


class AIController:
    """AI 控制器基类。"""

    difficulty: AIDifficulty = AIDifficulty.NORMAL

    def think(self, tank: Tank, engine: "BattleEngine") -> TankInput:
        """每帧返回本帧输入。子类实现。"""
        return TankInput()


class EasyAIController(AIController):
    difficulty = AIDifficulty.EASY

    def think(self, tank: Tank, engine: "BattleEngine") -> TankInput:
        return TankInput()


class NormalAIController(AIController):
    difficulty = AIDifficulty.NORMAL

    def think(self, tank: Tank, engine: "BattleEngine") -> TankInput:
        return TankInput()


class MasterAIController(AIController):
    difficulty = AIDifficulty.MASTER

    def think(self, tank: Tank, engine: "BattleEngine") -> TankInput:
        return TankInput()


class NightmareAIController(AIController):
    difficulty = AIDifficulty.NIGHTMARE

    def think(self, tank: Tank, engine: "BattleEngine") -> TankInput:
        return TankInput()


# 工厂
_DIFFICULTY_MAP = {
    AIDifficulty.EASY: EasyAIController,
    AIDifficulty.NORMAL: NormalAIController,
    AIDifficulty.MASTER: MasterAIController,
    AIDifficulty.NIGHTMARE: NightmareAIController,
}


def make_ai_controller(difficulty: AIDifficulty) -> AIController:
    """按难度实例化对应 AI。"""
    cls = _DIFFICULTY_MAP.get(difficulty, NormalAIController)
    return cls()
