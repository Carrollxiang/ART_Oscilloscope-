"""
FittedSnapshot — FitWorker 产出数据包 (v0.4)

FitWorker 消费原始帧（AnalysisResult）后，产出此轻量快照。
不含原始波形数据，仅含计算结果。所有消费者（UI、反馈）从此读取。

用法:
    snap = FittedSnapshot(
        sequence_num=42,
        channel_measurements={"CH1_Vpp": 3.3, "CH1_Freq": 1000.0},
        event_measurements={"A_power": 5.3},
    )
    flat = snap.as_flat_dict()
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class FittedSnapshot:
    """FitWorker 产出 — 全部计算结果，不含原始波形。"""

    sequence_num: int = 0

    # 通道级测量 (AutoMeasure / Pipeline 产出)
    #   {"CH1_Vpp": 3.3, "CH1_Freq": 1000.0, ...}
    channel_measurements: dict[str, float] = field(default_factory=dict)

    # 事件窗口测量 (EventWindowSpec 产出)
    #   {"A_power": 5.3, "B_power": 2.1, ...}
    event_measurements: dict[str, float] = field(default_factory=dict)

    # 元信息
    timestamp: float = field(default_factory=time.monotonic)
    pipeline_latency_ms: float = 0.0  # Pipeline + 窗口计算耗时

    def get(self, key: str) -> float | None:
        """
        按结构化 key 获取值:
          - "event:A_power" → event_measurements["A_power"]
          - "raw:CH1_Vpp"   → channel_measurements["CH1_Vpp"]
          - "meta:seq"      → float(sequence_num)
          - 无前缀时先查 event, 再查 channel (兼容旧订阅)
        """
        if key.startswith("event:"):
            return self.event_measurements.get(key[6:])
        elif key.startswith("raw:"):
            return self.channel_measurements.get(key[4:])
        elif key.startswith("meta:"):
            meta_key = key[5:]
            if meta_key == "seq":
                return float(self.sequence_num)
            if meta_key == "timestamp":
                return self.timestamp
            return None
        else:
            # 兼容旧 key: 先查 event, 再查 channel
            v = self.event_measurements.get(key)
            if v is not None:
                return v
            return self.channel_measurements.get(key)

    def as_flat_dict(self) -> dict[str, float]:
        """合并所有测量值为扁平 dict（用于 MiniChart / 日志 / dispatch）。"""
        result: dict[str, float] = {}
        result.update(self.channel_measurements)
        result.update(self.event_measurements)
        return result
