"""
反馈面板 — 管理反馈插槽的 UI 组件

提供反馈插槽的可视化管理界面。
"""

from __future__ import annotations

import logging
from typing import Optional, Callable, Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QMessageBox,
)

from scope.model.enums import SlotStatus

logger = logging.getLogger(__name__)


class FeedbackPanel(QWidget):
    """
    反馈面板 — 显示和管理反馈插槽。
    
    功能:
      - 显示所有反馈插槽列表
      - 每个插槽显示: 名称、状态、开始/暂停/停止按钮
      - 支持添加新插槽
    """

    # 信号: 请求添加新反馈
    add_feedback_requested = pyqtSignal()

    def __init__(
        self,
        parent_widget: QWidget = None,
        feedback_manager=None,
        measurement_panel=None,
        status_callback: Optional[Callable] = None,
        async_loop=None,
    ):
        super().__init__(parent_widget)
        self._feedback_mgr = feedback_manager
        self._measurement_panel = measurement_panel
        self._status_callback = status_callback
        self._async_loop = async_loop
        self._slots_ui = {}  # slot_id -> UI widget

        self._setup_ui()

    def _setup_ui(self):
        """构建 UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # 标题
        title = QLabel("反馈插槽")
        title.setStyleSheet("color: #888; font-size: 11px; font-weight: bold;")
        layout.addWidget(title)

        # 滚动区 (预留，目前为空)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet("QScrollArea { border: none; }")

        self._container = QWidget()
        self._container_layout = QVBoxLayout(self._container)
        self._container_layout.setContentsMargins(0, 0, 0, 0)
        self._container_layout.setSpacing(4)
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
        self._placeholder = QLabel("暂无反馈插槽")
        self._placeholder.setStyleSheet("color: #666; font-style: italic; padding: 10px;")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._container_layout.insertWidget(0, self._placeholder)

    def _on_add(self):
        """添加新反馈插槽"""
        dlg = FeedbackDialog(self)
        dlg.exec()

    def get_active_count(self) -> tuple[int, int]:
        """返回 (running_count, total_count)"""
        if not self._feedback_mgr:
            return 0, 0
        return self._feedback_mgr.get_active_count()

    def refresh_slots(self):
        """刷新 worker 列表显示"""
        # TODO: 根据 feedback_manager.list_workers() 更新 UI
        pass


class FeedbackDialog(QMessageBox):
    """反馈配置对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("添加反馈")
        self.setText("反馈配置功能开发中...")
        self.setStandardButtons(QMessageBox.StandardButton.Ok)
