"""
工具模块
"""

from .logger import setup_logger, get_logger
from .collision import (
    aabb_overlap,
    rect_from_tank,
    rect_from_projectile,
    reflect_vector,
    slide_collision,
)
from .dpi import is_high_dpi_scale

__all__ = [
    "setup_logger",
    "get_logger",
    "aabb_overlap",
    "rect_from_tank",
    "rect_from_projectile",
    "reflect_vector",
    "slide_collision",
    "is_high_dpi_scale",
]
