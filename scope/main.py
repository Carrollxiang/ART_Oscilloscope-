"""
数字示波器 — 主入口 (Phase 3: 桌面 UI + 模拟器)

运行: python -m scope.main

架构:
  - qasync 桥接 asyncio 和 Qt 事件循环
  - SimulatorDevice 在采集线程中生成模拟数据
  - 采集完成 → FeedbackManager.dispatch() + UI.update_display()
"""

import asyncio
import logging
import threading
import time

import numpy as np
import qasync
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

from scope.hardware import DeviceConfig
from scope.hardware.simulator import SimulatorDevice
from scope.io import FeedbackManager
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
        self._seq = 0

        # 设备配置
        self._config = DeviceConfig(
            sample_rate=1_000_000,
            record_length=5000,
            channels_enabled=[0, 1, 2, 3],
        )

    async def start(self):
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

        # 2. 启动 FeedbackManager (暂无预设 slot, 由 UI 动态添加)
        await self.feedback_mgr.start_all()

        # 3. 创建主窗口
        self.main_win = MainWindow(feedback_manager=self.feedback_mgr)
        self.main_win.show()

        # 4. 启动采集循环 (在独立线程中)
        self._running = True
        self._acq_thread = threading.Thread(
            target=self._acquisition_loop,
            daemon=True,
            name="acquisition",
        )
        self._acq_thread.start()

        logger.info("ScopeApp 已启动")

    async def stop(self):
        """停止所有子系统"""
        self._running = False
        if self._acq_thread:
            self._acq_thread.join(timeout=2)

        self.device.stop_acquisition()
        self.device.close()
        await self.feedback_mgr.stop_all()
        logger.info("ScopeApp 已停止")

    def _acquisition_loop(self):
        """
        采集线程 (同步循环)。

        每次从模拟器读取一帧数据, 填充测量值,
        然后通过 Signal 投递到 UI 线程, 并 dispatch 到 feedback slots。
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        while self._running:
            try:
                # 读取一帧
                chunk = self.device.read_chunk()
                result = self.device.make_analysis_result(chunk)

                # 模拟 Pipeline: 填充测量值
                for ch_idx in range(len(self._config.channels_enabled)):
                    ch_name = f"CH{ch_idx + 1}"
                    data = chunk[ch_idx]
                    result.measurements[f"{ch_name}_Vpp"] = float(np.ptp(data))
                    result.measurements[f"{ch_name}_Vrms"] = float(np.std(data))
                    # 简单的过零频率检测
                    zero_crossings = np.where(np.diff(np.signbit(data)))[0]
                    if len(zero_crossings) > 1:
                        period = np.mean(np.diff(zero_crossings)) / self._config.sample_rate
                        result.measurements[f"{ch_name}_Freq"] = float(1.0 / period) if period > 0 else 0.0
                    result.measurements[f"{ch_name}_Vmax"] = float(np.max(data))
                    result.measurements[f"{ch_name}_Vmin"] = float(np.min(data))

                # 投递到 UI (通过 pyqtSignal 跨线程)
                if self.main_win:
                    self.main_win.data_received.emit(result)

                # Dispatch 到反馈系统
                loop.run_until_complete(self.feedback_mgr.dispatch(result))

                # 控制采集速率 (~30fps)
                time.sleep(0.033)

            except Exception as e:
                logger.error(f"采集循环错误: {e}", exc_info=True)
                time.sleep(0.1)

        loop.close()


async def main():
    app = QApplication.instance() or QApplication([])

    scope_app = ScopeApp()
    await scope_app.start()

    # 用 qasync 运行 Qt 事件循环
    with qasync.QApplicationExecutor(app):
        await asyncio.Future()  # 运行直到窗口关闭


if __name__ == "__main__":
    qasync.run(main())
