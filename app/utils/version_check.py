"""
远程版本升级检测
==================

架构阶段提供骨架实现：
- 定义 VersionInfo 数据类
- 定义 check_latest_version() 函数占位（实际 URL 由部署后填入）
- 主界面启动时可调用，返回是否有新版本 + 下载地址

注意：在未配置 VERSION_CHECK_URL 环境变量时，函数立即返回 None（不联网）。
"""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from typing import Optional

from ..config.settings import Settings
from .logger import get_logger

logger = get_logger(__name__)


@dataclass
class VersionInfo:
    latest_version: str      # 语义化版本号，如 "1.0.1"
    download_url: str        # 安装包下载地址
    changelog: str = ""      # 更新说明（可选）


def check_latest_version(settings: Settings, timeout: float = 5.0) -> Optional[VersionInfo]:
    """
    检查远程最新版本。
    - 未配置 VERSION_CHECK_URL 返回 None（不做联网）
    - 联网失败返回 None，写日志不抛异常
    - 有新版本且版本号大于本地 VERSION 时返回 VersionInfo
    """
    url = os.environ.get("VERSION_CHECK_URL")
    if not url:
        return None

    try:
        req = urllib.request.Request(url, headers={"User-Agent": f"TankTrouble/{settings.VERSION}"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                logger.warning(f"版本检查 HTTP {resp.status}")
                return None
            data = json.loads(resp.read().decode("utf-8"))
            latest = data.get("latest_version", "")
            if not latest:
                logger.warning("版本检查响应缺少 latest_version 字段")
                return None
            if _is_newer(latest, settings.VERSION):
                return VersionInfo(
                    latest_version=latest,
                    download_url=data.get("download_url", ""),
                    changelog=data.get("changelog", ""),
                )
            return None
    except Exception as exc:  # noqa: BLE001
        logger.info(f"版本检查失败: {exc}")
        return None


def _is_newer(latest: str, current: str) -> bool:
    """简单语义化版本比较：x.y.z。非数字段返回 False。"""
    def parse(v: str):
        try:
            parts = v.strip().split(".")
            return tuple(int(p) for p in parts[:3])
        except ValueError:
            return None
    a = parse(latest)
    b = parse(current)
    if not a or not b:
        return False
    return a > b
