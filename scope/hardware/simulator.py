"""
模拟采集设备 — 在没有真实硬件时开发上层逻辑

特性:
- 生成正弦波 / 方波 / 三角波 / 噪声
- 可配置通道数、采样率、频率、幅值
- 支持故障注入: 随机断流、模拟掉线 (用于测试 Watchdog)
"""

from __future__ import annotations

import time
import random
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from scope.model import TriggerInfo, ChannelData
from .device import AcquisitionDevice, DeviceConfig, DeviceInfo, DeviceHealthEvent


@dataclass
class SimSignalConfig:
    """每个通道的信号发生器参数"""
    waveform: str = "sine"        # "sine" | "square" | "triangle" | "noise" | "dc"
    frequency: float = 1000.0     # Hz
    amplitude: float = 2.0        # Vpp
    offset: float = 0.0           # 直流偏置
    noise_level: float = 0.0      # 叠加噪声 (Vrms)


class SimulatorDevice(AcquisitionDevice):
    """
    模拟采集设备

    用法:
        device = SimulatorDevice()
        device.open()
        device.configure(DeviceConfig(sample_rate=1_000_000, record_length=10000))
        device.start_acquisition()
        while True:
            chunk = device.read_chunk()  # (4, 10000) float32
    """

    def __init__(self):
        super().__init__()
        self._info = DeviceInfo(
            vendor_id=0xFFFF,
            product_id=0x0001,
            serial_number="SIM-0001",
            channel_count=4,
            resolution_bits=12,
            max_sample_rate=10_000_000,
            firmware_version="simulator-1.0",
        )
        self._config: Optional[DeviceConfig] = None
        self._running = False
        self._seq = 0

        # 每通道信号参数
        self._signals: list[SimSignalConfig] = [
            SimSignalConfig(waveform="sine",     frequency=1000.0, amplitude=2.0),
            SimSignalConfig(waveform="square",   frequency=1000.0, amplitude=3.3),
            SimSignalConfig(waveform="triangle", frequency=500.0,  amplitude=1.5),
            SimSignalConfig(waveform="noise",    amplitude=0.5),
        ]

        # 故障注入
        self._fail_on_read_every_n: int = 0     # 0 = 不注入故障
        self._fail_read_counter: int = 0
        self._ping_fail: bool = False
        self._reset_fail: bool = False

        # 模拟时间基准
        self._sim_start_time: float = 0.0
        self._sim_sample_count: int = 0

    # ── 生命周期 ────────────────────────────────────────────────

    def open(self) -> bool:
        self._sim_start_time = time.monotonic()
        self._sim_sample_count = 0
        self._fire_health("healthy", 0, "模拟设备已就绪")
        return True

    def close(self):
        self._running = False
        self._fire_health("healthy", 0, "模拟设备已关闭")

    def start_acquisition(self):
        self._running = True
        self._sim_sample_count = 0
        self._fire_health("healthy", 0, "采集已启动")

    def stop_acquisition(self):
        self._running = False
        self._fire_health("healthy", 0, "采集已停止")

    # ── 数据流 ─────────────────────────────────────────────────

    def read_chunk(self) -> np.ndarray:
        """
        生成一帧模拟数据。
        返回 (ch, samples) 的 float32 数组, 单位: 伏特。
        """
        if not self._config:
            raise RuntimeError("设备未配置, 请先调用 configure()")

        # 故障注入: 模拟断流
        if self._fail_on_read_every_n > 0:
            self._fail_read_counter += 1
            if self._fail_read_counter >= self._fail_on_read_every_n:
                self._fail_read_counter = 0
                raise TimeoutError("[故障注入] 模拟读取超时")

        n_ch = len(self._config.channels_enabled)
        n_samples = self._config.record_length
        fs = self._config.sample_rate

        chunk = np.zeros((n_ch, n_samples), dtype=np.float32)
        t = np.arange(n_samples, dtype=np.float64) / fs

        for ch_idx in range(n_ch):
            sig = self._signals[ch_idx]
            if sig.waveform == "sine":
                chunk[ch_idx] = self._generate_sine(t, sig)
            elif sig.waveform == "square":
                chunk[ch_idx] = self._generate_square(t, sig)
            elif sig.waveform == "triangle":
                chunk[ch_idx] = self._generate_triangle(t, sig)
            elif sig.waveform == "noise":
                chunk[ch_idx] = self._generate_noise(n_samples, sig)
            else:  # dc
                chunk[ch_idx] = np.full(n_samples, sig.offset, dtype=np.float32)

        self._sim_sample_count += n_samples
        self._seq += 1

        # 模拟极小的采集延迟 (< 1ms)
        time.sleep(0.0005)

        return chunk

    def make_analysis_result(self, chunk: np.ndarray) -> "AnalysisResult":
        """
        将 read_chunk 返回的原始数据组装成 AnalysisResult。
        这是采集线程在收到 USB 数据后做的事情; 模拟器也提供这个方法
        方便上层在不连接真实设备时调试。
        """
        from scope.model import AnalysisResult, TriggerInfo, ChannelData

        n_ch = len(self._config.channels_enabled)
        n_samples = self._config.record_length
        fs = self._config.sample_rate

        t = np.arange(n_samples, dtype=np.float64) / fs
        now = time.monotonic()

        channels = {}
        for ch_idx in range(n_ch):
            name = f"CH{ch_idx + 1}"
            channels[name] = ChannelData(
                raw=chunk[ch_idx].copy(),
                time_axis=t.copy(),
                sample_rate=fs,
                resolution=self._info.resolution_bits,
                vertical_scale=self._config.vertical_ranges[ch_idx],
                vertical_offset=0.0,
                enabled=ch_idx in self._config.channels_enabled,
            )

        return AnalysisResult(
            sequence_num=self._seq,
            trigger=TriggerInfo(
                trigger_type="immediate",
                trigger_source=0,
                trigger_level=0.0,
                trigger_slope="rising",
                trigger_position=0.5,
                trigger_timestamp=now,
            ),
            channels=channels,
        )

    # ── 配置 ───────────────────────────────────────────────────

    def configure(self, config: DeviceConfig):
        self._config = config

    def get_config(self) -> DeviceConfig:
        if not self._config:
            return DeviceConfig()
        return self._config

    # ── Watchdog 支持 ──────────────────────────────────────────

    def ping(self) -> bool:
        return not self._ping_fail

    def reset(self) -> bool:
        if self._reset_fail:
            return False
        self._fire_health("resetting", 1, "模拟 USB 重置...")
        time.sleep(0.3)  # 模拟重置耗时
        return True

    def restore_state(self, config: DeviceConfig):
        self.configure(config)
        self._sim_sample_count = 0
        self._fire_health("healthy", 0, "状态已恢复")

    # ── 故障注入控制 (仅模拟器) ─────────────────────────────────

    def inject_read_failure(self, every_n_reads: int = 10):
        """每 N 次 read_chunk 抛一次 TimeoutError"""
        self._fail_on_read_every_n = every_n_reads
        self._fail_read_counter = 0

    def inject_ping_failure(self, fail: bool = True):
        """ping 始终返回 False"""
        self._ping_fail = fail

    def inject_reset_failure(self, fail: bool = True):
        """reset 始终返回 False"""
        self._reset_fail = fail

    def clear_faults(self):
        """清除所有故障注入"""
        self._fail_on_read_every_n = 0
        self._ping_fail = False
        self._reset_fail = False

    def set_channel_signal(self, ch_idx: int, sig: SimSignalConfig):
        """运行时修改某通道的信号参数"""
        self._signals[ch_idx] = sig

    # ── 内部 helper ────────────────────────────────────────────

    def _generate_sine(self, t: np.ndarray, sig: SimSignalConfig) -> np.ndarray:
        data = (sig.amplitude / 2) * np.sin(2 * np.pi * sig.frequency * t)
        data += sig.offset
        if sig.noise_level > 0:
            data += np.random.normal(0, sig.noise_level, len(t))
        return data.astype(np.float32)

    def _generate_square(self, t: np.ndarray, sig: SimSignalConfig) -> np.ndarray:
        data = (sig.amplitude / 2) * np.sign(np.sin(2 * np.pi * sig.frequency * t))
        data += sig.offset
        if sig.noise_level > 0:
            data += np.random.normal(0, sig.noise_level, len(t))
        return data.astype(np.float32)

    def _generate_triangle(self, t: np.ndarray, sig: SimSignalConfig) -> np.ndarray:
        data = (sig.amplitude / 2) * (
            2 * np.abs(2 * (t * sig.frequency - np.floor(t * sig.frequency + 0.5))) - 1
        )
        data += sig.offset
        if sig.noise_level > 0:
            data += np.random.normal(0, sig.noise_level, len(t))
        return data.astype(np.float32)

    def _generate_noise(self, n: int, sig: SimSignalConfig) -> np.ndarray:
        return np.random.normal(sig.offset, sig.amplitude / 2, n).astype(np.float32)

    def _fire_health(self, state: str, attempt: int, message: str):
        if self._on_health_event:
            self._on_health_event(DeviceHealthEvent(state, attempt, message))
