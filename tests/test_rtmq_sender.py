"""
RtmqSender 单元测试

使用 mock 避免真实 rpyc 连接。
"""

import asyncio
from unittest.mock import MagicMock

import pytest

from scope.io.rtmq_sender import RtmqSender
from scope.io.rpyc_pool import ConnectionPoolManager, PoolConfig


def make_mock_conn():
    conn = MagicMock()
    conn.ping.return_value = None
    conn.root.set_sideband = MagicMock()
    return conn


def _patch_pool_create(pool, mock_conn):
    def _mock_create():
        pool._total_created += 1
        return mock_conn
    pool._create_connection = _mock_create


@pytest.fixture(autouse=True)
def reset_pools():
    ConnectionPoolManager._pools.clear()
    ConnectionPoolManager._refcount.clear()
    yield


@pytest.fixture
def sender():
    """创建 RtmqSender，池的 _create_connection 返回 mock"""
    cfg = PoolConfig(min_size=0, max_size=2, acquire_timeout=1.0)
    s = RtmqSender(ip="192.168.1.200", port=18861, card_index=0, sbg_channel=1, pool_config=cfg)
    mock_conn = make_mock_conn()
    _patch_pool_create(s._pool, mock_conn)
    s._mock_conn = mock_conn
    yield s
    asyncio.run(s.close())


class TestSenderInit:
    def test_init(self, sender):
        assert sender.ip == "192.168.1.200"
        assert sender.port == 18861
        assert sender.card_index == 0
        assert sender.sbg_channel == 1

    def test_init_acquires_pool(self):
        s = RtmqSender("10.0.0.1", 18861)
        assert ("10.0.0.1", 18861) in ConnectionPoolManager._pools
        asyncio.run(s.close())


class TestAdjustDelta:
    async def test_adjust_delta_success(self, sender):
        ok = await sender.adjust_delta(0.05)
        assert ok
        sender._mock_conn.root.set_sideband.assert_called_once_with(0, 1, 0.05)

    async def test_adjust_delta_negative(self, sender):
        ok = await sender.adjust_delta(-0.1)
        assert ok
        sender._mock_conn.root.set_sideband.assert_called_once_with(0, 1, -0.1)

    async def test_adjust_delta_failure(self, sender):
        sender._mock_conn.root.set_sideband.side_effect = Exception("rpc error")
        ok = await sender.adjust_delta(0.1)
        assert not ok


class TestSenderClose:
    async def test_close_releases_pool(self):
        s = RtmqSender("10.0.0.1", 18861)
        assert ConnectionPoolManager._refcount[("10.0.0.1", 18861)] == 1
        await s.close()
        assert ("10.0.0.1", 18861) not in ConnectionPoolManager._pools
