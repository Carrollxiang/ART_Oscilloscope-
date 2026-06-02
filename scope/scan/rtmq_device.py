"""
RTMQ 射频卡设备 — intf_usb 全局单例封装

single_card() 通过 run_in_executor 调用,
不阻塞事件循环。
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)


class RtmqDevice:
    """
    RTMQ 射频卡设备。

    intf_usb 是全局单例, 持续占用 COM 口。
    single_card() 在独立线程中执行 (同步阻塞 → run_in_executor 调用)。

    用法:
        rtmq = RtmqDevice(port="COM8", baudrate=10_000_000)
        rtmq.open()
        rtmq.single_card(scan_freq_amp=0.5, base_freq=146.0, scan_dur=1_000_000)
        rtmq.close()
    """

    def __init__(self, port: str = "COM8", baudrate: int = 10_000_000):
        self._port = port
        self._baudrate = baudrate
        self._intf_usb = None
        self._lock = threading.Lock()
        self._opened = False

    # ── 生命周期 ────────────────────────────────────────────────

    def open(self) -> bool:
        """创建 intf_usb 单例, 持续占用 COM 口。"""
        try:
            from rtmq.single_card import intf_usb as _intf_cls
            # 实际导入 uart_intf
            import sys
            from pathlib import Path

            # 确保 rtmq 目录在 path 中
            rtmq_root = Path(__file__).resolve().parent.parent.parent / "rtmq"
            if str(rtmq_root) not in sys.path:
                sys.path.insert(0, str(rtmq_root))

            from oasm.rtmq2.intf import uart_intf

            self._intf_usb = uart_intf(self._port, self._baudrate)
            self._intf_usb.nod_adr = 0
            self._intf_usb.loc_chn = 1
            self._opened = True
            logger.info(f"RTMQ 射频卡已连接: {self._port} @ {self._baudrate}")
            return True
        except ImportError as e:
            logger.error(f"RTMQ 驱动未安装: {e}")
            return False
        except Exception as e:
            logger.error(f"打开 RTMQ 设备失败 ({self._port}): {e}")
            return False

    def close(self):
        """释放 intf_usb。"""
        if self._intf_usb is not None:
            try:
                # uart_intf 可能有 __exit__ / close 方法
                if hasattr(self._intf_usb, '__exit__'):
                    self._intf_usb.__exit__(None, None, None)
                elif hasattr(self._intf_usb, 'close'):
                    self._intf_usb.close()
            except Exception as e:
                logger.warning(f"关闭 RTMQ 设备时出错: {e}")
            self._intf_usb = None
        self._opened = False
        logger.info("RTMQ 射频卡已断开")

    @property
    def is_open(self) -> bool:
        return self._opened

    # ── 扫频控制 ────────────────────────────────────────────────

    def single_card(
        self,
        scan_freq_amp: float,
        base_freq: float,
        scan_dur: float,
    ):
        """
        下发扫频配置到 RWG 卡 (同步阻塞, 应在 run_in_executor 中调用)。

        参数:
            scan_freq_amp: 扫频范围 (MHz, 总跨度)
            base_freq: 中心频率 (MHz)
            scan_dur: 扫频时长 (μs)
        """
        if not self._opened or self._intf_usb is None:
            raise RuntimeError("RTMQ 设备未打开")

        with self._lock:
            from pulser2 import (
                run_cfg, rwg, core_run,
                rwg_init, count_down, rwg_play,
                dio, asm, While,
            )

            asm.cfg = rwg_run = run_cfg(self._intf_usb, [0], core=rwg.C_RWG)

            @core_run
            def _single_card(scan_freq_amp, base_freq, scan_dur):
                scan_coeff = scan_freq_amp / scan_dur
                amp = 0.1

                dio.dir.off(0, 1, 2, 3)

                rwg_init([base_freq, base_freq, base_freq, base_freq])
                count_down(2000)

                with While():
                    rwg_play(
                        scan_dur,
                        {0x00: ([-scan_freq_amp/2, scan_coeff, 0, 0], amp)},
                        dio=[0, 1, 2, 3],
                    )
                    rwg_play(scan_dur / 2, {0x00: (0, 0)})

            _single_card(scan_freq_amp, base_freq, scan_dur)

        logger.info(
            f"RTMQ 扫频执行中: f0={base_freq}MHz, "
            f"Δf=±{scan_freq_amp/2}MHz, dur={scan_dur}μs"
        )
