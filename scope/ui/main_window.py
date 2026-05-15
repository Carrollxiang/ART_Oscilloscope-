"""
主窗口控制器 — 整合所有面板和背后数据流

前端 (.ui 文件) 与 后端 (控制器代码) 的桥梁。
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
from PyQt6 import uic
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QMainWindow, QMessageBox, QTableWidgetItem

from scope.model import AnalysisResult
from scope.io import FeedbackManager
from .waveform_view import WaveformView
from .panels.channel_panel import ChannelPanel
from .panels.measurement_panel import MeasurementPanel
from .panels.feedback_panel import FeedbackPanel, FeedbackDialog
from .panels.device_panel import DevicePanel

logger = logging.getLogger(__name__)

UI_PATH = "scope/ui/main_window.ui"


class MainWindow(QMainWindow):
    """
    示波器主窗口。

    架构:
      - main_window.ui 定义布局
      - 子控件通过 objectName 访问 (uic.loadUi 自动绑定)
      - 各 panel 控制器用组合而非继承
      - 数据流: AnalysisResult → update_display()
    """

    # 跨线程信号: 采集线程 → UI 线程
    data_received = pyqtSignal(object)
    # ART 配置变更信号: 对话框确认 → ScopeApp 重建设备
    # ART 配置变更信号: 设备面板确认 → ScopeApp 重建设备
    art_config_applied = pyqtSignal(dict, object)

    def __init__(self, feedback_manager: Optional[FeedbackManager] = None):
        super().__init__()

        # ── 加载 UI ──
        uic.loadUi(UI_PATH, self)

        # ── 波形视图 (替换 waveformContainer) ──
        self.waveform = WaveformView(self.waveformContainer, channel_count=4)

        # ── 通道面板 (替换 channelList) ──
        self.channel_panel = ChannelPanel(channel_count=4)
        self._embed_widget(self.tabChannels.layout(), self.channel_panel)
        self.channel_panel.channel_changed.connect(self._on_channel_changed)

        # 初始可见性: 默认打开 CH1/CH2
        for ch in range(4):
            visible = self.channel_panel.is_channel_enabled(ch)
            self.waveform.set_channel_visible(ch, visible)

        # 图例点击 → 同步通道面板复选框
        self.waveform._on_visible_changed = self._on_legend_toggle

        # ── 设备面板 (替换触发) ──
        self.device_panel = DevicePanel()
        self._embed_widget(self.tabDevice.layout(), self.device_panel)
        self.device_panel.config_applied.connect(self._on_device_config)

        # ── 测量面板 (动态行) ──
        self.measure_panel = MeasurementPanel(self.tabMeasurements)

        # ── 反馈面板 ──
        self._feedback_mgr = feedback_manager or FeedbackManager()
        self.feedback_panel = FeedbackPanel(
            table_widget=self.feedbackTable,
            btn_add=self.btnAddFeedback,
            btn_edit=self.btnEditFeedback,
            btn_remove=self.btnRemoveFeedback,
            btn_pause=self.btnPauseFeedback,
            feedback_manager=self._feedback_mgr,
            measurement_panel=self.measure_panel,
            status_callback=self._update_status_bar,
        )

        # ── 初始化 MeasurementBar / StatusBar ──
        self._update_status_bar()

        # ── 跨线程数据信号 ──
        self.data_received.connect(self.update_display)

        # ── 信号连接 ──
        self._connect_actions()

        logger.info("MainWindow 初始化完成")

    def _on_device_config(self, params: dict, config: DeviceConfig):
        """设备面板 → 转发配置到 ScopeApp。"""
        logger.info(f"设备配置已应用: {params}")
        self.art_config_applied.emit(params, config)

    def _on_legend_toggle(self, ch: int, visible: bool):
        """图例点击切换时, 同步通道面板的复选框。"""
        # ChannelPanel 的复选框切换会触发 channel_changed 信号
        # 我们直接设置复选框状态, 但不额外 emit 事件
        block = self.channel_panel._controls[ch]["enable"].blockSignals(True)
        self.channel_panel._controls[ch]["enable"].setChecked(visible)
        self.channel_panel._controls[ch]["enable"].blockSignals(block)

    # ── 数据更新接口 (由采集循环调用) ─────────────────────────

    def update_display(self, result: AnalysisResult):
        """
        用一次采集结果更新所有显示。

        采集线程安全调用 (通过 Signal/Slot 桥接)。
        """
        # 1. 更新波形 (可见性由 ChannelPanel 复选框控制)
        for ch_name, ch_data in result.channels.items():
            ch_idx = int(ch_name.replace("CH", "")) - 1
            visible = self.waveform.is_channel_visible(ch_idx)
            color = self.channel_panel.get_channel_color(ch_idx)

            self.waveform.update_waveform(
                ch=ch_idx,
                time_axis=ch_data.time_axis,
                data=ch_data.raw,
                enabled=visible,
                color=color,
            )

        # 2. 更新触发标记
        self.waveform.set_trigger_position(
            result.trigger.trigger_position
        )

        # 3. 更新测量面板 (带时间窗口和原始数据)
        self.measure_panel.update_from_result(result)

        # 4. 更新状态栏
        self._update_status_bar(result)

    # ── 状态栏 ────────────────────────────────────────────────

    def _update_status_bar(self, result: Optional[AnalysisResult] = None):
        """更新底部信息条"""
        if result:
            # 取第一个通道的采样率
            sample_rate = "—"
            for ch_data in result.channels.values():
                sample_rate = self._format_sample_rate(ch_data.sample_rate)
                break
            self.statusSampling.setText(f"采样率: {sample_rate}")
            self.statusFrames.setText(f"帧 #: {result.sequence_num}")
            self.statusTrigger.setText(
                f"触发: {result.trigger.trigger_type}"
                f" @ {result.trigger.trigger_level:.2f}V"
            )

        # 反馈状态
        running, paused, total = self.feedback_panel.get_active_count()
        status_parts = []
        if running:
            status_parts.append(f"{running} 运行")
        if paused:
            status_parts.append(f"{paused} 暂停")
        if not running and not paused:
            status_parts.append("0 活跃")
        self.statusFeedback.setText(f"反馈: {' | '.join(status_parts)}/{total}")

    # ── 内部 ──────────────────────────────────────────────────

    def _connect_actions(self):
        """连接菜单动作"""
        self.actionQuit.triggered.connect(self.close)
        self.actionAbout.triggered.connect(self._show_about)
        self.actionResetLayout.triggered.connect(
            lambda: logger.info("重置布局 (待实现)")
        )

    def _show_about(self):
        QMessageBox.about(
            self,
            "关于 数字示波器",
            "数字示波器 v0.1\n"
            "基于 PyQt6 + pyqtgraph + rpyc\n"
            "驱动 ART 多通道 USB 采集卡",
        )

    def _on_channel_changed(self, ch: int, key: str, value):
        """通道参数变化回调"""
        logger.debug(f"通道 CH{ch+1} {key} → {value}")

        if key == "enabled":
            # 切换波形显示/隐藏
            self.waveform.set_channel_visible(ch, bool(value))
        # TODO: scale/coupling/probe 更新设备配置

    def _embed_widget(self, layout, widget):
        """将 widget 填入指定 layout"""
        if layout is None:
            return
        # 清空布局
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        layout.addWidget(widget)

    @staticmethod
    def _format_sample_rate(rate: float) -> str:
        if rate >= 1_000_000:
            return f"{rate/1e6:.1f} MSa/s"
        elif rate >= 1_000:
            return f"{rate/1e3:.1f} kSa/s"
        else:
            return f"{rate:.0f} Sa/s"
