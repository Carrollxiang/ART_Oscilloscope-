"""
自动测量 — 对每个通道计算一组标准测量值

PipelineStage 实现, 在 process() 中填充 result.measurements。

支持的测量项:
  - Vpp:    峰峰值 (np.ptp)
  - Vmax:   最大值
  - Vmin:   最小值
  - Vrms:   有效值 (均方根)
  - Vavg:   平均值
  - Freq:   频率 (过零检测)
  - Period: 周期
  - DutyCycle: 占空比
  - PosWidth:  正脉宽
  - NegWidth:  负脉宽
  - RiseTime:  上升时间
  - FallTime:  下降时间
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

from scope.model import AnalysisResult
from .pipeline import PipelineStage

logger = logging.getLogger(__name__)


def compute_vpp(data: np.ndarray) -> float:
    return float(np.ptp(data))


def compute_vmax(data: np.ndarray) -> float:
    return float(np.max(data))


def compute_vmin(data: np.ndarray) -> float:
    return float(np.min(data))


def compute_vrms(data: np.ndarray) -> float:
    """有效值 = sqrt(mean(x²))"""
    return float(np.sqrt(np.mean(np.square(data))))


def compute_vavg(data: np.ndarray) -> float:
    return float(np.mean(data))


def compute_freq(data: np.ndarray, fs: float) -> float:
    """
    频率: 过零检测法。

    统计所有正→負过零点的时间间隔, 取中位数的倒数。
    适用于正弦波/方波等周期信号。
    """
    if len(data) < 3:
        return 0.0
    # 找正→負过零点
    positive = data > 0
    crossings = np.where(np.diff(positive.astype(int)) == -1)[0]
    if len(crossings) < 2:
        return 0.0
    periods = np.diff(crossings) / fs
    median_period = float(np.median(periods))
    if median_period <= 0:
        return 0.0
    return 1.0 / median_period


def compute_period(data: np.ndarray, fs: float) -> float:
    freq = compute_freq(data, fs)
    return 1.0 / freq if freq > 0 else 0.0


def compute_duty_cycle(data: np.ndarray) -> float:
    """
    占空比: 正脉宽 / (正脉宽 + 负脉宽)

    方波专用, 通过中值电平区分高/低。
    """
    if len(data) < 3:
        return 0.0
    threshold = (np.median(data) + np.mean(data)) / 2
    high = data > threshold
    if not np.any(high) or np.all(high):
        return 0.0
    high_ratio = float(np.sum(high)) / len(high)
    return high_ratio * 100.0


def compute_pos_width(data: np.ndarray, fs: float) -> float:
    """正脉宽: 上升沿到下降沿的平均时间 (秒)"""
    if len(data) < 3:
        return 0.0
    threshold = (np.median(data) + np.mean(data)) / 2
    high = data > threshold
    edges = np.diff(high.astype(int))
    risings = np.where(edges == 1)[0]
    fallings = np.where(edges == -1)[0]
    if len(risings) == 0 or len(fallings) == 0:
        return 0.0
    # 配对: 每个上升沿找下一个下降沿
    widths = []
    for r in risings:
        f = fallings[fallings > r]
        if len(f) > 0:
            widths.append((f[0] - r) / fs)
    if not widths:
        return 0.0
    return float(np.mean(widths))


def compute_neg_width(data: np.ndarray, fs: float) -> float:
    """负脉宽: 下降沿到上升沿的平均时间 (秒)"""
    if len(data) < 3:
        return 0.0
    threshold = (np.median(data) + np.mean(data)) / 2
    high = data > threshold
    edges = np.diff(high.astype(int))
    risings = np.where(edges == 1)[0]
    fallings = np.where(edges == -1)[0]
    if len(risings) == 0 or len(fallings) == 0:
        return 0.0
    widths = []
    for f in fallings:
        r = risings[risings > f]
        if len(r) > 0:
            widths.append((r[0] - f) / fs)
    if not widths:
        return 0.0
    return float(np.mean(widths))


def compute_rise_time(data: np.ndarray, fs: float) -> float:
    """
    上升时间: 从 10% 到 90% 幅值的时间。
    """
    if len(data) < 3:
        return 0.0
    vmin = np.min(data)
    vmax = np.max(data)
    if vmax - vmin < 1e-12:
        return 0.0
    low = vmin + 0.1 * (vmax - vmin)
    high = vmin + 0.9 * (vmax - vmin)

    # 找第一个上升沿
    for i in range(1, len(data)):
        if data[i - 1] <= low <= data[i] or data[i - 1] >= low >= data[i]:
            start = i
            break
    else:
        return 0.0
    for i in range(start, len(data)):
        if data[i - 1] <= high <= data[i] or data[i - 1] >= high >= data[i]:
            return (i - start) / fs
    return 0.0


def compute_fall_time(data: np.ndarray, fs: float) -> float:
    """下降时间: 从 90% 到 10% 幅值的时间。"""
    if len(data) < 3:
        return 0.0
    vmin = np.min(data)
    vmax = np.max(data)
    if vmax - vmin < 1e-12:
        return 0.0
    low = vmin + 0.1 * (vmax - vmin)
    high = vmin + 0.9 * (vmax - vmin)

    for i in range(1, len(data)):
        if data[i - 1] >= high >= data[i] or data[i - 1] <= high <= data[i]:
            start = i
            break
    else:
        return 0.0
    for i in range(start, len(data)):
        if data[i - 1] >= low >= data[i] or data[i - 1] <= low <= data[i]:
            return (i - start) / fs
    return 0.0


# ── 测量函数注册表 ──────────────────────────────────────────

MEASUREMENT_FUNCTIONS: dict[str, callable] = {
    "Vpp": lambda d, fs: compute_vpp(d),
    "Vmax": lambda d, fs: compute_vmax(d),
    "Vmin": lambda d, fs: compute_vmin(d),
    "Vrms": lambda d, fs: compute_vrms(d),
    "Vavg": lambda d, fs: compute_vavg(d),
    "Freq": lambda d, fs: compute_freq(d, fs),
    "Period": lambda d, fs: compute_period(d, fs),
    "DutyCycle": lambda d, fs: compute_duty_cycle(d),
    "PosWidth": lambda d, fs: compute_pos_width(d, fs),
    "NegWidth": lambda d, fs: compute_neg_width(d, fs),
    "RiseTime": lambda d, fs: compute_rise_time(d, fs),
    "FallTime": lambda d, fs: compute_fall_time(d, fs),
}


class AutoMeasure(PipelineStage):
    """
    自动测量阶段。

    对指定的通道列表, 计算一组测量值并写入 result.measurements。

    用法:
        pipeline.add_stage(AutoMeasure(
            measurements=["Vpp", "Freq", "Vrms"],
            channels=["CH1", "CH2"],
        ))

    结果写入 result.measurements 的 key 格式: "CH1_Vpp", "CH1_Freq", ...
    """

    def __init__(self, measurements: list[str] = None, channels: list[str] = None):
        """
        measurements: 要计算的测量项名列表, 默认全部。
        channels: 要计算的通道名列表, 默认全部已启用通道。
        """
        self._measurements = measurements or list(MEASUREMENT_FUNCTIONS.keys())
        self._channels = channels or []

        # 验证测量项
        unknown = set(self._measurements) - set(MEASUREMENT_FUNCTIONS.keys())
        if unknown:
            logger.warning(f"未知测量项: {unknown}")
            self._measurements = [m for m in self._measurements
                                  if m in MEASUREMENT_FUNCTIONS]

    def process(self, result: AnalysisResult) -> AnalysisResult:
        channels = self._channels or list(result.channels.keys())

        for ch_name in channels:
            ch_data = result.channels.get(ch_name)
            if ch_data is None or not ch_data.enabled:
                continue

            data = ch_data.raw
            fs = ch_data.sample_rate

            for meas in self._measurements:
                func = MEASUREMENT_FUNCTIONS.get(meas)
                if func is None:
                    continue
                try:
                    value = func(data, fs)
                    key = f"{ch_name}_{meas}"
                    result.measurements[key] = value
                except Exception as e:
                    logger.warning(f"测量 {ch_name}_{meas} 失败: {e}")

        return result

    def __repr__(self) -> str:
        return (
            f"AutoMeasure({self._measurements}, "
            f"channels={self._channels})"
        )
