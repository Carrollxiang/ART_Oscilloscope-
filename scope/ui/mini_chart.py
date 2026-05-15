"""
迷你实时曲线 — 仅显示反馈订阅的物理量趋势

左下角持久化显示 (不随 Tab 切换隐藏)。
样式与主波形一致: 暗色底 + 网格 + 可勾选图例。
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Optional

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QWidget, QVBoxLayout

logger = logging.getLogger(__name__)

MAX_POINTS = 3600
UPDATE_INTERVAL_MS = 100

# 曲线颜色 (与主波形区分)
TRACE_COLORS = [
    QColor("#FF6B6B"), QColor("#4ECDC4"), QColor("#45B7D1"),
    QColor("#96CEB4"), QColor("#FFEAA7"), QColor("#DDA0DD"),
    QColor("#98D8C8"), QColor("#F7DC6F"), QColor("#BB8FCE"),
    QColor("#85C1E9"), QColor("#F0B27A"), QColor("#82E0AA"),
]


class MiniChartData:
    """环形缓冲, 每订阅项一个 deque。"""

    def __init__(self, maxlen: int = MAX_POINTS):
        self._maxlen = maxlen
        self._buf: dict[str, deque] = {}
        self._colors: dict[str, QColor] = {}
        self._timeline: deque = deque(maxlen=maxlen)
        self._count = 0
        self._ci = 0

    def add(self, key: str, value: float):
        if key not in self._buf:
            self._buf[key] = deque(maxlen=self._maxlen)
            self._colors[key] = TRACE_COLORS[self._ci % len(TRACE_COLORS)]
            self._ci += 1
        self._buf[key].append(value)
        self._timeline.append(self._count)
        self._count += 1

    def add_batch(self, data: dict[str, float]):
        for k, v in data.items():
            self.add(k, v)

    def trace(self, key: str) -> tuple[np.ndarray, np.ndarray]:
        if key not in self._buf or not self._buf[key]:
            return np.array([]), np.array([])
        b = self._buf[key]
        offset = self._count - len(b)
        x = np.arange(offset, offset + len(b), dtype=np.float64)
        return x, np.array(b, dtype=np.float64)

    def keys(self):
        return list(self._buf.keys())

    def color(self, key: str) -> QColor:
        return self._colors.get(key, QColor("#888"))

    @property
    def count(self):
        return self._count


class MiniChartWidget(QWidget):
    """迷你趋势图 (暗色 + 可勾选图例)。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data = MiniChartData()
        self._curves: dict[str, pg.PlotDataItem] = {}
        self._visible: dict[str, bool] = {}
        self._legend: Optional[pg.LegendItem] = None

        self.setMinimumWidth(180)
        self.setStyleSheet("background: #111;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)

        # ── 绘图区 ──
        self.plot = pg.PlotWidget()
        self.plot.setLabel("left", "", units="")
        self.plot.setLabel("bottom", "", units="")
        self.plot.showGrid(x=True, y=True, alpha=0.15)
        self.plot.setMouseEnabled(False, False)
        self.plot.hideButtons()
        self.plot.setMenuEnabled(False)
        self.plot.setBackground("#0D0D0D")
        try:
            self.plot.useOpenGL(True)
        except Exception:
            pass

        # 图例 (右下角)
        self._legend = pg.LegendItem(
            size=(60, 60), offset=(0, 0),
            brush=(20, 20, 20, 200), pen=(80, 80, 80),
            labelTextColor=(180, 180, 180),
        )
        self._legend.setParentItem(self.plot.plotItem.vb)
        self._legend.anchor((1, 1), (1, 1), (-5, -5))
        self._legend.setZValue(100)

        layout.addWidget(self.plot, stretch=1)

        # ── 定时刷新 ──
        self._timer = QTimer()
        self._timer.setInterval(UPDATE_INTERVAL_MS)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()

    # ── 数据接口 ───────────────────────────────────────────────

    def add_data(self, filtered: dict[str, float]):
        """仅添加被反馈订阅选中的数据。"""
        if filtered:
            self._data.add_batch(filtered)

    # ── 图例点击切换 ───────────────────────────────────────────

    def _toggle_trace(self, key: str):
        self._visible[key] = not self._visible.get(key, True)
        self._update_legend_style(key)

    def _update_legend_style(self, key: str):
        vis = self._visible.get(key, True)
        for sample, label in self._legend.items:
            if hasattr(label, 'text') and label.text == key:
                if vis:
                    label.setAttr("color", (180, 180, 180))
                else:
                    label.setAttr("color", (80, 80, 80))
                break

    # ── 刷新曲线 ───────────────────────────────────────────────

    def _refresh(self):
        keys = self._data.keys()
        if not keys:
            return

        for key in keys:
            x, y = self._data.trace(key)
            if len(x) < 2:
                continue

            if key not in self._curves:
                color = self._data.color(key)
                pen = pg.mkPen(color=color, width=1.0)
                curve = self.plot.plot(x, y, pen=pen, name=key)
                curve.hide()
                self._curves[key] = curve
                self._visible[key] = True
                # 加入图例
                self._legend.addItem(curve, key)
                # 图例点击
                idx = len(self._legend.items) - 1
                sample, label = self._legend.items[idx]
                if hasattr(label, 'mousePressEvent'):
                    old = label.mousePressEvent
                    def handler(ev, k=key):
                        self._toggle_trace(k)
                    label.mousePressEvent = handler

            # 可见性
            if self._visible.get(key, True):
                self._curves[key].setData(x, y)
                self._curves[key].show()
            else:
                self._curves[key].hide()

        # 自动 X 轴滚动
        n = self._data.count
        if n > 0:
            win = min(MAX_POINTS, n)
            self.plot.setXRange(n - win, n)

        # 自动 Y 轴 (只考虑可见曲线)
        self.plot.enableAutoRange(axis="y")

    def clear_all(self):
        self._data = MiniChartData()
        self._curves.clear()
        self._visible.clear()
        self.plot.clear()
