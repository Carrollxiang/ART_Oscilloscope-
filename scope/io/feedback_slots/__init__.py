"""
Feedback Slots — 反馈插槽模块

提供各种反馈实现（rpyc、PID、null 等）。
"""

from .base import FeedbackSlot, SlotConfig, DataSubscription, SlotInfo

__all__ = [
    "FeedbackSlot",
    "SlotConfig",
    "DataSubscription",
    "SlotInfo",
]
