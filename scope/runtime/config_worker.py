"""
ConfigWorker — 消费 config.change → 硬件配置重配 (v0.4)

在 asyncio 线程中运行，订阅 config.change topic，
收到 ConfigChange 后调用设备重建逻辑。

走控制面路径，确保配置变更在帧边界原子生效。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Optional

from scope.runtime import EventBus, ConfigChange
from scope.hardware import DeviceConfig

logger = logging.getLogger(__name__)


class ConfigWorker:
    """
    硬件配置变更 Worker (asyncio)。

    apply_fn 是一个同步回调，用于实际执行硬件重配。
    通常传入 ScopeApp._on_art_config()。

    用法:
        worker = ConfigWorker(event_bus, apply_fn=self._on_art_config)
        asyncio.create_task(worker.run())
        ...
        worker.stop()
    """

    def __init__(
        self,
        event_bus: EventBus,
        apply_fn: Callable[[dict[str, Any], DeviceConfig], None],
    ):
        self._event_bus = event_bus
        self._apply_fn = apply_fn
        self._queue = event_bus.subscribe("config.change")
        self._running = False
        self._last_change_id: Optional[int] = None

        # 统计
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
        """在 asyncio loop 中运行，消费 config.change 队列。"""
        self._running = True
        logger.info("ConfigWorker 已启动")
        while self._running:
            try:
                change = self._queue.get_nowait()
                if change is not None:
                    self._changes_received += 1
                    await self._apply_change(change)
                    await asyncio.sleep(0)
                else:
                    await asyncio.sleep(0.1)  # 空队列
            except Exception as e:
                logger.error(f"ConfigWorker 异常: {e}", exc_info=True)
                await asyncio.sleep(0.2)

    async def _apply_change(self, change: ConfigChange):
        """应用一个配置变更（含去重）。"""
        # 去重: 跳过相同 change_id
        if (self._last_change_id is not None
                and change.change_id <= self._last_change_id):
            self._changes_skipped += 1
            logger.debug(
                f"ConfigWorker 跳过重复 change_id={change.change_id}"
            )
            return

        self._last_change_id = change.change_id
        logger.info(
            f"ConfigWorker 应用配置 #{change.change_id}: "
            f"sample_rate={change.device_config.sample_rate}, "
            f"channels={len(change.device_config.channels_enabled)}"
        )

        # 同步调用 apply_fn（_on_art_config 内部有重试/回退逻辑）
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            self._apply_fn,
            change.art_params,
            change.device_config,
        )
        self._changes_applied += 1

    def stop(self):
        """停止 Worker。"""
        self._running = False
        logger.info(
            f"ConfigWorker 已停止 "
            f"(received={self._changes_received}, "
            f"applied={self._changes_applied}, "
            f"skipped={self._changes_skipped})"
        )
