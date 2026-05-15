"""
Phase 1 反馈系统 — 集成测试

注意: 部分测试需要 rpyc 库。如未安装, 跳过 rpyc 相关测试。
"""

import asyncio
import time

import numpy as np
import pytest

from scope.model import AnalysisResult, ChannelData, TriggerInfo
from scope.model.enums import SlotStatus
from scope.io.feedback_slots.base import SlotConfig, DataSubscription
from scope.io.feedback_slots.null_slot import NullFeedbackSlot
from scope.io.feedback_manager import FeedbackManager


# ── Helper ─────────────────────────────────────────────────────

def make_sample_result(**meas) -> AnalysisResult:
    """生成一个带测量值的测试用 AnalysisResult"""
    t = np.linspace(0, 0.01, 1000)
    ch1 = ChannelData(
        raw=np.sin(2 * np.pi * 1000 * t),
        time_axis=t,
        sample_rate=100_000,
        resolution=12,
        vertical_scale=1.0,
        vertical_offset=0.0,
    )
    return AnalysisResult(
        sequence_num=1,
        trigger=TriggerInfo.immediate(),
        channels={"CH1": ch1},
        measurements=meas,
    )


# ── FeedbackSlot: NullSlot ─────────────────────────────────────

class TestNullSlot:
    async def test_start_stop(self):
        slot = NullFeedbackSlot(SlotConfig(slot_id="test-1"))
        assert slot.status == SlotStatus.IDLE

        await slot.start()
        assert slot.status == SlotStatus.PAUSED  # 默认暂停
        await slot.resume()
        assert slot.status == SlotStatus.RUNNING

        await slot.stop()
        assert slot.status == SlotStatus.IDLE

    async def test_on_data_records_payload(self):
        slot = NullFeedbackSlot(SlotConfig(slot_id="test-2"))
        await slot.start()

        await slot.on_data({"CH1_Vpp": 3.3, "CH1_Freq": 1000.0})
        assert len(slot.history) == 1
        assert slot.history[0]["CH1_Vpp"] == 3.3

        await slot.stop()

    async def test_on_data_multiple_frames(self):
        slot = NullFeedbackSlot(SlotConfig(slot_id="test-3"))
        await slot.start()

        for i in range(10):
            await slot.on_data({"seq": float(i)})

        assert len(slot.history) == 10
        assert slot.get_info().sent_count == 10

        await slot.stop()


# ── Subscription ───────────────────────────────────────────────

class TestSubscription:
    async def test_key_resolution(self):
        """订阅的 key 应从 AnalysisResult.measurements 中正确提取"""
        mgr = FeedbackManager()
        slot = NullFeedbackSlot(SlotConfig(
            slot_id="sub-test",
            subscriptions=[DataSubscription(local_key="CH1_Vpp")],
        ))
        await mgr.add_slot(slot)
        await slot.resume()

        result = make_sample_result(CH1_Vpp=3.3, CH1_Freq=1000.0)
        await mgr.dispatch(result)

        # NullSlot 应该只收到订阅的 CH1_Vpp, 没有 CH1_Freq
        assert len(slot.history) == 1
        assert "CH1_Vpp" in slot.history[0]
        assert "CH1_Freq" not in slot.history[0]

        await mgr.stop_all()

    async def test_key_not_found(self):
        """订阅的 key 不存在时应被跳过"""
        mgr = FeedbackManager()
        slot = NullFeedbackSlot(SlotConfig(
            slot_id="missing-key",
            subscriptions=[DataSubscription(local_key="NONEXISTENT")],
        ))
        await mgr.add_slot(slot)
        await slot.resume()

        result = make_sample_result(CH1_Vpp=3.3)
        await mgr.dispatch(result)

        # 空的 payload 不应触发发送
        assert len(slot.history) == 0
        await mgr.stop_all()

    async def test_remote_key_mapping(self):
        """local_key 和 remote_key 不同时应正确映射"""
        mgr = FeedbackManager()
        slot = NullFeedbackSlot(SlotConfig(
            slot_id="key-map",
            subscriptions=[
                DataSubscription(
                    local_key="CH1_Vpp",
                    remote_key="voltage_peak",
                    scale=1000.0,  # V → mV
                ),
            ],
        ))
        await mgr.add_slot(slot)
        await slot.resume()

        result = make_sample_result(CH1_Vpp=3.3)
        await mgr.dispatch(result)

        assert len(slot.history) == 1
        assert "voltage_peak" in slot.history[0]
        # 3.3 * 1000 = 3300
        assert slot.history[0]["voltage_peak"] == 3300.0

        await mgr.stop_all()

    async def test_sequence_num_subscription(self):
        """元信息 key (sequence_num) 应可订阅"""
        mgr = FeedbackManager()
        slot = NullFeedbackSlot(SlotConfig(
            slot_id="seq-test",
            subscriptions=[DataSubscription(local_key="sequence_num")],
        ))
        await mgr.add_slot(slot)
        await slot.resume()

        result = make_sample_result(CH1_Vpp=1.0)
        await mgr.dispatch(result)

        assert len(slot.history) == 1
        assert slot.history[0]["sequence_num"] == 1.0

        await mgr.stop_all()


# ── FeedbackManager ────────────────────────────────────────────

class TestFeedbackManager:
    async def test_dispatch_basic(self):
        """基本分发: 数据正确到达 NullSlot"""
        mgr = FeedbackManager()
        slot = NullFeedbackSlot(SlotConfig(
            slot_id="dispatch-basic",
            subscriptions=[DataSubscription(local_key="CH1_Vpp")],
        ))
        await mgr.add_slot(slot)
        await slot.resume()

        for i in range(5):
            result = make_sample_result(CH1_Vpp=float(i))
            await mgr.dispatch(result)

        assert len(slot.history) == 5
        assert [h["CH1_Vpp"] for h in slot.history] == [0.0, 1.0, 2.0, 3.0, 4.0]

        await mgr.stop_all()

    async def test_dispatch_empty_result(self):
        """空的 AnalysisResult (无 measurements) 不应出错"""
        mgr = FeedbackManager()
        slot = NullFeedbackSlot(SlotConfig(
            slot_id="empty-test",
            subscriptions=[DataSubscription(local_key="CH1_Vpp")],
        ))
        await mgr.add_slot(slot)
        await slot.resume()

        result = make_sample_result()  # 无测量值
        await mgr.dispatch(result)

        assert len(slot.history) == 0

        await mgr.stop_all()

    async def test_dynamic_add_mid_stream(self):
        """
        运行时添加 slot → 新 slot 从下一帧开始接收, 不影响已有 slot。
        这是 LabVIEW 版做不到的核心能力。
        """
        mgr = FeedbackManager()
        slot_a = NullFeedbackSlot(SlotConfig(
            slot_id="slot-a",
            subscriptions=[DataSubscription(local_key="val")],
        ))
        await mgr.add_slot(slot_a)
        await slot_a.resume()

        # 前 3 帧只有 slot_a
        for i in range(3):
            result = make_sample_result(val=float(i))
            await mgr.dispatch(result)

        # 中间添加 slot_b
        slot_b = NullFeedbackSlot(SlotConfig(
            slot_id="slot-b",
            subscriptions=[DataSubscription(local_key="val")],
        ))
        await mgr.add_slot(slot_b)
        await slot_b.resume()

        # 后 3 帧两个 slot 都收
        for i in range(3, 6):
            result = make_sample_result(val=float(i))
            await mgr.dispatch(result)

        assert len(slot_a.history) == 6
        assert [h["val"] for h in slot_a.history] == [0, 1, 2, 3, 4, 5]

        assert len(slot_b.history) == 3
        assert [h["val"] for h in slot_b.history] == [3, 4, 5]

        await mgr.stop_all()

    async def test_dynamic_remove_mid_stream(self):
        """
        运行时删除 slot → 已删除的 slot 不再收到数据, 不影响其他 slot。
        """
        mgr = FeedbackManager()
        slot_a = NullFeedbackSlot(SlotConfig(
            slot_id="slot-a",
            subscriptions=[DataSubscription(local_key="val")],
        ))
        slot_b = NullFeedbackSlot(SlotConfig(
            slot_id="slot-b",
            subscriptions=[DataSubscription(local_key="val")],
        ))
        await mgr.add_slot(slot_a)
        await mgr.add_slot(slot_b)
        await slot_a.resume()
        await slot_b.resume()

        # 前 3 帧: 两个都收
        for i in range(3):
            await mgr.dispatch(make_sample_result(val=float(i)))

        # 移除 slot_a
        mgr.remove_slot("slot-a")

        # 后 3 帧: 只有 slot_b
        for i in range(3, 6):
            await mgr.dispatch(make_sample_result(val=float(i)))

        assert len(slot_a.history) == 3
        assert len(slot_b.history) == 6

        await mgr.stop_all()

    async def test_remove_nonexistent(self):
        """删除不存在的 slot 应安全返回 None"""
        mgr = FeedbackManager()
        assert mgr.remove_slot("ghost") is None

    async def test_duplicate_slot_id(self):
        """重复的 slot_id 应报错"""
        mgr = FeedbackManager()
        slot_a = NullFeedbackSlot(SlotConfig(slot_id="dup"))
        slot_b = NullFeedbackSlot(SlotConfig(slot_id="dup"))

        await mgr.add_slot(slot_a)
        with pytest.raises(KeyError):
            await mgr.add_slot(slot_b)

        await mgr.stop_all()

    async def test_error_isolation(self):
        """
        一个 slot 出错不影响其他 slot。
        通过一个 on_data 会抛异常的 slot 验证。
        """
        class CrashSlot(NullFeedbackSlot):
            async def on_data(self, payload):
                raise RuntimeError("模拟崩溃")

        mgr = FeedbackManager()
        good = NullFeedbackSlot(SlotConfig(
            slot_id="good",
            subscriptions=[DataSubscription(local_key="val")],
        ))
        bad = CrashSlot(SlotConfig(
            slot_id="bad",
            subscriptions=[DataSubscription(local_key="val")],
        ))
        await mgr.add_slot(good)
        await mgr.add_slot(bad)
        await good.resume()
        await bad.resume()

        # 即使 bad 崩溃, good 应该正常收
        for i in range(5):
            await mgr.dispatch(make_sample_result(val=float(i)))

        assert len(good.history) == 5
        await mgr.stop_all()

    async def test_multi_slot_concurrent(self):
        """多个 slot 同时运行, 各自收到完整数据"""
        mgr = FeedbackManager()
        slots = []
        for i in range(5):
            s = NullFeedbackSlot(SlotConfig(
                slot_id=f"concurrent-{i}",
                subscriptions=[DataSubscription(local_key="val")],
            ))
            slots.append(s)
            await mgr.add_slot(s)
        for s in slots:
            await s.resume()

        for i in range(10):
            await mgr.dispatch(make_sample_result(val=float(i)))

        for s in slots:
            assert len(s.history) == 10

        await mgr.stop_all()

    async def test_list_slots(self):
        """list_slots 返回正确信息"""
        mgr = FeedbackManager()
        slot = NullFeedbackSlot(SlotConfig(
            slot_id="info-test",
            label="测试插槽",
            subscriptions=[DataSubscription(local_key="CH1_Vpp")],
        ))
        await mgr.add_slot(slot)
        await slot.resume()
        await mgr.dispatch(make_sample_result(CH1_Vpp=1.0))

        infos = mgr.list_slots()
        assert len(infos) == 1
        info = infos[0]
        assert info.slot_id == "info-test"
        assert info.protocol == "null"
        assert info.sent_count == 1

        await mgr.stop_all()


# ── RpycConnectionPool (无远程服务器) ──────────────────────────

class TestRpycConnectionPool:
    """连接池单元测试 (不依赖真实 rpyc 服务)"""

    def test_init_params(self):
        from scope.io.feedback_slots.rpyc_pool import RpycConnectionPool

        pool = RpycConnectionPool("127.0.0.1", 18861, max_size=2)
        assert pool._host == "127.0.0.1"
        assert pool._port == 18861
        assert pool._max == 2

        # 关闭后不应有遗留
        pool.close()
        assert pool._closed

    def test_acquire_timeout_on_dead_server(self):
        """连接不存在的服务器应超时报错"""
        from scope.io.feedback_slots.rpyc_pool import RpycConnectionPool

        pool = RpycConnectionPool(
            "127.0.0.1", 19999,
            max_size=1,
            connect_timeout=1.0,
            acquire_timeout=1.0,
        )

        with pytest.raises(ConnectionError):
            pool.acquire()

        pool.close()


# ── 完整集成: Simulator → FeedbackManager → NullSlot ──────────

class TestSimulatorToFeedbackIntegration:
    """
    从 SimulatorDevice 产生数据, 经过 Pipeline (此处简化为直接
    填充 measurements), 通过 FeedbackManager 分发到 NullSlot。
    """

    async def test_full_flow(self):
        from scope.hardware.simulator import SimulatorDevice
        from scope.hardware import DeviceConfig

        device = SimulatorDevice()
        config = DeviceConfig(sample_rate=100_000, record_length=1000)
        device.open()
        device.configure(config)
        device.start_acquisition()

        mgr = FeedbackManager()
        slot = NullFeedbackSlot(SlotConfig(
            slot_id="integration-test",
            subscriptions=[
                DataSubscription(local_key="CH1_Vpp"),
                DataSubscription(local_key="sequence_num"),
            ],
        ))
        await mgr.add_slot(slot)
        await slot.resume()

        # 模拟 5 帧采集 → 分析 → 反馈 全流程
        for _ in range(5):
            chunk = device.read_chunk()
            result = device.make_analysis_result(chunk)
            # 模拟 Pipeline 填充 measurements
            result.measurements["CH1_Vpp"] = float(np.ptp(chunk[0]))
            result.measurements["CH1_Freq"] = 1000.0

            await mgr.dispatch(result)

        assert len(slot.history) == 5
        for entry in slot.history:
            assert "CH1_Vpp" in entry
            assert "sequence_num" in entry
            # 正弦波 2Vpp → Vpp ≈ 2.0
            assert abs(entry["CH1_Vpp"] - 2.0) < 0.01

        device.stop_acquisition()
        device.close()
        await mgr.stop_all()
