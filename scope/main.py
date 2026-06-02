"""
数字示波器 — 主入口 (v0.5 EventBus 解耦)

模式:
  - 默认 (无参数)     → 连接 ART 硬件 (ArtDevice)
  - --mock / -m       → 模拟数据 (SimulatorDevice)，无硬件也可运行

v0.5 变更:
  - EventBus 发布-订阅解耦: 采集 → 测量 → UIBridge / FeedbackWorker 独立消费
  - _on_frame 瘦身: 只做采集 + 测量 + bus.publish
  - FeedbackWorker: 独立 async worker, 自持 enabled 开关
  - UIBridge: 回调订阅 frame.measured → 主波形刷新
"""

import argparse
import logging

import numpy as np
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from scope.hardware import DeviceConfig
from scope.hardware.simulator import SimulatorDevice
from scope.hardware.art_device import ArtDevice
from scope.io import FeedbackManager
from scope.processing import ProcessingPipeline, AutoMeasure, MathOp, FFTAnalyze
from scope.runtime import EventBus, MeasurementSnapshot
from scope.runtime.workers import FeedbackWorker, UIBridge
from scope.ui import MainWindow

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("scope")


class ScopeApp:
    """
    示波器应用 — ART 多通道采集 (v0.5 EventBus 解耦)。
    """

    def __init__(self, mock: bool = False):
        self._mock = mock
        self.feedback_mgr = FeedbackManager()
        self.main_win: MainWindow = None
        self._running = False
        self._device_type = "unknown"

        # 设备配置 (16 通道, ai0:15, 30k Sa/s, 0.5s/帧, ±10V)
        self._config = DeviceConfig(
            sample_rate=30_000,
            record_length=15000,
            channels_enabled=list(range(16)),
            channel_min_vals=[-10.0] * 16,
            channel_max_vals=[10.0] * 16,
        )

        # 信号处理管道 (16 通道, 全测量项)
        from scope.processing.measurements import MEASUREMENT_FUNCTIONS
        self._pipeline = ProcessingPipeline()
        self._pipeline.add_stage(
            AutoMeasure(
                measurements=list(MEASUREMENT_FUNCTIONS.keys()),
                channels=[f"CH{i+1}" for i in range(16)],
            )
        )
        self._pipeline.add_stage(
            MathOp("CH1 + CH2", output="MATH1")
        )
        self._pipeline.add_stage(
            FFTAnalyze(channels=["CH1", "CH2"])
        )

        # ART 设备参数 (用于配置对话框 → 设备重建)
        self._art_params = {
            "device_name": "Dev42",
            "ai_channels": "ai0:15",
            "terminal_config": "NRSE",
            "read_timeout": 5.0,
            "trigger_source": "ai12",
            "trigger_slope": "rising",
            "trigger_level": 1.0,
        }

        # v0.5: EventBus 发布-订阅
        self._bus = EventBus()

        # v0.5: Workers (UIBridge 在 start() 中创建, 依赖 main_win)
        self._feedback_worker = FeedbackWorker(
            self._bus, self.feedback_mgr,
            subscribe_topic="frame.measured",  # master: 无拟合层, 直接消费测量值
        )
        self._ui_bridge: UIBridge | None = None

        # 创建设备
        self.device = self._create_device()

    def _create_device(self):
        """
        根据 mock 标志创建设备:
          - mock=True  → SimulatorDevice (跳过硬件)
          - mock=False → 先尝试 ArtDevice; 失败后回退到 SimulatorDevice
        """
        if self._mock:
            logger.info("Mock 模式: 使用 SimulatorDevice (模拟数据)")
            self._device_type = "simulator"
            dev = SimulatorDevice()
            dev.open()
            dev.configure(self._config)
            return dev

        # 默认: 尝试 ART 硬件
        logger.info("尝试连接 ART 硬件 (Dev42/ai0:15) ...")
        art = ArtDevice(
            device_name=self._art_params["device_name"],
            ai_channels=self._art_params["ai_channels"],
            terminal_config=self._art_params["terminal_config"],
            trigger_source=self._art_params["trigger_source"],
            trigger_slope=self._art_params["trigger_slope"],
            trigger_level=self._art_params["trigger_level"],
        )
        art._read_timeout = self._art_params["read_timeout"]

        if art.open():
            try:
                art.configure(self._config)
                art.start_acquisition()
                logger.info("ART 硬件连接成功")
                self._device_type = "art"
                return art
            except Exception as e:
                logger.warning(f"ART 硬件启动失败: {e}")
                try:
                    art.close()
                except Exception:
                    pass
        else:
            logger.warning("Art_DAQ.dll 加载失败 — 硬件不可用")

        # 回退到模拟设备
        logger.info("回退到 SimulatorDevice (模拟数据)")
        self._device_type = "simulator"
        dev = SimulatorDevice()
        dev.open()
        dev.configure(self._config)
        return dev

    def start(self):
        """启动所有子系统"""
        # 1. 创建主窗口
        self.main_win = MainWindow(feedback_manager=self.feedback_mgr)
        self.main_win.art_config_applied.connect(self._on_art_config)
        self.main_win.show()

        # 2. 创建 UIBridge (依赖 main_win)
        self._ui_bridge = UIBridge(
            bus=self._bus,
            data_received_signal=self.main_win.data_received,
            # master 无扫频/拟合层, 不传 scan_panel_signal / trend_update_signal
        )

        # 3. 绑定反馈开关: FeedbackPanel checkbox → FeedbackWorker.enabled
        if hasattr(self.main_win.feedback_panel, '_feedback_toggle_cb'):
            self.main_win.feedback_panel._feedback_toggle_cb = (
                lambda checked: setattr(self._feedback_worker, 'enabled', checked)
            )

        # 4. 启动 workers
        self._feedback_worker.start()
        self._ui_bridge.start()

        # 5. 注册数据回调 (事件驱动: ArtDevice DONE → 采集线程 → _on_frame)
        if hasattr(self.device, 'set_data_callback'):
            self.device.set_data_callback(self._on_frame)

        device_label = "模拟设备" if self._device_type == "simulator" else "ART 采集卡"
        logger.info(
            f"{device_label} 已启动: "
            f"{len(self._config.channels_enabled)}ch @ "
            f"{self._config.sample_rate/1e6:.1f}MSa/s, "
            f"模式={'mock' if self._mock else '硬件'}"
        )

        # 6. SimulatorDevice 降级: 用 QTimer 驱动
        self._running = True
        if not hasattr(self.device, 'set_data_callback'):
            self._timer = QTimer()
            self._timer.setInterval(500)
            self._timer.timeout.connect(self._on_timer_tick)
            self._timer.start()

        logger.info("ScopeApp (ART v0.5 EventBus) 已启动")

    def stop(self):
        """停止所有子系统"""
        self._running = False
        if hasattr(self, '_timer'):
            self._timer.stop()

        # v0.5: 停止 workers
        self._feedback_worker.stop()
        if self._ui_bridge:
            self._ui_bridge.stop()

        self.device.stop_acquisition()
        self.device.close()
        logger.info("ScopeApp 已停止")

    def _on_art_config(self, params: dict, config: DeviceConfig):
        """
        收到 ART 配置变更 → 重建设备。
        """
        self._art_params = params
        old_device = self.device
        old_config = self._config

        if hasattr(self, '_timer'):
            self._timer.stop()
        try:
            old_device.stop_acquisition()
        except Exception:
            pass
        if hasattr(old_device, '_close_task'):
            try:
                old_device._close_task()
            except Exception:
                pass

        new_device = None
        try:
            new_device = ArtDevice(
                device_name=params["device_name"],
                ai_channels=params["ai_channels"],
                terminal_config=params["terminal_config"],
                trigger_source=params["trigger_source"],
                trigger_slope=params["trigger_slope"],
                trigger_level=params["trigger_level"],
            )
            new_device._read_timeout = params["read_timeout"]

            if not new_device.open():
                raise RuntimeError("open() failed — 请检查 Art_DAQ.dll")

            new_device.configure(config)
            new_device.start_acquisition()

            try:
                old_device.close()
            except Exception:
                pass
            self.device = new_device
            self._config = config
            self._device_type = "art"
            if hasattr(new_device, 'set_data_callback'):
                new_device.set_data_callback(self._on_frame)
            logger.info(
                f"ART 设备已切换: {params['device_name']}/"
                f"{params['ai_channels']}, "
                f"{config.sample_rate}Sa/s, "
                f"{config.record_length}samples"
            )

        except Exception as e:
            logger.error(f"新设备启动失败: {e}")
            if new_device is not None:
                try:
                    new_device.stop_acquisition()
                    new_device.close()
                except Exception:
                    pass
            try:
                old_device.configure(old_config)
                old_device.start_acquisition()
                self.device = old_device
                self._config = old_config
                self._device_type = getattr(old_device, '_device_type', None) or "simulator"
                logger.info("已恢复旧设备继续运行")
            except Exception as restore_err:
                logger.error(f"恢复旧设备也失败: {restore_err}")
                fallback = SimulatorDevice()
                fallback.open()
                fallback.configure(old_config)
                fallback.start_acquisition()
                self.device = fallback
                self._config = old_config
                self._device_type = "simulator"
                logger.info("已回退到模拟设备")

        if hasattr(self.device, 'set_data_callback'):
            self.device.set_data_callback(self._on_frame)
        else:
            frame_ms = int(self._config.record_length / self._config.sample_rate * 1000)
            self._timer.setInterval(max(frame_ms, 50))
            self._timer.start()

    def _on_frame(self, chunk: np.ndarray):
        """
        采集线程回调 — 最小工作量 (v0.5)。
        只做采集 + 测量 + 事件窗口计算 + 发布到 EventBus。
        反馈、UI 更新由各自 worker 独立消费。
        """
        try:
            result = self.device.make_analysis_result(chunk)
            result = self._pipeline.process(result)

            # 事件窗口测量
            raw_measurements = dict(result.measurements)
            event_measurements = {}
            if hasattr(self.main_win, "measure_panel"):
                event_measurements = (
                    self.main_win.measure_panel.compute_event_measurements(result)
                )
                result.measurements.update(event_measurements)

            # 构建 snapshot
            snap = MeasurementSnapshot(
                sequence_num=result.sequence_num,
                raw_measurements=raw_measurements,
                event_measurements=event_measurements,
            )
            snap._analysis_result = result

            self._bus.publish("frame.measured", snap)

        except Exception as e:
            logger.error(f"数据处理错误: {e}", exc_info=True)

    def _on_timer_tick(self):
        """QTimer 回调 (仅 SimulatorDevice 降级模式 — 保留兼容)。"""
        try:
            chunk = self.device.read_chunk()
            self._on_frame(chunk)
            if hasattr(self.device, 'rearm'):
                self.device.rearm()
        except Exception as e:
            logger.error(f"采集错误: {e}", exc_info=True)


def main():
    import sys

    parser = argparse.ArgumentParser(prog="digital-scope", description="多通道数字示波器")
    parser.add_argument(
        "-m", "--mock",
        action="store_true",
        help="Mock 模式: 使用模拟数据，不连接真实硬件"
    )
    args = parser.parse_args()

    if args.mock:
        logger.info("启动模式: mock — 使用模拟数据，不连接硬件")
    else:
        logger.info("启动模式: hardware — 连接 ART 采集卡 (添加 --mock 使用模拟数据)")

    app = QApplication(sys.argv)

    scope_app = ScopeApp(mock=args.mock)
    scope_app.start()

    try:
        app.exec()
    finally:
        scope_app.stop()


if __name__ == "__main__":
    main()
