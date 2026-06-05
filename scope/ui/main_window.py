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
from PyQt6.QtWidgets import QMainWindow, QVBoxLayout, QMessageBox, QTableWidgetItem

from scope.model import RawFrame
from scope.runtime import FittedSnapshot
from scope.io import FeedbackManager
from .waveform_view import WaveformView
from .panels.channel_panel import ChannelPanel
from .panels.measurement_panel import MeasurementPanel
from PyQt6.QtWidgets import QFileDialog

from .panels.feedback_panel import FeedbackPanel, FeedbackDialog
from .panels.device_panel import DevicePanel
from .mini_chart import MiniChartWidget
from scope.config.settings import ConfigManager

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

    # 跨线程信号 (旧 data_received 已移除, 统一走 UIBridge)
    # ART 配置变更信号: 设备面板确认 → ScopeApp 重建设备
    art_config_applied = pyqtSignal(dict, object)

    def __init__(self, feedback_manager: Optional[FeedbackManager] = None,
                 async_loop=None):
        super().__init__()
        self._async_loop = async_loop

        # ── 加载 UI ──
        uic.loadUi(UI_PATH, self)

        # ── 波形视图 (替换 waveformContainer) ──
        self.waveform = WaveformView(self.waveformContainer, channel_count=16)

        # ── 通道面板 (替换 channelList) ──
        self.channel_panel = ChannelPanel(channel_count=16)
        self._embed_widget(self.tabChannels.layout(), self.channel_panel)
        self.channel_panel.channel_changed.connect(self._on_channel_changed)

        # 初始可见性: 默认打开全部通道 (由 ChannelPanel 的复选框控制)
        for ch in range(16):
            visible = self.channel_panel.is_channel_enabled(ch)
            self.waveform.set_channel_visible(ch, visible)

        # 图例点击 → 同步通道面板复选框
        self.waveform._on_visible_changed = self._on_legend_toggle

        # 强制垂直分割: 波形区 2/3, 底部 1/3
        cl = self.centralWidget().layout()
        if cl:
            cl.setStretch(0, 1)  # waveformContainer
            cl.setStretch(1, 1)  # bottomSplitter
        # 水平分割: 迷你图 1/4, 配置Tab 3/4
        bs = getattr(self, 'bottomSplitter', None)
        if bs:
            bs.setSizes([150, 450])

        # ── 迷你图 (左下角持久化) ──
        self.mini_chart = MiniChartWidget()
        # 填入 miniChartContainer 的布局
        mc_lay = self.miniChartContainer.layout() or QVBoxLayout(self.miniChartContainer)
        self.miniChartContainer.setLayout(mc_lay)
        while mc_lay.count():
            item = mc_lay.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        mc_lay.addWidget(self.mini_chart)

        # ── 设备面板 (替换触发) ──
        self.device_panel = DevicePanel()
        self._embed_widget(self.tabDevice.layout(), self.device_panel)
        self.device_panel.config_applied.connect(self._on_device_config)

        # ── 测量面板 (动态行) ──
        self.measure_panel = MeasurementPanel(self.tabMeasurements)

        # ── 反馈面板 ──
        self._feedback_mgr = feedback_manager or FeedbackManager()
        self.feedback_panel = FeedbackPanel(
            parent_widget=self.tabFeedback,
            feedback_manager=self._feedback_mgr,
            measurement_panel=self.measure_panel,
            status_callback=self._update_status_bar,
            async_loop=getattr(self, '_async_loop', None),
        )

        # ── 初始化 MeasurementBar / StatusBar ──
        self._update_status_bar()

        # ── 信号连接 ──
        self._connect_actions()

        logger.info("MainWindow 初始化完成")

    def _save_config(self):
        """保存当前配置到 JSON 文件。"""
        path, _ = QFileDialog.getSaveFileName(
            self, "保存配置", ConfigManager.default_filepath(),
            "JSON 配置 (*.json)")
        if path:
            ConfigManager.save_to_file(self, path)

    def _load_config(self):
        """从 JSON 文件加载配置。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "加载配置", ConfigManager.default_filepath(),
            "JSON 配置 (*.json)")
        if path:
            ok = ConfigManager.load_from_file(self, path)
            if ok:
                self._update_status_bar()

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

    # ── UIBridge 连接 (v0.4 数据面桥接) ─────────────────────

    def connect_ui_bridge(self, ui_bridge):
        """
        连接 UIBridge 的信号到 UI 更新槽函数。

        取代旧的 data_received → update_display 路径。
        """
        ui_bridge.signal_raw_frame.connect(self._on_ui_raw_frame)
        ui_bridge.signal_fitted.connect(self._on_ui_fitted)

    def _on_ui_raw_frame(self, frame: RawFrame):
        """原始帧更新 → 主波形。"""
        try:
            t = frame.time_axis()
            for ch in range(frame.n_channels):
                visible = self.waveform.is_channel_visible(ch)
                color = self.channel_panel.get_channel_color(ch)
                self.waveform.update_waveform(
                    ch=ch,
                    time_axis=t,
                    data=frame.data[ch],
                    enabled=visible,
                    color=color,
                )
            self._update_status_bar(frame)
        except Exception as e:
            logger.error(f"原始帧更新异常: {e}", exc_info=True)

    def _on_ui_fitted(self, fitted_snapshot: FittedSnapshot):
        """拟合结果更新 → 测量面板 + MiniChart。"""
        try:
            if hasattr(self, 'measure_panel'):
                self.measure_panel.update_from_fitted(fitted_snapshot)

            flat = fitted_snapshot.as_flat_dict()
            if flat and hasattr(self, 'mini_chart'):
                self.mini_chart.add_data(flat)
        except Exception as e:
            logger.error(f"拟合结果更新异常: {e}", exc_info=True)

    def _update_status_bar(self, frame: RawFrame = None):
        """更新底部信息条"""
        if frame:
            sample_rate = self._format_sample_rate(frame.sample_rate)
            self.statusSampling.setText(f"采样率: {sample_rate}")
            self.statusFrames.setText(f"帧 #: {frame.sequence_num}")
            self.statusTrigger.setText(f"触发: {frame.n_channels}ch")

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
        self.actionSaveConfig.triggered.connect(self._save_config)
        self.actionLoadConfig.triggered.connect(self._load_config)
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
            self.waveform.set_channel_visible(ch, bool(value))

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
