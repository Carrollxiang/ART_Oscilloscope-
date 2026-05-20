"""
ART USB 采集卡设备驱动 — 基于 artdaq 库

将 artdaq (NI-DAQmx 兼容封装) 适配到 AcquisitionDevice 接口。
直接使用 artdaq.Task API，避免 artdaq_main.py 的全局 task 冲突。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

import numpy as np

from scope.hardware.device import (
    AcquisitionDevice,
    DeviceConfig,
    DeviceInfo,
    DeviceHealthEvent,
)
from scope.model import TriggerInfo

logger = logging.getLogger(__name__)

DEFAULT_DEVICE = "Dev42"
DEFAULT_AI_CHANNELS = "ai0:15"
DEFAULT_RATE = 30_000
DEFAULT_SAMPLES = 5000
DEFAULT_TIMEOUT = 5.0


class ArtDevice(AcquisitionDevice):
    """
    ART USB 采集卡设备驱动。

    直接使用 artdaq.Task API (NI-DAQmx 兼容)。
    每个 ArtDevice 实例管理自己的 Task，无全局状态。
    """

    def __init__(
        self,
        device_name: str = DEFAULT_DEVICE,
        ai_channels: str = DEFAULT_AI_CHANNELS,
        terminal_config: str = "NRSE",
        min_val: float = -10.0,
        max_val: float = 10.0,
        trigger_source: str = "ai12",
        trigger_level: float = 1.0,
        trigger_slope: str = "rising",
    ):
        super().__init__()

        self._device_name = device_name
        self._ai_channels = ai_channels
        self._terminal_config = terminal_config
        self._min_val = min_val
        self._max_val = max_val
        self._trigger_source = trigger_source
        self._trigger_level = trigger_level
        self._trigger_slope = trigger_slope

        self._task = None
        self._config: Optional[DeviceConfig] = None
        self._info = DeviceInfo(
            vendor_id=0xFFFF,
            product_id=0x0002,
            serial_number="ART-USB-001",
            channel_count=4,
            resolution_bits=16,
            max_sample_rate=250_000,
            firmware_version="artdaq-1.0",
        )
        self._running = False
        self._seq = 0
        self._read_timeout = DEFAULT_TIMEOUT

        # 事件驱动采集
        self._done_event = threading.Event()
        self._acquire_thread: Optional[threading.Thread] = None
        self._data_callback: Optional[Callable[[np.ndarray], None]] = None

    # ── 生命周期 ────────────────────────────────────────────────

    def open(self) -> bool:
        """加载 artdaq 库。"""
        try:
            import artdaq as _artdaq
            self._artdaq = _artdaq
            from artdaq.constants import (
                AcquisitionType, TerminalConfiguration,
                Slope, Edge, WAIT_INFINITELY,
            )
            self._AcquisitionType = AcquisitionType
            self._TerminalConfiguration = TerminalConfiguration
            self._Slope = Slope
            self._Edge = Edge
            self._WAIT_INFINITELY = WAIT_INFINITELY
        except ImportError:
            logger.error("artdaq 未安装, 请确认 Art_DAQ 驱动已安装")
            return False
        except OSError as e:
            logger.error(f"加载 Art_DAQ.dll 失败: {e}")
            return False

        logger.info(
            f"ArtDevice 已创建: {self._device_name}/{self._ai_channels}, "
            f"{self._min_val}~{self._max_val}V"
        )
        return True

    def set_data_callback(self, callback: Callable[[np.ndarray], None]):
        """设置数据就绪回调 (线程安全: 从采集线程调用, 需 emit Qt signal)。"""
        self._data_callback = callback

    def close(self):
        """关闭 Task。"""
        self._running = False
        self._done_event.set()  # 唤醒 worker 使其退出
        if self._acquire_thread and self._acquire_thread.is_alive():
            self._acquire_thread.join(timeout=2.0)
        self._close_task()
        logger.info("ArtDevice 已关闭")

    def start_acquisition(self):
        """
        配置并启动采集。

        按 NI-DAQmx 顺序:
          1. 创建 Task
          2. 添加 AI 电压通道
          3. 配置采样时钟
          4. 配置硬件触发 (可选)
          5. 启动 Task
        """
        cfg = self._config
        if cfg is None:
            raise RuntimeError("请先调用 configure()")

        self._close_task()

        try:
            task = self._artdaq.Task()
            self._task = task

            # 1. 添加 AI 电压通道 (逐通道设置独立量程)
            term_cfg = self._terminal_config.upper()
            term_map = {
                "DEFAULT": self._TerminalConfiguration.DEFAULT,
                "RSE": self._TerminalConfiguration.RSE,
                "NRSE": self._TerminalConfiguration.NRSE,
                "DIFFERENTIAL": self._TerminalConfiguration.DIFFERENTIAL,
                "PSEUDODIFFERENTIAL": self._TerminalConfiguration.PSEUDODIFFERENTIAL,
            }
            term = term_map.get(term_cfg, self._TerminalConfiguration.NRSE)
            n_ch = len(cfg.channels_enabled)

            # 确保量程数组长度匹配
            while len(cfg.channel_min_vals) < n_ch:
                cfg.channel_min_vals.append(-10.0)
            while len(cfg.channel_max_vals) < n_ch:
                cfg.channel_max_vals.append(10.0)

            for ch_idx in cfg.channels_enabled:
                ch = f"{self._device_name}/ai{ch_idx}"
                task.ai_channels.add_ai_voltage_chan(
                    ch,
                    terminal_config=term,
                    min_val=cfg.channel_min_vals[ch_idx],
                    max_val=cfg.channel_max_vals[ch_idx],
                )

            # 2. 采样时钟 — 有限点采集，由硬件触发或 QTimer 驱动
            task.timing.cfg_samp_clk_timing(
                rate=cfg.sample_rate,
                sample_mode=self._AcquisitionType.FINITE,
                samps_per_chan=cfg.record_length,
            )

            # 3. 硬件触发 (可选)
            if self._trigger_source:
                trig_ch = f"{self._device_name}/{self._trigger_source}"
                slope = (
                    self._Slope.RISING
                    if self._trigger_slope == "rising"
                    else self._Slope.FALLING
                )
                task.triggers.start_trigger.cfg_anlg_edge_start_trig(
                    trigger_source=trig_ch,
                    trigger_slope=slope,
                    trigger_level=self._trigger_level,
                )
                logger.info(
                    f"硬件触发: src={trig_ch}, "
                    f"level={self._trigger_level}V, "
                    f"slope={self._trigger_slope}"
                )

            # 4. 注册 DONE 事件回调 (硬件触发 → 采集完成 → 回调)
            task.register_done_event(self._on_task_done)

            # 5. 启动
            task.start()
            self._running = True

            # 6. 启动采集工作线程 (等待 DONE 事件 → 读取 → 回调 → rearm)
            if self._acquire_thread is None or not self._acquire_thread.is_alive():
                self._acquire_thread = threading.Thread(
                    target=self._acquire_worker,
                    daemon=True,
                    name="art-acquire",
                )
                self._acquire_thread.start()

            logger.info(
                f"采集已启动: {cfg.sample_rate/1e3:.1f}kSa/s, "
                f"{cfg.record_length}samples/ch, "
                f"{len(cfg.channels_enabled)}ch"
            )

        except Exception as e:
            self._running = False
            logger.error(f"启动采集失败: {e}")
            raise

    def stop_acquisition(self):
        """停止采集。"""
        self._running = False
        self._done_event.set()  # 唤醒 worker
        if self._task is not None:
            try:
                self._task.stop()
            except Exception as e:
                logger.warning(f"stop_task 出错: {e}")
        logger.info("采集已停止")

    # ── 数据流 ─────────────────────────────────────────────────

    def read_chunk(self) -> np.ndarray:
        """
        从 ART 卡读取一帧数据。

        Returns:
            ndarray, shape=(channels, samples), dtype=float32, 单位: 伏特

        Raises:
            TimeoutError: 读超时
        """
        if not self._running or self._task is None:
            raise RuntimeError("采集未运行")

        cfg = self._config
        n_samples = cfg.record_length

        try:
            # artdaq.Task.read() 返回 list of lists:
            #   [[ch1_s1, ch1_s2, ...], [ch2_s1, ch2_s2, ...]]
            raw_data = self._task.read(
                number_of_samples_per_channel=n_samples,
                timeout=self._read_timeout,
            )
        except Exception as e:
            raise TimeoutError(f"ART 读取超时: {e}") from e

        # list of lists → numpy array (channels, samples), float32
        data = np.array(raw_data, dtype=np.float32)
        if data.ndim == 1:
            data = data.reshape(1, -1)

        self._seq += 1
        return data

    def make_analysis_result(self, chunk: np.ndarray) -> "AnalysisResult":
        """将原始数据组装成 AnalysisResult。"""
        from scope.model import AnalysisResult, ChannelData

        cfg = self._config
        n_ch = chunk.shape[0]
        n_samples = chunk.shape[1]
        fs = cfg.sample_rate
        now = time.monotonic()

        t = np.arange(n_samples, dtype=np.float64) / fs
        channels = {}
        for ch_idx in range(n_ch):
            name = f"CH{ch_idx + 1}"
            max_val = cfg.channel_max_vals[ch_idx] if ch_idx < len(cfg.channel_max_vals) else 10.0
            channels[name] = ChannelData(
                raw=chunk[ch_idx].copy(),
                time_axis=t.copy(),
                sample_rate=fs,
                resolution=self._info.resolution_bits,
                vertical_scale=max_val,
                vertical_offset=0.0,
                enabled=ch_idx in cfg.channels_enabled,
            )

        return AnalysisResult(
            sequence_num=self._seq,
            trigger=TriggerInfo(
                trigger_type="edge",
                trigger_source=0,
                trigger_level=self._trigger_level,
                trigger_slope=self._trigger_slope,
                trigger_position=0.5,
                trigger_timestamp=now,
            ),
            channels=channels,
        )

    def rearm(self):
        """
        重新触发采集 (FINITE 模式 + 硬件触发)。

        FINITE 模式下每帧采集完成后 Task 自动停止。
        重建整个 Task 以重新等待下一次硬件触发。
        """
        if not self._running:
            return
        try:
            self._close_task()
            self.start_acquisition()
        except Exception as e:
            logger.error(f"rearm 失败: {e}")

    # ── 配置 ───────────────────────────────────────────────────

    def configure(self, config: DeviceConfig):
        self._config = config
        n_ch = len(config.channels_enabled)
        while len(self._config.vertical_ranges) < n_ch:
            self._config.vertical_ranges.append(5.0)
        logger.info(
            f"ArtDevice 已配置: {n_ch}ch, "
            f"{config.sample_rate/1e3:.1f}kSa/s"
        )

    def get_config(self) -> DeviceConfig:
        return self._config or DeviceConfig()

    # ── Watchdog 支持 ──────────────────────────────────────────

    def ping(self) -> bool:
        """探活: 读取 1 个样本来验证设备在线。"""
        if self._task is None:
            return False
        try:
            self._task.read(number_of_samples_per_channel=1, timeout=1.0)
            return True
        except Exception:
            return False

    def reset(self) -> bool:
        """关闭并重建 Task。"""
        try:
            self._close_task()
            self._task = self._artdaq.Task()
            logger.info("USB reset OK")
            return True
        except Exception as e:
            logger.error(f"USB reset 失败: {e}")
            return False

    def restore_state(self, config: DeviceConfig):
        self._config = config
        try:
            self.start_acquisition()
            logger.info("状态已恢复")
        except Exception as e:
            logger.error(f"状态恢复失败: {e}")
            raise

    # ── 内部 ───────────────────────────────────────────────────

    def _on_task_done(self, task_handle, status, callback_data):
        """
        NI-DAQmx DONE 事件回调 (在 DLL 线程中执行)。

        仅设置 Event 通知采集工作线程, 不在回调中做任何耗时操作。
        """
        self._done_event.set()
        return 0

    def _acquire_worker(self):
        """
        采集工作线程: 等待硬件触发 → DONE 事件 → 读取数据 → 回调 → rearm。

        无轮询, 完全事件驱动。无触发信号时线程挂起在 Event.wait()。
        """
        while self._running:
            self._done_event.wait(timeout=0.5)  # 0.5s 心跳, 防止死等
            if not self._running:
                return
            self._done_event.clear()

            try:
                chunk = self.read_chunk()
            except Exception as e:
                logger.error(f"读取失败: {e}")
                continue

            # 回调通知上层 (线程安全: 上层应 emit Qt signal)
            if self._data_callback:
                self._data_callback(chunk)

            # rearm: 重建 Task, 注册新 DONE 回调, 等待下一次触发
            self.rearm()

    def _close_task(self):
        if self._task is not None:
            try:
                self._task.close()
            except Exception:
                pass
            self._task = None
