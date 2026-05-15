"""
信号处理管道 — Pipeline 框架

采用责任链模式 (Chain of Responsibility):
  每个处理阶段是一个 PipelineStage, 接收 AnalysisResult 并返回 (可能修改后的) AnalysisResult。
  管道按注册顺序依次执行各阶段。

用法:
    pipeline = ProcessingPipeline()
    pipeline.add_stage(AutoMeasure(["Vpp", "Freq"], channels=["CH1", "CH2"]))
    pipeline.add_stage(MathOp("CH1 + CH2", output="MATH1"))
    pipeline.add_stage(FFTAnalyze(channels=["CH1"]))

    result = pipeline.process(raw_result)
    # result.measurements 现在包含 Vpp/Freq
    # result.math_channels 包含 MATH1
    # result.fft 包含 CH1 的频谱
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Optional

from scope.model import AnalysisResult

logger = logging.getLogger(__name__)


class PipelineStage(ABC):
    """管道中的单个处理阶段。"""

    @abstractmethod
    def process(self, result: AnalysisResult) -> AnalysisResult:
        """
        处理 AnalysisResult 并返回 (可能修改后的) 结果。
        必须在原地修改 (填充 measurements/fft/math_channels) 或返回新对象。
        """
        ...

    def __repr__(self) -> str:
        return self.__class__.__name__


class ProcessingPipeline:
    """
    处理管道 — 按顺序执行多个 PipelineStage。

    用法:
        pipeline = ProcessingPipeline()
        pipeline.add_stage(SomeStage())
        result = pipeline.process(result)
    """

    def __init__(self):
        self._stages: list[PipelineStage] = []

    def add_stage(self, stage: PipelineStage):
        """添加一个处理阶段 (追加到末尾)。"""
        self._stages.append(stage)
        logger.info(f"Pipeline 添加阶段: {stage}")

    def remove_stage(self, stage_class: type):
        """移除指定类型的所有阶段。"""
        before = len(self._stages)
        self._stages = [s for s in self._stages if not isinstance(s, stage_class)]
        removed = before - len(self._stages)
        if removed:
            logger.info(f"Pipeline 移除 {removed} 个 {stage_class.__name__} 阶段")

    def process(self, result: AnalysisResult) -> AnalysisResult:
        """
        处理 AnalysisResult, 依次执行所有阶段。
        如果某个阶段出错, 记录日志并跳过它继续执行后续阶段。
        """
        for stage in self._stages:
            try:
                result = stage.process(result)
            except Exception as e:
                logger.error(f"Pipeline 阶段 {stage} 出错: {e}", exc_info=True)
        return result

    def list_stages(self) -> list[str]:
        """返回当前管道的阶段名列表 (用于 UI 显示/调试)。"""
        return [str(s) for s in self._stages]

    def clear(self):
        """清空所有阶段。"""
        self._stages.clear()
