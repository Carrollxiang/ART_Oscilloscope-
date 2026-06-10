"""
FeedbackWorker 单元测试
"""

import pytest

from scope.model.enums import SlotStatus
from scope.runtime.pid_controller import PidConfig
from scope.io.feedback_worker import FeedbackConfig, FeedbackWorker


@pytest.fixture
def worker_cfg():
    return FeedbackConfig(
        worker_id="test-worker",
        measurement_key="CH1_vpp",
        pid_config=PidConfig(preset_value=3.3, kp=0.1, ki=0.0, kd=0.0),
    )


@pytest.fixture
def worker(worker_cfg):
    return FeedbackWorker(worker_cfg)


# ── 初始化 ─────────────────────────────────────────────────────

class TestWorkerInit:
    async def test_init(self, worker):
        """初始化配置正确"""
        assert worker.worker_id == "test-worker"
        assert worker.measurement_key == "CH1_vpp"
        assert worker.status == SlotStatus.IDLE

    async def test_init_with_target_none(self, worker_cfg):
        """target 默认为 None"""
        w = FeedbackWorker(worker_cfg)
        assert w._target is None


# ── 生命周期 ───────────────────────────────────────────────────

class TestWorkerLifecycle:
    async def test_start(self, worker):
        """start 后状态变为 RUNNING"""
        await worker.start()
        assert worker.status == SlotStatus.RUNNING

    async def test_stop(self, worker):
        """stop 后状态变为 IDLE"""
        await worker.start()
        await worker.stop()
        assert worker.status == SlotStatus.IDLE

    async def test_start_stop_twice(self, worker):
        """重复 start/stop 不报错"""
        await worker.start()
        await worker.stop()
        await worker.start()
        assert worker.status == SlotStatus.RUNNING
        await worker.stop()


# ── 暂停/恢复 ─────────────────────────────────────────────────

class TestWorkerPauseResume:
    async def test_pause(self, worker):
        """pause 后状态变为 PAUSED"""
        await worker.start()
        await worker.pause()
        assert worker.status == SlotStatus.PAUSED

    async def test_resume(self, worker):
        """resume 后恢复 RUNNING"""
        await worker.start()
        await worker.pause()
        await worker.resume()
        assert worker.status == SlotStatus.RUNNING

    async def test_pause_when_idle(self, worker):
        """IDLE 状态 pause 无效果"""
        await worker.pause()
        assert worker.status == SlotStatus.IDLE

    async def test_resume_when_running(self, worker):
        """RUNNING 状态 resume 无效果"""
        await worker.start()
        await worker.resume()
        assert worker.status == SlotStatus.RUNNING


# ── process ────────────────────────────────────────────────────

class TestWorkerProcess:
    async def test_process_running(self, worker):
        """RUNNING 状态下调用 PID 计算"""
        await worker.start()
        # 只验证不抛异常
        await worker.process(3.0)

    async def test_process_paused(self, worker):
        """PAUSED 状态不处理"""
        await worker.start()
        await worker.pause()
        # 不抛异常即可
        await worker.process(3.0)

    async def test_process_idle(self, worker):
        """IDLE 状态不处理"""
        await worker.process(3.0)

    async def test_process_error_isolation(self, worker):
        """异常处理不崩溃"""
        await worker.start()
        # 传入无效值（如 None 不会发生，但极端情况）
        await worker.process(3.0)
        # 能正常继续
        await worker.process(3.1)


# ── PID 集成 ──────────────────────────────────────────────────

class TestWorkerPidIntegration:
    async def test_process_changes_pid_state(self, worker):
        """process 后 PID 内部状态更新"""
        await worker.start()
        assert worker._pid.metrics["errors_count"] == 0

        await worker.process(3.0)  # error = 0.3
        assert worker._pid.metrics["errors_count"] == 1

        await worker.process(3.1)  # error = 0.2
        assert worker._pid.metrics["errors_count"] == 2

    async def test_process_after_reset(self, worker):
        """stop/start 后 PID 重置"""
        await worker.start()
        await worker.process(3.0)
        assert worker._pid.metrics["errors_count"] == 1

        await worker.stop()
        await worker.start()  # start 会 reset PID
        assert worker._pid.metrics["errors_count"] == 0
