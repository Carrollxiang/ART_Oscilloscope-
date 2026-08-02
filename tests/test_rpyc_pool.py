"""
RpycConnectionPool 单元测试

使用 mock 避免真实 rpyc 连接。策略：通过池实例的 _create_connection 方法注入 mock。
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from scope.io.rpyc_pool import PoolConfig, RpycConnectionPool, ConnectionPoolManager


def make_mock_conn(ping_ok=True):
    """创建模拟 rpyc.Connection，默认 ping 成功"""
    conn = MagicMock()
    conn.ping.return_value = None
    return conn


@pytest.fixture
def pool():
    """创建测试用池（min=0, max=4, 超时 1s），_create_connection 返回 mock"""
    cfg = PoolConfig(min_size=0, max_size=4, acquire_timeout=1.0)
    p = RpycConnectionPool("127.0.0.1", 9999, cfg)
    mock_conn = make_mock_conn()
    # mock _create_connection 并保持 _total_created 计数
    def _mock_create():
        p._total_created += 1
        return mock_conn
    p._create_connection = _mock_create
    p._mock_conn = mock_conn
    yield p
    asyncio.run(p.close())


# ── 初始化 ─────────────────────────────────────────────────────

class TestPoolInit:
    def test_init_defaults(self):
        """默认配置初始化"""
        p = RpycConnectionPool("10.0.0.1", 3251)
        assert p.ip == "10.0.0.1"
        assert p.port == 3251
        assert p.size == 0

    def test_init_with_config(self):
        """自定义配置"""
        cfg = PoolConfig(min_size=0, max_size=2)
        p = RpycConnectionPool("10.0.0.1", 3251, cfg)
        assert p._config.max_size == 2
        assert p._config.min_size == 0
        assert p.size == 0


# ── acquire / release ──────────────────────────────────────────

class TestAcquireRelease:
    async def test_acquire_creates_connection(self, pool):
        """acquire 创建新连接"""
        conn = await pool.acquire()
        assert conn is pool._mock_conn
        assert pool._total_created == 1
        assert pool._total_acquired == 1

    async def test_acquire_reuse(self, pool):
        """release 后 acquire 复用连接"""
        conn1 = await pool.acquire()
        assert pool.size == 1

        await pool.release(conn1)
        assert pool.size == 1
        assert len(pool._available) == 1

        conn2 = await pool.acquire()
        assert conn2 is conn1  # 复用同一连接
        assert pool._total_created == 1  # 没有新建

    async def test_acquire_discard_dead(self, pool):
        """acquire 时 ping 失败则丢弃死连接"""
        pool._available.append(pool._mock_conn)  # 将当前 mock 放入 available
        pool._mock_conn.ping.side_effect = Exception("dead")

        new_mock = make_mock_conn()
        def _new_create():
            pool._total_created += 1
            return new_mock
        pool._create_connection = _new_create

        conn = await pool.acquire()
        assert conn is new_mock  # 死连接被丢弃，拿到新建的
        assert pool._total_discarded == 1
        assert pool._total_created == 1  # 创建了新的

    async def test_acquire_max_size_timeout(self):
        """池满时等待，超时抛 TimeoutError"""
        cfg = PoolConfig(min_size=0, max_size=1, acquire_timeout=0.5)
        p = RpycConnectionPool("127.0.0.1", 9999, cfg)
        mock_conn = make_mock_conn()
        def _create():
            p._total_created += 1
            return mock_conn
        p._create_connection = _create

        conn = await p.acquire()  # 占满单槽
        with pytest.raises(TimeoutError):
            await p.acquire()  # 超时
        await p.release(conn)
        await p.close()

    async def test_release_unknown(self, pool):
        """release 未知连接不崩溃"""
        unknown = MagicMock()
        await pool.release(unknown)

    async def test_acquire_connection_failure(self):
        """连接失败抛异常"""
        p = RpycConnectionPool("10.0.0.1", 9999)
        p._create_connection = MagicMock(side_effect=Exception("connection refused"))
        with pytest.raises(Exception):
            await p.acquire()
        await p.close()

    async def test_metrics(self, pool):
        """metrics 返回正确统计"""
        await pool.acquire()
        m = pool.metrics
        assert m["ip"] == "127.0.0.1"
        assert m["port"] == 9999
        assert m["total_created"] == 1
        assert m["total_acquired"] == 1


# ── ConnectionPoolManager ──────────────────────────────────────

class TestConnectionPoolManager:
    def setup_method(self):
        ConnectionPoolManager._pools.clear()
        ConnectionPoolManager._refcount.clear()

    @pytest.mark.asyncio
    async def test_acquire_pool_singleton(self):
        pool1 = ConnectionPoolManager.acquire_pool("10.0.0.1", 3251)
        pool2 = ConnectionPoolManager.acquire_pool("10.0.0.1", 3251)
        assert pool1 is pool2

    @pytest.mark.asyncio
    async def test_acquire_release_refcount(self):
        ConnectionPoolManager.acquire_pool("10.0.0.1", 3251)
        ConnectionPoolManager.acquire_pool("10.0.0.1", 3251)
        assert ConnectionPoolManager._refcount[("10.0.0.1", 3251)] == 2

        await ConnectionPoolManager.release_pool("10.0.0.1", 3251)
        assert ConnectionPoolManager._refcount[("10.0.0.1", 3251)] == 1
        assert ("10.0.0.1", 3251) in ConnectionPoolManager._pools

    @pytest.mark.asyncio
    async def test_release_last_closes_pool(self):
        ConnectionPoolManager.acquire_pool("10.0.0.1", 3251)
        pool = ConnectionPoolManager._pools[("10.0.0.1", 3251)]
        with patch.object(pool, "close") as mock_close:
            await ConnectionPoolManager.release_pool("10.0.0.1", 3251)
            mock_close.assert_called_once()
        assert ("10.0.0.1", 3251) not in ConnectionPoolManager._pools

    @pytest.mark.asyncio
    async def test_release_pool_unknown(self):
        await ConnectionPoolManager.release_pool("no-such", 1234)

    @pytest.mark.asyncio
    async def test_close_all(self):
        ConnectionPoolManager.acquire_pool("10.0.0.1", 3251)
        ConnectionPoolManager.acquire_pool("10.0.0.2", 18861)
        await ConnectionPoolManager.close_all()
        assert len(ConnectionPoolManager._pools) == 0


# ── 并发安全回归 (自死锁 / 取消安全) ────────────────────────────

class TestConcurrencySafety:
    """v0.7 修复: 持锁建连自死锁回归 + acquire 取消安全"""

    def test_create_connection_no_deadlock_under_lock(self):
        """
        回归: 持 Condition 锁调用 _create_connection 不应自死锁。

        旧实现中 _create_connection 的 logger.debug 访问 self.size,
        size property 内部 with self._lock (与 Condition 同一把
        threading.Lock, 不可重入) → 自死锁, 线程永不返回。
        """
        import threading
        import time

        import rpyc
        from rpyc.utils.server import ThreadedServer

        port = 33995

        class Svc(rpyc.Service):
            def exposed_ping(self):
                return True

        server = ThreadedServer(
            Svc, hostname="127.0.0.1", port=port,
            protocol_config={"allow_public_attrs": True, "allow_pickle": True},
        )
        t = threading.Thread(target=server.start, daemon=True)
        t.start()
        try:
            pool = RpycConnectionPool(
                "127.0.0.1", port, PoolConfig(connect_timeout=2.0, acquire_timeout=2.0),
            )
            result = {}

            def _call():
                with pool._cond:                     # 持锁 (旧实现在此自死锁)
                    conn = pool._create_connection()
                    conn.close()
                result["ok"] = True

            th = threading.Thread(target=_call, daemon=True)
            th.start()
            th.join(timeout=8)
            assert result.get("ok"), "持锁建连卡死 (自死锁回归)!"
        finally:
            server.close()

    async def test_acquire_cancelled_returns_connection(self, pool):
        """acquire 被取消后, 连接建立完成仍归还池 (不泄漏)"""
        import time as _time

        orig = pool._create_connection

        def slow_create():
            _time.sleep(0.5)                 # 模拟慢建连
            return orig()

        pool._create_connection = slow_create

        task = asyncio.create_task(pool.acquire())
        await asyncio.sleep(0.1)             # acquire 进行中
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        await asyncio.sleep(1.0)             # 等 executor 完成 + 清理协程归还
        m = pool.metrics
        assert m["available"] == 1, f"取消后连接应归还 available: {m}"
        assert m["in_use"] == 0, f"取消后不应泄漏 in_use: {m}"
        assert m["total_released"] == 1, f"应 release 一次: {m}"

    async def test_acquire_after_cancel_pool_usable(self, pool):
        """取消后池仍可正常 acquire (未损坏)"""
        task = asyncio.create_task(pool.acquire())
        await asyncio.sleep(0.01)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0.3)

        conn = await pool.acquire()
        assert conn is not None
        await pool.release(conn)
        assert pool.metrics["available"] == 1
