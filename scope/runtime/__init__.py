"""
runtime — 运行时基础设施 (v0.4)

提供:
  - BoundedQueue: 有界队列 + 背压策略
  - EventBus: 多 topic 数据分发路由器
  - FittedSnapshot: FitWorker 产出数据包
  - ConfigChange: 硬件配置变更指令
  - MeasurementSnapshot: 测量值单一数据源 (兼容遗留)
"""

from .event_bus import BoundedQueue, DropStrategy, QueueStats, EventBus, TopicConfig
from .fitted_snapshot import FittedSnapshot
from .config_change import ConfigChange
from .measurement_snapshot import MeasurementSnapshot

__all__ = [
    "BoundedQueue",
    "DropStrategy",
    "QueueStats",
    "EventBus",
    "TopicConfig",
    "FittedSnapshot",
    "ConfigChange",
    "MeasurementSnapshot",
]
