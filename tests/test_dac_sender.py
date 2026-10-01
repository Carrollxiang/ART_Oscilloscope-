"""
DACSender 单元测试

使用 mock 避免真实 rpyc 连接。
覆盖: read→clamp→set_ab(0, v)、0/5V 双向限幅、scale 缩放、
      read 异常响应、rpyc 异常、use_set_ab=False 回退 set_b、
      连接池共享与 close 释放引用。
"""

import asyncio
from unittest.mock import MagicMock

import pytest

from scope.io.dac_sender import DEFAULT_VOLT_RANGE, DACMapping, DACSender
from scope.io.rpyc_pool import ConnectionPoolManager, PoolConfig


def make_mock_conn(b_voltage=1.0, status="success"):
    """构造 mock rpyc 连接: root.read() 返回服务端真实响应结构。"""
    conn = MagicMock()
    conn.ping.return_value = None
    conn.root.read.return_value = {
        "status": status,
        "command": "READ",
        "a_voltage": 0.0,
        "b_voltage": b_voltage,
        "device_response": ["A=0mV B=1000mV ZEROA=-32 ZEROB=-32"],
        "command_count": 26,
        "timestamp": "2026-10-01T12:07:46.155",
    }
    conn.root.set_ab = MagicMock()
    conn.root.set_b = MagicMock()
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
    """创建 DACSender，池的 _create_connection 返回 mock"""
    cfg = PoolConfig(min_size=0, max_size=2, acquire_timeout=1.0)
    s = DACSender(ip="192.168.1.58", port=18863, pool_config=cfg)
    mock_conn = make_mock_conn()
    _patch_pool_create(s._pool, mock_conn)
    s._mock_conn = mock_conn
    yield s
    asyncio.run(s.close())


class TestDACMapping:
    def test_default_range(self):
        m = DACMapping()
        assert (m.min_volt, m.max_volt) == DEFAULT_VOLT_RANGE

    def test_clamp_volt(self):
        m = DACMapping()
        assert m.clamp_volt(9.9) == 5.0
        assert m.clamp_volt(-1.0) == 0.0
        assert m.clamp_volt(2.5) == 2.5

    def test_apply_delta(self):
        m = DACMapping(scale=2.0)
        assert m.apply_delta(1.0, 0.5) == 2.0

    def test_apply_delta_clamps(self):
        m = DACMapping()
        assert m.apply_delta(4.9, 0.5) == 5.0
        assert m.apply_delta(0.1, -0.5) == 0.0

    def test_invalid_range(self):
        with pytest.raises(ValueError):
            DACMapping(min_volt=5.0, max_volt=0.0)


class TestSenderInit:
    def test_init_defaults(self, sender):
        assert sender.ip == "192.168.1.58"
        assert sender.port == 18863
        assert sender.mapping.scale == 1.0
        assert (sender.mapping.min_volt, sender.mapping.max_volt) == DEFAULT_VOLT_RANGE
        assert sender.use_set_ab is True
        assert sender.last_volt is None

    def test_init_acquires_pool(self):
        s = DACSender("10.0.0.1", 18863)
        assert ("10.0.0.1", 18863) in ConnectionPoolManager._pools
        asyncio.run(s.close())


class TestReadVoltage:
    async def test_read_voltage_success(self, sender):
        assert await sender.read_voltage() == 1.0

    async def test_read_voltage_bad_status(self, sender):
        sender._mock_conn.root.read.return_value = {"status": "error", "b_voltage": 1.0}
        assert await sender.read_voltage() is None

    async def test_read_voltage_missing_field(self, sender):
        sender._mock_conn.root.read.return_value = {"status": "success"}
        assert await sender.read_voltage() is None

    async def test_read_voltage_non_numeric(self, sender):
        sender._mock_conn.root.read.return_value = {"status": "success", "b_voltage": "abc"}
        assert await sender.read_voltage() is None

    async def test_read_voltage_rpc_error(self, sender):
        sender._mock_conn.root.read.side_effect = Exception("rpc error")
        assert await sender.read_voltage() is None


class TestSetVoltage:
    async def test_set_voltage_uses_set_ab_with_zero_a(self, sender):
        assert await sender.set_voltage(2.5)
        sender._mock_conn.root.set_ab.assert_called_once_with(0, 2.5)
        assert sender.last_volt == 2.5

    async def test_set_voltage_clamps_high(self, sender):
        assert await sender.set_voltage(7.5)
        sender._mock_conn.root.set_ab.assert_called_once_with(0, 5.0)

    async def test_set_voltage_clamps_low(self, sender):
        assert await sender.set_voltage(-3.0)
        sender._mock_conn.root.set_ab.assert_called_once_with(0, 0.0)

    async def test_set_voltage_fallback_set_b(self):
        cfg = PoolConfig(min_size=0, max_size=2, acquire_timeout=1.0)
        s = DACSender("10.1.1.1", 18863, use_set_ab=False, pool_config=cfg)
        mock_conn = make_mock_conn()
        _patch_pool_create(s._pool, mock_conn)
        assert await s.set_voltage(1.5)
        mock_conn.root.set_b.assert_called_once_with(1.5)
        mock_conn.root.set_ab.assert_not_called()
        await s.close()

    async def test_set_voltage_rpc_error(self, sender):
        sender._mock_conn.root.set_ab.side_effect = Exception("rpc error")
        assert not await sender.set_voltage(1.0)


class TestAdjustDelta:
    async def test_adjust_delta_based_on_device_voltage(self, sender):
        # 设备当前 1.0V, delta=+0.5, scale=1.0 → 1.5V
        assert await sender.adjust_delta(0.5)
        sender._mock_conn.root.set_ab.assert_called_once_with(0, 1.5)
        assert sender.last_volt == 1.5

    async def test_adjust_delta_negative(self, sender):
        assert await sender.adjust_delta(-0.25)
        sender._mock_conn.root.set_ab.assert_called_once_with(0, 0.75)

    async def test_adjust_delta_clamps_to_max(self, sender):
        sender._mock_conn.root.read.return_value = {"status": "success", "b_voltage": 4.9}
        assert await sender.adjust_delta(0.5)
        sender._mock_conn.root.set_ab.assert_called_once_with(0, 5.0)

    async def test_adjust_delta_clamps_to_min(self, sender):
        sender._mock_conn.root.read.return_value = {"status": "success", "b_voltage": 0.1}
        assert await sender.adjust_delta(-0.5)
        sender._mock_conn.root.set_ab.assert_called_once_with(0, 0.0)

    async def test_adjust_delta_no_send_when_read_fails(self, sender):
        sender._mock_conn.root.read.return_value = {"status": "error"}
        assert not await sender.adjust_delta(0.5)
        sender._mock_conn.root.set_ab.assert_not_called()

    async def test_adjust_delta_applies_scale(self):
        cfg = PoolConfig(min_size=0, max_size=2, acquire_timeout=1.0)
        s = DACSender("10.2.2.2", 18863, mapping=DACMapping(scale=10.0), pool_config=cfg)
        mock_conn = make_mock_conn(b_voltage=1.0)
        _patch_pool_create(s._pool, mock_conn)
        assert await s.adjust_delta(0.1)          # 1.0 + 0.1*10 = 2.0
        mock_conn.root.set_ab.assert_called_once_with(0, 2.0)
        await s.close()


class TestSenderClose:
    async def test_close_releases_pool(self):
        s = DACSender("10.0.0.1", 18863)
        assert ConnectionPoolManager._refcount[("10.0.0.1", 18863)] == 1
        await s.close()
        assert ("10.0.0.1", 18863) not in ConnectionPoolManager._pools

    async def test_pool_shared_between_senders(self):
        s1 = DACSender("10.9.9.9", 18863)
        s2 = DACSender("10.9.9.9", 18863)
        assert s1._pool is s2._pool
        assert ConnectionPoolManager._refcount[("10.9.9.9", 18863)] == 2
        await s1.close()
        await s2.close()
