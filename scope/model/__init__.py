# 数据模型 — 零第三方依赖, 纯 Python 数据类

# scope/model/
# 本模块定义整个系统流通的黄金数据包结构。
# 所有其他模块 import 自这里, 但不依赖这里的实现细节以外的任何东西。

from .analysis_result import AnalysisResult, ChannelData, TriggerInfo
from .enums import (
    ChannelCoupling,
    TriggerType,
    TriggerSlope,
    SlotStatus,
    SlotProtocol,
    DeviceHealthState,
    MeasurementId,
)

__all__ = [
    "AnalysisResult",
    "ChannelData",
    "TriggerInfo",
    "ChannelCoupling",
    "TriggerType",
    "TriggerSlope",
    "SlotStatus",
    "SlotProtocol",
    "DeviceHealthState",
    "MeasurementId",
]
