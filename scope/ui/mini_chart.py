"""
迷你实时曲线 — 反馈订阅物理量的变化趋势

左下角持久化显示, 不随 Tab 切换而隐藏。
每订阅项一条曲线, 滚动显示最近 3600 个数据点。
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Optional

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel

from scope.model import AnalysisResult

logger = logging.getLogger(__name__)

MAX_POINTS = 3600
UPDATE_INTERVAL_MS = 100  # 10 fps


class MiniChartData:
    """
    迷你图数据缓冲。

    对每个订阅项维护一个环形队列, 缓存最近 MAX_POINTS 个值。
    """

    def __init__(self, maxlen: int = MAX_POINTS):
        self._maxlen = maxlen
        self._buffers: dict[str, deque] = {}  # key → deque
        self._colors: dict[str, str] = {}
        self._timeline: deque = deque(maxlen=maxlen)
        self._sample_count = 0

    def add_measurement(self, key: str, value: float, color: str = "#888"):
        """添加一个数据点。"""
        if key not in self._buffers:
            self._buffers[key] = deque(maxlen=self._maxlen)
            self._colors[key] = color
        self._buffers[key].append(value)
        self._timeline.append(self._sample_count)
        self._sample_count += 1

    def add_batch(self, measurements: dict[str, float], color_map: dict[str, str] = None):
        """批量添加测量值。"""
        for key, value in measurements.items():
            c = (color_map or {}).get(key, "#888")
            self.add_measurement(key, value, c)

    def get_trace(self, key: str) -> tuple[np.ndarray, np.ndarray]:
        """返回 (x, y) 用于绘图。"""
        if key not in self._buffers or not self._buffers[key]:
            return np.array([]), np.array([])
        buf = self._buffers[key]
        offset = self._sample_count - len(buf)
        x = np.arange(offset, offset + len(buf), dtype=np.float64)
        y = np.array(buf, dtype=np.float64)
        return x, y

    def keys(self) -> list[str]:
        return list(self._buffers.keys())

    def get_color(self, key: str) -> str:
        return self._colors.get(key, "#888")

    @property
    def sample_count(self) -> int:
        return self._sample_count


class MiniChartWidget(QWidget):
    """
    迷你实时曲线控件 — 固定在底部左侧, 跨 Tab 持久可见。

    接收 AnalysisResult → 提取订阅值 → 滚动绘制。
    """

    # 预设颜色轮 (与通道颜色区分)
    TRACE_COLORS = [
        "#FF6B6B", "#4ECDC4", "#45B7D1", "#96CEB4",
        "#FFEAA7", "#DDA0DD", "#98D8C8", "#F7DC6F",
        "#BB8FCE", "#85C1E9", "#F0B27A", "#82E0AA",
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data = MiniChartData()
        self._curves: dict[str, pg.PlotDataItem] = {}
        self._color_idx = 0

        self.setMinimumWidth(180)
        self.setStyleSheet("background: #111; border: 1px solid #333;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        title = QLabel("📊 反馈趋势")
        title.setStyleSheet("color: #aaa; font-size: 10px; padding: 2px;")
        layout.addWidget(title)

        self.plot = pg.PlotWidget()
        self.plot.setLabel("left", "", units="")
        self.plot.setLabel("bottom", "", units="")
        self.plot.showGrid(x=True, y=True, alpha=0.15)
        self.plot.setMouseEnabled(False, False)
        self.plot.hideButtons()
        self.plot.setMenuEnabled(False)
        try:
            self.plot.useOpenGL(True)
        except Exception:
            pass
        layout.addWidget(self.plot, stretch=1)

        # 状态文字
        self._status = QLabel("无数据")
        self._status.setStyleSheet("color: #666; font-size: 9px;")
        layout.addWidget(self._status)

        # 定时刷新
        self._timer = QTimer()
        self._timer.setInterval(UPDATE_INTERVAL_MS)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()

    def add_data(self, measurements: dict[str, float]):
        """从 AnalysisResult.measurements 或 payload 添加数据。"""
        self._data.add_batch(measurements)

    def add_from_result(self, result: AnalysisResult):
        """从完整 AnalysisResult 添加数据 (值 + 元信息)。"""
        self._data.add_batch(result.measurements)

    def _refresh(self):
        """刷新曲线 (约 10fps)。"""
        keys = self._data.keys()
        if not keys:
            return

        for key in keys:
            x, y = self._data.get_trace(key)
            if len(x) < 2:
                continue

            if key not in self._curves:
                color = self._data.get_color(key)
                pen = pg.mkPen(color=color, width=1.2)
                curve = self.plot.plot(x, y, pen=pen, name=key)
                self._curves[key] = curve
            else:
                self._curves[key].setData(x, y)

        # 自动滚动 X 轴
        last_x = self._data.sample_count
        if last_x > 0:
            visible = min(MAX_POINTS, last_x)
            self.plot.setXRange(last_x - visible, last_x)

        # 自动 Y 轴范围
        self.plot.enableAutoRange(axis="y")

        # 更新状态
        self._status.setText(f"{len(keys)} 条曲线 · {self._data.sample_count} 点")

    def clear_all(self):
        """清除所有数据。"""
        self._data = MiniChartData()
        self._curves.clear()
        self.plot.clear()
        self._status.setText("已清除")
