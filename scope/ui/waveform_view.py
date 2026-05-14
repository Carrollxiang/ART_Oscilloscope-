"""
波形视图 — 基于 pyqtgraph 的多通道示波器波形显示

职责:
  - 多通道波形叠加显示 (每个通道不同颜色)
  - 自动缩放 Y 轴 (根据通道档位)
  - 触发位置标记
  - 网格、时间轴
  - 高性能渲染 (pyqtgraph 自动降采样)
"""

from __future__ import annotations

import logging
from typing import Optional, Callable

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QColor, QPen, QFont

logger = logging.getLogger(__name__)

# 通道颜色 (与 ChannelPanel 一致)
CHANNEL_COLORS = [
    QColor("#FFFF00"),  # CH1: 黄
    QColor("#00FFFF"),  # CH2: 青
    QColor("#FF00FF"),  # CH3: 紫
    QColor("#00FF00"),  # CH4: 绿
]


class WaveformView:
    """
    波形视图控件。

    包装 pyqtgraph.PlotWidget, 在 main_window 的 waveformContainer
    位置创建。不继承 QWidget, 作为组合对象嵌入 MainWindow。
    """

    def __init__(self, parent_widget, channel_count: int = 4):
        """
        parent_widget: main_window.waveformContainer (QWidget)
        此方法将 parent_widget 的布局替换为 PlotWidget。
        """
        self._channel_count = channel_count
        self._curves: dict[int, pg.PlotDataItem] = {}
        self._trigger_line: Optional[pg.InfiniteLine] = None
        self._parent = parent_widget

        # 创建 PlotWidget
        self.plot_widget = pg.PlotWidget(parent=parent_widget)
        self.plot_widget.setLabel("left", "电压", units="V")
        self.plot_widget.setLabel("bottom", "时间", units="s")
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)

        # 启用 OpenGL 加速 (如果有的话)
        try:
            self.plot_widget.useOpenGL(True)
        except Exception:
            pass  # OpenGL 不可用时忽略

        # 鼠标交互: 滚轮缩放 X, 右键拖动
        self.plot_widget.setMouseEnabled(x=True, y=False)

        # 将 PlotWidget 填入父容器的布局
        if parent_widget.layout():
            # 如果已有布局, 清空并添加
            while parent_widget.layout().count():
                item = parent_widget.layout().takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            parent_widget.layout().addWidget(self.plot_widget)
        else:
            # 创建新布局
            from PyQt6.QtWidgets import QVBoxLayout
            layout = QVBoxLayout(parent_widget)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(self.plot_widget)

        # 为每个通道创建曲线 (初始隐藏)
        for ch in range(channel_count):
            color = CHANNEL_COLORS[ch % len(CHANNEL_COLORS)]
            pen = pg.mkPen(color=color, width=1.5)
            curve = self.plot_widget.plot(pen=pen, name=f"CH{ch+1}")
            curve.hide()
            self._curves[ch] = curve

        # 触发位置标记
        self._trigger_line = pg.InfiniteLine(
            pos=0.5,
            angle=90,
            pen=pg.mkPen(color="#FFFFFF", width=1, style=Qt.PenStyle.DashLine),
            label="触发",
        )
        self.plot_widget.addItem(self._trigger_line)

        # 自动范围
        self.plot_widget.enableAutoRange(axis="xy")

        # 禁用鼠标滚轮 Y 轴缩放 (由 V/div 控制)
        self.plot_widget.setMouseEnabled(y=False)

        logger.info("WaveformView 已创建")

    def update_waveform(self, ch: int, time_axis: np.ndarray, data: np.ndarray,
                        enabled: bool = True, color: Optional[QColor] = None):
        """
        更新单个通道的波形数据。

        由 MainWindow 在每次采集完成后调用。
        """
        curve = self._curves.get(ch)
        if curve is None:
            return

        if not enabled:
            curve.hide()
            return

        curve.setData(time_axis, data)
        curve.show()

        if color:
            curve.setPen(pg.mkPen(color=color, width=1.5))

    def set_trigger_position(self, position_ratio: float = 0.5):
        """
        设置触发位置标记 (0~1, 相对屏幕宽度)。
        默认 0.5 表示触发点在屏幕中央。
        """
        if self._trigger_line:
            self._trigger_line.setValue(position_ratio)

    def set_time_range(self, seconds_per_div: float):
        """设置水平时基 (s/div)"""
        # 假设屏幕约 10 格
        total_time = seconds_per_div * 10
        self.plot_widget.setXRange(0, total_time)

    def set_voltage_range(self, volts_per_div: float):
        """设置垂直范围 (V/div)"""
        total_volts = volts_per_div * 8  # 8 格
        self.plot_widget.setYRange(-total_volts / 2, total_volts / 2)

    def clear_all(self):
        """清除所有波形"""
        for curve in self._curves.values():
            curve.hide()
            curve.clear()

    def auto_range(self):
        """自动适应范围"""
        self.plot_widget.enableAutoRange(axis="xy")
