"""
数学运算 — 通道间的数学运算生成 MATH 通道

PipelineStage 实现, 在 process() 中填充 result.math_channels。

支持的运算:
  - ADD:       CH1 + CH2
  - SUBTRACT:  CH1 - CH2
  - MULTIPLY:  CH1 × CH2
  - DIVIDE:    CH1 / CH2
  - INVERT:    -CH1
  - ABSOLUTE:  |CH1|
  - OFFSET:    CH1 + 常数
  - SCALE:     CH1 × 常数
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

from scope.model import AnalysisResult
from .pipeline import PipelineStage

logger = logging.getLogger(__name__)


class MathOp(PipelineStage):
    """
    数学运算阶段。

    每定义一个运算, 创建一个 MATH 通道输出到 result.math_channels。

    用法:
        pipeline.add_stage(MathOp("CH1 + CH2", output="MATH1"))
        pipeline.add_stage(MathOp("CH1 * 2", output="MATH2"))
        pipeline.add_stage(MathOp("-CH1", output="MATH3"))
        pipeline.add_stage(MathOp("|CH1|", output="MATH4"))
    """

    # 支持的二元运算符
    BINARY_OPS = {
        "+": np.add,
        "-": np.subtract,
        "*": np.multiply,
        "/": np.divide,  # division by zero → inf
    }

    # 支持的一元运算符
    UNARY_OPS = {
        "-": np.negative,
        "abs": np.abs,
    }

    def __init__(self, expression: str, output: str = "MATH1"):
        """
        expression: 表达式字符串, 如 "CH1 + CH2", "-CH1", "CH1 * 2"
        output: 输出通道名, 如 "MATH1"
        """
        self._expression = expression
        self._output = output
        self._parsed = self._parse(expression)

    def _parse(self, expr: str) -> dict:
        """解析表达式为结构化操作"""
        expr = expr.strip()

        # 一元操作: -CH1, |CH1|
        if expr.startswith("-") and not expr[1:].startswith(" "):
            operand = expr[1:].strip()
            return {"type": "unary", "op": "-", "args": [operand]}
        if expr.startswith("|") and expr.endswith("|"):
            operand = expr[1:-1].strip()
            return {"type": "unary", "op": "abs", "args": [operand]}

        # 二元操作: CH1 + CH2, CH1 * 3.0, CH1 - CH2
        for op_sym in ["+", "-", "*", "/"]:
            if op_sym in expr:
                parts = expr.split(op_sym, 1)
                lhs = parts[0].strip()
                rhs = parts[1].strip()
                return {
                    "type": "binary",
                    "op": op_sym,
                    "args": [lhs, rhs],
                }

        raise ValueError(f"无法解析表达式: {expr}")

    def process(self, result: AnalysisResult) -> AnalysisResult:
        try:
            data = self._compute(result)
            if data is not None:
                result.math_channels[self._output] = data
        except Exception as e:
            logger.warning(f"数学运算 {self._expression} 失败: {e}")

        return result

    def _compute(self, result: AnalysisResult) -> Optional[np.ndarray]:
        parsed = self._parsed

        if parsed["type"] == "unary":
            operand = self._resolve_arg(result, parsed["args"][0])
            if operand is None:
                return None
            func = self.UNARY_OPS.get(parsed["op"])
            if func is None:
                return None
            return func(operand).astype(np.float32)

        if parsed["type"] == "binary":
            lhs = self._resolve_arg(result, parsed["args"][0])
            rhs = self._resolve_arg(result, parsed["args"][1])
            if lhs is None or rhs is None:
                return None
            # 广播: 标量与数组运算
            func = self.BINARY_OPS.get(parsed["op"])
            if func is None:
                return None
            with np.errstate(divide="ignore", invalid="ignore"):
                result_data = func(lhs, rhs)
            # 替换 inf/nan 为 0
            if np.issubdtype(result_data.dtype, np.floating):
                result_data = np.nan_to_num(result_data, nan=0.0, posinf=0.0, neginf=0.0)
            return result_data.astype(np.float32)

        return None

    def _resolve_arg(self, result: AnalysisResult, arg: str) -> Optional[np.ndarray]:
        """解析参数: 通道名或常数"""
        # 常数
        try:
            return float(arg)
        except ValueError:
            pass

        # 通道名
        if arg in result.channels:
            return result.channels[arg].raw
        if arg in result.math_channels:
            return result.math_channels[arg]

        logger.warning(f"数学运算: 找不到 '{arg}' (可用通道: {list(result.channels.keys())})")
        return None

    def __repr__(self) -> str:
        return f"MathOp({self._expression} → {self._output})"
