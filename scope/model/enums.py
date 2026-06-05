"""
枚举定义
"""

from enum import Enum


class SlotStatus(Enum):
    """反馈插槽状态"""
    IDLE = "idle"
    PAUSED = "paused"
    RUNNING = "running"
    ERROR = "error"


class MeasurementFeature(Enum):
    """测量特征类型 - 只保留 4 个基本测量量"""
    Vpp = "Vpp"
    Vmax = "Vmax"
    Vmin = "Vmin"
    Mean = "Mean"


class ChannelCoupling(Enum):
    """通道耦合方式"""
    DC = "dc"
    AC = "ac"
    GND = "gnd"


class MeasurementId(Enum):
    """测量项 ID - 与 MeasurementFeature 保持一致"""
    Vpp = "Vpp"
    Vmax = "Vmax"
    Vmin = "Vmin"
    Mean = "Mean"


# 支持的测量特征列表
MEASUREMENT_FEATURES = [f.value for f in MeasurementFeature]
