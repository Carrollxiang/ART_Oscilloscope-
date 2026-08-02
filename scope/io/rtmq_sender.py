"""
RtmqSender — RTMQ 白盒子设备目标发送器。

通过 rpyc 连接远程 RTMQ 服务器，控制边带通道参数。
将 PID 计算的 delta 值映射为设备的调整量。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import rpyc

from scope.io.rpyc_pool import ConnectionPoolManager, PoolConfig

logger = logging.getLogger(__name__)


class RtmqSender:
    """
    RTMQ 目标发送器。

    每个实例通过连接池管理器获取共享 rpyc 连接。
    使用方式:
        sender = RtmqSender("192.168.1.100", 18861, card_index=0, sbg_channel=1)
        await sender.adjust_delta(0.05)  # PID delta → 设备调整
    """

    def __init__(
        self,
        ip: str,
        port: int = 18861,
        card_index: int = 0,
        sbg_channel: int = 0,
        pool_config: Optional[PoolConfig] = None,
    ):
        self._ip = ip
        self._port = port
        self._card_index = card_index
        self._sbg_channel = sbg_channel
        self._pool_config = pool_config
        self._pool = ConnectionPoolManager.acquire_pool(
            ip, port, pool_config,
        )
        self._last_value: Optional[float] = None

    @property
    def ip(self) -> str:
        return self._ip

    @property
    def port(self) -> int:
        return self._port

    @property
    def card_index(self) -> int:
        return self._card_index

    @property
    def sbg_channel(self) -> int:
        return self._sbg_channel

    async def close(self):
        """释放连接池引用。"""
        await ConnectionPoolManager.release_pool(self._ip, self._port)

    # ── 核心 API ────────────────────────────────────────────────

    async def adjust_delta(self, delta: float) -> bool:
        """
        应用 PID 计算出的 delta 调整量到 RTMQ 边带通道。

        Args:
            delta: PID 输出调整量（有符号），直接作为目标值发送。

        Returns:
            True 发送成功, False 失败
        """
        try:
            conn = await self._pool.acquire()
            try:
                await self._call_remote(conn, delta)
                self._last_value = delta
                logger.debug(
                    "RTMQ %s:%d card=%d ch=%d delta=%+.6f",
                    self._ip, self._port,
                    self._card_index, self._sbg_channel, delta,
                )
                return True
            finally:
                await self._pool.release(conn)
        except Exception as e:
            logger.error(
                "RTMQ adjust_delta 失败 %s:%d card=%d ch=%d: %s",
                self._ip, self._port,
                self._card_index, self._sbg_channel, e,
            )
            return False

    # ── 远程调用 ────────────────────────────────────────────────

    async def _call_remote(self, conn: rpyc.Connection, value: float):
        """
        通过 rpyc 远程调用 RTMQ 设备的 API。

        设备接口假设:
          - conn.root.set_sideband(card_index, sbg_channel, value)
        """
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: conn.root.set_sideband(
                self._card_index, self._sbg_channel, value,
            ),
        )
