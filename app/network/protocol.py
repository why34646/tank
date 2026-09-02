"""
网络消息协议
===============

集中枚举所有消息类型，并记录每种消息 data 字段的结构约定（文档化）。

消息外层格式（见 message.py）：
    {
        "type": str,          # 本文件 MessageType 的 value
        "data": dict,         # 业务字段
        "seq": int | None,    # 可选序列号（预留）
        "ts": int | None,     # 可选时间戳毫秒（预留）
    }

注意：此处仅定义常量与结构文档，具体消息构造/解析在 server/client 中完成。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict


class MessageType(str, Enum):
    """TCP 消息类型枚举。value 即 on-wire 字符串。"""

    # ===== 房间发现（UDP 广播，格式同 JSON）=====
    ROOM_BROADCAST = "room_broadcast"       # 房主 UDP 广播：房间名、端口、人数、密码是否有

    # ===== 房间加入（TCP）=====
    ROOM_JOIN_REQ = "room_join_req"         # 客户端 -> 房主：代号、密码
    ROOM_JOIN_RESP = "room_join_resp"       # 房主 -> 客户端：成功/失败原因、玩家id、房间快照

    # ===== 房间内状态同步 =====
    ROOM_STATE_SYNC = "room_state_sync"     # 房主 -> 所有：成员列表、设置项、准备状态
    PLAYER_READY = "player_ready"           # 客户端 -> 房主：准备状态变更
    PLAYER_LEAVE = "player_leave"           # 客户端 -> 房主：离开房间
    PLAYER_JOINED_NOTIFY = "player_joined_notify"  # 房主 -> 其他成员：新人加入通知
    PLAYER_LEFT_NOTIFY = "player_left_notify"      # 房主 -> 其他成员：有人离开通知

    # ===== 聊天 =====
    CHAT_MSG_SEND = "chat_msg_send"         # 客户端 -> 房主
    CHAT_MSG_BROADCAST = "chat_msg_broadcast"  # 房主 -> 所有

    # ===== 房主设置变更 =====
    ROOM_SETTINGS_CHANGE = "room_settings_change"  # 房主 -> 所有：新的房间设置
    PLAYER_COLOR_CHANGE = "player_color_change"    # 客户端 -> 房主 -> 所有：玩家坦克颜色变更（房主校验不重复）
    PLAYER_TEAM_CHANGE = "player_team_change"      # 客户端 -> 房主 -> 所有：玩家队伍变更（房主校验队伍人数）

    # ===== 对局流程 =====
    GAME_START_NOTIFY = "game_start_notify"      # 房主 -> 所有：开始对局（含初始状态）
    GAME_STATE_SYNC = "game_state_sync"          # 房主 -> 所有：周期性游戏状态快照（校准用，10Hz）
    EVENT_NOTIFY = "event_notify"                # 房主 -> 所有：即时事件通知（反弹/击中/消失，事件触发时立即发）
    PLAYER_INPUT = "player_input"                # 客户端 -> 房主：本帧玩家输入
    INPUT_BUNDLE = "input_bundle"                # 房主 -> 所有：所有坦克（玩家+AI）本帧输入（60Hz）
    GAME_ROUND_START = "game_round_start"        # 房主 -> 所有：新一局 round 开始（新 seed + 初始 tank 快照）
    GAME_END_NOTIFY = "game_end_notify"          # 房主 -> 所有：整场 game session 结束（带 reason）
    # GAME_END_NOTIFY.reason 取值：
    #   "host_exit"            房主主动 ESC 退出 → 全员回主菜单 + 仅客户端提示
    #   "player_exit"          某客户端主动 ESC 退出 → 退出者回主菜单，其余回房间
    #   "player_disconnect"    某客户端断连超时（6s）→ 同 player_exit
    #   "player_disconnect_timeout" 同上
    #   "player_leave"         PLAYER_LEAVE → 房主广播结束 → 同 player_exit

    # ===== 对局暂停（断连等待重连）=====
    GAME_PAUSE_NOTIFY = "game_pause_notify"      # 房主 -> 所有：暂停并告知"等待某玩家重连"
    GAME_RESUME_NOTIFY = "game_resume_notify"    # 房主 -> 所有：对局恢复


    # ===== 心跳/保活 =====
    PING = "ping"
    PONG = "pong"

    # ===== 通用错误 =====
    ERROR = "error"


# ============================================================
# 结构约定（仅供参考/文档）
# ============================================================
@dataclass(frozen=True)
class MessageProtocol:
    """
    仅作为协议字段文档。不参与实际运行。

    关键 data 结构：
    - ROOM_BROADCAST (UDP):
        {
            "room_name": str,
            "host_codename": str,
            "tcp_port": int,
            "total_slots": int,
            "current_players": int,
            "has_password": bool,
            "maze_sizes": [str,...],
        }
    - ROOM_JOIN_REQ:
        { "codename": str, "password": str }
    - ROOM_JOIN_RESP:
        { "ok": bool, "reason": str, "player_id": int, "room_snapshot": {...} }
    - PLAYER_READY:
        { "ready": bool }
    - PLAYER_INPUT (联机对战核心):
        {
            "match_id": int,
            "tank_id": int,
            "input": {
                "move_x": int, "move_y": int,
                "fire": bool, "aim_angle": float | null,
            },
            "frame": int,
        }
    - INPUT_BUNDLE（房主 -> 所有客户端，60Hz 广播）:
        {
            "frame": int,        # 房主 engine 帧号（用于追踪）
            "inputs": [
                {"tank_id": int,
                 "input": {"move_x": int, "move_y": int, "fire": bool}},
                ...  # 所有坦克：玩家坦克 + AI 坦克
            ]
        }
      客户端收到后，遍历 inputs 注入 BattleEngine.input_overrides，然后完整跑 engine.update(dt, ai_provider=None)。
      这样客户端本地也触发发射动画、爆炸粒子、炮弹消失动画、音效等所有视觉效果。
    - GAME_STATE_SYNC（房主 -> 所有客户端，10Hz 校准用，不再高频做位置同步）:
        {
            "match_id": int,
            "frame": int,
            "duration": float,
            "last_input_seqs": {str(tank_id): int, ...},  # ack：房主最近已处理到的客户端输入 seq
            "tanks": [{
                "id": int, "x": float, "y": float, "angle": float,
                "alive": bool, "ammo": int, "kills": int, "team": int,
                "round_survived": int,
            }, ...],
            "projectiles": [{
                "x": float, "y": float, "vx": float, "vy": float, "owner": int
            }, ...],
        }
    - CHAT_MSG_SEND:
        { "text": str }
    - CHAT_MSG_BROADCAST:
        { "from_codename": str, "text": str, "ts_ms": int }
    """

    @staticmethod
    def wrap(type_: MessageType, data: Dict[str, Any], seq: int | None = None) -> Dict[str, Any]:
        """
        构造标准消息字典。server/client 统一通过此函数打包。
        """
        msg: Dict[str, Any] = {"type": type_.value, "data": data}
        if seq is not None:
            msg["seq"] = seq
        return msg
