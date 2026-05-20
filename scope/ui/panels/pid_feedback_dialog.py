"""
PID 反馈配置对话框 — AD9910 / RTMQ 独立选择, PID 参数配置
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QLabel, QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox,
    QRadioButton, QDialogButtonBox, QListWidget, QAbstractItemView,
    QStackedWidget, QWidget,
)

from scope.io.feedback_slots.pid_slot import (
    PidSlotConfig,
    PidParams,
    Ad9910Target,
    RtmqTarget,
)
from scope.io.feedback_slots.base import DataSubscription

TEXT_DIM = "#888888"
TEXT_LABEL = "#555555"


class PidFeedbackDialog(QDialog):
    """PID 反馈目标配置对话框。"""

    def __init__(self, parent=None, slot_id: str = "",
                 measurement_items: list[dict] = None,
                 existing_config: PidSlotConfig = None):
        super().__init__(parent)
        self.setWindowTitle("PID 反馈配置")
        self.setMinimumSize(520, 520)
        self._meas_items = measurement_items or []
        self._build_ui()

        if existing_config:
            self._load(existing_config)
        if slot_id:
            self.editId.setText(slot_id)
            self.editId.setEnabled(False)

    def _build_ui(self):
        lay = QVBoxLayout(self)

        # ── 基本 ──
        g0 = QGroupBox("基本")
        f0 = QFormLayout(g0)
        self.editId = QLineEdit()
        self.editId.setPlaceholderText("唯一标识, 如 ch1-fb-dds")
        self.editLabel = QLineEdit()
        self.editLabel.setPlaceholderText("显示名 (可选)")
        f0.addRow("标识", self.editId)
        f0.addRow("标签", self.editLabel)
        lay.addWidget(g0)

        # ── 目标设备 ──
        g1 = QGroupBox("目标设备")
        f1 = QFormLayout(g1)
        self.editIp = QLineEdit("192.168.1.20")
        self.editPort = QSpinBox(); self.editPort.setRange(1, 65535); self.editPort.setValue(3251)
        f1.addRow("IP", self.editIp)
        f1.addRow("端口", self.editPort)
        lay.addWidget(g1)

        # ── 设备类型切换 ──
        self.radioAd9910 = QRadioButton("AD9910 DDS")
        self.radioRtmq = QRadioButton("RTMQ 白盒子")
        self.radioAd9910.setChecked(True)

        type_row = QHBoxLayout()
        type_row.addWidget(self.radioAd9910)
        type_row.addWidget(self.radioRtmq)
        type_row.addStretch()
        lay.addLayout(type_row)

        # ── AD9910 专用参数 ──
        self._ad9910_widget = QWidget()
        a1 = QFormLayout(self._ad9910_widget)
        self.editAd9910Sn = QLineEdit("0D11")
        self.editAd9910Sn.setPlaceholderText("如 0D11 (hex)")
        self.cmbAd9910Prof = QComboBox()
        for p in range(8):
            self.cmbAd9910Prof.addItem(f"0x{p:02X}", p)
        a1.addRow("SN (hex)", self.editAd9910Sn)
        a1.addRow("Profile", self.cmbAd9910Prof)

        # ── RTMQ 专用参数 ──
        self._rtmq_widget = QWidget()
        r1 = QFormLayout(self._rtmq_widget)
        self.cmbRtmqCard = QComboBox()
        for c in [1, 2, 3, 4]:
            self.cmbRtmqCard.addItem(f"Card {c}", c)
        self.cmbRtmqSbg = QComboBox()
        for s in [0x00, 0x20, 0x40, 0x60]:
            self.cmbRtmqSbg.addItem(f"0x{s:02X}", s)
        r1.addRow("板卡", self.cmbRtmqCard)
        r1.addRow("SBG 通道", self.cmbRtmqSbg)

        # 堆叠
        self._stack = QStackedWidget()
        self._stack.addWidget(self._ad9910_widget)
        self._stack.addWidget(self._rtmq_widget)

        self.radioAd9910.toggled.connect(
            lambda on: self._stack.setCurrentIndex(0 if on else 1)
        )
        self.radioRtmq.toggled.connect(
            lambda on: self._stack.setCurrentIndex(1 if on else 0)
        )

        lay.addWidget(self._stack)

        # ── PID 参数 ──
        g2 = QGroupBox("PID 参数")
        f2 = QFormLayout(g2)
        self.spinPreset = QDoubleSpinBox()
        self.spinPreset.setRange(-1e6, 1e6); self.spinPreset.setDecimals(4); self.spinPreset.setValue(0.8)
        f2.addRow("目标值", self.spinPreset)

        self.spinKp = QDoubleSpinBox()
        self.spinKp.setRange(-100, 100); self.spinKp.setDecimals(4); self.spinKp.setValue(0.03); self.spinKp.setFixedWidth(100)
        self.spinKi = QDoubleSpinBox()
        self.spinKi.setRange(-100, 100); self.spinKi.setDecimals(4); self.spinKi.setValue(0.0); self.spinKi.setFixedWidth(100)
        self.spinKd = QDoubleSpinBox()
        self.spinKd.setRange(-100, 100); self.spinKd.setDecimals(4); self.spinKd.setValue(0.0); self.spinKd.setFixedWidth(100)
        f2.addRow("Kp", self.spinKp)
        f2.addRow("Ki", self.spinKi)
        f2.addRow("Kd", self.spinKd)

        self.spinILimit = QDoubleSpinBox()
        self.spinILimit.setRange(0, 10); self.spinILimit.setDecimals(3); self.spinILimit.setValue(0.1)
        self.spinOutLimit = QDoubleSpinBox()
        self.spinOutLimit.setRange(0, 10); self.spinOutLimit.setDecimals(3); self.spinOutLimit.setValue(0.1)
        f2.addRow("I 限幅", self.spinILimit)
        f2.addRow("输出限幅", self.spinOutLimit)

        self.spinDeadband = QDoubleSpinBox()
        self.spinDeadband.setRange(0, 10); self.spinDeadband.setDecimals(4); self.spinDeadband.setValue(0.0)
        self.spinDeadband.setSuffix(" (0=禁用)")
        f2.addRow("死区", self.spinDeadband)
        lay.addWidget(g2)

        # ── 订阅 ──
        lay.addWidget(QLabel("订阅测量项 (选一项):"))
        self.subList = QListWidget()
        self.subList.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        for item in self._meas_items:
            self.subList.addItem(f"{item['name']} ({item['channel']}_{item['meas_key']})")
        lay.addWidget(self.subList)

        # ── 按钮 ──
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _load(self, cfg: PidSlotConfig):
        t = cfg.target
        if t is None:
            return
        self.editId.setText(cfg.slot_id)
        self.editLabel.setText(cfg.label)
        self.editIp.setText(t.ip)
        self.editPort.setValue(t.port)
        if isinstance(t, Ad9910Target):
            self.radioAd9910.setChecked(True)
            self.editAd9910Sn.setText(f"{t.device_id:04X}")
            idx = self.cmbAd9910Prof.findData(t.profile)
            if idx >= 0: self.cmbAd9910Prof.setCurrentIndex(idx)
        elif isinstance(t, RtmqTarget):
            self.radioRtmq.setChecked(True)
            idx = self.cmbRtmqCard.findData(t.card_index)
            if idx >= 0: self.cmbRtmqCard.setCurrentIndex(idx)
            idx = self.cmbRtmqSbg.findData(t.sbg_channel)
            if idx >= 0: self.cmbRtmqSbg.setCurrentIndex(idx)

        p = cfg.pid
        self.spinPreset.setValue(p.preset_value)
        self.spinKp.setValue(p.kp); self.spinKi.setValue(p.ki); self.spinKd.setValue(p.kd)
        self.spinILimit.setValue(p.i_limit); self.spinOutLimit.setValue(p.output_limit)
        self.spinDeadband.setValue(p.deadband)

        # 回填订阅
        for i, item in enumerate(self._meas_items):
            key = f"{item['channel']}_{item['meas_key']}"
            if any(s.local_key == key for s in cfg.subscriptions):
                self.subList.item(i).setSelected(True)

    def get_config(self) -> PidSlotConfig:
        """返回填好的 PidSlotConfig。"""
        # 目标
        ip = self.editIp.text()
        port = self.editPort.value()
        if self.radioAd9910.isChecked():
            sn = self.editAd9910Sn.text().strip()
            target = Ad9910Target(
                ip=ip, port=port,
                device_id=int(sn, 16),
                profile=self.cmbAd9910Prof.currentData(),
            )
        else:
            target = RtmqTarget(
                ip=ip, port=port,
                card_index=self.cmbRtmqCard.currentData(),
                sbg_channel=self.cmbRtmqSbg.currentData(),
            )

        # 订阅
        measurement_key = ""
        subs = []
        for idx in range(self.subList.count()):
            if self.subList.item(idx).isSelected() and idx < len(self._meas_items):
                m = self._meas_items[idx]
                measurement_key = f"{m['channel']}_{m['meas_key']}"
                subs.append(DataSubscription(
                    local_key=measurement_key,
                    remote_key=m.get('name', measurement_key),
                ))
                break  # 只取第一个选中项

        return PidSlotConfig(
            slot_id=self.editId.text().strip() or "pid-fb",
            label=self.editLabel.text().strip(),
            subscriptions=subs,
            pid=PidParams(
                preset_value=self.spinPreset.value(),
                kp=self.spinKp.value(),
                ki=self.spinKi.value(),
                kd=self.spinKd.value(),
                i_limit=self.spinILimit.value(),
                output_limit=self.spinOutLimit.value(),
                deadband=self.spinDeadband.value(),
            ),
            measurement_key=measurement_key,
            target=target,
        )
