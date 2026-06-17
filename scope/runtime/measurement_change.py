"""
MeasurementSpecsChanged — 测量规格变更指令

由 UI 发布完整 MeasurementSpec 快照，runtime worker 消费后更新
MeasurementProcessor。这样采集线程不需要轮询 UI 状态。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .measurement_spec import MeasurementSpec


@dataclass
class MeasurementSpecsChanged:
    """测量规格完整快照。"""

    specs: list[MeasurementSpec]
    """当前完整测量规格列表。"""

    change_id: int
    """单调递增 ID，用于去重。"""

    source: str = "ui"
    """变更来源。"""

    timestamp: float = field(default_factory=time.monotonic)
    """指令创建时间。"""
