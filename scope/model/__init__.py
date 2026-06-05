"""
数据模型 — RawFrame

采集线程产出的原始帧数据结构。
"""

from dataclasses import dataclass, field
import time
from typing import Optional

import numpy as np


@dataclass
class RawFrame:
    """
    采集线程产出的原始帧 — 极简包装。
    
    不做任何处理，只封装原始数据和必要元信息。
    下游消费者（WaveformView、MeasurementProcessor）按需取用。
    
    Attributes:
        sequence_num: 帧序号，单调递增
        data: 原始数据，shape=(n_channels, n_samples)，dtype=float32
        sample_rate: 采样率 (Hz)
        timestamp: 采集完成时间戳 (time.monotonic)
    """
    
    sequence_num: int
    data: np.ndarray
    sample_rate: float
    timestamp: float = field(default_factory=time.monotonic)
    
    @property
    def n_channels(self) -> int:
        """通道数"""
        return self.data.shape[0]
    
    @property
    def n_samples(self) -> int:
        """每通道采样点数"""
        return self.data.shape[1]
    
    @property
    def duration(self) -> float:
        """帧时长 (秒)"""
        return self.n_samples / self.sample_rate if self.sample_rate > 0 else 0.0
    
    def time_axis(self) -> np.ndarray:
        """生成时间轴 (秒)"""
        return np.arange(self.n_samples, dtype=np.float64) / self.sample_rate
    
    def get_channel(self, ch: int) -> np.ndarray:
        """获取单个通道数据"""
        return self.data[ch]
    
    def __post_init__(self):
        """验证数据完整性"""
        if self.data.ndim != 2:
            raise ValueError(f"RawFrame.data 必须是 2D 数组，实际 ndim={self.data.ndim}")
        if self.sample_rate <= 0:
            raise ValueError(f"sample_rate 必须大于 0，实际 {self.sample_rate}")
