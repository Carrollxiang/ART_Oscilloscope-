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
    eb.register_topic("feedback.status", maxsize=10)
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
        assert config[0]["target"] is None  # 无 target

    async def test_load_config(self, mgr):
        """导入配置重建 worker"""
        status_queue = mgr._event_bus.subscribe("feedback.status")
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
        snapshot = status_queue.get_nowait()
        assert snapshot is not None
        assert snapshot.total_count == 2
        assert status_queue.get_nowait() is None

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


# ── Target 管理 ─────────────────────────────────────────────────

class TestWorkerTarget:
    async def test_get_worker_target_none(self, mgr):
        """无 target 时返回 None"""
        assert mgr.get_worker_target("nonexistent") is None

    async def test_get_worker_target_ad9910(self, mgr):
        """获取 AD9910 target"""
        from scope.io.feedback_worker import Ad9910Target
        cfg = FeedbackConfig(
            worker_id="ad9910-w", measurement_key="CH1_vpp",
            pid_config=PidConfig(preset_value=3.3),
            target=Ad9910Target(ip="10.0.0.1", port=3251, device_id=0x0D11),
        )
        await mgr.add_worker(cfg)
        target = mgr.get_worker_target("ad9910-w")
        assert isinstance(target, Ad9910Target)
        assert target.ip == "10.0.0.1"

    async def test_update_worker_target(self, mgr):
        """更新 target：无→AD9910→RTMQ"""
        from scope.io.feedback_worker import Ad9910Target, RtmqTarget

        cfg = FeedbackConfig(
            worker_id="tgt-test", measurement_key="CH1_vpp",
            pid_config=PidConfig(preset_value=3.3),
            target=None,
        )
        await mgr.add_worker(cfg)

        # 更新到 AD9910
        ad9910 = Ad9910Target(ip="192.168.1.1", port=3251, device_id=0x0D11)
        await mgr.update_worker_target("tgt-test", ad9910)
        assert isinstance(mgr.get_worker_target("tgt-test"), Ad9910Target)

        # 更新到 RTMQ
        rtmq = RtmqTarget(ip="192.168.1.2", port=18861, card_index=1, sbg_channel=2)
        await mgr.update_worker_target("tgt-test", rtmq)
        assert isinstance(mgr.get_worker_target("tgt-test"), RtmqTarget)

        # 更新到 None
        await mgr.update_worker_target("tgt-test", None)
        assert mgr.get_worker_target("tgt-test") is None


# ── 数据分发 ─────────────────────────────────────────────────

class TestDispatch:
    async def test_paused_worker_still_refreshes_subscribed_value(self, mgr, sample_config):
        """暂停的 worker 仍刷新订阅值, 但不执行 PID 计算/发送"""
        await mgr.add_worker(sample_config)
        await mgr.pause_worker("test-w1")
        worker = mgr._workers["test-w1"]
        assert worker.status == SlotStatus.PAUSED

        await mgr.start()
        mgr._event_bus.publish("frame.fitted", make_snapshot(CH1_vpp=2.5))

        # 等待分发循环处理该帧
        for _ in range(100):
            if worker.last_value == 2.5:
                break
            await asyncio.sleep(0.01)

        assert worker.last_value == 2.5, "暂停期间订阅值仍应刷新"
        assert worker.last_error == pytest.approx(3.3 - 2.5)
        assert worker.status == SlotStatus.PAUSED
        assert worker.frames_processed == 0, "暂停期间不执行 PID 计算"

        await mgr.stop()

    async def test_running_worker_unchanged_while_other_paused(self, mgr):
        """一个 worker 暂停不影响其他 RUNNING worker 的分发"""
        cfg1 = FeedbackConfig(
            worker_id="w1", measurement_key="k1",
            pid_config=PidConfig(preset_value=1.0, kp=0.1),
        )
        cfg2 = FeedbackConfig(
            worker_id="w2", measurement_key="k2",
            pid_config=PidConfig(preset_value=2.0, kp=0.1),
        )
        await mgr.add_worker(cfg1)
        await mgr.add_worker(cfg2)
        await mgr.pause_worker("w1")
        w1, w2 = mgr._workers["w1"], mgr._workers["w2"]
        assert w1.status == SlotStatus.PAUSED
        assert w2.status == SlotStatus.RUNNING

        await mgr.start()
        mgr._event_bus.publish("frame.fitted", make_snapshot(k1=0.5, k2=1.5))

        for _ in range(100):
            if w2.last_value == 1.5:
                break
            await asyncio.sleep(0.01)

        assert w1.last_value == 0.5, "暂停 worker 订阅值仍刷新"
        assert w1.frames_processed == 0
        assert w2.last_value == 1.5
        assert w2.frames_processed >= 1, "RUNNING worker 照常执行 PID 计算"

        await mgr.stop()


# ── 订阅生命周期 (v0.8.2 内存修复) ─────────────────────────────

class TestManagerSubscription:
    async def test_start_stop_unsubscribes(self, event_bus, mgr):
        """stop() 后订阅队列被移除, 不再收到数据 (防止重复订阅累积)"""
        await mgr.start()
        assert len(event_bus.metrics()["frame.fitted"]) == 1
        await mgr.stop()
        assert len(event_bus.metrics()["frame.fitted"]) == 0

    async def test_start_twice_subscribes_once(self, event_bus, mgr):
        """重复 start 只保留一个订阅队列"""
        await mgr.start()
        await mgr.start()
        assert len(event_bus.metrics()["frame.fitted"]) == 1
        await mgr.stop()

    async def test_restart_reuses_subscription(self, event_bus, mgr):
        """stop 后再次 start 恢复单个订阅 (幂等)"""
        await mgr.start()
        await mgr.stop()
        await mgr.start()
        assert len(event_bus.metrics()["frame.fitted"]) == 1
        await mgr.stop()
