"""
信号处理管道 — 入口

用法:
    pipeline = ProcessingPipeline()
    pipeline.add_stage(AutoMeasure(["Vpp", "Freq"]))
    pipeline.add_stage(MathOp("CH1 + CH2", output="MATH1"))
    pipeline.add_stage(FFTAnalyze(channels=["CH1"]))

    result = pipeline.process(raw_result)
"""

from .pipeline import ProcessingPipeline, PipelineStage
from .measurements import AutoMeasure, MEASUREMENT_FUNCTIONS
from .math_ops import MathOp
from .fft import FFTAnalyze
from .filters import LowPassFilter, HighPassFilter

__all__ = [
    "ProcessingPipeline",
    "PipelineStage",
    "AutoMeasure",
    "MathOp",
    "FFTAnalyze",
    "LowPassFilter",
    "HighPassFilter",
    "MEASUREMENT_FUNCTIONS",
]
