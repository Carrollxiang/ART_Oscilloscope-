"""
Phase 0 验证测试: RawFrame + 模拟器

测试 RawFrame 数据结构和 SimulatorDevice 的基本功能。
包括事件驱动模式和预生成数据功能。
"""

import time
import numpy as np
import pytest

from scope.model import RawFrame
from scope.hardware import DeviceConfig
from scope.hardware.simulator import SimulatorDevice
from scope.runtime import MeasurementSpec, FittedSnapshot


# ── RawFrame Tests ─────────────────────────────────────────────

def test_raw_frame_creation():
    """验证 RawFrame 创建"""
    data = np.random.randn(4, 1000).astype(np.float32)
    frame = RawFrame(
        sequence_num=1,
        data=data,
        sample_rate=100_000,
    )
    
    assert frame.sequence_num == 1
    assert frame.n_channels == 4
    assert frame.n_samples == 1000
    assert frame.sample_rate == 100_000
    assert frame.duration == 0.01  # 1000 / 100000


def test_raw_frame_time_axis():
    """验证时间轴生成"""
    data = np.zeros((2, 500), dtype=np.float32)
    frame = RawFrame(sequence_num=1, data=data, sample_rate=50_000)
    
    t = frame.time_axis()
    assert len(t) == 500
    assert t[0] == 0.0
    assert abs(t[-1] - 499/50000) < 1e-6


def test_raw_frame_validation():
    """验证数据验证"""
    # 有效数据
    data = np.zeros((2, 100), dtype=np.float32)
    frame = RawFrame(sequence_num=1, data=data, sample_rate=1000)
    assert frame.n_channels == 2
    
    # 无效: 非 2D 数组
    with pytest.raises(ValueError):
        RawFrame(sequence_num=1, data=np.zeros(100), sample_rate=1000)
    
    # 无效: 采样率 <= 0
    with pytest.raises(ValueError):
        RawFrame(sequence_num=1, data=data, sample_rate=0)


# ── SimulatorDevice Tests ─────────────────────────────────────--

def test_simulator_basic():
    """基本功能测试"""
    device = SimulatorDevice()
    config = DeviceConfig(
        sample_rate=100_000,
        record_length=5000,
        channels_enabled=[0, 1],
    )
    
    assert device.open()
    device.configure(config)
    device.start_acquisition()
    
    chunk = device.read_chunk()
    assert chunk.shape == (2, 5000)
    assert chunk.dtype == np.float32
    
    device.stop_acquisition()
    device.close()


def test_simulator_make_raw_frame():
    """验证 make_raw_frame 输出"""
    device = SimulatorDevice()
    config = DeviceConfig(sample_rate=50_000, record_length=1000)
    device.open()
    device.configure(config)
    device.start_acquisition()
    
    chunk = device.read_chunk()
    frame = device.make_raw_frame(chunk)
    
    assert isinstance(frame, RawFrame)
    assert frame.sequence_num == 1
    assert frame.n_channels == len(config.channels_enabled)
    assert frame.n_samples == config.record_length
    assert frame.sample_rate == config.sample_rate
    
    device.stop_acquisition()
    device.close()


def test_simulator_waveform_shapes():
    """验证不同波形的生成结果"""
    device = SimulatorDevice()
    config = DeviceConfig(sample_rate=100_000, record_length=1000)
    device.open()
    device.configure(config)
    device.start_acquisition()
    
    # 正弦波: amplitude=2.0 → 范围应在 ±1.0V
    chunk = device.read_chunk()
    ch0 = chunk[0]
    assert -1.1 < ch0.min() < -0.9
    assert 0.9 < ch0.max() < 1.1
    
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
    
    with pytest.raises(TimeoutError):
        device.read_chunk()  # fail
    
    device.stop_acquisition()
    device.close()


def test_simulator_ping():
    """验证 ping 功能"""
    device = SimulatorDevice()
    device.open()
    assert device.ping()
    
    device.inject_ping_failure(True)
    assert not device.ping()
    
    device.clear_faults()
    assert device.ping()
    device.close()


# ── MeasurementSpec Tests ──────────────────────────────────────

def test_measurement_spec_creation():
    """验证 MeasurementSpec 创建"""
    spec = MeasurementSpec(
        tag="CH1_power",
        channel=0,
        start_ms=10.0,
        end_ms=100.0,
        feature="Vrms",
    )
    assert spec.tag == "CH1_power"
    assert spec.channel == 0
    assert spec.feature == "Vrms"


def test_measurement_spec_validation():
    """验证参数验证"""
    # 有效
    spec = MeasurementSpec(tag="test", channel=0)
    assert spec.channel == 0
    
    # 无效: 负通道
    with pytest.raises(ValueError):
        MeasurementSpec(tag="test", channel=-1)
    
    # 无效: 负 start_ms
    with pytest.raises(ValueError):
        MeasurementSpec(tag="test", channel=0, start_ms=-1)
    
    # 无效: end_ms <= start_ms
    with pytest.raises(ValueError):
        MeasurementSpec(tag="test", channel=0, start_ms=10, end_ms=5)


# ── FittedSnapshot Tests ───────────────────────────────────────

def test_fitted_snapshot():
    """验证 FittedSnapshot"""
    snap = FittedSnapshot(
        sequence_num=42,
        event_measurements={"CH1_vpp": 2.0, "CH1_vrms": 0.707},
    )
    
    assert snap.sequence_num == 42
    assert snap.get("CH1_vpp") == 2.0
    assert snap.get("CH1_vrms") == 0.707
    assert snap.get("nonexistent") is None
    assert snap.get("meta:seq") == 42.0


def test_fitted_snapshot_flat_dict():
    """验证 as_flat_dict"""
    snap = FittedSnapshot(
        sequence_num=1,
        event_measurements={"a": 1.0, "b": 2.0},
    )
    flat = snap.as_flat_dict()
    assert flat == {"a": 1.0, "b": 2.0}


# ── SimulatorDevice Event-Driven Tests ───────────────────────────

def test_simulator_event_driven():
    """验证事件驱动模式"""
    device = SimulatorDevice()
    config = DeviceConfig(
        sample_rate=100_000,
        record_length=1000,
        channels_enabled=[0, 1],
    )
    device.open()
    device.configure(config)
    
    received = []
    def callback(chunk):
        received.append(chunk.copy())
    
    device.set_data_callback(callback)
    device.start_acquisition()
    
    # 等待接收几帧
    time.sleep(0.3)
    
    device.stop_acquisition()
    device.close()
    
    assert len(received) >= 2, f"Expected >= 2 frames, got {len(received)}"
    assert received[0].shape == (2, 1000)
    assert received[0].dtype == np.float32


def test_simulator_pregenerated_frames():
    """验证预生成帧功能"""
    device = SimulatorDevice()
    device.set_cache_size(5)  # 只预生成 5 帧
    config = DeviceConfig(sample_rate=50_000, record_length=500)
    device.open()
    device.configure(config)
    device.start_acquisition()
    
    # 读取多于预生成数量的帧
    frames = [device.read_chunk() for _ in range(12)]
    
    device.stop_acquisition()
    device.close()
    
    # 验证帧 0 == 帧 5 == 帧 10 (5帧循环)
    assert np.allclose(frames[0], frames[5]), "Frame 0 should equal frame 5"
    assert np.allclose(frames[0], frames[10]), "Frame 0 should equal frame 10"
    
    # 验证帧 0 != 帧 1 (不同帧有变化)
    assert not np.allclose(frames[0], frames[1]), "Frame 0 should differ from frame 1"


def test_simulator_frame_variation():
    """验证预生成帧有变化（不是完全相同）"""
    device = SimulatorDevice()
    config = DeviceConfig(sample_rate=100_000, record_length=1000)
    device.open()
    device.configure(config)
    device.start_acquisition()
    
    frames = [device.read_chunk() for _ in range(10)]
    
    device.stop_acquisition()
    device.close()
    
    # 至少应该有两帧不完全相同（预生成时有参数变化）
    # 帧 0 和 帧 1 应该有差异（相位、频率或幅度）
    diff = np.abs(frames[0] - frames[1])
    assert np.max(diff) > 0.01, "Frames should have variation"


def test_simulator_thread_cleanup():
    """验证线程正确清理"""
    device = SimulatorDevice()
    config = DeviceConfig(sample_rate=50_000, record_length=100)
    device.open()
    device.configure(config)
    
    received = []
    device.set_data_callback(lambda chunk: received.append(chunk))
    
    device.start_acquisition()
    time.sleep(0.2)
    device.stop_acquisition()
    
    # 再次启动
    device.start_acquisition()
    time.sleep(0.2)
    device.stop_acquisition()
    
    device.close()
    
    assert len(received) >= 2
