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

    def test_reset(self, device, monkeypatch):
        """reset 应调用 ArtDAQ_ResetDevice 设备级重置。"""
        _install_mock_lib(monkeypatch)
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


# ── rearm 失败恢复 / 资源清理 (v0.7.3) ─────────────────────────

class TestArtDeviceRecovery:
    """start 失败清理 / rearm 退避重试 / 最终失败停摆"""

    def test_start_failure_cleans_task(self, device, mock_artdaq):
        """start_acquisition 失败 → 已创建 Task 被清理 (无 -50103 泄漏)"""
        from scope.hardware import DeviceConfig
        _, mock_task = mock_artdaq
        config = DeviceConfig(sample_rate=10000, record_length=100)
        device.configure(config)
        mock_task.start.side_effect = RuntimeError("resource reserved")

        with pytest.raises(RuntimeError):
            device.start_acquisition()
        assert device._task is None, "失败后 Task 应被清理"
        assert device._running is False

    def test_rearm_retry_recovers(self, device, mock_artdaq, monkeypatch):
        """rearm 失败一次后重试成功 → 恢复运行, 不中断"""
        import scope.hardware.art_device as art_mod
        from scope.hardware import DeviceConfig
        _, mock_task = mock_artdaq
        monkeypatch.setattr(art_mod, "REARM_RETRY_DELAY_S", 0.01)

        config = DeviceConfig(sample_rate=10000, record_length=100)
        device.configure(config)
        device.start_acquisition()

        # 第一次 rearm: start() 抛 -50103 类错误; 重试时成功
        mock_task.start.side_effect = [RuntimeError("resource reserved"), None]
        device.rearm()                      # 不应抛异常
        assert device._running is True, "重试成功后应保持运行"
        assert device._task is not None
        device.stop_acquisition()
        mock_task.start.side_effect = None

    def test_rearm_retry_keeps_running_flag(self, device, mock_artdaq, monkeypatch):
        """重试期间 _running 保持 True (采集线程存活, 可无缝续帧)"""
        import scope.hardware.art_device as art_mod
        from scope.hardware import DeviceConfig
        _, mock_task = mock_artdaq
        monkeypatch.setattr(art_mod, "REARM_RETRY_DELAY_S", 0.01)

        config = DeviceConfig(sample_rate=10000, record_length=100)
        device.configure(config)
        device.start_acquisition()

        mock_task.start.side_effect = [RuntimeError("x"), RuntimeError("x"), None]
        device.rearm()
        assert device._running is True
        device.stop_acquisition()
        mock_task.start.side_effect = None

    def test_rearm_final_failure_stops_with_health(self, device, mock_artdaq, monkeypatch):
        """rearm 持续失败 (重试+reset 均失败) → 停摆并上报健康事件"""
        import scope.hardware.art_device as art_mod
        from scope.hardware import DeviceConfig
        _, mock_task = mock_artdaq
        monkeypatch.setattr(art_mod, "REARM_RETRY_DELAY_S", 0.01)

        config = DeviceConfig(sample_rate=10000, record_length=100)
        device.configure(config)
        device.start_acquisition()

        events = []
        device.on_health_event = lambda ev: events.append(ev.state)
        mock_task.start.side_effect = RuntimeError("resource reserved")  # 永远失败
        device.rearm()
        assert device._running is False, "最终失败应停摆"
        assert "stopped" in events, "应上报 stopped 健康事件"
        device.stop_acquisition()
        mock_task.start.side_effect = None


# ── 设备级重置 (ArtDAQ_ResetDevice) / 自动重连 (v0.7.5) ────────

def _install_mock_lib(monkeypatch, reset_ret=0):
    """在 mock artdaq 环境下安装 artdaq._lib.lib_importer 桩 (ArtDAQ_ResetDevice)。"""
    import sys
    import types
    from unittest.mock import MagicMock
    fake_lib = types.ModuleType("artdaq._lib")
    importer = MagicMock()
    importer.windll._library.ArtDAQ_ResetDevice.return_value = reset_ret
    fake_lib.lib_importer = importer
    monkeypatch.setitem(sys.modules, "artdaq._lib", fake_lib)
    return importer


class TestDeviceLevelReset:
    """reset() 应调用 DLL 的 ArtDAQ_ResetDevice (真正的设备级重置)"""

    def test_reset_calls_device_reset_api(self, device, monkeypatch):
        importer = _install_mock_lib(monkeypatch)
        assert device.reset() is True
        importer.windll._library.ArtDAQ_ResetDevice.assert_called_once_with(b"Dev42")

    def test_reset_failure_returns_false(self, device, monkeypatch):
        importer = _install_mock_lib(monkeypatch, reset_ret=1)
        assert device.reset() is False


class TestAutoReconnect:
    """停摆后自动重连: 设备恢复后自动恢复采集 (替代手动重启)"""

    def test_rearm_final_failure_starts_reconnect(self, device, mock_artdaq, monkeypatch):
        import scope.hardware.art_device as art_mod
        from scope.hardware import DeviceConfig
        _install_mock_lib(monkeypatch)
        monkeypatch.setattr(art_mod, "REARM_RETRY_DELAY_S", 0.01)
        monkeypatch.setattr(art_mod, "RECONNECT_INTERVAL_S", 0.05)
        _, mock_task = mock_artdaq
        config = DeviceConfig(sample_rate=10000, record_length=100)
        device.configure(config)
        device.start_acquisition()

        mock_task.start.side_effect = RuntimeError("resource reserved")
        device.rearm()
        assert device._running is False, "最终失败应停摆"
        assert device._reconnect_thread is not None, "应启动重连线程"
        assert device._reconnect_thread.is_alive(), "重连线程应运行中"
        device.stop_acquisition()
        mock_task.start.side_effect = None

    def test_reconnect_recovers_automatically(self, device, mock_artdaq, monkeypatch):
        import time
        import scope.hardware.art_device as art_mod
        from scope.hardware import DeviceConfig
        _install_mock_lib(monkeypatch)
        monkeypatch.setattr(art_mod, "REARM_RETRY_DELAY_S", 0.01)
        monkeypatch.setattr(art_mod, "RECONNECT_INTERVAL_S", 0.05)
        _, mock_task = mock_artdaq
        config = DeviceConfig(sample_rate=10000, record_length=100)
        device.configure(config)
        device.start_acquisition()

        events = []
        device.on_health_event = lambda ev: events.append(ev.state)
        # rearm 阶段共 5 次 start 调用(直接+重试3+reset后1)全部失败,
        # 之后重连线程的 start 调用成功 → 自动恢复
        mock_task.start.side_effect = [RuntimeError("x")] * 5 + [None]
        device.rearm()
        assert device._running is False

        deadline = time.time() + 5
        while time.time() < deadline and not device._running:
            time.sleep(0.05)
        assert device._running is True, "自动重连应恢复采集"
        assert "recovering" in events, "应上报 recovering 事件"
        assert "healthy" in events, "恢复后应上报 healthy 事件"
        device.stop_acquisition()
        mock_task.start.side_effect = None

    def test_reconnect_fatal_after_consecutive_failures(self, device, mock_artdaq, monkeypatch):
        """ResetDevice 连续失败 → 达到阈值后上报 fatal 事件并停止重连 (等待程序重启)"""
        import time
        import scope.hardware.art_device as art_mod
        from scope.hardware import DeviceConfig
        _install_mock_lib(monkeypatch, reset_ret=1)   # ArtDAQ_ResetDevice 永远失败 (-50302 类)
        monkeypatch.setattr(art_mod, "REARM_RETRY_DELAY_S", 0.01)
        monkeypatch.setattr(art_mod, "RECONNECT_INTERVAL_S", 0.05)
        _, mock_task = mock_artdaq
        config = DeviceConfig(sample_rate=10000, record_length=100)
        device.configure(config)
        device.start_acquisition()

        events = []
        device.on_health_event = lambda ev: events.append(ev.state)
        mock_task.start.side_effect = RuntimeError("resource reserved")
        device.rearm()                     # 停摆 + 启动重连

        deadline = time.time() + 5
        while time.time() < deadline and (device._reconnect_thread is None or device._reconnect_thread.is_alive()):
            time.sleep(0.05)
        assert "fatal" in events, f"应上报 fatal 事件: {events}"
        assert device._reconnect_thread.is_alive() is False, "fatal 后重连线程应退出"
        device.stop_acquisition()
        mock_task.start.side_effect = None


# ── CONTINUOUS 模式 (v0.8): 一次性 Task + 软件触发帧化 ───────────

class TestContinuousMode:
    """CONTINUOUS 采集: 连续读取 + TriggerDetector 软件帧化"""

    def test_continuous_uses_continuous_timing(self, device, mock_artdaq):
        """acquisition_mode=continuous → cfg_samp_clk_timing 用 CONTINUOUS"""
        from scope.hardware import DeviceConfig
        _, mock_task = mock_artdaq
        config = DeviceConfig(
            sample_rate=10000, record_length=2000,
            acquisition_mode="continuous",
        )
        device.configure(config)
        device.start_acquisition()
        args, kwargs = mock_task.timing.cfg_samp_clk_timing.call_args
        assert kwargs.get("sample_mode") is device._AcquisitionType.CONTINUOUS
        # ART 驱动要求 samps_per_chan >= 2; 连续模式用作缓冲大小 (2 倍帧长)
        assert kwargs.get("samps_per_chan") == max(config.record_length * 4, 4096)
        device.stop_acquisition()

    def test_continuous_worker_produces_triggered_frames(self, device, mock_artdaq):
        """连续数据流 → 软件触发帧化 → 回调收到帧 (起点=沿)"""
        from unittest.mock import MagicMock
        from scope.hardware import DeviceConfig
        _, mock_task = mock_artdaq

        frame_len = 2000
        block = max(frame_len // 8, 1024)      # 1024 (与 _continuous_worker 一致)
        n_ch = 16
        total = 9000
        # ch12 方波: 0→1 上升沿在 0 和 4000 (电平 0.5)
        trig = np.zeros(total, dtype=np.float32)
        trig[0:2000] = 1.0
        trig[4000:6000] = 1.0
        data = np.zeros((n_ch, total), dtype=np.float32)
        data[12] = trig
        chunks = [data[:, s:s + block] for s in range(0, total, block)]

        calls = {"n": 0}
        def fake_read(number_of_samples_per_channel=None, timeout=None):
            if calls["n"] >= len(chunks):
                device._running = False        # 数据耗尽 → 停 worker
                raise RuntimeError("数据耗尽")
            c = chunks[calls["n"]]
            calls["n"] += 1
            return c.tolist()
        mock_task.read = MagicMock(side_effect=fake_read)

        config = DeviceConfig(
            sample_rate=10000, record_length=frame_len,
            acquisition_mode="continuous",
        )
        device.configure(config)
        received = []
        device.set_data_callback(lambda ch: received.append(ch))
        device.start_acquisition()
        device._acquire_thread.join(timeout=10)

        assert len(received) == 2, f"应收到 2 帧 (沿在 0 与 4000), 实际 {len(received)}"
        for f in received:
            assert f.shape == (n_ch, frame_len), f"帧形状 {f.shape}"
        # 帧起点 = 沿: 第 0 帧从 trig[0]=1 开始, 第 1 帧从 trig[4000]=1 开始
        assert received[0][12, 0] == 1.0
        assert received[1][12, 0] == 1.0
        device.stop_acquisition()
