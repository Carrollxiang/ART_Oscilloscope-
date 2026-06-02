"""
数字示波器 — STM32 频率锁定分支 (v0.5)

模式:
  - 默认 (无参数)     → 连接 STM32 串口设备 (Stm32Device)
  - --mock / -m       → 模拟数据 (SimulatorDevice, 1 通道)，无硬件也可运行

v0.5 变更:
  - EventBus 发布-订阅解耦: 测量 → 拟合 → 反馈 → UI 各自独立消费
  - _on_frame 瘦身: 只做采集 + 测量 + bus.publish
  - FitWorker: 独立线程拟合, 不阻塞采集
  - FeedbackWorker: 独立 async worker, 自持 enabled 开关
  - UIBridge: 双订阅 frame.measured (波形) + frame.fitted (扫频/趋势图)
"""

import argparse
import logging
import time

import numpy as np
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from scope.hardware import DeviceConfig
from scope.hardware.simulator import SimulatorDevice
from scope.hardware.stm32_device import Stm32Device
from scope.io import FeedbackManager
from scope.processing import ProcessingPipeline, AutoMeasure
from scope.runtime import EventBus, MeasurementSnapshot
from scope.runtime.workers import FitWorker, FeedbackWorker, UIBridge
from scope.scan import ScanCoordinator
from scope.ui import MainWindow

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("scope")


class ScopeApp:
    """
    示波器应用 — STM32 频锁分支 (v0.5 EventBus 解耦)。
    """

    def __init__(self, mock: bool = False):
        self._mock = mock
        self.feedback_mgr = FeedbackManager()
        self.main_win: MainWindow = None
        self._running = False
        self._device_type = "unknown"

        # 设备配置 (1 通道, ~149 Sa/s 实测, 450 点 ≈ 3s)
        self._config = DeviceConfig(
            sample_rate=149,
            record_length=450,
            channels_enabled=[0],
            channel_min_vals=[0.0],
            channel_max_vals=[1.0],
        )

        # 信号处理管道 (单通道 AutoMeasure)
        from scope.processing.measurements import MEASUREMENT_FUNCTIONS
        self._pipeline = ProcessingPipeline()
        self._pipeline.add_stage(
            AutoMeasure(
                measurements=list(MEASUREMENT_FUNCTIONS.keys()),
                channels=["CH0"],
            )
        )

        # STM32 设备参数
        self._stm32_params = {
            "port": "COM11",
            "baudrate": 115200,
            "sample_rate": self._config.sample_rate,
            "record_length": self._config.record_length,
        }

        # v0.5: EventBus 发布-订阅
        self._bus = EventBus()

        # 扫频协调器 (全局单例)
        self.scan_coordinator = ScanCoordinator()

        # v0.5: Workers
        self._fit_worker = FitWorker(self._bus, self.scan_coordinator)
        self._feedback_worker = FeedbackWorker(self._bus, self.feedback_mgr)
        self._ui_bridge: UIBridge | None = None  # 在 start() 中创建 (依赖 main_win)

        # 创建设备
        self.device = self._create_device()

    def _create_device(self):
        """
        根据 mock 标志创建设备:
          - mock=True  → SimulatorDevice (1 通道)
          - mock=False → 尝试 Stm32Device; 失败后回退到 SimulatorDevice
        """
        if self._mock:
            logger.info("Mock 模式: 使用 SimulatorDevice (1 通道模拟数据)")
            self._device_type = "simulator"
            dev = SimulatorDevice()
            dev._info = type(dev._info)(
                vendor_id=0xFFFF, product_id=0x0001,
                serial_number="SIM-0001", channel_count=1,
                resolution_bits=12, max_sample_rate=1000,
                firmware_version="simulator-1ch",
            )
            dev.open()
            dev.configure(self._config)
            return dev

        # 默认: 尝试 STM32 串口
        logger.info("尝试连接 STM32 串口 (%s @ %d) ...",
                     self._stm32_params["port"],
                     self._stm32_params["baudrate"])
        stm32 = Stm32Device(
            port=self._stm32_params["port"],
            baudrate=self._stm32_params["baudrate"],
        )

        if stm32.open():
            try:
                stm32.configure(self._config)
                stm32.start_acquisition()
                logger.info("STM32 串口连接成功")
                self._device_type = "stm32"
                return stm32
            except Exception as e:
                logger.warning(f"STM32 启动失败: {e}")
                try:
                    stm32.close()
                except Exception:
                    pass
        else:
            logger.warning("STM32 串口不可用")

        # 回退到模拟设备 (1 通道)
        logger.info("回退到 SimulatorDevice (1 通道模拟数据)")
        self._device_type = "simulator"
        dev = SimulatorDevice()
        dev._info = type(dev._info)(
            vendor_id=0xFFFF, product_id=0x0001,
            serial_number="SIM-0001", channel_count=1,
            resolution_bits=12, max_sample_rate=1000,
            firmware_version="simulator-1ch",
        )
        dev.open()
        dev.configure(self._config)
        return dev

    def start(self):
        """启动所有子系统"""
        # 1. 创建主窗口 (1 通道)
        self.main_win = MainWindow(
            feedback_manager=self.feedback_mgr,
            async_loop=None,  # v0.5: 不再需要外部 async loop
            channel_count=1,
            scan_coordinator=self.scan_coordinator,
        )
        self.main_win.stm32_config_applied.connect(self._on_stm32_config)
        self.main_win.show()

        # 2. 创建 UIBridge (依赖 main_win)
        self._ui_bridge = UIBridge(
            bus=self._bus,
            data_received_signal=self.main_win.data_received,
            scan_panel_signal=self.main_win.scan_panel_update,
            trend_update_signal=self.main_win.trend_update,
        )

        # 3. 绑定反馈开关: FeedbackPanel checkbox → FeedbackWorker.enabled
        self.main_win.feedback_panel._feedback_toggle_cb = (
            lambda checked: setattr(self._feedback_worker, 'enabled', checked)
        )

        # 4. 启动 workers
        self._fit_worker.start()
        self._feedback_worker.start()
        self._ui_bridge.start()

        # 4. 注册数据回调 (采集线程 → _on_frame)
        if hasattr(self.device, 'set_data_callback'):
            self.device.set_data_callback(self._on_frame)

        device_label = "模拟设备" if self._device_type == "simulator" else "STM32 串口"
        logger.info(
            f"{device_label} 已启动: "
            f"1ch @ 1kSa/s, "
            f"模式={'mock' if self._mock else '硬件'}"
        )

        # 5. SimulatorDevice 降级: 用 QTimer 驱动
        self._running = True
        if not hasattr(self.device, 'set_data_callback'):
            self._timer = QTimer()
            self._timer.setInterval(200)
            self._timer.timeout.connect(self._on_timer_tick)
            self._timer.start()

        logger.info("ScopeApp (STM32 频锁 v0.5) 已启动")

    def stop(self):
        """停止所有子系统"""
        self._running = False
        if hasattr(self, '_timer'):
            self._timer.stop()

        # v0.5: 停止 workers
        self._fit_worker.stop()
        self._feedback_worker.stop()
        if self._ui_bridge:
            self._ui_bridge.stop()

        self.device.stop_acquisition()
        self.device.close()
        logger.info("ScopeApp 已停止")

    def _on_stm32_config(self, params: dict, config: DeviceConfig):
        """
        收到串口配置变更 → 重建 STM32 设备。
        params 包含: port, baudrate, sample_rate, record_length
        config 为完整的 DeviceConfig
        """
        self._stm32_params = params
        self._config = config  # 更新当前配置 (含采样率/缓存长度)
        old_device = self.device

        # 1. 停掉旧设备
        if hasattr(self, '_timer'):
            self._timer.stop()
        try:
            old_device.stop_acquisition()
        except Exception:
            pass
        try:
            old_device.close()
        except Exception:
            pass

        # 2. 创建新设备
        new_device = None
        try:
            new_device = Stm32Device(
                port=params["port"],
                baudrate=params["baudrate"],
            )
            if not new_device.open():
                raise RuntimeError("open() failed")

            new_device.configure(config)
            new_device.start_acquisition()
            self.device = new_device
            self._config = config
            self._device_type = "stm32"
            if hasattr(new_device, 'set_data_callback'):
                new_device.set_data_callback(self._on_frame)
            logger.info(
                f"STM32 设备已切换: {params['port']} @ {params['baudrate']}"
            )

        except Exception as e:
            logger.error(f"新设备启动失败: {e}")
            if new_device is not None:
                try:
                    new_device.stop_acquisition()
                    new_device.close()
                except Exception:
                    pass
            # 恢复旧设备
            try:
                old_device.open()
                old_device.configure(self._config)
                old_device.start_acquisition()
                self.device = old_device
                if hasattr(old_device, 'set_data_callback'):
                    old_device.set_data_callback(self._on_frame)
                logger.info("已恢复旧设备继续运行")
            except Exception as restore_err:
                logger.error(f"恢复旧设备也失败: {restore_err}")
                # 终极回退
                fallback = SimulatorDevice()
                fallback._info = type(fallback._info)(
                    vendor_id=0xFFFF, product_id=0x0001,
                    serial_number="SIM-0001", channel_count=1,
                    resolution_bits=12, max_sample_rate=1000,
                    firmware_version="simulator-fallback",
                )
                fallback.open()
                fallback.configure(self._config)
                fallback.start_acquisition()
                self.device = fallback
                self._device_type = "simulator"
                logger.info("已回退到模拟设备")

        # 5. 重启 QTimer (模拟器降级)
        if not hasattr(self.device, 'set_data_callback'):
            self._timer.setInterval(200)
            self._timer.start()

    def _on_frame(self, chunk: np.ndarray):
        """
        采集线程回调 — 最小工作量 (v0.5)。
        只做采集 + 测量 + 事件窗口计算 + 发布到 EventBus。
        拟合、反馈、UI 更新由各自 worker 独立消费。
        """
        try:
            result = self.device.make_analysis_result(chunk)
            result = self._pipeline.process(result)

            # 事件窗口测量 (线程安全: MeasurementPanel._spec_lock 保护)
            event_measurements = {}
            if hasattr(self.main_win, "measure_panel"):
                event_measurements = (
                    self.main_win.measure_panel.compute_event_measurements(result)
                )

            # 构建 snapshot
            ch0 = result.channels.get("CH0")
            snap = MeasurementSnapshot(
                sequence_num=result.sequence_num,
                raw_measurements=dict(result.measurements),
                event_measurements=event_measurements,
                ch0_raw=ch0.raw if ch0 else None,
                ch0_time_axis=ch0.time_axis if ch0 else None,
            )
            # 传递 AnalysisResult 引用供 UIBridge 直接 emit (用后释放)
            snap._analysis_result = result

            self._bus.publish("frame.measured", snap)

        except Exception as e:
            logger.error(f"数据处理错误: {e}", exc_info=True)

    def _on_timer_tick(self):
        """QTimer 回调 (仅 SimulatorDevice 降级模式)。"""
        try:
            chunk = self.device.read_chunk()
            self._on_frame(chunk)
        except Exception as e:
            logger.error(f"采集错误: {e}", exc_info=True)


def main():
    import sys

    parser = argparse.ArgumentParser(prog="freq-lock-stm32", description="STM32 频率锁定示波器")
    parser.add_argument(
        "-m", "--mock",
        action="store_true",
        help="Mock 模式: 使用模拟数据，不连接 STM32 硬件"
    )
    args = parser.parse_args()

    if args.mock:
        logger.info("启动模式: mock — 使用模拟数据")
    else:
        logger.info("启动模式: hardware — 连接 STM32 串口 (--mock 使用模拟数据)")

    app = QApplication(sys.argv)

    scope_app = ScopeApp(mock=args.mock)
    scope_app.start()

    try:
        app.exec()
    finally:
        scope_app.stop()


if __name__ == "__main__":
    main()
