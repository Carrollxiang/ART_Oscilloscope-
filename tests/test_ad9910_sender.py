"""
Ad9910Sender 单元测试

使用 mock 避免真实 rpyc 连接。策略：mock 池实例的 _create_connection 方法。
"""

import asyncio
from unittest.mock import MagicMock

import pytest

from scope.io.ad9910_sender import Ad9910Mapping, Ad9910Sender
from scope.io.rpyc_pool import ConnectionPoolManager, PoolConfig


def make_mock_conn():
    """创建模拟 rpyc.Connection。

    模拟服务端架构:
      conn.root.get_ad9910_service() → service (含 set_frequency / set_amplitude)
    """
    conn = MagicMock()
    conn.ping.return_value = None
    # 创建 mock service 对象
    service = MagicMock()
    conn.root.get_ad9910_service.return_value = service
    return conn


def _patch_pool_create(pool, mock_conn):
    """替换池的 _create_connection 以返回 mock 且保持计数"""
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
    """创建 Ad9910Sender，池的 _create_connection 返回 mock"""
    cfg = PoolConfig(min_size=0, max_size=2, acquire_timeout=1.0)
    s = Ad9910Sender(
        ip="192.168.1.100",
        port=3251,
        device_id=0x0D11,
        profile=0,
        pool_config=cfg,
    )
    # 替换池的 _create_connection
    mock_conn = make_mock_conn()
    _patch_pool_create(s._pool, mock_conn)
    s._mock_conn = mock_conn
    yield s
    asyncio.run(s.close())


# ── Ad9910Mapping ─────────────────────────────────────────────

class TestMapping:
    def test_mapping_defaults(self):
        m = Ad9910Mapping()
        assert m.mode == "amplitude"
        assert m.center_freq == 100e6

    def test_mapping_invalid_mode(self):
        with pytest.raises(ValueError, match="mode"):
            Ad9910Mapping(mode="invalid")

    def test_mapping_amplitude_mode(self):
        m = Ad9910Mapping(mode="amplitude")
        assert m.mode == "amplitude"


# ── 初始化 ─────────────────────────────────────────────────────

class TestSenderInit:
    def test_init(self, sender):
        assert sender.ip == "192.168.1.100"
        assert sender.port == 3251
        assert sender.device_id == 0x0D11
        assert sender.profile == 0

    def test_init_acquires_pool(self):
        """初始化时获取连接池"""
        s = Ad9910Sender("10.0.0.1", 3251)
        assert ("10.0.0.1", 3251) in ConnectionPoolManager._pools
        asyncio.run(s.close())


# ── set_frequency ─────────────────────────────────────────────

class TestSetFrequency:
    async def test_set_frequency_happy_path(self, sender):
        """正常设置频率"""
        ok = await sender.set_frequency(100e6)
        assert ok
        assert sender._last_freq == 100e6
        sender._mock_conn.root.get_ad9910_service().set_frequency.assert_called_once_with(0x0D11, 0, 100e6)

    async def test_set_frequency_clamp_low(self, sender):
        """频率低于下限自动限幅"""
        ok = await sender.set_frequency(-100)
        assert ok
        sender._mock_conn.root.get_ad9910_service().set_frequency.assert_called_once_with(0x0D11, 0, 0.0)

    async def test_set_frequency_clamp_high(self, sender):
        """频率高于上限自动限幅"""
        ok = await sender.set_frequency(1e9)
        assert ok
        sender._mock_conn.root.get_ad9910_service().set_frequency.assert_called_once_with(0x0D11, 0, 400e6)

    async def test_set_frequency_failure(self, sender):
        """连接异常返回 False"""
        sender._mock_conn.root.get_ad9910_service().set_frequency.side_effect = Exception("rpc error")
        ok = await sender.set_frequency(100e6)
        assert not ok


# ── set_amplitude ─────────────────────────────────────────────

class TestSetAmplitude:
    async def test_set_amplitude_happy(self, sender):
        """正常设置幅度"""
        ok = await sender.set_amplitude(0.5)
        assert ok
        assert sender._last_amp == 0.5
        sender._mock_conn.root.get_ad9910_service().set_amplitude.assert_called_once_with(0x0D11, 0, 0.5)

    async def test_set_amplitude_clamp(self, sender):
        """幅度超出范围自动限幅"""
        await sender.set_amplitude(2.0)
        sender._mock_conn.root.get_ad9910_service().set_amplitude.assert_called_with(0x0D11, 0, 1.0)

        await sender.set_amplitude(-0.5)
        sender._mock_conn.root.get_ad9910_service().set_amplitude.assert_called_with(0x0D11, 0, 0.0)


# ── adjust_delta ──────────────────────────────────────────────

class TestAdjustDelta:
    async def test_adjust_delta_frequency(self):
        """PID delta → 频率调整（显式 frequency mapping）"""
        cfg = PoolConfig(min_size=0, max_size=2)
        s = Ad9910Sender(
            "10.0.0.1", 3251,
            device_id=0x0D11, profile=0,
            mapping=Ad9910Mapping(mode="frequency"),
            pool_config=cfg,
        )
        mock_conn = make_mock_conn()
        _patch_pool_create(s._pool, mock_conn)

        ok = await s.adjust_delta(0.05)  # 100e6 + 0.05 * 1.0 = 100000000.05
        assert ok
        mock_conn.root.get_ad9910_service().set_frequency.assert_called_once_with(
            0x0D11, 0, pytest.approx(100000000.05)
        )
        await s.close()

    async def test_adjust_delta_frequency_negative(self):
        """负 delta（显式 frequency mapping）"""
        cfg = PoolConfig(min_size=0, max_size=2)
        s = Ad9910Sender(
            "10.0.0.1", 3251,
            device_id=0x0D11, profile=0,
            mapping=Ad9910Mapping(mode="frequency"),
            pool_config=cfg,
        )
        mock_conn = make_mock_conn()
        _patch_pool_create(s._pool, mock_conn)

        ok = await s.adjust_delta(-0.1)  # 100e6 - 0.1 = 99999999.9
        assert ok
        mock_conn.root.get_ad9910_service().set_frequency.assert_called_once_with(
            0x0D11, 0, pytest.approx(99999999.9)
        )
        await s.close()

    async def test_adjust_delta_amplitude_default(self, sender):
        """默认 mode（amplitude）→ adjust_amplitude 原样传 delta"""
        ok = await sender.adjust_delta(0.1)
        assert ok
        sender._mock_conn.root.get_ad9910_service().adjust_amplitude.assert_called_once_with(
            0x0D11, 0, pytest.approx(0.1)
        )

    async def test_adjust_delta_amplitude(self):
        """幅度模式 delta 原样传服务端 adjust_amplitude"""
        cfg = PoolConfig(min_size=0, max_size=2)
        s = Ad9910Sender(
            "10.0.0.1", 3251,
            mapping=Ad9910Mapping(mode="amplitude", center_freq=0),
            pool_config=cfg,
        )
        mock_conn = make_mock_conn()
        _patch_pool_create(s._pool, mock_conn)

        ok = await s.adjust_delta(-0.05)
        assert ok
        mock_conn.root.get_ad9910_service().adjust_amplitude.assert_called_once_with(
            0, 0, pytest.approx(-0.05)
        )
        await s.close()


# ── close ─────────────────────────────────────────────────────

class TestSenderClose:
    async def test_close_releases_pool(self):
        """close 释放连接池引用"""
        s = Ad9910Sender("10.0.0.1", 3251)
        assert ConnectionPoolManager._refcount[("10.0.0.1", 3251)] == 1
        await s.close()
        assert ("10.0.0.1", 3251) not in ConnectionPoolManager._pools
