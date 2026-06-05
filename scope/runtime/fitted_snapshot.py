"""
FittedSnapshot — MeasurementProcessor 产出数据包

MeasurementProcessor 消费 RawFrame 后，产出此轻量快照。
不含原始波形数据，仅含测量结果。所有消费者（UI、反馈）从此读取。

用法:
    snap = FittedSnapshot(
        sequence_num=42,
        event_measurements={"CH1_vpp": 3.3, "A_power": 5.3},
    )
    value = snap.get("CH1_vpp")
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FittedSnapshot:
    """MeasurementProcessor 产出 — 测量结果快照，不含原始波形。"""

    sequence_num: int = 0

    # 测量结果 (MeasurementSpec.tag → value)
    event_measurements: dict[str, float] = field(default_factory=dict)

    # 元信息
    timestamp: float = field(default_factory=time.monotonic)
    pipeline_latency_ms: float = 0.0

    def get(self, key: str) -> Optional[float]:
        """
        按 key 获取测量值:
          - "tag_name"     → event_measurements["tag_name"]
          - "meta:seq"     → float(sequence_num)
          - "meta:latency" → pipeline_latency_ms
        """
        if key.startswith("meta:"):
            meta_key = key[5:]
            if meta_key == "seq":
                return float(self.sequence_num)
            if meta_key == "timestamp":
                return self.timestamp
            if meta_key == "latency":
                return self.pipeline_latency_ms
            return None
        return self.event_measurements.get(key)

    def as_flat_dict(self) -> dict[str, float]:
        """返回所有测量值（用于反馈 dispatch）"""
        return dict(self.event_measurements)
