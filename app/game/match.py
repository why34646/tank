"""
对局管理
===========

Match 负责：
- 保存当前局的配置（模式、玩家列表、地图大小等）
- 状态机：PREPARING → PLAYING → ENDED
- 结束判定（仅剩一队/一人）、触发 END 事件
- 无尽模式下递增 AI 难度（参数递增逻辑骨架）

BattleEngine 负责每帧驱动战斗循环（见 game_engine.py）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, List

from .tank import Tank
from ..core.constants import GameMode, AIDifficulty


class MatchState(Enum):
    PREPARING = auto()
    PLAYING = auto()
    ENDED = auto()


class MatchEndReason(Enum):
    LAST_TEAM_STANDING = "last_team"        # 仅剩一队/一人
    HOST_LEFT = "host_left"                 # 房主离开
    MANUAL_STOP = "manual_stop"             # 手动退出


@dataclass
class TeamStanding:
    team_id: int
    alive: bool


class Match:
    """一局对战的管理对象。"""

    def __init__(
        self,
        match_id: int,
        mode: GameMode,
        tanks: List[Tank],
        maze_size_key: str,
        ai_difficulty: AIDifficulty = AIDifficulty.NORMAL,
    ) -> None:
        self.id = match_id
        self.mode = mode
        self.tanks: Dict[int, Tank] = {t.id: t for t in tanks}
        self.maze_size_key = maze_size_key
        self.ai_difficulty = ai_difficulty

        self.state: MatchState = MatchState.PREPARING
        self.end_reason: MatchEndReason | None = None

        # 对局开始后统计
        self.duration_sec: float = 0.0
        self.winner_team_id: int | None = None   # FFA 时为存活坦克 id（取 tank.team 兼容）

    # -------------------------------------------------
    # 状态
    # -------------------------------------------------
    def start(self) -> None:
        if self.state != MatchState.PREPARING:
            return
        self.state = MatchState.PLAYING
        self.duration_sec = 0.0

    def tick(self, dt: float) -> None:
        if self.state == MatchState.PLAYING:
            self.duration_sec += dt

    # -------------------------------------------------
    # 结束判定
    # -------------------------------------------------
    def check_end(self) -> MatchEndReason | None:
        """如果结束，返回原因；否则 None。并设置 winner_team_id。"""
        if self.state != MatchState.PLAYING:
            return None

        alive_tanks = [t for t in self.tanks.values() if t.alive]

        if self.mode == GameMode.FREE_FOR_ALL:
            # 各自为战：存活人数 <= 1 结束
            if len(alive_tanks) <= 1:
                self.state = MatchState.ENDED
                self.end_reason = MatchEndReason.LAST_TEAM_STANDING
                self.winner_team_id = alive_tanks[0].id if alive_tanks else None
                return self.end_reason
            return None

        # 2v2 / 3v3：只剩一支队伍时结束
        teams_alive = {t.team for t in alive_tanks}
        if len(teams_alive) <= 1:
            self.state = MatchState.ENDED
            self.end_reason = MatchEndReason.LAST_TEAM_STANDING
            self.winner_team_id = next(iter(teams_alive)) if teams_alive else None
            return self.end_reason
        return None

    # -------------------------------------------------
    # 无尽模式递进
    # -------------------------------------------------
    @staticmethod
    def next_difficulty(current: AIDifficulty) -> AIDifficulty:
        """
        无尽模式：难度按 AI 难度提高（立项文档十一确认项）。
        噩梦之后保持噩梦。
        """
        order = [AIDifficulty.EASY, AIDifficulty.NORMAL, AIDifficulty.MASTER, AIDifficulty.NIGHTMARE]
        try:
            idx = order.index(current)
            return order[min(idx + 1, len(order) - 1)]
        except ValueError:
            return AIDifficulty.NORMAL
