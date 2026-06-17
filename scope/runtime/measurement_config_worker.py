"""
MeasurementConfigWorker — 消费 measurement.specs.changed 控制面事件。

将 UI 发布的 MeasurementSpec 快照应用到 MeasurementProcessor。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from .event_bus import EventBus
from .measurement_change import MeasurementSpecsChanged
from .measurement_processor import MeasurementProcessor

logger = logging.getLogger(__name__)


class MeasurementConfigWorker:
    """测量规格配置 Worker。"""

    def __init__(self, event_bus: EventBus, processor: MeasurementProcessor):
        self._queue = event_bus.subscribe("measurement.specs.changed")
        self._processor = processor
        self._running = False
        self._last_change_id: Optional[int] = None
        self._changes_received = 0
        self._changes_applied = 0
        self._changes_skipped = 0

    @property
    def metrics(self) -> dict:
        return {
            "changes_received": self._changes_received,
            "changes_applied": self._changes_applied,
            "changes_skipped": self._changes_skipped,
            "queue_size": self._queue.qsize,
        }

    async def run(self):
        """在 asyncio loop 中运行，消费测量规格变更。"""
        self._running = True
        logger.info("MeasurementConfigWorker 已启动")
        while self._running:
            try:
                change = self._queue.get_nowait()
                if change is not None:
                    self._changes_received += 1
                    self._apply_change(change)
                    await asyncio.sleep(0)
                else:
                    await asyncio.sleep(0.1)
            except Exception as e:
                logger.error(f"MeasurementConfigWorker 异常: {e}", exc_info=True)
                await asyncio.sleep(0.2)

    def _apply_change(self, change: MeasurementSpecsChanged):
        """应用一组测量规格。"""
        if (
            self._last_change_id is not None
            and change.change_id <= self._last_change_id
        ):
            self._changes_skipped += 1
            return

        self._last_change_id = change.change_id
        self._processor.set_specs(change.specs)
        self._changes_applied += 1
        logger.info(
            "MeasurementConfigWorker 应用测量规格 #%s: %s 项",
            change.change_id,
            len(change.specs),
        )

    def stop(self):
        """停止 Worker。"""
        self._running = False
        logger.info(
            "MeasurementConfigWorker 已停止 "
            "(received=%s, applied=%s, skipped=%s)",
            self._changes_received,
            self._changes_applied,
            self._changes_skipped,
        )
