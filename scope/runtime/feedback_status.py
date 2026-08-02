"""
FeedbackStatusSnapshot — feedback.status topic 载荷

FeedbackManager 周期发布，通过 UIBridge 交付到 Qt 主线程。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FeedbackWorkerStatus:
    """单个 feedback worker 的状态快照。"""

    worker_id: str
    status: str  # "running" | "paused" | "idle"
    measurement_key: str
    last_value: Optional[float]
    last_error: Optional[float]
    errors_std: float
    errors_count: int
    frames_processed: int
    preset_value: float
    deadband: float
    kp: float
    ki: float
    kd: float
    output_limit: float
    i_limit: float
    window_size: int
    max_error_ratio: float = 0.30       # 单帧误差保护阈值 (0=禁用)
    trend_window: int = 5               # 误差趋势检查窗口 (0=禁用)
    chain_type: str = "none"          # "none" | "ad9910" | "rtmq"
    target_info: str = ""              # e.g. "192.168.1.100:3251"
    sender_error: str = ""             # sender 创建失败时的错误信息
    stop_reason: str = ""              # 自动暂停原因 (误差趋势变差等, 空=未自动暂停)
    frames_skipped: int = 0            # 单帧误差保护跳过的帧数


@dataclass
class FeedbackStatusSnapshot:
    """全部 worker 状态快照。"""

    workers: list[FeedbackWorkerStatus]
    running_count: int
    total_count: int
    timestamp: float = field(default_factory=time.monotonic)
