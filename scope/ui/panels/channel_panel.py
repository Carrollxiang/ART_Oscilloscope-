"""
通道控制面板 — 控制器

管理每个通道的:
- 开关 (enable/disable)
- 垂直档位 (V/div)
- 耦合 (DC/AC/GND)
- 探头衰减比

数据通过 Signal 通知 MainWindow 或直接写入 DeviceConfig。
"""

from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtWidgets import QWidget, QLabel, QCheckBox, QDoubleSpinBox, QComboBox
from PyQt6.QtGui import QColor

from scope.model.enums import ChannelCoupling, MeasurementId

logger = logging.getLogger(__name__)

# 通道预设颜色 (循环使用, 最多支持 32 通道)
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


class ChannelPanel(QWidget):
    """
    通道控制面板。

    每个通道一行: [☑ CH1] [1.0 V/div] [DC ▼] [1.0X]
    右侧面板的"通道"Tab 嵌入此控件。
    """

    # 信号: (channel_index, 属性名, 新值)
    channel_changed = pyqtSignal(int, str, object)

    def __init__(self, parent=None, channel_count: int = 1):
        super().__init__(parent)

        self._channel_count = channel_count
        self._controls: list[dict] = []

        self._build_channel_rows()

    def _build_channel_rows(self):
        """为每个通道创建控制行 (两列网格, 高信息密度)"""
        from PyQt6.QtWidgets import QVBoxLayout as VBoxLayout
        from PyQt6.QtWidgets import QGridLayout, QScrollArea

        # 创建主布局
        self.setLayout(VBoxLayout())
        lay = self.layout()

        # 标题
        title = QLabel("通道开关 / 电压量程 (±V)")
        title.setStyleSheet("color: #888; font-size: 11px; padding: 2px;")
        lay.addWidget(title)

        # 滚动区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border: none; }")

        container = QWidget()
        grid = QGridLayout(container)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(2)

        for ch in range(self._channel_count):
            row_data = self._create_channel_row(ch)
            self._controls.append(row_data)
            w = QWidget()
            w.setLayout(row_data["layout"])
            grid.addWidget(w, ch // 2, ch % 2)  # 2列网格

        scroll.setWidget(container)
        lay.addWidget(scroll, stretch=1)

    def _create_channel_row(self, ch: int) -> dict:
        """创建单个通道的控制行: [☑ CH1] [最小 -10V] [最大 10V]"""
        from PyQt6.QtWidgets import QHBoxLayout

        layout = QHBoxLayout()
        layout.setContentsMargins(0, 2, 0, 2)

        # 颜色指示
        color = CHANNEL_COLORS[ch % len(CHANNEL_COLORS)]

        # 启用复选框
        cb = QCheckBox(f"CH{ch}")
        cb.setChecked(True)  # 默认全部通道开启
        cb.setStyleSheet(f"color: {color.name()}; font-weight: bold;")
        cb.toggled.connect(lambda checked, c=ch: self._on_change(c, "enabled", checked))
        layout.addWidget(cb)

        # 最小电压
        from PyQt6.QtWidgets import QLabel as Lbl
        layout.addWidget(Lbl(" 最小"))
        min_val = QDoubleSpinBox()
        min_val.setRange(-100.0, 100.0)
        min_val.setValue(-10.0)
        min_val.setSuffix(" V")
        min_val.setSingleStep(1.0)
        min_val.setDecimals(1)
        min_val.setFixedWidth(100)
        min_val.valueChanged.connect(lambda v, c=ch: self._on_change(c, "min_val", v))
        layout.addWidget(min_val)

        # 最大电压
        layout.addWidget(Lbl("最大"))
        max_val = QDoubleSpinBox()
        max_val.setRange(-100.0, 100.0)
        max_val.setValue(10.0)
        max_val.setSuffix(" V")
        max_val.setSingleStep(1.0)
        max_val.setDecimals(1)
        max_val.setFixedWidth(100)
        max_val.valueChanged.connect(lambda v, c=ch: self._on_change(c, "max_val", v))
        layout.addWidget(max_val)

        layout.addStretch()

        return {
            "layout": layout,
            "enable": cb,
            "min_val": min_val,
            "max_val": max_val,
            "color": color,
        }

    def _on_change(self, ch: int, key: str, value):
        self.channel_changed.emit(ch, key, value)
        logger.debug(f"CH{ch}.{key} = {value}")

    # ── 公开查询接口 ───────────────────────────────────────────

    def is_channel_enabled(self, ch: int) -> bool:
        if ch < len(self._controls):
            return self._controls[ch]["enable"].isChecked()
        return False

    def get_channel_min_val(self, ch: int) -> float:
        if ch < len(self._controls):
            return self._controls[ch]["min_val"].value()
        return -10.0

    def get_channel_max_val(self, ch: int) -> float:
        if ch < len(self._controls):
            return self._controls[ch]["max_val"].value()
        return 10.0

    def get_channel_color(self, ch: int) -> QColor:
        if ch < len(self._controls):
            return self._controls[ch]["color"]
        return QColor("#FFFFFF")

    @property
    def channel_count(self) -> int:
        return self._channel_count

    def get_all_channel_ranges(self) -> tuple[list[float], list[float]]:
        """返回 (min_vals, max_vals) 各通道电压量程列表。"""
        min_vals = []
        max_vals = []
        for ch in range(self._channel_count):
            ctrl = self._controls[ch]
            min_vals.append(ctrl["min_val"].value())
            max_vals.append(ctrl["max_val"].value())
        return min_vals, max_vals
