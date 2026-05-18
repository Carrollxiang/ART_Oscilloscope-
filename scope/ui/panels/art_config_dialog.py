"""
ART 采集卡配置对话框。

配置项映射到 ArtDevice 构造参数和 DeviceConfig 字段。
通过 MainWindow 菜单 "文件 → ART 设备配置" 打开。
"""

from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QGroupBox,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QComboBox,
    QSpinBox,
    QDoubleSpinBox,
    QCheckBox,
    QPushButton,
    QFrame,
)

from scope.hardware import DeviceConfig

logger = logging.getLogger(__name__)


class ArtConfigDialog(QDialog):
    """
    ART 采集卡配置对话框。

    读取配置后通过 get_config() / get_device_params() 获取值。
    """

    # 接地方式选项
    TERMINAL_MODES = [
        ("NRSE", "NRSE (非参考单端)"),
        ("RSE", "RSE (参考单端)"),
        ("DIFFERENTIAL", "Differential (差分)"),
        ("PSEUDODIFFERENTIAL", "Pseudo Differential (伪差分)"),
        ("DEFAULT", "Default (默认)"),
    ]

    # 采样模式
    SAMPLE_MODES = [
        ("FINITE", "有限采集"),
        ("CONTINUOUS", "连续采集"),
    ]

    # 触发斜率
    SLOPES = [
        ("rising", "上升沿"),
        ("falling", "下降沿"),
    ]

    def __init__(self, parent=None, device_params: Optional[dict] = None):
        """
        device_params: 当前 ArtDevice 参数快照, 用于回填。
        """
        super().__init__(parent)
        self.setWindowTitle("ART 设备配置")
        self.setMinimumSize(460, 480)
        self._params = device_params or {}

        self._build_ui()
        self._load_params()

        self.btn_box.accepted.connect(self.accept)
        self.btn_box.rejected.connect(self.reject)

    def _run_test(self):
        """测试硬件通讯: 检查 DLL → 创建 ArtDevice → open → read → 显示结果。"""
        self.btn_test.setEnabled(False)
        self.test_status.setText("⏳ 正在测试...")
        self.test_status.setStyleSheet(
            "padding: 6px; background: #1a1a2e; border: 1px solid #666; "
            "font-family: Consolas, monospace; font-size: 11px; color: yellow;"
        )
        from PyQt6.QtCore import QCoreApplication
        QCoreApplication.processEvents()

        # 预检查 DLL 是否存在
        import ctypes, os
        dll_checked = False
        try:
            ctypes.windll.LoadLibrary("Art_DAQ")
            dll_checked = True
        except Exception:
            dll_checked = False

        try:
            params = self.get_device_params()
            cfg = self.get_device_config()

            if not dll_checked:
                raise RuntimeError(
                    "未找到 Art_DAQ.dll — 请确认 ART 硬件驱动已安装。\n"
                    "如果驱动已安装, 尝试将 Art_DAQ.dll 所在目录加入 PATH。"
                )

            from scope.hardware.art_device import ArtDevice
            dev = ArtDevice(
                device_name=params["device_name"],
                ai_channels=params["ai_channels"],
                terminal_config=params["terminal_config"],
                min_val=params["min_val"],
                max_val=params["max_val"],
                trigger_source=params["trigger_source"],
                trigger_slope=params["trigger_slope"],
                trigger_level=params["trigger_level"],
            )
            dev._read_timeout = params["read_timeout"]

            lines = [f"🟡 设备: {params['device_name']}/{params['ai_channels']}"]

            ok = dev.open()
            if not ok:
                raise RuntimeError("open() 返回 False")
            lines.append("✅ 1. open()          成功 — 模块加载正常")

            dev.configure(cfg)
            lines.append(f"✅ 2. configure()     成功 — {cfg.sample_rate} Sa/s, {cfg.record_length}samples")

            dev.start_acquisition()
            lines.append("✅ 3. start_acquisition()  成功 — 开始采集")

            chunk = dev.read_chunk()
            ch, samples = chunk.shape
            lines.append(f"✅ 4. read_chunk()     成功 — {ch}ch × {samples}samples, {chunk.dtype}")

            dev.stop_acquisition()
            dev.close()
            lines.append("✅ 5. stop/close       成功 — 资源已释放")

            self.test_status.setText("\n".join(lines))
            self.test_status.setStyleSheet(
                "padding: 6px; background: #0a1a0a; border: 1px solid #0a0; "
                "font-family: Consolas, monospace; font-size: 11px; color: #0f0;"
            )

        except Exception as e:
            err_lines = [f"❌ {type(e).__name__}: {e}"]
            self.test_status.setText("\n".join(err_lines))
            self.test_status.setStyleSheet(
                "padding: 6px; background: #1a0a0a; border: 1px solid #a00; "
                "font-family: Consolas, monospace; font-size: 11px; color: #f44;"
            )

        finally:
            self.btn_test.setEnabled(True)

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # ── 设备标识 ──
        grp_device = QGroupBox("设备标识")
        dev_layout = QFormLayout(grp_device)
        self.editDeviceName = QLineEdit()
        self.editAiChannels = QLineEdit()
        dev_layout.addRow("设备名", self.editDeviceName)
        dev_layout.addRow("AI 通道", self.editAiChannels)
        layout.addWidget(grp_device)

        # ── 模拟输入 ──
        grp_ai = QGroupBox("模拟输入")
        ai_layout = QFormLayout(grp_ai)

        self.cmbTerminal = QComboBox()
        for code, label in self.TERMINAL_MODES:
            self.cmbTerminal.addItem(label, code)
        ai_layout.addRow("接地方式", self.cmbTerminal)

        # 电压量程
        range_layout = QHBoxLayout()
        self.spinMinVal = QDoubleSpinBox()
        self.spinMinVal.setRange(-100, 0)
        self.spinMinVal.setValue(-10.0)
        self.spinMinVal.setSuffix(" V")
        self.spinMaxVal = QDoubleSpinBox()
        self.spinMaxVal.setRange(0, 100)
        self.spinMaxVal.setValue(10.0)
        self.spinMaxVal.setSuffix(" V")
        range_layout.addWidget(QLabel("最小"))
        range_layout.addWidget(self.spinMinVal)
        range_layout.addWidget(QLabel("最大"))
        range_layout.addWidget(self.spinMaxVal)
        ai_layout.addRow("电压量程", range_layout)

        # 读取超时
        self.spinTimeout = QDoubleSpinBox()
        self.spinTimeout.setRange(0.1, 60.0)
        self.spinTimeout.setValue(5.0)
        self.spinTimeout.setSuffix(" 秒")
        self.spinTimeout.setSingleStep(0.5)
        ai_layout.addRow("读取超时", self.spinTimeout)

        # 采样率
        self.spinSampleRate = QSpinBox()
        self.spinSampleRate.setRange(100, 250_000)
        self.spinSampleRate.setValue(10_000)
        self.spinSampleRate.setSuffix(" Sa/s")
        self.spinSampleRate.setSingleStep(1000)
        ai_layout.addRow("采样率", self.spinSampleRate)

        # 采样时长 (秒)
        dur_layout = QHBoxLayout()
        self.spinDuration = QDoubleSpinBox()
        self.spinDuration.setRange(0.001, 60.0)
        self.spinDuration.setValue(0.5)
        self.spinDuration.setSuffix(" 秒")
        self.spinDuration.setDecimals(3)
        self.spinDuration.setSingleStep(0.1)
        self.sampleCountLabel = QLabel("= 5000 样本")
        self.spinDuration.valueChanged.connect(self._update_sample_count)
        dur_layout.addWidget(self.spinDuration)
        dur_layout.addWidget(self.sampleCountLabel)
        dur_layout.addStretch()
        ai_layout.addRow("采样时长", dur_layout)

        # 采样模式
        self.cmbSampleMode = QComboBox()
        for code, label in self.SAMPLE_MODES:
            self.cmbSampleMode.addItem(label, code)
        ai_layout.addRow("采样模式", self.cmbSampleMode)

        layout.addWidget(grp_ai)

        # ── 触发 ──
        grp_trig = QGroupBox("硬件触发")
        trig_layout = QFormLayout(grp_trig)

        self.chkEnableTrigger = QCheckBox("启用硬件触发")
        trig_layout.addRow("", self.chkEnableTrigger)

        self.editTriggerSource = QLineEdit()
        self.editTriggerSource.setPlaceholderText("如 ai1 或留空")
        trig_layout.addRow("触发源 (通道)", self.editTriggerSource)

        self.cmbTriggerSlope = QComboBox()
        for code, label in self.SLOPES:
            self.cmbTriggerSlope.addItem(label, code)
        trig_layout.addRow("触发斜率", self.cmbTriggerSlope)

        self.spinTriggerLevel = QDoubleSpinBox()
        self.spinTriggerLevel.setRange(-10, 10)
        self.spinTriggerLevel.setValue(0.0)
        self.spinTriggerLevel.setSuffix(" V")
        self.spinTriggerLevel.setSingleStep(0.1)
        trig_layout.addRow("触发电平", self.spinTriggerLevel)

        layout.addWidget(grp_trig)

        # ── 硬件通讯测试 ──
        test_group = QGroupBox("通讯测试")
        test_layout = QVBoxLayout(test_group)

        self.btn_test = QPushButton("🧪 测试硬件通讯")
        self.btn_test.setStyleSheet(
            "QPushButton { padding: 6px; font-weight: bold; }"
            "QPushButton:hover { background: #224466; }"
        )
        self.btn_test.clicked.connect(self._run_test)
        test_layout.addWidget(self.btn_test)

        self.test_status = QLabel("就绪 — 点击测试硬件连接")
        self.test_status.setWordWrap(True)
        self.test_status.setStyleSheet(
            "padding: 6px; background: #1a1a2e; border: 1px solid #333; "
            "font-family: Consolas, monospace; font-size: 11px;"
        )
        test_layout.addWidget(self.test_status)

        layout.addWidget(test_group)

        # ── 确定/取消 ──
        self.btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        self.btn_box.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        self.btn_box.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        layout.addWidget(self.btn_box)

    def _load_params(self):
        """用现有参数回填 UI。"""
        p = self._params
        self.editDeviceName.setText(p.get("device_name", "Dev42"))
        self.editAiChannels.setText(p.get("ai_channels", "ai0:15"))

        term = p.get("terminal_config", "NRSE")
        for i in range(self.cmbTerminal.count()):
            if self.cmbTerminal.itemData(i) == term:
                self.cmbTerminal.setCurrentIndex(i)
                break

        self.spinMinVal.setValue(p.get("min_val", -10.0))
        self.spinMaxVal.setValue(p.get("max_val", 10.0))
        self.spinTimeout.setValue(p.get("read_timeout", 5.0))
        self.spinSampleRate.setValue(p.get("sample_rate", 10_000))

        dur = p.get("duration", 0.5)
        self.spinDuration.setValue(dur)
        self._update_sample_count()

        mode = p.get("sample_mode", "FINITE")
        for i in range(self.cmbSampleMode.count()):
            if self.cmbSampleMode.itemData(i) == mode:
                self.cmbSampleMode.setCurrentIndex(i)
                break

        trig_src = p.get("trigger_source", "")
        if trig_src:
            self.chkEnableTrigger.setChecked(True)
            self.editTriggerSource.setText(trig_src)
        else:
            self.chkEnableTrigger.setChecked(False)
            self.editTriggerSource.setEnabled(False)
        self.chkEnableTrigger.toggled.connect(
            lambda checked: self.editTriggerSource.setEnabled(checked)
        )

        slope = p.get("trigger_slope", "rising")
        for i in range(self.cmbTriggerSlope.count()):
            if self.cmbTriggerSlope.itemData(i) == slope:
                self.cmbTriggerSlope.setCurrentIndex(i)
                break

        self.spinTriggerLevel.setValue(p.get("trigger_level", 0.0))

    def _update_sample_count(self):
        """更新采样时长 → 样本数显示。"""
        rate = self.spinSampleRate.value()
        dur = self.spinDuration.value()
        samples = int(rate * dur)
        self.sampleCountLabel.setText(f"= {samples} 样本")

    # ── 读取配置 ───────────────────────────────────────────────

    def get_device_params(self) -> dict:
        """返回 ArtDevice 构造参数。"""
        return {
            "device_name": self.editDeviceName.text(),
            "ai_channels": self.editAiChannels.text(),
            "terminal_config": self.cmbTerminal.currentData(),
            "min_val": self.spinMinVal.value(),
            "max_val": self.spinMaxVal.value(),
            "read_timeout": self.spinTimeout.value(),
            "trigger_source": self.editTriggerSource.text()
                              if self.chkEnableTrigger.isChecked() else "",
            "trigger_slope": self.cmbTriggerSlope.currentData(),
            "trigger_level": self.spinTriggerLevel.value(),
        }

    def get_device_config(self) -> DeviceConfig:
        """返回 DeviceConfig (采样率 + 记录长度)。"""
        rate = self.spinSampleRate.value()
        dur = self.spinDuration.value()
        samples = int(rate * dur)
        return DeviceConfig(
            sample_rate=rate,
            record_length=max(samples, 10),
            channels_enabled=list(
                range(self._parse_channel_count())
            ),
        )

    def get_sample_mode(self) -> str:
        return self.cmbSampleMode.currentData()

    def _parse_channel_count(self) -> int:
        """从 ai_channels 字符串解析通道数, 如 "ai0:15" → 16。"""
        ch_str = self.editAiChannels.text()
        if ":" in ch_str:
            parts = ch_str.split(":")[-1]
            try:
                end = int(parts)
                start_str = ch_str.split(":")[0]
                start = int(''.join(c for c in start_str if c.isdigit()) or 0)
                return end - start + 1
            except ValueError:
                pass
        return 4
