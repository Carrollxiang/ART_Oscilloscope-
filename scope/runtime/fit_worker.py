"""
FitWorker — 独立线程运行 Pipeline + EventWindowSpec 计算

消费 frame.measured (AnalysisResult)，产出 frame.fitted (FittedSnapshot)。

线程模型：
  - 独立线程运行，不阻塞采集线程
  - Pipeline 是 CPU 密集型（numpy），放在线程中天然合适
  - EventWindowSpec 做时间窗切片，同样在 numpy 空间完成

用法:
    worker = FitWorker(event_bus)
    worker.start()    # 启动线程
    ...
    worker.stop()     # 停止线程
"""

from __future__ import annotations

import logging
import time
import threading
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from scope.model import AnalysisResult, ChannelData
from scope.processing import (
    ProcessingPipeline,
    AutoMeasure,
    MathOp,
    FFTAnalyze,
    MEASUREMENT_FUNCTIONS,
)
from scope.runtime import EventBus, FittedSnapshot

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# EventWindowSpec — 事件窗口测量规格
# ═══════════════════════════════════════════════════════════════


@dataclass
class EventWindowSpec:
    """
    事件窗口测量规格。

    在原始波形上切出 [start_ms, end_ms) 时间段，计算指定特征值。
    每个窗口有一个语义 tag，用于订阅和 UI 显示。

    用法:
        spec = EventWindowSpec(tag="A_power", channel="CH1",
                                start_ms=10.0, end_ms=100.0, feature="Vrms")
        value = spec.compute(channel_data)  # → 0.85
    """

    tag: str = ""             # 语义名，如 "A_power"、"早期幅值"
    channel: str = "CH1"      # 通道名 "CH1"~"CH16"
    start_ms: float = 0.0     # 窗口起始（相对帧起点，毫秒）
    end_ms: float = 100.0     # 窗口结束（相对帧起点，毫秒）
    feature: str = "Vrms"     # 特征: Vpp | Vmax | Vmin | Vrms | Mean | Integral
    semantic: str = ""        # 可选说明文字

    def compute(self, channel_data: ChannelData) -> Optional[float]:
        """
        从原始波形中切片并计算特征值。

        Args:
            channel_data: 通道原始数据（含 raw + time_axis + sample_rate）

        Returns:
            特征值，或 None（切片为空时）
        """
        # 计算采样点索引
        fs = channel_data.sample_rate
        start_idx = max(0, int(self.start_ms / 1000.0 * fs))
        end_idx = min(len(channel_data.raw),
                      int(self.end_ms / 1000.0 * fs))

        if end_idx <= start_idx:
            return None

        segment = channel_data.raw[start_idx:end_idx]
        if len(segment) == 0:
            return None

        if self.feature == "Vpp":
            return float(np.ptp(segment))
        elif self.feature == "Vmax":
            return float(np.max(segment))
        elif self.feature == "Vmin":
            return float(np.min(segment))
        elif self.feature == "Vrms":
            return float(np.sqrt(np.mean(np.square(segment))))
        elif self.feature == "Mean":
            return float(np.mean(segment))
        elif self.feature == "Integral":
            return float(np.trapz(segment)) / fs
        else:
            logger.warning(f"未知特征: {self.feature}")
            return None


# ═══════════════════════════════════════════════════════════════
# FitWorker — 独立线程拟合计算
# ═══════════════════════════════════════════════════════════════


class FitWorker:
    """
    独立线程运行 Pipeline + EventWindowSpec 计算。

    生命周期:
        worker = FitWorker(event_bus)
        worker.start()    → 创建线程，开始消费 frame.measured
        ...
        worker.stop()     → 停止线程
    """

    def __init__(
        self,
        event_bus: EventBus,
        channel_count: int = 16,
    ):
        self._event_bus = event_bus
        self._channel_count = channel_count
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # 创建 ProcessingPipeline (与当前 ScopeApp 一致)
        self._pipeline = ProcessingPipeline()
        self._pipeline.add_stage(
            AutoMeasure(
                measurements=list(MEASUREMENT_FUNCTIONS.keys()),
                channels=[f"CH{i+1}" for i in range(channel_count)],
            )
        )
        self._pipeline.add_stage(
            MathOp("CH1 + CH2", output="MATH1")
        )
        self._pipeline.add_stage(
            FFTAnalyze(channels=["CH1", "CH2"])
        )

        # EventWindowSpec 列表（由 MeasurementPanel 运行时同步配置）
        # 通过 set_event_windows() 更新
        self._event_windows: list[EventWindowSpec] = []
        self._ew_lock = threading.Lock()

        # 统计
        self._frames_processed = 0
        self._total_latency_ms = 0.0

        # 订阅 frame.measured 队列
        self._queue = event_bus.subscribe("frame.measured")

    @property
    def metrics(self) -> dict:
        """运行统计。"""
        avg_latency = (
            self._total_latency_ms / self._frames_processed
            if self._frames_processed > 0 else 0.0
        )
        return {
            "frames_processed": self._frames_processed,
            "avg_latency_ms": avg_latency,
            "queue_size": self._queue.qsize,
            "queue_stats": self._queue.stats_text(),
        }

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def set_event_windows(self, windows: list[EventWindowSpec]):
        """运行时更新事件窗口配置（线程安全）。"""
        with self._ew_lock:
            self._event_windows = list(windows)

    # ── 生命周期 ───────────────────────────────────────────────

    def start(self):
        """启动 FitWorker 线程。"""
        if self._thread is not None and self._thread.is_alive():
            logger.warning("FitWorker 已在运行")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="fit-worker",
        )
        self._thread.start()
        logger.info("FitWorker 已启动 (独立线程)")

    def stop(self):
        """请求停止。设置停止标记，等待当前帧处理完。"""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
            if self._thread.is_alive():
                logger.warning("FitWorker 线程未在 3s 内退出")
        logger.info(f"FitWorker 已停止 (处理 {self._frames_processed} 帧)")

    # ── 核心循环 ───────────────────────────────────────────────

    def _run_loop(self):
        """主循环：消费 frame.measured → Pipeline → publish frame.fitted"""
        logger.info("FitWorker 循环开始")
        while not self._stop_event.is_set():
            try:
                result = self._queue.get(timeout=0.1)
                if result is None:
                    continue
                self._process_frame(result)
            except Exception as e:
                logger.error(f"FitWorker 异常: {e}", exc_info=True)
        logger.info(f"FitWorker 循环结束 (处理 {self._frames_processed} 帧)")

    def _process_frame(self, result: AnalysisResult):
        """处理一帧：Pipeline + EventWindowSpec → FittedSnapshot"""
        t0 = time.monotonic()

        # 1. Pipeline 计算 (AutoMeasure / MathOp / FFT)
        result = self._pipeline.process(result)

        # 2. 提取通道级测量
        channel_measurements = dict(result.measurements)

        # 3. 事件窗口计算
        event_measurements: dict[str, float] = {}
        with self._ew_lock:
            windows = list(self._event_windows)
        for spec in windows:
            ch_data = result.channels.get(spec.channel)
            if ch_data is None:
                continue
            value = spec.compute(ch_data)
            if value is not None:
                event_measurements[spec.tag] = value

        # 4. 组装 FittedSnapshot
        latency_ms = (time.monotonic() - t0) * 1000
        snap = FittedSnapshot(
            sequence_num=result.sequence_num,
            channel_measurements=channel_measurements,
            event_measurements=event_measurements,
            pipeline_latency_ms=latency_ms,
        )

        # 5. 发布到 frame.fitted
        self._event_bus.publish("frame.fitted", snap)

        # 6. 统计
        self._frames_processed += 1
        self._total_latency_ms += latency_ms

        if self._frames_processed % 100 == 0:
            logger.info(
                f"FitWorker: #{snap.sequence_num} "
                f"latency={latency_ms:.1f}ms "
                f"ch_meas={len(channel_measurements)} "
                f"ev_meas={len(event_measurements)}"
            )
