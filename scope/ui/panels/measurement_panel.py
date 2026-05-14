"""
测量读数面板 — 控制器

显示实时测量值: Vpp, Freq, Vrms, 占空比 等。
数据由 AnalysisResult.measurements 驱动更新。
"""

from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget, QTableWidgetItem, QHeaderView

from scope.model.enums import MeasurementId

logger = logging.getLogger(__name__)


class MeasurementPanel:
    """
    测量面板控制器。

    绑定到 main_window.measurementTable。
    不继承 QWidget, 直接操作传入的 QTableWidget。
    """

    # 标准测量项列表 (显示名, key, 单位)
    STANDARD_MEASUREMENTS: list[tuple[str, str, str]] = [
        ("峰峰值", "Vpp", "V"),
        ("最大值", "Vmax", "V"),
        ("最小值", "Vmin", "V"),
        ("有效值", "Vrms", "V"),
        ("频率", "Freq", "Hz"),
        ("周期", "Period", "s"),
        ("占空比", "DutyCycle", "%"),
        ("正脉宽", "PosWidth", "s"),
        ("负脉宽", "NegWidth", "s"),
    ]

    def __init__(self, table_widget):
        """
        table_widget: main_window.measurementTable (QTableWidget)
        """
        self._table = table_widget
        self._setup_table()

    def _setup_table(self):
        """初始化表格头"""
        headers = ["测量项"] + [f"CH{i+1}" for i in range(4)]
        self._table.setColumnCount(len(headers))
        self._table.setHorizontalHeaderLabels(headers)
        self._table.setRowCount(len(self.STANDARD_MEASUREMENTS))

        for row, (label, key, unit) in enumerate(self.STANDARD_MEASUREMENTS):
            item = QTableWidgetItem(f"{label} ({unit})")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, 0, item)

        # 列宽
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self._table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._table.setEditTriggers(QHeaderView.EditTrigger.NoEditTriggers)

    def update_measurements(self, measurements: dict[str, float]):
        """
        用 AnalysisResult.measurements 更新表格。

        measurements 的 key 格式: "CH1_Vpp", "CH2_Freq", ...
        """
        for row, (label, key, unit) in enumerate(self.STANDARD_MEASUREMENTS):
            for ch in range(4):
                full_key = f"CH{ch+1}_{key}"
                value = measurements.get(full_key)
                col = ch + 1
                if value is not None:
                    text = self._format_value(value, unit)
                    item = QTableWidgetItem(text)
                else:
                    item = QTableWidgetItem("—")
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._table.setItem(row, col, item)

    def _format_value(self, value: float, unit: str) -> str:
        """智能格式化: 自动选择合适的小数位"""
        if abs(value) >= 100:
            return f"{value:.1f}"
        elif abs(value) >= 1:
            return f"{value:.3f}"
        elif abs(value) >= 0.001:
            return f"{value:.6f}"
        else:
            return f"{value:.3e}"
