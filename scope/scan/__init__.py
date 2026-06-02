"""
扫频协调器 — 全局单例

职责:
  - 持有扫频参数 ScanConfig (线程安全原子读写)
  - 持有 RtmqDevice 单例 (intf_usb 全局唯一)
  - 提供反馈开关 (调试用, 默认关闭)
  - 状态跟踪 (idle / scanning / done)
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

logger = logging.getLogger(__name__)


class ScanState(Enum):
    IDLE = auto()       # 等待下发
    SCANNING = auto()   # 扫频执行中
    DONE = auto()       # 扫频完成


@dataclass
class ScanConfig:
    """扫频参数 — 与 single_card 接口对齐"""
    base_freq: float = 146.0           # 中心频率 (MHz)
    scan_freq_amp: float = 0.5         # 扫频范围 (MHz, 总跨度)
    scan_dur: float = 1_000_000.0      # 扫频时长 (μs)

    @property
    def f_start(self) -> float:
        """扫频起始频率 (MHz)"""
        return self.base_freq - self.scan_freq_amp / 2

    @property
    def f_end(self) -> float:
        """扫频终止频率 (MHz)"""
        return self.base_freq + self.scan_freq_amp / 2


class ScanCoordinator:
    """
    扫频协调器 — 全局单例, 线程安全。

    用法:
        coord = ScanCoordinator()
        coord.rtmq_device = RtmqDevice("COM8")
        coord.scan_config = ScanConfig(base_freq=146.0, scan_freq_amp=0.5, scan_dur=1e6)
        await coord.upload_scan()   # 下发到 RWG 卡
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._scan_config = ScanConfig()
        self._feedback_enabled: bool = False
        self._state: ScanState = ScanState.IDLE
        self._rtmq_device: Optional["RtmqDevice"] = None

    # ── 属性 (线程安全) ────────────────────────────────────────

    @property
    def scan_config(self) -> ScanConfig:
        with self._lock:
            return self._scan_config

    @scan_config.setter
    def scan_config(self, config: ScanConfig):
        with self._lock:
            self._scan_config = config

    @property
    def feedback_enabled(self) -> bool:
        with self._lock:
            return self._feedback_enabled

    @feedback_enabled.setter
    def feedback_enabled(self, enabled: bool):
        with self._lock:
            self._feedback_enabled = enabled

    @property
    def state(self) -> ScanState:
        with self._lock:
            return self._state

    @state.setter
    def state(self, s: ScanState):
        with self._lock:
            self._state = s
        logger.info(f"扫频状态 → {s.name}")

    @property
    def rtmq_device(self) -> Optional["RtmqDevice"]:
        return self._rtmq_device

    @rtmq_device.setter
    def rtmq_device(self, device: "RtmqDevice"):
        self._rtmq_device = device

    # ── 操作 ──────────────────────────────────────────────────

    async def upload_scan(self, config: ScanConfig | None = None):
        """
        下发扫频配置到 RWG 卡。

        single_card() 在 executor 中执行, 不阻塞事件循环。
        """
        if config is not None:
            self.scan_config = config

        cfg = self.scan_config
        rtmq = self._rtmq_device
        if rtmq is None:
            logger.error("RtmqDevice 未初始化, 无法下发扫频配置")
            return

        import asyncio
        self.state = ScanState.SCANNING

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                rtmq.single_card,
                cfg.scan_freq_amp,
                cfg.base_freq,
                cfg.scan_dur,
            )
            logger.info(
                f"扫频配置已下发: base={cfg.base_freq}MHz, "
                f"span={cfg.scan_freq_amp}MHz, dur={cfg.scan_dur}μs"
            )
        except Exception as e:
            logger.error(f"扫频下发失败: {e}")
            self.state = ScanState.IDLE

    def mark_done(self):
        """标记扫频完成 (在收到扫频结束后调用)。"""
        self.state = ScanState.DONE

    def reset(self):
        """重置为空闲状态。"""
        self.state = ScanState.IDLE

    # ── 便捷方法: 从 AnalysisResult 获取当前配置的副本 ────────

    def snapshot(self) -> ScanConfig:
        """原子获取当前配置快照 (供采集线程使用)。"""
        with self._lock:
            return ScanConfig(
                base_freq=self._scan_config.base_freq,
                scan_freq_amp=self._scan_config.scan_freq_amp,
                scan_dur=self._scan_config.scan_dur,
            )
