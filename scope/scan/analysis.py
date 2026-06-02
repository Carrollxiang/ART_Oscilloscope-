"""
扫频分析 — V(t) → V(f) 映射 + 线型拟合

输入:
  - AnalysisResult (含 CH0 波形 + 时间轴)
  - ScanConfig (base_freq, scan_freq_amp, scan_dur)

输出:
  - ScanFitResult: f0 (中心频率), gamma (线宽), R2 (拟合优度)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class ScanFitResult:
    """扫频拟合结果"""
    f0: float = 0.0               # 中心频率 (MHz)
    gamma: float = 0.0            # 线宽 / HWHM (MHz)
    amplitude: float = 0.0        # 峰值幅度 (V)
    offset: float = 0.0           # 基线偏移 (V)
    r_squared: float = 0.0        # 拟合优度 R²
    f_axis: np.ndarray | None = None   # 频率轴 (MHz)
    v_f: np.ndarray | None = None      # V(f) 数据
    v_fit: np.ndarray | None = None    # 拟合曲线

    @property
    def is_valid(self) -> bool:
        return not np.isnan(self.f0) and self.r_squared > 0


def map_to_frequency_domain(
    v_t: np.ndarray,
    time_axis: np.ndarray,
    base_freq: float,
    scan_freq_amp: float,
    scan_dur: float,
) -> tuple[np.ndarray, np.ndarray]:
    """
    将 V(t) 映射为 V(f)。

    假设线性扫频:
      f(t) = f_start + (scan_freq_amp / scan_dur) * t
      其中 f_start = base_freq - scan_freq_amp/2

    返回 (f_axis_mhz, v_f)
    """
    f_start = base_freq - scan_freq_amp / 2
    f_end = base_freq + scan_freq_amp / 2

    # scan_dur 单位是 μs, time_axis 单位是秒
    # 频率轴: 线性映射 t → f
    scan_dur_s = scan_dur / 1_000_000.0

    f_axis = f_start + (f_end - f_start) * time_axis / scan_dur_s
    return f_axis, v_t


def fit_lorentzian(
    f_axis: np.ndarray,
    v_f: np.ndarray,
    initial_guess: tuple[float, float, float, float] | None = None,
) -> ScanFitResult:
    """
    Lorentzian 线型拟合: V(f) = offset + amplitude * (gamma² / ((f-f0)² + gamma²))

    参数:
        f_axis: 频率轴 (MHz)
        v_f: 电压数据 (V)
        initial_guess: (f0, gamma, amplitude, offset) 初始猜测

    返回 ScanFitResult。
    """
    try:
        from scipy.optimize import curve_fit

        # Lorentzian 模型
        def lorentzian(f, f0, gamma, amp, off):
            return off + amp * (gamma**2 / ((f - f0)**2 + gamma**2))

        # 初始猜测: 用数据特征推断
        if initial_guess is None:
            # 找最大值位置作为 f0
            idx_max = np.argmax(v_f)
            f0_guess = f_axis[idx_max]
            amp_guess = v_f[idx_max] - np.min(v_f)
            off_guess = np.min(v_f)
            # gamma 猜测: 半高宽 / 2
            half_max = off_guess + amp_guess / 2
            above_half = f_axis[v_f >= half_max]
            if len(above_half) >= 2:
                gamma_guess = (above_half[-1] - above_half[0]) / 2
            else:
                gamma_guess = (f_axis[-1] - f_axis[0]) / 20
            p0 = (f0_guess, gamma_guess, amp_guess, off_guess)
        else:
            p0 = initial_guess

        # 拟合
        popt, pcov = curve_fit(
            lorentzian, f_axis, v_f,
            p0=p0,
            bounds=(
                [f_axis[0], 0, 0, -np.inf],              # 下界
                [f_axis[-1], f_axis[-1]-f_axis[0], np.inf, np.inf],  # 上界
            ),
            maxfev=10000,
        )

        f0, gamma, amp, off = popt
        v_fit = lorentzian(f_axis, *popt)

        # R²
        ss_res = np.sum((v_f - v_fit)**2)
        ss_tot = np.sum((v_f - np.mean(v_f))**2)
        r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0

        return ScanFitResult(
            f0=f0,
            gamma=gamma,
            amplitude=amp,
            offset=off,
            r_squared=r_squared,
            f_axis=f_axis,
            v_f=v_f,
            v_fit=v_fit,
        )

    except ImportError:
        logger.warning("scipy 未安装, 回退到峰值查找 (不做拟合)")
        return _peak_find_fallback(f_axis, v_f)
    except Exception as e:
        logger.warning(f"Lorentzian 拟合失败: {e}, 回退到峰值查找")
        return _peak_find_fallback(f_axis, v_f)


def _peak_find_fallback(f_axis: np.ndarray, v_f: np.ndarray) -> ScanFitResult:
    """峰值查找回退方案 (无 scipy 时使用)。"""
    idx_max = np.argmax(v_f)
    f0 = f_axis[idx_max]
    amp = v_f[idx_max] - np.min(v_f)

    # 半高宽估算
    half_max = np.min(v_f) + amp / 2
    above = f_axis[v_f >= half_max]
    if len(above) >= 2:
        gamma = (above[-1] - above[0]) / 2
    else:
        gamma = 0.0

    return ScanFitResult(
        f0=f0,
        gamma=gamma,
        amplitude=amp,
        offset=np.min(v_f),
        r_squared=0.0,
        f_axis=f_axis,
        v_f=v_f,
    )
