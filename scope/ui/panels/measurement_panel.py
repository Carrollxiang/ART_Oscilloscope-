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
    QFrame,
    QHBoxLayout,
    QVBoxLayout,
    QComboBox,
    QLabel,
    QLineEdit,
    QDoubleSpinBox,
    QPushButton,
    QScrollArea,
    QWidget,
)

from scope.runtime import FittedSnapshot, MeasurementSpec, MeasurementSpecsChanged

logger = logging.getLogger(__name__)

CHANNELS = ["CH1", "CH2", "CH3", "CH4", "CH5", "CH6", "CH7", "CH8",
             "CH9", "CH10", "CH11", "CH12", "CH13", "CH14", "CH15", "CH16"]

# (key, label, unit) - 4 个基本测量量 + 比值 (两个测量项相除)
MEASUREMENT_TYPES: list[tuple[str, str, str]] = [
    ("Vpp",  "峰峰值", "V"),
    ("Vmax", "最大值", "V"),
    ("Vmin", "最小值", "V"),
    ("Mean", "平均值", "V"),
    ("Ratio", "比值", ""),
]

# 比值测量项的 feature key (与 MeasurementSpec 一致)
RATIO_MEAS_KEY = "Ratio"

# 下拉框统一外观
_COMBO_STYLE = (
    "NoWheelComboBox { background: #F5F5F5; color: #111; border: 1px solid #999;"
    " border-radius: 3px; padding: 2px 4px; }"
    "NoWheelComboBox:hover { border-color: #777; }"
    "NoWheelComboBox QAbstractItemView { background: #FFFFFF; color: #111;"
    "  selection-background-color: #DDDDDD; }"
)


class NoWheelComboBox(QComboBox):
    """QComboBox 子类 — 忽略鼠标滚轮事件，防止滚轮意外修改值"""
    def wheelEvent(self, e):
        e.ignore()


class NoWheelDoubleSpinBox(QDoubleSpinBox):
    """QDoubleSpinBox 子类 — 忽略鼠标滚轮事件，防止滚轮意外修改值"""
    def wheelEvent(self, e):
        e.ignore()


class MeasurementRow(QFrame):
    """单行卡片: [名称] [通道▼] [测量项▼] [起始] [结束] [值] [✕]"""

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
                 on_remove=None,
                 stable_tag: str | None = None,
                 numerator_tag: str = "",
                 denominator_tag: str = ""):
        super().__init__(parent)
        self._row_id = self._allocate_row_id(stable_tag)
        self._stable_tag = stable_tag or f"m{self._row_id}"
        self._meas_key = meas_key
        self._on_remove = on_remove
        self._frame_duration = frame_duration
        self._numerator_tag = numerator_tag
        self._denominator_tag = denominator_tag
        self._ratio_labels: dict[str, str] = {}   # 源测量项 tag → 显示名
        self._invalid_reason = ""

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet("""
            MeasurementRow {
                border: 1px solid #CCCCCC;
                border-radius: 4px;
                padding: 0px;
                margin: 1px 0px;
                background: #FFFFFF;
            }
            MeasurementRow:hover {
                border-color: #999999;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)

        # 名称（用户可改，仅作显示用）
        self.name_edit = QLineEdit(name)
        self.name_edit.setPlaceholderText("名称")
        self.name_edit.setStyleSheet(
            "QLineEdit { background: #F5F5F5; color: #111; border: 1px solid #999;"
            " border-radius: 3px; padding: 2px 4px; }"
        )
        self.name_edit.setFixedWidth(240)
        self.name_edit.textChanged.connect(lambda: self.name_changed.emit(self._row_id))
        layout.addWidget(self.name_edit)

        # 通道
        self.channel_combo = NoWheelComboBox()
        self.channel_combo.setStyleSheet(
            "NoWheelComboBox { background: #F5F5F5; color: #111; border: 1px solid #999;"
            " border-radius: 3px; padding: 2px 4px; }"
            "NoWheelComboBox:hover { border-color: #777; }"
            "NoWheelComboBox QAbstractItemView { background: #FFFFFF; color: #111;"
            "  selection-background-color: #DDDDDD; }"
        )
        self.channel_combo.addItems(CHANNELS)
        self.channel_combo.setCurrentText(channel)
        self.channel_combo.setFixedWidth(65)
        layout.addWidget(self.channel_combo)

        # 测量项
        self.meas_combo = NoWheelComboBox()
        self.meas_combo.setStyleSheet(
            "NoWheelComboBox { background: #F5F5F5; color: #111; border: 1px solid #999;"
            " border-radius: 3px; padding: 2px 4px; }"
            "NoWheelComboBox:hover { border-color: #777; }"
            "NoWheelComboBox QAbstractItemView { background: #FFFFFF; color: #111;"
            "  selection-background-color: #DDDDDD; }"
        )
        for key, label, unit in MEASUREMENT_TYPES:
            self.meas_combo.addItem(f"{label} ({unit})", key)
        idx = next((i for i, (k, _, _) in enumerate(MEASUREMENT_TYPES)
                    if k == meas_key), 0)
        self.meas_combo.setCurrentIndex(idx)
        self.meas_combo.setFixedWidth(120)
        layout.addWidget(self.meas_combo)

        # 起始时间 (ms)
        self.start_spin = NoWheelDoubleSpinBox()
        self.start_spin.setStyleSheet(
            "NoWheelDoubleSpinBox { background: #F5F5F5; color: #111; border: 1px solid #999;"
            " border-radius: 3px; padding: 2px 2px; }"
            "NoWheelDoubleSpinBox:hover { border-color: #777; }"
        )
        self.start_spin.setDecimals(1)
        self.start_spin.setRange(0.0, 60_000.0)
        self.start_spin.setSuffix(" ms")
        self.start_spin.setValue(start_time)
        self.start_spin.setSingleStep(10.0)
        self.start_spin.setFixedWidth(90)
        layout.addWidget(self.start_spin)

        # 结束时间 (ms)
        self.end_spin = NoWheelDoubleSpinBox()
        self.end_spin.setStyleSheet(
            "NoWheelDoubleSpinBox { background: #F5F5F5; color: #111; border: 1px solid #999;"
            " border-radius: 3px; padding: 2px 2px; }"
            "NoWheelDoubleSpinBox:hover { border-color: #777; }"
        )
        self.end_spin.setDecimals(1)
        self.end_spin.setRange(0.1, 60_000.0)
        self.end_spin.setSuffix(" ms")
        self.end_spin.setValue(end_time)
        self.end_spin.setSingleStep(10.0)
        self.end_spin.setFixedWidth(90)
        layout.addWidget(self.end_spin)

        layout.addStretch()

        # 比值分子 / 分母 (仅测量项 = 比值 时显示, 取代通道与时间窗)
        self.ratio_num_combo = NoWheelComboBox()
        self.ratio_den_combo = NoWheelComboBox()
        for combo in (self.ratio_num_combo, self.ratio_den_combo):
            combo.setStyleSheet(_COMBO_STYLE)
            combo.setToolTip("比值测量项的源测量项 (同帧相除)")
            combo.setFixedWidth(120)
        self.ratio_slash = QLabel("/")
        self.ratio_slash.setStyleSheet("color: #666;")
        layout.addWidget(self.ratio_num_combo)
        layout.addWidget(self.ratio_slash)
        layout.addWidget(self.ratio_den_combo)

        # 值 + 单位 (右侧视觉重心)
        self.value_label = QLabel("—")
        self.value_label.setStyleSheet(
            "color: #00008B; font-weight: bold; font-size: 15px;"
            "font-family: Consolas, monospace;"
        )
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.value_label.setMinimumWidth(150)
        layout.addWidget(self.value_label)

        # 删除
        if on_remove:
            btn = QPushButton("✕")
            btn.setFixedSize(22, 22)
            btn.setStyleSheet(
                "QPushButton { color: #FF4444; border: none; }"
                "QPushButton:hover { background: #FFCCCC; }"
            )
            btn.clicked.connect(lambda: self._on_remove(self))
            layout.addWidget(btn)

        self.channel_combo.currentIndexChanged.connect(self._emit_config_changed)
        self.meas_combo.currentIndexChanged.connect(self._emit_config_changed)
        self.meas_combo.currentIndexChanged.connect(self._update_value_unit)
        self.meas_combo.currentIndexChanged.connect(self._apply_meas_mode)
        self.start_spin.valueChanged.connect(self._emit_config_changed)
        self.end_spin.valueChanged.connect(self._emit_config_changed)
        self.ratio_num_combo.currentIndexChanged.connect(self._emit_config_changed)
        self.ratio_den_combo.currentIndexChanged.connect(self._emit_config_changed)

        self._apply_meas_mode()
        self._update_value_unit()

    def _emit_config_changed(self, *args):
        self.config_changed.emit(self._row_id)

    def _apply_meas_mode(self):
        """切换标量/比值模式: 比值时禁用通道与时间窗, 显示分子/分母。"""
        is_ratio = self.get_meas_key() == RATIO_MEAS_KEY
        # 比值行没有"自身通道"概念: 禁用并隐藏, 避免与分子/分母的源通道混淆
        self.channel_combo.setEnabled(not is_ratio)
        self.channel_combo.setVisible(not is_ratio)
        self.start_spin.setVisible(not is_ratio)
        self.end_spin.setVisible(not is_ratio)
        self.ratio_num_combo.setVisible(is_ratio)
        self.ratio_slash.setVisible(is_ratio)
        self.ratio_den_combo.setVisible(is_ratio)

    def set_ratio_candidates(self, candidates: list[tuple[str, str]]):
        """
        注入可选源测量项到分子/分母下拉。

        关键点: 已填充过的下拉一律以**当前选择**为准, 不能用
        `_numerator_tag` / `_denominator_tag` 恢复 —— 否则用户刚改完选择,
        刷新候选时会被旧字段回滚 (表现为"选了不变")。

        Args:
            candidates: [(显示名, tag)] — 只应包含标量测量项(排除本行)
        """
        self._ratio_labels = {tag: label for label, tag in candidates}
        candidate_tags = [tag for _, tag in candidates]
        first_fill = self.ratio_num_combo.count() == 0 and self.ratio_den_combo.count() == 0

        for combo, configured in (
            (self.ratio_num_combo, self._numerator_tag),
            (self.ratio_den_combo, self._denominator_tag),
        ):
            # 已填充 → 保留用户当前选择; 首次填充 → 用配置值/默认值
            current = (combo.currentData() if combo.count() else None) or configured

            combo.blockSignals(True)
            if [combo.itemData(i) for i in range(combo.count())] != candidate_tags:
                combo.clear()
                for label, tag in candidates:
                    combo.addItem(f"{label} ({tag})", tag)
            else:
                # 候选集合未变: 只刷新显示名, 不动当前选择
                for i, (label, tag) in enumerate(candidates):
                    combo.setItemText(i, f"{label} ({tag})")

            idx = combo.findData(current) if current else -1
            if current and idx < 0:
                # 源测量项已被删除: 保留原选择, 标记为缺失, 避免静默改配置
                combo.addItem(f"{current} (缺失)", current)
                idx = combo.count() - 1
            elif idx < 0 and combo.count() > 0:
                idx = 0
            combo.setCurrentIndex(idx)
            combo.blockSignals(False)

        if first_fill and len(candidate_tags) >= 2:
            # 默认分母与分子相同会算出 1.0, 且 spec 校验会判为非法 → 换成第二个候选
            if self.get_numerator_tag() == self.get_denominator_tag():
                self.ratio_den_combo.blockSignals(True)
                self.ratio_den_combo.setCurrentIndex(1)
                self.ratio_den_combo.blockSignals(False)

        self._numerator_tag = self.get_numerator_tag()
        self._denominator_tag = self.get_denominator_tag()

    @classmethod
    def _allocate_row_id(cls, stable_tag: str | None) -> int:
        """为行分配 ID；尽量从 m123 形式的 tag 中恢复原 ID。"""
        row_id = None
        if stable_tag and stable_tag.startswith("m"):
            suffix = stable_tag[1:]
            if suffix.isdigit():
                row_id = int(suffix)

        if row_id is None:
            row_id = cls._next_id

        cls._next_id = max(cls._next_id, row_id + 1)
        return row_id

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
        return self._stable_tag

    def get_name(self) -> str:
        if self.name_edit.text():
            return self.name_edit.text()
        if self.get_meas_key() == RATIO_MEAS_KEY:
            num = self._ratio_label(self.get_numerator_tag())
            den = self._ratio_label(self.get_denominator_tag())
            if num or den:
                return f"{num or '?'}/{den or '?'}"
        return f"M{self._row_id}"

    def _ratio_label(self, tag: str) -> str:
        """源测量项 tag → 显示名 (无候选时回退 tag 本身)。"""
        if not tag:
            return ""
        return self._ratio_labels.get(tag, tag)

    def is_ratio(self) -> bool:
        """本行是否为比值测量项。"""
        return self.get_meas_key() == RATIO_MEAS_KEY

    def get_numerator_tag(self) -> str:
        return self.ratio_num_combo.currentData() or ""

    def get_denominator_tag(self) -> str:
        return self.ratio_den_combo.currentData() or ""

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

    _last_value: Optional[float] = None

    def set_value(self, value: Optional[float]):
        """用已算好的值直接更新显示。从 FittedSnapshot 获取。"""
        self._last_value = value
        self._invalid_reason = ""
        self.value_label.setToolTip("")
        unit = self._unit_of(self.get_meas_key())
        suffix = f" {unit}" if unit else ""
        if value is not None and not np.isnan(value):
            self.value_label.setText(f"{self._fmt(value)}{suffix}")
        else:
            self.value_label.setText(f"—{suffix}")

    def set_invalid_reason(self, reason: str):
        """本帧无效 (比值分母接近 0 等) → 值显示「— (无效)」并挂原因 tooltip。"""
        self._invalid_reason = reason
        if reason:
            unit = self._unit_of(self.get_meas_key())
            suffix = f" {unit}" if unit else ""
            self.value_label.setText(f"— (无效){suffix}")
            self.value_label.setToolTip(f"无效: {reason}")
        else:
            self.value_label.setToolTip("")

    @staticmethod
    def _fmt(v: float) -> str:
        """固定 4 位小数格式化"""
        return f"{v:.4f}"

    def _update_value_unit(self):
        """测量项变更时刷新单位后缀"""
        self.set_value(self._last_value)


class MeasurementPanel:
    """
    动态测量面板控制器。

    每行: 名称 + 通道 + 测量项 + 起始时间 + 结束时间 → 值

    注意: 本面板不执行计算，只显示 MeasurementProcessor 的结果。
    """

    def __init__(
        self,
        parent_widget: QWidget,
        event_bus=None,
        initial_measurements: list[dict] | None = None,
    ):
        self._parent = parent_widget
        self._rows: list[MeasurementRow] = []
        self._event_bus = event_bus
        self._name_change_callback = None
        self._spec_change_id = 0
        self._suspend_spec_publish = False
        self._setup_ui()

        if initial_measurements:
            self.set_config(initial_measurements)
        else:
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
        title = QLabel("测量项 (名称 / 通道 / 测量 / 起始~结束 ms；测量=比值 时改选分子/分母)")
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
        self._container_layout.setSpacing(4)

        # 列标题栏
        header = QWidget()
        header.setStyleSheet("background: transparent;")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(8, 2, 8, 2)
        header_layout.setSpacing(8)
        cols = [
            ("名称", 240),
            ("通道", 65),
            ("测量", 120),
            ("起始", 90),
            ("结束", 90),
        ]
        for text, w in cols:
            lbl = QLabel(text)
            lbl.setFixedWidth(w)
            lbl.setStyleSheet("color: #333; font-size: 10px;")
            header_layout.addWidget(lbl)
        # 值标题 (右侧)
        val_lbl = QLabel("值")
        val_lbl.setStyleSheet("color: #333; font-size: 10px;")
        val_lbl.setMinimumWidth(150)
        val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        header_layout.addWidget(val_lbl)
        # 删除列占位
        header_layout.addSpacing(22)
        header_layout.addStretch()
        self._container_layout.addWidget(header)

        self._container_layout.addStretch()
        scroll.setWidget(self._container)
        layout.addWidget(scroll, stretch=1)

        # 按钮
        btn_add = QPushButton("+ 添加测量")
        btn_add.setStyleSheet(
            "QPushButton { color: #00CC00; border: 1px solid #336633; "
            "padding: 4px; }"
            "QPushButton:hover { background: #D0F0D0; }"
        )
        btn_add.clicked.connect(self._on_add)
        layout.addWidget(btn_add)

    def add_row(self, name: str = "", channel: str = "CH1",
                meas_key: str = "Vpp",
                start_time: float = 0.0, end_time: float = 500.0,
                stable_tag: str | None = None,
                publish: bool = True,
                numerator_tag: str = "",
                denominator_tag: str = ""):
        row = MeasurementRow(
            name=name, channel=channel, meas_key=meas_key,
            start_time=start_time, end_time=end_time,
            frame_duration=500.0,
            on_remove=self._on_remove,
            stable_tag=stable_tag,
            numerator_tag=numerator_tag,
            denominator_tag=denominator_tag,
        )
        row.name_changed.connect(self._on_row_name_changed)
        row.config_changed.connect(self._on_row_config_changed)
        self._rows.append(row)
        self._container_layout.insertWidget(
            self._container_layout.count() - 1, row
        )
        self._refresh_ratio_candidates()
        if publish and not self._suspend_spec_publish:
            self._publish_specs_changed()
        return row

    def _refresh_ratio_candidates(self):
        """把全部标量测量项注入所有比值行的分子/分母下拉。"""
        candidates = [
            (row.get_name(), row.stable_tag)
            for row in self._rows
            if not row.is_ratio()
        ]
        for row in self._rows:
            if row.is_ratio():
                row.set_ratio_candidates(candidates)

    def _on_row_name_changed(self, row_id: int):
        """行名称变更 → 刷新比值候选显示名 → 通知回调"""
        self._refresh_ratio_candidates()
        if self._name_change_callback:
            self._name_change_callback()

    def _on_row_config_changed(self, row_id: int):
        """行配置变更 → 刷新比值候选 → 发布完整测量规格快照"""
        self._refresh_ratio_candidates()
        self._publish_specs_changed()

    def _on_add(self):
        self.add_row()

    def _on_remove(self, row: MeasurementRow):
        if row in self._rows:
            tag = row.stable_tag
            self._rows.remove(row)
            self._container_layout.removeWidget(row)
            row.deleteLater()

            self._refresh_ratio_candidates()
            if self._event_bus:
                self._event_bus.publish("measurement.remove", tag)
            if not self._suspend_spec_publish:
                self._publish_specs_changed()

    def get_measurement_specs(self) -> list[dict]:
        """返回测量规格列表，供 MeasurementProcessor 使用"""
        return [
            {
                "tag": row.stable_tag,
                "name": row.get_name(),
                "channel": row.get_channel_index(),
                "channel_name": row.get_channel(),
                "feature": row.get_meas_key(),
                "start_ms": row.get_start_time(),
                "end_ms": row.get_end_time(),
                "numerator_tag": row.get_numerator_tag(),
                "denominator_tag": row.get_denominator_tag(),
            }
            for row in self._rows
        ]

    def get_display_name_mapping(self) -> dict[str, str]:
        """返回 {稳定tag: 显示名称} 映射，供 FeedbackPanel 使用"""
        return {row.stable_tag: row.get_name() for row in self._rows}

    def update_from_fitted(self, snap: FittedSnapshot):
        """用 FittedSnapshot 更新所有行的显示值 (无效项显示原因)。"""
        get_invalid = getattr(snap, "get_invalid", None)
        for row in self._rows:
            value = snap.get(row.stable_tag)
            reason = get_invalid(row.stable_tag) if get_invalid else None
            if value is None and reason:
                row.set_invalid_reason(reason)
            else:
                row.set_value(value)

    def get_subscriptions(self) -> list[dict]:
        """返回订阅信息, 供 FeedbackPanel 使用"""
        return self.get_measurement_specs()

    def clear_all(self):
        for row in list(self._rows):
            self._on_remove(row)

    def set_config(self, config: list[dict]):
        """恢复测量配置（清空重建）"""
        self._suspend_spec_publish = True
        try:
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
                    stable_tag=item.get("tag"),
                    numerator_tag=item.get("numerator_tag", ""),
                    denominator_tag=item.get("denominator_tag", ""),
                    publish=False,
                )
        finally:
            self._suspend_spec_publish = False

        self._publish_specs_changed()

    @staticmethod
    def _runtime_spec_from_item(item: dict) -> MeasurementSpec | None:
        """把 UI 行快照转成运行时规格；非法项保留 UI 行但不发布。"""
        if (str(item.get("feature", "")).lower() == "ratio"
                and item.get("numerator_tag") == item.get("denominator_tag")):
            # 用户配置中间态 (分子/分母暂相同): 静默跳过, 不打告警
            logger.debug("比值项 %s 分子与分母相同, 暂不发布", item.get("tag"))
            return None
        try:
            return MeasurementSpec(
                tag=item["tag"],
                channel=item["channel"],
                feature=item["feature"],
                start_ms=item["start_ms"],
                end_ms=item["end_ms"],
                numerator_tag=item.get("numerator_tag", ""),
                denominator_tag=item.get("denominator_tag", ""),
            )
        except Exception as e:
            logger.warning(
                "跳过无效测量项 %s (%s): %s",
                item.get("tag", "<unknown>"),
                item.get("name", ""),
                e,
            )
            return None

    def _publish_specs_changed(self):
        """发布完整 MeasurementSpec 快照到控制面。"""
        if not self._event_bus or self._suspend_spec_publish:
            return

        specs = [
            spec
            for item in self.get_measurement_specs()
            if (spec := self._runtime_spec_from_item(item)) is not None
        ]

        self._spec_change_id += 1
        self._event_bus.publish(
            "measurement.specs.changed",
            MeasurementSpecsChanged(
                specs=specs,
                change_id=self._spec_change_id,
            ),
        )
