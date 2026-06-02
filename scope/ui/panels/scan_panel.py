"""
扫频控制面板 — 参数设置 + 下发按钮 + 反馈开关 + 拟合结果显示
"""

from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QGroupBox,
    QLabel,
    QDoubleSpinBox,
    QPushButton,
    QCheckBox,
)

from scope.scan import ScanConfig, ScanState, ScanCoordinator

logger = logging.getLogger(__name__)


class ScanPanel(QWidget):
    """
    扫频控制面板。

    布局:
      ┌─ 扫频参数 ──────────────────────┐
      │  中心频率 / 扫频范围 / 扫频时长  │
      ├─ 控制 ──────────────────────────┤
      │  [下发扫频配置]  [反馈开关]       │
      ├─ 状态 ──────────────────────────┤
      │  状态: IDLE / SCANNING / DONE   │
      ├─ 拟合结果 ──────────────────────┤
      │  f0 / Γ / R²                   │
      └─────────────────────────────────┘
    """

    def __init__(
        self,
        coordinator: ScanCoordinator,
        parent=None,
    ):
        super().__init__(parent)
        self._coordinator = coordinator
        self._build_ui()
        self._update_state_display()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        # ── 扫频参数 ──
        g1 = QGroupBox("扫频参数")
        f1 = QFormLayout(g1)

        cfg = self._coordinator.scan_config

        self.spinBaseFreq = QDoubleSpinBox()
        self.spinBaseFreq.setRange(1.0, 500.0)
        self.spinBaseFreq.setValue(cfg.base_freq)
        self.spinBaseFreq.setSuffix(" MHz")
        self.spinBaseFreq.setDecimals(1)
        f1.addRow("中心频率", self.spinBaseFreq)

        self.spinScanAmp = QDoubleSpinBox()
        self.spinScanAmp.setRange(0.001, 100.0)
        self.spinScanAmp.setValue(cfg.scan_freq_amp)
        self.spinScanAmp.setSuffix(" MHz")
        self.spinScanAmp.setDecimals(3)
        self.spinScanAmp.setSingleStep(0.1)
        f1.addRow("扫频范围", self.spinScanAmp)

        lbl_span = QLabel()
        lbl_span.setStyleSheet("color: #888; font-size: 10px;")
        self._lbl_span = lbl_span
        self._update_span_label()

        dur_row = QHBoxLayout()
        self.spinScanDur = QDoubleSpinBox()
        self.spinScanDur.setRange(1.0, 60_000_000.0)
        self.spinScanDur.setValue(cfg.scan_dur)
        self.spinScanDur.setSuffix(" μs")
        self.spinScanDur.setDecimals(0)
        self.spinScanDur.setSingleStep(100000)
        dur_row.addWidget(self.spinScanDur)
        self._lbl_dur_s = QLabel()
        self._lbl_dur_s.setStyleSheet("color: #888; font-size: 10px;")
        dur_row.addWidget(self._lbl_dur_s)
        self._update_dur_label()
        f1.addRow("扫频时长", dur_row)
        f1.addRow("", lbl_span)

        self.spinBaseFreq.valueChanged.connect(lambda _: self._update_span_label())
        self.spinScanAmp.valueChanged.connect(lambda _: self._update_span_label())
        self.spinScanDur.valueChanged.connect(lambda _: self._update_dur_label())

        layout.addWidget(g1)

        # ── 控制按钮 ──
        ctrl = QHBoxLayout()

        self.btnUpload = QPushButton("🚀 下发扫频配置")
        self.btnUpload.setStyleSheet(
            "QPushButton {"
            "  padding: 8px; font-weight: bold; font-size: 13px;"
            "  background: #2196F3; color: white;"
            "  border: 1px solid #1976D2; border-radius: 4px;"
            "}"
            "QPushButton:hover { background: #42A5F5; }"
            "QPushButton:disabled { background: #555; color: #999; }"
        )
        self.btnUpload.clicked.connect(self._on_upload)
        ctrl.addWidget(self.btnUpload)

        self.chkFeedback = QCheckBox("启用反馈链路")
        self.chkFeedback.setChecked(self._coordinator.feedback_enabled)
        self.chkFeedback.toggled.connect(self._on_feedback_toggle)
        ctrl.addWidget(self.chkFeedback)

        ctrl.addStretch()
        layout.addLayout(ctrl)

        # ── 状态显示 ──
        g2 = QGroupBox("状态")
        s2 = QVBoxLayout(g2)
        self.lblState = QLabel("⏳ 等待下发")
        self.lblState.setStyleSheet(
            "font-weight: bold; font-size: 14px; padding: 4px;"
        )
        s2.addWidget(self.lblState)
        layout.addWidget(g2)

        # ── 拟合结果 ──
        g3 = QGroupBox("最近拟合结果")
        f3 = QFormLayout(g3)

        self.lblF0 = QLabel("—")
        self.lblF0.setStyleSheet("font-weight: bold; font-size: 13px; color: #FFD700;")
        f3.addRow("中心频率 f0", self.lblF0)

        self.lblGamma = QLabel("—")
        f3.addRow("线宽 Γ (HWHM)", self.lblGamma)

        self.lblR2 = QLabel("—")
        f3.addRow("拟合优度 R²", self.lblR2)

        self.lblAmp = QLabel("—")
        f3.addRow("峰值幅度", self.lblAmp)

        layout.addWidget(g3)
        layout.addStretch()

    # ── 内部 ───────────────────────────────────────────────────

    def _update_span_label(self):
        amp = self.spinScanAmp.value()
        base = self.spinBaseFreq.value()
        self._lbl_span.setText(
            f"实际扫描 {base - amp/2:.3f} ~ {base + amp/2:.3f} MHz"
        )

    def _update_dur_label(self):
        dur = self.spinScanDur.value()
        self._lbl_dur_s.setText(f"= {dur/1e6:.3f} s")

    def _on_feedback_toggle(self, checked: bool):
        self._coordinator.feedback_enabled = checked
        logger.info(f"反馈链路: {'启用' if checked else '关闭'}")

    def _on_upload(self):
        """下发扫频配置。"""
        config = ScanConfig(
            base_freq=self.spinBaseFreq.value(),
            scan_freq_amp=self.spinScanAmp.value(),
            scan_dur=self.spinScanDur.value(),
        )

        self.btnUpload.setEnabled(False)
        self.btnUpload.setText("⏳ 下发中...")
        self.lblState.setText("⏳ 下发中...")
        self.lblState.setStyleSheet(
            "font-weight: bold; font-size: 14px; padding: 4px; color: #FFD700;"
        )
        from PyQt6.QtCore import QCoreApplication
        QCoreApplication.processEvents()

        # 在后台执行 (需要 async loop)
        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None:
            loop.create_task(self._do_upload(config))
        else:
            # 同步回退 (调试用)
            self._coordinator.scan_config = config
            if self._coordinator.rtmq_device:
                self._coordinator.rtmq_device.single_card(
                    config.scan_freq_amp, config.base_freq, config.scan_dur
                )
            self._coordinator.state = ScanState.DONE
            self._update_state_display()
            self.btnUpload.setEnabled(True)
            self.btnUpload.setText("🚀 下发扫频配置")

    async def _do_upload(self, config: ScanConfig):
        await self._coordinator.upload_scan(config)
        self.btnUpload.setEnabled(True)
        self.btnUpload.setText("🚀 下发扫频配置")
        self._update_state_display()

    def _update_state_display(self):
        state = self._coordinator.state
        if state == ScanState.IDLE:
            self.lblState.setText("⏳ 等待下发")
            self.lblState.setStyleSheet(
                "font-weight: bold; font-size: 14px; padding: 4px; color: #888;"
            )
        elif state == ScanState.SCANNING:
            self.lblState.setText("🟢 扫频中...")
            self.lblState.setStyleSheet(
                "font-weight: bold; font-size: 14px; padding: 4px; color: #4CAF50;"
            )
        elif state == ScanState.DONE:
            self.lblState.setText("✅ 扫频完成")
            self.lblState.setStyleSheet(
                "font-weight: bold; font-size: 14px; padding: 4px; color: #2196F3;"
            )

    # ── 公开接口 ───────────────────────────────────────────────

    def update_fit_result(self, fit: "ScanFitResult"):
        """更新拟合结果显示。"""
        if fit is None or not fit.is_valid:
            return
        self.lblF0.setText(f"{fit.f0:.6f} MHz")
        self.lblGamma.setText(f"{fit.gamma:.6f} MHz")
        self.lblR2.setText(f"{fit.r_squared:.6f}")
        self.lblAmp.setText(f"{fit.amplitude:.4f} V")

    def get_config(self) -> ScanConfig:
        return ScanConfig(
            base_freq=self.spinBaseFreq.value(),
            scan_freq_amp=self.spinScanAmp.value(),
            scan_dur=self.spinScanDur.value(),
        )

    def mark_scan_done(self):
        """外部通知扫频完成。"""
        self._coordinator.state = ScanState.DONE
        self._update_state_display()
