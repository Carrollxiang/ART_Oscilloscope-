"""
UIBridge — 采集线程 → Qt 主线程的桥接层 (v0.4)

消费 EventBus 中的 frame.raw, frame.fitted, feedback.status 队列，
通过 pyqtSignal 桥接到 Qt 主线程更新 UI。

UIBridge 自身运行在采集线程（不创建额外线程），
只做非阻塞 get_nowait() + emit()。

用法:
    bridge = UIBridge(event_bus)
    # 在采集线程中调用:
    bridge.poll()

    # 在主线程中连接信号:
    bridge.signal_raw_frame.connect(self._on_ui_raw_frame)
    bridge.signal_fitted.connect(self._on_ui_fitted)
    bridge.signal_feedback_status.connect(self._on_ui_feedback_status)
"""

from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal

from scope.model import RawFrame
from scope.runtime import EventBus, FittedSnapshot, FeedbackStatusSnapshot

logger = logging.getLogger(__name__)


class UIBridge(QObject):
    """
    采集线程 → Qt 主线程桥接。

    poll() 方法在采集线程中非阻塞调用，检查三个 EventBus 队列，
    有数据时通过 pyqtSignal 排入 Qt 主线程的事件队列。
    """

    # 原始帧 — 用于主波形渲染
    signal_raw_frame = pyqtSignal(object)  # RawFrame

    # 拟合结果 — 用于测量面板 + MiniChart
    signal_fitted = pyqtSignal(object)     # FittedSnapshot

    # 反馈状态 — 用于反馈面板 + 状态栏
    signal_feedback_status = pyqtSignal(object)  # FeedbackStatusSnapshot

    def __init__(self, event_bus: EventBus, parent=None):
        super().__init__(parent)
        self._event_bus = event_bus

        # 订阅三个 topic
        self._raw_queue = event_bus.subscribe("frame.raw")
        self._fitted_queue = event_bus.subscribe("frame.fitted")
        self._status_queue = event_bus.subscribe("feedback.status")

        # 统计
        self._raw_emitted = 0
        self._fitted_emitted = 0
        self._raw_errors = 0
        self._fitted_errors = 0
        self._status_emitted = 0
        self._status_errors = 0

    @property
    def metrics(self) -> dict:
        return {
            "raw_emitted": self._raw_emitted,
            "fitted_emitted": self._fitted_emitted,
            "raw_errors": self._raw_errors,
            "fitted_errors": self._fitted_errors,
            "status_emitted": self._status_emitted,
            "status_errors": self._status_errors,
        }

    def poll(self):
        """
        非阻塞轮询三个队列，有数据则 emit QSignal。

        队列中积压的旧帧直接丢弃，只转发最新一帧 (丢旧留新)，
        避免 Qt 主线程处理不过来时事件队列无限积压导致内存增长。

        每个 emit 被 try/except 包裹，确保单个信号处理异常
        不会中断整个 poll 循环或传播到采集线程。
        """
        self._poll_latest(self._raw_queue, self.signal_raw_frame,
                          "_raw_emitted", "_raw_errors", "signal_raw_frame")
        self._poll_latest(self._fitted_queue, self.signal_fitted,
                          "_fitted_emitted", "_fitted_errors", "signal_fitted")
        self._poll_latest(self._status_queue, self.signal_feedback_status,
                          "_status_emitted", "_status_errors", "signal_feedback_status")

    def _poll_latest(self, queue, signal, counter_attr: str, error_attr: str, name: str):
        """取队列中最新一项并 emit（丢弃中间积压的旧帧）。"""
        latest = None
        while True:
            item = queue.get_nowait()
            if item is None:
                break
            latest = item
        if latest is not None:
            try:
                signal.emit(latest)
                setattr(self, counter_attr, getattr(self, counter_attr) + 1)
            except Exception as e:
                setattr(self, error_attr, getattr(self, error_attr) + 1)
                logger.error(f"UIBridge {name} 处理异常: {e}", exc_info=True)
