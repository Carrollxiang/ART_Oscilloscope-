"""
数字滤波 — FIR/IIR 滤波器

PipelineStage 实现, 对指定通道的原始数据进行数字滤波,
结果写入同一通道的 raw 数组 (原地替换) 或创建新的滤波通道。

依赖 scipy.signal, 如未安装则跳过。
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

from scope.model import AnalysisResult
from .pipeline import PipelineStage

logger = logging.getLogger(__name__)

try:
    from scipy import signal as scipy_signal
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False
    logger.warning("scipy 未安装, 数字滤波不可用")


class LowPassFilter(PipelineStage):
    """
    低通滤波器 (IIR Butterworth)。

    用法:
        pipeline.add_stage(LowPassFilter(cutoff=1e6, order=4, channels=["CH1"]))
    """

    def __init__(self, cutoff: float, order: int = 4,
                 channels: list[str] = None, inplace: bool = True):
        self._cutoff = cutoff
        self._order = order
        self._channels = channels or []
        self._inplace = inplace
        self._sos = None  # 二级联节 (second-order sections)

    def _init_filter(self, fs: float):
        if not HAS_SCIPY:
            return
        nyquist = fs / 2
        normalized_cutoff = self._cutoff / nyquist
        if normalized_cutoff >= 1.0:
            logger.warning(f"截止频率 {self._cutoff}Hz 超过奈奎斯特频率 {fs/2}Hz, 跳过")
            self._sos = None
            return
        self._sos = scipy_signal.butter(
            self._order, normalized_cutoff, btype="low", output="sos"
        )

    def process(self, result: AnalysisResult) -> AnalysisResult:
        if not HAS_SCIPY or self._sos is None:
            self._init_filter(
                next(iter(result.channels.values())).sample_rate
            )

        channels = self._channels or list(result.channels.keys())

        for ch_name in channels:
            ch_data = result.channels.get(ch_name)
            if ch_data is None or not ch_data.enabled:
                continue

            if self._sos is None:
                continue

            try:
                filtered = scipy_signal.sosfilt(self._sos, ch_data.raw)
                if self._inplace:
                    ch_data.raw = filtered.astype(np.float32)
                else:
                    result.channels[f"{ch_name}_LPF"] = ch_data  # TODO: copy
            except Exception as e:
                logger.warning(f"低通滤波 {ch_name} 失败: {e}")

        return result

    def __repr__(self) -> str:
        return f"LowPassFilter(cutoff={self._cutoff}Hz, order={self._order})"


class HighPassFilter(PipelineStage):
    """高通滤波器 (IIR Butterworth)。"""

    def __init__(self, cutoff: float, order: int = 4,
                 channels: list[str] = None, inplace: bool = True):
        self._cutoff = cutoff
        self._order = order
        self._channels = channels or []
        self._inplace = inplace
        self._sos = None

    def _init_filter(self, fs: float):
        if not HAS_SCIPY:
            return
        nyquist = fs / 2
        normalized_cutoff = self._cutoff / nyquist
        if normalized_cutoff >= 1.0:
            self._sos = None
            return
        self._sos = scipy_signal.butter(
            self._order, normalized_cutoff, btype="high", output="sos"
        )

    def process(self, result: AnalysisResult) -> AnalysisResult:
        if not HAS_SCIPY or self._sos is None:
            self._init_filter(
                next(iter(result.channels.values())).sample_rate
            )

        channels = self._channels or list(result.channels.keys())
        for ch_name in channels:
            ch_data = result.channels.get(ch_name)
            if ch_data is None or not ch_data.enabled or self._sos is None:
                continue
            try:
                filtered = scipy_signal.sosfilt(self._sos, ch_data.raw)
                if self._inplace:
                    ch_data.raw = filtered.astype(np.float32)
            except Exception as e:
                logger.warning(f"高通滤波 {ch_name} 失败: {e}")
        return result

    def __repr__(self) -> str:
        return f"HighPassFilter(cutoff={self._cutoff}Hz, order={self._order})"
