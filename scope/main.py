"""
数字示波器 — 主入口

模式:
  - 默认 (无参数)     → 连接 ART 硬件 (ArtDevice)
  - --mock / -m       → 模拟数据 (SimulatorDevice)，无硬件也可运行

架构 (统一事件驱动):
  - ArtDevice: 硬件触发 → DONE 回调 → _on_frame
  - SimulatorDevice: 内部线程模拟触发 → _on_frame
  - 两者接口一致，上层代码无需区分
  - _on_frame → EventBus → MeasurementProcessor / UIBridge / FeedbackManager
"""

import argparse
import asyncio
import logging
import threading

import numpy as np
from PyQt6.QtWidgets import QApplication

from scope.hardware import DeviceConfig
from scope.hardware.simulator import SimulatorDevice
from scope.hardware.art_device import ArtDevice
from scope.io import FeedbackCommandWorker, FeedbackManager
from scope.ui import MainWindow
from scope.runtime import EventBus, DropStrategy, MeasurementConfigWorker, MeasurementProcessor
from scope.runtime import RuntimeMetricsSnapshot
from scope.ui.ui_bridge import UIBridge
from scope.runtime.config_worker import ConfigWorker
from scope.config.settings import ConfigManager
from scope.io.feedback_command import FeedbackCommand

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("scope")

# 抑制 artdaq 原生库的重复日志（每帧都会触发）
logging.getLogger("artdaq").setLevel(logging.WARNING)


class ScopeApp:
    """
    示波器应用 — 整合采集、分析、反馈、UI。
    
    统一事件驱动架构：
      - 所有设备通过 set_data_callback 注册回调
      - 每帧触发 _on_frame，同步测量规格并发布数据
    """

    def __init__(self, mock: bool = False):
        self._mock = mock
        self.main_win: MainWindow = None
        self._running = False
        self._device_type = "unknown"
        self._async_loop = asyncio.new_event_loop()

        # 设备配置 (16 通道, ai0:15, 30k Sa/s, 0.5s/帧, ±10V)
        self._config = DeviceConfig(
            sample_rate=30_000,
            record_length=15000,
            channels_enabled=list(range(16)),
            channel_min_vals=[-10.0] * 16,
            channel_max_vals=[10.0] * 16,
        )

        # EventBus — 数据面/控制面分离路由器
        self._event_bus = EventBus()
        self._event_bus.register_topic("frame.raw", maxsize=2, on_drop=DropStrategy.DROP_OLDEST)
        self._event_bus.register_topic("frame.fitted", maxsize=2, on_drop=DropStrategy.DROP_OLDEST)
        self._event_bus.register_topic("config.change", maxsize=8, on_drop=DropStrategy.BLOCK)
        self._event_bus.register_topic("measurement.remove", maxsize=8, on_drop=DropStrategy.BLOCK)
        self._event_bus.register_topic(
            "measurement.specs.changed",
            maxsize=4,
            on_drop=DropStrategy.DROP_OLDEST,
        )
        self._event_bus.register_topic(
            "feedback.worker.command",
            maxsize=32,
            on_drop=DropStrategy.BLOCK,
        )
        self._event_bus.register_topic(
            "feedback.status",
            maxsize=2,
            on_drop=DropStrategy.DROP_OLDEST,
        )
        self._event_bus.register_topic(
            "runtime.metrics",
            maxsize=2,
            on_drop=DropStrategy.DROP_OLDEST,
        )

        # MeasurementProcessor — 独立线程运行测量计算
        self._processor = MeasurementProcessor(self._event_bus, specs=[])
        self._measurement_config_worker = MeasurementConfigWorker(
            self._event_bus,
            self._processor,
        )

        # FeedbackManager — 内部持有 EventBus 订阅和分发协程
        # (Worker 通过 add_worker 添加)
        self.feedback_mgr = FeedbackManager(event_bus=self._event_bus)
        self._feedback_command_worker = FeedbackCommandWorker(
            self._event_bus,
            self.feedback_mgr,
        )

        # ConfigWorker — asyncio 消费 config.change
        self._config_worker = ConfigWorker(
            self._event_bus, apply_fn=self._on_art_config
        )

        # UIBridge — 采集线程 → Qt 主线程桥接
        self._ui_bridge = None

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

        # 创建设备
        self.device = self._create_device()

    def _create_device(self):
        """
        根据 mock 标志创建设备:
          - mock=True  → SimulatorDevice (事件驱动模式)
          - mock=False → 先尝试 ArtDevice; 失败后回退到 SimulatorDevice
        """
        if self._mock:
            logger.info("Mock 模式: 使用 SimulatorDevice (事件驱动)")
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
        logger.info("回退到 SimulatorDevice (事件驱动)")
        self._device_type = "simulator"
        dev = SimulatorDevice()
        dev.open()
        dev.configure(self._config)
        return dev

    def start(self):
        """启动所有子系统"""
        self._running = True

        # 1. 启动 asyncio 工作线程
        async_thread = threading.Thread(
            target=self._async_worker,
            daemon=True,
            name="async-worker",
        )
        async_thread.start()

        # 2. 创建主窗口
        self.main_win = MainWindow(
            feedback_manager=self.feedback_mgr,
            async_loop=self._async_loop,
            event_bus=self._event_bus
        )
        self.main_win.show()

        # 3. 创建 UIBridge 并连接信号
        self._ui_bridge = UIBridge(self._event_bus)
        self.main_win.connect_ui_bridge(self._ui_bridge)

        # 4. 启动 MeasurementProcessor
        self._processor.start()

        # 4.5 自动加载上次保存的反馈 Worker 配置
        self._auto_load_feedback_workers()

        # 4.6 启动后 rpyc 连接探测（延迟确保 worker 已加载）
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(2000, self._startup_rpyc_ping)

        # 5. 注册数据回调 (统一事件驱动)
        self.device.set_data_callback(self._on_frame)

        # 6. 启动设备采集
        self.device.start_acquisition()

        device_label = "模拟设备" if self._device_type == "simulator" else "ART 采集卡"
        logger.info(
            f"{device_label} 已启动: "
            f"{len(self._config.channels_enabled)}ch @ "
            f"{self._config.sample_rate/1e3:.1f}kSa/s"
        )
        logger.info("ScopeApp 已启动")

    def stop(self):
        """停止所有子系统"""
        self._running = False

        # 停止 MeasurementProcessor
        if hasattr(self, '_processor'):
            self._processor.stop()
        if hasattr(self, '_measurement_config_worker'):
            self._measurement_config_worker.stop()
        if hasattr(self, '_feedback_command_worker'):
            self._feedback_command_worker.stop()
        if hasattr(self, '_config_worker'):
            self._config_worker.stop()

        # 停止设备
        self.device.stop_acquisition()
        self.device.close()

        # 停止 asyncio loop
        self._async_loop.call_soon_threadsafe(self._async_loop.stop)

        logger.info("ScopeApp 已停止")

    def _auto_load_feedback_workers(self):
        """启动时自动加载全部配置（channels + device + measurements + feedback_workers）。"""
        config_path = ConfigManager.default_filepath()
        try:
            from pathlib import Path
            if not Path(config_path).exists():
                return
            config = ConfigManager.load_json(config_path)

            # 1. 通道配置（回填 UI）
            if 'channels' in config and hasattr(self.main_win, 'channel_panel'):
                self.main_win.channel_panel.set_config(config['channels'])

            # 2. 设备配置（回填 UI，不 apply 硬件 — 用户可能不在真实硬件环境）
            if 'device' in config and hasattr(self.main_win, 'device_panel'):
                self.main_win.device_panel.set_config(config['device'])

            # 3. 测量配置 — 关键！必须先加载测量项，反馈 Worker 才能匹配 measurement_key
            if 'measurements' in config and hasattr(self.main_win, 'measure_panel'):
                self.main_win.measure_panel.set_config(config['measurements'])
                logger.info("自动加载 %d 个测量项", len(config['measurements']))

            # 4. 反馈 Worker — 延迟一小段确保 MeasurementProcessor 已处理 specs
            fw = config.get("feedback_workers")
            if fw:
                from PyQt6.QtCore import QTimer
                def _delayed_load():
                    self._event_bus.publish(
                        "feedback.worker.command",
                        FeedbackCommand(
                            action="load_batch",
                            worker_id="_auto_",
                            config_list=fw,
                            change_id=0,
                        ),
                    )
                    logger.info("自动加载 %d 个反馈 Worker", len(fw))
                # 500ms 延迟确保 MeasurementProcessor 线程已处理多帧
                QTimer.singleShot(500, _delayed_load)

        except Exception as e:
            logger.warning("自动加载配置跳过: %s", e)

    def _startup_rpyc_ping(self):
        """启动后探测目标设备的 rpyc 连通性。

        在 Qt 主线程(QTimer 回调)中被调用, 但实际连接改为调度到
        asyncio loop 的 executor 线程执行, 避免 rpyc.connect
        (最坏 3s × 6 次 ≈ 18s) 阻塞 UI。
        """
        try:
            import rpyc  # noqa: F401
        except ImportError:
            logger.warning("rpyc 未安装，跳过连通性探测")
            return
        self._async_loop.call_soon_threadsafe(
            lambda: self._async_loop.create_task(self._async_rpyc_ping())
        )

    async def _async_rpyc_ping(self):
        """在 asyncio loop 中执行 rpyc 连通性探测 (建连在 executor 线程)。"""
        loop = asyncio.get_running_loop()
        workers = list(self.feedback_mgr._workers.items())
        for wid, w in workers:
            if w.target is None:
                continue
            try:
                await loop.run_in_executor(None, self._ping_one_target, w)
            except Exception as e:
                logger.warning("rpyc 探测任务异常 %s: %s", wid, e)
        self.feedback_mgr._publish_status()

    def _ping_one_target(self, worker):
        """同步探测单个目标 (在 executor 线程中执行)。"""
        wid = worker.worker_id
        target = worker.target
        try:
            import rpyc
            conn = rpyc.connect(target.ip, target.port,
                                config={"sync_request_timeout": 2.0})
            conn.ping()
            conn.close()
            logger.info("rpyc ✓ %s → %s:%d", wid, target.ip, target.port)
            worker._sender_error = ""
        except Exception as e:
            worker._sender_error = f"rpyc 不可达: {e}"
            logger.warning("rpyc ✗ %s → %s:%d: %s", wid, target.ip, target.port, e)

    def _on_frame(self, chunk: np.ndarray):
        """
        事件驱动回调: 每收到一帧原始数据后调用。
        
        职责:
          1. 组装 RawFrame
          2. 发布到 EventBus
          3. 轮询 UIBridge
        """
        try:
            # 1. 组装并发布
            frame = self.device.make_raw_frame(chunk)
            self._event_bus.publish("frame.raw", frame)

            # 2. 轮询 UI 桥接
            if self._ui_bridge is not None:
                self._ui_bridge.poll()

        except Exception as e:
            logger.error(f"帧处理错误: {e}", exc_info=True)

    def _on_art_config(self, params: dict, config: DeviceConfig):
        """
        收到 ART 配置变更 → 重建设备。
        """
        self._art_params = params
        old_device = self.device
        old_config = self._config

        # 1. 停掉旧设备
        try:
            old_device.stop_acquisition()
        except Exception:
            pass
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
            new_device.set_data_callback(self._on_frame)
            new_device.start_acquisition()

            # 成功 → 关掉旧设备
            try:
                old_device.close()
            except Exception:
                pass

            self.device = new_device
            self._config = config
            self._device_type = "art"
            logger.info(
                f"✅ ART 设备已切换: {params['device_name']}/"
                f"{params['ai_channels']}, {config.sample_rate}Sa/s"
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
                old_device.configure(old_config)
                old_device.set_data_callback(self._on_frame)
                old_device.start_acquisition()
                self.device = old_device
                self._config = old_config
                logger.info("已恢复旧设备继续运行")
            except Exception as restore_err:
                logger.error(f"恢复旧设备也失败: {restore_err}")
                # 回退到模拟器
                fallback = SimulatorDevice()
                fallback.open()
                fallback.configure(old_config)
                fallback.set_data_callback(self._on_frame)
                fallback.start_acquisition()
                self.device = fallback
                self._config = old_config
                self._device_type = "simulator"
                logger.info("已回退到模拟设备")

    async def _metrics_publish_task(self):
        """周期发布 runtime.metrics 快照。"""
        while self._running:
            try:
                snapshot = RuntimeMetricsSnapshot(
                    measurement_processor=self._processor.metrics,
                    event_bus=self._event_bus.metrics(),
                    config_worker=self._config_worker.metrics,
                    measurement_config_worker=self._measurement_config_worker.metrics,
                    feedback_command_worker=self._feedback_command_worker.metrics,
                    ui_bridge=self._ui_bridge.metrics if self._ui_bridge else {},
                )
                self._event_bus.publish("runtime.metrics", snapshot)
            except Exception as e:
                logger.error(f"RuntimeMetrics publish error: {e}")
            await asyncio.sleep(1.0)

    def _async_worker(self):
        """在独立线程中运行 asyncio loop"""
        asyncio.set_event_loop(self._async_loop)
        self._async_loop.create_task(self.feedback_mgr.start())
        self._async_loop.create_task(self._config_worker.run())
        self._async_loop.create_task(self._measurement_config_worker.run())
        self._async_loop.create_task(self._feedback_command_worker.run())
        self._async_loop.create_task(self._metrics_publish_task())
        self._async_loop.run_forever()


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
    scope_app.start()

    try:
        app.exec()
    finally:
        scope_app.stop()


if __name__ == "__main__":
    main()
