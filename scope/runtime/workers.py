"""
Workers — EventBus 消费者 (v0.5)

三个独立 worker, 各自订阅 EventBus topic, 解耦执行:
  - FitWorker:       frame.measured → 拟合 → frame.fitted
  - FeedbackWorker:  frame.fitted → PID 反馈
  - UIBridge:        frame.measured → 主波形 + frame.fitted → 扫频面板 / 迷你趋势图
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Optional

from scope.runtime.event_bus import BoundedQueue, DropStrategy, EventBus
from scope.runtime.measurement_snapshot import (
    FittedSnapshot,
    MeasurementSnapshot,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# FitWorker
# ──────────────────────────────────────────────────────────────

class FitWorker:
    """
    拟合工作线程。

    订阅 frame.measured, 执行 V(f) 映射 + Lorentzian 拟合,
    发布 FittedSnapshot 到 frame.fitted。
    """

    def __init__(self, bus: EventBus, scan_coordinator):
        self._bus = bus
        self._sc = scan_coordinator
        self._q: BoundedQueue = bus.subscribe(
            "frame.measured",
            maxsize=2,
            on_drop=DropStrategy.DROP_OLDEST,
            name="fit",
        )
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def start(self):
        self._running = True
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="fit-worker",
        )
        self._thread.start()
        logger.info("FitWorker started")

    def stop(self):
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        logger.info("FitWorker stopped")

    def _run(self):
        while self._running:
            snap = self._q.get(timeout=5.0)
            if snap is None:
                continue
            try:
                fit_result = self._do_fit(snap)
                fitted = FittedSnapshot.from_snapshot(snap, fit_result=fit_result)
                snap.ch0_raw = None          # 释放原始波形引用
                snap.ch0_time_axis = None
                snap._analysis_result = None  # 释放 AnalysisResult 引用
                self._bus.publish("frame.fitted", fitted)
            except Exception as e:
                logger.error(f"FitWorker error: {e}", exc_info=True)

    def _do_fit(self, snap: MeasurementSnapshot):
        """
        从 snapshot 提取 ch0_raw, 结合 ScanCoordinator 参数执行拟合。
        失败时返回全零 ScanFitResult (不返回 None)。
        """
        from scope.scan.analysis import ScanFitResult, map_to_frequency_domain, fit_lorentzian

        cfg = self._sc.snapshot()
        if snap.ch0_raw is None or len(snap.ch0_raw) <= 2:
            return ScanFitResult()
        try:
            f_axis, v_f = map_to_frequency_domain(
                snap.ch0_raw, snap.ch0_time_axis,
                cfg.base_freq, cfg.scan_freq_amp, cfg.scan_dur,
            )
            return fit_lorentzian(f_axis, v_f)
        except Exception as e:
            logger.warning(f"FitWorker 拟合异常: {e}")
            return ScanFitResult()


# ──────────────────────────────────────────────────────────────
# FeedbackWorker
# ──────────────────────────────────────────────────────────────

class FeedbackWorker:
    """
    反馈工作线程。

    订阅指定 topic (默认 frame.fitted), 检查自身 enabled 开关,
    执行 PID step → RPC 发送。

    Parameters:
        subscribe_topic: 订阅的 EventBus topic。
            - master 分支: "frame.measured" (无拟合中间层)
            - freq_lock 分支: "frame.fitted" (消费拟合结果)
    """

    def __init__(self, bus: EventBus, feedback_manager,
                 subscribe_topic: str = "frame.fitted"):
        self._bus = bus
        self._mgr = feedback_manager
        self._q: BoundedQueue = bus.subscribe(
            subscribe_topic,
            maxsize=2,
            on_drop=DropStrategy.DROP_OLDEST,
            name="feedback",
        )
        self._enabled: bool = False
        self._async_loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool):
        self._enabled = value
        logger.info(f"FeedbackWorker enabled={value}")

    def start(self):
        self._running = True
        self._thread = threading.Thread(
            target=self._async_worker, daemon=True, name="feedback-worker",
        )
        self._thread.start()
        logger.info("FeedbackWorker started")

    def stop(self):
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        if self._async_loop and not self._async_loop.is_closed():
            self._async_loop.call_soon_threadsafe(self._async_loop.stop)
        logger.info("FeedbackWorker stopped")

    def _async_worker(self):
        """独立 asyncio loop: 消费队列 → dispatch。"""
        self._async_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._async_loop)
        try:
            self._async_loop.run_until_complete(self._consume_loop())
        finally:
            self._async_loop.close()

    async def _consume_loop(self):
        while self._running:
            try:
                snap = await asyncio.to_thread(self._q.get, timeout=5.0)
            except Exception:
                continue
            if snap is None:
                continue
            if not self._enabled:
                continue
            try:
                latency_ms = (time.monotonic() - snap.timestamp) * 1000
                if latency_ms > 500:
                    logger.warning(
                        f"反馈延迟 {latency_ms:.0f}ms, "
                        f"seq={snap.sequence_num}"
                    )
                await self._mgr.dispatch(snap)
            except Exception as e:
                logger.error(f"FeedbackWorker dispatch error: {e}", exc_info=True)


# ──────────────────────────────────────────────────────────────
# UIBridge
# ──────────────────────────────────────────────────────────────

class UIBridge:
    """
    Qt 信号桥接 — 无线程, 通过 EventBus 回调订阅直接 emit。

    subscribe_callback 注册后, publish 时在发布者线程内直接调用回调,
    回调内 emit Qt signal (线程安全), 主线程接收后刷新 UI。

    保证主波形和趋势图与数据发布完全同步, 无额外计时器。

    Parameters:
        scan_panel_signal / trend_update_signal: 可选。
            - freq_lock 分支: 都传入, 订阅 frame.fitted
            - master 分支: 传 None, 仅订阅 frame.measured
    """

    def __init__(
        self,
        bus: EventBus,
        data_received_signal,        # MainWindow.data_received (pyqtSignal)
        scan_panel_signal=None,      # MainWindow.scan_panel_update (pyqtSignal, optional)
        trend_update_signal=None,    # MainWindow.trend_update (pyqtSignal, optional)
    ):
        self._bus = bus
        self._data_sig = data_received_signal
        self._scan_sig = scan_panel_signal
        self._trend_sig = trend_update_signal

        # 回调订阅: publish 时直接调用, 无队列延迟
        bus.subscribe_callback(
            "frame.measured",
            self._on_measured,
            name="ui-waveform",
        )
        if scan_panel_signal is not None or trend_update_signal is not None:
            bus.subscribe_callback(
                "frame.fitted",
                self._on_fitted,
                name="ui-trend",
            )

    def start(self):
        # 无线程启动, 回调由 EventBus.publish 触发
        logger.info("UIBridge started (callback mode)")

    def stop(self):
        logger.info("UIBridge stopped")

    def _on_measured(self, item):
        """frame.measured 回调: 从 snapshot 取 AnalysisResult, emit 到主波形。"""
        result = getattr(item, "_analysis_result", None)
        if result is not None:
            self._data_sig.emit(result)

    def _on_fitted(self, item):
        """
        frame.fitted 回调:
        - fit_result → scan_panel signal (扫频面板)
        - f0        → trend_update signal (迷你趋势图)
        """
        fitted: FittedSnapshot = item
        if self._scan_sig is not None and fitted.fit_result is not None:
            self._scan_sig.emit(fitted.fit_result)
        if self._trend_sig is not None:
            f0_val = fitted.f0 if fitted.f0 is not None else 0.0
            self._trend_sig.emit({"f0": f0_val, "__timestamp__": fitted.timestamp})
