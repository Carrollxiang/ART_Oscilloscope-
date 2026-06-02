"""
MeasurementSnapshot — 测量值单一数据源 (v0.5)

设计目标:
  - 测量面板和反馈系统读取同一份快照, 消除双路径计算导致的值不一致。
  - 支持事件窗口测量 (tag → 窗口值) 和通道级原始测量。
  - v0.5: 新增 ch0_raw / ch0_time_axis 引用, 供 FitWorker 做拟合。
  - v0.5: 新增 FittedSnapshot 子类, 携带拟合结果, 供 FeedbackWorker 和 UI 消费。

用法:
    snap = MeasurementSnapshot(
        sequence_num=42,
        raw_measurements={"CH0_Vpp": 3.3, ...},
        ch0_raw=raw_array,
        ch0_time_axis=time_array,
    )

    fitted = FittedSnapshot.from_snapshot(snap, fit_result=result)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np
    from scope.scan.analysis import ScanFitResult


@dataclass
class MeasurementSnapshot:
    """单帧测量快照 — 反馈与 UI 的唯一数据来源。"""

    sequence_num: int = 0

    # 通道级原始测量 (Pipeline AutoMeasure 输出)
    raw_measurements: dict[str, float] = field(default_factory=dict)

    # 事件窗口测量 (MeasurementPanel 窗口化计算输出)
    #   key = tag (语义名, 如 "A_power", "CH0 早期幅值")
    event_measurements: dict[str, float] = field(default_factory=dict)

    # 元信息
    timestamp: float = field(default_factory=time.monotonic)

    # v0.5: 原始波形引用 (传引用不复制, 拟合完释放)
    ch0_raw: np.ndarray | None = None
    ch0_time_axis: np.ndarray | None = None

    def get(self, key: str) -> float | None:
        """
        按结构化 key 获取值:
          - "event:A_power" → event_measurements["A_power"]
          - "raw:CH0_Vpp"   → raw_measurements["CH0_Vpp"]
          - "meta:seq"      → sequence_num
          - "scan:f0"       → fit_result.f0 (仅 FittedSnapshot)
          - 无前缀时先查 event, 再查 raw (兼容旧订阅)
        """
        if key.startswith("event:"):
            return self.event_measurements.get(key[6:])
        elif key.startswith("raw:"):
            return self.raw_measurements.get(key[4:])
        elif key.startswith("meta:"):
            meta_key = key[5:]
            if meta_key in ("seq", "sequence_num"):
                return float(self.sequence_num)
            return None
        else:
            # 兼容旧 key: 先查 event, 再查 raw
            if key == "sequence_num":
                return float(self.sequence_num)
            v = self.event_measurements.get(key)
            if v is not None:
                return v
            return self.raw_measurements.get(key)

    def as_dict(self) -> dict[str, float]:
        """展开为扁平 dict (用于 mini chart 等消费者)。"""
        result: dict[str, float] = {}
        result.update(self.raw_measurements)
        result.update(self.event_measurements)
        return result


@dataclass
class FittedSnapshot(MeasurementSnapshot):
    """帧测量 + 拟合结果。由 FitWorker 构建并发布到 frame.fitted。"""

    fit_result: ScanFitResult | None = None

    @classmethod
    def from_snapshot(
        cls,
        snap: MeasurementSnapshot,
        fit_result: ScanFitResult | None = None,
    ) -> FittedSnapshot:
        """从 MeasurementSnapshot 构建，继承全部字段，释放 ch0_raw。"""
        return cls(
            sequence_num=snap.sequence_num,
            raw_measurements=dict(snap.raw_measurements),
            event_measurements=dict(snap.event_measurements),
            timestamp=snap.timestamp,
            ch0_raw=None,            # 拟合完成，释放引用
            ch0_time_axis=None,
            fit_result=fit_result,
        )

    @property
    def f0(self) -> float | None:
        return self.fit_result.f0 if self.fit_result else None

    @property
    def gamma(self) -> float | None:
        return self.fit_result.gamma if self.fit_result else None

    @property
    def r_squared(self) -> float | None:
        return self.fit_result.r_squared if self.fit_result else None

    def get(self, key: str) -> float | None:
        """
        扩展 get: 支持 scan: 前缀访问拟合结果。
          - "scan:f0"     → fit_result.f0
          - "scan:gamma"  → fit_result.gamma
          - "scan:r2"     → fit_result.r_squared
        """
        if key.startswith("scan:"):
            if self.fit_result is None:
                return None
            scan_key = key[5:]
            if scan_key == "f0":
                return self.fit_result.f0
            elif scan_key in ("gamma", "hwhm"):
                return self.fit_result.gamma
            elif scan_key in ("r2", "r_squared"):
                return self.fit_result.r_squared
            elif scan_key == "amplitude":
                return self.fit_result.amplitude
            elif scan_key == "offset":
                return self.fit_result.offset
            return None
        return super().get(key)

    def as_dict(self) -> dict[str, float]:
        """展开为扁平 dict, 包含拟合结果。"""
        result = super().as_dict()
        if self.fit_result is not None:
            result["scan_f0"] = self.fit_result.f0
            result["scan_gamma"] = self.fit_result.gamma
            result["scan_r2"] = self.fit_result.r_squared
            result["scan_amplitude"] = self.fit_result.amplitude
            result["scan_offset"] = self.fit_result.offset
        return result
