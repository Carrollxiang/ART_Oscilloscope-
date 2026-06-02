"""
设备设置面板 — STM32 串口配置

包含串口参数 + 通讯测试 + 应用按钮
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
    QPushButton,
)

from scope.hardware import DeviceConfig

logger = logging.getLogger(__name__)

# 常用波特率
BAUD_RATES = [9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600]


class DevicePanel(QWidget):
    """
    STM32 串口设备设置面板。

    配置项: COM 口 + 波特率 + 通讯测试。
    配置确认后发射 stm32_config_applied 信号。
    """

    stm32_config_applied = pyqtSignal(dict, object)  # (params, DeviceConfig)

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
        grid = QHBoxLayout(container)
        grid.setSpacing(6)

        c1 = QVBoxLayout()
        c2 = QVBoxLayout()
        c3 = QVBoxLayout()

        # ── 列1: 串口设置 ──
        g1 = QGroupBox("串口设置")
        f1 = QFormLayout(g1)
        self.editPort = QLineEdit("COM11")
        self.editPort.setPlaceholderText("如 COM11 / /dev/ttyUSB0")
        f1.addRow("COM 口", self.editPort)

        self.cmbBaudrate = QComboBox()
        for rate in BAUD_RATES:
            self.cmbBaudrate.addItem(str(rate), rate)
        self.cmbBaudrate.setCurrentText("115200")
        self.cmbBaudrate.setEditable(True)
        f1.addRow("波特率", self.cmbBaudrate)
        c1.addWidget(g1)
        c1.addStretch()

        # ── 列2: 采集参数 ──
        g2 = QGroupBox("采集参数")
        f2 = QFormLayout(g2)
        self.spinSampleRate = QSpinBox()
        self.spinSampleRate.setRange(1, 10000)
        self.spinSampleRate.setValue(149)
        self.spinSampleRate.setSuffix(" Sa/s")
        f2.addRow("采样率", self.spinSampleRate)
        self.spinBufSize = QSpinBox()
        self.spinBufSize.setRange(10, 10000)
        self.spinBufSize.setValue(450)
        self.spinBufSize.setSuffix(" 点")
        f2.addRow("缓存长度", self.spinBufSize)
        dur_row = QHBoxLayout()
        self._lblDur = QLabel("≈ 3.0 s")
        self._lblDur.setStyleSheet("color: #888; font-size: 10px;")
        dur_row.addWidget(self._lblDur)
        dur_row.addStretch()
        f2.addRow("", dur_row)
        self.spinSampleRate.valueChanged.connect(self._update_dur)
        self.spinBufSize.valueChanged.connect(self._update_dur)
        lblMode = QLabel("门控触发 (CH1 电平)")
        lblMode.setStyleSheet("color: #888;")
        f2.addRow("触发模式", lblMode)
        c2.addWidget(g2)
        c2.addStretch()

        # ── 列3: 通讯测试 ──
        g4 = QGroupBox("通讯测试")
        t4 = QVBoxLayout(g4)
        self.btnTest = QPushButton("🧪 测试串口通讯")
        self.btnTest.clicked.connect(self._run_test)
        t4.addWidget(self.btnTest)
        self.testStatus = QLabel("就绪")
        self.testStatus.setWordWrap(True)
        self.testStatus.setStyleSheet(
            "padding: 4px; background: #1a1a2e; border: 1px solid #333; "
            "font-family: Consolas; font-size: 11px;")
        t4.addWidget(self.testStatus)
        c3.addWidget(g4)
        c3.addStretch()

        grid.addLayout(c1)
        grid.addLayout(c2)
        grid.addLayout(c3)

        scroll.setWidget(container)
        layout.addWidget(scroll)

        # ── 应用按钮 (固定底部) ──
        self.btnApply = QPushButton("✅ 应用配置到设备")
        self.btnApply.setStyleSheet(
            "QPushButton {"
            "  padding: 8px; font-weight: bold; font-size: 13px;"
            "  background: #4CAF50; color: white;"
            "  border: 1px solid #388E3C; border-radius: 4px;"
            "}"
            "QPushButton:hover { background: #66BB6A; }"
            "QPushButton:pressed { background: #388E3C; }"
        )
        self.btnApply.clicked.connect(self._apply)
        layout.addWidget(self.btnApply)

        self._update_dur()

    # ── 内部 ───────────────────────────────────────────────────

    def _run_test(self):
        """测试串口通讯。"""
        self.btnTest.setEnabled(False)
        self.testStatus.setText("⏳ 测试中...")
        self.testStatus.setStyleSheet(
            "padding:4px;background:#1a1a2e;border:1px solid #666;"
            "font-family:Consolas;font-size:11px;color:yellow;")
        from PyQt6.QtCore import QCoreApplication
        QCoreApplication.processEvents()

        port = self.editPort.text().strip()

        try:
            import serial
        except ImportError:
            self.testStatus.setText("❌ pyserial 未安装 — 请执行: pip install pyserial")
            self.testStatus.setStyleSheet(
                "padding:4px;background:#1a0a0a;border:1px solid #a00;"
                "font-family:Consolas;font-size:11px;color:#f44;")
            self.btnTest.setEnabled(True)
            return

        try:
            baudrate = int(self.cmbBaudrate.currentText())
            ser = serial.Serial(port=port, baudrate=baudrate, timeout=0.1)

            lines = [
                f"🟡 串口: {port} @ {baudrate}",
                "✅ 串口已打开",
            ]

            # 尝试读取几行看是否有数据
            import time
            start = time.time()
            data_lines = 0
            while time.time() - start < 2.0:
                line = ser.readline()
                if line:
                    data_lines += 1
                    if data_lines <= 3:
                        lines.append(f"📥 {line.decode('utf-8', errors='replace').strip()[:60]}")
                if data_lines >= 5:
                    break

            if data_lines > 0:
                lines.append(f"✅ 收到 {data_lines} 行数据")
            else:
                lines.append("⚠️ 2秒内未收到数据 (可能触发信号未激活)")

            ser.close()
            lines.append("✅ 串口已关闭")

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
        """发射 stm32_config_applied 信号。"""
        self.stm32_config_applied.emit(self.get_params(), self.get_config())

    def _update_dur(self):
        rate = self.spinSampleRate.value()
        buf = self.spinBufSize.value()
        if rate > 0:
            self._lblDur.setText(f"≈ {buf/rate:.1f} s")

    # ── 公开接口 ───────────────────────────────────────────────

    def get_params(self) -> dict:
        return {
            "port": self.editPort.text().strip(),
            "baudrate": int(self.cmbBaudrate.currentText()),
            "sample_rate": self.spinSampleRate.value(),
            "record_length": self.spinBufSize.value(),
        }

    def get_config(self) -> DeviceConfig:
        return DeviceConfig(
            sample_rate=self.spinSampleRate.value(),
            record_length=self.spinBufSize.value(),
            channels_enabled=[0],
            channel_min_vals=[0.0],
            channel_max_vals=[1.0],
        )

    def load_params(self, params: dict):
        """从现有设备参数回填。"""
        self.editPort.setText(params.get("port", "COM11"))
        baud = str(params.get("baudrate", 115200))
        self.cmbBaudrate.setCurrentText(baud)
        if "sample_rate" in params:
            self.spinSampleRate.setValue(params["sample_rate"])
        if "record_length" in params:
            self.spinBufSize.setValue(params["record_length"])
        self._update_dur()
