"""
动态测量面板 — 每行可独立配置 (通道 + 测量项)

每行:
  [CH1 ▼] [Vpp ▼] [3.30 V] [✕ 删除]

用户可随时添加/删除行, 不同行可对应同一通道的不同物理量。
值从 AnalysisResult.measurements 动态查找更新。
"""

from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QComboBox,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
)

logger = logging.getLogger(__name__)

# 可用通道
CHANNELS = ["CH1", "CH2", "CH3", "CH4"]

# 可用测量项 (key 名, 显示名, 单位)
MEASUREMENT_TYPES: list[tuple[str, str, str]] = [
    ("Vpp",       "峰峰值",   "V"),
    ("Vmax",      "最大值",   "V"),
    ("Vmin",      "最小值",   "V"),
    ("Vrms",      "有效值",   "V"),
    ("Vavg",      "平均值",   "V"),
    ("Freq",      "频率",     "Hz"),
    ("Period",    "周期",     "s"),
    ("DutyCycle", "占空比",   "%"),
    ("PosWidth",  "正脉宽",   "s"),
    ("NegWidth",  "负脉宽",   "s"),
    ("RiseTime",  "上升时间", "s"),
    ("FallTime",  "下降时间", "s"),
]


class MeasurementRow(QWidget):
    """单行测量条目: [CH组合框] [测量项组合框] [值标签] [删除按钮]"""

    def __init__(self, parent=None, channel: str = "CH1",
                 meas_key: str = "Vpp", on_remove=None):
        super().__init__(parent)
        self._meas_key = meas_key
        self._on_remove = on_remove

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)

        # 通道选择
        self.channel_combo = QComboBox()
        self.channel_combo.addItems(CHANNELS)
        self.channel_combo.setCurrentText(channel)
        self.channel_combo.setMinimumWidth(60)
        layout.addWidget(self.channel_combo)

        # 测量项选择
        self.meas_combo = QComboBox()
        for key, label, unit in MEASUREMENT_TYPES:
            self.meas_combo.addItem(f"{label} ({unit})", key)
        self.meas_combo.setCurrentIndex(
            next(i for i, (k, _, _) in enumerate(MEASUREMENT_TYPES) if k == meas_key)
        )
        self.meas_combo.setMinimumWidth(120)
        layout.addWidget(self.meas_combo)

        # 当前值 (只读)
        self.value_label = QLabel("—")
        self.value_label.setStyleSheet("font-weight: bold; font-size: 13px;")
        self.value_label.setMinimumWidth(100)
        layout.addWidget(self.value_label)

        # 单位
        self.unit_label = QLabel(self._find_unit(meas_key))
        self.unit_label.setStyleSheet("color: #888;")
        layout.addWidget(self.unit_label)

        # 删除按钮
        if on_remove:
            btn_remove = QPushButton("✕")
            btn_remove.setFixedSize(24, 24)
            btn_remove.setStyleSheet(
                "QPushButton { color: #FF4444; border: none; }"
                "QPushButton:hover { background: #442222; }"
            )
            btn_remove.clicked.connect(self._remove_self)
            layout.addWidget(btn_remove)

        layout.addStretch()

    def _find_unit(self, key: str) -> str:
        for k, _, unit in MEASUREMENT_TYPES:
            if k == key:
                return unit
        return ""

    def _remove_self(self):
        if self._on_remove:
            self._on_remove(self)

    def get_channel(self) -> str:
        return self.channel_combo.currentText()

    def get_meas_key(self) -> str:
        return self.meas_combo.currentData()

    def update_value(self, measurements: dict[str, float]):
        """从 measurements 字典查找并更新值"""
        key = f"{self.get_channel()}_{self.get_meas_key()}"
        value = measurements.get(key)
        if value is not None:
            self.value_label.setText(self._format_value(value))
        else:
            self.value_label.setText("—")

    @staticmethod
    def _format_value(value: float) -> str:
        if abs(value) >= 1000:
            return f"{value:.1f}"
        elif abs(value) >= 1:
            return f"{value:.4f}"
        elif abs(value) >= 0.001:
            return f"{value:.6f}"
        elif value == 0:
            return "0"
        else:
            return f"{value:.3e}"


class MeasurementPanel:
    """
    动态测量面板控制器。

    每行是一个独立 (通道, 测量项) 对, 可动态添加/删除。
    绑定到 main_window 的 measurementTab 布局。
    """

    def __init__(self, parent_widget: QWidget):
        self._parent = parent_widget
        self._rows: list[MeasurementRow] = []

        self._setup_ui()

        # 默认添加 4 行常用测量
        self.add_row("CH1", "Vpp")
        self.add_row("CH1", "Freq")
        self.add_row("CH2", "Vpp")
        self.add_row("CH2", "Freq")

    def _setup_ui(self):
        """创建滚动区域 + 行容器 + 添加按钮"""
        # 清空父控件
        layout = self._parent.layout()
        if layout is None:
            layout = QVBoxLayout(self._parent)
            self._parent.setLayout(layout)
        else:
            # 保留 layout
            pass

        # 标题
        title = QLabel("测量项 (双击空行可编辑, 点击 [+添加] 新增)")
        title.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(title)

        # 滚动区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border: none; }")

        self._container = QWidget()
        self._container_layout = QVBoxLayout(self._container)
        self._container_layout.setContentsMargins(0, 0, 0, 0)
        self._container_layout.setSpacing(2)
        self._container_layout.addStretch()

        scroll.setWidget(self._container)
        layout.addWidget(scroll, stretch=1)

        # 添加按钮
        btn_add = QPushButton("+ 添加测量")
        btn_add.setStyleSheet(
            "QPushButton { color: #00CC00; border: 1px solid #336633; "
            "padding: 4px; }"
            "QPushButton:hover { background: #224422; }"
        )
        btn_add.clicked.connect(self._on_add)
        layout.addWidget(btn_add)

    def add_row(self, channel: str = "CH1", meas_key: str = "Vpp"):
        """添加一行, 返回 MeasurementRow 对象"""
        row = MeasurementRow(
            channel=channel,
            meas_key=meas_key,
            on_remove=self._on_remove,
        )
        self._rows.append(row)
        # 在 stretch 之前插入
        self._container_layout.insertWidget(
            self._container_layout.count() - 1, row
        )
        return row

    def _on_add(self):
        """添加默认行"""
        self.add_row()

    def _on_remove(self, row: MeasurementRow):
        """移除一行"""
        if row in self._rows:
            self._rows.remove(row)
            self._container_layout.removeWidget(row)
            row.deleteLater()

    def update_measurements(self, measurements: dict[str, float]):
        """用 AnalysisResult.measurements 更新所有行的值"""
        for row in self._rows:
            row.update_value(measurements)

    def get_subscriptions(self) -> list[tuple[str, str]]:
        """返回所有行配置的 (通道, 测量项) 列表, 供 FeedbackPanel 使用"""
        return [(row.get_channel(), row.get_meas_key()) for row in self._rows]

    def clear_all(self):
        """移除所有行"""
        for row in list(self._rows):
            self._on_remove(row)
