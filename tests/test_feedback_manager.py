"""
FeedbackManager 单元测试
"""

import asyncio
import pytest

from scope.model.enums import SlotStatus
from scope.runtime import EventBus
from scope.runtime.pid_controller import PidConfig
from scope.io.feedback_worker import FeedbackConfig
from scope.io.feedback_manager import FeedbackManager


@pytest.fixture
def event_bus():
    eb = EventBus()
    eb.register_topic("frame.fitted", maxsize=10)
    return eb


@pytest.fixture
def mgr(event_bus):
    return FeedbackManager(event_bus)


def make_snapshot(**kwargs):
    """创建一个模拟的 FittedSnapshot 替代品"""
    class FakeSnapshot:
        def as_flat_dict(self):
            return kwargs
    return FakeSnapshot()


@pytest.fixture
def sample_config():
    return FeedbackConfig(
        worker_id="test-w1",
        measurement_key="CH1_vpp",
        pid_config=PidConfig(preset_value=3.3, kp=0.1),
    )


# ── 初始化 ─────────────────────────────────────────────────────

class TestManagerInit:
    async def test_init(self, mgr):
        """初始化正确"""
        assert mgr._event_bus is not None
        assert len(mgr._workers) == 0
        assert not mgr._running

    async def test_init_no_eventbus(self):
        """无 EventBus 也可初始化"""
        m = FeedbackManager()
        assert m._event_bus is None


# ── 生命周期 ───────────────────────────────────────────────────

class TestManagerLifecycle:
    async def test_start_stop(self, mgr):
        """start/stop 正常"""
        await mgr.start()
        assert mgr._running
        await mgr.stop()
        assert not mgr._running

    async def test_start_twice(self, mgr):
        """重复 start 安全"""
        await mgr.start()
        await mgr.start()
        assert mgr._running
        await mgr.stop()


# ── Worker 管理 ────────────────────────────────────────────────

class TestWorkerManagement:
    async def test_add_worker(self, mgr, sample_config):
        """添加 worker"""
        wid = await mgr.add_worker(sample_config)
        assert wid == "test-w1"
        assert len(mgr._workers) == 1
        assert mgr._workers["test-w1"].status == SlotStatus.RUNNING

    async def test_add_duplicate(self, mgr, sample_config):
        """重复 worker_id 抛异常"""
        await mgr.add_worker(sample_config)
        with pytest.raises(KeyError):
            await mgr.add_worker(sample_config)

    async def test_remove_worker(self, mgr, sample_config):
        """移除 worker"""
        await mgr.add_worker(sample_config)
        w = await mgr.remove_worker("test-w1")
        assert w is not None
        assert w.worker_id == "test-w1"
        assert len(mgr._workers) == 0

    async def test_remove_nonexistent(self, mgr):
        """移除不存在的 worker 返回 None"""
        w = await mgr.remove_worker("no-such")
        assert w is None

    async def test_pause_resume_worker(self, mgr, sample_config):
        """暂停/恢复 worker"""
        await mgr.add_worker(sample_config)
        await mgr.pause_worker("test-w1")
        assert mgr._workers["test-w1"].status == SlotStatus.PAUSED
        await mgr.resume_worker("test-w1")
        assert mgr._workers["test-w1"].status == SlotStatus.RUNNING

    async def test_stop_all_workers(self, mgr):
        """停止所有 worker"""
        cfg1 = FeedbackConfig(worker_id="w1", measurement_key="key1", pid_config=PidConfig(preset_value=1.0))
        cfg2 = FeedbackConfig(worker_id="w2", measurement_key="key2", pid_config=PidConfig(preset_value=2.0))
        await mgr.add_worker(cfg1)
        await mgr.add_worker(cfg2)
        await mgr.stop_all_workers()
        for w in mgr._workers.values():
            assert w.status == SlotStatus.IDLE


# ── 配置管理 ───────────────────────────────────────────────────

class TestConfigManagement:
    async def test_get_config(self, mgr, sample_config):
        """导出配置正确"""
        await mgr.add_worker(sample_config)
        config = mgr.get_config()
        assert len(config) == 1
        assert config[0]["worker_id"] == "test-w1"
        assert config[0]["measurement_key"] == "CH1_vpp"
        assert config[0]["pid_config"]["preset_value"] == 3.3
        assert config[0]["pid_config"]["kp"] == 0.1

    async def test_load_config(self, mgr):
        """导入配置重建 worker"""
        config_list = [
            {
                "worker_id": "w1",
                "measurement_key": "CH1_vpp",
                "pid_config": {
                    "preset_value": 3.3,
                    "kp": 0.03,
                    "ki": 0.0,
                    "kd": 0.0,
                    "i_limit": 0.1,
                    "output_limit": 0.1,
                    "window_size": 10,
                    "deadband": 0.0,
                },
                "target": None,
            },
            {
                "worker_id": "w2",
                "measurement_key": "CH2_vpp",
                "pid_config": {
                    "preset_value": 5.0,
                    "kp": 0.05,
                    "ki": 0.0,
                    "kd": 0.0,
                    "i_limit": 0.1,
                    "output_limit": 0.1,
                    "window_size": 10,
                    "deadband": 0.0,
                },
                "target": None,
            },
        ]
        await mgr.load_config(config_list)
        assert len(mgr._workers) == 2
        assert mgr._workers["w1"].measurement_key == "CH1_vpp"
        assert mgr._workers["w2"].measurement_key == "CH2_vpp"

    async def test_load_config_replaces_existing(self, mgr, sample_config):
        """加载配置替换现有 worker"""
        await mgr.add_worker(sample_config)
        assert len(mgr._workers) == 1

        await mgr.load_config([])
        assert len(mgr._workers) == 0


# ── 列表 ───────────────────────────────────────────────────────

class TestWorkerList:
    async def test_list_workers_empty(self, mgr):
        """空列表"""
        assert mgr.list_workers() == []

    async def test_list_workers(self, mgr, sample_config):
        """列出 worker"""
        await mgr.add_worker(sample_config)
        workers = mgr.list_workers()
        assert len(workers) == 1
        assert workers[0]["worker_id"] == "test-w1"
        assert workers[0]["status"] == "running"

    async def test_get_active_count(self, mgr, sample_config):
        """活跃计数"""
        await mgr.add_worker(sample_config)
        running, total = mgr.get_active_count()
        assert running == 1
        assert total == 1

        await mgr.pause_worker("test-w1")
        running, total = mgr.get_active_count()
        assert running == 0
        assert total == 1
