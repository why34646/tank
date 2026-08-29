"""
战绩存储
===========

JSON 文件存储，路径：%APPDATA%/TankTrouble/battle_records.json

存储结构：
    {
        "records": [
            {
                "win": true/false,
                "kills": int,
                "survival": int,
                "max_streak": int,
                "duration_sec": int,
                "mode": "free_for_all" | "2v2" | "3v3",
                "is_online": true/false,
                "teammates": ["代号A", ...],
                "opponents": ["代号B", ...],
                "timestamp": "2026-08-29T12:00:00"
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
import shutil
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from ..config.settings import Settings
from ..core.exceptions import StorageError
from ..core.constants import GameMode
from ..utils.logger import get_logger

logger = get_logger(__name__)


# ============================================================
# 战绩记录数据类
# ============================================================
@dataclass
class BattleRecord:
    """单条战绩记录。"""
    win: bool
    kills: int
    survival: int
    max_streak: int
    duration_sec: int
    mode: str                       # GameMode.value
    is_online: bool = False
    teammates: List[str] = field(default_factory=list)
    opponents: List[str] = field(default_factory=list)
    timestamp: str = ""            # ISO 格式

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now().replace(microsecond=0).isoformat()


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
        self._write_raw({"records": []})
        logger.info(f"战绩文件初始化: {self._path}")

    # -------------------------------------------------
    # CRUD
    # -------------------------------------------------
    def load_all(self) -> List[BattleRecord]:
        """读取所有战绩，按时间倒序返回。"""
        raw = self._read_raw()
        result: List[BattleRecord] = []
        for item in raw.get("records", []):
            try:
                result.append(BattleRecord(**item))
            except TypeError:
                logger.warning(f"跳过格式异常的战绩记录: {item}")
        # 时间倒序
        result.sort(key=lambda r: r.timestamp, reverse=True)
        return result

    def add(self, record: BattleRecord) -> None:
        """追加一条战绩。"""
        raw = self._read_raw()
        raw.setdefault("records", []).append(asdict(record))
        self._write_raw(raw)
        logger.info(f"战绩已保存: 击杀={record.kills}, 存活={record.survival}, 胜={record.win}")

    # -------------------------------------------------
    # 内部：读写原始 JSON
    # -------------------------------------------------
    def _read_raw(self) -> dict:
        try:
            with self._path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or "records" not in data:
                raise StorageError(f"战绩文件格式异常: {self._path}")
            return data
        except StorageError:
            raise
        except json.JSONDecodeError as exc:
            raise StorageError("战绩文件 JSON 解析失败", cause=exc)
        except OSError as exc:
            raise StorageError("战绩文件读取失败", cause=exc)

    def _write_raw(self, data: dict) -> None:
        # 原子写入：先写临时文件再 rename
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os_replace = getattr(__import__("os"), "replace")
            os_replace(str(tmp), str(self._path))
        except OSError as exc:
            raise StorageError("战绩文件写入失败", cause=exc)
        finally:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
