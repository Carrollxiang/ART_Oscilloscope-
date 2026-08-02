"""
RpycConnectionPool — 按目标 (ip,port) 分组的共享 rpyc 连接池。

设计:
  - ConnectionPoolManager 单例管理全局池，引用计数自动回收
  - 每个 (ip,port) 一个 RpycConnectionPool 实例
  - asyncio 友好: acquire/release/close 均可 await
"""

from __future__ import annotations

import asyncio
import logging
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import rpyc

logger = logging.getLogger(__name__)


@dataclass
class PoolConfig:
    """连接池配置"""
    min_size: int = 1                     # 最小保持连接数
    max_size: int = 4                     # 最大连接数
    acquire_timeout: float = 5.0          # 获取连接超时 (秒)
    idle_timeout: float = 60.0            # 空闲连接回收 (秒)
    connect_timeout: float = 3.0          # 新建连接超时 (秒)
    rpyc_config: Optional[dict] = None    # rpyc 连接 config 覆盖项 (默认注入 allow_* 标志)


class RpycConnectionPool:
    """
    单个 (ip, port) 目标的 rpyc 连接池。

    线程安全: threading.Lock + Condition 保护借还操作。
    健康检查: acquire 时 ping，死连接自动丢弃。
    """

    def __init__(self, ip: str, port: int, config: Optional[PoolConfig] = None):
        self._ip = ip
        self._port = port
        self._config = config or PoolConfig()
        self._rpyc_config_override = self._config.rpyc_config
        self._available: list[rpyc.Connection] = []
        self._in_use: set[int] = set()       # id(conn) 用于跟踪
        self._creating: int = 0              # 锁外建连中的数量 (防超限并发建连)
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._closed = False
        self._total_created = 0
        self._total_acquired = 0
        self._total_released = 0
        self._total_discarded = 0

    # ── 属性 ────────────────────────────────────────────────────

    @property
    def ip(self) -> str:
        return self._ip

    @property
    def port(self) -> int:
        return self._port

    @property
    def size(self) -> int:
        """当前池大小 (可用 + 在用)"""
        with self._lock:
            return len(self._available) + len(self._in_use)

    @property
    def metrics(self) -> dict:
        with self._lock:
            return {
                "ip": self._ip,
                "port": self._port,
                "available": len(self._available),
                "in_use": len(self._in_use),
                "total_created": self._total_created,
                "total_acquired": self._total_acquired,
                "total_released": self._total_released,
                "total_discarded": self._total_discarded,
            }

    # ── 核心操作 ─────────────────────────────────────────────────

    async def acquire(self) -> rpyc.Connection:
        """获取一个可用连接（asyncio 友好）。超时抛 TimeoutError。

        取消安全: rpyc.connect 在 executor 线程中执行, 无法被 asyncio
        取消中断。若调用方协程被取消 (如 manager 停止), 连接仍会建立;
        本方法会等待其完成后归还到池中, 避免连接泄漏。
        """
        loop = asyncio.get_running_loop()
        fut = loop.run_in_executor(None, self._acquire_sync)
        try:
            return await asyncio.shield(fut)
        except asyncio.CancelledError:
            async def _cleanup():
                try:
                    conn = await asyncio.shield(fut)
                    await self.release(conn)
                except BaseException:
                    pass
            loop.create_task(_cleanup())
            raise

    async def release(self, conn: rpyc.Connection):
        """归还连接（asyncio 友好）。不做健康检查，死连接由 acquire 检测。"""
        conn_id = id(conn)
        with self._lock:
            if conn_id not in self._in_use:
                logger.warning("release() called on unknown connection: %s", conn)
                return
            self._in_use.discard(conn_id)
            if self._closed:
                self._discard_unlocked(conn)
            else:
                self._available.append(conn)
                self._total_released += 1
                self._cond.notify()

    async def close(self):
        """关闭池中所有连接。"""
        with self._lock:
            self._closed = True
            all_conns = self._available[:] + [
                conn for conn in self._available  # 避免引用问题
            ]
            self._available.clear()
            self._in_use.clear()
            self._cond.notify_all()

        # 在锁外关闭连接（I/O 操作）
        for conn in all_conns:
            try:
                conn.close()
            except Exception:
                pass

    # ── 同步内部方法 ───────────────────────────────────────────

    def _acquire_sync(self) -> rpyc.Connection:
        """同步获取连接（在 executor 中运行）。"""
        deadline = time.monotonic() + self._config.acquire_timeout

        while True:
            # ── 阶段 1: 锁内取空闲连接 / 判断是否需要建连 ──
            with self._cond:
                # 1. 尝试取空闲连接 (ping 健康检查)
                while self._available:
                    conn = self._available.pop()
                    try:
                        conn.ping()
                    except Exception:
                        self._discard_unlocked(conn)
                        continue
                    self._in_use.add(id(conn))
                    self._total_acquired += 1
                    return conn

                if self._closed:
                    raise RuntimeError("连接池已关闭")

                # 2. 池满或在建连接已达上限 → 等待归还/建连完成
                if len(self._in_use) + self._creating >= self._config.max_size:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(
                            f"连接池已满 ({self._config.max_size}), "
                            f"超时 {self._config.acquire_timeout}s: "
                            f"{self._ip}:{self._port}"
                        )
                    self._cond.wait(remaining)
                    continue

                # 3. 预留建连槽位后释放锁, 锁外建连。
                #    rpyc.connect 最坏 ~18s (3s × 6 次退避重试), 持锁执行会
                #    阻塞 release/close; 且建连后的日志若访问池属性
                #    (如 size) 会因 threading.Lock 不可重入而自死锁。
                self._creating += 1

            # ── 阶段 2: 锁外建连 ──
            try:
                conn = self._create_connection()
            except BaseException:
                with self._cond:
                    self._creating -= 1
                    self._cond.notify_all()
                raise

            # ── 阶段 3: 回锁登记 ──
            with self._cond:
                self._creating -= 1
                if self._closed:
                    # 建连期间池被关闭 → 丢弃新连接
                    self._cond.notify_all()
                    try:
                        conn.close()
                    except Exception:
                        pass
                    raise RuntimeError("连接池已关闭")
                self._in_use.add(id(conn))
                self._total_acquired += 1
                self._cond.notify_all()
                return conn

    def _create_connection(self) -> rpyc.Connection:
        """创建新 rpyc 连接（含 TCP connect 超时）。

        rpyc.connect() 默认不设置 socket connect 超时，
        OS 层面可能阻塞 20+ 秒（Windows）。这里通过
        socket.setdefaulttimeout 临时设置 connect 超时。

        默认注入 allow_public_attrs / allow_pickle / allow_all_attrs，
        与 AD9910 / RTMQ 服务端 exposed_ 之外属性访问兼容；
        PoolConfig.rpyc_config 可覆盖单项。
        """
        timeout = self._config.connect_timeout
        saved = socket.getdefaulttimeout()
        socket.setdefaulttimeout(timeout)
        try:
            rpyc_cfg: dict = {
                "sync_request_timeout": timeout,
                "allow_public_attrs": True,
                "allow_pickle": True,
                "allow_all_attrs": True,
            }
            if self._rpyc_config_override:
                rpyc_cfg.update(self._rpyc_config_override)
            conn = rpyc.connect(
                self._ip,
                self._port,
                config=rpyc_cfg,
            )
            self._total_created += 1
            # 注意: 此处不能访问需要持锁的属性 (如 size), 否则在
            # _acquire_sync 持锁调用的路径上会触发不可重入锁自死锁。
            logger.debug(
                "新连接 %s:%d (created=%d)",
                self._ip, self._port, self._total_created,
            )
            return conn
        except TimeoutError:
            logger.error("连接超时 %s:%d (%.1fs)", self._ip, self._port, timeout)
            raise
        except ConnectionRefusedError:
            logger.error("连接被拒绝 %s:%d — 服务器未运行？", self._ip, self._port)
            raise
        except OSError as e:
            logger.error("连接失败 %s:%d: %s", self._ip, self._port, e)
            raise
        finally:
            socket.setdefaulttimeout(saved)

    def _discard_unlocked(self, conn: rpyc.Connection):
        """丢弃死连接（调用方需持有 _lock）。"""
        self._total_discarded += 1
        try:
            conn.close()
        except Exception:
            pass


class ConnectionPoolManager:
    """
    全局连接池管理器（单例）。

    按 (ip, port) 分组合享连接池。引用计数管理：每个 worker 注册时
    acquire_pool()，销毁时 release_pool()。计数归零时自动关闭池。
    """

    _instance: Optional[ConnectionPoolManager] = None
    _pools: dict[tuple[str, int], RpycConnectionPool] = {}
    _refcount: dict[tuple[str, int], int] = {}
    _lock = threading.Lock()

    def __new__(cls) -> ConnectionPoolManager:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def acquire_pool(
        cls,
        ip: str,
        port: int,
        config: Optional[PoolConfig] = None,
    ) -> RpycConnectionPool:
        """获取或创建共享连接池（引用计数 +1）。"""
        key = (ip, port)
        with cls._lock:
            if key not in cls._pools:
                cls._pools[key] = RpycConnectionPool(ip, port, config)
                cls._refcount[key] = 0
            cls._refcount[key] += 1
            logger.debug(
                "acquire_pool %s:%d refcount=%d",
                ip, port, cls._refcount[key],
            )
            return cls._pools[key]

    @classmethod
    async def release_pool(cls, ip: str, port: int):
        """释放引用计数。归零时关闭池。"""
        key = (ip, port)
        with cls._lock:
            if key not in cls._refcount:
                return
            cls._refcount[key] -= 1
            logger.debug(
                "release_pool %s:%d refcount=%d",
                ip, port, cls._refcount[key],
            )
            if cls._refcount[key] > 0:
                return
            pool = cls._pools.pop(key, None)
            del cls._refcount[key]

        if pool:
            await pool.close()
            logger.info("连接池已关闭 %s:%d", ip, port)

    @classmethod
    async def close_all(cls):
        """关闭所有连接池。"""
        with cls._lock:
            keys = list(cls._pools.keys())
            pools = {k: cls._pools.pop(k) for k in keys}
            cls._refcount.clear()

        for (ip, port), pool in pools.items():
            await pool.close()
            logger.info("close_all: 连接池已关闭 %s:%d", ip, port)
