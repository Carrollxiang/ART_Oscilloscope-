"""
FeedbackManager — 反馈调度器核心

职责:
  1. 管理所有 FeedbackSlot 的生命周期 (增删改查)
  2. 在每次采集完成后, 提取订阅数据并向所有活跃 slot 并发分发
  3. 保持运行时动态操作的能力 (add/remove/reconfigure 不阻塞采集)

关键设计:
  - dispatch() 并发执行所有 active slot 的 on_data()
  - 单个 slot 的失败不影响其他 slot
  - add/remove 操作线程安全, 可在 dispatch 间隙调用
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional, Union

from scope.model import AnalysisResult
from scope.model.enums import SlotStatus
from scope.runtime import MeasurementSnapshot
from .feedback_slots.base import (
    FeedbackSlot,
    SlotConfig,
    DataSubscription,
    SlotInfo,
)

logger = logging.getLogger(__name__)


class FeedbackManager:
    """
    反馈调度器。

    用法:
        mgr = FeedbackManager()
        mgr.add_slot(slot_a)
        mgr.add_slot(slot_b)
        await mgr.start_all()

        # 每次采集完成后
        await mgr.dispatch(analysis_result)

        # 运行时动态操作
        mgr.remove_slot("slot_a")
        mgr.add_slot(slot_c)

        # 停止
        await mgr.stop_all()
    """

    def __init__(self):
        self._slots: dict[str, FeedbackSlot] = {}
        self._lock = asyncio.Lock()

    # ── 生命周期管理 ───────────────────────────────────────────

    async def start_all(self):
        """启动所有已注册的 slot"""
        async with self._lock:
            tasks = []
            for slot in self._slots.values():
                if slot.status == SlotStatus.IDLE:
                    tasks.append(self._safe_start(slot))
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    async def stop_all(self):
        """停止所有 slot"""
        async with self._lock:
            tasks = [
                self._safe_stop(slot)
                for slot in self._slots.values()
                if slot.status != SlotStatus.IDLE
            ]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    # ── 增删改查 ───────────────────────────────────────────────

    async def add_slot(self, slot: FeedbackSlot, auto_start: bool = True) -> str:
        """
        注册一个新 slot。

        auto_start=True 时立即启动。
        返回 slot_id。
        """
        if slot.slot_id in self._slots:
            raise KeyError(f'slot_id "{slot.slot_id}" 已存在')

        async with self._lock:
            self._slots[slot.slot_id] = slot

        if auto_start:
            await self._safe_start(slot)

        active, total = self._count_status()
        logger.info(
            f'FeedbackSlot "{slot.slot_id}" added '
            f"(protocol={slot.protocol}, target={slot._get_target()}) "
            f"[active={active}/{total}]"
        )
        return slot.slot_id

    def remove_slot(self, slot_id: str) -> Optional[FeedbackSlot]:
        """
        从管理器中移除 slot。

        如果 slot 正在运行会先停止它。
        返回被移除的 slot 对象。
        """
        slot = self._slots.get(slot_id)
        if not slot:
            logger.warning(f'remove_slot: "{slot_id}" 不存在')
            return None

        # 先停止
        if slot.status != SlotStatus.IDLE:
            try:
                # 已有运行中 loop (如 pytest-asyncio) → 调度但不等待
                loop = asyncio.get_running_loop()
                asyncio.ensure_future(self._safe_stop(slot))
            except RuntimeError:
                # UI 线程无 loop → 创建临时 loop 同步执行
                asyncio.run(self._safe_stop(slot))

        # 从字典移除 (新 dispatch 不会再包含此 slot)
        del self._slots[slot_id]

        active, total = self._count_status()
        logger.info(
            f'FeedbackSlot "{slot_id}" removed '
            f"[active={active}/{total}]"
        )
        return slot

    def get_slot(self, slot_id: str) -> Optional[FeedbackSlot]:
        return self._slots.get(slot_id)

    def list_slots(self) -> list[SlotInfo]:
        """返回所有 slot 的运行快照, 用于 UI 显示"""
        return [s.get_info() for s in self._slots.values()]

    def list_slots_summary(self) -> list[dict]:
        """轻量摘要, 用于日志"""
        return [
            {
                "id": s.slot_id,
                "protocol": s.protocol,
                "status": s.status.value,
                "target": s._get_target(),
            }
            for s in self._slots.values()
        ]

    # ── v0.4: dispatch_raw (从扁平 dict 分发) ──────────────

    async def dispatch_raw(self, measurements: dict[str, float]):
        """
        将扁平测量字典分发给所有活跃 slot（v0.4 新路径）。

        measurements 来自 FittedSnapshot.as_flat_dict()，包含
        channel_measurements + event_measurements 的合并结果。
        """
        if not self._slots:
            return

        active_slots = [
            s for s in list(self._slots.values())
            if s.status == SlotStatus.RUNNING
        ]
        if not active_slots:
            return

        tasks = []
        for slot in active_slots:
            payload = self._build_payload_from_dict(
                measurements, slot._config.subscriptions
            )
            if payload:
                tasks.append(self._safe_on_data(slot, payload))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _build_payload_from_dict(
        self,
        measurements: dict[str, float],
        subscriptions: list[DataSubscription],
    ) -> dict[str, Any]:
        """从扁平测量字典中根据订阅列表提取数据。"""
        payload: dict[str, Any] = {}
        for sub in subscriptions:
            value = self._resolve_value_from_dict(measurements, sub.local_key)
            if value is not None:
                scaled = value * sub.scale + sub.offset
                payload[sub.remote_key] = scaled
        return payload

    def _resolve_value_from_dict(
        self, measurements: dict[str, float], key: str
    ) -> Optional[float]:
        """
        从扁平测量字典中解析单个值。

        支持结构化 key:
          - "event:tag"   → 直查 measurements
          - "raw:CH1_Vpp" → 直查 measurements
          - "meta:seq"    → None (元信息不在此处)
          - "CH1_Vpp"     → 直查 measurements (兼容旧订阅)
        """
        if key.startswith("event:"):
            return measurements.get(key[6:])
        elif key.startswith("raw:"):
            return measurements.get(key[4:])
        elif key.startswith("meta:"):
            return None
        else:
            return measurements.get(key)

    # ── 核心: 数据分发 ─────────────────────────────────────────

    async def dispatch(self, result: Union[AnalysisResult, MeasurementSnapshot]):
        """
        将一次采集结果分发给所有活跃 slot。

        每个 slot 只收到它订阅的数据项。
        全部 slot 并发发送, 互不阻塞。
        """
        if not self._slots:
            return

        # 快照当前 slot 列表 (避免迭代中增删问题)
        active_slots = [
            s for s in list(self._slots.values())
            if s.status == SlotStatus.RUNNING
        ]
        if not active_slots:
            return

        tasks = []
        for slot in active_slots:
            payload = self._build_payload(result, slot._config.subscriptions)
            if payload:
                tasks.append(self._safe_on_data(slot, payload))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # ── 内部方法 ───────────────────────────────────────────────

    def _build_payload(
        self,
        result: Union[AnalysisResult, MeasurementSnapshot],
        subscriptions: list[DataSubscription],
    ) -> dict[str, Any]:
        """
        根据订阅列表从数据源中提取数据。

        按 remote_key 组织, 每个 key 对应一个 float 值。
        """
        payload: dict[str, Any] = {}
        for sub in subscriptions:
            value = self._resolve_value(result, sub.local_key)
            if value is not None:
                # 应用缩放和偏移
                scaled = value * sub.scale + sub.offset
                payload[sub.remote_key] = scaled
        return payload

    def _resolve_value(
        self, result: Union[AnalysisResult, MeasurementSnapshot], key: str
    ) -> Optional[float]:
        """
        从 AnalysisResult 或 MeasurementSnapshot 中解析单个测量值。

        支持 key 格式:
          - "CH1_Vpp"       → result.measurements["CH1_Vpp"]
          - "CH1_Freq"      → result.measurements["CH1_Freq"]
          - "sequence_num"  → result.sequence_num (元信息)
        """
        # v0.4: 优先走 MeasurementSnapshot
        if isinstance(result, MeasurementSnapshot):
            return result.get(key)

        # 兼容旧路径: AnalysisResult.measurements
        if key in result.measurements:
            return result.measurements[key]

        # 元信息
        if key == "sequence_num":
            return float(result.sequence_num)

        return None

    async def _safe_start(self, slot: FeedbackSlot):
        """安全启动, 捕获异常"""
        try:
            await slot.start()
        except Exception as e:
            logger.error(f'slot "{slot.slot_id}" start failed: {e}')

    async def _safe_stop(self, slot: FeedbackSlot):
        """安全停止, 捕获异常"""
        try:
            await slot.stop()
        except Exception as e:
            logger.error(f'slot "{slot.slot_id}" stop failed: {e}')

    async def _safe_on_data(self, slot: FeedbackSlot, payload: dict):
        """安全发送, 捕获异常"""
        try:
            await slot.on_data(payload)
        except Exception as e:
            logger.error(
                f'slot "{slot.slot_id}" on_data failed: {e}'
            )

    def _count_status(self) -> tuple[int, int]:
        """返回 (running_count, total_count)"""
        running = sum(
            1 for s in self._slots.values()
            if s.status == SlotStatus.RUNNING
        )
        return running, len(self._slots)
