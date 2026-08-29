"""
AI 对手模块
"""

from .ai_controller import (
    AIController,
    EasyAIController,
    NormalAIController,
    MasterAIController,
    NightmareAIController,
    make_ai_controller,
)

__all__ = [
    "AIController",
    "EasyAIController",
    "NormalAIController",
    "MasterAIController",
    "NightmareAIController",
    "make_ai_controller",
]
