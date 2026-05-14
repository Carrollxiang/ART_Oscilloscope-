"""
触发面板 — 控制器 (占位)

绑定到主窗口的触发控件。
目前硬件触发由 ART 卡负责, 软件触发为预留。
"""

from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtWidgets import QComboBox, QDoubleSpinBox, QCheckBox

logger = logging.getLogger(__name__)


class TriggerPanel:
    """
    触发面板控制器。

    绑定到 main_window 中触发 tab 的控件。
    """

    def __init__(self, source_combo: QComboBox, slope_combo: QComboBox,
                 level_spin: QDoubleSpinBox, mode_combo: QComboBox,
                 hw_check: QCheckBox):
        self._source = source_combo
        self._slope = slope_combo
        self._level = level_spin
        self._mode = mode_combo
        self._hw = hw_check

        # 硬件触发模式下软件触发控件禁用
        self._hw.toggled.connect(self._on_hw_toggle)
        self._on_hw_toggle(self._hw.isChecked())

    def _on_hw_toggle(self, hw_mode: bool):
        """硬件触发模式下禁用软件触发设置"""
        self._source.setEnabled(not hw_mode)
        self._slope.setEnabled(not hw_mode)
        self._level.setEnabled(not hw_mode)
        self._mode.setEnabled(not hw_mode)

    @property
    def trigger_source(self) -> int:
        return self._source.currentIndex()

    @property
    def trigger_level(self) -> float:
        return self._level.value()

    @property
    def trigger_slope(self) -> str:
        return "rising" if self._slope.currentIndex() == 0 else "falling"

    @property
    def trigger_mode(self) -> str:
        return ["auto", "normal", "single"][self._mode.currentIndex()]

    @property
    def is_hardware_trigger(self) -> bool:
        return self._hw.isChecked()
