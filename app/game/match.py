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
from ..core.constants import GameMode, AIDifficulty, MapGenMode


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
        map_gen_modes: List[str] | None = None,
        inherit_team_wins: Dict[int, int] | None = None,
    ) -> None:
        self.id = match_id
        self.mode = mode
        self.tanks: Dict[int, Tank] = {t.id: t for t in tanks}
        self.maze_size_key = maze_size_key
        self.ai_difficulty = ai_difficulty
        # 地图生成模式候选列表；None/空时回退新版
        if map_gen_modes is None:
            self.map_gen_modes: List[str] = [MapGenMode.CARVE.value]
        else:
            valid = {MapGenMode.CLASSIC.value, MapGenMode.CARVE.value}
            filtered = [m for m in map_gen_modes if m in valid]
            self.map_gen_modes = filtered if filtered else [MapGenMode.CARVE.value]

        self.state: MatchState = MatchState.PREPARING
        self.end_reason: MatchEndReason | None = None
        # 是否启用"结束条件触发后继续战斗 N 秒"机制（联机 V1 关闭，单机开启）
        self.enable_post_end_continue: bool = True

        # 对局开始后统计
        self.duration_sec: float = 0.0
        self.winner_team_id: int | None = None   # FFA 时为存活坦克 id（取 tank.team 兼容）

        # 队伍胜场（Team 模式跨局累计；FFA 不使用）
        # 构造时先从 tanks 扫描所有 team；再叠加 inherit_team_wins 的历史值
        all_teams = sorted({t.team for t in self.tanks.values() if t.team != 0})
        self.team_wins: Dict[int, int] = {
            tid: (inherit_team_wins or {}).get(tid, 0) for tid in all_teams
        }

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
        """
        如果结束，返回原因；否则 None。并设置 winner_team_id / end_reason。

        注意：本方法**不**修改 self.state —— state 由 BattleEngine 在合适时机（如
        结束条件触发后继续战斗 3s 倒计时归零）统一置为 ENDED。这样"继续打 3s"
        阶段引擎仍能正常 update（因为 state 还是 PLAYING），只是不再重复检查结束。
        """
        if self.state != MatchState.PLAYING:
            return None

        alive_tanks = [t for t in self.tanks.values() if t.alive]

        if self.mode == GameMode.FREE_FOR_ALL:
            if len(alive_tanks) <= 1:
                self.end_reason = MatchEndReason.LAST_TEAM_STANDING
                self.winner_team_id = alive_tanks[0].id if alive_tanks else None
                return self.end_reason
            return None

        teams_alive = {t.team for t in alive_tanks}
        if len(teams_alive) <= 1:
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
