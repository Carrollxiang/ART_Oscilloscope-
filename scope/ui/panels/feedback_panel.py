"""
反馈面板 — 管理反馈 Worker 的 UI 组件

提供可折叠的 Worker 卡片，显示运行状态和监控数据。
"""

from __future__ import annotations

import logging
from typing import Optional, Callable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QDialog,
    QLineEdit,
    QComboBox,
    QDoubleSpinBox,
    QSpinBox,
    QDialogButtonBox,
    QFrame,
    QSizePolicy,
)

from scope.runtime.pid_controller import PidConfig
from scope.runtime import FeedbackStatusSnapshot, FeedbackWorkerStatus
from scope.io.feedback_command import FeedbackCommand
from scope.io.feedback_worker import Ad9910Target, FeedbackConfig, RtmqTarget

logger = logging.getLogger(__name__)

# ── 状态灯颜色 ──────────────────────────────────────────────────

STATUS_COLORS = {
    "running_green": "#00CC00",   # 目标范围内
    "running_red": "#CC0000",     # 超出范围
    "paused": "#888888",          # 暂停
    "idle": "#444444",            # 关闭
}


def _fmt(val: Optional[float], decimals: int = 4) -> str:
    """格式化浮点数值"""
    if val is None:
        return "—"
    return f"{val:.{decimals}f}"


# ═══════════════════════════════════════════════════════════════
# WorkerCard — 可折叠卡片
# ═══════════════════════════════════════════════════════════════

class WorkerCard(QFrame):
    """
    单个反馈 Worker 卡片。

    折叠态: [状态灯] [名称] [目标值] [实际值] [标准差]
    展开态: + PID 参数 + [暂停/恢复] [移除] 按钮
    """

    pause_clicked = pyqtSignal(str)     # worker_id
    resume_clicked = pyqtSignal(str)    # worker_id
    remove_clicked = pyqtSignal(str)    # worker_id
    edit_clicked = pyqtSignal(str)      # worker_id

    def __init__(self, worker_id: str, parent=None):
        super().__init__(parent)
        self._worker_id = worker_id
        self._expanded = False

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet("""
            WorkerCard {
                border: 1px solid #333;
                border-radius: 4px;
                padding: 0px;
                margin: 1px 0px;
            }
            WorkerCard:hover {
                border-color: #555;
            }
        """)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        # 主布局
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(6, 4, 6, 4)
        self._layout.setSpacing(4)

        # ── 折叠行（点击切换） ──
        self._header = QWidget()
        self._header.setCursor(Qt.CursorShape.PointingHandCursor)
        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)

        # 状态灯
        self._status_light = QLabel("●")
        self._status_light.setFixedWidth(16)
        self._status_light.setStyleSheet(f"color: {STATUS_COLORS['idle']}; font-size: 14px;")
        header_layout.addWidget(self._status_light)

        # ID + 测量项
        self._name_label = QLabel(worker_id)
        self._name_label.setStyleSheet("color: #111; font-weight: bold; font-size: 12px;")
        self._name_label.setMinimumWidth(140)
        header_layout.addWidget(self._name_label)

        # 测量项
        self._meas_label = QLabel("")
        self._meas_label.setStyleSheet("color: #111; font-size: 11px;")
        self._meas_label.setMinimumWidth(80)
        header_layout.addWidget(self._meas_label)

        # 链路标签
        self._chain_label = QLabel("")
        self._chain_label.setStyleSheet("color: #5577AA; font-size: 10px; font-weight: bold;")
        self._chain_label.setMinimumWidth(60)
        header_layout.addWidget(self._chain_label)

        # 目标值
        header_layout.addWidget(QLabel("目标:"))
        self._preset_label = QLabel("—")
        self._preset_label.setStyleSheet("color: #111; font-weight: bold;")
        self._preset_label.setMinimumWidth(60)
        header_layout.addWidget(self._preset_label)

        # 实际值
        header_layout.addWidget(QLabel("实际:"))
        self._value_label = QLabel("—")
        self._value_label.setStyleSheet("color: #111; font-weight: bold; font-size: 12px;")
        self._value_label.setMinimumWidth(70)
        header_layout.addWidget(self._value_label)

        # 标准差
        header_layout.addWidget(QLabel("σ:"))
        self._std_label = QLabel("—")
        self._std_label.setStyleSheet("color: #111; font-size: 11px;")
        self._std_label.setMinimumWidth(50)
        header_layout.addWidget(self._std_label)

        # 展开指示器
        self._expand_icon = QLabel("▼")
        self._expand_icon.setStyleSheet("color: #666; font-size: 10px;")
        header_layout.addWidget(self._expand_icon)
        header_layout.addStretch()

        self._header.mousePressEvent = self._toggle_expand
        self._layout.addWidget(self._header)

        # ── 展开区域 ──
        self._body = QWidget()
        self._body.setVisible(False)
        body_layout = QVBoxLayout(self._body)
        body_layout.setContentsMargins(22, 4, 0, 4)
        body_layout.setSpacing(4)

        # PID 参数行
        pid_row = QHBoxLayout()
        pid_row.setSpacing(12)
        self._kp_label = QLabel("Kp: —")
        self._kp_label.setStyleSheet("color: #999; font-size: 11px;")
        pid_row.addWidget(self._kp_label)
        self._ki_label = QLabel("Ki: —")
        self._ki_label.setStyleSheet("color: #999; font-size: 11px;")
        pid_row.addWidget(self._ki_label)
        self._kd_label = QLabel("Kd: —")
        self._kd_label.setStyleSheet("color: #999; font-size: 11px;")
        pid_row.addWidget(self._kd_label)
        self._window_label = QLabel("窗口: —")
        self._window_label.setStyleSheet("color: #999; font-size: 11px;")
        pid_row.addWidget(self._window_label)
        self._deadband_label = QLabel("死区: —")
        self._deadband_label.setStyleSheet("color: #999; font-size: 11px;")
        pid_row.addWidget(self._deadband_label)
        self._frames_label = QLabel("帧: 0")
        self._frames_label.setStyleSheet("color: #666; font-size: 11px;")
        pid_row.addStretch()
        pid_row.addWidget(self._frames_label)
        body_layout.addLayout(pid_row)

        # sender 错误提示
        self._sender_error_label = QLabel("")
        self._sender_error_label.setStyleSheet("color: #CC5555; font-size: 10px; font-style: italic;")
        self._sender_error_label.setVisible(False)
        body_layout.addWidget(self._sender_error_label)

        # 自动暂停原因提示 (误差趋势变差等)
        self._stop_reason_label = QLabel("")
        self._stop_reason_label.setStyleSheet("color: #FFA500; font-size: 10px; font-style: italic;")
        self._stop_reason_label.setWordWrap(True)
        self._stop_reason_label.setVisible(False)
        body_layout.addWidget(self._stop_reason_label)

        # 按钮行
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        self._pause_btn = QPushButton("⏸ 暂停")
        self._pause_btn.setFixedHeight(22)
        self._pause_btn.setStyleSheet(
            "QPushButton { color: #FFA500; border: 1px solid #664400; "
            "padding: 2px 8px; font-size: 11px; border-radius: 3px; }"
            "QPushButton:hover { background: #332200; }"
        )
        self._pause_btn.clicked.connect(lambda: self.pause_clicked.emit(self._worker_id))
        btn_row.addWidget(self._pause_btn)

        self._resume_btn = QPushButton("▶ 恢复")
        self._resume_btn.setFixedHeight(22)
        self._resume_btn.setStyleSheet(
            "QPushButton { color: #00CC00; border: 1px solid #336633; "
            "padding: 2px 8px; font-size: 11px; border-radius: 3px; }"
            "QPushButton:hover { background: #224422; }"
        )
        self._resume_btn.clicked.connect(lambda: self.resume_clicked.emit(self._worker_id))
        btn_row.addWidget(self._resume_btn)

        self._edit_btn = QPushButton("✎ 编辑")
        self._edit_btn.setFixedHeight(22)
        self._edit_btn.setStyleSheet(
            "QPushButton { color: #66AAFF; border: 1px solid #335577; "
            "padding: 2px 8px; font-size: 11px; border-radius: 3px; }"
            "QPushButton:hover { background: #223355; }"
        )
        self._edit_btn.clicked.connect(lambda: self.edit_clicked.emit(self._worker_id))
        btn_row.addWidget(self._edit_btn)

        btn_row.addStretch()

        self._remove_btn = QPushButton("✕ 移除")
        self._remove_btn.setFixedHeight(22)
        self._remove_btn.setStyleSheet(
            "QPushButton { color: #FF4444; border: 1px solid #662222; "
            "padding: 2px 8px; font-size: 11px; border-radius: 3px; }"
            "QPushButton:hover { background: #442222; }"
        )
        self._remove_btn.clicked.connect(lambda: self.remove_clicked.emit(self._worker_id))
        btn_row.addWidget(self._remove_btn)

        body_layout.addLayout(btn_row)
        self._layout.addWidget(self._body)

    def _toggle_expand(self, event=None):
        """点击折叠行切换展开/折叠"""
        self._expanded = not self._expanded
        self._body.setVisible(self._expanded)
        self._expand_icon.setText("▲" if self._expanded else "▼")

    def update_data(self, data: dict):
        """
        更新卡片显示数据。

        data fields:
            status, measurement_key, preset_value, deadband,
            last_value, last_error, errors_std, errors_count,
            frames_processed, kp, ki, kd, window_size,
            chain_type, target_info
        """
        status = data.get("status", "idle")
        self._meas_label.setText(data.get("measurement_key", ""))

        # 链路标签
        ct = data.get("chain_type", "none")
        if ct == "ad9910":
            self._chain_label.setText(f"[AD9910]")
        elif ct == "rtmq":
            self._chain_label.setText(f"[RTMQ]")
        else:
            self._chain_label.setText("")

        # 状态灯
        if status == "running":
            last_error = data.get("last_error")
            deadband = data.get("deadband", 0.0)
            if last_error is not None and abs(last_error) > deadband:
                color = STATUS_COLORS["running_red"]
            else:
                color = STATUS_COLORS["running_green"]
        elif status == "paused":
            color = STATUS_COLORS["paused"]
        else:
            color = STATUS_COLORS["idle"]
        self._status_light.setStyleSheet(f"color: {color}; font-size: 14px;")

        # 数值
        self._preset_label.setText(_fmt(data.get("preset_value")))
        self._value_label.setText(_fmt(data.get("last_value")))
        self._std_label.setText(_fmt(data.get("errors_std"), 4))

        # 展开区 — PID 参数
        self._kp_label.setText(f"Kp: {_fmt(data.get('kp', 0), 4)}")
        self._ki_label.setText(f"Ki: {_fmt(data.get('ki', 0), 4)}")
        self._kd_label.setText(f"Kd: {_fmt(data.get('kd', 0), 4)}")
        self._window_label.setText(f"窗口: {data.get('window_size', '—')}")
        self._deadband_label.setText(f"死区: {_fmt(data.get('deadband', 0), 6)}")
        skipped = data.get('frames_skipped', 0)
        frames_text = f"帧: {data.get('frames_processed', 0)}"
        if skipped:
            frames_text += f" (跳过 {skipped})"
        self._frames_label.setText(frames_text)

        # 自动暂停原因 (误差趋势变差等)
        stop_reason = data.get("stop_reason", "")
        if stop_reason:
            self._stop_reason_label.setText(f"⛔ {stop_reason}")
            self._stop_reason_label.setVisible(True)
        else:
            self._stop_reason_label.setVisible(False)

        # sender 错误
        sender_err = data.get("sender_error", "")
        if sender_err:
            self._sender_error_label.setText(f"⚠ sender: {sender_err}")
            self._sender_error_label.setVisible(True)
        else:
            self._sender_error_label.setVisible(False)

        # 按钮显隐
        is_running = status == "running"
        is_paused = status == "paused"
        self._pause_btn.setVisible(is_running)
        self._resume_btn.setVisible(is_paused)


# ═══════════════════════════════════════════════════════════════
# FeedbackDialog — 配置表单
# ═══════════════════════════════════════════════════════════════

class FeedbackDialog(QDialog):
    """添加反馈 Worker 的配置对话框"""

    def __init__(self, parent=None, measurement_keys: list[str] | None = None,
                 measurement_display_map: dict[str, str] | None = None,
                 existing_measurement_keys: set[str] | None = None,
                 target=None):
        super().__init__(parent)
        self.setWindowTitle("添加反馈 Worker")
        self.setMinimumWidth(440)

        self._measurement_keys = measurement_keys or []
        self._meas_display_map = measurement_display_map or {}
        self._existing_meas_keys: set[str] = existing_measurement_keys or set()
        self._result_config: Optional[FeedbackConfig] = None
        self._worker_id_internal: str = "w0"
        self._result_target = target

        layout = QVBoxLayout(self)

        form = QFormLayout()
        form.setSpacing(6)

        self._meas_key = QComboBox()
        self._meas_key.setEditable(True)
        for tag in self._measurement_keys:
            display = self._meas_display_map.get(tag, "")
            label = f"{display} ({tag})" if display else tag
            self._meas_key.addItem(label, tag)
        self._meas_key.lineEdit().setPlaceholderText("输入或选择测量项")
        form.addRow("测量项:", self._meas_key)

        # 反馈链路
        self._chain_combo = QComboBox()
        self._chain_combo.addItem("无 (仅测试)", "none")
        self._chain_combo.addItem("AD9910", "ad9910")
        self._chain_combo.addItem("RTMQ", "rtmq")
        self._chain_combo.currentIndexChanged.connect(self._on_chain_changed)
        form.addRow("反馈链路:", self._chain_combo)

        self._preset = QDoubleSpinBox()
        self._preset.setRange(-1000.0, 1000.0)
        self._preset.setDecimals(4)
        self._preset.setValue(3.3)
        form.addRow("目标值:", self._preset)
        layout.addLayout(form)

        # 目标设备配置（动态显隐）
        self._target_group = self._make_target_group()
        layout.addWidget(self._target_group)

        pid_group = QFrame()
        pid_group.setFrameShape(QFrame.Shape.StyledPanel)
        pid_group.setStyleSheet("QFrame { border: 1px solid #333; border-radius: 3px; padding: 6px; }")
        pid_layout = QFormLayout(pid_group)
        pid_layout.setSpacing(4)

        self._kp = QDoubleSpinBox(); self._kp.setRange(0, 10000); self._kp.setDecimals(6); self._kp.setValue(0.03); self._kp.setSingleStep(0.01)
        pid_layout.addRow("Kp:", self._kp)
        self._ki = QDoubleSpinBox(); self._ki.setRange(0, 10000); self._ki.setDecimals(6); self._ki.setValue(0); self._ki.setSingleStep(0.01)
        pid_layout.addRow("Ki:", self._ki)
        self._kd = QDoubleSpinBox(); self._kd.setRange(0, 10000); self._kd.setDecimals(6); self._kd.setValue(0); self._kd.setSingleStep(0.01)
        pid_layout.addRow("Kd:", self._kd)
        self._output_limit = QDoubleSpinBox(); self._output_limit.setRange(0, 1000); self._output_limit.setDecimals(6); self._output_limit.setValue(0.1)
        pid_layout.addRow("输出限幅:", self._output_limit)
        self._i_limit = QDoubleSpinBox(); self._i_limit.setRange(0, 1000); self._i_limit.setDecimals(6); self._i_limit.setValue(0.1)
        pid_layout.addRow("积分限幅:", self._i_limit)
        self._window_size = QSpinBox(); self._window_size.setRange(1, 10000); self._window_size.setValue(10)
        pid_layout.addRow("窗口大小:", self._window_size)
        self._deadband = QDoubleSpinBox(); self._deadband.setRange(0, 1000); self._deadband.setDecimals(6); self._deadband.setValue(0); self._deadband.setSingleStep(0.001)
        pid_layout.addRow("死区:", self._deadband)
        self._max_error_ratio = QDoubleSpinBox(); self._max_error_ratio.setRange(0, 1); self._max_error_ratio.setDecimals(2); self._max_error_ratio.setValue(0.30); self._max_error_ratio.setSingleStep(0.05)
        self._max_error_ratio.setToolTip("单帧 |误差|/|目标| 超过该值时跳过该帧 (0 = 禁用)")
        pid_layout.addRow("误差保护:", self._max_error_ratio)
        self._trend_window = QSpinBox(); self._trend_window.setRange(0, 1000); self._trend_window.setValue(5)
        self._trend_window.setToolTip("每 N 次反馈检查误差趋势, 不降反增则自动暂停 (0 = 禁用)")
        pid_layout.addRow("趋势检查:", self._trend_window)

        pid_title = QLabel("PID 参数")
        pid_title.setStyleSheet("color: #888; font-weight: bold; font-size: 11px;")
        layout.addWidget(pid_title)
        layout.addWidget(pid_group)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._on_chain_changed()

    def _make_target_group(self):
        group = QFrame()
        group.setFrameShape(QFrame.Shape.StyledPanel)
        group.setStyleSheet("QFrame { border: 1px solid #444; border-radius: 3px; padding: 6px; }")
        glayout = QVBoxLayout(group); glayout.setContentsMargins(4,4,4,4); glayout.setSpacing(4)
        title = QLabel("目标设备配置"); title.setStyleSheet("color: #888; font-weight: bold; font-size: 11px;")
        glayout.addWidget(title)
        from PyQt6.QtWidgets import QStackedWidget
        self._target_stack = QStackedWidget()
        self._target_stack.addWidget(self._make_none_page())
        self._target_stack.addWidget(self._make_ad9910_page())
        self._target_stack.addWidget(self._make_rtmq_page())
        glayout.addWidget(self._target_stack)
        return group

    def _make_none_page(self):
        w = QWidget(); wl = QVBoxLayout(w); wl.setContentsMargins(0,0,0,0)
        lbl = QLabel("无目标设备 — PID 输出仅记录不发送")
        lbl.setStyleSheet("color: #666; font-style: italic; font-size: 11px;"); wl.addWidget(lbl)
        return w

    def _make_ad9910_page(self):
        w = QWidget(); form = QFormLayout(w); form.setSpacing(4)
        self._ad9910_ip = QLineEdit(); self._ad9910_ip.setPlaceholderText("192.168.1.100"); self._ad9910_ip.setText("192.168.1.100")
        form.addRow("IP:", self._ad9910_ip)
        self._ad9910_port = QSpinBox(); self._ad9910_port.setRange(1,65535); self._ad9910_port.setValue(3251)
        form.addRow("端口:", self._ad9910_port)
        self._ad9910_dev_id = QLineEdit(); self._ad9910_dev_id.setPlaceholderText("hex, 如 0x0D11"); self._ad9910_dev_id.setText("0x0D11")
        form.addRow("Device ID:", self._ad9910_dev_id)
        self._ad9910_profile = QSpinBox(); self._ad9910_profile.setRange(0,7); self._ad9910_profile.setValue(0)
        form.addRow("Profile:", self._ad9910_profile)
        return w

    def _make_rtmq_page(self):
        w = QWidget(); form = QFormLayout(w); form.setSpacing(4)
        self._rtmq_ip = QLineEdit(); self._rtmq_ip.setPlaceholderText("192.168.1.100"); self._rtmq_ip.setText("192.168.1.100")
        form.addRow("IP:", self._rtmq_ip)
        self._rtmq_port = QSpinBox(); self._rtmq_port.setRange(1,65535); self._rtmq_port.setValue(18861)
        form.addRow("端口:", self._rtmq_port)
        self._rtmq_card = QSpinBox(); self._rtmq_card.setRange(0,255); self._rtmq_card.setValue(0)
        form.addRow("板卡号:", self._rtmq_card)
        self._rtmq_channel = QSpinBox(); self._rtmq_channel.setRange(0,255); self._rtmq_channel.setValue(0)
        form.addRow("边带通道:", self._rtmq_channel)
        return w

    def _on_chain_changed(self):
        chain = self._chain_combo.currentData()
        if chain == "ad9910":
            self._target_stack.setCurrentIndex(1); self._target_group.setVisible(True)
        elif chain == "rtmq":
            self._target_stack.setCurrentIndex(2); self._target_group.setVisible(True)
        else:
            self._target_stack.setCurrentIndex(0); self._target_group.setVisible(False)

    def _build_target(self):
        chain = self._chain_combo.currentData()
        if chain == "ad9910":
            dev_str = self._ad9910_dev_id.text().strip()
            try:
                device_id = int(dev_str, 16)
            except ValueError:
                device_id = 0
            return Ad9910Target(ip=self._ad9910_ip.text().strip(), port=self._ad9910_port.value(),
                                device_id=device_id, profile=self._ad9910_profile.value())
        elif chain == "rtmq":
            return RtmqTarget(ip=self._rtmq_ip.text().strip(), port=self._rtmq_port.value(),
                              card_index=self._rtmq_card.value(), sbg_channel=self._rtmq_channel.value())
        return None

    def _on_accept(self):
        worker_id = self._worker_id_internal
        meas_key = self._meas_key.currentData() or self._meas_key.currentText().strip()
        if not meas_key:
            self._meas_key.lineEdit().setPlaceholderText("⚠️ 测量项不能为空"); return
        if meas_key in self._existing_meas_keys:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "重复订阅",
                                f"测量项 \"{meas_key}\" 已被其他 Worker 订阅。\n请选择不同的测量项。"); return
        pid_config = PidConfig(
            preset_value=self._preset.value(), kp=self._kp.value(), ki=self._ki.value(),
            kd=self._kd.value(), i_limit=self._i_limit.value(),
            output_limit=self._output_limit.value(),
            window_size=self._window_size.value(), deadband=self._deadband.value(),
            max_error_ratio=self._max_error_ratio.value(),
            trend_window=self._trend_window.value(),
        )
        self._result_config = FeedbackConfig(
            worker_id=worker_id, measurement_key=meas_key,
            pid_config=pid_config, target=self._build_target(),
        )
        self.accept()

    @property
    def result_config(self) -> Optional[FeedbackConfig]:
        return self._result_config


# ═══════════════════════════════════════════════════════════════
# PidEditDialog — 编辑 PID 参数
# ═══════════════════════════════════════════════════════════════

class PidEditDialog(QDialog):
    """编辑已有 Worker 的 PID 参数和反馈链路"""

    def __init__(self, parent, pid_config: PidConfig, target=None):
        super().__init__(parent)
        self.setWindowTitle("编辑反馈 Worker")
        self.setMinimumWidth(440)
        self._result_config: Optional[PidConfig] = None
        self._result_target = target

        layout = QVBoxLayout(self)

        form = QFormLayout(); form.setSpacing(6)
        self._chain_combo = QComboBox()
        self._chain_combo.addItem("无 (仅测试)", "none")
        self._chain_combo.addItem("AD9910", "ad9910")
        self._chain_combo.addItem("RTMQ", "rtmq")
        self._chain_combo.currentIndexChanged.connect(self._on_chain_changed)
        form.addRow("反馈链路:", self._chain_combo)
        layout.addLayout(form)

        self._target_group = self._make_target_group()
        layout.addWidget(self._target_group)

        pid_group = QFrame()
        pid_group.setFrameShape(QFrame.Shape.StyledPanel)
        pid_group.setStyleSheet("QFrame { border: 1px solid #333; border-radius: 3px; padding: 6px; }")
        pid_layout = QFormLayout(pid_group); pid_layout.setSpacing(4)

        self._preset = QDoubleSpinBox(); self._preset.setRange(-1000,1000); self._preset.setDecimals(4); self._preset.setValue(pid_config.preset_value)
        pid_layout.addRow("目标值:", self._preset)
        self._kp = QDoubleSpinBox(); self._kp.setRange(0,10000); self._kp.setDecimals(6); self._kp.setValue(pid_config.kp); self._kp.setSingleStep(0.01)
        pid_layout.addRow("Kp:", self._kp)
        self._ki = QDoubleSpinBox(); self._ki.setRange(0,10000); self._ki.setDecimals(6); self._ki.setValue(pid_config.ki); self._ki.setSingleStep(0.01)
        pid_layout.addRow("Ki:", self._ki)
        self._kd = QDoubleSpinBox(); self._kd.setRange(0,10000); self._kd.setDecimals(6); self._kd.setValue(pid_config.kd); self._kd.setSingleStep(0.01)
        pid_layout.addRow("Kd:", self._kd)
        self._output_limit = QDoubleSpinBox(); self._output_limit.setRange(0,1000); self._output_limit.setDecimals(6); self._output_limit.setValue(pid_config.output_limit)
        pid_layout.addRow("输出限幅:", self._output_limit)
        self._i_limit = QDoubleSpinBox(); self._i_limit.setRange(0,1000); self._i_limit.setDecimals(6); self._i_limit.setValue(pid_config.i_limit)
        pid_layout.addRow("积分限幅:", self._i_limit)
        self._window_size = QSpinBox(); self._window_size.setRange(1,10000); self._window_size.setValue(pid_config.window_size)
        pid_layout.addRow("窗口大小:", self._window_size)
        self._deadband = QDoubleSpinBox(); self._deadband.setRange(0,1000); self._deadband.setDecimals(6); self._deadband.setValue(pid_config.deadband); self._deadband.setSingleStep(0.001)
        pid_layout.addRow("死区:", self._deadband)
        self._max_error_ratio = QDoubleSpinBox(); self._max_error_ratio.setRange(0,1); self._max_error_ratio.setDecimals(2); self._max_error_ratio.setValue(pid_config.max_error_ratio); self._max_error_ratio.setSingleStep(0.05)
        self._max_error_ratio.setToolTip("单帧 |误差|/|目标| 超过该值时跳过该帧 (0 = 禁用)")
        pid_layout.addRow("误差保护:", self._max_error_ratio)
        self._trend_window = QSpinBox(); self._trend_window.setRange(0,1000); self._trend_window.setValue(pid_config.trend_window)
        self._trend_window.setToolTip("每 N 次反馈检查误差趋势, 不降反增则自动暂停 (0 = 禁用)")
        pid_layout.addRow("趋势检查:", self._trend_window)

        layout.addWidget(pid_group)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_accept); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._prefill_target(target)
        self._on_chain_changed()

    def _make_target_group(self):
        group = QFrame()
        group.setFrameShape(QFrame.Shape.StyledPanel); group.setStyleSheet("QFrame { border: 1px solid #444; border-radius: 3px; padding: 6px; }")
        glayout = QVBoxLayout(group); glayout.setContentsMargins(4,4,4,4); glayout.setSpacing(4)
        title = QLabel("目标设备配置"); title.setStyleSheet("color: #888; font-weight: bold; font-size: 11px;"); glayout.addWidget(title)
        from PyQt6.QtWidgets import QStackedWidget
        self._target_stack = QStackedWidget()
        self._target_stack.addWidget(self._make_none_page())
        self._target_stack.addWidget(self._make_ad9910_page())
        self._target_stack.addWidget(self._make_rtmq_page())
        glayout.addWidget(self._target_stack)
        return group

    def _make_none_page(self):
        w = QWidget(); wl = QVBoxLayout(w); wl.setContentsMargins(0,0,0,0)
        lbl = QLabel("无目标设备 — PID 输出仅记录不发送")
        lbl.setStyleSheet("color: #666; font-style: italic; font-size: 11px;"); wl.addWidget(lbl)
        return w

    def _make_ad9910_page(self):
        w = QWidget(); form = QFormLayout(w); form.setSpacing(4)
        self._ad9910_ip = QLineEdit(); self._ad9910_ip.setPlaceholderText("192.168.1.100"); self._ad9910_ip.setText("192.168.1.100")
        form.addRow("IP:", self._ad9910_ip)
        self._ad9910_port = QSpinBox(); self._ad9910_port.setRange(1,65535); self._ad9910_port.setValue(3251)
        form.addRow("端口:", self._ad9910_port)
        self._ad9910_dev_id = QLineEdit(); self._ad9910_dev_id.setPlaceholderText("hex, 如 0x0D11"); self._ad9910_dev_id.setText("0x0D11")
        form.addRow("Device ID:", self._ad9910_dev_id)
        self._ad9910_profile = QSpinBox(); self._ad9910_profile.setRange(0,7); self._ad9910_profile.setValue(0)
        form.addRow("Profile:", self._ad9910_profile)
        return w

    def _make_rtmq_page(self):
        w = QWidget(); form = QFormLayout(w); form.setSpacing(4)
        self._rtmq_ip = QLineEdit(); self._rtmq_ip.setPlaceholderText("192.168.1.100"); self._rtmq_ip.setText("192.168.1.100")
        form.addRow("IP:", self._rtmq_ip)
        self._rtmq_port = QSpinBox(); self._rtmq_port.setRange(1,65535); self._rtmq_port.setValue(18861)
        form.addRow("端口:", self._rtmq_port)
        self._rtmq_card = QSpinBox(); self._rtmq_card.setRange(0,255); self._rtmq_card.setValue(0)
        form.addRow("板卡号:", self._rtmq_card)
        self._rtmq_channel = QSpinBox(); self._rtmq_channel.setRange(0,255); self._rtmq_channel.setValue(0)
        form.addRow("边带通道:", self._rtmq_channel)
        return w

    def _prefill_target(self, target):
        if isinstance(target, Ad9910Target):
            self._chain_combo.setCurrentIndex(1)
            self._ad9910_ip.setText(target.ip); self._ad9910_port.setValue(target.port)
            self._ad9910_dev_id.setText(hex(target.device_id)); self._ad9910_profile.setValue(target.profile)
        elif isinstance(target, RtmqTarget):
            self._chain_combo.setCurrentIndex(2)
            self._rtmq_ip.setText(target.ip); self._rtmq_port.setValue(target.port)
            self._rtmq_card.setValue(target.card_index); self._rtmq_channel.setValue(target.sbg_channel)

    def _on_chain_changed(self):
        chain = self._chain_combo.currentData()
        if chain == "ad9910":
            self._target_stack.setCurrentIndex(1); self._target_group.setVisible(True)
        elif chain == "rtmq":
            self._target_stack.setCurrentIndex(2); self._target_group.setVisible(True)
        else:
            self._target_stack.setCurrentIndex(0); self._target_group.setVisible(False)

    def _build_target(self):
        chain = self._chain_combo.currentData()
        if chain == "ad9910":
            dev_str = self._ad9910_dev_id.text().strip()
            try: device_id = int(dev_str, 16)
            except ValueError: device_id = 0
            return Ad9910Target(ip=self._ad9910_ip.text().strip(), port=self._ad9910_port.value(),
                                device_id=device_id, profile=self._ad9910_profile.value())
        elif chain == "rtmq":
            return RtmqTarget(ip=self._rtmq_ip.text().strip(), port=self._rtmq_port.value(),
                              card_index=self._rtmq_card.value(), sbg_channel=self._rtmq_channel.value())
        return None

    def _on_accept(self):
        self._result_config = PidConfig(
            preset_value=self._preset.value(), kp=self._kp.value(), ki=self._ki.value(),
            kd=self._kd.value(), i_limit=self._i_limit.value(),
            output_limit=self._output_limit.value(),
            window_size=self._window_size.value(), deadband=self._deadband.value(),
            max_error_ratio=self._max_error_ratio.value(),
            trend_window=self._trend_window.value(),
        )
        self._result_target = self._build_target()
        self.accept()

    @property
    def result_config(self) -> Optional[PidConfig]:
        return self._result_config

    @property
    def result_target(self):
        return self._result_target


# ═══════════════════════════════════════════════════════════════
# FeedbackPanel — 主面板
# ═══════════════════════════════════════════════════════════════

class FeedbackPanel(QWidget):
    """反馈面板 — 显示和管理反馈 Worker"""

    def __init__(
        self,
        parent_widget: QWidget = None,
        feedback_manager=None,
        measurement_panel=None,
        status_callback: Optional[Callable] = None,
        async_loop=None,
        event_bus=None,
        command_id_provider: Optional[Callable[[], int]] = None,
    ):
        super().__init__(parent_widget)
        self._feedback_mgr = feedback_manager
        self._measurement_panel = measurement_panel
        self._status_callback = status_callback
        self._async_loop = async_loop
        self._event_bus = event_bus
        self._command_change_id = 0
        self._command_id_provider = command_id_provider or self._next_command_id
        self._card_widgets: dict[str, WorkerCard] = {}  # worker_id → WorkerCard
        self._worker_counter: int = len(self._card_widgets)
        self._last_status: Optional[FeedbackStatusSnapshot] = None

        self._setup_ui()

    def _next_command_id(self) -> int:
        self._command_change_id += 1
        return self._command_change_id

    def _setup_ui(self):
        """构建 UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # 标题
        title = QLabel("反馈 Worker")
        title.setStyleSheet("color: #888; font-size: 11px; font-weight: bold;")
        layout.addWidget(title)

        # 滚动区
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet("QScrollArea { border: none; }")

        self._container = QWidget()
        self._container_layout = QVBoxLayout(self._container)
        self._container_layout.setContentsMargins(0, 0, 0, 0)
        self._container_layout.setSpacing(2)
        self._container_layout.addStretch()
        self._scroll.setWidget(self._container)
        layout.addWidget(self._scroll, stretch=1)

        # 添加按钮
        btn_add = QPushButton("+ 添加反馈")
        btn_add.setStyleSheet(
            "QPushButton { color: #00CC00; border: 1px solid #336633; padding: 4px; }"
            "QPushButton:hover { background: #224422; }"
        )
        btn_add.clicked.connect(self._on_add)
        layout.addWidget(btn_add)

        # 占位提示
        self._placeholder = QLabel("暂无反馈 Worker\n点击「+ 添加反馈」创建")
        self._placeholder.setStyleSheet("color: #666; font-style: italic; padding: 10px;")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._container_layout.insertWidget(0, self._placeholder)

    # ── 添加 Worker ────────────────────────────────────────────

    def _on_add(self):
        """打开配置对话框创建 Worker"""
        # 获取可用测量项列表
        meas_tags: list[str] = []
        display_map: dict[str, str] = {}  # stable_tag → display_name
        if self._measurement_panel:
            try:
                if hasattr(self._measurement_panel, "get_display_name_mapping"):
                    display_map = dict(self._measurement_panel.get_display_name_mapping())
                specs = self._measurement_panel.get_measurement_specs()
                meas_tags = [s["tag"] for s in specs if s.get("tag")]
            except Exception as e:
                logger.warning("获取测量项列表失败: %s", e)

        if not meas_tags:
            logger.warning("没有可用测量项，请先在测量面板添加测量行")
            return

        # 自动生成 Worker ID
        worker_id = f"w{self._worker_counter}"
        self._worker_counter += 1

        # 收集已被订阅的测量项 key
        existing_keys: set[str] = set()
        if self._last_status:
            for ws in self._last_status.workers:
                existing_keys.add(ws.measurement_key)

        dlg = FeedbackDialog(self, measurement_keys=meas_tags,
                             measurement_display_map=display_map,
                             existing_measurement_keys=existing_keys)
        dlg._worker_id_internal = worker_id
        if dlg.exec() == QDialog.DialogCode.Accepted:
            config = dlg.result_config
            if config:
                self._publish_feedback_command(
                    action="add",
                    worker_id=worker_id,
                    config=config,
                )
                logger.info(f'添加 Worker 成功: {worker_id} → {config.measurement_key}')

    # ── 事件驱动刷新 ───────────────────────────────────────────

    def on_status_update(self, snapshot: FeedbackStatusSnapshot):
        """
        从 feedback.status topic 快照更新卡片。

        由 UIBridge signal_feedback_status 驱动，替代旧的
        refresh_slots() 对 _feedback_mgr.list_workers() 的直接调用。
        """
        self._last_status = snapshot

        name_map: dict[str, str] = {}
        if self._measurement_panel and hasattr(self._measurement_panel, "get_display_name_mapping"):
            try:
                name_map = dict(self._measurement_panel.get_display_name_mapping())
            except Exception:
                pass

        current_ids = {ws.worker_id for ws in snapshot.workers}

        # 移除已删除的卡片
        for wid in list(self._card_widgets.keys()):
            if wid not in current_ids:
                card = self._card_widgets.pop(wid)
                self._container_layout.removeWidget(card)
                card.deleteLater()

        # 添加/更新卡片
        for ws in snapshot.workers:
            wid = ws.worker_id
            display_name = name_map.get(ws.measurement_key, ws.measurement_key)
            card_data = {
                "status": ws.status,
                "measurement_key": display_name,
                "preset_value": ws.preset_value,
                "deadband": ws.deadband,
                "last_value": ws.last_value,
                "last_error": ws.last_error,
                "errors_std": ws.errors_std,
                "errors_count": ws.errors_count,
                "frames_processed": ws.frames_processed,
                "kp": ws.kp,
                "ki": ws.ki,
                "kd": ws.kd,
                "window_size": ws.window_size,
                "chain_type": ws.chain_type,
                "target_info": ws.target_info,
                "sender_error": ws.sender_error,
            }

            if wid in self._card_widgets:
                self._card_widgets[wid].update_data(card_data)
            else:
                card = WorkerCard(wid)
                card.pause_clicked.connect(self._on_pause_worker)
                card.resume_clicked.connect(self._on_resume_worker)
                card.remove_clicked.connect(self._on_remove_worker)
                card.edit_clicked.connect(self._on_edit_worker)
                card.update_data(card_data)
                self._card_widgets[wid] = card
                self._container_layout.insertWidget(
                    self._container_layout.count() - 1, card
                )

        # 占位提示显隐
        has_workers = len(self._card_widgets) > 0
        self._placeholder.setVisible(not has_workers)

    # ── Worker 操作 ────────────────────────────────────────────

    def _on_pause_worker(self, worker_id: str):
        self._publish_feedback_command("pause", worker_id)

    def _on_resume_worker(self, worker_id: str):
        self._publish_feedback_command("resume", worker_id)

    def _on_remove_worker(self, worker_id: str):
        self._publish_feedback_command("remove", worker_id)

    def _on_edit_worker(self, worker_id: str):
        """弹出编辑对话框（PID + 链路）"""
        if not self._last_status:
            return

        wdata = next((w for w in self._last_status.workers if w.worker_id == worker_id), None)
        if not wdata:
            return

        current = PidConfig(
            preset_value=wdata.preset_value,
            kp=wdata.kp, ki=wdata.ki, kd=wdata.kd,
            output_limit=wdata.output_limit, i_limit=wdata.i_limit,
            window_size=wdata.window_size, deadband=wdata.deadband,
            max_error_ratio=wdata.max_error_ratio,
            trend_window=wdata.trend_window,
        )
        # 获取当前 target
        current_target = None
        if self._feedback_mgr:
            try:
                current_target = self._feedback_mgr.get_worker_target(worker_id)
            except Exception:
                pass

        dlg = PidEditDialog(self, pid_config=current, target=current_target)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            if dlg.result_config:
                self._publish_feedback_command(
                    action="update_pid",
                    worker_id=worker_id,
                    pid_config=dlg.result_config,
                )
                logger.info(f"Worker '{worker_id}' PID 参数更新已提交")
            new_target = dlg.result_target
            # 只有 target 真正变化时才发送 update_target 命令
            if new_target != current_target:
                cfg = FeedbackConfig(worker_id=worker_id, measurement_key=wdata.measurement_key,
                                     pid_config=current, target=new_target)
                self._publish_feedback_command(
                    action="update_target",
                    worker_id=worker_id,
                    config=cfg,
                )
                logger.info(f"Worker '{worker_id}' 目标设备更新已提交")

    def _publish_feedback_command(
        self,
        action: str,
        worker_id: str,
        config: Optional[FeedbackConfig] = None,
        pid_config: Optional[PidConfig] = None,
    ):
        """发布反馈控制命令到 EventBus。"""
        if not self._event_bus:
            logger.error("无法发布反馈命令: EventBus 未连接")
            return

        self._event_bus.publish(
            "feedback.worker.command",
            FeedbackCommand(
                action=action,
                worker_id=worker_id,
                config=config,
                pid_config=pid_config,
                change_id=self._command_id_provider(),
            ),
        )

    # ── 状态查询 ───────────────────────────────────────────────

    def get_active_count(self) -> tuple[int, int]:
        """返回 (running_count, total_count) — 从缓存读。"""
        if not self._last_status:
            return 0, 0
        return self._last_status.running_count, self._last_status.total_count
