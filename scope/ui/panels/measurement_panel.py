"""
动态测量面板 — 每行独立配置: 名称 + 通道 + 测量项 + 时间段

每行:
  [名称___] [CH1 ▼] [Vpp ▼] [起始_0.000s] [结束_0.010s] [2.0000 V] [✕]

- 每行限定时间段 (起始～结束), 只计算该段内的测量值
- 不同行可对同一通道的不同时间段测量不同物理量
- 每行有独立名称用于标识和反馈订阅
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QComboBox,
    QLabel,
    QLineEdit,
    QDoubleSpinBox,
    QPushButton,
    QScrollArea,
)

from scope.model import AnalysisResult
from scope.processing.measurements import MEASUREMENT_FUNCTIONS

logger = logging.getLogger(__name__)

CHANNELS = ["CH1", "CH2", "CH3", "CH4"]

# (key, label, unit)
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
    """单行: [名称] [通道▼] [测量项▼] [起始] [结束] [值] [✕]"""

    def __init__(self, parent=None,
                 name: str = "",
                 channel: str = "CH1",
                 meas_key: str = "Vpp",
                 start_time: float = 0.0,
                 end_time: float = 0.01,
                 frame_duration: float = 0.01,
                 on_remove=None):
        super().__init__(parent)
        self._meas_key = meas_key
        self._on_remove = on_remove
        self._frame_duration = frame_duration

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)

        # 名称
        self.name_edit = QLineEdit(name)
        self.name_edit.setPlaceholderText("名称")
        self.name_edit.setMaximumWidth(100)
        layout.addWidget(self.name_edit)

        # 通道
        self.channel_combo = QComboBox()
        self.channel_combo.addItems(CHANNELS)
        self.channel_combo.setCurrentText(channel)
        self.channel_combo.setMinimumWidth(55)
        layout.addWidget(self.channel_combo)

        # 测量项
        self.meas_combo = QComboBox()
        for key, label, unit in MEASUREMENT_TYPES:
            self.meas_combo.addItem(f"{label} ({unit})", key)
        idx = next((i for i, (k, _, _) in enumerate(MEASUREMENT_TYPES)
                    if k == meas_key), 0)
        self.meas_combo.setCurrentIndex(idx)
        self.meas_combo.setMinimumWidth(110)
        layout.addWidget(self.meas_combo)

        # 起始时间
        self.start_spin = QDoubleSpinBox()
        self.start_spin.setDecimals(4)
        self.start_spin.setRange(0.0, 999.9)
        self.start_spin.setSuffix(" s")
        self.start_spin.setValue(start_time)
        self.start_spin.setSingleStep(0.001)
        self.start_spin.setMaximumWidth(90)
        layout.addWidget(self.start_spin)

        # 结束时间
        self.end_spin = QDoubleSpinBox()
        self.end_spin.setDecimals(4)
        self.end_spin.setRange(0.0001, 999.9)
        self.end_spin.setSuffix(" s")
        self.end_spin.setValue(end_time)
        self.end_spin.setSingleStep(0.001)
        self.end_spin.setMaximumWidth(90)
        layout.addWidget(self.end_spin)

        # 当前值
        self.value_label = QLabel("—")
        self.value_label.setStyleSheet("font-weight: bold; font-size: 13px;")
        self.value_label.setMinimumWidth(90)
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self.value_label)

        # 单位
        self.unit_label = QLabel(self._unit_of(meas_key))
        self.unit_label.setStyleSheet("color: #888;")
        layout.addWidget(self.unit_label)

        # 删除
        if on_remove:
            btn = QPushButton("✕")
            btn.setFixedSize(24, 24)
            btn.setStyleSheet(
                "QPushButton { color: #FF4444; border: none; }"
                "QPushButton:hover { background: #442222; }"
            )
            btn.clicked.connect(lambda: self._on_remove(self))
            layout.addWidget(btn)

        layout.addStretch()

    @staticmethod
    def _unit_of(key: str) -> str:
        for k, _, u in MEASUREMENT_TYPES:
            if k == key:
                return u
        return ""

    # ── 获取配置 ───────────────────────────────────────────────

    def get_name(self) -> str:
        return self.name_edit.text() or f"{self.get_channel()}_{self.get_meas_key()}"

    def get_channel(self) -> str:
        return self.channel_combo.currentText()

    def get_meas_key(self) -> str:
        return self.meas_combo.currentData()

    def get_start_time(self) -> float:
        return self.start_spin.value()

    def get_end_time(self) -> float:
        return self.end_spin.value()

    # ── 计算值 ─────────────────────────────────────────────────

    def compute_value(self, result: AnalysisResult) -> Optional[float]:
        """
        从 AnalysisResult 中提取通道数据, 按时间窗口切片, 计算测量值。
        """
        ch_name = self.get_channel()
        ch_data = result.channels.get(ch_name)
        if ch_data is None:
            return None

        data = ch_data.raw
        time_axis = ch_data.time_axis
        fs = ch_data.sample_rate
        meas_key = self.get_meas_key()

        # 时间窗口 → 样本范围
        start_t = self.get_start_time()
        end_t = self.get_end_time()
        if end_t <= start_t:
            return None

        # 找到对应样本索引
        idx_start = int(start_t * fs)
        idx_end = int(end_t * fs)
        idx_start = max(0, idx_start)
        idx_end = min(len(data), idx_end)

        if idx_end - idx_start < 2:
            return None

        segment = data[idx_start:idx_end]

        # 调用测量函数
        func = MEASUREMENT_FUNCTIONS.get(meas_key)
        if func is None:
            return None

        return func(segment, fs)

    def update_value(self, result: AnalysisResult):
        """用一帧数据计算并显示当前值"""
        value = self.compute_value(result)
        if value is not None:
            self.value_label.setText(self._fmt(value))
        else:
            self.value_label.setText("—")

    @staticmethod
    def _fmt(v: float) -> str:
        if abs(v) >= 10000:
            return f"{v:.1f}"
        elif abs(v) >= 1:
            return f"{v:.4f}"
        elif abs(v) >= 0.001:
            return f"{v:.6f}"
        elif v == 0:
            return "0"
        else:
            return f"{v:.3e}"


class MeasurementPanel:
    """
    动态测量面板控制器。

    每行: 名称 + 通道 + 测量项 + 起始时间 + 结束时间 → 值
    """

    def __init__(self, parent_widget: QWidget):
        self._parent = parent_widget
        self._rows: list[MeasurementRow] = []
        self._last_result: Optional[AnalysisResult] = None
        self._setup_ui()

        # 默认行
        self.add_row(name="CH1 幅值", meas_key="Vpp", end_time=0.01)
        self.add_row(name="CH1 频率", meas_key="Freq", end_time=0.01)
        self.add_row(name="CH2 幅值", meas_key="Vpp", end_time=0.01)

    def _setup_ui(self):
        layout = self._parent.layout() or QVBoxLayout(self._parent)
        self._parent.setLayout(layout)

        # 标题
        title = QLabel("测量项 (名称 / 通道 / 测量项 / 起始时间 / 结束时间)")
        title.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(title)

        # 滚动区
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

        # 按钮
        btn_add = QPushButton("+ 添加测量")
        btn_add.setStyleSheet(
            "QPushButton { color: #00CC00; border: 1px solid #336633; "
            "padding: 4px; }"
            "QPushButton:hover { background: #224422; }"
        )
        btn_add.clicked.connect(self._on_add)
        layout.addWidget(btn_add)

    def add_row(self, name: str = "", channel: str = "CH1",
                meas_key: str = "Vpp",
                start_time: float = 0.0, end_time: float = 0.01):
        frame_dur = 0.01
        if self._last_result:
            ch = self._last_result.channels.get(channel)
            if ch:
                frame_dur = ch.time_axis[-1] if len(ch.time_axis) > 0 else 0.01

        row = MeasurementRow(
            name=name, channel=channel, meas_key=meas_key,
            start_time=start_time, end_time=end_time or frame_dur,
            frame_duration=frame_dur,
            on_remove=self._on_remove,
        )
        self._rows.append(row)
        self._container_layout.insertWidget(
            self._container_layout.count() - 1, row
        )
        return row

    def _on_add(self):
        self.add_row()

    def _on_remove(self, row: MeasurementRow):
        if row in self._rows:
            self._rows.remove(row)
            self._container_layout.removeWidget(row)
            row.deleteLater()

    def update_from_result(self, result: AnalysisResult):
        """用最新一帧 AnalysisResult 更新所有行"""
        self._last_result = result
        for row in self._rows:
            row.update_value(result)

    def get_subscriptions(self) -> list[dict]:
        """返回订阅信息, 供 FeedbackPanel 使用"""
        return [
            {
                "name": row.get_name(),
                "channel": row.get_channel(),
                "meas_key": row.get_meas_key(),
                "start": row.get_start_time(),
                "end": row.get_end_time(),
            }
            for row in self._rows
        ]

    def clear_all(self):
        for row in list(self._rows):
            self._on_remove(row)
