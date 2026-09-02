"""游戏音效管理器（pygame.mixer 封装）。

支持：预加载 ogg / wav，按名字播放，固定 channel 分配，音量走 settings。
"""

from __future__ import annotations

import os
from typing import Dict, Optional

import pygame

from app.utils.logger import get_logger

logger = get_logger(__name__)


class SoundManager:
    """单例音效管理器。"""

    _instance: Optional["SoundManager"] = None

    # 资源名 → 文件名（不带扩展名）
    _FILE_NAMES = {
        "boom": "boom",
        "shoot": "shoot",
        "disappear": "disappear",
        "kada": "kada",
    }

    # channel 分配：固定 channel，避免同类音效互相打断
    _CHANNEL_MAP: Dict[str, int] = {
        "boom": 0,
        "shoot": 1,
        "disappear": 2,
    }

    def __init__(self) -> None:
        self._sounds: Dict[str, pygame.mixer.Sound] = {}
        self._muted = False
        self._volume = 0.7

    @classmethod
    def instance(cls) -> "SoundManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # -------------------------------------------------
    # 生命周期
    # -------------------------------------------------
    def preload(self, base_dir: str, volume: float = 0.7) -> None:
        """加载所有音效文件。base_dir = app/assets/sound 目录。"""
        self._volume = volume
        # 确保 mixer 已初始化
        if not pygame.mixer.get_init():
            try:
                pygame.mixer.init()
            except pygame.error as e:
                logger.warning(f"mixer 初始化失败，音效不可用: {e}")
                return

        # 预留 channel（pygame 默认 8 个，足够用）
        try:
            pygame.mixer.set_num_channels(8)
        except Exception:
            pass

        for name, fname in self._FILE_NAMES.items():
            # 优先 ogg，fallback mp3
            path = os.path.join(base_dir, f"{fname}.ogg")
            if not os.path.isfile(path):
                path = os.path.join(base_dir, f"{fname}.mp3")
            if not os.path.isfile(path):
                logger.warning(f"音效文件不存在: {fname} (ogg/mp3 都没找到)")
                continue
            try:
                snd = pygame.mixer.Sound(path)
                snd.set_volume(volume)
                self._sounds[name] = snd
                logger.info(f"音效加载: {name} <- {os.path.basename(path)}")
            except pygame.error as e:
                logger.warning(f"音效加载失败 {name}: {e}")

    # -------------------------------------------------
    # 播放
    # -------------------------------------------------
    def play(self, name: str) -> None:
        """按名字播放音效。找不到或静音则跳过。"""
        if self._muted:
            return
        snd = self._sounds.get(name)
        if snd is None:
            return
        ch_id = self._CHANNEL_MAP.get(name)
        if ch_id is not None:
            channel = pygame.mixer.Channel(ch_id)
            channel.play(snd)
        else:
            snd.play()

    # -------------------------------------------------
    # 控制
    # -------------------------------------------------
    def set_volume(self, v: float) -> None:
        """设置所有已加载音效音量（0~1）。"""
        v = max(0.0, min(1.0, v))
        self._volume = v
        for snd in self._sounds.values():
            snd.set_volume(v)

    def mute(self) -> None:
        self._muted = True

    def unmute(self) -> None:
        self._muted = False

    @property
    def muted(self) -> bool:
        return self._muted

    def stop_all(self) -> None:
        """停止所有 channel。"""
        for i in range(pygame.mixer.get_num_channels()):
            try:
                pygame.mixer.Channel(i).stop()
            except Exception:
                pass
