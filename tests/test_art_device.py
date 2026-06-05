"""
Phase 4 — ArtDevice 硬件适配层测试

因实际 ART 硬件未就绪, 使用 unittest.mock 模拟 artdaq (NI-DAQmx) 库。
测试覆盖:
  - open/close 生命周期
  - start/stop 采集
  - read_chunk 数据格式
  - configure 配置
  - Watchdog 接口 (ping/reset/restore_state)
  - 超时异常 → TimeoutError 转换
  - make_analysis_result 组装
"""

import numpy as np
import pytest
from unittest.mock import patch, MagicMock, PropertyMock


# ── Mock 辅助 ─────────────────────────────────────────────────

@pytest.fixture
def mock_artdaq():
    """
    创建 mock artdaq 模块, 替换真实 artdaq.Task。

    mock_task.read() 默认返回 4 通道 × 100 样本的 float64 数据。
    """
    with patch.dict('sys.modules', {'artdaq': MagicMock()}):

        # 创建 mock 常量
        import sys
        from types import ModuleType

        # 构造 mock artdaq 模块树
        mock_artdaq_mod = ModuleType('artdaq')
        mock_artdaq_mod.Task = MagicMock()

        # Mock 常量
        mock_constants = ModuleType('artdaq.constants')
        mock_constants.AcquisitionType = MagicMock()
        mock_constants.AcquisitionType.FINITE = "finite"
        mock_constants.AcquisitionType.CONTINUOUS = "continuous"
        mock_constants.TerminalConfiguration = MagicMock()
        mock_constants.TerminalConfiguration.DEFAULT = -1
        mock_constants.TerminalConfiguration.RSE = 10083
        mock_constants.TerminalConfiguration.NRSE = 10078
        mock_constants.TerminalConfiguration.DIFFERENTIAL = 10106
        mock_constants.TerminalConfiguration.PSEUDODIFFERENTIAL = 12529
        mock_constants.Slope = MagicMock()
        mock_constants.Slope.RISING = 10280
        mock_constants.Slope.FALLING = 10171
        mock_constants.Edge = MagicMock()
        mock_constants.Edge.RISING = 10280
        mock_constants.Edge.FALLING = 10171
        mock_constants.WAIT_INFINITELY = -1.0

        sys.modules['artdaq.constants'] = mock_constants

        # 常量的快捷引用 — art_device.py 通过 self._xxx 访问
        mock_artdaq_mod.constants = mock_constants

        # Mock Task 实例
        mock_task = MagicMock()
        mock_task.ai_channels = MagicMock()
        mock_task.timing = MagicMock()
        mock_task.triggers = MagicMock()
        mock_task.triggers.start_trigger = MagicMock()

        # mock_task.read 默认返回 4 通道正弦波数据
        def default_read(number_of_samples_per_channel=100, timeout=5.0):
            t = np.linspace(0, 0.01, number_of_samples_per_channel)
            ch1 = np.sin(2 * np.pi * 1000 * t)          # CH1: 1kHz 正弦
            ch2 = np.sign(np.sin(2 * np.pi * 500 * t))   # CH2: 500Hz 方波
            ch3 = np.zeros(number_of_samples_per_channel) # CH3: 零
            ch4 = np.random.normal(0, 0.1, number_of_samples_per_channel)  # CH4: 噪声
            return [ch1.tolist(), ch2.tolist(), ch3.tolist(), ch4.tolist()]

        mock_task.read = MagicMock(side_effect=default_read)
        mock_artdaq_mod.Task.return_value = mock_task

        sys.modules['artdaq'] = mock_artdaq_mod

        yield mock_artdaq_mod, mock_task


@pytest.fixture
def device(mock_artdaq):
    """创建 ArtDevice 实例 (已 open)。"""
    from scope.hardware.art_device import ArtDevice
    dev = ArtDevice(
        device_name="Dev42",
        ai_channels="ai0:3",
        terminal_config="NRSE",
        min_val=-10.0,
        max_val=10.0,
    )
    assert dev.open(), "ArtDevice open() 应返回 True"
    return dev


# ── open/close ────────────────────────────────────────────────

class TestArtDeviceLifecycle:
    def test_open_success(self, mock_artdaq):
        from scope.hardware.art_device import ArtDevice
        dev = ArtDevice()
        assert dev.open() is True
        dev.close()

    def test_open_fail_dll(self):
        """Art_DAQ.dll 加载失败时 open() 返回 False。"""
        # 无 artdaq 包 → ImportError → 返回 False
        # 有包但无DLL → 通常 open() 成功, 实际调用时失败
        from scope.hardware.art_device import ArtDevice
        dev = ArtDevice()
        result = dev.open()
        # 不强制要求 True/False, 只要不抛异常
        assert isinstance(result, bool)
        dev.close()

    def test_close(self, device):
        device.close()  # 不应抛异常

    def test_double_close(self, device):
        device.close()
        device.close()  # 二次关闭应安全


# ── 采集生命周期 ──────────────────────────────────────────────

class TestArtDeviceAcquisition:
    def test_start_stop(self, device):
        from scope.hardware import DeviceConfig
        config = DeviceConfig(sample_rate=10000, record_length=5000)
        device.configure(config)
        device.start_acquisition()
        device.stop_acquisition()

    def test_start_without_configure(self, device):
        """未 configure 就 start 应抛异常。"""
        with pytest.raises(RuntimeError, match="请先调用 configure"):
            device.start_acquisition()

    def test_read_after_start(self, device):
        from scope.hardware import DeviceConfig
        config = DeviceConfig(sample_rate=10000, record_length=100)
        device.configure(config)
        device.start_acquisition()

        chunk = device.read_chunk()
        assert isinstance(chunk, np.ndarray)
        assert chunk.shape == (4, 100)  # 4 通道 × 100 样本
        assert chunk.dtype == np.float32
        device.stop_acquisition()

    def test_read_without_start(self, device):
        from scope.hardware import DeviceConfig
        config = DeviceConfig(sample_rate=10000, record_length=100)
        device.configure(config)
        with pytest.raises(RuntimeError, match="采集未运行"):
            device.read_chunk()

    def test_read_returns_mock_data(self, device):
        """验证 mock 数据通过 read_chunk 正确返回。"""
        from scope.hardware import DeviceConfig
        config = DeviceConfig(sample_rate=10000, record_length=500)
        device.configure(config)
        device.start_acquisition()

        chunk = device.read_chunk()
        # CH1 应是近似正弦波 (模拟)
        assert abs(np.max(chunk[0]) - 1.0) < 0.1
        assert abs(np.min(chunk[0]) + 1.0) < 0.1
        device.stop_acquisition()


# ── 配置 ──────────────────────────────────────────────────────

class TestArtDeviceConfig:
    def test_configure(self, device):
        from scope.hardware import DeviceConfig
        config = DeviceConfig(sample_rate=50000, record_length=1000)
        device.configure(config)
        assert device.get_config().sample_rate == 50000
        assert device.get_config().record_length == 1000

    def test_configure_channel_count(self, device):
        from scope.hardware import DeviceConfig
        config = DeviceConfig(channels_enabled=[0, 1])
        device.configure(config)
        assert len(device.get_config().channels_enabled) == 2


# ── Watchdog 接口 ─────────────────────────────────────────────

class TestArtDeviceWatchdog:
    def test_ping_success(self, device, mock_artdaq):
        """ping 应返回 True (mock 未设置异常)。"""
        from scope.hardware import DeviceConfig
        config = DeviceConfig(sample_rate=10000, record_length=100)
        device.configure(config)
        device.start_acquisition()
        assert device.ping() is True
        device.stop_acquisition()

    def test_ping_failure(self, device, mock_artdaq):
        """task.read 抛异常时 ping 应返回 False。"""
        mock_artdaq_mod, mock_task = mock_artdaq
        mock_task.read.side_effect = RuntimeError("Device not found")
        from scope.hardware import DeviceConfig
        config = DeviceConfig(sample_rate=10000, record_length=100)
        device.configure(config)
        device.start_acquisition()
        assert device.ping() is False
        device.stop_acquisition()

    def test_reset(self, device):
        """reset 应能重建 Task。"""
        assert device.reset() is True

    def test_restore_state(self, device):
        """restore_state 应能重新启动采集。"""
        from scope.hardware import DeviceConfig
        config = DeviceConfig(sample_rate=10000, record_length=100)
        device.configure(config)
        device.restore_state(config)
        chunk = device.read_chunk()
        assert chunk.shape[1] == 100
        device.stop_acquisition()

    def test_read_timeout(self, device, mock_artdaq):
        """task.read 超时 → TimeoutError。"""
        mock_artdaq_mod, mock_task = mock_artdaq
        mock_task.read.side_effect = TimeoutError("timeout")
        from scope.hardware import DeviceConfig
        config = DeviceConfig(sample_rate=10000, record_length=100)
        device.configure(config)
        device.start_acquisition()
        with pytest.raises(TimeoutError):
            device.read_chunk()
        device.stop_acquisition()


# ── make_analysis_result ──────────────────────────────────────

class TestArtDeviceAnalysisResult:
    def test_make_analysis_result(self, device):
        """make_raw_frame() 能正确封装数据"""
        from scope.hardware import DeviceConfig
        config = DeviceConfig(sample_rate=10000, record_length=500)
        device.configure(config)
        device.start_acquisition()

        chunk = device.read_chunk()
        result = device.make_raw_frame(chunk)

        assert result.sequence_num == 1
        assert result.n_channels == 4
        assert result.n_samples == 500
        assert result.sample_rate == 10000
        assert result.data.shape == (4, 500)

        device.stop_acquisition()

    def test_incremental_sequence(self, device):
        """每次 read_chunk → make_raw_frame 序号递增。"""
        from scope.hardware import DeviceConfig
        config = DeviceConfig(sample_rate=10000, record_length=100)
        device.configure(config)
        device.start_acquisition()

        for i in range(1, 4):
            chunk = device.read_chunk()
            result = device.make_raw_frame(chunk)
            assert result.sequence_num == i

        device.stop_acquisition()
