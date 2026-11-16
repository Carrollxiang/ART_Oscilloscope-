"""
比值测量 (Ratio) — MeasurementSpec / MeasurementProcessor / FittedSnapshot 测试

覆盖: 比值规格校验、同帧相除、源缺失、分母保护 (除零/近零)、
      禁止 ratio 引用 ratio、非 ratio 回归、invalid_reasons 语义。
"""

import numpy as np
import pytest

from scope.model import RawFrame
from scope.runtime import EventBus, FittedSnapshot, MeasurementSpec
from scope.runtime.measurement_processor import (
    RATIO_MIN_DENOM_ABS,
    MeasurementProcessor,
)


# ── 辅助 ─────────────────────────────────────────────────────────

def make_frame(ch0_vpp: float = 2.0, ch1_vpp: float = 1.0,
               n_samples: int = 100, sample_rate: float = 1000.0) -> RawFrame:
    """构造两通道方波帧: 每通道前一半为 Vpp, 后一半为 0, 因此 ptp == vpp。"""
    data = np.zeros((2, n_samples), dtype=np.float32)
    data[0, : n_samples // 2] = ch0_vpp
    data[1, : n_samples // 2] = ch1_vpp
    return RawFrame(sequence_num=1, data=data, sample_rate=sample_rate)


def run_processor(specs: list[MeasurementSpec], frame: RawFrame) -> FittedSnapshot:
    """跑一帧并返回产出的 FittedSnapshot。"""
    bus = EventBus()
    bus.register_topic("frame.raw")
    bus.register_topic("frame.fitted")
    queue = bus.subscribe("frame.fitted")
    processor = MeasurementProcessor(bus, specs=specs)
    processor._process_frame(frame)
    snap = queue.get_nowait()
    assert snap is not None, "MeasurementProcessor 未发布 frame.fitted"
    return snap


def scalar(tag: str, channel: int, feature: str = "Vpp") -> MeasurementSpec:
    return MeasurementSpec(tag=tag, channel=channel, feature=feature)


def ratio(tag: str, num: str, den: str) -> MeasurementSpec:
    return MeasurementSpec(
        tag=tag, feature="Ratio", numerator_tag=num, denominator_tag=den,
    )


# ── MeasurementSpec ──────────────────────────────────────────────

class TestRatioSpec:
    def test_valid_ratio_spec(self):
        spec = ratio("r1", "CH1_vpp", "CH2_vpp")
        assert spec.is_ratio()
        assert spec.numerator_tag == "CH1_vpp"
        assert spec.denominator_tag == "CH2_vpp"
        assert spec.channel == 0

    def test_is_ratio_case_insensitive(self):
        spec = MeasurementSpec(
            tag="r1", feature="RATIO",
            numerator_tag="a", denominator_tag="b",
        )
        assert spec.is_ratio()

    def test_scalar_spec_unaffected(self):
        spec = scalar("CH1_vpp", 0)
        assert not spec.is_ratio()
        assert spec.numerator_tag == ""
        assert spec.denominator_tag == ""

    def test_missing_numerator(self):
        with pytest.raises(ValueError, match="分子"):
            MeasurementSpec(tag="r1", feature="Ratio", denominator_tag="CH2_vpp")

    def test_missing_denominator(self):
        with pytest.raises(ValueError, match="分母"):
            MeasurementSpec(tag="r1", feature="Ratio", numerator_tag="CH1_vpp")

    def test_same_numerator_and_denominator(self):
        with pytest.raises(ValueError):
            ratio("r1", "CH1_vpp", "CH1_vpp")

    def test_self_reference(self):
        with pytest.raises(ValueError, match="自身"):
            ratio("r1", "r1", "CH2_vpp")


# ── MeasurementProcessor 比值计算 ────────────────────────────────

class TestRatioCompute:
    def test_ratio_is_numerator_over_denominator(self):
        specs = [scalar("CH1_vpp", 0), scalar("CH2_vpp", 1), ratio("r1", "CH1_vpp", "CH2_vpp")]
        snap = run_processor(specs, make_frame(ch0_vpp=2.0, ch1_vpp=1.0))
        assert snap.get("r1") == pytest.approx(2.0, rel=1e-6)
        assert snap.get_invalid("r1") is None

    def test_ratio_same_frame_values(self):
        """比值必须由同一帧的两个通道值算出。"""
        specs = [scalar("CH1_vpp", 0), scalar("CH2_vpp", 1), ratio("r1", "CH2_vpp", "CH1_vpp")]
        snap = run_processor(specs, make_frame(ch0_vpp=4.0, ch1_vpp=1.0))
        assert snap.get("r1") == pytest.approx(0.25, rel=1e-6)

    def test_zero_denominator_is_invalid(self):
        specs = [scalar("CH1_vpp", 0), scalar("CH2_vpp", 1), ratio("r1", "CH1_vpp", "CH2_vpp")]
        snap = run_processor(specs, make_frame(ch0_vpp=2.0, ch1_vpp=0.0))
        assert snap.get("r1") is None
        reason = snap.get_invalid("r1")
        assert reason is not None and "分母" in reason

    def test_near_zero_denominator_is_invalid(self):
        specs = [scalar("CH1_vpp", 0), scalar("CH2_vpp", 1), ratio("r1", "CH1_vpp", "CH2_vpp")]
        snap = run_processor(
            specs, make_frame(ch0_vpp=2.0, ch1_vpp=RATIO_MIN_DENOM_ABS / 10),
        )
        assert snap.get("r1") is None
        assert "分母" in snap.get_invalid("r1")

    def test_negative_denominator_allowed(self):
        """负分母是合法的物理量, 只保护接近 0 的情况。"""
        data = np.zeros((2, 100), dtype=np.float32)
        data[0, :50] = 2.0
        data[1, :50] = -1.0
        frame = RawFrame(sequence_num=1, data=data, sample_rate=1000.0)
        specs = [
            scalar("CH1_mean", 0, "Mean"),
            scalar("CH2_mean", 1, "Mean"),
            ratio("r1", "CH1_mean", "CH2_mean"),
        ]
        snap = run_processor(specs, frame)
        # 均值: CH1 = 1.0, CH2 = -0.5 → 比值 -2.0
        assert snap.get("r1") == pytest.approx(-2.0, rel=1e-6)
        assert snap.get_invalid("r1") is None

    def test_missing_numerator_source(self):
        specs = [scalar("CH2_vpp", 1), ratio("r1", "CH1_vpp", "CH2_vpp")]
        snap = run_processor(specs, make_frame())
        assert snap.get("r1") is None
        assert "分子" in snap.get_invalid("r1")

    def test_missing_denominator_source(self):
        specs = [scalar("CH1_vpp", 0), ratio("r1", "CH1_vpp", "CH2_vpp")]
        snap = run_processor(specs, make_frame())
        assert snap.get("r1") is None
        assert "分母" in snap.get_invalid("r1")

    def test_nan_source_is_invalid(self):
        """源测量为 NaN 时不产出比值 (避免污染 PID 输出)。"""
        spec = ratio("r1", "CH1_vpp", "CH2_vpp")
        value, reason = MeasurementProcessor._compute_ratio(
            spec, {"CH1_vpp": float("nan"), "CH2_vpp": 1.0},
        )
        assert value is None
        assert "NaN" in reason

    def test_ratio_cannot_reference_ratio(self):
        """嵌套引用不生效: 比值只从本帧标量结果取值。"""
        specs = [
            scalar("CH1_vpp", 0),
            scalar("CH2_vpp", 1),
            ratio("r1", "CH1_vpp", "CH2_vpp"),
            ratio("r2", "r1", "CH1_vpp"),
        ]
        snap = run_processor(specs, make_frame(ch0_vpp=2.0, ch1_vpp=1.0))
        assert snap.get("r1") == pytest.approx(2.0, rel=1e-6)
        assert snap.get("r2") is None
        assert "分子" in snap.get_invalid("r2")

    def test_scalar_measurements_unaffected(self):
        """加入比值规格不影响原有标量测量。"""
        specs = [
            scalar("CH1_vpp", 0),
            scalar("CH2_mean", 1, "Mean"),
            ratio("r1", "CH1_vpp", "CH2_mean"),
        ]
        snap = run_processor(specs, make_frame(ch0_vpp=2.0, ch1_vpp=1.0))
        assert snap.get("CH1_vpp") == pytest.approx(2.0, rel=1e-6)
        assert snap.get("CH2_mean") == pytest.approx(0.5, rel=1e-6)
        assert snap.get("r1") == pytest.approx(4.0, rel=1e-6)

    def test_invalid_ratio_does_not_raise(self):
        """连续无效帧只做节流告警, 不抛异常。"""
        specs = [scalar("CH1_vpp", 0), ratio("r1", "CH1_vpp", "CH2_vpp")]
        frame = make_frame()
        for _ in range(5):
            snap = run_processor(specs, frame)
            assert "r1" in snap.invalid_reasons


# ── FittedSnapshot.invalid_reasons ───────────────────────────────

class TestSnapshotInvalidReasons:
    def test_default_empty(self):
        snap = FittedSnapshot()
        assert snap.invalid_reasons == {}
        assert snap.get_invalid("anything") is None

    def test_invalid_not_in_flat_dict(self):
        snap = FittedSnapshot(
            event_measurements={"a": 1.0},
            invalid_reasons={"r1": "分母接近 0"},
        )
        assert snap.as_flat_dict() == {"a": 1.0}
        assert snap.get_invalid("r1") == "分母接近 0"


# ── FeedbackWorker / FeedbackManager: 无效输入自动暂停 ────────────

from scope.io.feedback_manager import FeedbackManager
from scope.io.feedback_worker import FeedbackConfig, FeedbackWorker
from scope.model.enums import SlotStatus
from scope.runtime.pid_controller import PidConfig


def make_config(worker_id: str = "w1", key: str = "r1",
                preset: float = 1.0) -> FeedbackConfig:
    return FeedbackConfig(
        worker_id=worker_id,
        measurement_key=key,
        pid_config=PidConfig(preset_value=preset),
    )


def make_worker(worker_id: str = "w1", key: str = "r1") -> FeedbackWorker:
    return FeedbackWorker(make_config(worker_id, key))


class TestReportInvalid:
    async def test_running_worker_paused_with_reason(self):
        w = make_worker()
        await w.start()
        assert w.status == SlotStatus.RUNNING

        w.report_invalid("分母接近 0 (1e-12)")

        assert w.status == SlotStatus.PAUSED
        assert w.stop_reason == "输入无效: 分母接近 0 (1e-12)"

    async def test_reason_updated_when_already_paused(self):
        w = make_worker()
        await w.start()
        w.report_invalid("原因 A")
        w.report_invalid("原因 B")
        assert w.status == SlotStatus.PAUSED
        assert w.stop_reason == "输入无效: 原因 B"

    async def test_paused_worker_keeps_paused(self):
        w = make_worker()
        await w.start()
        await w.pause()
        w.report_invalid("分母接近 0")
        assert w.status == SlotStatus.PAUSED
        assert "分母接近 0" in w.stop_reason

    async def test_idle_worker_ignored(self):
        w = make_worker()
        assert w.status == SlotStatus.IDLE
        w.report_invalid("分母接近 0")
        assert w.status == SlotStatus.IDLE
        assert w.stop_reason == ""

    async def test_resume_clears_invalid_reason(self):
        w = make_worker()
        await w.start()
        w.report_invalid("分母接近 0")
        await w.resume()
        assert w.status == SlotStatus.RUNNING
        assert w.stop_reason == ""


class TestDispatchInvalid:
    async def test_invalid_snapshot_pauses_worker(self):
        manager = FeedbackManager()
        await manager.add_worker(make_config("w1", "r1"), publish=False)
        worker = manager._workers["w1"]

        snap = FittedSnapshot(invalid_reasons={"r1": "分母接近 0 (1e-12)"})
        await manager._dispatch_snapshot(snap)

        assert worker.status == SlotStatus.PAUSED
        assert "分母接近 0" in worker.stop_reason
        assert worker.frames_processed == 0
        await manager.stop()

    async def test_valid_ratio_still_processed(self):
        manager = FeedbackManager()
        await manager.add_worker(make_config("w1", "r1", preset=1.5), publish=False)
        worker = manager._workers["w1"]

        await manager._dispatch_snapshot(FittedSnapshot(event_measurements={"r1": 1.5}))

        assert worker.status == SlotStatus.RUNNING
        assert worker.frames_processed == 1
        assert worker.last_value == pytest.approx(1.5)
        await manager.stop()

    async def test_invalid_only_affects_its_worker(self):
        manager = FeedbackManager()
        await manager.add_worker(make_config("w_ratio", "r1"), publish=False)
        await manager.add_worker(make_config("w_scalar", "CH1_vpp", preset=2.0), publish=False)
        ratio_worker = manager._workers["w_ratio"]
        scalar_worker = manager._workers["w_scalar"]

        snap = FittedSnapshot(
            event_measurements={"CH1_vpp": 2.0},
            invalid_reasons={"r1": "分母接近 0"},
        )
        await manager._dispatch_snapshot(snap)

        assert ratio_worker.status == SlotStatus.PAUSED
        assert scalar_worker.status == SlotStatus.RUNNING
        assert scalar_worker.frames_processed == 1
        await manager.stop()

    async def test_end_to_end_processor_invalid_pauses_worker(self):
        """processor 分母为 0 产出的 invalid 快照 → worker 自动暂停。"""
        specs = [
            scalar("CH1_vpp", 0),
            scalar("CH2_vpp", 1),
            ratio("r1", "CH1_vpp", "CH2_vpp"),
        ]
        snap = run_processor(specs, make_frame(ch0_vpp=2.0, ch1_vpp=0.0))

        manager = FeedbackManager()
        await manager.add_worker(make_config("w1", "r1"), publish=False)
        worker = manager._workers["w1"]
        await manager._dispatch_snapshot(snap)

        assert worker.status == SlotStatus.PAUSED
        assert "分母" in worker.stop_reason
        await manager.stop()
