"""
NullFeedbackSlot — 空操作反馈插槽（调试用）

只记录日志，不产生实际网络 I/O。
"""

from __future__ import annotations

import logging
from typing import Any

from .base import FeedbackSlot, SlotConfig

logger = logging.getLogger(__name__)


class NullFeedbackSlot(FeedbackSlot):
    """空操作反馈插槽 — 只记录日志"""
    
    protocol = "null"
    
    def __init__(self, config: SlotConfig):
        super().__init__(config)
        self._payloads_received = 0
        self._last_payload: dict = {}
    
    async def start(self):
        """启动"""
        logger.info(f'NullFeedbackSlot "{self.slot_id}" started')
        self._status = self._status.PAUSED  # 默认暂停
    
    async def stop(self):
        """停止"""
        logger.info(
            f'NullFeedbackSlot "{self.slot_id}" stopped '
            f"(received={self._payloads_received})"
        )
        self._status = self._status.IDLE
    
    async def on_data(self, payload: dict[str, Any]):
        """记录数据"""
        if self._status != self._status.RUNNING:
            return
        self._payloads_received += 1
        self._last_payload = payload
        logger.debug(f'NullFeedbackSlot "{self.slot_id}": {payload}')
    
    def _get_target(self) -> str:
        return "null://debug"
