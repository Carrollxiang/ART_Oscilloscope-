"""
FeedbackWorker — 消费 frame.fitted → PID 反馈分发 (v0.4)

在 asyncio 线程中运行，订阅 frame.fitted topic，
收到 FittedSnapshot 后直接调用 feedback_mgr.dispatch()。

不再重建 AnalysisResult（旧路径 hack）。
"""

from __future__ import annotations

import asyncio
import logging

from scope.runtime import EventBus
from scope.io import FeedbackManager

logger = logging.getLogger(__name__)


class FeedbackWorker:
    """
    反馈分发 Worker (asyncio)。

    用法:
        worker = FeedbackWorker(event_bus, feedback_mgr)
        # 在 asyncio loop 中启动:
        asyncio.create_task(worker.run())
        ...
        worker.stop()
    """

    def __init__(
        self,
        event_bus: EventBus,
        feedback_mgr: FeedbackManager,
    ):
        self._event_bus = event_bus
        self._feedback_mgr = feedback_mgr
        self._queue = event_bus.subscribe("frame.fitted")
        self._running = False

        # 统计
        self._frames_received = 0
        self._frames_dispatched = 0

    @property
    def metrics(self) -> dict:
        return {
            "frames_received": self._frames_received,
            "frames_dispatched": self._frames_dispatched,
            "queue_size": self._queue.qsize,
        }

    async def run(self):
        """在 asyncio loop 中运行，消费 frame.fitted 队列。"""
        self._running = True
        logger.info("FeedbackWorker 已启动")
        while self._running:
            try:
                snapshot = self._queue.get_nowait()
                if snapshot is not None:
                    self._frames_received += 1
                    # FittedSnapshot → 扁平 dict → dispatch
                    flat = snapshot.as_flat_dict()
                    if flat:
                        # dispatch 需要 dict[str, float]，直接传入
                        await self._feedback_mgr.dispatch_raw(flat)
                        self._frames_dispatched += 1
                    await asyncio.sleep(0)
                else:
                    await asyncio.sleep(0.05)  # 空队列
            except Exception as e:
                logger.error(f"FeedbackWorker 异常: {e}", exc_info=True)
                await asyncio.sleep(0.1)

    def stop(self):
        """停止 Worker。"""
        self._running = False
        logger.info(
            f"FeedbackWorker 已停止 "
            f"(received={self._frames_received}, "
            f"dispatched={self._frames_dispatched})"
        )
