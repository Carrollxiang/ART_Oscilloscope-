"""
模拟采集设备 — 事件驱动模式，统一与真实硬件的数据流

特性:
- 生成正弦波 / 方波 / 三角波 / 噪声
- 预生成多帧数据，循环播放（可复现、可调试）
- 事件驱动：内置触发线程，模拟硬件触发行为
- 与 ArtDevice 接口一致，上层代码无需区分
- 支持故障注入: 随机断流、模拟掉线 (用于测试)
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

from scope.model import RawFrame
from .device import AcquisitionDevice, DeviceConfig, DeviceInfo, DeviceHealthEvent

logger = logging.getLogger(__name__)


@dataclass
class SimSignalConfig:
    """每个通道的信号发生器参数"""
    waveform: str = "sine"
    frequency: float = 1000.0
    amplitude: float = 2.0
    offset: float = 0.0
    noise_level: float = 0.0


class SimulatorDevice(AcquisitionDevice):
    """
    模拟采集设备 — 事件驱动模式。
    
    与 ArtDevice 接口完全一致：
      - set_data_callback(): 注册数据就绪回调
      - start_acquisition(): 启动内部触发线程
      - stop_acquisition(): 停止触发线程
      
    内部使用线程模拟硬件触发，定时调用 callback 推送数据。
    数据预生成，循环播放，便于调试。
    
    用法:
        device = SimulatorDevice()
        device.open()
        device.configure(config)
        device.set_data_callback(my_callback)
        device.start_acquisition()  # 启动内部触发线程
        # 数据会自动通过 callback 推送
    """

    def __init__(self):
        super().__init__()
        self._info = DeviceInfo(
            vendor_id=0xFFFF,
            product_id=0x0001,
            serial_number="SIM-0001",
            channel_count=16,
            resolution_bits=12,
            max_sample_rate=10_000_000,
            firmware_version="simulator-2.0-event-driven",
        )
        self._config: Optional[DeviceConfig] = None
        self._running = False
        self._seq = 0

        # 每通道信号参数 (自动扩展到实际通道数)
        self._signals: list[SimSignalConfig] = [
            SimSignalConfig(waveform="sine",     frequency=1000.0, amplitude=2.0),
            SimSignalConfig(waveform="square",   frequency=1000.0, amplitude=3.3),
            SimSignalConfig(waveform="triangle", frequency=500.0,  amplitude=1.5),
            SimSignalConfig(waveform="noise",    amplitude=0.5),
        ]

        # 故障注入
        self._fail_on_read_every_n: int = 0
        self._fail_read_counter: int = 0
        self._ping_fail: bool = False
        self._reset_fail: bool = False

        # 预生成帧缓存
        self._frame_cache: list[np.ndarray] = []
        self._frame_index: int = 0
        self._cache_size: int = 10

        # 事件驱动
        self._data_callback: Optional[Callable[[np.ndarray], None]] = None
        self._trigger_thread: Optional[threading.Thread] = None
        self._trigger_interval_ms: float = 500.0

    # ── 事件驱动接口 (与 ArtDevice 一致) ────────────────────────

    def set_data_callback(self, callback: Callable[[np.ndarray], None]):
        """设置数据就绪回调 (与 ArtDevice 接口一致)"""
        self._data_callback = callback
        logger.debug(f"SimulatorDevice: 已设置数据回调")

    # ── 生命周期 ────────────────────────────────────────────────

    def open(self) -> bool:
        self._fire_health("healthy", 0, "模拟设备已就绪")
        logger.info("SimulatorDevice 已打开")
        return True

    def close(self):
        self._running = False
        if self._trigger_thread and self._trigger_thread.is_alive():
            self._trigger_thread.join(timeout=1.0)
        self._fire_health("healthy", 0, "模拟设备已关闭")
        logger.info("SimulatorDevice 已关闭")

    def start_acquisition(self):
        """启动采集 + 预生成数据 + 启动模拟触发线程"""
        if not self._config:
            raise RuntimeError("请先调用 configure()")

        self._running = True
        self._seq = 0
        self._frame_index = 0

        # 预生成帧数据
        self._pregenerate_frames()

        # 启动模拟触发线程
        if self._trigger_thread is None or not self._trigger_thread.is_alive():
            self._trigger_thread = threading.Thread(
                target=self._trigger_worker,
                daemon=True,
                name="sim-trigger",
            )
            self._trigger_thread.start()

        self._fire_health("healthy", 0, "采集已启动 (事件驱动)")
        logger.info(
            f"SimulatorDevice 采集已启动: "
            f"{len(self._config.channels_enabled)}ch, "
            f"{self._config.sample_rate/1e3:.1f}kSa/s, "
            f"触发间隔={self._trigger_interval_ms:.0f}ms"
        )

    def stop_acquisition(self):
        """停止采集"""
        self._running = False
        if self._trigger_thread and self._trigger_thread.is_alive():
            self._trigger_thread.join(timeout=1.0)
        self._trigger_thread = None
        self._fire_health("healthy", 0, "采集已停止")
        logger.info("SimulatorDevice 采集已停止")

    # ── 预生成帧数据 ────────────────────────────────────────────

    def _pregenerate_frames(self):
        """预生成多帧不同的模拟数据，循环播放"""
        self._frame_cache = []
        
        n_ch = len(self._config.channels_enabled)
        n_samples = self._config.record_length
        fs = self._config.sample_rate
        t = np.arange(n_samples, dtype=np.float64) / fs

        # 计算触发间隔（帧时长）
        if fs > 0 and n_samples > 0:
            self._trigger_interval_ms = n_samples / fs * 1000.0

        # 扩展信号配置到实际通道数
        while len(self._signals) < n_ch:
            idx = len(self._signals)
            waveforms = ["sine", "square", "triangle", "noise"]
            freqs = [1000.0, 1000.0, 500.0, 0.0]
            amps = [2.0, 3.3, 1.5, 0.5]
            self._signals.append(SimSignalConfig(
                waveform=waveforms[idx % 4],
                frequency=freqs[idx % 4],
                amplitude=amps[idx % 4],
            ))

        # 预生成 N 帧
        for frame_idx in range(self._cache_size):
            frame = np.zeros((n_ch, n_samples), dtype=np.float32)
            
            # 每帧使用略微不同的参数
            phase_offset = frame_idx * np.pi / 5  # 相位偏移
            freq_factor = 1.0 + 0.02 * frame_idx   # 频率因子
            amp_factor = 1.0 + 0.05 * np.sin(frame_idx * 0.6)  # 幅度因子

            for ch_idx in range(n_ch):
                sig = self._signals[ch_idx]
                frame[ch_idx] = self._generate_with_variation(
                    t, sig, phase_offset, freq_factor, amp_factor, seed=frame_idx * 100 + ch_idx
                )
            
            self._frame_cache.append(frame)

        logger.info(
            f"预生成 {len(self._frame_cache)} 帧模拟数据 "
            f"(每帧 {n_samples} samples, {n_ch} channels)"
        )

    def _generate_with_variation(
        self, 
        t: np.ndarray, 
        sig: SimSignalConfig, 
        phase: float, 
        freq_factor: float, 
        amp_factor: float,
        seed: int = 0
    ) -> np.ndarray:
        """带变化的信号生成"""
        freq = sig.frequency * freq_factor
        amp = sig.amplitude * amp_factor

        if sig.waveform == "sine":
            data = (amp / 2) * np.sin(2 * np.pi * freq * t + phase)
        elif sig.waveform == "square":
            data = (amp / 2) * np.sign(np.sin(2 * np.pi * freq * t + phase))
        elif sig.waveform == "triangle":
            phase_offset = phase / (2 * np.pi)
            data = (amp / 2) * (
                2 * np.abs(2 * (t * freq + phase_offset - np.floor(t * freq + phase_offset + 0.5))) - 1
            )
        elif sig.waveform == "noise":
            rng = np.random.RandomState(seed)
            data = rng.normal(sig.offset, amp / 2, len(t)).astype(np.float32)
            if sig.noise_level > 0:
                data += rng.normal(0, sig.noise_level, len(t))
            return data
        else:  # dc
            data = np.full(len(t), sig.offset, dtype=np.float32)
            return data

        data += sig.offset
        if sig.noise_level > 0:
            rng = np.random.RandomState(seed + 1000)
            data += rng.normal(0, sig.noise_level, len(t))

        return data.astype(np.float32)

    # ── 触发线程 ───────────────────────────────────────────────

    def _trigger_worker(self):
        """模拟硬件触发的线程：定时调用 callback"""
        logger.info("SimulatorDevice 触发线程已启动")
        
        while self._running:
            # 等待触发间隔
            time.sleep(self._trigger_interval_ms / 1000.0)
            
            if not self._running:
                break
            
            try:
                # 从缓存读取
                chunk = self._read_from_cache()
                
                # 调用回调
                if self._data_callback:
                    self._data_callback(chunk)
            except Exception as e:
                logger.error(f"模拟触发错误: {e}", exc_info=True)
        
        logger.info("SimulatorDevice 触发线程已退出")

    def _read_from_cache(self) -> np.ndarray:
        """从预生成缓存读取（循环）"""
        # 故障注入检查
        if self._fail_on_read_every_n > 0:
            self._fail_read_counter += 1
            if self._fail_read_counter >= self._fail_on_read_every_n:
                self._fail_read_counter = 0
                raise TimeoutError("[故障注入] 模拟读取超时")

        if not self._frame_cache:
            raise RuntimeError("帧缓存未初始化")

        frame = self._frame_cache[self._frame_index].copy()
        self._frame_index = (self._frame_index + 1) % len(self._frame_cache)
        self._seq += 1
        
        return frame

    # ── 兼容接口：主动读取 ──────────────────────────────────────

    def read_chunk(self) -> np.ndarray:
        """
        外部主动读取（兼容模式）。
        
        注意：事件驱动模式下不应调用此方法。
        使用 set_data_callback() 注册回调代替。
        """
        if not self._running:
            raise RuntimeError("采集未启动")
        return self._read_from_cache()

    def make_raw_frame(self, chunk: np.ndarray) -> RawFrame:
        """将原始数据组装成 RawFrame"""
        return RawFrame(
            sequence_num=self._seq,
            data=chunk.copy(),
            sample_rate=self._config.sample_rate,
        )

    # ── 配置 ───────────────────────────────────────────────────

    def configure(self, config: DeviceConfig):
        self._config = config
        while len(self._config.vertical_ranges) < len(config.channels_enabled):
            self._config.vertical_ranges.append(5.0)
        logger.debug(
            f"SimulatorDevice 已配置: {len(config.channels_enabled)}ch, "
            f"{config.sample_rate/1e3:.1f}kSa/s"
        )

    def get_config(self) -> DeviceConfig:
        return self._config or DeviceConfig()

    # ── Watchdog 支持 ──────────────────────────────────────────

    def ping(self) -> bool:
        return not self._ping_fail

    def reset(self) -> bool:
        if self._reset_fail:
            return False
        self._fire_health("resetting", 1, "模拟 USB 重置...")
        time.sleep(0.3)
        return True

    def restore_state(self, config: DeviceConfig):
        self.configure(config)
        self._fire_health("healthy", 0, "状态已恢复")

    # ── 故障注入控制 ───────────────────────────────────────────

    def inject_read_failure(self, every_n_reads: int = 10):
        self._fail_on_read_every_n = every_n_reads
        self._fail_read_counter = 0

    def inject_ping_failure(self, fail: bool = True):
        self._ping_fail = fail

    def inject_reset_failure(self, fail: bool = True):
        self._reset_fail = fail

    def clear_faults(self):
        self._fail_on_read_every_n = 0
        self._ping_fail = False
        self._reset_fail = False

    def set_channel_signal(self, ch_idx: int, sig: SimSignalConfig):
        if ch_idx < len(self._signals):
            self._signals[ch_idx] = sig

    def set_cache_size(self, size: int):
        """设置预生成帧数量（需在 start_acquisition 前调用）"""
        self._cache_size = max(1, size)

    # ── 内部 ───────────────────────────────────────────────────

    def _fire_health(self, state: str, attempt: int, message: str):
        if self._on_health_event:
            self._on_health_event(DeviceHealthEvent(state, attempt, message))
