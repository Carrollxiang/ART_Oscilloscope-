"""
PidController — PID 控制器封装

独立组件，不依赖反馈系统。支持死区、积分限幅、输出限幅。
"""

from __future__ import annotations

from collections import deque
import math
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PidConfig:
    """PID 参数配置"""
    preset_value: float                   # 目标值
    kp: float = 0.03                      # 比例系数
    ki: float = 0.0                       # 积分系数 (0 = 无积分)
    kd: float = 0.0                       # 微分系数
    i_limit: float = 0.1                  # 积分限幅（抗饱和）
    output_limit: float = 0.1              # 输出限幅
    window_size: int = 10                  # 误差窗口大小
    deadband: float = 0.0                  # 死区（|error| < deadband 返回 None）


class PidController:
    """PID 控制器 — 保持误差历史，单步计算"""

    def __init__(self, config: PidConfig):
        self._config = config
        self._errors: deque[float] = deque(maxlen=config.window_size)
        self._last_error: float = 0.0

    # ── 核心计算 ───────────────────────────────────────────────

    def step(self, measured_value: float) -> Optional[float]:
        """
        单步 PID 计算。

        Args:
            measured_value: 当前测量值

        Returns:
            float: 调整量 delta（限幅后）
            None: 在死区内
        """
        error = self._config.preset_value - measured_value

        # 死区检查
        if abs(error) < self._config.deadband:
            return None

        # 保存误差历史
        self._errors.append(error)

        # P
        p_out = self._config.kp * error

        # I — 窗口内所有误差之和，限幅
        i_out = 0.0
        if self._config.ki != 0.0:
            i_sum = sum(self._errors)
            i_out = self._config.ki * i_sum
            i_out = max(-self._config.i_limit, min(self._config.i_limit, i_out))

        # D
        d_out = 0.0
        if self._config.kd != 0.0:
            d_out = self._config.kd * (error - self._last_error)

        # 总输出
        output = p_out + i_out + d_out
        output = max(-self._config.output_limit, min(self._config.output_limit, output))

        self._last_error = error
        return output

    # ── 状态管理 ───────────────────────────────────────────────

    def reset(self):
        """重置状态（用于重新启动）"""
        self._errors.clear()
        self._last_error = 0.0

    @property
    def errors_std(self) -> float:
        """误差窗口内的标准差"""
        if len(self._errors) < 2:
            return 0.0
        mean = sum(self._errors) / len(self._errors)
        variance = sum((e - mean) ** 2 for e in self._errors) / len(self._errors)
        return math.sqrt(variance)

    @property
    def errors_count(self) -> int:
        return len(self._errors)

    @property
    def preset_value(self) -> float:
        return self._config.preset_value

    @property
    def deadband(self) -> float:
        return self._config.deadband

    @property
    def metrics(self) -> dict:
        """运行时指标"""
        return {
            "errors_count": len(self._errors),
            "last_error": self._last_error,
            "preset_value": self._config.preset_value,
            "errors_std": self.errors_std,
        }
