"""
数字示波器 — 主入口

模式:
  - 默认 (无参数)     → 连接 ART 硬件 (ArtDevice)
  - --mock / -m       → 模拟数据 (SimulatorDevice)，无硬件也可运行

架构 (事件驱动):
  - ArtDevice: register_done_event → 硬件触发 → DONE 回调 → 采集线程读取
  - SimulatorDevice: QTimer 驱动 (保持兼容)
  - 采集线程调用 _on_frame() → pyqtSignal → UI 线程处理
  - 跨线程通过 pyqtSignal 通信
"""

import argparse
import asyncio
import logging
import threading
import time

import numpy as np
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from scope.hardware import DeviceConfig
from scope.hardware.simulator import SimulatorDevice
from scope.hardware.art_device import ArtDevice
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

    def __init__(self, mock: bool = False):
        self._mock = mock
        self.feedback_mgr = FeedbackManager()
        self.main_win: MainWindow = None
        self._running = False
        self._device_type = "unknown"

        # 设备配置 (16 通道, ai0:15, 30k Sa/s, 0.5s/帧, ±10V)
        self._config = DeviceConfig(
            sample_rate=30_000,      # 上限 31250 Sa/s
            record_length=15000,     # 30k × 0.5s = 15000
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

        # asyncio loop 用于 feedback dispatch
        self._async_loop = asyncio.new_event_loop()

        # v0.4: 有界反馈队列 (maxsize=2, drop_oldest)
        from scope.runtime import BoundedQueue, DropStrategy
        self._feedback_queue = BoundedQueue(
            maxsize=2,
            on_drop=DropStrategy.DROP_OLDEST,
            name="feedback",
        )

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
                logger.info("✅ ART 硬件连接成功")
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
        # 1. 启动 asyncio 工作线程
        async_thread = threading.Thread(
            target=self._async_worker,
            daemon=True,
            name="async-worker",
        )
        async_thread.start()

        # 2. 创建主窗口 (必须在注册回调之前, 否则第一帧到达时 main_win 为 None)
        self.main_win = MainWindow(feedback_manager=self.feedback_mgr,
                                    async_loop=self._async_loop)
        self.main_win.art_config_applied.connect(self._on_art_config)
        self.main_win.show()

        # 3. 注册数据回调 (事件驱动: ArtDevice DONE → 采集线程 → _on_frame)
        if hasattr(self.device, 'set_data_callback'):
            self.device.set_data_callback(self._on_frame)

        device_label = "模拟设备" if self._device_type == "simulator" else "ART 采集卡"
        logger.info(
            f"{device_label} 已启动: "
            f"{len(self._config.channels_enabled)}ch @ "
            f"{self._config.sample_rate/1e6:.1f}MSa/s, "
            f"模式={'mock' if self._mock else '硬件'}"
        )

        # 4. SimulatorDevice 降级: 用 QTimer 驱动
        self._running = True
        if not hasattr(self.device, 'set_data_callback'):
            from PyQt6.QtCore import QTimer
            self._timer = QTimer()
            self._timer.setInterval(500)
            self._timer.timeout.connect(self._on_timer_tick)
            self._timer.start()

        logger.info("ScopeApp 已启动")

    def stop(self):
        """停止所有子系统"""
        self._running = False
        if hasattr(self, '_timer'):
            self._timer.stop()

        self.device.stop_acquisition()
        self.device.close()
        logger.info("ScopeApp 已停止")

    def _on_art_config(self, params: dict, config: DeviceConfig):
        """
        收到 ART 配置变更 → 重建设备。

        策略:
          1. 停掉旧设备 (释放硬件资源)
          2. 创建新设备
          3. 如果新设备失败 → 恢复旧设备继续运行
          4. 如果旧设备也恢复失败 → 终极回退到模拟器
        """
        self._art_params = params
        old_device = self.device
        old_config = self._config

        # 1. 停掉旧设备 + 关闭 Task 句柄 (释放硬件, 让新设备能创建 Task)
        if hasattr(self, '_timer'):
            self._timer.stop()
        try:
            old_device.stop_acquisition()
        except Exception:
            pass
        # 必须关闭 Task 句柄, 否则 NI-DAQmx 仍视设备为 reserved
        if hasattr(old_device, '_close_task'):
            try:
                old_device._close_task()
            except Exception:
                pass

        # 2. 创建并启动新设备
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
            # 不在此处读数据验证 — 硬件触发模式下会阻塞
            # 让第一个 QTimer tick 自然读取

            # 成功 → 关掉旧设备, 换入新设备, 注册回调
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
                f"✅ ART 设备已切换: {params['device_name']}/"
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

            # 3. 恢复旧设备
            try:
                old_device.configure(old_config)
                old_device.start_acquisition()
                self.device = old_device
                self._config = old_config
                if hasattr(old_device, '_device_type') and old_device._device_type:
                    self._device_type = old_device._device_type
                else:
                    self._device_type = "simulator"
                logger.info("已恢复旧设备继续运行")
            except Exception as restore_err:
                logger.error(f"恢复旧设备也失败: {restore_err}")
                # 4. 终极回退到模拟器
                fallback = SimulatorDevice()
                fallback.open()
                fallback.configure(old_config)
                fallback.start_acquisition()
                self.device = fallback
                self._config = old_config
                self._device_type = "simulator"
                logger.info("已回退到模拟设备")

        # 5. 寄存器回调 (事件驱动) 或重启 QTimer (模拟器降级)
        if hasattr(self.device, 'set_data_callback'):
            self.device.set_data_callback(self._on_frame)
        else:
            frame_ms = int(self._config.record_length / self._config.sample_rate * 1000)
            self._timer.setInterval(max(frame_ms, 50))
            self._timer.start()

    def _on_frame(self, chunk: np.ndarray):
        """
        事件驱动回调: ArtDevice 采集线程读取到数据后调用 (非 UI 线程)。

        使用 pyqtSignal.emit() 是线程安全的 — Qt 自动将调用排入接收者线程。
        """
        try:
            result = self.device.make_analysis_result(chunk)
            result = self._pipeline.process(result)

            # v0.4: 保存 Pipeline 全局测量 (用于 raw_measurements)
            pipeline_keys = set(result.measurements.keys())

            # 更新测量面板 → 标签化窗口值写入 result.measurements
            if hasattr(self.main_win, 'measure_panel'):
                self.main_win.measure_panel.update_from_result(result)

            # 构建 MeasurementSnapshot (单一数据源)
            from scope.runtime import MeasurementSnapshot
            snap = MeasurementSnapshot(
                sequence_num=result.sequence_num,
                raw_measurements={
                    k: v for k, v in result.measurements.items()
                    if k in pipeline_keys
                },
                event_measurements={
                    k: v for k, v in result.measurements.items()
                    if k not in pipeline_keys
                },
            )

            # 更新 UI
            self.main_win.data_received.emit(result)

            # v0.4: 通过有界队列发送反馈 (避免无界堆积)
            self._feedback_queue.put(snap)

            # 迷你图: 从 snapshot 读取
            self.main_win.mini_chart.add_data(snap.as_dict())

        except Exception as e:
            logger.error(f"数据处理错误: {e}", exc_info=True)

    def _on_timer_tick(self):
        """QTimer 回调 (仅 SimulatorDevice 降级模式 — 保留兼容)"""
        try:
            chunk = self.device.read_chunk()
            self._on_frame(chunk)
            if hasattr(self.device, 'rearm'):
                self.device.rearm()
        except Exception as e:
            logger.error(f"采集错误: {e}", exc_info=True)

    def _async_worker(self):
        """在独立线程中运行 asyncio loop, 消费反馈队列 + dispatch"""
        asyncio.set_event_loop(self._async_loop)
        loop = self._async_loop
        # 启动队列消费者
        loop.create_task(self._feedback_consumer())
        loop.run_forever()

    async def _feedback_consumer(self):
        """消费 FeedbackQueue → dispatch (v0.4 背压保护)"""
        import time
        while True:
            snap = self._feedback_queue.get(timeout=0.1)
            if snap is not None:
                # 记录队列延迟
                latency_ms = (time.monotonic() - snap.timestamp) * 1000
                if latency_ms > 100:
                    logger.warning(
                        f"反馈延迟 {latency_ms:.0f}ms, "
                        f"队列深度={self._feedback_queue.qsize}"
                    )
                # 构建临时 AnalysisResult 用于 dispatch
                from scope.model import AnalysisResult
                proxy = AnalysisResult(
                    sequence_num=snap.sequence_num,
                    trigger=None,
                )
                proxy.measurements = snap.as_dict()
                await self.feedback_mgr.dispatch(proxy)
            await asyncio.sleep(0)  # yield 给其他协程


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
        logger.info("🟡 启动模式: mock — 使用模拟数据，不连接硬件")
    else:
        logger.info("🟢 启动模式: hardware — 连接 ART 采集卡 (添加 --mock 使用模拟数据)")

    app = QApplication(sys.argv)

    scope_app = ScopeApp(mock=args.mock)

    # start() 会在 __init__ 中自动完成，此处显式调用确保 timer 等就绪
    scope_app.start()

    # 进入 Qt 事件循环
    try:
        app.exec()
    finally:
        scope_app.stop()


if __name__ == "__main__":
    main()
