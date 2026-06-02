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

from scope.model import AnalysisResult
from scope.io import FeedbackManager
from .waveform_view import WaveformView
from .panels.channel_panel import ChannelPanel
from .panels.measurement_panel import MeasurementPanel
from PyQt6.QtWidgets import QFileDialog

from .panels.feedback_panel import FeedbackPanel, FeedbackDialog
from .panels.device_panel import DevicePanel
from .panels.scan_panel import ScanPanel
from .mini_chart import MiniChartWidget
from scope.config.settings import ConfigManager
from scope.scan import ScanCoordinator

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
    # 扫频拟合结果信号: 采集线程 → UI 线程
    scan_panel_update = pyqtSignal(object)
    # 趋势图数据信号: EventBus → UI 线程 (dict[str, float])
    trend_update = pyqtSignal(dict)
    # STM32 串口配置变更信号: 设备面板确认 → ScopeApp 重建设备
    stm32_config_applied = pyqtSignal(dict, object)

    def __init__(self, feedback_manager: Optional[FeedbackManager] = None,
                 async_loop=None,
                 channel_count: int = 1,
                 scan_coordinator: Optional[ScanCoordinator] = None):
        super().__init__()
        self._channel_count = channel_count
        self._async_loop = async_loop

        # ── 加载 UI ──
        uic.loadUi(UI_PATH, self)

        # ── 波形视图 (替换 waveformContainer) ──
        self.waveform = WaveformView(self.waveformContainer, channel_count=channel_count)

        # ── 通道面板 (替换 channelList) ──
        self.channel_panel = ChannelPanel(channel_count=channel_count)
        self._embed_widget(self.tabChannels.layout(), self.channel_panel)
        self.channel_panel.channel_changed.connect(self._on_channel_changed)

        # 初始可见性: 默认打开全部通道 (由 ChannelPanel 的复选框控制)
        for ch in range(channel_count):
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
        self.device_panel.stm32_config_applied.connect(self._on_device_config)

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

        # ── 扫频面板 ──
        self._scan_coordinator = scan_coordinator or ScanCoordinator()
        self.scan_panel = ScanPanel(coordinator=self._scan_coordinator)
        # 找到 tabWidget 并添加扫频 Tab
        tab_widget = self.tabChannels.parent()
        from PyQt6.QtWidgets import QTabWidget
        while tab_widget is not None and not isinstance(tab_widget, QTabWidget):
            tab_widget = tab_widget.parent()
        if tab_widget is not None:
            tab_widget.addTab(self.scan_panel, "扫频")

        # ── 初始化 MeasurementBar / StatusBar ──
        self._update_status_bar()

        # ── 跨线程数据信号 ──
        self.data_received.connect(self.update_display)
        self.scan_panel_update.connect(self.scan_panel.update_fit_result)
        self.scan_panel_update.connect(self._on_fit_result_for_measurement)
        self.trend_update.connect(self._on_trend_update)

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
        logger.info(f"串口配置已应用: {params}")
        self.stm32_config_applied.emit(params, config)

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
            # 通道名 CH0/CH1/... → 0-based index
            ch_idx = int(ch_name.replace("CH", ""))
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

        # 3. 更新测量面板 (UI 线程)
        if hasattr(self, "measure_panel"):
            self.measure_panel.update_from_result(result)

        # 4. mini chart 由 UIBridge _on_fitted 独立驱动, 此处不再写入

        # 5. 更新状态栏
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
            "关于 频率锁定示波器",
            "频率锁定示波器 (STM32 分支)\n"
            "基于 PyQt6 + pyqtgraph\n"
            "STM32 串口门控采集 · 单通道",
        )

    def _on_trend_update(self, data: dict):
        """趋势图数据 (主线程, 由 trend_update signal 触发)。"""
        if data:
            # 提取内嵌时间戳
            ts = data.pop("__timestamp__", None)
            self.mini_chart.add_data(data, timestamp=ts)
            self.mini_chart.refresh_now()

    def _on_fit_result_for_measurement(self, fit_result):
        """拟合结果 → MeasurementPanel (展示 f0 / R² / σ)。"""
        if fit_result is not None:
            self.measure_panel.update_fit_result(
                f0=getattr(fit_result, 'f0', None),
                r2=getattr(fit_result, 'r_squared', None),
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
