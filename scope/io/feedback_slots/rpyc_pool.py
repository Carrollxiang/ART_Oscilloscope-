"""
rpyc 连接池 — 线程安全, 支持借/还/健康检查/自动收缩

为什么需要连接池:
  - rpyc 握手涉及 TCP + 对象序列化协商, 每次新建开销大
  - 采集触发频率可达 KHz 级, 不可能每个触发都建新连接
  - 多 slot 共享一个连接池比每触发建连接省端口

设计:
  - 一个 slot 对应一个连接池 (指向同一个远程仪器)
  - acquire() 从池中借一个空闲连接 (阻塞直到可用)
  - release() 归还, 其他协程/线程可复用
  - 空闲超时自动关闭 (减少资源占用)
  - 使用 threading.Condition 避免忙等
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional

import rpyc

logger = logging.getLogger(__name__)


@dataclass
class PooledConnection:
    """池中的一条连接"""
    conn: rpyc.Connection
    in_use: bool = False
    last_used: float = 0.0
    created_at: float = 0.0


class RpycConnectionPool:
    """
    线程安全的 rpyc 连接池。

    用法:
        pool = RpycConnectionPool("192.168.1.100", 12345, max_size=4)
        conn = pool.acquire()
        try:
            result = conn.root.exposed_some_method(data)
        finally:
            pool.release(conn)
    """

    def __init__(
        self,
        host: str,
        port: int,
        min_size: int = 1,
        max_size: int = 4,
        connect_timeout: float = 5.0,
        idle_timeout: float = 60.0,
        acquire_timeout: float = 10.0,
    ):
        self._host = host
        self._port = port
        self._min = min_size
        self._max = max_size
        self._connect_timeout = connect_timeout
        self._idle_timeout = idle_timeout
        self._acquire_timeout = acquire_timeout

        self._pool: list[PooledConnection] = []
        self._available = threading.Condition(threading.Lock())
        self._closed = False
        self._total_created = 0
        self._total_destroyed = 0

        # 预创建 min_size 个连接
        self._warm_up()

    # ── 公共 API ───────────────────────────────────────────────

    def acquire(self) -> rpyc.Connection:
        """
        从池中借一条连接。

        阻塞直到:
          - 有空闲且健康的连接 → 返回
          - 池没满, 创建新连接 → 返回
          - acquire_timeout 超时 → 抛 TimeoutError
          - 池已关闭 → 抛 RuntimeError
        """
        with self._available:
            deadline = time.monotonic() + self._acquire_timeout

            while not self._closed:
                # 1. 找空闲的健康连接
                for pc in self._pool:
                    if not pc.in_use:
                        if self._is_healthy(pc.conn):
                            pc.in_use = True
                            pc.last_used = time.monotonic()
                            logger.debug(f"Acquired existing conn (pool={self._size()})")
                            return pc.conn
                        else:
                            # 连接已死, 移除
                            self._pool.remove(pc)
                            self._close_conn(pc.conn)
                            logger.warning(
                                f"Removed dead conn, pool={self._size()}"
                            )
                            break  # 重新循环

                # 2. 池没满 → 建新连接
                if len(self._pool) < self._max:
                    conn = self._create_conn()
                    pc = PooledConnection(
                        conn=conn,
                        in_use=True,
                        last_used=time.monotonic(),
                        created_at=time.monotonic(),
                    )
                    self._pool.append(pc)
                    logger.info(
                        f"Created new conn (pool={self._size()}, "
                        f"total={self._total_created})"
                    )
                    return conn

                # 3. 池满了 → 等 (直到有人归还或超时)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"rpyc 连接池已满 ({self._max}/{self._max}), "
                        f"等待 {self._acquire_timeout}s 超时"
                    )
                self._available.wait(timeout=remaining)

            raise RuntimeError("连接池已关闭")

    def release(self, conn: rpyc.Connection):
        """
        归还连接。

        如果连接池已关闭或连接已死, 直接关闭它。
        """
        with self._available:
            for pc in self._pool:
                if pc.conn is conn:
                    pc.in_use = False
                    pc.last_used = time.monotonic()
                    self._available.notify()
                    logger.debug(f"Released conn (pool={self._size()})")
                    return

            # 不在池中 → 直接关
            self._close_conn(conn)

    def close(self):
        """关闭池中所有连接, 拒绝后续 acquire 请求"""
        with self._available:
            self._closed = True
            for pc in self._pool:
                self._close_conn(pc.conn)
            self._pool.clear()
            self._available.notify_all()
            logger.info(
                f"ConnectionPool closed: "
                f"created={self._total_created}, destroyed={self._total_destroyed}"
            )

    @property
    def size(self) -> tuple[int, int, int]:
        """
        返回 (active, idle, total) 三值。
        用于 UI 显示和监控。
        """
        with self._available:
            active = sum(1 for pc in self._pool if pc.in_use)
            idle = len(self._pool) - active
            return active, idle, len(self._pool)

    def status_text(self) -> str:
        """一行状态, 用于日志"""
        active, idle, total = self.size
        return f"{self._host}:{self._port} [{active}活跃/{idle}空闲/{total}总计]"

    # ── 内部方法 ───────────────────────────────────────────────

    def _warm_up(self):
        """预创建 min_size 个连接"""
        for _ in range(self._min):
            try:
                conn = self._create_conn()
                pc = PooledConnection(
                    conn=conn,
                    in_use=False,
                    last_used=time.monotonic(),
                    created_at=time.monotonic(),
                )
                self._pool.append(pc)
                logger.debug(f"Warm-up conn created (pool={self._size()})")
            except Exception as e:
                logger.warning(f"Warm-up connection failed: {e}")
                break  # 预创建失败不阻塞

    def _create_conn(self) -> rpyc.Connection:
        """创建一条新 rpyc 连接"""
        try:
            conn = rpyc.connect(
                self._host,
                self._port,
                config={
                    "sync_request_timeout": self._connect_timeout,
                    "allow_public_attrs": True,
                },
            )
            self._total_created += 1
            return conn
        except Exception as e:
            raise ConnectionError(
                f"无法连接到 rpyc 服务 {self._host}:{self._port}: {e}"
            ) from e

    def _is_healthy(self, conn: rpyc.Connection) -> bool:
        """检查连接是否健康"""
        try:
            conn.ping()
            return True
        except Exception:
            return False

    def _close_conn(self, conn: rpyc.Connection):
        """安全关闭一条连接"""
        try:
            conn.close()
        except Exception:
            pass
        self._total_destroyed += 1

    def _size(self) -> str:
        return f"{len(self._pool)}/{self._max}"

    def __del__(self):
        if not self._closed:
            self.close()
