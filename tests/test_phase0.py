"""Phase 0 验证测试: 数据模型 + 模拟器"""

import numpy as np
from scope.model import AnalysisResult, ChannelData, TriggerInfo
from scope.hardware import DeviceConfig
from scope.hardware.simulator import SimulatorDevice


def test_analysis_result_creation():
    """验证 AnalysisResult 及其嵌套数据类的创建"""
    t = np.linspace(0, 0.01, 1000)
    raw = np.sin(2 * np.pi * 1000 * t)

    ch1 = ChannelData(
        raw=raw,
        time_axis=t,
        sample_rate=100_000,
        resolution=12,
        vertical_scale=1.0,
        vertical_offset=0.0,
    )
    assert len(ch1.raw) == 1000
    assert ch1.sample_rate == 100_000

    trigger = TriggerInfo(
        trigger_type="immediate",
        trigger_source=0,
        trigger_level=0.0,
        trigger_slope="rising",
        trigger_position=0.5,
        trigger_timestamp=12345.0,
    )

    result = AnalysisResult(
        sequence_num=1,
        trigger=trigger,
        channels={"CH1": ch1},
        measurements={"CH1_Vpp": 2.0},
    )
    assert result.sequence_num == 1
    assert result.trigger.trigger_timestamp == 12345.0
    assert result.measurements["CH1_Vpp"] == 2.0


def test_channel_data_length_mismatch():
    """raw 和 time_axis 长度不一致应报错"""
    import pytest
    with pytest.raises(ValueError):
        ChannelData(
            raw=np.zeros(100),
            time_axis=np.linspace(0, 1, 101),  # 少一个
            sample_rate=1000,
            resolution=12,
            vertical_scale=1.0,
            vertical_offset=0.0,
        )


def test_trigger_immediate_factory():
    trigger = TriggerInfo.immediate()
    assert trigger.trigger_type == "immediate"
    assert trigger.trigger_position == 0.5


def test_simulator_basic():
    device = SimulatorDevice()
    config = DeviceConfig(
        sample_rate=1_000_000,
        record_length=5000,
        channels_enabled=[0, 1],
    )

    assert device.open()
    device.configure(config)
    device.start_acquisition()

    chunk = device.read_chunk()
    assert chunk.shape == (len(config.channels_enabled), config.record_length)
    assert chunk.dtype == np.float32

    result = device.make_analysis_result(chunk)
    assert result.sequence_num == 1
    assert "CH1" in result.channels
    assert "CH2" in result.channels
    assert len(result.channels["CH1"].raw) == config.record_length

    device.stop_acquisition()
    device.close()


def test_simulator_waveform_shapes():
    """验证不同波形的生成结果在合理范围内"""
    device = SimulatorDevice()
    config = DeviceConfig(sample_rate=100_000, record_length=1000)
    device.open()
    device.configure(config)
    device.start_acquisition()

    # 正弦波: amplitude=2.0 → 范围应在 ±1.0V
    chunk = device.read_chunk()
    ch0 = chunk[0]
    assert -1.1 < ch0.min() < -0.9  # -amplitude/2
    assert 0.9 < ch0.max() < 1.1    # +amplitude/2

    device.stop_acquisition()
    device.close()


def test_simulator_fault_injection():
    """验证故障注入机制"""
    device = SimulatorDevice()
    config = DeviceConfig()
    device.open()
    device.configure(config)
    device.start_acquisition()

    # 每 3 次读取抛一次故障
    device.inject_read_failure(every_n_reads=3)
    _ = device.read_chunk()  # ok
    _ = device.read_chunk()  # ok

    import pytest
    with pytest.raises(TimeoutError):
        device.read_chunk()  # fail

    device.stop_acquisition()
    device.close()


def test_simulator_ping():
    device = SimulatorDevice()
    device.open()
    assert device.ping()

    device.inject_ping_failure(True)
    assert not device.ping()

    device.clear_faults()
    assert device.ping()
    device.close()


def test_summary_format():
    """验证 AnalysisResult.summary() 的输出格式"""
    t = np.linspace(0, 0.01, 100)
    result = AnalysisResult(
        sequence_num=42,
        trigger=TriggerInfo.immediate(),
        channels={
            "CH1": ChannelData(
                raw=np.zeros(100),
                time_axis=t,
                sample_rate=1000,
                resolution=12,
                vertical_scale=1.0,
                vertical_offset=0.0,
            )
        },
        measurements={"CH1_Vpp": 3.3, "CH1_Freq": 1000.0},
    )
    summary = result.summary()
    assert "#42" in summary
    assert "CH1_Vpp" in summary
