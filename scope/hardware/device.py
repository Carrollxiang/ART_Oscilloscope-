"""
硬件抽象层 — AcquisitionDevice 基类

所有采集设备 (真实 ART USB 卡或模拟器) 实现此接口。
上位机开发期间使用 SimulatorDevice, 硬件就绪后实现 ArtUsbDevice。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np


@dataclass
class DeviceConfig:
    """采集设备配置参数"""
    sample_rate: float = 1_000_000       # 采样率 (Sa/s)
    channels_enabled: list[int] = field(default_factory=lambda: [0, 1, 2, 3])
    vertical_ranges: list[float] = field(default_factory=lambda: [5.0, 5.0, 5.0, 5.0])
    trigger_source: int = 0
    trigger_level: float = 0.0
    trigger_slope: str = "rising"
    record_length: int = 10_000          # 每帧采样点


@dataclass
class DeviceInfo:
    """设备信息 — 由设备上报"""
    vendor_id: int = 0
    product_id: int = 0
    serial_number: str = ""
    channel_count: int = 4
    resolution_bits: int = 12
    max_sample_rate: float = 10_000_000
    firmware_version: str = ""


@dataclass
class DeviceHealthEvent:
    """设备健康状态变更事件"""
    state: str       # DeviceHealthState 的 value
    attempt: int     # 当前重连尝试次数
    message: str     # 人类可读描述


class AcquisitionDevice(ABC):
    """
    采集设备抽象基类

    关键设计:
    - open/close 配对使用
    - start/stop 控制采集流
    - read_chunk 是同步阻塞调用 (由独立线程执行)
    """

    def __init__(self):
        self._config: Optional[DeviceConfig] = None
        self._info: Optional[DeviceInfo] = None
        self._on_health_event: Optional[Callable[[DeviceHealthEvent], None]] = None

    # ── 生命周期 ────────────────────────────────────────────────

    @abstractmethod
    def open(self) -> bool:
        """打开设备连接"""
        ...

    @abstractmethod
    def close(self):
        """关闭设备连接"""
        ...

    @abstractmethod
    def start_acquisition(self):
        """开始采集"""
        ...

    @abstractmethod
    def stop_acquisition(self):
        """停止采集"""
        ...

    # ── 数据流 ─────────────────────────────────────────────────

    @abstractmethod
    def read_chunk(self) -> np.ndarray:
        """
        读取一块数据。
        返回 shape=(channels, samples) 的 float32 numpy 数组, 单位: 伏特。
        同步阻塞调用, 应在独立线程中执行。
        """
        ...

    # ── 配置 ───────────────────────────────────────────────────

    @abstractmethod
    def configure(self, config: DeviceConfig):
        """
        应用设备配置。
        可在运行中调用 (实时调整采样率、垂直档位等)。
        """
        ...

    @abstractmethod
    def get_config(self) -> DeviceConfig:
        """获取当前配置"""
        ...

    # ── Watchdog 支持 ──────────────────────────────────────────

    @abstractmethod
    def ping(self) -> bool:
        """
        探活。向设备发送短命令, 验证通信链路正常。
        返回 True 表示设备响应正常。
        """
        ...

    @abstractmethod
    def reset(self) -> bool:
        """
        USB 级重置。不涉及上层状态恢复。
        调用后设备应重新可枚举/可打开。
        """
        ...

    @abstractmethod
    def restore_state(self, config: DeviceConfig):
        """
        在 reset 或 reconnect 后, 恢复采集状态。
        将之前保存的 DeviceConfig 重新下发。
        """
        ...

    # ── 属性 ───────────────────────────────────────────────────

    @property
    def info(self) -> Optional[DeviceInfo]:
        """设备信息"""
        return self._info

    @property
    def config(self) -> Optional[DeviceConfig]:
        """当前配置"""
        return self._config

    # ── 事件 ───────────────────────────────────────────────────

    @property
    def on_health_event(self) -> Optional[Callable[[DeviceHealthEvent], None]]:
        return self._on_health_event

    @on_health_event.setter
    def on_health_event(self, callback: Optional[Callable[[DeviceHealthEvent], None]]):
        self._on_health_event = callback
