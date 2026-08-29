"""
日志工具
==========

- 控制台 + 文件双通道
- 文件按日期轮转，保留 7 天
- 统一格式：[时间] [级别] [模块] 消息
- 提供全局 get_logger(name)，避免重复配置

使用方式：
    # main.py 入口调用一次 setup_logger(settings)
    # 其他模块使用：
        from app.utils.logger import get_logger
        logger = get_logger(__name__)
        logger.info("...")
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from logging import Logger
from pathlib import Path
from typing import Optional

from ..config.settings import Settings


_LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_INITIALIZED = False


def _get_level_from_env() -> int:
    """从环境变量读取日志级别，默认 INFO。"""
    lv = os.environ.get("LOG_LEVEL", "INFO").upper()
    mapping = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }
    return mapping.get(lv, logging.INFO)


def setup_logger(settings: Optional[Settings] = None) -> Logger:
    """
    初始化全局日志系统（幂等，重复调用只生效第一次）。
    返回根 logger（名为 "tanktrouble"）。
    """
    global _INITIALIZED
    root = logging.getLogger("tanktrouble")
    if _INITIALIZED:
        return root

    level = _get_level_from_env()
    root.setLevel(level)
    root.propagate = False

    # ---- 控制台 handler ----
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(logging.Formatter(_LOG_FORMAT, _DATE_FORMAT))
    root.addHandler(console_handler)

    # ---- 文件 handler（按日期轮转，保留 7 天）----
    if settings is not None:
        logs_dir: Path = settings.logs_dir
        log_file = logs_dir / "tanktrouble.log"
        try:
            file_handler = logging.handlers.TimedRotatingFileHandler(
                filename=str(log_file),
                when="midnight",
                interval=1,
                backupCount=7,
                encoding="utf-8",
            )
            file_handler.setLevel(level)
            file_handler.setFormatter(logging.Formatter(_LOG_FORMAT, _DATE_FORMAT))
            root.addHandler(file_handler)
            root.info(f"日志文件: {log_file}")
        except Exception:  # noqa: BLE001
            # 日志文件创建失败不致命
            root.warning("日志文件初始化失败，仅输出到控制台", exc_info=True)

    _INITIALIZED = True
    return root


def get_logger(name: str) -> Logger:
    """
    获取子 logger。name 建议用 __name__。
    注意：首次调用前应确保 setup_logger 已被 main.py 调用。
    """
    if name.startswith("tanktrouble."):
        return logging.getLogger(name)
    return logging.getLogger(f"tanktrouble.{name}")
