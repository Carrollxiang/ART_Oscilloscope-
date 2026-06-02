"""
迷你实时曲线 — 仅显示反馈订阅的物理量趋势

左下角持久化显示, 不随 Tab 切换隐藏。
与主波形一致的交互: 鼠标滚轮缩放 / 拖拽平移 / 图例点击切换。
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Optional

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QSizePolicy
from PyQt6.QtGui import QColor

logger = logging.getLogger(__name__)

MAX_POINTS = 3600

TRACE_COLORS = [
    QColor("#FF6B6B"), QColor("#4ECDC4"), QColor("#45B7D1"),
    QColor("#96CEB4"), QColor("#FFEAA7"), QColor("#DDA0DD"),
    QColor("#98D8C8"), QColor("#F7DC6F"), QColor("#BB8FCE"),
    QColor("#85C1E9"), QColor("#F0B27A"), QColor("#82E0AA"),
]


class MiniChartData:
    """环形缓冲, 每订阅项一个 deque, 存储 (timestamp, value) 对。"""

    def __init__(self, maxlen: int = MAX_POINTS):
        self._maxlen = maxlen
        self._buf: dict[str, deque] = {}   # key → deque of (timestamp, value)
        self._colors: dict[str, QColor] = {}
        self._count = 0
        self._ci = 0
        self._start_time: float = time.monotonic()  # 启动基准时间

    def add(self, key: str, value: float, timestamp: float | None = None):
        if key not in self._buf:
            self._buf[key] = deque(maxlen=self._maxlen)
            self._colors[key] = TRACE_COLORS[self._ci % len(TRACE_COLORS)]
            self._ci += 1
        ts = timestamp if timestamp is not None else time.monotonic()
        self._buf[key].append((ts, value))
        self._count += 1

    def add_batch(self, data: dict[str, float], timestamp: float | None = None):
        for k, v in data.items():
            self.add(k, v, timestamp=timestamp)

    def trace(self, key: str) -> tuple[np.ndarray, np.ndarray]:
        """返回 (x_min, y) — x 为距启动时间的分钟数。"""
        if key not in self._buf or not self._buf[key]:
            return np.array([]), np.array([])
        b = self._buf[key]
        arr = np.array(b, dtype=np.float64)   # shape (N, 2): [timestamp, value]
        x_min = (arr[:, 0] - self._start_time) / 60.0
        return x_min, arr[:, 1]

    def keys(self):
        return list(self._buf.keys())

    def color(self, key: str) -> QColor:
        return self._colors.get(key, QColor("#888"))

    @property
    def count(self):
        return self._count

    @property
    def latest_time_min(self) -> float:
        """最近一个数据点的时间 (min), 用于自动滚动 X 轴。"""
        latest = 0.0
        for b in self._buf.values():
            if b:
                latest = max(latest, b[-1][0])
        return (latest - self._start_time) / 60.0

    @property
    def window_min(self) -> float:
        """X 轴窗口宽度 (min), 对应 MAX_POINTS 个采样点。"""
        return MAX_POINTS / 60.0   # 假设 ~1 点/s, 可按实际调整


class MiniChartWidget(QWidget):
    """迷你趋势图 (可交互 + 可勾选图例)。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data = MiniChartData()
        self._curves: dict[str, pg.PlotDataItem] = {}
        self._visible: dict[str, bool] = {}
        self._dirty = False

        self.setMinimumWidth(180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet("background: #111;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)

        # ── 绘图区 (与主波形相同交互能力) ──
        self.plot = pg.PlotWidget()
        self.plot.setLabel("left", "", units="")
        self.plot.setLabel("bottom", "t", units="min")
        self.plot.showGrid(x=True, y=True, alpha=0.15)
        self.plot.setMouseEnabled(x=True, y=True)  # 允许滚轮缩放 + 拖拽
        self.plot.setBackground("#0D0D0D")
        try:
            self.plot.useOpenGL(True)
        except Exception:
            pass

        # 图例 (右下角, 点击切换显隐)
        self._legend = pg.LegendItem(
            size=(60, 60), offset=(0, 0),
            brush=(20, 20, 20, 200), pen=(80, 80, 80),
            labelTextColor=(180, 180, 180),
        )
        self._legend.setParentItem(self.plot.plotItem.vb)
        self._legend.anchor((1, 1), (1, 1), (-5, -5))
        self._legend.setZValue(100)
        # 图例点击 —— 仿 WaveformView
        self._legend.mousePressEvent = self._on_legend_click

        layout.addWidget(self.plot, stretch=1)

    # ── 数据接口 ───────────────────────────────────────────────

    def add_data(self, filtered: dict[str, float], timestamp: float | None = None):
        if filtered:
            self._data.add_batch(filtered, timestamp=timestamp)
            self._dirty = True

    # ── 图例点击切换 ───────────────────────────────────────────

    def _on_legend_click(self, ev):
        """点击图例条目切换对应曲线显隐。"""
        pos = ev.pos()
        item_height = 20
        y_offset = 5

        keys = self._data.keys()
        for idx, key in enumerate(keys):
            y_start = y_offset + idx * item_height
            y_end = y_start + item_height
            if y_start <= pos.y() <= y_end:
                self._visible[key] = not self._visible.get(key, True)
                self._update_legend_color(key)
                self._dirty = True
                ev.accept()
                return
        pg.LegendItem.mousePressEvent(self._legend, ev)

    def _update_legend_color(self, key: str):
        """更新图例文字颜色: 可见→亮, 隐藏→灰。"""
        vis = self._visible.get(key, True)
        for sample, label in self._legend.items:
            if hasattr(label, 'text') and label.text == key:
                label.setAttr("color", (180, 180, 180) if vis else (80, 80, 80))
                break

    # ── 刷新曲线 ───────────────────────────────────────────────

    def _refresh(self):
        if not self._dirty:
            return
        self._dirty = False

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
                self._visible.setdefault(key, True)
                self._legend.addItem(curve, key)
                self._update_legend_color(key)

            if self._visible.get(key, True):
                self._curves[key].setData(x, y)
                self._curves[key].show()
            else:
                self._curves[key].hide()

        # 自动 X 轴滚动: 跟随最新时间
        if self._data.count > 0:
            latest = self._data.latest_time_min
            win = self._data.window_min
            vb = self.plot.plotItem.vb
            xr = vb.viewRange()[0]
            # 仅在当前窗口接近默认窗口时自动滚动 (用户手动缩放后不强制)
            if abs(xr[1] - xr[0] - win) < win * 0.1:
                self.plot.setXRange(max(0, latest - win), max(win, latest))

    def refresh_now(self):
        """触发驱动刷新：由主显示在每帧数据到达时调用。"""
        self._refresh()

    def clear_all(self):
        self._data = MiniChartData()
        self._curves.clear()
        self._visible.clear()
        self.plot.clear()
        self._dirty = False
