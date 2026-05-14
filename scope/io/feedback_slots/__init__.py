"""
反馈插槽协议实现

每个文件实现一种协议的 FeedbackSlot。
核心接口在 base.py 中定义。
"""

from .base import FeedbackSlot, SlotConfig, DataSubscription, SlotInfo
from .null_slot import NullFeedbackSlot
from .rpyc_slot import RpycFeedbackSlot, RpycSlotConfig
from .rpyc_pool import RpycConnectionPool

__all__ = [
    "FeedbackSlot",
    "SlotConfig",
    "DataSubscription",
    "SlotInfo",
    "NullFeedbackSlot",
    "RpycFeedbackSlot",
    "RpycSlotConfig",
    "RpycConnectionPool",
]
