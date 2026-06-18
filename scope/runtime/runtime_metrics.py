"""
RuntimeMetricsSnapshot — runtime.metrics topic 载荷

ScopeApp 周期聚合各 worker 的 metrics 并发布。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class RuntimeMetricsSnapshot:
    """各运行时组件 metrics 聚合快照。"""

    measurement_processor: dict
    event_bus: dict
    config_worker: dict
    measurement_config_worker: dict
    feedback_command_worker: dict
    ui_bridge: dict
    timestamp: float = field(default_factory=time.monotonic)
