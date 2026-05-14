"""
rpyc 反馈插槽 — 通过 rpyc 向实验室仪器发送测量数据

使用连接池复用 rpyc 连接, 避免每次触发都重新握手。
on_data() 是 async 方法, 底层 rpyc 同步调用通过 run_in_executor 桥接。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from scope.model.enums import SlotStatus
from .base import FeedbackSlot, SlotConfig, DataSubscription
from .rpyc_pool import RpycConnectionPool

logger = logging.getLogger(__name__)


@dataclass
class RpycSlotConfig(SlotConfig):
    """rpyc 插槽专用配置"""

    host: str = "127.0.0.1"
    port: int = 18861
    remote_method: str = "exposed_update"  # 远程对象上调用的方法名

    # 连接池参数
    pool_min: int = 1
    pool_max: int = 4
    connect_timeout: float = 5.0
    idle_timeout: float = 60.0
    acquire_timeout: float = 10.0

    # 数据组装方式
    # "flat": {"CH1_Vpp": 3.3, "CH1_Freq": 1000.0}
    # "grouped": {"CH1": {"Vpp": 3.3, "Freq": 1000.0}, ...}
    payload_format: str = "flat"


class RpycFeedbackSlot(FeedbackSlot):
    """
    基于 rpyc 连接池的反馈插槽。

    用法:
        config = RpycSlotConfig(
            slot_id="scope-to-oscilloscope",
            host="192.168.1.100",
            port=18861,
            remote_method="exposed_update",
            subscriptions=[DataSubscription(local_key="CH1_Vpp")],
        )
        slot = RpycFeedbackSlot(config)
        await slot.start()
        await slot.on_data({"CH1_Vpp": 3.3})
        await slot.stop()
    """

    def __init__(self, config: RpycSlotConfig):
        super().__init__(config)
        self._rpyc_config: RpycSlotConfig = config
        self._pool: Optional[RpycConnectionPool] = None
        self._executor: Optional[asyncio.AbstractEventLoop] = None

    # ── 属性 ───────────────────────────────────────────────────

    @property
    def protocol(self) -> str:
        return "rpyc"

    def _get_target(self) -> str:
        return f"{self._rpyc_config.host}:{self._rpyc_config.port}"

    # ── 生命周期 ───────────────────────────────────────────────

    async def start(self):
        if self._status == SlotStatus.RUNNING:
            return

        cfg = self._rpyc_config
        self._pool = RpycConnectionPool(
            host=cfg.host,
            port=cfg.port,
            min_size=cfg.pool_min,
            max_size=cfg.pool_max,
            connect_timeout=cfg.connect_timeout,
            idle_timeout=cfg.idle_timeout,
            acquire_timeout=cfg.acquire_timeout,
        )
        self._status = SlotStatus.RUNNING
        self._sent_count = 0
        self._error_count = 0
        self._last_error = ""
        logger.info(
            f"[{cfg.slot_id}] RpycSlot started → "
            f"{cfg.host}:{cfg.port}/{cfg.remote_method} "
            f"pool=[{cfg.pool_min}..{cfg.pool_max}] "
            f"subs={[s.local_key for s in cfg.subscriptions]}"
        )

    async def stop(self):
        if self._status == SlotStatus.IDLE:
            return
        self._status = SlotStatus.IDLE
        if self._pool:
            await asyncio.get_event_loop().run_in_executor(None, self._pool.close)
            self._pool = None
        logger.info(
            f"[{self._rpyc_config.slot_id}] RpycSlot stopped "
            f"(sent={self._sent_count}, errors={self._error_count})"
        )

    # ── 数据推送 ───────────────────────────────────────────────

    async def on_data(self, payload: dict[str, Any]):
        """
        将 payload 通过 rpyc 推送给远程仪器。

        payload 由 FeedbackManager 根据 subscriptions 预组装好,
        格式为 {remote_key: value}。
        """
        if self._status != SlotStatus.RUNNING:
            logger.debug(f"[{self._config.slot_id}] skip: slot not running")
            return

        if not payload:
            return

        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, self._do_rpyc_call, payload)
            self._count_sent()
        except Exception as e:
            self._count_error(str(e))

    # ── 运行时重配 ─────────────────────────────────────────────

    async def reconfigure(self, config: SlotConfig):
        if not isinstance(config, RpycSlotConfig):
            raise TypeError(f"RpycFeedbackSlot 需要 RpycSlotConfig, 收到 {type(config)}")

        old_host = self._rpyc_config.host
        old_port = self._rpyc_config.port
        self._rpyc_config = config
        self._config = config  # 基类配置同步更新

        # 如果目标地址变了, 重建连接池
        if (config.host, config.port) != (old_host, old_port):
            logger.info(
                f"[{config.slot_id}] target changed "
                f"{old_host}:{old_port} → {config.host}:{config.port}, "
                f"rebuilding pool"
            )
            await self.stop()
            await self.start()

    # ── 内部实现 ───────────────────────────────────────────────

    def _do_rpyc_call(self, payload: dict[str, Any]):
        """
        同步 rpyc 调用 (在 executor 线程中执行)。

        从连接池借一条连接, 调用远程方法, 然后归还。
        """
        if not self._pool:
            raise RuntimeError("连接池未初始化")

        conn = self._pool.acquire()
        try:
            # 获取远程 root 对象并调用方法
            root = conn.root
            method = getattr(root, self._rpyc_config.remote_method)
            method(self._format_payload(payload))
            logger.debug(
                f"[{self._config.slot_id}] sent {len(payload)} items "
                f"via {self._rpyc_config.remote_method}"
            )
        except Exception as e:
            logger.error(
                f"[{self._config.slot_id}] rpyc call failed: {e} "
                f"(pool: {self._pool.status_text()})"
            )
            raise
        finally:
            if self._pool:
                self._pool.release(conn)

    def _format_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """根据 payload_format 转换数据格式"""
        if self._rpyc_config.payload_format == "grouped":
            grouped: dict[str, dict[str, Any]] = {}
            for key, value in payload.items():
                parts = key.split("_", 1)
                channel = parts[0] if len(parts) > 1 else "_global"
                meas = parts[1] if len(parts) > 1 else key
                if channel not in grouped:
                    grouped[channel] = {}
                grouped[channel][meas] = value
            return grouped
        # "flat"
        return payload
