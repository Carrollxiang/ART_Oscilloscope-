"""
runtime — 运行时基础设施 (v0.4)

提供:
  - BoundedQueue: 有界队列 + 背压策略
  - EventBus: 多 topic 数据分发路由器
  - FittedSnapshot: MeasurementProcessor 产出数据包
  - MeasurementSpec: 测量规格 (纯配置)
  - MeasurementProcessor: 测量处理器
  - ConfigChange: 硬件配置变更指令
"""

from .event_bus import BoundedQueue, DropStrategy, QueueStats, EventBus, TopicConfig
from .fitted_snapshot import FittedSnapshot
from .measurement_spec import MeasurementSpec
from .measurement_processor import MeasurementProcessor
from .config_change import ConfigChange

__all__ = [
    "BoundedQueue",
    "DropStrategy",
    "QueueStats",
    "EventBus",
    "TopicConfig",
    "FittedSnapshot",
    "MeasurementSpec",
    "MeasurementProcessor",
    "ConfigChange",
]
