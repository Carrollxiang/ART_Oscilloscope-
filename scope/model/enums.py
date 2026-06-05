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
    """测量特征类型"""
    Vpp = "Vpp"
    Vmax = "Vmax"
    Vmin = "Vmin"
    Vrms = "Vrms"
    Mean = "Mean"
    Integral = "Integral"
    Freq = "Freq"
    Period = "Period"
    DutyCycle = "DutyCycle"


class ChannelCoupling(Enum):
    """通道耦合方式"""
    DC = "dc"
    AC = "ac"
    GND = "gnd"


class MeasurementId(Enum):
    """测量项 ID"""
    Vpp = "Vpp"
    Vmax = "Vmax"
    Vmin = "Vmin"
    Vrms = "Vrms"
    Vavg = "Vavg"
    Freq = "Freq"
    Period = "Period"
    DutyCycle = "DutyCycle"
    PosWidth = "PosWidth"
    NegWidth = "NegWidth"
    RiseTime = "RiseTime"
    FallTime = "FallTime"


# 支持的测量特征列表
MEASUREMENT_FEATURES = [f.value for f in MeasurementFeature]
