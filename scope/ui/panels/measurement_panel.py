"""
动态测量面板 — 每行独立配置: 名称 + 通道 + 测量项 + 时间段

每行:
  [名称___] [CH1 ▼] [Vpp ▼] [起始_0.000s] [结束_0.010s] [2.0000 V] [✕]

- 每行限定时间段 (起始～结束), 只计算该段内的测量值
- 不同行可对同一通道的不同时间段测量不同物理量
- 每行有独立名称用于标识和反馈订阅

注意: 测量计算由 MeasurementProcessor 完成，本面板只负责:
  1. 配置 UI
  2. 从 FittedSnapshot 显示结果
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
from PyQt6.QtCore import Qt, pyqtSignal
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

from scope.runtime import FittedSnapshot, MeasurementSpec, MeasurementSpecsChanged

logger = logging.getLogger(__name__)

CHANNELS = ["CH1", "CH2", "CH3", "CH4", "CH5", "CH6", "CH7", "CH8",
             "CH9", "CH10", "CH11", "CH12", "CH13", "CH14", "CH15", "CH16"]

# (key, label, unit) - 只保留 4 个基本测量量
MEASUREMENT_TYPES: list[tuple[str, str, str]] = [
    ("Vpp",  "峰峰值", "V"),
    ("Vmax", "最大值", "V"),
    ("Vmin", "最小值", "V"),
    ("Mean", "平均值", "V"),
]


class MeasurementRow(QWidget):
    """单行: [名称] [通道▼] [测量项▼] [起始] [结束] [值] [✕]"""

    # 类级别自增 ID 计数器
    _next_id = 0
    # 名称变更信号
    name_changed = pyqtSignal(int)  # row_id
    config_changed = pyqtSignal(int)  # row_id

    def __init__(self, parent=None,
                 name: str = "",
                 channel: str = "CH1",
                 meas_key: str = "Vpp",
                 start_time: float = 0.0,
                 end_time: float = 500.0,
                 frame_duration: float = 500.0,
                 on_remove=None):
        super().__init__(parent)
        self._row_id = MeasurementRow._next_id
        MeasurementRow._next_id += 1
        self._meas_key = meas_key
        self._on_remove = on_remove
        self._frame_duration = frame_duration

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 3, 0, 3)
        layout.setSpacing(6)

        # 名称（用户可改，仅作显示用）
        self.name_edit = QLineEdit(name)
        self.name_edit.setPlaceholderText("名称")
        self.name_edit.setFixedWidth(80)
        self.name_edit.textChanged.connect(lambda: self.name_changed.emit(self._row_id))
        layout.addWidget(self.name_edit)

        # 通道
        self.channel_combo = QComboBox()
        self.channel_combo.addItems(CHANNELS)
        self.channel_combo.setCurrentText(channel)
        self.channel_combo.setFixedWidth(65)
        layout.addWidget(self.channel_combo)

        # 测量项
        self.meas_combo = QComboBox()
        for key, label, unit in MEASUREMENT_TYPES:
            self.meas_combo.addItem(f"{label} ({unit})", key)
        idx = next((i for i, (k, _, _) in enumerate(MEASUREMENT_TYPES)
                    if k == meas_key), 0)
        self.meas_combo.setCurrentIndex(idx)
        self.meas_combo.setFixedWidth(120)
        layout.addWidget(self.meas_combo)

        # 起始时间 (ms)
        self.start_spin = QDoubleSpinBox()
        self.start_spin.setDecimals(1)
        self.start_spin.setRange(0.0, 60_000.0)
        self.start_spin.setSuffix(" ms")
        self.start_spin.setValue(start_time)
        self.start_spin.setSingleStep(10.0)
        self.start_spin.setFixedWidth(90)
        layout.addWidget(self.start_spin)

        # 结束时间 (ms)
        self.end_spin = QDoubleSpinBox()
        self.end_spin.setDecimals(1)
        self.end_spin.setRange(0.1, 60_000.0)
        self.end_spin.setSuffix(" ms")
        self.end_spin.setValue(end_time)
        self.end_spin.setSingleStep(10.0)
        self.end_spin.setFixedWidth(90)
        layout.addWidget(self.end_spin)

        # 值 + 标准差 + 单位
        self.value_label = QLabel("—")
        self.value_label.setStyleSheet("font-weight: bold; font-size: 13px;")
        self.value_label.setMinimumWidth(160)
        layout.addWidget(self.value_label)

        # 删除
        if on_remove:
            btn = QPushButton("✕")
            btn.setFixedSize(22, 22)
            btn.setStyleSheet(
                "QPushButton { color: #FF4444; border: none; }"
                "QPushButton:hover { background: #442222; }"
            )
            btn.clicked.connect(lambda: self._on_remove(self))
            layout.addWidget(btn)

        layout.addStretch()

        self.channel_combo.currentIndexChanged.connect(self._emit_config_changed)
        self.meas_combo.currentIndexChanged.connect(self._emit_config_changed)
        self.start_spin.valueChanged.connect(self._emit_config_changed)
        self.end_spin.valueChanged.connect(self._emit_config_changed)

    def _emit_config_changed(self, *args):
        self.config_changed.emit(self._row_id)

    @staticmethod
    def _unit_of(key: str) -> str:
        for k, _, u in MEASUREMENT_TYPES:
            if k == key:
                return u
        return ""

    # ── 获取配置 ───────────────────────────────────────────────

    @property
    def row_id(self) -> int:
        """稳定唯一 ID（创建后不变）"""
        return self._row_id

    @property
    def stable_tag(self) -> str:
        """稳定标识符，用于 FittedSnapshot key 和反馈订阅"""
        return f"m{self._row_id}"

    def get_name(self) -> str:
        return self.name_edit.text() or f"M{self._row_id}"

    def get_channel(self) -> str:
        return self.channel_combo.currentText()

    def get_meas_key(self) -> str:
        return self.meas_combo.currentData()

    def get_channel_index(self) -> int:
        """返回 0-based 通道索引"""
        return self.channel_combo.currentIndex()

    def get_start_time(self) -> float:
        return self.start_spin.value()

    def get_end_time(self) -> float:
        return self.end_spin.value()

    # ── 显示值 ─────────────────────────────────────────────────

    def set_value(self, value: Optional[float]):
        """用已算好的值直接更新显示。从 FittedSnapshot 获取。"""
        unit = self._unit_of(self.get_meas_key())
        if value is not None and not np.isnan(value):
            self.value_label.setText(f"{self._fmt(value)} {unit}")
        else:
            self.value_label.setText(f"— {unit}")

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

    注意: 本面板不执行计算，只显示 MeasurementProcessor 的结果。
    """

    def __init__(self, parent_widget: QWidget, event_bus=None):
        self._parent = parent_widget
        self._rows: list[MeasurementRow] = []
        self._event_bus = event_bus
        self._name_change_callback = None
        self._spec_change_id = 0
        self._setup_ui()

        # 默认行
        self.add_row(name="CH1_vpp", channel="CH1", meas_key="Vpp", end_time=500)
        self.add_row(name="CH1_mean", channel="CH1", meas_key="Mean", end_time=500)
        self.add_row(name="CH2_vpp", channel="CH2", meas_key="Vpp", end_time=500)

    def set_name_change_callback(self, callback):
        """设置名称变更回调（用于通知 FeedbackPanel 刷新）"""
        self._name_change_callback = callback

    def _setup_ui(self):
        # 获取或创建布局, 清空子控件
        layout = self._parent.layout()
        if layout is None:
            layout = QVBoxLayout(self._parent)
            self._parent.setLayout(layout)
        else:
            # 只清除子控件, 保留布局本身
            while layout.count():
                item = layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

        # 标题
        title = QLabel("测量项 (名称 / 通道 / 测量 / 起始~结束 ms)")
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
                start_time: float = 0.0, end_time: float = 500.0):
        row = MeasurementRow(
            name=name, channel=channel, meas_key=meas_key,
            start_time=start_time, end_time=end_time,
            frame_duration=500.0,
            on_remove=self._on_remove,
        )
        row.name_changed.connect(self._on_row_name_changed)
        row.config_changed.connect(self._on_row_config_changed)
        self._rows.append(row)
        self._container_layout.insertWidget(
            self._container_layout.count() - 1, row
        )
        self._publish_specs_changed()
        return row

    def _on_row_name_changed(self, row_id: int):
        """行名称变更 → 通知回调"""
        if self._name_change_callback:
            self._name_change_callback()

    def _on_row_config_changed(self, row_id: int):
        """行配置变更 → 发布完整测量规格快照"""
        self._publish_specs_changed()

    def _on_add(self):
        self.add_row()

    def _on_remove(self, row: MeasurementRow):
        if row in self._rows:
            tag = row.stable_tag
            self._rows.remove(row)
            self._container_layout.removeWidget(row)
            row.deleteLater()

            if self._event_bus:
                self._event_bus.publish("measurement.remove", tag)
            self._publish_specs_changed()

    def get_measurement_specs(self) -> list[dict]:
        """返回测量规格列表，供 MeasurementProcessor 使用"""
        return [
            {
                "tag": row.stable_tag,
                "channel": row.get_channel_index(),
                "feature": row.get_meas_key(),
                "start_ms": row.get_start_time(),
                "end_ms": row.get_end_time(),
            }
            for row in self._rows
        ]

    def get_display_name_mapping(self) -> dict[str, str]:
        """返回 {稳定tag: 显示名称} 映射，供 FeedbackPanel 使用"""
        return {row.stable_tag: row.get_name() for row in self._rows}

    def update_from_fitted(self, snap: FittedSnapshot):
        """用 FittedSnapshot 更新所有行的显示值。"""
        for row in self._rows:
            value = snap.get(row.stable_tag)
            row.set_value(value)

    def get_subscriptions(self) -> list[dict]:
        """返回订阅信息, 供 FeedbackPanel 使用"""
        return self.get_measurement_specs()

    def clear_all(self):
        for row in list(self._rows):
            self._on_remove(row)

    def set_config(self, config: list[dict]):
        """恢复测量配置（清空重建）"""
        self.clear_all()

        for item in config:
            # 兼容新旧格式：新格式有"name"字段，旧格式用"tag"作为显示名
            display_name = item.get("name") or item.get("tag", "")
            self.add_row(
                name=display_name,
                channel=f"CH{item.get('channel', 0) + 1}",
                meas_key=item.get("feature", "Vpp"),
                start_time=item.get("start_ms", 0.0),
                end_time=item.get("end_ms", 500.0),
            )

        self._publish_specs_changed()

    def _publish_specs_changed(self):
        """发布完整 MeasurementSpec 快照到控制面。"""
        if not self._event_bus:
            return

        try:
            specs = [MeasurementSpec(**item) for item in self.get_measurement_specs()]
        except Exception as e:
            logger.warning("构建 MeasurementSpec 快照失败: %s", e)
            return

        self._spec_change_id += 1
        self._event_bus.publish(
            "measurement.specs.changed",
            MeasurementSpecsChanged(
                specs=specs,
                change_id=self._spec_change_id,
            ),
        )
