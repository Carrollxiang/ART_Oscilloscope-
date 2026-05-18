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

    def __init__(self, parent=None, channel_count: int = 16):
        super().__init__(parent)

        self._channel_count = channel_count
        self._controls: list[dict] = []

        self._build_channel_rows()

    def _build_channel_rows(self):
        """为每个通道创建控制行 (放入 QScrollArea)"""
        from PyQt6.QtWidgets import QVBoxLayout as VBoxLayout
        from PyQt6.QtWidgets import QScrollArea

        # 创建主布局
        self.setLayout(VBoxLayout())
        lay = self.layout()

        # 标题
        title = QLabel("通道开关 / 垂直档位 / 耦合 / 探头比")
        title.setStyleSheet("color: #888; font-size: 11px; padding: 2px;")
        lay.addWidget(title)

        # 滚动区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border: none; }")

        container = QWidget()
        container_layout = VBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(4)

        for ch in range(self._channel_count):
            row = self._create_channel_row(ch)
            self._controls.append(row)
            w = QWidget()
            w.setLayout(row["layout"])
            container_layout.addWidget(w)

        container_layout.addStretch()
        scroll.setWidget(container)
        lay.addWidget(scroll, stretch=1)

    def _create_channel_row(self, ch: int) -> dict:
        """创建单个通道的控制行"""
        from PyQt6.QtWidgets import QHBoxLayout

        layout = QHBoxLayout()
        layout.setContentsMargins(0, 2, 0, 2)

        # 颜色指示
        color = CHANNEL_COLORS[ch % len(CHANNEL_COLORS)]

        # 启用复选框
        cb = QCheckBox(f"CH{ch + 1}")
        cb.setChecked(True)  # 默认全部通道开启
        cb.setStyleSheet(f"color: {color.name()}; font-weight: bold;")
        cb.toggled.connect(lambda checked, c=ch: self._on_change(c, "enabled", checked))
        layout.addWidget(cb)

        # V/div
        scale = QDoubleSpinBox()
        scale.setSuffix(" V/div")
        scale.setDecimals(1)
        scale.setRange(0.01, 10.0)
        scale.setValue(1.0)
        scale.setSingleStep(0.5)
        scale.valueChanged.connect(lambda v, c=ch: self._on_change(c, "scale", v))
        layout.addWidget(scale)

        # 耦合
        coupling = QComboBox()
        coupling.addItems(["DC", "AC", "GND"])
        coupling.currentTextChanged.connect(
            lambda t, c=ch: self._on_change(c, "coupling", t.lower())
        )
        layout.addWidget(coupling)

        # 探头比
        probe = QDoubleSpinBox()
        probe.setSuffix("X")
        probe.setDecimals(1)
        probe.setRange(0.1, 1000.0)
        probe.setValue(1.0)
        probe.valueChanged.connect(lambda v, c=ch: self._on_change(c, "probe", v))
        layout.addWidget(probe)

        return {
            "layout": layout,
            "enable": cb,
            "scale": scale,
            "coupling": coupling,
            "probe": probe,
            "color": color,
        }

    def _on_change(self, ch: int, key: str, value):
        self.channel_changed.emit(ch, key, value)
        logger.debug(f"CH{ch + 1}.{key} = {value}")

    # ── 公开查询接口 ───────────────────────────────────────────

    def is_channel_enabled(self, ch: int) -> bool:
        if ch < len(self._controls):
            return self._controls[ch]["enable"].isChecked()
        return False

    def get_channel_scale(self, ch: int) -> float:
        if ch < len(self._controls):
            return self._controls[ch]["scale"].value()
        return 1.0

    def get_channel_coupling(self, ch: int) -> str:
        if ch < len(self._controls):
            return self._controls[ch]["coupling"].currentText().lower()
        return "dc"

    def get_channel_probe(self, ch: int) -> float:
        if ch < len(self._controls):
            return self._controls[ch]["probe"].value()
        return 1.0

    def get_channel_color(self, ch: int) -> QColor:
        if ch < len(self._controls):
            return self._controls[ch]["color"]
        return QColor("#FFFFFF")

    @property
    def channel_count(self) -> int:
        return self._channel_count
