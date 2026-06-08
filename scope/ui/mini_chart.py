"""
迷你实时曲线 — 仅显示反馈订阅的物理量趋势

左下角持久化显示, 不随 Tab 切换隐藏。
与主波形一致的交互: 鼠标滚轮缩放 / 拖拽平移 / 图例点击切换。
"""

from __future__ import annotations

import logging
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
    """环形缓冲, 每订阅项一个 deque。"""

    def __init__(self, maxlen: int = MAX_POINTS):
        self._maxlen = maxlen
        self._buf: dict[str, deque] = {}
        self._colors: dict[str, QColor] = {}
        self._count = 0
        self._ci = 0

    def add(self, key: str, value: float):
        if key not in self._buf:
            self._buf[key] = deque(maxlen=self._maxlen)
            self._colors[key] = TRACE_COLORS[self._ci % len(TRACE_COLORS)]
            self._ci += 1
        self._buf[key].append(value)
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

    def remove(self, key: str):
        """删除单个 key 的数据"""
        if key in self._buf:
            del self._buf[key]
        if key in self._colors:
            del self._colors[key]

    @property
    def count(self):
        return self._count


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
        self.plot.setLabel("bottom", "", units="")
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

    def add_data(self, filtered: dict[str, float]):
        if filtered:
            self._data.add_batch(filtered)
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

        # 自动 X 轴 (仅在用户未手动缩放时)
        n = self._data.count
        if n > 0:
            vb = self.plot.plotItem.vb
            # 检查用户是否手动拖拽过 (viewRange 变化了就不自动调)
            xr = vb.viewRange()[0]
            if abs(xr[1] - xr[0] - MAX_POINTS) < 1:
                win = min(MAX_POINTS, n)
                self.plot.setXRange(n - win, n)

    def refresh_now(self):
        """触发驱动刷新：由主显示在每帧数据到达时调用。"""
        self._refresh()

    def remove_key(self, key: str):
        """删除单个测量项的曲线"""
        self._data.remove(key)
        
        if key in self._curves:
            curve = self._curves[key]
            self._legend.removeItem(curve)
            curve.clear()
            del self._curves[key]
        
        if key in self._visible:
            del self._visible[key]
        
        self._dirty = True

    def clear_all(self):
        self._data = MiniChartData()
        self._curves.clear()
        self._visible.clear()
        self.plot.clear()
        self._dirty = False
