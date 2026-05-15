"""
反馈管理面板 — 控制器

UI 上显示所有反馈 slot 的列表, 提供添加/编辑/删除操作。
集成 FeedbackManager 实现运行时动态增删改。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional, Callable

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QWidget,
    QTableWidgetItem,
    QHeaderView,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QListWidget,
    QPushButton,
    QSpacerItem,
    QSizePolicy,
    QMessageBox,
    QAbstractItemView,
)
from PyQt6.QtGui import QColor, QBrush

from scope.io import FeedbackManager, RpycFeedbackSlot, RpycSlotConfig, DataSubscription
from scope.io.feedback_slots.base import SlotConfig

logger = logging.getLogger(__name__)


class FeedbackDialog(QDialog):
    """添加/编辑反馈目标的对话框 (纯代码, 无 .ui 依赖)"""

    def __init__(self, parent=None, slot_id: str = ""):
        super().__init__(parent)
        self.setWindowTitle("反馈目标配置")
        self.setMinimumSize(420, 360)

        # 创建 UI
        layout = QVBoxLayout(self)

        # 表单
        form = QFormLayout()
        self.editId = QLineEdit()
        self.editHost = QLineEdit("127.0.0.1")
        self.editPort = QSpinBox()
        self.editPort.setRange(1, 65535)
        self.editPort.setValue(18861)
        self.editMethod = QLineEdit("exposed_update")

        form.addRow("标识", self.editId)
        form.addRow("主机", self.editHost)
        form.addRow("端口", self.editPort)
        form.addRow("远程方法", self.editMethod)

        # 连接池
        pool_layout = QHBoxLayout()
        self.editPoolMin = QSpinBox()
        self.editPoolMin.setRange(0, 10)
        self.editPoolMin.setValue(1)
        self.editPoolMax = QSpinBox()
        self.editPoolMax.setRange(1, 20)
        self.editPoolMax.setValue(4)
        pool_layout.addWidget(QLabel("最小"))
        pool_layout.addWidget(self.editPoolMin)
        pool_layout.addWidget(QLabel("最大"))
        pool_layout.addWidget(self.editPoolMax)
        form.addRow("连接池", pool_layout)

        layout.addLayout(form)

        # 订阅列表
        layout.addWidget(QLabel("订阅测量项"))
        self.subscriptionList = QListWidget()
        self.subscriptionList.setSelectionMode(
            QAbstractItemView.SelectionMode.MultiSelection
        )
        self._all_measurements = [
            "CH1_Vpp", "CH1_Vmax", "CH1_Vmin", "CH1_Vrms", "CH1_Freq",
            "CH2_Vpp", "CH2_Vmax", "CH2_Vmin", "CH2_Vrms", "CH2_Freq",
            "CH3_Vpp", "CH3_Freq",
            "CH4_Vpp", "CH4_Freq",
        ]
        for item in self._all_measurements:
            self.subscriptionList.addItem(item)
        layout.addWidget(self.subscriptionList)

        # 按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.btnOk = QPushButton("确定")
        self.btnCancel = QPushButton("取消")
        btn_layout.addWidget(self.btnOk)
        btn_layout.addWidget(self.btnCancel)
        layout.addLayout(btn_layout)

        # 如果编辑已有 slot
        if slot_id:
            self.editId.setText(slot_id)
            self.editId.setEnabled(False)

        self.btnCancel.clicked.connect(self.reject)
        self.btnOk.clicked.connect(self.accept)

    def get_config(self) -> RpycSlotConfig:
        """读取对话框中的配置"""
        subscriptions = []
        for item in self.subscriptionList.selectedItems():
            subscriptions.append(DataSubscription(local_key=item.text()))

        return RpycSlotConfig(
            slot_id=self.editId.text(),
            host=self.editHost.text(),
            port=self.editPort.value(),
            remote_method=self.editMethod.text(),
            pool_min=self.editPoolMin.value(),
            pool_max=self.editPoolMax.value(),
            subscriptions=subscriptions,
        )


class FeedbackPanel:
    """
    反馈管理面板控制器。

    绑定到 main_window.feedbackTable 和按钮。
    不继承 QWidget, 直接操作传入的控件。
    """

    def __init__(self, table_widget, btn_add, btn_edit, btn_remove,
                 feedback_manager: FeedbackManager,
                 status_callback: Optional[Callable[[], None]] = None):
        """
        table_widget: main_window.feedbackTable
        btn_add: 添加按钮
        btn_edit: 编辑按钮
        btn_remove: 删除按钮
        feedback_manager: FeedbackManager 实例
        status_callback: 更新状态栏的回调
        """
        self._table = table_widget
        self._btn_add = btn_add
        self._btn_edit = btn_edit
        self._btn_remove = btn_remove
        self._mgr = feedback_manager
        self._status_cb = status_callback

        self._setup_table()
        self._connect_buttons()

        # 定时刷新 UI 状态
        self._refresh_timer = QTimer()
        self._refresh_timer.setInterval(1000)
        self._refresh_timer.timeout.connect(self.refresh_table)
        self._refresh_timer.start()

    def _setup_table(self):
        headers = ["标识", "目标", "状态", "已发送", "错误"]
        self._table.setColumnCount(len(headers))
        self._table.setHorizontalHeaderLabels(headers)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._table.setEditTriggers(QHeaderView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(
            QHeaderView.SelectionBehavior.SelectRows
        )

    def _connect_buttons(self):
        self._btn_add.clicked.connect(self._on_add)
        self._btn_edit.clicked.connect(self._on_edit)
        self._btn_remove.clicked.connect(self._on_remove)

    def refresh_table(self):
        """刷新表格显示所有 slot 的最新状态"""
        infos = self._mgr.list_slots()
        self._table.setRowCount(len(infos))

        for row, info in enumerate(infos):
            self._table.setItem(row, 0, QTableWidgetItem(info.slot_id))

            target = f"{info.target}"
            if info.protocol != "null":
                target = f"rpyc://{info.target}"
            self._table.setItem(row, 1, QTableWidgetItem(target))

            status_item = QTableWidgetItem(info.status)
            if info.status == "running":
                status_item.setForeground(QBrush(QColor("#00FF00")))
            elif info.status == "error":
                status_item.setForeground(QBrush(QColor("#FF0000")))
            else:
                status_item.setForeground(QBrush(QColor("#888888")))
            self._table.setItem(row, 2, status_item)

            self._table.setItem(row, 3, QTableWidgetItem(str(info.sent_count)))
            err_text = info.last_error[:20] if info.last_error else str(info.error_count)
            self._table.setItem(row, 4, QTableWidgetItem(err_text))

        if self._status_cb:
            self._status_cb()

    def _on_add(self):
        """打开添加对话框 (同步, asyncio.run 执行异步操作)"""
        dialog = FeedbackDialog(self._table)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            config = dialog.get_config()
            if not config.slot_id:
                QMessageBox.warning(self._table, "警告", "请输入标识")
                return

            try:
                async def do_add():
                    slot = RpycFeedbackSlot(config)
                    await self._mgr.add_slot(slot)
                asyncio.run(do_add())
                self.refresh_table()
                logger.info(f"反馈目标 '{config.slot_id}' 已添加")
            except KeyError:
                QMessageBox.warning(
                    self._table, "重复",
                    f'标识 "{config.slot_id}" 已存在'
                )
            except Exception as e:
                QMessageBox.critical(
                    self._table, "错误",
                    f"添加失败: {e}"
                )

    def _on_edit(self):
        """编辑选中的 slot"""
        row = self._table.currentRow()
        if row < 0:
            QMessageBox.information(self._table, "提示", "请先选择一个反馈目标")
            return

        slot_id = self._table.item(row, 0).text()
        slot = self._mgr.get_slot(slot_id)
        if not slot:
            return

        dialog = FeedbackDialog(self._table, slot_id=slot_id)
        if hasattr(slot, '_rpyc_config'):
            cfg = slot._rpyc_config
            dialog.editHost.setText(cfg.host)
            dialog.editPort.setValue(cfg.port)
            dialog.editMethod.setText(cfg.remote_method)
            dialog.editPoolMin.setValue(cfg.pool_min)
            dialog.editPoolMax.setValue(cfg.pool_max)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_config = dialog.get_config()
            try:
                async def do_reconfig():
                    await slot.reconfigure(new_config)
                asyncio.run(do_reconfig())
                self.refresh_table()
                logger.info(f"反馈目标 '{slot_id}' 已更新")
            except Exception as e:
                QMessageBox.critical(self._table, "错误", f"更新失败: {e}")

    def _on_remove(self):
        """删除选中的 slot"""
        row = self._table.currentRow()
        if row < 0:
            QMessageBox.information(self._table, "提示", "请先选择一个反馈目标")
            return

        slot_id = self._table.item(row, 0).text()
        reply = QMessageBox.question(
            self._table, "确认删除",
            f'确定要删除反馈目标 "{slot_id}"?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._mgr.remove_slot(slot_id)
            self.refresh_table()
            logger.info(f"反馈目标 '{slot_id}' 已删除")

    def get_active_count(self) -> tuple[int, int]:
        """返回 (运行中, 总数)"""
        infos = self._mgr.list_slots()
        total = len(infos)
        running = sum(1 for i in infos if i.status == "running")
        return running, total
