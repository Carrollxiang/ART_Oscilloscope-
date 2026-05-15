"""
Null 反馈插槽 — 调试用, 只打日志不发送
"""

import logging
from typing import Any

from scope.model.enums import SlotStatus
from .base import FeedbackSlot, SlotConfig

logger = logging.getLogger(__name__)


class NullFeedbackSlot(FeedbackSlot):
    """
    调试用插槽。

    只将 payload 写入日志, 不产生任何网络 I/O。
    用于验证 dispatch 流程和统计正确性。
    """

    def __init__(self, config: SlotConfig):
        super().__init__(config)
        self._written: list[dict[str, Any]] = []

    @property
    def protocol(self) -> str:
        return "null"

    def _get_target(self) -> str:
        return "logger"

    async def start(self):
        self._status = SlotStatus.PAUSED
        self._written.clear()
        logger.info(f"[{self._config.slot_id}] NullSlot started (paused by default)")

    async def stop(self):
        self._status = SlotStatus.IDLE
        logger.info(f"[{self._config.slot_id}] NullSlot stopped ({self._sent_count} sent)")

    async def on_data(self, payload: dict[str, Any]):
        self._written.append(payload)
        self._count_sent()
        logger.debug(f"[{self._config.slot_id}] payload={payload}")

    async def reconfigure(self, config: SlotConfig):
        self._config = config
        logger.info(f"[{self._config.slot_id}] reconfigured")

    @property
    def history(self) -> list[dict[str, Any]]:
        """返回所有已记录的 payload, 测试用"""
        return list(self._written)
