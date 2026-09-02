"""
战绩存储
===========

JSON 文件存储，路径：%APPDATA%/TankTrouble/battle_records.json

存储结构：
    {
        "next_id": 42,       // 下一条将使用的编号（1-based 递增）
        "records": [
            {
                "id": 1,
                "game_scope": "个人" | "联机",
                "mode": "各自为战" | "分两队" | "分三队",
                "total_rounds": int,     // 一场内玩了多少局
                "kills": int,            // 玩家累计击杀
                "survived": int,         // 玩家累计存活局数
                "team_wins": int,        // 队伍胜场（Team 模式才有，FFA=0）
                "duration_sec": int,     // 一场总时长（秒）
                "started_at": "2026-08-30 21:40"   // 对局开始时间（到分钟）
            },
            ...
        ]
    }

功能：
- 初始化时自动创建空文件
- 写入时原子替换（先写 .tmp 再 rename，防损坏）
- 读取损坏时自动备份旧文件并重建
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from ..config.settings import Settings
from ..core.exceptions import StorageError
from ..utils.logger import get_logger

logger = get_logger(__name__)


# ============================================================
# 战绩记录数据类
# ============================================================
@dataclass
class BattleRecord:
    """单条战绩记录。

    一条记录 = 一次个人游戏（可跨多局无尽）或一次联机游戏。
    """
    id: int = 0                       # 编号，由 RecordsStore.add() 自动分配
    game_scope: str = "个人"           # "个人" / "联机"
    mode: str = ""                    # GameMode.value（"各自为战"/"分两队"/"分三队"）
    total_rounds: int = 1             # 一场内玩了多少局
    kills: int = 0                    # 玩家累计击杀
    survived: int = 0                 # 玩家累计存活局数
    team_wins: int = 0                # 队伍胜场（仅 Team 模式有效，FFA 恒为 0）
    duration_sec: int = 0             # 一场总时长（秒）
    started_at: str = ""              # 对局开始时间 "YYYY-MM-DD HH:MM"

    def __post_init__(self) -> None:
        if not self.started_at:
            # 默认取当前时间到分钟
            self.started_at = datetime.now().strftime("%Y-%m-%d %H:%M")


# ============================================================
# 存储管理器
# ============================================================
class RecordsStore:
    """战绩 JSON 文件读写管理器。"""

    def __init__(self, settings: Settings) -> None:
        self._path: Path = settings.records_file

    # -------------------------------------------------
    # 初始化
    # -------------------------------------------------
    def ensure_file(self) -> None:
        """确保文件存在，不存在则创建空结构。损坏则备份重建。"""
        if self._path.exists():
            try:
                self._read_raw()
                logger.info(f"战绩文件加载完成: {self._path}")
                return
            except StorageError:
                # 损坏：备份
                backup = self._path.with_suffix(self._path.suffix + f".bak.{int(time.time())}")
                try:
                    shutil.copy(self._path, backup)
                    logger.warning(f"战绩文件损坏，已备份到 {backup}，正在重建空文件")
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"备份战绩文件失败: {exc}")
        self._write_raw({"next_id": 1, "records": []})
        logger.info(f"战绩文件初始化: {self._path}")

    # -------------------------------------------------
    # CRUD
    # -------------------------------------------------
    def load_all(self) -> List[BattleRecord]:
        """读取所有战绩，按 id 倒序返回（最新的排最前）。"""
        raw = self._read_raw()
        result: List[BattleRecord] = []
        for item in raw.get("records", []):
            try:
                # 兼容旧文件字段（旧字段名不同时用默认值填充）
                result.append(BattleRecord(**self._normalize_old_record(item)))
            except TypeError:
                logger.warning(f"跳过格式异常的战绩记录: {item}")
        # 编号倒序 = 最新在最前
        result.sort(key=lambda r: r.id, reverse=True)
        return result

    def add(self, record: BattleRecord) -> BattleRecord:
        """追加一条战绩，自动分配递增编号，返回填好 id 的 record。"""
        raw = self._read_raw()
        next_id: int = raw.get("next_id", 1)
        record.id = next_id
        raw.setdefault("records", []).append(asdict(record))
        raw["next_id"] = next_id + 1
        self._write_raw(raw)
        logger.info(
            f"战绩已保存 #{record.id:03d}: {record.game_scope}/{record.mode} "
            f"总局{record.total_rounds} 击杀{record.kills} 存活{record.survived} "
            f"时长{record.duration_sec}s"
        )
        return record

    # -------------------------------------------------
    # 兼容旧字段（如果用户之前有旧战绩文件）
    # -------------------------------------------------
    @staticmethod
    def _normalize_old_record(item: dict) -> dict:
        """把旧格式字段（win / survival / duration_sec / mode / is_online）映射到新字段。"""
        if "total_rounds" in item:
            return item  # 已经是新格式
        # 旧格式 → 新格式
        old_mode = item.get("mode", "free_for_all")
        scope = "联机" if item.get("is_online") else "个人"
        mode_map = {
            "free_for_all": "各自为战",
            "2v2": "分两队",
            "3v3": "分三队",
            "TEAMS_2": "分两队",
            "TEAMS_3": "分三队",
        }
        new_mode = mode_map.get(old_mode, old_mode)
        return {
            "id": item.get("id", 0),
            "game_scope": scope,
            "mode": new_mode,
            "total_rounds": 1,
            "kills": item.get("kills", 0),
            "survived": 1 if item.get("survival", 0) else 0,
            "team_wins": 0,
            "duration_sec": item.get("duration_sec", 0),
            "started_at": item.get("timestamp", "")[:16].replace("T", " "),
        }

    # -------------------------------------------------
    # 内部：读写原始 JSON
    # -------------------------------------------------
    def _read_raw(self) -> dict:
        try:
            with self._path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or "records" not in data:
                raise StorageError(f"战绩文件格式异常: {self._path}")
            data.setdefault("next_id", 1)
            return data
        except StorageError:
            raise
        except json.JSONDecodeError as exc:
            raise StorageError("战绩文件 JSON 解析失败", cause=exc)
        except OSError as exc:
            raise StorageError("战绩文件读取失败", cause=exc)

    def _write_raw(self, data: dict) -> None:
        # 原子写入：先写临时文件再 rename；加重试应对多进程并发写撞 PermissionError。
        import random as _r
        last_exc: Optional[BaseException] = None
        for attempt in range(6):
            tmp = self._path.with_name(self._path.name + f".tmp{os.getpid()}_{attempt}")
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with tmp.open("w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                os.replace(str(tmp), str(self._path))
                return
            except PermissionError as exc:
                # Windows 下两个进程同时 os.replace 可能撞 — 短退避后重试
                last_exc = exc
                time.sleep(0.05 + _r.random() * 0.1)
                continue
            except OSError as exc:
                last_exc = exc
                break
            finally:
                try:
                    if tmp.exists():
                        tmp.unlink()
                except OSError:
                    pass
        raise StorageError(
            "战绩文件写入失败（多次重试后仍冲突）", cause=last_exc
        )
