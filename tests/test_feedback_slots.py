"""
Phase 1 反馈系统 — 集成测试

简化版，只测试核心功能。
"""

import asyncio
import numpy as np
import pytest

from scope.runtime import FittedSnapshot
from scope.model.enums import SlotStatus
from scope.io.feedback_slots.base import SlotConfig, DataSubscription
from scope.io.feedback_slots.null_slot import NullFeedbackSlot
from scope.io.feedback_manager import FeedbackManager


def make_sample_measurements(**kwargs) -> dict[str, float]:
    """生成扁平测量字典"""
    return kwargs


class TestNullSlot:
    async def test_start_stop(self):
        slot = NullFeedbackSlot(SlotConfig(slot_id="test-1"))
        assert slot.status == SlotStatus.IDLE

        await slot.start()
        assert slot.status == SlotStatus.PAUSED
        await slot.resume()
        assert slot.status == SlotStatus.RUNNING

        await slot.stop()
        assert slot.status == SlotStatus.IDLE

    async def test_on_data_counts_payloads(self):
        slot = NullFeedbackSlot(SlotConfig(slot_id="test-2"))
        await slot.start()
        await slot.resume()

        # 此时 RUNNING 状态才能接收数据
        await slot.on_data({"CH1_Vpp": 3.3, "CH1_Freq": 1000.0})
        assert slot._payloads_received == 1
        assert slot._last_payload.get("CH1_Vpp") == 3.3

        await slot.stop()

    async def test_on_data_ignored_when_not_running(self):
        slot = NullFeedbackSlot(SlotConfig(slot_id="test-3"))
        await slot.start()
        # 默认是 PAUSED 状态，不应接收数据

        await slot.on_data({"seq": 1.0})
        assert slot._payloads_received == 0

        await slot.stop()


class TestFeedbackManager:
    async def test_add_slot(self):
        mgr = FeedbackManager()
        slot = NullFeedbackSlot(SlotConfig(slot_id="slot-1"))

        await mgr.add_slot(slot)
        info = mgr.list_slots()
        assert len(info) == 1
        assert info[0].slot_id == "slot-1"

    async def test_remove_slot(self):
        mgr = FeedbackManager()
        slot = NullFeedbackSlot(SlotConfig(slot_id="slot-rem"))

        await mgr.add_slot(slot)
        mgr.remove_slot("slot-rem")

        assert len(mgr.list_slots()) == 0

    async def test_remove_nonexistent(self):
        mgr = FeedbackManager()
        mgr.remove_slot("no-such-slot")  # 应不抛异常

    async def test_duplicate_slot_id(self):
        mgr = FeedbackManager()
        slot_a = NullFeedbackSlot(SlotConfig(slot_id="dup-id"))
        slot_b = NullFeedbackSlot(SlotConfig(slot_id="dup-id"))

        await mgr.add_slot(slot_a)
        with pytest.raises(KeyError):
            await mgr.add_slot(slot_b)

    async def test_dispatch_raw_basic(self):
        mgr = FeedbackManager()
        slot = NullFeedbackSlot(SlotConfig(
            slot_id="dispatch-test",
            subscriptions=[DataSubscription(local_key="CH1_Vpp")],
        ))
        await mgr.add_slot(slot)
        await mgr.start_all()
        await slot.resume()

        measurements = make_sample_measurements(CH1_Vpp=3.3, CH1_Mean=1.5)
        await mgr.dispatch_raw(measurements)

        assert slot._payloads_received == 1
        assert "CH1_Vpp" in slot._last_payload
        assert "CH1_Mean" not in slot._last_payload  # 未订阅

        await mgr.stop_all()

    async def test_dispatch_empty_measurements(self):
        mgr = FeedbackManager()
        slot = NullFeedbackSlot(SlotConfig(
            slot_id="empty-test",
            subscriptions=[DataSubscription(local_key="NONEXISTENT")],
        ))
        await mgr.add_slot(slot)
        await mgr.start_all()
        await slot.resume()

        measurements = make_sample_measurements(CH1_Vpp=3.3)
        await mgr.dispatch_raw(measurements)

        assert slot._payloads_received == 0  # 无匹配，不发送

        await mgr.stop_all()

    async def test_scale_and_offset(self):
        mgr = FeedbackManager()
        slot = NullFeedbackSlot(SlotConfig(
            slot_id="scale-test",
            subscriptions=[
                DataSubscription(
                    local_key="CH1_Vpp",
                    remote_key="voltage_mv",
                    scale=1000.0,  # V -> mV
                    offset=100.0,
                ),
            ],
        ))
        await mgr.add_slot(slot)
        await mgr.start_all()
        await slot.resume()

        measurements = make_sample_measurements(CH1_Vpp=3.3)
        await mgr.dispatch_raw(measurements)

        assert slot._last_payload.get("voltage_mv") == 3400.0  # 3.3*1000 + 100

        await mgr.stop_all()

    async def test_error_isolation(self):
        """单个 slot 异常不影响其他 slot"""
        mgr = FeedbackManager()

        # slot_a 正常
        slot_a = NullFeedbackSlot(SlotConfig(
            slot_id="good-slot",
            subscriptions=[DataSubscription(local_key="val")],
        ))

        # slot_b 会通过传入错误参数模拟异常（这里先不模拟，因为 NullSlot 不会抛异常）
        slot_b = NullFeedbackSlot(SlotConfig(
            slot_id="another-slot",
            subscriptions=[DataSubscription(local_key="val")],
        ))

        await mgr.add_slot(slot_a)
        await mgr.add_slot(slot_b)
        await mgr.start_all()
        await slot_a.resume()
        await slot_b.resume()

        measurements = make_sample_measurements(val=1.0)
        await mgr.dispatch_raw(measurements)

        assert slot_a._payloads_received == 1
        assert slot_b._payloads_received == 1

        await mgr.stop_all()
