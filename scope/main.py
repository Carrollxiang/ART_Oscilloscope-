"""
数字示波器 — 主入口

当前阶段: Phase 0 (数据模型 + 模拟器验证)
运行: python -m scope.main
"""

import time
import logging

from scope.hardware import DeviceConfig
from scope.hardware.simulator import SimulatorDevice

logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(message)s",
)
logger = logging.getLogger("scope")


def main():
    print("=" * 60)
    print("  数字示波器 — Phase 0 数据模型验证")
    print("=" * 60)

    # 初始化模拟设备
    device = SimulatorDevice()
    config = DeviceConfig(
        sample_rate=1_000_000,
        record_length=5000,
        channels_enabled=[0, 1, 2, 3],
    )

    device.open()
    device.configure(config)
    device.start_acquisition()

    logger.info(f"模拟设备已启动: {config.channel_count}ch @ {config.sample_rate/1e6:.1f}MSa/s")

    try:
        for _ in range(20):
            chunk = device.read_chunk()
            result = device.make_analysis_result(chunk)
            logger.info(result.summary())
            time.sleep(0.05)  # 模拟 20fps 采集间隔
    finally:
        device.stop_acquisition()
        device.close()
        logger.info("模拟设备已关闭")


if __name__ == "__main__":
    main()
