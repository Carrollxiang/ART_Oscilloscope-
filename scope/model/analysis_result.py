"""
分析结果数据结构 — 系统的黄金数据包

每个硬件触发事件产生一个 AnalysisResult 实例,
贯穿采集 → 分析 → 显示 → 反馈 的全链路。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np


@dataclass
class TriggerInfo:
    """本次触发事件的元信息"""

    trigger_type: str          # "edge" | "immediate"
    trigger_source: int        # 触发源通道索引 (0-based)
    trigger_level: float       # 触发电平 (伏特)
    trigger_slope: str         # "rising" | "falling"
    trigger_position: float    # 触发点在帧中的位置 (0~1, 通常 0.5)
    trigger_timestamp: float   # 绝对时间戳, time.monotonic() 基准

    @classmethod
    def immediate(cls) -> TriggerInfo:
        """无触发模式 (自动/立即触发)"""
        return cls(
            trigger_type="immediate",
            trigger_source=0,
            trigger_level=0.0,
            trigger_slope="rising",
            trigger_position=0.5,
            trigger_timestamp=0.0,
        )


@dataclass
class ChannelData:
    """单个通道的一帧数据"""

    raw: np.ndarray            # 电压值数组 (float), 长度 = 本次触发捕获的样本数
    time_axis: np.ndarray      # 相对时间轴 (秒), 长度同 raw
    sample_rate: float         # 实际采样率 (Sa/s)
    resolution: int            # ADC 位宽
    vertical_scale: float      # V/div
    vertical_offset: float     # 垂直偏移 (伏特)
    probe_attenuation: float = 1.0  # 探头衰减系数
    enabled: bool = True       # 通道是否启用

    def __post_init__(self):
        if len(self.raw) != len(self.time_axis):
            raise ValueError(
                f"raw ({len(self.raw)}) 与 time_axis ({len(self.time_axis)}) 长度不一致"
            )


@dataclass
class DecodeResult:
    """协议解码结果"""
    protocol: str              # "uart" | "i2c" | "spi"
    parsed_frames: list[dict]  # 解码后的帧列表
    error_count: int           # 解码错误数


@dataclass
class AnalysisResult:
    """一次完整触发采集的分析结果"""

    sequence_num: int                         # 单调递增序号
    trigger: TriggerInfo                      # 本次触发信息
    channels: dict[str, ChannelData]          # {"CH1": ChannelData, ...}

    # 以下由 Pipeline 填充
    measurements: dict[str, float] = field(default_factory=dict)
    """{"CH1_Vpp": 3.3, "CH1_Freq": 1000.0, ...}"""

    fft: dict[str, tuple[np.ndarray, np.ndarray]] = field(default_factory=dict)
    """{"CH1": (freqs, magnitudes), ...}"""

    math_channels: dict[str, np.ndarray] = field(default_factory=dict)
    """{"MATH1": np.ndarray, ...}"""

    decoded_protocols: dict[str, DecodeResult] = field(default_factory=dict)

    # 元信息
    processing_latency: float = 0.0           # Pipeline 处理耗时 (秒)
    acquisition_latency: float = 0.0          # 从触发到组装完成耗时 (秒)

    def summary(self) -> str:
        """一行摘要, 用于日志/调试"""
        meas_str = "  ".join(
            f"{k}={v:.4g}" for k, v in self.measurements.items()
        )
        return (
            f"[#{self.sequence_num}] "
            f"trigger={self.trigger.trigger_type}@{self.trigger.trigger_timestamp:.3f}s "
            f"ch={list(self.channels.keys())} "
            f"{meas_str}"
        )
