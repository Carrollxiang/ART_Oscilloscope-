"""
MeasurementSnapshot — 测量值单一数据源 (v0.4)

设计目标:
  - 测量面板和反馈系统读取同一份快照, 消除双路径计算导致的值不一致。
  - 支持事件窗口测量 (tag → 窗口值) 和通道级原始测量。

用法:
    snap = MeasurementSnapshot(
        sequence_num=42,
        raw_measurements={"CH1_Vpp": 3.3, ...},
        event_measurements={"A_power": 5.3, "B_power": 2.1},
    )
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class MeasurementSnapshot:
    """单帧测量快照 — 反馈与 UI 的唯一数据来源。"""

    sequence_num: int = 0

    # 通道级原始测量 (Pipeline AutoMeasure 输出)
    raw_measurements: dict[str, float] = field(default_factory=dict)

    # 事件窗口测量 (MeasurementPanel 窗口化计算输出)
    #   key = tag (语义名, 如 "A_power", "CH1 早期幅值")
    event_measurements: dict[str, float] = field(default_factory=dict)

    # 元信息
    timestamp: float = field(default_factory=time.monotonic)

    def get(self, key: str) -> float | None:
        """
        按结构化 key 获取值:
          - "event:A_power" → event_measurements["A_power"]
          - "raw:CH1_Vpp"   → raw_measurements["CH1_Vpp"]
          - "meta:seq"      → sequence_num
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
