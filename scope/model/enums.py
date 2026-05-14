"""
枚举类型定义
"""

from enum import Enum, auto


class ChannelCoupling(Enum):
    DC = "dc"
    AC = "ac"
    GND = "gnd"


class TriggerType(Enum):
    EDGE = "edge"
    IMMEDIATE = "immediate"
    PULSE = "pulse"  # 扩展


class TriggerSlope(Enum):
    RISING = "rising"
    FALLING = "falling"


class SlotStatus(Enum):
    """反馈通道的运行状态"""
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    ERROR = "error"


class SlotProtocol(Enum):
    """支持的反馈协议类型"""
    UDP = "udp"
    SERIAL = "serial"
    MODBUS_TCP = "modbus_tcp"
    MODBUS_RTU = "modbus_rtu"
    MQTT = "mqtt"
    HTTP = "http"
    NULL = "null"  # 调试用


class DeviceHealthState(Enum):
    """硬件健康状态"""
    HEALTHY = "healthy"
    PINGING = "pinging"
    RESETTING = "resetting"
    REINITIALIZING = "reinitializing"
    OFFLINE = "offline"


class MeasurementId(Enum):
    """标准测量项标识符"""
    VPP = "Vpp"
    VMAX = "Vmax"
    VMIN = "Vmin"
    VRMS = "Vrms"
    VAVG = "Vavg"
    FREQ = "Freq"
    PERIOD = "Period"
    DUTY_CYCLE = "DutyCycle"
    POS_WIDTH = "PosWidth"
    NEG_WIDTH = "NegWidth"
    RISE_TIME = "RiseTime"
    FALL_TIME = "FallTime"
