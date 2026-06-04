"""
STM32 串口采集设备 — 门控触发 + 单通道电压采集

协议:
  - STM32 有两个模拟输入通道:
    CH0: 实际测量电压
    CH1: 触发电压 (高电平 → 有效数据, 低电平 → 静默)
  - 串口格式: 115200 8N1
  - 有效数据: b'CH0:120332 \r\n' (原始 ADC 码值, 24 位无符号整数)
  - 电压转换: V = raw * 5.0 / 2^23
  - 静默数据: b''

门控采集逻辑:
  1. 收到非空行 → 解析原始 ADC 码值 → 换算电压 (V = raw * 5.0 / 2^23) → 填入预分配数组
  2. 收到空行 + 数组非空 → 切片已填充部分 → 封帧 → data_callback → 复位写指针
  3. 收到空行 + 数组为空 → 继续等待
"""

from __future__ import annotations

import logging
import os
import re
import sys
import threading
import time
from contextlib import contextmanager
from typing import Callable, Optional

import numpy as np

from scope.hardware.device import (
    AcquisitionDevice,
    DeviceConfig,
    DeviceInfo,
    DeviceHealthEvent,
)
from scope.model import TriggerInfo

logger = logging.getLogger(__name__)

# 电压解析正则: "CH0:120332"
_RAW_ADC_RE = re.compile(r"CH0:\s*(\d+)")

# ADC 电压转换系数: 24 位 ADC, 5V 参考电压
_ADC_VREF = 5.0
_ADC_BITS = 24
_ADC_SCALE = _ADC_VREF / (1 << _ADC_BITS)   # ≈ 2.98e-7 V/LSB

# 默认缓冲区大小 (运行时由 DeviceConfig.record_length 覆盖)
DEFAULT_BUFFER_SIZE = 450


@contextmanager
def _suppress_stdout():
    """临时禁用 stdout (避免 print 开销干扰采集)。"""
    old_stdout = sys.stdout
    sys.stdout = open(os.devnull, 'w')
    try:
        yield
    finally:
        sys.stdout.close()
        sys.stdout = old_stdout


class Stm32Device(AcquisitionDevice):
    """
    STM32 串口采集设备。

    单通道, 门控触发, 可变帧长。

    用法:
        device = Stm32Device(port="COM11", baudrate=115200)
        device.open()
        device.configure(DeviceConfig(sample_rate=1000, record_length=BUFFER_SIZE))
        device.set_data_callback(my_handler)
        device.start_acquisition()
        # 采集线程自动运行, 门控触发自动封帧
    """

    def __init__(
        self,
        port: str = "COM11",
        baudrate: int = 115200,
        timeout: float = 0.1,
    ):
        super().__init__()

        self._port = port
        self._baudrate = baudrate
        self._timeout = timeout

        self._serial = None
        self._config: Optional[DeviceConfig] = None
        self._info = DeviceInfo(
            vendor_id=0,
            product_id=0,
            serial_number="STM32-001",
            channel_count=1,
            resolution_bits=24,          # 24 位 ADC (ADS1256 或同类)
            max_sample_rate=180,          # 实测 ~149 Sa/s
            firmware_version="stm32-1.0",
        )
        self._running = False
        self._seq = 0

        # 预分配缓冲区 + 写指针
        self._buf_size: int = DEFAULT_BUFFER_SIZE
        self._buffer: np.ndarray = np.zeros(self._buf_size, dtype=np.float32)
        self._write_idx: int = 0

        # 采集线程
        self._acquire_thread: Optional[threading.Thread] = None
        self._data_callback: Optional[Callable[[np.ndarray], None]] = None

        # stdout 抑制标志
        self._suppress_print: bool = True

    # ── 生命周期 ────────────────────────────────────────────────

    def open(self) -> bool:
        """打开串口连接。"""
        try:
            import serial
            self._serial = serial.Serial(
                port=self._port,
                baudrate=self._baudrate,
                timeout=self._timeout,
            )
        except ImportError:
            logger.error("pyserial 未安装, 请执行: pip install pyserial")
            return False
        except Exception as e:
            logger.error(f"打开串口 {self._port} 失败: {e}")
            return False

        if self._serial.is_open:
            logger.info(f"STM32 串口已打开: {self._port} @ {self._baudrate}")
            return True
        else:
            logger.error(f"串口 {self._port} 打开失败")
            return False

    def set_data_callback(self, callback: Callable[[np.ndarray], None]):
        """设置数据就绪回调 (线程安全: 从采集线程调用, 需 emit Qt signal)。"""
        self._data_callback = callback

    def close(self):
        """关闭串口连接。"""
        self._running = False
        if self._acquire_thread and self._acquire_thread.is_alive():
            self._acquire_thread.join(timeout=2.0)
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None
        logger.info("STM32 串口已关闭")

    def start_acquisition(self):
        """启动采集线程。"""
        cfg = self._config
        if cfg is None:
            raise RuntimeError("请先调用 configure()")

        self._running = True
        self._write_idx = 0
        self._buf_size = cfg.record_length
        self._buffer = np.zeros(self._buf_size, dtype=np.float32)
        self._seq = 0

        if self._acquire_thread is None or not self._acquire_thread.is_alive():
            self._acquire_thread = threading.Thread(
                target=self._acquire_worker,
                daemon=True,
                name="stm32-acquire",
            )
            self._acquire_thread.start()

        logger.info(f"STM32 采集已启动: 1ch @ {cfg.sample_rate} Sa/s")

    def stop_acquisition(self):
        """停止采集线程。"""
        self._running = False
        if self._acquire_thread and self._acquire_thread.is_alive():
            self._acquire_thread.join(timeout=2.0)
        logger.info("STM32 采集已停止")

    # ── 数据流 ─────────────────────────────────────────────────

    def read_chunk(self) -> np.ndarray:
        """
        单次读取一行串口数据并返回。
        用于兼容 AcquisitionDevice 接口, 实际由 _acquire_worker 内部使用。
        """
        raise NotImplementedError("Stm32Device 使用门控触发, 请用 set_data_callback")

    def make_analysis_result(self, chunk: np.ndarray) -> "AnalysisResult":
        """将缓冲区切片数据组装成 AnalysisResult。"""
        from scope.model import AnalysisResult, ChannelData

        n_samples = chunk.shape[1] if chunk.ndim == 2 else len(chunk)
        # 确保是 (1, N) 形状
        if chunk.ndim == 1:
            chunk = chunk.reshape(1, -1)

        fs = self._config.sample_rate if self._config else 1000.0
        now = time.monotonic()

        t = np.arange(n_samples, dtype=np.float64) / fs
        channels = {
            "CH0": ChannelData(
                raw=chunk[0].copy(),
                time_axis=t.copy(),
                sample_rate=fs,
                resolution=self._info.resolution_bits,
                vertical_scale=10.0,
                vertical_offset=0.0,
                enabled=True,
            ),
        }

        return AnalysisResult(
            sequence_num=self._seq,
            trigger=TriggerInfo(
                trigger_type="edge",
                trigger_source=0,
                trigger_level=0.0,
                trigger_slope="rising",
                trigger_position=0.0,     # 门控触发: 帧起点即触发点
                trigger_timestamp=now,
            ),
            channels=channels,
        )

    # ── 配置 ───────────────────────────────────────────────────

    def configure(self, config: DeviceConfig):
        self._config = config
        logger.info(f"STM32 设备已配置: {config.sample_rate} Sa/s")

    def get_config(self) -> DeviceConfig:
        return self._config or DeviceConfig(
            sample_rate=149,
            record_length=DEFAULT_BUFFER_SIZE,
            channels_enabled=[0],
        )

    def set_port_params(self, port: str = None, baudrate: int = None):
        """运行时修改串口参数 (需重启采集才会生效)。"""
        if port is not None:
            self._port = port
        if baudrate is not None:
            self._baudrate = baudrate

    # ── Watchdog 支持 (简化) ──────────────────────────────────

    def ping(self) -> bool:
        return self._serial is not None and self._serial.is_open

    def reset(self) -> bool:
        try:
            if self._serial:
                self._serial.close()
            time.sleep(0.1)
            self._serial.open()
            return True
        except Exception as e:
            logger.error(f"STM32 重置失败: {e}")
            return False

    def restore_state(self, config: DeviceConfig):
        self._config = config
        self._write_idx = 0
        self._buf_size = config.record_length
        self._buffer = np.zeros(self._buf_size, dtype=np.float32)
        logger.info("STM32 状态已恢复")

    # ── 内部: 采集工作线程 ─────────────────────────────────────

    # 空数据超时 (秒): 超过此时间无数据视为门关闭
    GATE_CLOSE_TIMEOUT = 0.15
    # 轮询间隔 (秒)
    POLL_INTERVAL = 0.0005

    # 最大帧时长 (秒): 等待缓冲区填满再封帧
    MAX_FRAME_DURATION = 3.0

    def _acquire_worker(self):
        """
        采集线程主循环 — in_waiting + read() 批量读取。

        出帧条件 (满足任一即封帧):
          1. 无数据超过 GATE_CLOSE_TIMEOUT 且 buffer 非空 (门关闭)
          2. buffer 满 (防止溢出)
          3. 帧时长超过 MAX_FRAME_DURATION (数据连续场景下的时间窗口)
        """
        line_buf = bytearray()
        frame_start = time.monotonic()

        while self._running:
            # 1. 等待数据, 超时即视为门关闭
            t_start = time.monotonic()
            while self._serial.in_waiting == 0:
                if not self._running:
                    break
                elapsed = time.monotonic() - t_start
                if elapsed >= self.GATE_CLOSE_TIMEOUT:
                    break
                # 数据连续时也要检查帧时长
                if self._write_idx > 0:
                    frame_elapsed = time.monotonic() - frame_start
                    if frame_elapsed >= self.MAX_FRAME_DURATION:
                        break
                time.sleep(self.POLL_INTERVAL)

            if not self._running:
                break

            # 2. 检查封帧条件
            if self._write_idx > 0:
                frame_elapsed = time.monotonic() - frame_start
                if frame_elapsed >= self.MAX_FRAME_DURATION:
                    self._emit_frame()
                    frame_start = time.monotonic()
                    # 此时可能还有未读数据, 但 _emit_frame 后继续读

            # 3. 超时无数据 → 门关闭, 封帧
            if self._serial.in_waiting == 0:
                if self._write_idx > 0:
                    self._emit_frame()
                    frame_start = time.monotonic()
                continue

            # 4. 一次性读取所有可用字节
            try:
                raw = self._serial.read(self._serial.in_waiting)
            except Exception as e:
                logger.error(f"串口读取错误: {e}")
                time.sleep(0.01)
                continue

            if not raw:
                continue

            # 5. 拼接到行累积器, 按行分割
            line_buf.extend(raw)

            while True:
                nl_pos = line_buf.find(b'\n')
                if nl_pos < 0:
                    break

                line = bytes(line_buf[:nl_pos]).rstrip(b'\r')
                del line_buf[:nl_pos + 1]

                if line:
                    voltage = self._parse_voltage(line)
                    if voltage is not None:
                        if self._write_idx < self._buf_size:
                            self._buffer[self._write_idx] = voltage
                            self._write_idx += 1
                        else:
                            self._emit_frame()
                            frame_start = time.monotonic()
                            self._buffer[0] = voltage
                            self._write_idx = 1

        # 退出时如有残留数据也发射
        if self._write_idx > 0:
            self._emit_frame()

    def _parse_voltage(self, line: bytes) -> Optional[float]:
        """解析 'CH0:120332' → 伏特 (raw * 5.0 / 2^23), 失败返回 None。"""
        try:
            text = line.decode('utf-8', errors='replace').strip()
            m = _RAW_ADC_RE.search(text)
            if m:
                raw = int(m.group(1))
                return raw * _ADC_SCALE
        except (ValueError, UnicodeDecodeError):
            pass
        return None

    def _emit_frame(self):
        """将 buffer[:write_idx] 切片封帧并回调。"""
        if self._write_idx == 0:
            return

        self._seq += 1

        # 切片 (1, N) shape
        chunk = self._buffer[:self._write_idx].copy().reshape(1, -1)

        # 发射前抑制 stdout
        if self._suppress_print:
            with _suppress_stdout():
                if self._data_callback:
                    self._data_callback(chunk)
        else:
            if self._data_callback:
                self._data_callback(chunk)

        logger.debug(
            f"帧 #{self._seq}: {self._write_idx} 采样点, "
            f"{self._write_idx / 1000:.3f}s"
        )

        # 复位写指针
        self._write_idx = 0

    @property
    def suppress_print(self) -> bool:
        return self._suppress_print

    @suppress_print.setter
    def suppress_print(self, value: bool):
        self._suppress_print = value
