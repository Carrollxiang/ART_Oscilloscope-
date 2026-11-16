"""
MeasurementSpec — 测量规格 (纯配置)

定义单个测量项的参数：通道、时间窗、特征类型。
不含计算逻辑，计算由 MeasurementProcessor 执行。

两类规格:
  - 标量规格 (feature = Vpp/Vmax/Vmin/Mean): 单通道切片后取特征值
  - 比值规格 (feature = Ratio): 引用两个标量规格的 tag, 同帧内相除
"""

from dataclasses import dataclass

# 比值规格的特征名（大小写不敏感）
RATIO_FEATURE = "ratio"


@dataclass
class MeasurementSpec:
    """
    测量规格 — 纯配置数据类。
    
    定义如何在 RawFrame 上切片并计算单个测量值。
    
    Attributes:
        tag: 语义名，用于标识和反馈订阅，如 "CH1_power"
        channel: 通道索引 (0-based)，比值规格不使用（保持 0）
        start_ms: 时间窗起始 (毫秒，相对帧起点)
        end_ms: 时间窗结束 (毫秒，0 表示帧结尾)
        feature: 特征类型 (Vpp, Vmax, Vmin, Mean, Ratio)
        semantic: 可选说明文字
        numerator_tag: 比值规格的分子测量项 tag (feature="Ratio" 时必填)
        denominator_tag: 比值规格的分母测量项 tag (feature="Ratio" 时必填)
    
    Example:
        spec = MeasurementSpec(
            tag="CH1_vpp",
            channel=0,
            start_ms=0.0,
            end_ms=0.0,  # 0 表示到帧结尾
            feature="Vpp",
        )
        ratio = MeasurementSpec(
            tag="ratio_12",
            feature="Ratio",
            numerator_tag="CH1_vpp",
            denominator_tag="CH2_vpp",
        )
    """
    
    tag: str
    channel: int = 0
    start_ms: float = 0.0
    end_ms: float = 0.0
    feature: str = "Vpp"
    semantic: str = ""
    numerator_tag: str = ""
    denominator_tag: str = ""
    
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

        if self.is_ratio():
            if not self.numerator_tag:
                raise ValueError(
                    f"比值规格 '{self.tag}' 缺少分子测量项 (numerator_tag)"
                )
            if not self.denominator_tag:
                raise ValueError(
                    f"比值规格 '{self.tag}' 缺少分母测量项 (denominator_tag)"
                )
            if self.numerator_tag == self.denominator_tag:
                raise ValueError(
                    f"比值规格 '{self.tag}' 的分子与分母相同: "
                    f"{self.numerator_tag}"
                )
            if self.tag in (self.numerator_tag, self.denominator_tag):
                raise ValueError(
                    f"比值规格 '{self.tag}' 不能引用自身"
                )

    def is_ratio(self) -> bool:
        """是否为比值规格（分子/分母同帧相除）。"""
        return self.feature.strip().lower() == RATIO_FEATURE
