"""
FeedbackManager — 反馈管理器（简化调度器）

职责:
  - 持有唯一的 EventBus 订阅
  - 管理 worker 生命周期
  - 并发分发数据给所有 worker
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from typing import Optional

from scope.model.enums import SlotStatus
from scope.runtime import EventBus
from scope.runtime import FeedbackStatusSnapshot, FeedbackWorkerStatus
from scope.runtime.pid_controller import PidConfig
from .feedback_worker import FeedbackConfig, FeedbackWorker

logger = logging.getLogger(__name__)


class FeedbackManager:
    """反馈管理器 — 数据分发 + 生命周期管理"""

    def __init__(self, event_bus: Optional[EventBus] = None):
        self._event_bus = event_bus
        self._workers: dict[str, FeedbackWorker] = {}
        self._queue = None
        self._lock = asyncio.Lock()
        self._running = False
        self._dispatch_task = None

    # ── 生命周期 ───────────────────────────────────────────────

    async def start(self):
        """启动管理器（开始分发协程）"""
        if self._running:
            return

        self._running = True
        if self._event_bus:
            self._queue = self._event_bus.subscribe("frame.fitted")
        self._dispatch_task = asyncio.create_task(self._dispatch_loop())
        logger.info("FeedbackManager started")

    async def stop(self):
        """停止管理器"""
        self._running = False
        await self.stop_all_workers()
        if self._dispatch_task:
            self._dispatch_task.cancel()
            self._dispatch_task = None
        logger.info("FeedbackManager stopped")

    # ── Worker 管理 ───────────────────────────────────────────

    async def add_worker(
        self,
        config: FeedbackConfig,
        publish: bool = True,
    ) -> str:
        """添加反馈 worker"""
        if config.worker_id in self._workers:
            raise KeyError(f'worker_id "{config.worker_id}" already exists')

        worker = FeedbackWorker(config)

        async with self._lock:
            self._workers[config.worker_id] = worker

        await worker.start()
        logger.info(f'FeedbackWorker "{config.worker_id}" added (measurement={config.measurement_key})')
        if publish:
            self._publish_status()
        return config.worker_id

    async def remove_worker(self, worker_id: str) -> Optional[FeedbackWorker]:
        """移除反馈 worker"""
        async with self._lock:
            worker = self._workers.pop(worker_id, None)

        if worker:
            await worker.stop()
            logger.info(f'FeedbackWorker "{worker_id}" removed')
            self._publish_status()
        return worker

    async def pause_worker(self, worker_id: str):
        """暂停指定 worker"""
        async with self._lock:
            worker = self._workers.get(worker_id)
        if worker:
            await worker.pause()
            self._publish_status()

    async def resume_worker(self, worker_id: str):
        """恢复指定 worker"""
        async with self._lock:
            worker = self._workers.get(worker_id)
        if worker:
            await worker.resume()
            self._publish_status()

    async def stop_all_workers(self):
        """停止所有 worker"""
        async with self._lock:
            workers = list(self._workers.values())
        for worker in workers:
            await worker.stop()

    async def update_worker_pid(self, worker_id: str, pid_config: PidConfig):
        """更新指定 worker 的 PID 参数"""
        async with self._lock:
            worker = self._workers.get(worker_id)
        if worker:
            worker.update_pid_config(pid_config)
            self._publish_status()
        else:
            raise KeyError(f'worker_id "{worker_id}" not found')

    # ── 配置管理 ───────────────────────────────────────────────

    def get_config(self) -> list[dict]:
        """导出所有 worker 配置（用于保存）"""
        return [
            {
                "worker_id": w.worker_id,
                "measurement_key": w.measurement_key,
                "pid_config": dataclasses.asdict(w.pid_config),
                "target": None,  # v0.7 实现
            }
            for w in self._workers.values()
        ]

    async def load_config(self, config_list: list[dict]):
        """加载配置（重建所有 worker）"""
        # 清空现有 worker
        await self.stop_all_workers()
        async with self._lock:
            self._workers.clear()

        # 重新创建
        for item in config_list:
            pid_config = PidConfig(**item["pid_config"])
            worker_config = FeedbackConfig(
                worker_id=item["worker_id"],
                measurement_key=item["measurement_key"],
                pid_config=pid_config,
                target=None,
            )
            await self.add_worker(worker_config, publish=False)

        self._publish_status()

    # ── 数据分发 ───────────────────────────────────────────────

    async def _dispatch_loop(self):
        """分发协程：订阅 → 提取 → 并发分发"""
        if not self._queue:
            logger.warning("FeedbackManager dispatch loop: no queue (event_bus not set)")
            return

        while self._running:
            try:
                snapshot = self._queue.get_nowait()
                if snapshot is not None:
                    # 只调用一次 as_flat_dict()
                    flat = snapshot.as_flat_dict()

                    # 并发分发给所有 worker
                    tasks = []
                    async with self._lock:
                        for worker in self._workers.values():
                            if worker.status == SlotStatus.RUNNING:
                                value = flat.get(worker.measurement_key)
                                if value is not None:
                                    tasks.append(worker.process(value))

                    if tasks:
                        await asyncio.gather(*tasks, return_exceptions=True)

                    self._publish_status()
                    await asyncio.sleep(0)  # 有数据 → 让出控制权，不延后批处理
                else:
                    await asyncio.sleep(0.01)  # 空队列 → 休眠 10ms 避免忙等

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"FeedbackManager dispatch error: {e}")
                await asyncio.sleep(0.1)

    # ── 状态发布 ───────────────────────────────────────────────

    def _publish_status(self):
        """发布当前 worker 状态快照到 feedback.status topic。"""
        if not self._event_bus:
            return
        snapshot = self._build_status_snapshot()
        self._event_bus.publish("feedback.status", snapshot)

    def _build_status_snapshot(self) -> FeedbackStatusSnapshot:
        """构建当前全部 worker 状态快照。"""
        workers = list(self._workers.values())
        worker_statuses = []
        running_count = 0
        for w in workers:
            if w.status == SlotStatus.RUNNING:
                running_count += 1
            worker_statuses.append(FeedbackWorkerStatus(
                worker_id=w.worker_id,
                status=w.status.value,
                measurement_key=w.measurement_key,
                last_value=w.last_value,
                last_error=w.last_error,
                errors_std=w._pid.errors_std,
                errors_count=w._pid.errors_count,
                frames_processed=w.frames_processed,
                preset_value=w.pid_config.preset_value,
                deadband=w.pid_config.deadband,
                kp=w.pid_config.kp,
                ki=w.pid_config.ki,
                kd=w.pid_config.kd,
                output_limit=w.pid_config.output_limit,
                i_limit=w.pid_config.i_limit,
                window_size=w.pid_config.window_size,
            ))
        return FeedbackStatusSnapshot(
            workers=worker_statuses,
            running_count=running_count,
            total_count=len(workers),
        )

    # ── 状态查询 ───────────────────────────────────────────────

    def list_workers(self) -> list[dict]:
        """列出所有 worker 状态（含运行时数据）"""
        return [
            {
                "worker_id": w.worker_id,
                "status": w.status.value,
                "measurement_key": w.measurement_key,
                "preset_value": w.pid_config.preset_value,
                "deadband": w.pid_config.deadband,
                "last_value": w.last_value,
                "last_error": w.last_error,
                "errors_std": w._pid.errors_std,
                "errors_count": w._pid.errors_count,
                "frames_processed": w.frames_processed,
                "kp": w.pid_config.kp,
                "ki": w.pid_config.ki,
                "kd": w.pid_config.kd,
                "output_limit": w.pid_config.output_limit,
                "i_limit": w.pid_config.i_limit,
                "window_size": w.pid_config.window_size,
            }
            for w in self._workers.values()
        ]

    def get_active_count(self) -> tuple[int, int]:
        """返回 (running_count, total_count)"""
        running = sum(
            1 for w in self._workers.values()
            if w.status == SlotStatus.RUNNING
        )
        return running, len(self._workers)
