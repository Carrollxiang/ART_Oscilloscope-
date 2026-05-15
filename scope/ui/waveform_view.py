"""
波形视图 — 基于 pyqtgraph 的多通道示波器波形显示

职责:
  - 多通道波形叠加显示 (每个通道不同颜色)
  - 图例显示 (通道名+颜色)
  - 触发位置标记
  - 网格、时间轴
  - 高性能渲染 (pyqtgraph 自动降采样)
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPen

logger = logging.getLogger(__name__)

# 通道颜色
CHANNEL_COLORS = [
    QColor("#FFFF00"),  # CH1: 黄
    QColor("#00FFFF"),  # CH2: 青
    QColor("#FF00FF"),  # CH3: 紫
    QColor("#00FF00"),  # CH4: 绿
]

CHANNEL_NAMES = ["CH1", "CH2", "CH3", "CH4"]


class WaveformView:
    """
    波形视图控件 — 带图例的 pyqtgraph PlotWidget。
    """

    def __init__(self, parent_widget, channel_count: int = 4):
        self._channel_count = channel_count
        self._curves: dict[int, pg.PlotDataItem] = {}
        self._trigger_line: Optional[pg.InfiniteLine] = None
        self._parent = parent_widget

        # 创建 PlotWidget
        self.plot_widget = pg.PlotWidget(parent=parent_widget)
        self.plot_widget.setLabel("left", "电压", units="V")
        self.plot_widget.setLabel("bottom", "时间", units="s")
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        try:
            self.plot_widget.useOpenGL(True)
        except Exception:
            pass
        self.plot_widget.setMouseEnabled(x=True, y=False)

        # 嵌入父容器
        if parent_widget.layout():
            while parent_widget.layout().count():
                item = parent_widget.layout().takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            parent_widget.layout().addWidget(self.plot_widget)
        else:
            from PyQt6.QtWidgets import QVBoxLayout
            layout = QVBoxLayout(parent_widget)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(self.plot_widget)

        # 创建各通道曲线 + 图例
        for ch in range(channel_count):
            color = CHANNEL_COLORS[ch % len(CHANNEL_COLORS)]
            pen = pg.mkPen(color=color, width=1.5)
            name = CHANNEL_NAMES[ch] if ch < len(CHANNEL_NAMES) else f"CH{ch+1}"
            curve = self.plot_widget.plot(pen=pen, name=name)
            curve.hide()
            self._curves[ch] = curve

        # 图例 (右上角, 帧外边框)
        self._legend = self.plot_widget.plotItem.addLegend(
            offset=(10, 10),
            labelTextColor=(200, 200, 200),
        )

        # 触发线
        self._trigger_line = pg.InfiniteLine(
            pos=0.5, angle=90,
            pen=pg.mkPen(color="#FFFFFF", width=1, style=Qt.PenStyle.DashLine),
            label="触发",
        )
        self.plot_widget.addItem(self._trigger_line)

        self.plot_widget.enableAutoRange(axis="xy")
        self.plot_widget.setMouseEnabled(y=False)

        # 可见性状态 (与 ChannelPanel 同步)
        self._visible: dict[int, bool] = {ch: False for ch in range(channel_count)}

        logger.info("WaveformView 已创建 (带图例)")

    # ── 波形数据 ───────────────────────────────────────────────

    def update_waveform(self, ch: int, time_axis: np.ndarray, data: np.ndarray,
                        enabled: bool = True, color: Optional[QColor] = None):
        """更新通道波形数据。"""
        curve = self._curves.get(ch)
        if curve is None:
            return
        if not enabled or not self._visible.get(ch, False):
            curve.hide()
            return
        curve.setData(time_axis, data)
        curve.show()
        if color:
            curve.setPen(pg.mkPen(color=color, width=1.5))

    # ── 可见性控制 (由 ChannelPanel 调用) ─────────────────────

    def set_channel_visible(self, ch: int, visible: bool):
        """显示/隐藏指定通道的波形。"""
        self._visible[ch] = visible
        curve = self._curves.get(ch)
        if curve is None:
            return
        if visible:
            curve.show()
        else:
            curve.hide()

    def is_channel_visible(self, ch: int) -> bool:
        return self._visible.get(ch, False)

    # ── 触发标记 ───────────────────────────────────────────────

    def set_trigger_position(self, position_ratio: float = 0.5):
        if self._trigger_line:
            self._trigger_line.setValue(position_ratio)

    # ── 缩放 ───────────────────────────────────────────────────

    def set_time_range(self, seconds_per_div: float):
        self.plot_widget.setXRange(0, seconds_per_div * 10)

    def set_voltage_range(self, volts_per_div: float):
        self.plot_widget.setYRange(-volts_per_div * 4, volts_per_div * 4)

    def clear_all(self):
        for curve in self._curves.values():
            curve.hide()
            curve.clear()

    def auto_range(self):
        self.plot_widget.enableAutoRange(axis="xy")
