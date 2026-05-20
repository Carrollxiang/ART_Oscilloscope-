"""
反馈管理面板 — 可折叠卡片式 UI

每个反馈 slot 显示为一张可折叠卡片:
  折叠: [▶] [●状态灯] 名称  mode  ●运行  sent:42  [继续] [编辑] [✕]
  展开: + 完整参数 / PID / 阈值 / 极限 / 订阅
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional, Callable

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QSpinBox, QDoubleSpinBox,
    QPushButton, QScrollArea, QFrame, QComboBox,
    QAbstractItemView, QListWidget,
    QDialog, QFormLayout, QGroupBox, QDialogButtonBox,
    QMessageBox,
)
from PyQt6.QtGui import QColor, QPainter, QBrush as QGBrush

from scope.io import FeedbackManager, RpycFeedbackSlot, RpycSlotConfig, DataSubscription
from scope.io.feedback_slots.pid_slot import PidFeedbackSlot, PidSlotConfig

logger = logging.getLogger(__name__)

# ── 卡片配色 ──────────────────────────────────────────────────

CARD_BG = "#FFF8E7"        # 奶白
CARD_BORDER = "#D4C9A8"    # 浅褐
CARD_HDR_BG = "#FFF0CC"    # 淡黄
TEXT_COLOR = "#222222"
TEXT_DIM = "#888888"
TEXT_LABEL = "#555555"

STATUS_COLORS = {
    "running": "#22AA22",
    "paused": "#CC8800",
    "error": "#CC2222",
    "idle": "#AAAAAA",
}

# 测量状态灯颜色
MEAS_LED = {
    "stable": "#00CC00",
    "unstable": "#FFAA00",
    "out_of_limit": "#CC2222",
    "unknown": "#888888",
}


# ── 添加/编辑对话框 ───────────────────────────────────────────

class FeedbackDialog(QDialog):
    """反馈目标配置对话框"""

    FEEDBACK_MODES = [
        ("standard", "标准 PID"),
        ("fast", "快速 PID"),
        ("slow", "慢速 PID"),
    ]

    DEFAULT_PID = {
        "standard": (1.0, 0.1, 0.01, -100, 100),
        "fast": (2.0, 0.05, 0.05, -100, 100),
        "slow": (0.5, 0.2, 0.005, -100, 100),
    }

    def __init__(self, parent=None, slot_id: str = "",
                 measurement_items: list[dict] = None,
                 existing_config: Optional[RpycSlotConfig] = None):
        super().__init__(parent)
        self.setWindowTitle("反馈目标配置")
        self.setMinimumSize(500, 420)
        self._meas_items = measurement_items or []
        self._build_ui()

        if existing_config:
            self._load_config(existing_config)
        if slot_id:
            self.editId.setText(slot_id)
            self.editId.setEnabled(False)

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # ── 连接 ──
        g1 = QGroupBox("连接")
        f1 = QFormLayout(g1)
        self.editId = QLineEdit()
        self.editHost = QLineEdit("127.0.0.1")
        self.editPort = QSpinBox(); self.editPort.setRange(1, 65535); self.editPort.setValue(18861)
        self.editMethod = QLineEdit("exposed_update")
        f1.addRow("标识", self.editId)
        f1.addRow("主机", self.editHost)
        f1.addRow("端口", self.editPort)
        f1.addRow("远程方法", self.editMethod)

        pool = QHBoxLayout()
        self.spinPoolMin = QSpinBox(); self.spinPoolMin.setRange(0, 10); self.spinPoolMin.setValue(1)
        self.spinPoolMax = QSpinBox(); self.spinPoolMax.setRange(1, 20); self.spinPoolMax.setValue(4)
        pool.addWidget(QLabel("最小")); pool.addWidget(self.spinPoolMin)
        pool.addWidget(QLabel("最大")); pool.addWidget(self.spinPoolMax)
        f1.addRow("连接池", pool)
        layout.addWidget(g1)

        # ── 反馈参数 (2 列: 左/右) ──
        g2 = QGroupBox("反馈参数")
        g2row = QHBoxLayout(g2)

        # 左: 模式 + 设定值 + PID
        left = QFormLayout()
        self.cmbMode = QComboBox()
        for code, label in self.FEEDBACK_MODES:
            self.cmbMode.addItem(label, code)
        self.cmbMode.currentIndexChanged.connect(self._on_mode_change)
        left.addRow("反馈模式", self.cmbMode)

        self.spinSetpoint = QDoubleSpinBox()
        self.spinSetpoint.setRange(-1e6, 1e6); self.spinSetpoint.setDecimals(4)
        self.spinSetpoint.setValue(0.0)
        self.spinSetpoint.setFixedWidth(120)
        left.addRow("设定值", self.spinSetpoint)

        self.spinKp = QDoubleSpinBox()
        self.spinKp.setRange(-1e6, 1e6); self.spinKp.setDecimals(4); self.spinKp.setValue(1.0); self.spinKp.setFixedWidth(100)
        self.spinKi = QDoubleSpinBox()
        self.spinKi.setRange(-1e6, 1e6); self.spinKi.setDecimals(4); self.spinKi.setValue(0.1); self.spinKi.setFixedWidth(100)
        self.spinKd = QDoubleSpinBox()
        self.spinKd.setRange(-1e6, 1e6); self.spinKd.setDecimals(4); self.spinKd.setValue(0.01); self.spinKd.setFixedWidth(100)
        left.addRow("Kp", self.spinKp)
        left.addRow("Ki", self.spinKi)
        left.addRow("Kd", self.spinKd)

        out = QHBoxLayout()
        self.spinOutMin = QDoubleSpinBox(); self.spinOutMin.setRange(-1e6, 0); self.spinOutMin.setValue(-100); self.spinOutMin.setFixedWidth(80)
        self.spinOutMax = QDoubleSpinBox(); self.spinOutMax.setRange(0, 1e6); self.spinOutMax.setValue(100); self.spinOutMax.setFixedWidth(80)
        out.addWidget(self.spinOutMin); out.addWidget(QLabel("~")); out.addWidget(self.spinOutMax)
        left.addRow("输出限幅", out)
        g2row.addLayout(left)

        # 右: 阈值 + 极限
        right = QFormLayout()
        self.spinThreshold = QDoubleSpinBox()
        self.spinThreshold.setRange(0, 1e6); self.spinThreshold.setDecimals(4); self.spinThreshold.setValue(0.0)
        self.spinThreshold.setSuffix(" (0=禁用)"); self.spinThreshold.setFixedWidth(150)
        right.addRow("反馈阈值", self.spinThreshold)
        thr_note = QLabel("|测量值 - 设定值| < 阈值 → 停止反馈")
        thr_note.setStyleSheet(f"color: {TEXT_DIM}; font-size: 10px;")
        right.addRow("", thr_note)

        self.spinLimit = QDoubleSpinBox()
        self.spinLimit.setRange(0, 1e6); self.spinLimit.setDecimals(4); self.spinLimit.setValue(0.0)
        self.spinLimit.setSuffix(" (0=禁用)"); self.spinLimit.setFixedWidth(150)
        right.addRow("反馈极限", self.spinLimit)
        lim_note = QLabel("|测量值 - 设定值| > 极限 → 停止反馈")
        lim_note.setStyleSheet(f"color: {TEXT_DIM}; font-size: 10px;")
        right.addRow("", lim_note)
        g2row.addLayout(right)

        layout.addWidget(g2)

        # ── 订阅列表 ──
        layout.addWidget(QLabel("订阅测量项:"))
        self.subList = QListWidget()
        self.subList.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        for item in self._meas_items:
            self.subList.addItem(f"{item['name']} ({item['channel']}_{item['meas_key']})")
        layout.addWidget(self.subList)

        # ── 按钮 ──
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _on_mode_change(self, idx):
        mode = self.cmbMode.itemData(idx)
        d = self.DEFAULT_PID.get(mode)
        if not d: return
        self.spinKp.setValue(d[0]); self.spinKi.setValue(d[1]); self.spinKd.setValue(d[2])
        self.spinOutMin.setValue(d[3]); self.spinOutMax.setValue(d[4])

    def _load_config(self, cfg: RpycSlotConfig):
        self.editId.setText(cfg.slot_id)
        self.editHost.setText(cfg.host); self.editPort.setValue(cfg.port)
        self.editMethod.setText(cfg.remote_method)
        self.spinPoolMin.setValue(cfg.pool_min); self.spinPoolMax.setValue(cfg.pool_max)
        for i in range(self.cmbMode.count()):
            if self.cmbMode.itemData(i) == cfg.feedback_mode:
                self.cmbMode.setCurrentIndex(i); break
        self.spinSetpoint.setValue(cfg.setpoint)
        self.spinKp.setValue(cfg.pid_kp); self.spinKi.setValue(cfg.pid_ki); self.spinKd.setValue(cfg.pid_kd)
        self.spinOutMin.setValue(cfg.pid_output_min); self.spinOutMax.setValue(cfg.pid_output_max)
        self.spinThreshold.setValue(cfg.feedback_threshold); self.spinLimit.setValue(cfg.feedback_limit)

    def get_config(self) -> RpycSlotConfig:
        subs = []
        for item in self.subList.selectedItems():
            idx = self.subList.row(item)
            if 0 <= idx < len(self._meas_items):
                m = self._meas_items[idx]
                subs.append(DataSubscription(local_key=f"{m['channel']}_{m['meas_key']}", remote_key=m['name']))
        return RpycSlotConfig(
            slot_id=self.editId.text(), host=self.editHost.text(), port=self.editPort.value(),
            remote_method=self.editMethod.text(), pool_min=self.spinPoolMin.value(), pool_max=self.spinPoolMax.value(),
            feedback_mode=self.cmbMode.currentData(), setpoint=self.spinSetpoint.value(),
            pid_kp=self.spinKp.value(), pid_ki=self.spinKi.value(), pid_kd=self.spinKd.value(),
            pid_output_min=self.spinOutMin.value(), pid_output_max=self.spinOutMax.value(),
            feedback_threshold=self.spinThreshold.value(), feedback_limit=self.spinLimit.value(),
            subscriptions=subs,
        )


# ── 状态灯控件 ────────────────────────────────────────────────

class StatusLED(QLabel):
    """圆形状态指示灯。"""
    def __init__(self, color="#888", size=12):
        super().__init__()
        self.setFixedSize(size, size)
        self._color = color

    def set_color(self, color: str):
        self._color = color
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect()
        p.setBrush(QGBrush(QColor(self._color)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(r.adjusted(1, 1, -1, -1))
        p.end()


# ── 单张卡片 ───────────────────────────────────────────────────

class FeedbackCard(QFrame):
    """单个 feedback slot 的可折叠卡片 (奶白底色)。"""

    def __init__(self, slot_info,
                 on_pause=None, on_edit=None, on_remove=None):
        super().__init__()
        self._slot_id = slot_info.slot_id
        self._expanded = False
        self._on_pause = on_pause
        self._on_edit = on_edit
        self._on_remove = on_remove

        self.setObjectName("feedbackCard")
        self.setStyleSheet(
            f"#feedbackCard {{ background: {CARD_BG}; border: 1px solid {CARD_BORDER}; "
            f"border-radius: 4px; margin: 2px; }}"
            f"#feedbackCard QPushButton {{ font-size: 10px; padding: 2px 4px; }}"
        )
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(8, 4, 8, 4)
        self._layout.setSpacing(4)

        self._header = self._build_header(slot_info)
        self._layout.addWidget(self._header)

        self._detail = self._build_detail(slot_info)
        self._detail.setVisible(False)
        self._layout.addWidget(self._detail)

    def _build_header(self, info) -> QWidget:
        hdr = QWidget()
        hdr.setStyleSheet(f"background: {CARD_HDR_BG}; border-radius: 3px;")
        row = QHBoxLayout(hdr)
        row.setContentsMargins(6, 4, 6, 4)

        # 展开/折叠箭头
        self._arrow = QLabel("▶")
        self._arrow.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11px; background: transparent;")
        row.addWidget(self._arrow)

        # 状态灯
        ms = getattr(info, 'measurement_status', 'unknown')
        led_color = MEAS_LED.get(ms, "#888")
        self._led = StatusLED(led_color)
        row.addWidget(self._led)

        # 名称
        name = QLabel(info.slot_id)
        name.setStyleSheet(f"font-weight: bold; color: {TEXT_COLOR}; font-size: 12px; background: transparent;")
        row.addWidget(name)

        # 模式
        mode = QLabel(f"mode:{getattr(info, 'feedback_mode', '-')}")
        mode.setStyleSheet(f"color: {TEXT_LABEL}; font-size: 10px; background: transparent;")
        row.addWidget(mode)

        # 状态文字
        sc = STATUS_COLORS.get(info.status, "#888")
        st = QLabel(f"● {info.status}")
        st.setStyleSheet(f"color: {sc}; font-size: 11px; background: transparent;")
        row.addWidget(st)

        # 已发送
        s = QLabel(f"sent:{info.sent_count}")
        s.setStyleSheet(f"color: {TEXT_DIM}; font-size: 10px; background: transparent;")
        row.addWidget(s)

        row.addStretch()

        # 暂停/继续/开始
        btn_text = {"idle": "开始", "running": "暂停", "paused": "继续"}.get(info.status, "开始")
        self._btnPause = QPushButton(btn_text)
        self._btnPause.setFixedSize(44, 22)
        self._btnPause.setStyleSheet("font-size: 10px; padding: 2px 4px;")
        self._btnPause.clicked.connect(lambda: self._on_pause(self._slot_id) if self._on_pause else None)
        row.addWidget(self._btnPause)

        btnEdit = QPushButton("编辑"); btnEdit.setFixedSize(36, 22)
        btnEdit.setStyleSheet("font-size: 10px; padding: 2px 4px;")
        btnEdit.clicked.connect(lambda: self._on_edit(self._slot_id) if self._on_edit else None)
        row.addWidget(btnEdit)

        # 点击头部切换折叠
        for w in [hdr, self._arrow, self._led, name, mode, st, s]:
            w.mousePressEvent = lambda ev: self._toggle()

        return hdr

    def _build_detail(self, info) -> QWidget:
        det = QWidget()
        det.setStyleSheet("background: transparent;")
        v = QVBoxLayout(det)
        v.setContentsMargins(6, 4, 6, 4)
        v.setSpacing(2)

        def lbl(text, style=""):
            l = QLabel(text); l.setStyleSheet(f"color: {TEXT_COLOR}; background: transparent; font-size: 11px; {style}")
            return l

        v.addWidget(lbl(f"🎯 {info.target}  |  mode: {getattr(info, 'feedback_mode', '-')}  |  "
                        f"setpoint: {getattr(info, 'setpoint', 0.0):.4f}"))

        kp = getattr(info, 'pid_kp', 0); ki = getattr(info, 'pid_ki', 0); kd = getattr(info, 'pid_kd', 0)
        omin = getattr(info, 'pid_output_min', -100); omax = getattr(info, 'pid_output_max', 100)
        v.addWidget(lbl(f"PID:  Kp={kp:.4f}  Ki={ki:.4f}  Kd={kd:.4f}  |  output=[{omin}, {omax}]"))

        thr = getattr(info, 'feedback_threshold', 0); lim = getattr(info, 'feedback_limit', 0)
        lv = getattr(info, 'latest_value', 0); sp = getattr(info, 'setpoint', 0)
        v.addWidget(lbl(f"阈值: {thr}  |  极限: {lim}  |  最新值: {lv:.4f}  |  Δ={abs(lv-sp):.4f}"))

        subs = info.subscriptions or []
        v.addWidget(lbl(f"订阅 ({len(subs)}): {', '.join(subs[:6])}"))

        if info.last_error:
            e = lbl(f"⚠ {info.last_error}", "color: #CC2222;")
            v.addWidget(e)

        # 危险操作: 删除此反馈 (展开详情可见)
        del_btn = QPushButton("删除此反馈")
        del_btn.setStyleSheet(
            "color: #CC4444; font-size: 10px; padding: 2px; "
            "background: transparent; border: 1px solid #EEDDDD; border-radius: 2px;"
        )
        del_btn.clicked.connect(lambda: self._on_remove(self._slot_id) if self._on_remove else None)
        v.addWidget(del_btn)

        return det

    def _toggle(self):
        self._expanded = not self._expanded
        self._detail.setVisible(self._expanded)
        self._arrow.setText("▼" if self._expanded else "▶")

    def update_info(self, info):
        """刷新卡片数据 (状态灯、按钮、详情)。"""
        # 状态灯
        ms = getattr(info, 'measurement_status', 'unknown')
        self._led.set_color(MEAS_LED.get(ms, "#888"))
        # 开始/暂停/继续 按钮
        btn_text = {"idle": "开始", "running": "暂停", "paused": "继续"}.get(info.status, "开始")
        self._btnPause.setText(btn_text)
        # 重建详情
        old = self._detail
        self._detail = self._build_detail(info)
        self._detail.setVisible(self._expanded)
        self._layout.replaceWidget(old, self._detail)
        old.deleteLater()


# ── 管理面板 ───────────────────────────────────────────────────

class FeedbackPanel:
    """
    反馈管理面板 — 可折叠卡片列表。
    """

    def __init__(self, parent_widget: QWidget,
                 feedback_manager: FeedbackManager,
                 measurement_panel=None,
                 status_callback: Optional[Callable[[], None]] = None,
                 async_loop=None):
        """
        async_loop: asyncio 事件循环 (用于避免 asyncio.run 闪窗)。
        """
        self._parent = parent_widget
        self._mgr = feedback_manager
        self._meas_panel = measurement_panel
        self._status_cb = status_callback
        self._cards: dict[str, FeedbackCard] = {}
        self._notified_auto_pause: set[str] = set()
        self._async_loop = async_loop
        self._build_ui()
        self._timer = QTimer()
        self._timer.setInterval(2000)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()

    def _build_ui(self):
        layout = self._parent.layout()
        if layout is None:
            layout = QVBoxLayout(self._parent)
            self._parent.setLayout(layout)
        else:
            # 只清除子控件
            while layout.count():
                item = layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
        top = QHBoxLayout()
        btn_add = QPushButton("+ 添加 RPC")
        btn_add.setStyleSheet("color: #228822; font-weight: bold;")
        btn_add.clicked.connect(self._on_add)
        top.addWidget(btn_add)
        btn_pid = QPushButton("+ 添加 PID")
        btn_pid.setStyleSheet("color: #CC6600; font-weight: bold;")
        btn_pid.clicked.connect(self._on_add_pid)
        top.addWidget(btn_pid)
        top.addStretch()
        layout.addLayout(top)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        self._container = QWidget()
        self._card_layout = QVBoxLayout(self._container)
        self._card_layout.setSpacing(4)
        self._card_layout.addStretch()
        scroll.setWidget(self._container)
        layout.addWidget(scroll, stretch=1)

    def _add_card(self, info):
        card = FeedbackCard(info, on_pause=self._on_pause, on_edit=self._on_edit, on_remove=self._on_remove)
        self._cards[info.slot_id] = card
        self._card_layout.insertWidget(self._card_layout.count() - 1, card)

    def _refresh(self):
        infos = self._mgr.list_slots()
        cur = set(self._cards.keys())
        nw = {i.slot_id for i in infos}
        for sid in cur - nw:
            if sid in self._cards:
                c = self._cards.pop(sid)
                self._card_layout.removeWidget(c); c.deleteLater()
        for info in infos:
            if info.slot_id not in self._cards:
                self._add_card(info)
        for info in infos:
            if info.slot_id in self._cards:
                self._cards[info.slot_id].update_info(info)
        for info in infos:
            if info.auto_paused and info.slot_id not in self._notified_auto_pause:
                self._notified_auto_pause.add(info.slot_id)
                QMessageBox.warning(self._parent, "自动暂停",
                    f"'{info.slot_id}' 连续 {info.consecutive_errors} 次失败, 已暂停。\n最后错误: {info.last_error}")
        if self._status_cb:
            self._status_cb()

    def _get_meas_items(self):
        return self._meas_panel.get_subscriptions() if self._meas_panel else []

    def _run_async(self, coro):
        """在 async loop 上执行协程, 避免 asyncio.run 闪窗。"""
        if self._async_loop and self._async_loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, self._async_loop)
        else:
            asyncio.run(coro)

    def _on_add(self):
        dlg = FeedbackDialog(self._parent, measurement_items=self._get_meas_items())
        if dlg.exec() == QDialog.DialogCode.Accepted:
            try:
                async def do():
                    await self._mgr.add_slot(RpycFeedbackSlot(dlg.get_config()))
                self._run_async(do())
                self._refresh()
            except KeyError:
                QMessageBox.warning(self._parent, "重复", f'"{dlg.get_config().slot_id}" 已存在')
            except Exception as e:
                QMessageBox.critical(self._parent, "错误", f"添加失败: {e}")

    def _on_edit(self, slot_id: str):
        slot = self._mgr.get_slot(slot_id)
        if not slot: return
        dlg = FeedbackDialog(self._parent, slot_id=slot_id,
                             measurement_items=self._get_meas_items(),
                             existing_config=getattr(slot, '_rpyc_config', None))
        if dlg.exec() == QDialog.DialogCode.Accepted:
            try:
                async def do():
                    await slot.reconfigure(dlg.get_config())
                self._run_async(do())
                self._refresh()
            except Exception as e:
                QMessageBox.critical(self._parent, "错误", f"更新失败: {e}")

    def _on_add_pid(self):
        """打开 PID 反馈配置对话框。"""
        from .pid_feedback_dialog import PidFeedbackDialog
        dlg = PidFeedbackDialog(self._parent, measurement_items=self._get_meas_items())
        if dlg.exec() == QDialog.DialogCode.Accepted:
            cfg = dlg.get_config()
            # 使用第一个订阅的 local_key 填充 measurement_key
            if not cfg.measurement_key and cfg.subscriptions:
                cfg.measurement_key = cfg.subscriptions[0].local_key
            try:
                async def do():
                    await self._mgr.add_slot(PidFeedbackSlot(cfg), auto_start=False)
                self._run_async(do())
                self._refresh()
            except KeyError:
                QMessageBox.warning(self._parent, "重复", f'"{cfg.slot_id}" 已存在')
            except Exception as e:
                QMessageBox.critical(self._parent, "错误", f"添加失败: {e}")

    def _on_remove(self, slot_id: str):
        if QMessageBox.question(self._parent, "确认", f'删除反馈目标 "{slot_id}"?',
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self._mgr.remove_slot(slot_id)
            self._refresh()

    def _on_pause(self, slot_id: str):
        slot = self._mgr.get_slot(slot_id)
        if not slot: return
        st = slot.status.value
        if st == "idle":
            async def do_start(): await slot.start(); self._run_async(do_start())
        elif st == "running":
            async def do_pause(): await slot.pause(); self._run_async(do_pause())
        elif st == "paused":
            async def do_resume(): await slot.resume(); self._run_async(do_resume())
        else:
            return
        self._refresh()

    def get_active_count(self) -> tuple:
        infos = self._mgr.list_slots()
        total = len(infos)
        running = sum(1 for i in infos if i.status == "running")
        paused = sum(1 for i in infos if i.status == "paused")
        return running, paused, total
