"""
反馈管理面板 — 可折叠卡片式 UI

每个反馈 slot 显示为一张可折叠卡片:
  折叠: [▶] 名称  模式  状态  已发送 [继续] [编辑] [删除]
  展开: + 详细 PID 参数 / 设定值 / 订阅项
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
    QListWidget, QListWidgetItem, QAbstractItemView,
    QDialog, QFormLayout, QGroupBox, QDialogButtonBox,
    QMessageBox, QSizePolicy,
)
from PyQt6.QtGui import QColor, QBrush

from scope.io import FeedbackManager, RpycFeedbackSlot, RpycSlotConfig, DataSubscription
from scope.io.feedback_slots.base import SlotConfig

logger = logging.getLogger(__name__)

# ── 卡片配色 ──────────────────────────────────────────────────

CARD_BG = "#1a1a2e"
CARD_BORDER = "#333"
CARD_HDR_BG = "#222244"
STATUS_COLORS = {
    "running": "#00FF00",
    "paused": "#FFAA00",
    "error": "#FF4444",
    "idle": "#888888",
}


# ── 添加/编辑对话框 ───────────────────────────────────────────

class FeedbackDialog(QDialog):
    """反馈目标配置对话框: 连接 + PID + 订阅"""

    FEEDBACK_MODES = [
        ("standard", "标准 PID"),
        ("fast", "快速 PID"),
        ("slow", "慢速 PID"),
    ]

    # 默认 PID 增益 (根据模式不同)
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
        self.setMinimumSize(560, 520)
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

        # ── 反馈模式 & 设定值 ──
        g2 = QGroupBox("反馈参数")
        g2_layout = QVBoxLayout(g2)
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("反馈模式"))
        self.cmbMode = QComboBox()
        for code, label in self.FEEDBACK_MODES:
            self.cmbMode.addItem(label, code)
        self.cmbMode.currentIndexChanged.connect(self._on_mode_change)
        row1.addWidget(self.cmbMode)
        row1.addWidget(QLabel("设定值"))
        self.spinSetpoint = QDoubleSpinBox()
        self.spinSetpoint.setRange(-1e6, 1e6); self.spinSetpoint.setDecimals(4)
        self.spinSetpoint.setValue(0.0)
        row1.addWidget(self.spinSetpoint)
        g2_layout.addLayout(row1)

        # PID 增益: 紧凑的 3×10 网格
        g2_layout.addWidget(QLabel("PID 增益数组 (×10):"))
        pid_grid = QGridLayout()
        pid_grid.setSpacing(2)
        self._pid_spins: dict[str, list[QDoubleSpinBox]] = {"Kp": [], "Ki": [], "Kd": []}
        for col, key in enumerate(["Kp", "Ki", "Kd"]):
            pid_grid.addWidget(QLabel(key), col + 1, 0)
            for i in range(10):
                sp = QDoubleSpinBox()
                sp.setRange(-1e6, 1e6); sp.setDecimals(4)
                sp.setValue(1.0 if key == "Kp" else 0.0)
                sp.setFixedWidth(56)
                pid_grid.addWidget(sp, col + 1, i + 1)
                self._pid_spins[key].append(sp)
        pid_grid.addWidget(QLabel("输出限幅"), 0, 0)
        self.spinOutMin = QDoubleSpinBox(); self.spinOutMin.setRange(-1e6, 0); self.spinOutMin.setValue(-100)
        self.spinOutMax = QDoubleSpinBox(); self.spinOutMax.setRange(0, 1e6); self.spinOutMax.setValue(100)
        self.spinOutMin.setFixedWidth(70); self.spinOutMax.setFixedWidth(70)
        pid_grid.addWidget(QLabel("min"), 0, 1); pid_grid.addWidget(self.spinOutMin, 0, 2)
        pid_grid.addWidget(QLabel("max"), 0, 3); pid_grid.addWidget(self.spinOutMax, 0, 4)
        g2_layout.addLayout(pid_grid)
        layout.addWidget(g2)

        # ── 订阅列表 ──
        layout.addWidget(QLabel("订阅测量项:"))
        self.subList = QListWidget()
        self.subList.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        for item in self._meas_items:
            display = f"{item['name']} ({item['channel']}_{item['meas_key']})"
            self.subList.addItem(display)
        layout.addWidget(self.subList)

        # ── 按钮 ──
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _on_mode_change(self, idx: int):
        """切换模式时更新 PID 默认值。"""
        mode = self.cmbMode.itemData(idx)
        defaults = self.DEFAULT_PID.get(mode)
        if not defaults:
            return
        kp, ki, kd, omin, omax = defaults
        for sp in self._pid_spins["Kp"]:
            sp.setValue(kp)
        for sp in self._pid_spins["Ki"]:
            sp.setValue(ki)
        for sp in self._pid_spins["Kd"]:
            sp.setValue(kd)
        self.spinOutMin.setValue(omin)
        self.spinOutMax.setValue(omax)

    def _load_config(self, cfg: RpycSlotConfig):
        """用已有配置回填表单。"""
        self.editId.setText(cfg.slot_id)
        self.editHost.setText(cfg.host)
        self.editPort.setValue(cfg.port)
        self.editMethod.setText(cfg.remote_method)
        self.spinPoolMin.setValue(cfg.pool_min)
        self.spinPoolMax.setValue(cfg.pool_max)

        for i in range(self.cmbMode.count()):
            if self.cmbMode.itemData(i) == cfg.feedback_mode:
                self.cmbMode.setCurrentIndex(i)
                break
        self.spinSetpoint.setValue(cfg.setpoint)

        for i in range(10):
            if i < len(cfg.pid_kp): self._pid_spins["Kp"][i].setValue(cfg.pid_kp[i])
            if i < len(cfg.pid_ki): self._pid_spins["Ki"][i].setValue(cfg.pid_ki[i])
            if i < len(cfg.pid_kd): self._pid_spins["Kd"][i].setValue(cfg.pid_kd[i])
        self.spinOutMin.setValue(cfg.pid_output_min)
        self.spinOutMax.setValue(cfg.pid_output_max)

    def get_config(self) -> RpycSlotConfig:
        """读取表单 → RpycSlotConfig。"""
        subs = []
        for item in self.subList.selectedItems():
            idx = self.subList.row(item)
            if 0 <= idx < len(self._meas_items):
                m = self._meas_items[idx]
                key = f"{m['channel']}_{m['meas_key']}"
                subs.append(DataSubscription(local_key=key, remote_key=m['name']))

        return RpycSlotConfig(
            slot_id=self.editId.text(),
            host=self.editHost.text(),
            port=self.editPort.value(),
            remote_method=self.editMethod.text(),
            pool_min=self.spinPoolMin.value(),
            pool_max=self.spinPoolMax.value(),
            feedback_mode=self.cmbMode.currentData(),
            setpoint=self.spinSetpoint.value(),
            pid_kp=[s.value() for s in self._pid_spins["Kp"]],
            pid_ki=[s.value() for s in self._pid_spins["Ki"]],
            pid_kd=[s.value() for s in self._pid_spins["Kd"]],
            pid_output_min=self.spinOutMin.value(),
            pid_output_max=self.spinOutMax.value(),
            subscriptions=subs,
        )


# ── 单张卡片 ───────────────────────────────────────────────────

class FeedbackCard(QFrame):
    """单个 feedback slot 的可折叠卡片。"""

    def __init__(self, slot_info,
                 on_pause=None, on_edit=None, on_remove=None):
        super().__init__()
        self._slot_id = slot_info.slot_id
        self._expanded = False
        self._on_pause = on_pause
        self._on_edit = on_edit
        self._on_remove = on_remove

        self.setStyleSheet(f"FeedbackCard {{ background: {CARD_BG}; border: 1px solid {CARD_BORDER}; border-radius: 4px; margin: 2px; }}")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(8, 4, 8, 4)
        self._layout.setSpacing(4)

        # ── 头部 (始终显示) ──
        self._header = self._build_header(slot_info)
        self._layout.addWidget(self._header)

        # ── 详情 (折叠) ──
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
        self._arrow.setStyleSheet("color: #888; font-size: 11px;")
        row.addWidget(self._arrow)

        # 名称
        name = QLabel(info.slot_id)
        name.setStyleSheet("font-weight: bold; color: #ddd; font-size: 12px;")
        row.addWidget(name)

        # 模式
        mode_label = QLabel(f"mode: {getattr(info, 'feedback_mode', 'standard')}")
        mode_label.setStyleSheet("color: #888; font-size: 10px; padding: 0 6px;")
        row.addWidget(mode_label)

        # 状态
        sc = STATUS_COLORS.get(info.status, "#888")
        status_label = QLabel(f"● {info.status}")
        status_label.setStyleSheet(f"color: {sc}; font-size: 11px;")
        row.addWidget(status_label)

        # 已发送
        sent_label = QLabel(f"sent: {info.sent_count}")
        sent_label.setStyleSheet("color: #888; font-size: 10px;")
        row.addWidget(sent_label)

        row.addStretch()

        # 暂停/继续
        self._btnPause = QPushButton("继续" if info.status == "paused" else "暂停")
        self._btnPause.setFixedSize(48, 22)
        self._btnPause.setStyleSheet("font-size: 10px;")
        self._btnPause.clicked.connect(lambda: self._on_pause(self._slot_id) if self._on_pause else None)
        row.addWidget(self._btnPause)

        # 编辑
        btnEdit = QPushButton("编辑")
        btnEdit.setFixedSize(40, 22)
        btnEdit.setStyleSheet("font-size: 10px;")
        btnEdit.clicked.connect(lambda: self._on_edit(self._slot_id) if self._on_edit else None)
        row.addWidget(btnEdit)

        # 删除
        btnDel = QPushButton("✕")
        btnDel.setFixedSize(24, 22)
        btnDel.setStyleSheet("font-size: 10px; color: #F44;")
        btnDel.clicked.connect(lambda: self._on_remove(self._slot_id) if self._on_remove else None)
        row.addWidget(btnDel)

        # 点击头部切换展开/折叠
        hdr.mousePressEvent = lambda ev: self._toggle()
        for w in [name, mode_label, status_label, sent_label]:
            w.mousePressEvent = lambda ev: self._toggle()

        return hdr

    def _build_detail(self, info) -> QWidget:
        det = QWidget()
        det.setStyleSheet("background: transparent;")
        v = QVBoxLayout(det)
        v.setContentsMargins(6, 4, 6, 4)

        # 目标
        target = QLabel(f"🎯 目标: {info.target}")
        target.setStyleSheet("color: #aaa; font-size: 11px;")
        v.addWidget(target)

        # 设定值 + 模式
        mode = getattr(info, 'feedback_mode', 'standard')
        sp = getattr(info, 'setpoint', 0.0)
        v.addWidget(QLabel(f"模式: {mode}   设定值: {sp}"))

        # PID 摘要
        pid_kp = getattr(info, 'pid_kp', [1.0]*10)
        pid_ki = getattr(info, 'pid_ki', [0.0]*10)
        pid_kd = getattr(info, 'pid_kd', [0.0]*10)
        v.addWidget(QLabel(
            f"PID: Kp=[{pid_kp[0]:.2f} … {pid_kp[-1]:.2f}]  "
            f"Ki=[{pid_ki[0]:.4f} … {pid_ki[-1]:.4f}]  "
            f"Kd=[{pid_kd[0]:.4f} … {pid_kd[-1]:.4f}]"
        ))

        # 订阅
        subs = info.subscriptions or []
        v.addWidget(QLabel(f"订阅 ({len(subs)}项): {', '.join(subs[:5])}"))

        # 错误信息
        if info.last_error:
            err = QLabel(f"⚠ {info.last_error}")
            err.setStyleSheet("color: #F44; font-size: 10px;")
            v.addWidget(err)

        return det

    def _toggle(self):
        self._expanded = not self._expanded
        self._detail.setVisible(self._expanded)
        self._arrow.setText("▼" if self._expanded else "▶")

    def update_info(self, info):
        """刷新卡片数据。"""
        # 更新暂停按钮文字
        self._btnPause.setText("继续" if info.status == "paused" else "暂停")
        # 重建 detail
        old = self._detail
        self._detail = self._build_detail(info)
        self._detail.setVisible(self._expanded)
        self._layout.replaceWidget(old, self._detail)
        old.deleteLater()


# ── 管理面板 ───────────────────────────────────────────────────

class FeedbackPanel:
    """
    反馈管理面板 — 可折叠卡片列表。

    绑定到 main_window 的反馈 Tab。
    """

    def __init__(self, parent_widget: QWidget,
                 feedback_manager: FeedbackManager,
                 measurement_panel=None,
                 status_callback: Optional[Callable[[], None]] = None):
        self._parent = parent_widget
        self._mgr = feedback_manager
        self._meas_panel = measurement_panel
        self._status_cb = status_callback
        self._cards: dict[str, FeedbackCard] = {}
        self._notified_auto_pause: set[str] = set()

        self._build_ui()

        # 定时刷新
        self._timer = QTimer()
        self._timer.setInterval(2000)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()

    def _build_ui(self):
        layout = self._parent.layout() or QVBoxLayout(self._parent)
        self._parent.setLayout(layout)

        # 清空
        while layout.count():
            item = layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()

        # 标题 + 添加按钮行
        top = QHBoxLayout()
        top.addWidget(QLabel("反馈目标"))
        top.addStretch()
        btn_add = QPushButton("+ 添加")
        btn_add.setStyleSheet("QPushButton { color: #0C0; }")
        btn_add.clicked.connect(self._on_add)
        top.addWidget(btn_add)
        layout.addLayout(top)

        # 卡片列表 (滚动)
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
        card = FeedbackCard(
            info,
            on_pause=self._on_pause,
            on_edit=self._on_edit,
            on_remove=self._on_remove,
        )
        self._cards[info.slot_id] = card
        self._card_layout.insertWidget(self._card_layout.count() - 1, card)

    def refresh(self):
        """刷新所有卡片。"""
        infos = self._mgr.list_slots()
        current_ids = set(self._cards.keys())
        new_ids = {i.slot_id for i in infos}

        # 移除已删除
        for sid in current_ids - new_ids:
            if sid in self._cards:
                card = self._cards.pop(sid)
                self._card_layout.removeWidget(card)
                card.deleteLater()

        # 添加新增
        for info in infos:
            if info.slot_id not in self._cards:
                self._add_card(info)

        # 更新现有
        for info in infos:
            if info.slot_id in self._cards:
                self._cards[info.slot_id].update_info(info)

        # 自动暂停弹窗
        for info in infos:
            if info.auto_paused and info.slot_id not in self._notified_auto_pause:
                self._notified_auto_pause.add(info.slot_id)
                QMessageBox.warning(
                    self._parent, "反馈自动暂停",
                    f"'{info.slot_id}' 连续 {info.consecutive_errors} 次失败, 已暂停。\n"
                    f"最后错误: {info.last_error}",
                )

        if self._status_cb:
            self._status_cb()

    def _get_meas_items(self):
        if self._meas_panel:
            return self._meas_panel.get_subscriptions()
        return []

    def _on_add(self):
        dlg = FeedbackDialog(self._parent, measurement_items=self._get_meas_items())
        if dlg.exec() == QDialog.DialogCode.Accepted:
            config = dlg.get_config()
            try:
                async def do_add():
                    s = RpycFeedbackSlot(config)
                    await self._mgr.add_slot(s)
                asyncio.run(do_add())
                self.refresh()
            except KeyError:
                QMessageBox.warning(self._parent, "重复", f'"{config.slot_id}" 已存在')
            except Exception as e:
                QMessageBox.critical(self._parent, "错误", f"添加失败: {e}")

    def _on_edit(self, slot_id: str):
        slot = self._mgr.get_slot(slot_id)
        if not slot:
            return
        cfg = getattr(slot, '_rpyc_config', None)
        dlg = FeedbackDialog(self._parent, slot_id=slot_id,
                             measurement_items=self._get_meas_items(),
                             existing_config=cfg)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            try:
                new_cfg = dlg.get_config()
                async def do_reconfig():
                    await slot.reconfigure(new_cfg)
                asyncio.run(do_reconfig())
                self.refresh()
            except Exception as e:
                QMessageBox.critical(self._parent, "错误", f"更新失败: {e}")

    def _on_remove(self, slot_id: str):
        reply = QMessageBox.question(
            self._parent, "确认", f'删除 "{slot_id}"?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self._mgr.remove_slot(slot_id)
            self.refresh()

    def _on_pause(self, slot_id: str):
        slot = self._mgr.get_slot(slot_id)
        if not slot:
            return
        if slot.status.value == "running":
            asyncio.run(slot.pause())
        elif slot.status.value == "paused":
            asyncio.run(slot.resume())
        self.refresh()

    def get_active_count(self) -> tuple:
        infos = self._mgr.list_slots()
        total = len(infos)
        running = sum(1 for i in infos if i.status == "running")
        paused = sum(1 for i in infos if i.status == "paused")
        return running, paused, total
