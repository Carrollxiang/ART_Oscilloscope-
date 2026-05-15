"""
FFT 频谱分析

PipelineStage 实现, 对指定通道计算幅度频谱并写入 result.fft。
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

from scope.model import AnalysisResult
from .pipeline import PipelineStage

logger = logging.getLogger(__name__)

# 支持的窗函数
WINDOW_FUNCTIONS: dict[str, callable] = {
    "none": lambda n: np.ones(n),
    "hanning": np.hanning,
    "hamming": np.hamming,
    "blackman": np.blackman,
    "bartlett": np.bartlett,
    "flattop": np.bartlett,  # fallback
}


class FFTAnalyze(PipelineStage):
    """
    FFT 频谱分析阶段。

    用法:
        pipeline.add_stage(FFTAnalyze(
            channels=["CH1", "CH2"],
            window="hanning",
            peak_count=5,     # 自动标注前 5 个峰值
        ))

    结果写入 result.fft, key 格式: "CH1" → (freqs, magnitudes)
    同时写入测量值:
      - CH1_FFT_Max: 最大幅度
      - CH1_FFT_Freq: 最大幅度对应的频率
      - CH1_FFT_THD: 总谐波失真 (可选)
    """

    def __init__(
        self,
        channels: list[str] = None,
        window: str = "hanning",
        peak_count: int = 0,
    ):
        """
        channels: 要分析的通道名列表, 默认全部。
        window: 窗函数 ("none", "hanning", "hamming", "blackman")。
        peak_count: 如果 >0, 额外标注前 N 个峰值的频率和幅度。
        """
        self._channels = channels or []
        self._window = window
        self._peak_count = peak_count

        if window not in WINDOW_FUNCTIONS:
            logger.warning(f"未知窗函数 '{window}', 使用 'hanning'")
            self._window = "hanning"

    def process(self, result: AnalysisResult) -> AnalysisResult:
        channels = self._channels or list(result.channels.keys())

        for ch_name in channels:
            ch_data = result.channels.get(ch_name)
            if ch_data is None or not ch_data.enabled:
                continue

            try:
                freqs, mags = self._compute_fft(ch_data.raw, ch_data.sample_rate)
                result.fft[ch_name] = (freqs, mags)

                # 峰值信息写入测量值
                if len(mags) > 0:
                    max_idx = int(np.argmax(mags))
                    result.measurements[f"{ch_name}_FFT_Max"] = float(mags[max_idx])
                    result.measurements[f"{ch_name}_FFT_Freq"] = float(freqs[max_idx])

                # 前 N 个峰值
                if self._peak_count > 0 and len(mags) > 1:
                    self._find_peaks(result, ch_name, freqs, mags)

            except Exception as e:
                logger.warning(f"FFT {ch_name} 失败: {e}")

        return result

    def _compute_fft(self, data: np.ndarray, fs: float
                     ) -> tuple[np.ndarray, np.ndarray]:
        """计算幅度频谱, 返回 (freqs, magnitudes)"""
        n = len(data)
        if n < 2:
            return np.array([0.0]), np.array([0.0])

        # 去直流
        data_ac = data - np.mean(data)

        # 加窗
        window = WINDOW_FUNCTIONS[self._window](n)
        data_windowed = data_ac * window

        # FFT
        spectrum = np.fft.rfft(data_windowed)
        mags = np.abs(spectrum) / n
        # 单边谱幅度补偿 (除 DC 和 Nyquist 外 ×2)
        mags[1:-1] *= 2

        freqs = np.fft.rfftfreq(n, 1.0 / fs)
        return freqs, mags

    def _find_peaks(self, result: AnalysisResult,
                    ch_name: str, freqs: np.ndarray, mags: np.ndarray):
        """
        查找前 peak_count 个峰值 (简单实现: 找局部极大值)。
        """
        # 忽略 DC 分量 (0 Hz)
        start = 1 if freqs[0] == 0 else 0
        if len(mags) <= start + 2:
            return

        # 找局部极大值
        peaks = []
        for i in range(start + 1, len(mags) - 1):
            if mags[i] > mags[i - 1] and mags[i] > mags[i + 1]:
                peaks.append((freqs[i], mags[i]))

        # 按幅度降序排列
        peaks.sort(key=lambda x: x[1], reverse=True)

        for idx, (freq, mag) in enumerate(peaks[:self._peak_count]):
            result.measurements[f"{ch_name}_FFT_Peak{idx + 1}_Freq"] = float(freq)
            result.measurements[f"{ch_name}_FFT_Peak{idx + 1}_Mag"] = float(mag)

    def __repr__(self) -> str:
        return f"FFTAnalyze(window={self._window}, peak_count={self._peak_count})"
