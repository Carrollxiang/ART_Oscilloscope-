"""
UIBridge — 采集线程 → Qt 主线程的桥接层 (v0.4)

消费 EventBus 中的 frame.raw 和 frame.fitted 队列，
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
"""

from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal

from scope.model import RawFrame
from scope.runtime import EventBus, FittedSnapshot

logger = logging.getLogger(__name__)


class UIBridge(QObject):
    """
    采集线程 → Qt 主线程桥接。

    poll() 方法在采集线程中非阻塞调用，检查两个 EventBus 队列，
    有数据时通过 pyqtSignal 排入 Qt 主线程的事件队列。
    """

    # 原始帧 — 用于主波形渲染
    signal_raw_frame = pyqtSignal(object)  # RawFrame

    # 拟合结果 — 用于测量面板 + MiniChart
    signal_fitted = pyqtSignal(object)     # FittedSnapshot

    def __init__(self, event_bus: EventBus, parent=None):
        super().__init__(parent)
        self._event_bus = event_bus

        # 订阅两个 topic
        self._raw_queue = event_bus.subscribe("frame.raw")
        self._fitted_queue = event_bus.subscribe("frame.fitted")

        # 统计
        self._raw_emitted = 0
        self._fitted_emitted = 0
        self._raw_errors = 0
        self._fitted_errors = 0

    @property
    def metrics(self) -> dict:
        return {
            "raw_emitted": self._raw_emitted,
            "fitted_emitted": self._fitted_emitted,
            "raw_errors": self._raw_errors,
            "fitted_errors": self._fitted_errors,
        }

    def poll(self):
        """
        非阻塞轮询两个队列，有数据则 emit QSignal。

        每个 emit 被 try/except 包裹，确保单个信号处理异常
        不会中断整个 poll 循环或传播到采集线程。
        """
        # 1. 原始帧（主波形）
        raw = self._raw_queue.get_nowait()
        while raw is not None:
            try:
                self.signal_raw_frame.emit(raw)
                self._raw_emitted += 1
            except Exception as e:
                self._raw_errors += 1
                logger.error(f"UIBridge signal_raw_frame 处理异常: {e}", exc_info=True)
            # 队列中可能积压了旧帧，丢弃旧的只留最新的
            raw = self._raw_queue.get_nowait()

        # 2. 拟合结果（测量面板 + MiniChart）
        fitted = self._fitted_queue.get_nowait()
        while fitted is not None:
            try:
                self.signal_fitted.emit(fitted)
                self._fitted_emitted += 1
            except Exception as e:
                self._fitted_errors += 1
                logger.error(f"UIBridge signal_fitted 处理异常: {e}", exc_info=True)
            fitted = self._fitted_queue.get_nowait()
