"""
ConfigChange — 硬件配置变更指令 (v0.4)

走控制面（config.change topic），替代 UI 线程直接调用硬件接口。
ConfigWorker 消费此指令，在帧边界原子生效。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from scope.hardware.device import DeviceConfig


@dataclass
class ConfigChange:
    """硬件配置变更指令（走控制面 config.change topic）。"""

    device_config: DeviceConfig
    """新的 DeviceConfig（sample_rate / record_length / channels / 电压量程）。"""

    art_params: dict[str, Any]
    """ART 参数全量（device_name / ai_channels / trigger_source / ...）。"""

    change_id: int
    """单调递增 ID，用于去重（防止同一配置重复应用）。"""

    timestamp: float = field(default_factory=time.monotonic)
    """指令创建时间。"""
