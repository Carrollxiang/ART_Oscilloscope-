"""
主窗口控制器 — 整合所有面板和背后数据流

前端 (.ui 文件) 与 后端 (控制器代码) 的桥梁。
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
from PyQt6 import uic
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QMainWindow, QVBoxLayout, QMessageBox, QTableWidgetItem

from scope.model import RawFrame
from scope.runtime import ConfigChange, FittedSnapshot
from scope.io import FeedbackManager
from scope.io.feedback_command import FeedbackCommand
from scope.hardware import DeviceConfig
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

    def __init__(self, feedback_manager: Optional[FeedbackManager] = None,
                 async_loop=None, event_bus=None):
        super().__init__()
        self._async_loop = async_loop
        self._event_bus = event_bus
        self._config_change_id = 0
        self._feedback_command_change_id = 0

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
        default_measurements = ConfigManager.load_default_measurements()
        self.measure_panel = MeasurementPanel(
            self.tabMeasurements,
            event_bus=event_bus,
            initial_measurements=default_measurements,
        )
        self.measure_panel.set_name_change_callback(
            lambda: self.feedback_panel.refresh_slots() if hasattr(self, 'feedback_panel') else None
        )

        # ── 反馈面板 ──
        self._feedback_mgr = feedback_manager or FeedbackManager()
        self.feedback_panel = FeedbackPanel(
            parent_widget=self.tabFeedback,
            feedback_manager=self._feedback_mgr,
            measurement_panel=self.measure_panel,
            status_callback=self._update_status_bar,
            async_loop=getattr(self, '_async_loop', None),
            event_bus=self._event_bus,
            command_id_provider=self._next_feedback_command_id,
        )
        self._embed_widget(self.tabFeedback.layout(), self.feedback_panel)

        # ── 初始化 MeasurementBar / StatusBar ──
        self._update_status_bar()

        # ── 信号连接 ──
        self._connect_actions()

        # 订阅测量项删除事件
        if self._event_bus:
            self._measurement_remove_queue = self._event_bus.subscribe("measurement.remove")
            self._measurement_remove_timer = QTimer()
            self._measurement_remove_timer.timeout.connect(self._poll_measurement_remove)
            self._measurement_remove_timer.start(100)

        logger.info("MainWindow 初始化完成")

    def _save_config(self):
        """保存当前配置到 JSON 文件。"""
        path, _ = QFileDialog.getSaveFileName(
            self, "保存配置", ConfigManager.default_filepath(),
            "JSON 配置 (*.json)")
        if path:
            ConfigManager.save_to_file(self, path)

    def _load_config(self):
        """从 JSON 文件加载配置 — 回填 UI，可选发布控制面命令。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "加载配置", ConfigManager.default_filepath(),
            "JSON 配置 (*.json)")
        if not path:
            return

        result = ConfigManager.load_from_file(self, path)
        if not result:
            return

        # ── 设备配置 → 问用户是否应用到硬件 ──
        device_info = result.get("device")
        if device_info is not None and self._event_bus:
            reply = QMessageBox.question(
                self,
                "应用设备配置",
                "是否将设备配置应用到硬件？\n\n"
                "选择「是」会重建设备（当前采集会中断）。\n"
                "选择「否」只回填 UI，不生效到硬件。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._config_change_id += 1
                change = ConfigChange(
                    device_config=device_info["config"],
                    art_params=device_info["params"],
                    change_id=self._config_change_id,
                )
                self._event_bus.publish("config.change", change)
                logger.info("配置加载: 设备配置已发布到 config.change")

        # ── 反馈配置 → 走 EventBus 控制面 ──
        fw = result.get("feedback_workers")
        if fw is not None and self._event_bus:
            self._event_bus.publish(
                "feedback.worker.command",
                FeedbackCommand(
                    action="load_batch",
                    worker_id="_batch_",
                    config_list=fw,
                    change_id=self._next_feedback_command_id(),
                ),
            )
            logger.info("配置加载: 反馈 workers 配置已发布到 feedback.worker.command")

        self._update_status_bar()

    def _next_feedback_command_id(self) -> int:
        """为所有反馈控制入口分配同一条单调递增命令序列。"""
        self._feedback_command_change_id += 1
        return self._feedback_command_change_id

    def _on_device_config(self, params: dict, config: DeviceConfig):
        """设备面板 → 发布配置变更到 EventBus 控制面。"""
        if not self._event_bus:
            logger.error("无法应用设备配置: EventBus 未连接")
            return

        self._config_change_id += 1
        change = ConfigChange(
            device_config=config,
            art_params=params,
            change_id=self._config_change_id,
        )
        self._event_bus.publish("config.change", change)
        logger.info(f"设备配置变更已发布到 config.change: {params}")

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
        """拟合结果更新 → 测量面板 + MiniChart + 反馈面板。"""
        try:
            if hasattr(self, 'measure_panel'):
                self.measure_panel.update_from_fitted(fitted_snapshot)

            flat = fitted_snapshot.as_flat_dict()
            if flat and hasattr(self, 'mini_chart'):
                self.mini_chart.add_data(flat)
                self.mini_chart.refresh_now()
                if fitted_snapshot.sequence_num % 10 == 1:
                    logger.debug(f"MiniChart updated: {len(flat)} items, seq={fitted_snapshot.sequence_num}")

            # 事件驱动：每帧刷新反馈面板（零轮询）
            if hasattr(self, 'feedback_panel'):
                self.feedback_panel.refresh_slots()

        except Exception as e:
            logger.error(f"拟合结果更新异常: {e}", exc_info=True)

    def _poll_measurement_remove(self):
        """轮询测量项删除事件并清理 MiniChart"""
        if not hasattr(self, '_measurement_remove_queue'):
            return
        
        tag = self._measurement_remove_queue.get_nowait()
        while tag is not None:
            if hasattr(self, 'mini_chart'):
                self.mini_chart.remove_key(tag)
                logger.debug(f"MiniChart 已删除测量项: {tag}")
            tag = self._measurement_remove_queue.get_nowait()

    def _update_status_bar(self, frame: RawFrame = None):
        """更新底部信息条"""
        if frame:
            sample_rate = self._format_sample_rate(frame.sample_rate)
            self.statusSampling.setText(f"采样率: {sample_rate}")
            self.statusFrames.setText(f"帧 #: {frame.sequence_num}")
            self.statusTrigger.setText(f"触发: {frame.n_channels}ch")

        # 反馈状态
        running, total = self.feedback_panel.get_active_count()
        paused = total - running
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
