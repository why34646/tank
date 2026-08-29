"""
高 DPI 适配工具
=================

Windows 下高 DPI 缩放检测（备用，主入口已调用 SetProcessDPIAware）。
"""

from __future__ import annotations

import sys


def is_high_dpi_scale() -> bool:
    """尝试判断当前系统 DPI 是否为高缩放。返回 True 表示可能需要适配。"""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        try:
            gdi = ctypes.windll.gdi32
            user32 = ctypes.windll.user32
            hdc = user32.GetDC(0)
            LOGPIXELSX = 88
            dpi = gdi.GetDeviceCaps(hdc, LOGPIXELSX)
            user32.ReleaseDC(0, hdc)
            return dpi > 96
        except Exception:  # noqa: BLE001
            return False
    except Exception:  # noqa: BLE001
        return False
