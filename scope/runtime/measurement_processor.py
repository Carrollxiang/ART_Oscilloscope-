"""
MeasurementProcessor — 测量处理器

消费 RawFrame，按 MeasurementSpec 列表计算特征值，输出 FittedSnapshot。

职责:
  1. 订阅 frame.raw topic
  2. 遍历 MeasurementSpec 列表计算每个测量值（先标量规格，再比值规格）
  3. 发布 FittedSnapshot 到 frame.fitted topic

比值规格 (feature="Ratio") 引用两个标量规格的 tag，同帧内相除；
分母接近 0 或源缺失时本帧不产出该值，原因写入 FittedSnapshot.invalid_reasons。

运行模型:
  - 独立线程，不阻塞采集线程
  - CPU 密集型计算 (numpy)，适合独立线程
"""

from __future__ import annotations

import logging
import math
import threading
import time
from typing import Optional

import numpy as np

from scope.model import RawFrame
from .event_bus import EventBus
from .fitted_snapshot import FittedSnapshot
from .measurement_spec import MeasurementSpec

logger = logging.getLogger(__name__)

# 比值规格的分母保护阈值: |分母| 小于该值视为除零风险, 本帧不产出该比值。
RATIO_MIN_DENOM_ABS: float = 1e-9

# 同一测量项的无效告警节流间隔 (秒): 比值无效是逐帧事件, 不节流会刷爆日志
INVALID_LOG_INTERVAL_S: float = 1.0


class MeasurementProcessor:
    """
    测量处理器 — 消费 RawFrame，按规格计算，输出 FittedSnapshot。
    
    用法:
        processor = MeasurementProcessor(event_bus, specs=[
            MeasurementSpec(tag="CH1_vpp", channel=0, feature="Vpp"),
            MeasurementSpec(tag="CH2_power", channel=1, start_ms=10, end_ms=100, feature="Vrms"),
        ])
        processor.start()
        ...
        processor.stop()
    """
    
    def __init__(self, event_bus: EventBus, specs: Optional[list[MeasurementSpec]] = None):
        self._event_bus = event_bus
        self._specs = specs or []
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        
        # 订阅 frame.raw
        self._queue = event_bus.subscribe("frame.raw")
        
        # 统计
        self._frames_processed = 0
        self._total_latency_ms = 0.0

        # 无效告警节流: tag → 上次告警 monotonic 时间
        self._invalid_log_ts: dict[str, float] = {}
    
    @property
    def metrics(self) -> dict:
        """运行统计"""
        avg_latency = (
            self._total_latency_ms / self._frames_processed
            if self._frames_processed > 0 else 0.0
        )
        return {
            "frames_processed": self._frames_processed,
            "avg_latency_ms": avg_latency,
            "queue_size": self._queue.qsize,
        }
    
    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()
    
    def set_specs(self, specs: list[MeasurementSpec]):
        """运行时更新测量规格（线程安全）"""
        with self._lock:
            # 只有规格数量变化时才记录日志
            if len(self._specs) != len(specs):
                logger.info(f"MeasurementSpec 已更新: {len(specs)} 项")
            self._specs = list(specs)
    
    # ── 生命周期 ───────────────────────────────────────────────
    
    def start(self):
        """启动处理线程"""
        if self._thread is not None and self._thread.is_alive():
            logger.warning("MeasurementProcessor 已在运行")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="measurement-processor",
        )
        self._thread.start()
        logger.info(f"MeasurementProcessor 已启动 ({len(self._specs)} specs)")
    
    def stop(self):
        """停止处理线程"""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
            if self._thread.is_alive():
                logger.warning("MeasurementProcessor 线程未在 3s 内退出")
        logger.info(f"MeasurementProcessor 已停止 (处理 {self._frames_processed} 帧)")
    
    # ── 核心循环 ───────────────────────────────────────────────
    
    def _run_loop(self):
        """主循环：消费 frame.raw → 计算 → publish frame.fitted"""
        while not self._stop_event.is_set():
            try:
                frame = self._queue.get(timeout=0.1)
                if frame is not None:
                    self._process_frame(frame)
            except Exception as e:
                logger.error(f"MeasurementProcessor 异常: {e}", exc_info=True)
    
    def _process_frame(self, frame: RawFrame):
        """处理一帧：遍历所有 spec 计算"""
        t0 = time.monotonic()
        
        measurements = {}
        invalid_reasons: dict[str, str] = {}
        with self._lock:
            specs = list(self._specs)

        scalar_specs = [s for s in specs if not s.is_ratio()]
        ratio_specs = [s for s in specs if s.is_ratio()]

        # ── 第一遍: 标量规格 (单通道切片取特征) ──
        for spec in scalar_specs:
            try:
                value = self._compute(frame, spec)
                if value is not None:
                    measurements[spec.tag] = value
            except Exception as e:
                logger.warning(f"计算 spec '{spec.tag}' 失败: {e}")

        # ── 第二遍: 比值规格 (只引用本帧已算出的标量值, 同帧相除) ──
        # scalar_values 取副本, 使比值结果不会回流到源池 — 禁止 ratio 引用 ratio。
        scalar_values = dict(measurements)
        for spec in ratio_specs:
            try:
                value, reason = self._compute_ratio(spec, scalar_values)
            except Exception as e:
                value, reason = None, f"计算异常: {e}"
            if value is None:
                invalid_reasons[spec.tag] = reason
                self._log_invalid_ratio(spec.tag, reason)
            else:
                measurements[spec.tag] = value
        
        latency_ms = (time.monotonic() - t0) * 1000
        snap = FittedSnapshot(
            sequence_num=frame.sequence_num,
            event_measurements=measurements,
            invalid_reasons=invalid_reasons,
            pipeline_latency_ms=latency_ms,
        )
        self._event_bus.publish("frame.fitted", snap)
        
        self._frames_processed += 1
        self._total_latency_ms += latency_ms
        
        if self._frames_processed % 100 == 0:
            logger.debug(
                f"MeasurementProcessor: #{snap.sequence_num} "
                f"latency={latency_ms:.1f}ms "
                f"measurements={len(measurements)}"
            )
    
    @staticmethod
    def _compute_ratio(
        spec: MeasurementSpec,
        values: dict[str, float],
    ) -> tuple[Optional[float], str]:
        """
        计算比值规格: 分子 / 分母, 两个源都取自本帧标量结果。

        Returns:
            (value, "")   计算成功
            (None, reason) 源缺失或分母接近 0; reason 为人类可读原因
        """
        num = values.get(spec.numerator_tag)
        if num is None:
            return None, f"分子测量 '{spec.numerator_tag}' 本帧无有效值"

        den = values.get(spec.denominator_tag)
        if den is None:
            return None, f"分母测量 '{spec.denominator_tag}' 本帧无有效值"

        if math.isnan(num) or math.isnan(den):
            # NaN 会污染 PID 输出并最终写进 DAC, 直接判为无效
            return None, "源测量值为 NaN"

        if abs(den) < RATIO_MIN_DENOM_ABS:
            return None, f"分母接近 0 ({den:.6g})"

        return num / den, ""

    def _log_invalid_ratio(self, tag: str, reason: str):
        """比值无效告警 — 同一 tag 最多每 INVALID_LOG_INTERVAL_S 秒一条。"""
        now = time.monotonic()
        if now - self._invalid_log_ts.get(tag, 0.0) < INVALID_LOG_INTERVAL_S:
            return
        self._invalid_log_ts[tag] = now
        logger.warning(f"比值测量 '{tag}' 本帧无效: {reason}")

    @staticmethod
    def _compute(frame: RawFrame, spec: MeasurementSpec) -> Optional[float]:
        """
        单个 spec 的计算逻辑。
        
        从 RawFrame 中切片并计算特征值。
        """
        if spec.channel < 0 or spec.channel >= frame.n_channels:
            logger.warning(f"通道索引越界: {spec.channel} >= {frame.n_channels}")
            return None
        
        raw = frame.data[spec.channel]
        fs = frame.sample_rate
        n_samples = frame.n_samples
        
        # 计算切片索引
        start_idx = max(0, int(spec.start_ms / 1000.0 * fs))
        end_idx = n_samples if spec.end_ms <= 0 else min(n_samples, int(spec.end_ms / 1000.0 * fs))
        
        if end_idx <= start_idx:
            return None
        
        segment = raw[start_idx:end_idx]
        if len(segment) == 0:
            return None
        
        # 特征计算 - 只支持 Vpp, Vmax, Vmin, Mean
        feature = spec.feature.lower()
        
        if feature == "vpp":
            return float(np.ptp(segment))
        elif feature == "vmax":
            return float(np.max(segment))
        elif feature == "vmin":
            return float(np.min(segment))
        elif feature == "mean":
            return float(np.mean(segment))
        else:
            logger.warning(f"未知特征类型: {spec.feature}")
            return None
