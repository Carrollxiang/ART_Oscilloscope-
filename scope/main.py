"""
数字示波器 — 主入口

架构:
  - 纯 PyQt6 事件循环 (不依赖 qasync)
  - SimulatorDevice 在采集线程中生成模拟数据
  - QTimer 驱动 UI 刷新
  - 跨线程通过 pyqtSignal 通信
"""

import asyncio
import logging
import threading
import time

import numpy as np
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from scope.hardware import DeviceConfig
from scope.hardware.simulator import SimulatorDevice
from scope.io import FeedbackManager
from scope.processing import ProcessingPipeline, AutoMeasure, MathOp, FFTAnalyze
from scope.ui import MainWindow

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("scope")


class ScopeApp:
    """
    示波器应用 — 整合采集、分析、反馈、UI。
    """

    def __init__(self):
        self.device = SimulatorDevice()
        self.feedback_mgr = FeedbackManager()
        self.main_win: MainWindow = None
        self._running = False

        # 设备配置
        self._config = DeviceConfig(
            sample_rate=10_000,      # 10kHz → 5000 samples = 0.5s/帧
            record_length=5000,
            channels_enabled=[0, 1, 2, 3],
        )

        # 信号处理管道
        self._pipeline = ProcessingPipeline()
        self._pipeline.add_stage(
            AutoMeasure(
                measurements=["Vpp", "Vrms", "Vmax", "Vmin", "Freq"],
                channels=["CH1", "CH2", "CH3", "CH4"],
            )
        )
        self._pipeline.add_stage(
            MathOp("CH1 + CH2", output="MATH1")
        )
        self._pipeline.add_stage(
            FFTAnalyze(channels=["CH1", "CH2"])
        )

        # asyncio loop 用于 feedback dispatch
        self._async_loop = asyncio.new_event_loop()

    def start(self):
        """启动所有子系统"""
        # 1. 初始化设备
        self.device.open()
        self.device.configure(self._config)
        self.device.start_acquisition()
        logger.info(
            f"模拟设备已启动: "
            f"{len(self._config.channels_enabled)}ch @ "
            f"{self._config.sample_rate/1e6:.1f}MSa/s"
        )

        # 2. 创建主窗口
        self.main_win = MainWindow(feedback_manager=self.feedback_mgr)
        self.main_win.show()

        # 3. 用 QTimer 驱动采集循环 (在主线程中运行)
        self._running = True
        self._timer = QTimer()
        self._timer.setInterval(33)  # ~30fps
        self._timer.timeout.connect(self._on_timer_tick)
        self._timer.start()

        logger.info("ScopeApp 已启动")

    def stop(self):
        """停止所有子系统"""
        self._running = False
        if hasattr(self, '_timer') and self._timer:
            self._timer.stop()

        self.device.stop_acquisition()
        self.device.close()
        logger.info("ScopeApp 已停止")

    def _on_timer_tick(self):
        """QTimer 回调: 采集一帧 → 处理 → 显示 → 反馈"""
        try:
            # 读取一帧
            chunk = self.device.read_chunk()
            result = self.device.make_analysis_result(chunk)

            # Pipeline: 自动测量 + 数学运算 + FFT
            result = self._pipeline.process(result)

            # 更新 UI
            self.main_win.data_received.emit(result)

            # Dispatch 到反馈系统 (在 asyncio loop 中执行)
            asyncio.run_coroutine_threadsafe(
                self.feedback_mgr.dispatch(result),
                self._async_loop,
            )

        except Exception as e:
            logger.error(f"采集错误: {e}", exc_info=True)

    def _async_worker(self):
        """在独立线程中运行 asyncio loop, 处理 feedback dispatch"""
        asyncio.set_event_loop(self._async_loop)
        self._async_loop.run_forever()


def main():
    import sys

    app = QApplication(sys.argv)

    scope_app = ScopeApp()
    scope_app.start()

    # 启动 asyncio 线程 (用于 feedback dispatch)
    async_thread = threading.Thread(
        target=scope_app._async_worker,
        daemon=True,
        name="async-worker",
    )
    async_thread.start()

    # 进入 Qt 事件循环
    try:
        app.exec()
    finally:
        scope_app.stop()


if __name__ == "__main__":
    main()
