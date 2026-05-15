"""
设备设置面板 — 替换"触发"Tab

包含 ART 采集卡所有配置项 + 通讯测试 + 应用按钮
(由原来的 ArtConfigDialog 改为内嵌 QWidget)
"""

from __future__ import annotations

import logging
from typing import Optional, Callable

from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QGroupBox,
    QScrollArea,
    QLabel,
    QLineEdit,
    QComboBox,
    QSpinBox,
    QDoubleSpinBox,
    QCheckBox,
    QPushButton,
)

from scope.hardware import DeviceConfig

logger = logging.getLogger(__name__)


class DevicePanel(QWidget):
    """
    设备设置面板 (替换原来的"触发"Tab)。

    所有 ART 采集卡配置在此完成 + 通讯测试按钮。
    配置确认后发射 config_applied 信号。
    """

    config_applied = pyqtSignal(dict, object)  # (params, DeviceConfig)

    TERMINAL_MODES = [
        ("NRSE", "NRSE"),
        ("RSE", "RSE"),
        ("DIFFERENTIAL", "Differential"),
        ("PSEUDODIFFERENTIAL", "Pseudo Diff"),
        ("DEFAULT", "Default"),
    ]
    SAMPLE_MODES = [("FINITE", "有限"), ("CONTINUOUS", "连续")]
    SLOPES = [("rising", "上升沿"), ("falling", "下降沿")]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border: none; }")

        container = QWidget()
        form = QVBoxLayout(container)
        form.setSpacing(6)

        # ── 设备标识 ──
        g1 = QGroupBox("设备")
        f1 = QFormLayout(g1)
        self.editDeviceName = QLineEdit("Dev42")
        self.editAiChannels = QLineEdit("ai0:3")
        f1.addRow("设备名", self.editDeviceName)
        f1.addRow("AI 通道", self.editAiChannels)
        form.addWidget(g1)

        # ── 采集参数 ──
        g2 = QGroupBox("采集参数")
        f2 = QFormLayout(g2)

        self.cmbTerminal = QComboBox()
        for code, label in self.TERMINAL_MODES:
            self.cmbTerminal.addItem(label, code)
        f2.addRow("接地方式", self.cmbTerminal)

        vr = QHBoxLayout()
        self.spinMinVal = QDoubleSpinBox()
        self.spinMinVal.setRange(-100, 0)
        self.spinMinVal.setValue(-10.0)
        self.spinMinVal.setSuffix(" V")
        self.spinMaxVal = QDoubleSpinBox()
        self.spinMaxVal.setRange(0, 100)
        self.spinMaxVal.setValue(10.0)
        self.spinMaxVal.setSuffix(" V")
        vr.addWidget(self.spinMinVal)
        vr.addWidget(QLabel("~"))
        vr.addWidget(self.spinMaxVal)
        f2.addRow("电压量程", vr)

        self.spinTimeout = QDoubleSpinBox()
        self.spinTimeout.setRange(0.1, 60.0)
        self.spinTimeout.setValue(5.0)
        self.spinTimeout.setSuffix(" s")
        f2.addRow("读取超时", self.spinTimeout)

        self.spinSampleRate = QSpinBox()
        self.spinSampleRate.setRange(100, 250_000)
        self.spinSampleRate.setValue(10_000)
        self.spinSampleRate.setSuffix(" Sa/s")
        f2.addRow("采样率", self.spinSampleRate)

        dur = QHBoxLayout()
        self.spinDuration = QDoubleSpinBox()
        self.spinDuration.setRange(0.001, 60.0)
        self.spinDuration.setValue(0.5)
        self.spinDuration.setSuffix(" s")
        self.spinDuration.setDecimals(3)
        self.spinDuration.setSingleStep(0.1)
        self.lblSamples = QLabel("= 5000 样本")
        self.spinDuration.valueChanged.connect(self._update_samples)
        dur.addWidget(self.spinDuration)
        dur.addWidget(self.lblSamples)
        f2.addRow("采样时长", dur)

        self.cmbSampleMode = QComboBox()
        for code, label in self.SAMPLE_MODES:
            self.cmbSampleMode.addItem(label, code)
        f2.addRow("采样模式", self.cmbSampleMode)

        form.addWidget(g2)

        # ── 硬件触发 ──
        g3 = QGroupBox("硬件触发")
        f3 = QFormLayout(g3)

        self.chkTrig = QCheckBox("启用")
        f3.addRow("", self.chkTrig)

        self.editTrigSrc = QLineEdit()
        self.editTrigSrc.setPlaceholderText("如 ai1")
        f3.addRow("触发源", self.editTrigSrc)

        self.cmbTrigSlope = QComboBox()
        for code, label in self.SLOPES:
            self.cmbTrigSlope.addItem(label, code)
        f3.addRow("斜率", self.cmbTrigSlope)

        self.spinTrigLevel = QDoubleSpinBox()
        self.spinTrigLevel.setRange(-10, 10)
        self.spinTrigLevel.setValue(0.0)
        self.spinTrigLevel.setSuffix(" V")
        f3.addRow("电平", self.spinTrigLevel)

        self.chkTrig.toggled.connect(
            lambda on: (self.editTrigSrc.setEnabled(on),
                        self.cmbTrigSlope.setEnabled(on),
                        self.spinTrigLevel.setEnabled(on))
        )
        self.chkTrig.setChecked(False)
        self.editTrigSrc.setEnabled(False)
        self.cmbTrigSlope.setEnabled(False)
        self.spinTrigLevel.setEnabled(False)

        form.addWidget(g3)

        # ── 通讯测试 ──
        g4 = QGroupBox("通讯测试")
        t4 = QVBoxLayout(g4)
        self.btnTest = QPushButton("🧪 测试硬件通讯")
        self.btnTest.clicked.connect(self._run_test)
        t4.addWidget(self.btnTest)

        self.testStatus = QLabel("就绪")
        self.testStatus.setWordWrap(True)
        self.testStatus.setStyleSheet(
            "padding: 4px; background: #1a1a2e; border: 1px solid #333; "
            "font-family: Consolas; font-size: 11px;")
        t4.addWidget(self.testStatus)
        form.addWidget(g4)

        # ── 应用按钮 ──
        self.btnApply = QPushButton("✅ 应用配置到设备")
        self.btnApply.setStyleSheet(
            "QPushButton { padding: 6px; font-weight: bold; "
            "background: #224422; border: 1px solid #484; }"
            "QPushButton:hover { background: #336633; }"
        )
        self.btnApply.clicked.connect(self._apply)
        form.addWidget(self.btnApply)

        scroll.setWidget(container)
        layout.addWidget(scroll)

        self._update_samples()

    # ── 内部 ───────────────────────────────────────────────────

    def _update_samples(self):
        rate = self.spinSampleRate.value()
        dur = self.spinDuration.value()
        self.lblSamples.setText(f"= {int(rate * dur)} 样本")

    def _run_test(self):
        """DLL 预检 + ArtDevice 全链路测试。"""
        self.btnTest.setEnabled(False)
        self.testStatus.setText("⏳ 测试中...")
        self.testStatus.setStyleSheet(
            "padding:4px;background:#1a1a2e;border:1px solid #666;"
            "font-family:Consolas;font-size:11px;color:yellow;")
        from PyQt6.QtCore import QCoreApplication
        QCoreApplication.processEvents()

        import ctypes
        try:
            ctypes.windll.LoadLibrary("Art_DAQ")
        except Exception:
            self.testStatus.setText(
                "❌ 未找到 Art_DAQ.dll — 请安装 ART 驱动")
            self.testStatus.setStyleSheet(
                "padding:4px;background:#1a0a0a;border:1px solid #a00;"
                "font-family:Consolas;font-size:11px;color:#f44;")
            self.btnTest.setEnabled(True)
            return

        try:
            p = self.get_params()
            cfg = self.get_config()
            from scope.hardware.art_device import ArtDevice
            dev = ArtDevice(**p)
            dev._read_timeout = p["read_timeout"]

            lines = [f"🟡 设备: {p['device_name']}/{p['ai_channels']}"]
            dev.open()
            lines.append("✅ open()")
            dev.configure(cfg)
            lines.append("✅ configure()")
            dev.start_acquisition()
            lines.append("✅ start_acquisition()")
            chunk = dev.read_chunk()
            lines.append(f"✅ read_chunk()  {chunk.shape[0]}ch×{chunk.shape[1]}samples")
            dev.stop_acquisition()
            dev.close()
            lines.append("✅ stop/close")

            self.testStatus.setText("\n".join(lines))
            self.testStatus.setStyleSheet(
                "padding:4px;background:#0a1a0a;border:1px solid #0a0;"
                "font-family:Consolas;font-size:11px;color:#0f0;")
        except Exception as e:
            self.testStatus.setText(f"❌ {e}")
            self.testStatus.setStyleSheet(
                "padding:4px;background:#1a0a0a;border:1px solid #a00;"
                "font-family:Consolas;font-size:11px;color:#f44;")
        finally:
            self.btnTest.setEnabled(True)

    def _apply(self):
        """发射 config_applied 信号。"""
        self.config_applied.emit(self.get_params(), self.get_config())

    # ── 公开接口 ───────────────────────────────────────────────

    def get_params(self) -> dict:
        return {
            "device_name": self.editDeviceName.text(),
            "ai_channels": self.editAiChannels.text(),
            "terminal_config": self.cmbTerminal.currentData(),
            "min_val": self.spinMinVal.value(),
            "max_val": self.spinMaxVal.value(),
            "read_timeout": self.spinTimeout.value(),
            "trigger_source": self.editTrigSrc.text()
                              if self.chkTrig.isChecked() else "",
            "trigger_slope": self.cmbTrigSlope.currentData(),
            "trigger_level": self.spinTrigLevel.value(),
        }

    def get_config(self) -> DeviceConfig:
        rate = self.spinSampleRate.value()
        samples = int(rate * self.spinDuration.value())
        return DeviceConfig(
            sample_rate=rate,
            record_length=max(samples, 10),
            channels_enabled=[0, 1, 2, 3],
        )

    def load_params(self, params: dict):
        """从现有设备参数回填。"""
        self.editDeviceName.setText(params.get("device_name", "Dev42"))
        self.editAiChannels.setText(params.get("ai_channels", "ai0:3"))

        term = params.get("terminal_config", "NRSE")
        for i in range(self.cmbTerminal.count()):
            if self.cmbTerminal.itemData(i) == term:
                self.cmbTerminal.setCurrentIndex(i)
                break

        self.spinMinVal.setValue(params.get("min_val", -10.0))
        self.spinMaxVal.setValue(params.get("max_val", 10.0))
        self.spinTimeout.setValue(params.get("read_timeout", 5.0))
        self.spinSampleRate.setValue(params.get("sample_rate", 10_000))
        self.spinDuration.setValue(params.get("duration", 0.5))
        self._update_samples()

        src = params.get("trigger_source", "")
        if src:
            self.chkTrig.setChecked(True)
            self.editTrigSrc.setText(src)
        else:
            self.chkTrig.setChecked(False)
