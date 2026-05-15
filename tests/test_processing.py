"""
Phase 2 处理管道 — 测试

验证 Pipeline 框架、自动测量、数学运算、FFT 的正确性。
"""

import numpy as np
import pytest

from scope.model import AnalysisResult, ChannelData, TriggerInfo
from scope.processing import (
    ProcessingPipeline,
    AutoMeasure,
    MathOp,
    FFTAnalyze,
    LowPassFilter,
    HighPassFilter,
)


# ── Helper ─────────────────────────────────────────────────────

def make_sine(fs: float = 100_000, freq: float = 1000.0,
              amplitude: float = 2.0, duration: float = 0.01,
              noise: float = 0.0, channel_name: str = "CH1") -> AnalysisResult:
    """生成已知参数的正弦波 AnalysisResult"""
    n_samples = int(fs * duration)
    t = np.linspace(0, duration, n_samples, endpoint=False)
    raw = (amplitude / 2) * np.sin(2 * np.pi * freq * t)
    if noise > 0:
        raw += np.random.normal(0, noise, n_samples)

    ch = ChannelData(
        raw=raw.astype(np.float32),
        time_axis=t,
        sample_rate=fs,
        resolution=12,
        vertical_scale=1.0,
        vertical_offset=0.0,
    )
    return AnalysisResult(
        sequence_num=1,
        trigger=TriggerInfo.immediate(),
        channels={channel_name: ch},
    )


def make_square(fs: float = 100_000, freq: float = 1000.0,
                amplitude: float = 3.3, duty: float = 0.5,
                duration: float = 0.01) -> AnalysisResult:
    """生成方波"""
    n_samples = int(fs * duration)
    t = np.linspace(0, duration, n_samples, endpoint=False)
    period_samples = int(fs / freq)
    high_samples = int(period_samples * duty)
    one_period = np.concatenate([
        np.ones(high_samples) * (amplitude / 2),
        np.ones(period_samples - high_samples) * (-amplitude / 2),
    ])
    raw = np.tile(one_period, int(np.ceil(n_samples / period_samples)))[:n_samples]

    ch = ChannelData(
        raw=raw.astype(np.float32),
        time_axis=t,
        sample_rate=fs,
        resolution=12,
        vertical_scale=1.0,
        vertical_offset=0.0,
    )
    return AnalysisResult(
        sequence_num=1,
        trigger=TriggerInfo.immediate(),
        channels={"CH1": ch},
    )


# ── Pipeline Framework ─────────────────────────────────────────

class TestPipeline:
    def test_empty_pipeline(self):
        p = ProcessingPipeline()
        result = make_sine()
        out = p.process(result)
        assert out is result  # same object, unchanged

    def test_single_stage(self):
        p = ProcessingPipeline()
        p.add_stage(AutoMeasure(["Vpp"], channels=["CH1"]))
        result = make_sine(amplitude=2.0)
        out = p.process(result)
        assert abs(out.measurements["CH1_Vpp"] - 2.0) < 0.01

    def test_multi_stage(self):
        p = ProcessingPipeline()
        p.add_stage(AutoMeasure(["Vpp", "Freq"], channels=["CH1"]))
        p.add_stage(AutoMeasure(["Vrms"], channels=["CH1"]))
        result = make_sine(amplitude=2.0, freq=1000.0)
        out = p.process(result)
        assert "CH1_Vpp" in out.measurements
        assert "CH1_Freq" in out.measurements
        assert "CH1_Vrms" in out.measurements

    def test_stage_error_isolation(self):
        """一个阶段出错不阻止后续阶段"""
        class CrashStage:
            def process(self, result):
                raise RuntimeError("booom")

        p = ProcessingPipeline()
        p.add_stage(CrashStage())
        p.add_stage(AutoMeasure(["Vpp"], channels=["CH1"]))
        result = make_sine(amplitude=2.0)
        out = p.process(result)
        assert "CH1_Vpp" in out.measurements

    def test_list_stages(self):
        p = ProcessingPipeline()
        p.add_stage(AutoMeasure(["Vpp"]))
        names = p.list_stages()
        assert len(names) == 1
        assert "AutoMeasure" in names[0]

    def test_clear(self):
        p = ProcessingPipeline()
        p.add_stage(AutoMeasure(["Vpp"]))
        p.clear()
        assert len(p.list_stages()) == 0


# ── Measurements ───────────────────────────────────────────────

class TestAutoMeasure:
    def test_vpp_sine(self):
        result = make_sine(amplitude=2.0)
        p = AutoMeasure(["Vpp"], channels=["CH1"])
        out = p.process(result)
        assert abs(out.measurements["CH1_Vpp"] - 2.0) < 0.01

    def test_vpp_square(self):
        result = make_square(amplitude=3.3)
        p = AutoMeasure(["Vpp"], channels=["CH1"])
        out = p.process(result)
        assert abs(out.measurements["CH1_Vpp"] - 3.3) < 0.1

    def test_freq_sine(self):
        result = make_sine(freq=1000.0, duration=0.02)
        p = AutoMeasure(["Freq"], channels=["CH1"])
        out = p.process(result)
        assert abs(out.measurements["CH1_Freq"] - 1000.0) < 10  # ±10Hz

    def test_freq_square(self):
        result = make_square(freq=1000.0, duration=0.02)
        p = AutoMeasure(["Freq"], channels=["CH1"])
        out = p.process(result)
        assert abs(out.measurements["CH1_Freq"] - 1000.0) < 10

    def test_vrms_sine(self):
        """正弦波 Vrms = Vpp / (2*sqrt(2))"""
        result = make_sine(amplitude=2.0)  # 1V amplitude
        expected_rms = 1.0 / np.sqrt(2)  # ≈ 0.707
        p = AutoMeasure(["Vrms"], channels=["CH1"])
        out = p.process(result)
        assert abs(out.measurements["CH1_Vrms"] - expected_rms) < 0.01

    def test_duty_cycle(self):
        result = make_square(duty=0.5)
        p = AutoMeasure(["DutyCycle"], channels=["CH1"])
        out = p.process(result)
        assert abs(out.measurements["CH1_DutyCycle"] - 50.0) < 2

    def test_duty_cycle_25(self):
        result = make_square(duty=0.25)
        p = AutoMeasure(["DutyCycle"], channels=["CH1"])
        out = p.process(result)
        assert abs(out.measurements["CH1_DutyCycle"] - 25.0) < 2

    def test_all_measurements(self):
        """所有测量项都能执行, 不报错"""
        result = make_sine(freq=1000.0, amplitude=2.0, duration=0.02)
        p = AutoMeasure(channels=["CH1"])
        out = p.process(result)
        for key in ["CH1_Vpp", "CH1_Freq", "CH1_Vrms", "CH1_Vavg",
                     "CH1_Period", "CH1_Vmax", "CH1_Vmin"]:
            assert key in out.measurements, f"缺少 {key}"

    def test_multiple_channels(self):
        """多通道各自计算"""
        result = make_sine(amplitude=2.0, channel_name="CH1")
        ch2 = make_sine(amplitude=5.0, channel_name="CH2")
        result.channels["CH2"] = ch2.channels["CH2"]

        p = AutoMeasure(["Vpp"], channels=["CH1", "CH2"])
        out = p.process(result)
        assert abs(out.measurements["CH1_Vpp"] - 2.0) < 0.01
        assert abs(out.measurements["CH2_Vpp"] - 5.0) < 0.01


# ── Math Operations ────────────────────────────────────────────

class TestMathOp:
    def test_add(self):
        result = make_sine(amplitude=2.0, channel_name="CH1")
        ch2 = make_sine(amplitude=3.0, channel_name="CH2")
        result.channels["CH2"] = ch2.channels["CH2"]

        p = MathOp("CH1 + CH2", output="MATH1")
        out = p.process(result)
        assert "MATH1" in out.math_channels
        # 相同频率的正弦波叠加, 幅度 ≈ 2.5V
        assert abs(np.ptp(out.math_channels["MATH1"]) - 5.0) < 0.1

    def test_subtract(self):
        result = make_sine(amplitude=2.0, channel_name="CH1")
        ch2 = make_sine(amplitude=2.0, channel_name="CH2")
        result.channels["CH2"] = ch2.channels["CH2"]

        p = MathOp("CH1 - CH2", output="MATH1")
        out = p.process(result)
        # 相同信号相减 → 接近 0
        assert abs(np.ptp(out.math_channels["MATH1"])) < 0.01

    def test_multiply(self):
        result = make_sine(amplitude=2.0, channel_name="CH1")
        p = MathOp("CH1 * 2", output="MATH1")
        out = p.process(result)
        assert abs(np.ptp(out.math_channels["MATH1"]) - 4.0) < 0.1

    def test_invert(self):
        result = make_sine(amplitude=2.0, channel_name="CH1")
        p = MathOp("-CH1", output="MATH1")
        out = p.process(result)
        # 原始数据和反相数据之和 ≈ 0
        sum_data = result.channels["CH1"].raw + out.math_channels["MATH1"]
        assert abs(np.max(np.abs(sum_data))) < 0.01

    def test_absolute(self):
        result = make_sine(amplitude=2.0, channel_name="CH1")
        p = MathOp("|CH1|", output="MATH1")
        out = p.process(result)
        assert np.all(out.math_channels["MATH1"] >= 0)

    def test_missing_channel(self):
        """找不到通道时应跳过不报错"""
        result = make_sine(channel_name="CH1")
        p = MathOp("CH1 + CH9", output="MATH1")
        out = p.process(result)
        assert "MATH1" not in out.math_channels


# ── FFT ────────────────────────────────────────────────────────

class TestFFT:
    def test_fft_peak_freq(self):
        result = make_sine(freq=1000.0, duration=0.05)
        p = FFTAnalyze(channels=["CH1"])
        out = p.process(result)
        assert "CH1" in out.fft
        freqs, mags = out.fft["CH1"]
        assert len(freqs) > 0
        assert len(mags) > 0
        # 最大幅度对应的频率 ≈ 1000Hz
        max_idx = np.argmax(mags)
        assert abs(freqs[max_idx] - 1000.0) < 5

    def test_fft_measurements(self):
        result = make_sine(freq=1000.0, amplitude=2.0, duration=0.05)
        p = FFTAnalyze(channels=["CH1"])
        out = p.process(result)
        assert "CH1_FFT_Freq" in out.measurements
        assert abs(out.measurements["CH1_FFT_Freq"] - 1000.0) < 5

    def test_fft_peaks(self):
        """多峰值标注"""
        result = make_sine(freq=1000.0, duration=0.05)
        p = FFTAnalyze(channels=["CH1"], peak_count=3)
        out = p.process(result)
        for i in range(1, 4):
            assert f"CH1_FFT_Peak{i}_Freq" in out.measurements

    def test_fft_window_none(self):
        result = make_sine(freq=1000.0, duration=0.05)
        p = FFTAnalyze(channels=["CH1"], window="none")
        out = p.process(result)
        assert "CH1" in out.fft


# ── Pipeline Integration ───────────────────────────────────────

class TestPipelineIntegration:
    def test_full_pipeline(self):
        """完整的测量 + 数学 + FFT 管道"""
        result = make_sine(freq=1000.0, amplitude=2.0, duration=0.02,
                           channel_name="CH1")
        ch2 = make_sine(freq=500.0, amplitude=1.0, duration=0.02,
                        channel_name="CH2")
        result.channels["CH2"] = ch2.channels["CH2"]

        pipeline = ProcessingPipeline()
        pipeline.add_stage(AutoMeasure(["Vpp", "Freq"], channels=["CH1", "CH2"]))
        pipeline.add_stage(MathOp("CH1 + CH2", output="MATH1"))
        pipeline.add_stage(FFTAnalyze(channels=["CH1", "CH2"]))

        out = pipeline.process(result)

        # 测量值
        assert abs(out.measurements["CH1_Vpp"] - 2.0) < 0.01
        assert abs(out.measurements["CH2_Freq"] - 500.0) < 10

        # 数学通道
        assert "MATH1" in out.math_channels

        # FFT
        assert "CH1" in out.fft
        assert "CH2" in out.fft

    def test_with_simulator(self):
        """与 Phase 0 的 SimulatorDevice 集成"""
        from scope.hardware.simulator import SimulatorDevice
        from scope.hardware import DeviceConfig

        device = SimulatorDevice()
        config = DeviceConfig(sample_rate=100_000, record_length=2000)
        device.open()
        device.configure(config)
        device.start_acquisition()

        pipeline = ProcessingPipeline()
        pipeline.add_stage(AutoMeasure(["Vpp", "Freq"], channels=["CH1", "CH2"]))

        chunk = device.read_chunk()
        result = device.make_analysis_result(chunk)
        out = pipeline.process(result)

        assert "CH1_Vpp" in out.measurements
        assert "CH2_Vpp" in out.measurements
        # 正弦波 amplitude=2.0 → Vpp≈2.0
        assert abs(out.measurements["CH1_Vpp"] - 2.0) < 0.1

        device.stop_acquisition()
        device.close()
