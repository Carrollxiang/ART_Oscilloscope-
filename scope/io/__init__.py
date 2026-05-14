"""
I/O 层入口 — 反馈系统 + 存储
"""

from .feedback_manager import FeedbackManager
from .feedback_slots.base import FeedbackSlot, SlotConfig, DataSubscription, SlotInfo
from .feedback_slots.null_slot import NullFeedbackSlot
from .feedback_slots.rpyc_slot import RpycFeedbackSlot, RpycSlotConfig
from .feedback_slots.rpyc_pool import RpycConnectionPool

__all__ = [
    "FeedbackManager",
    "FeedbackSlot",
    "SlotConfig",
    "DataSubscription",
    "SlotInfo",
    "NullFeedbackSlot",
    "RpycFeedbackSlot",
    "RpycSlotConfig",
    "RpycConnectionPool",
]
