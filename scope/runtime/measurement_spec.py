"""
MeasurementSpec — 测量规格 (纯配置)

定义单个测量项的参数：通道、时间窗、特征类型。
不含计算逻辑，计算由 MeasurementProcessor 执行。
"""

from dataclasses import dataclass


@dataclass
class MeasurementSpec:
    """
    测量规格 — 纯配置数据类。
    
    定义如何在 RawFrame 上切片并计算单个测量值。
    
    Attributes:
        tag: 语义名，用于标识和反馈订阅，如 "CH1_power"
        channel: 通道索引 (0-based)
        start_ms: 时间窗起始 (毫秒，相对帧起点)
        end_ms: 时间窗结束 (毫秒，0 表示帧结尾)
        feature: 特征类型 (Vpp, Vmax, Vmin, Mean)
        semantic: 可选说明文字
    
    Example:
        spec = MeasurementSpec(
            tag="CH1_vpp",
            channel=0,
            start_ms=0.0,
            end_ms=0.0,  # 0 表示到帧结尾
            feature="Vpp",
        )
    """
    
    tag: str
    channel: int
    start_ms: float = 0.0
    end_ms: float = 0.0
    feature: str = "Vrms"
    semantic: str = ""
    
    def __post_init__(self):
        """验证参数"""
        if self.channel < 0:
            raise ValueError(f"channel 不能为负数: {self.channel}")
        if self.start_ms < 0:
            raise ValueError(f"start_ms 不能为负数: {self.start_ms}")
        if self.end_ms < 0:
            raise ValueError(f"end_ms 不能为负数: {self.end_ms}")
        if self.end_ms > 0 and self.end_ms <= self.start_ms:
            raise ValueError(f"end_ms ({self.end_ms}) 必须大于 start_ms ({self.start_ms})")
