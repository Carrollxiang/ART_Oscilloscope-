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
from typing import Optional, Callable

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt, QRectF, QPointF
from PyQt6.QtGui import QColor, QPen

logger = logging.getLogger(__name__)

# 通道颜色 (最多支持 32 通道)
CHANNEL_COLORS = [
    QColor("#FFFF00"),  # CH1:  黄
    QColor("#00FFFF"),  # CH2:  青
    QColor("#FF00FF"),  # CH3:  紫
    QColor("#00FF00"),  # CH4:  绿
    QColor("#FFA500"),  # CH5:  橙
    QColor("#FF69B4"),  # CH6:  粉
    QColor("#87CEEB"),  # CH7:  天蓝
    QColor("#98FB98"),  # CH8:  淡绿
    QColor("#FFD700"),  # CH9:  金
    QColor("#DDA0DD"),  # CH10: 梅
    QColor("#40E0D0"),  # CH11: 碧绿
    QColor("#FF6347"),  # CH12: 番茄红
    QColor("#B0C4DE"),  # CH13: 灰蓝
    QColor("#F0E68C"),  # CH14: 卡其
    QColor("#C0C0C0"),  # CH15: 银灰
    QColor("#FFB6C1"),  # CH16: 浅粉
]

CHANNEL_NAMES = [f"CH{i+1}" for i in range(32)]


class WaveformView:
    """
    波形视图控件 — 带图例的 pyqtgraph PlotWidget。
    """

    def __init__(self, parent_widget, channel_count: int = 16):
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

        # 图例 (右上角)
        self._legend = pg.LegendItem(
            size=(80, 100),
            offset=(70, 10),
            brush=(30, 30, 30, 200),
            pen=(100, 100, 100),
            labelTextColor=(220, 220, 220),
        )
        self._legend.setParentItem(self.plot_widget.plotItem.vb)
        self._legend.anchor((1, 0), (1, 0), (10, 10))  # 右上角
        self._legend.setZValue(100)
        for ch in range(channel_count):
            curve = self._curves[ch]
            name = CHANNEL_NAMES[ch] if ch < len(CHANNEL_NAMES) else f"CH{ch+1}"
            self._legend.addItem(curve, name)

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
        self._on_visible_changed: Optional[Callable[[int, bool], None]] = None

        # 点击图例切换可见性
        self._legend.mousePressEvent = self._on_legend_click
        self._update_legend_appearance()

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

    def _update_legend_appearance(self):
        """更新图例条目外观: 可见→不透明, 隐藏→半透明+标注"""
        for ch in range(self._channel_count):
            if ch >= len(self._legend.items):
                continue
            sample, label = self._legend.items[ch]
            visible = self._visible.get(ch, False)
            name = CHANNEL_NAMES[ch] if ch < len(CHANNEL_NAMES) else f"CH{ch+1}"
            if visible:
                sample.setOpacity(1.0)
                label.setText(name)
                label.setAttr("color", (220, 220, 220))
            else:
                sample.setOpacity(0.25)
                label.setText(f"{name} (隐藏)")
                label.setAttr("color", (100, 100, 100))

    # ── 图例点击 ──────────────────────────────────────────────

    def _on_legend_click(self, ev):
        """图例点击事件: 切换对应通道的显隐。"""
        pos = ev.pos()
        item_height = 20
        y_offset = 5

        for ch in range(self._channel_count):
            y_start = y_offset + ch * item_height
            y_end = y_start + item_height
            if y_start <= pos.y() <= y_end:
                new_visible = not self._visible.get(ch, False)
                self.set_channel_visible(ch, new_visible)
                self._update_legend_appearance()
                if self._on_visible_changed:
                    self._on_visible_changed(ch, new_visible)
                ev.accept()
                return

        pg.LegendItem.mousePressEvent(self._legend, ev)

    # ── 可见性控制 ────────────────────────────────────────────

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
        self._update_legend_appearance()

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
