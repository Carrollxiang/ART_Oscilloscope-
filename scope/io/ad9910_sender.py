"""
Ad9910Sender — AD9910 DDS 信号发生器目标发送器。

通过 rpyc 连接远程 AD9910 服务器，控制频率和幅度。
将 PID 计算的 delta 值映射为设备的频率/幅度调整量。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

import rpyc

from scope.io.rpyc_pool import ConnectionPoolManager, PoolConfig

logger = logging.getLogger(__name__)

# AD9910 典型参数范围 (可配置)
DEFAULT_FREQ_RANGE_HZ = (0, 400e6)       # 400 MHz 最大输出
DEFAULT_AMP_RANGE = (0.0, 1.0)            # 归一化幅度 [0, 1]


@dataclass
class Ad9910Mapping:
    """
    delta → 设备参数 映射配置。

    mode 决定 delta 控制哪个参数:
      - "frequency": delta 为频率偏移 (Hz)
      - "amplitude": delta 为幅度偏移 [0,1]
    """
    mode: str = "amplitude"               # "frequency" | "amplitude"
    center_freq: float = 100e6            # 中心频率 (Hz)
    freq_scale: float = 1.0               # delta → Hz 缩放系数
    amplitude_scale: float = 0.1          # delta → 幅度缩放系数
    min_freq: float = 0.0                 # 频率下限
    max_freq: float = 400e6               # 频率上限
    min_amplitude: float = 0.0            # 幅度下限
    max_amplitude: float = 1.0            # 幅度上限

    def __post_init__(self):
        if self.mode not in ("frequency", "amplitude"):
            raise ValueError(f"mode must be 'frequency' or 'amplitude', got {self.mode}")


class Ad9910Sender:
    """
    AD9910 目标发送器。

    每个实例通过连接池管理器获取共享 rpyc 连接。
    使用方式:
        sender = Ad9910Sender("192.168.1.100", 3251, device_id=0x0D11, profile=0)
        await sender.set_frequency(100e6)
        await sender.adjust_delta(0.05)  # PID delta → 设备调整
    """

    def __init__(
        self,
        ip: str,
        port: int = 3251,
        device_id: int = 0,
        profile: int = 0,
        mapping: Optional[Ad9910Mapping] = None,
        pool_config: Optional[PoolConfig] = None,
    ):
        self._ip = ip
        self._port = port
        self._device_id = device_id
        self._profile = profile
        self._mapping = mapping or Ad9910Mapping()
        self._pool_config = pool_config
        self._pool = ConnectionPoolManager.acquire_pool(
            ip, port, pool_config,
        )
        self._last_freq: Optional[float] = None
        self._last_amp: Optional[float] = None

    @property
    def ip(self) -> str:
        return self._ip

    @property
    def port(self) -> int:
        return self._port

    @property
    def device_id(self) -> int:
        return self._device_id

    @property
    def profile(self) -> int:
        return self._profile

    async def close(self):
        """释放连接池引用。"""
        await ConnectionPoolManager.release_pool(self._ip, self._port)

    # ── 核心 API ────────────────────────────────────────────────

    async def set_frequency(self, freq_hz: float) -> bool:
        """
        设置 AD9910 输出频率。

        Args:
            freq_hz: 目标频率 (Hz)，自动限幅到 [min_freq, max_freq]

        Returns:
            True 发送成功, False 失败
        """
        freq_hz = max(self._mapping.min_freq,
                      min(self._mapping.max_freq, freq_hz))
        try:
            conn = await self._pool.acquire()
            try:
                await self._call_remote(conn, "set_frequency", freq_hz)
                self._last_freq = freq_hz
                logger.debug("AD9910 %s:%d 频率已设为 %.2f Hz",
                             self._ip, self._port, freq_hz)
                return True
            finally:
                await self._pool.release(conn)
        except Exception as e:
            logger.error("AD9910 set_frequency 失败 %s:%d: %s",
                         self._ip, self._port, e)
            return False

    async def set_amplitude(self, amplitude: float) -> bool:
        """
        设置 AD9910 输出幅度。

        Args:
            amplitude: 归一化幅度 [0, 1]

        Returns:
            True 发送成功, False 失败
        """
        amplitude = max(self._mapping.min_amplitude,
                        min(self._mapping.max_amplitude, amplitude))
        try:
            conn = await self._pool.acquire()
            try:
                await self._call_remote(conn, "set_amplitude", amplitude)
                self._last_amp = amplitude
                logger.debug("AD9910 %s:%d 幅度已设为 %.4f",
                             self._ip, self._port, amplitude)
                return True
            finally:
                await self._pool.release(conn)
        except Exception as e:
            logger.error("AD9910 set_amplitude 失败 %s:%d: %s",
                         self._ip, self._port, e)
            return False

    async def adjust_delta(self, delta: float) -> bool:
        """
        应用 PID 计算出的 delta 调整量。

        根据 mapping.mode 决定调整频率或幅度:
          - frequency:  new_freq = center_freq + delta * freq_scale (本地计算绝对值，服务端 set_frequency)
          - amplitude:  delta 原样传服务端 adjust_amplitude (服务端自己做增量+限幅)

        Args:
            delta: PID 输出调整量 (有符号)

        Returns:
            True 发送成功, False 失败
        """
        if self._mapping.mode == "frequency":
            new_freq = self._mapping.center_freq + delta * self._mapping.freq_scale
            return await self.set_frequency(new_freq)
        else:
            return await self._adjust_amplitude_delta(delta)

    async def _adjust_amplitude_delta(self, delta: float) -> bool:
        """
        把 PID delta 原样传给服务端 adjust_amplitude。

        服务端架构:
          exposed_adjust_amplitude(device_id, profile, delta)
            → new_amp = device[profile].amplitude + delta
            → device[profile].amplitude = clamp(new_amp, 0, 0.4472)

        本地不做缩放/限幅/last_amp 跟踪 — 所有状态由服务端维护。
        """
        try:
            conn = await self._pool.acquire()
            try:
                await self._call_remote(conn, "adjust_amplitude", delta)
                logger.debug(
                    "AD9910 %s:%d delta=%+.6f 已发",
                    self._ip, self._port, delta,
                )
                return True
            finally:
                await self._pool.release(conn)
        except Exception as e:
            logger.error(
                "AD9910 adjust_amplitude 失败 %s:%d: %s",
                self._ip, self._port, e,
            )
            return False

    # ── 远程调用 ────────────────────────────────────────────────

    async def _call_remote(self, conn: rpyc.Connection, method: str, value: float):
        """
        通过 rpyc 远程调用 AD9910 设备的 API。

        服务器架构:
          AD9910RPyCServer (conn.root) → exposed_get_ad9910_service()
          → AD9910RPyCService → exposed_set_frequency / exposed_set_amplitude

        客户端先获取 service 代理对象，再调用目标方法。
        """
        loop = asyncio.get_running_loop()

        def _call():
            service = conn.root.get_ad9910_service()
            getattr(service, method)(self._device_id, self._profile, value)

        await loop.run_in_executor(None, _call)
