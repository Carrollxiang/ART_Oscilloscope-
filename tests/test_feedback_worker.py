"""
FeedbackWorker 单元测试
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

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


# ── 目标设备集成 ──────────────────────────────────────────────

class TestWorkerTarget:
    async def test_start_with_ad9910_creates_sender(self, worker_cfg):
        """配置 AD9910 target 时 start 创建 sender"""
        from scope.io.feedback_worker import Ad9910Target

        cfg = FeedbackConfig(
            worker_id="ad9910-worker",
            measurement_key="CH1_vpp",
            pid_config=PidConfig(preset_value=3.3, kp=0.1),
            target=Ad9910Target(ip="192.168.1.100", port=3251, device_id=0x0D11),
        )
        with patch("scope.io.ad9910_sender.Ad9910Sender") as MockSender:
            # Make close() awaitable
            MockSender.return_value.close = AsyncMock()
            w = FeedbackWorker(cfg)
            await w.start()
            MockSender.assert_called_once_with(
                ip="192.168.1.100",
                port=3251,
                device_id=0x0D11,
                profile=0,
            )
            assert w._sender is not None
            await w.stop()

    async def test_start_without_target_no_sender(self, worker):
        """无 target 时不创建 sender"""
        await worker.start()
        assert worker._sender is None
        await worker.stop()

    async def test_stop_closes_sender(self, worker_cfg):
        """stop 时关闭 sender"""
        from scope.io.feedback_worker import Ad9910Target

        cfg = FeedbackConfig(
            worker_id="test",
            measurement_key="CH1_vpp",
            pid_config=PidConfig(preset_value=3.3),
            target=Ad9910Target(ip="10.0.0.1", port=3251),
        )
        with patch("scope.io.ad9910_sender.Ad9910Sender") as MockSender:
            mock_instance = MagicMock()
            mock_instance.close = AsyncMock()
            MockSender.return_value = mock_instance

            w = FeedbackWorker(cfg)
            await w.start()
            await w.stop()
            mock_instance.close.assert_called_once()

    async def test_process_with_ad9910_calls_send(self, worker_cfg):
        """RUNNING + AD9910 target 时 process 调用 sender.adjust_delta"""
        from scope.io.feedback_worker import Ad9910Target

        cfg = FeedbackConfig(
            worker_id="test",
            measurement_key="CH1_vpp",
            pid_config=PidConfig(preset_value=3.3, kp=0.1),
            target=Ad9910Target(ip="10.0.0.1", port=3251),
        )
        with patch("scope.io.ad9910_sender.Ad9910Sender") as MockSender:
            mock_instance = MagicMock()
            mock_instance.adjust_delta = AsyncMock(return_value=True)
            mock_instance.close = AsyncMock()
            MockSender.return_value = mock_instance

            w = FeedbackWorker(cfg)
            await w.start()
            # preset=3.3, measured=3.0 → error=0.3 → delta>0
            await w.process(3.0)
            mock_instance.adjust_delta.assert_called_once()
            await w.stop()

    async def test_process_without_target_no_send(self, worker):
        """无 target 时 process 不调用发送"""
        await worker.start()
        with patch.object(worker, "_send_to_target") as mock_send:
            await worker.process(3.0)
            mock_send.assert_not_called()
        await worker.stop()


# ── target 序列化 ─────────────────────────────────────────────

class TestTargetSerialization:
    def test_target_to_dict_none(self):
        from scope.io.feedback_worker import target_to_dict
        assert target_to_dict(None) is None

    def test_target_to_dict_ad9910(self):
        from scope.io.feedback_worker import Ad9910Target, target_to_dict

        t = Ad9910Target(ip="10.0.0.1", port=3251, device_id=0x0D11, profile=1)
        d = target_to_dict(t)
        assert d["type"] == "Ad9910Target"
        assert d["ip"] == "10.0.0.1"
        assert d["port"] == 3251
        assert d["device_id"] == 0x0D11
        assert d["profile"] == 1

    def test_target_from_dict_ad9910(self):
        from scope.io.feedback_worker import Ad9910Target, target_from_dict

        d = {"type": "Ad9910Target", "ip": "10.0.0.1", "port": 3251,
             "device_id": 0x0D11, "profile": 1}
        t = target_from_dict(d)
        assert isinstance(t, Ad9910Target)
        assert t.ip == "10.0.0.1"
        assert t.port == 3251
        assert t.device_id == 0x0D11

    def test_target_from_dict_none(self):
        from scope.io.feedback_worker import target_from_dict
        assert target_from_dict(None) is None

    def test_target_from_dict_unknown_type(self):
        from scope.io.feedback_worker import target_from_dict
        d = {"type": "UnknownTarget", "ip": "10.0.0.1"}
        assert target_from_dict(d) is None

    def test_target_roundtrip(self):
        from scope.io.feedback_worker import (
            Ad9910Target,
            target_from_dict,
            target_to_dict,
        )

        t = Ad9910Target(ip="10.0.0.1", port=3251, device_id=0x0D11, profile=1)
        d = target_to_dict(t)
        t2 = target_from_dict(d)
        assert t == t2


# ── 发送失败冷却 / in-flight 防重叠 ─────────────────────────────

class TestWorkerSendCooldown:
    """发送失败冷却与 in-flight 防重叠 (v0.7 修复)"""

    @staticmethod
    def _make_sender(impl):
        """构造 sender 桩: 实例属性方法, 避免类方法绑定"""
        s = type("S", (), {})()
        s.adjust_delta = impl
        return s

    async def test_failure_sets_cooldown_and_skips_next_frame(self):
        """发送失败 → 冷却期内跳过发送, 冷却过期后恢复"""
        from scope.io.feedback_worker import FeedbackWorker, Ad9910Target
        w = FeedbackWorker(FeedbackConfig(
            worker_id="cd1", measurement_key="m1",
            pid_config=PidConfig(preset_value=3.3),
            target=Ad9910Target(ip="127.0.0.1", port=1),
        ))
        await w.start()
        calls = []

        async def fail(delta):
            calls.append(delta)
            return False

        w._sender = self._make_sender(fail)

        await w.process(1.0)                 # 第1帧: 发送失败 → 冷却
        assert w._next_retry_ts > 0, "失败后应进入冷却"
        await w.process(1.1)                 # 第2帧: 冷却期内 → 跳过
        assert len(calls) == 1, f"冷却期内不应再发送, calls={calls}"

        w._next_retry_ts = 0.0               # 模拟冷却过期
        await w.process(1.2)                 # 第3帧: 重新尝试
        assert len(calls) == 2, "冷却过期后应重试"

    async def test_inflight_skips_overlapping_frame(self):
        """上一帧发送未完成时, 重叠帧跳过 (不并发堆积)"""
        from scope.io.feedback_worker import FeedbackWorker, Ad9910Target
        w = FeedbackWorker(FeedbackConfig(
            worker_id="cd2", measurement_key="m1",
            pid_config=PidConfig(preset_value=3.3),
            target=Ad9910Target(ip="127.0.0.1", port=1),
        ))
        await w.start()
        gate = asyncio.Event()
        started = []

        async def slow(delta):
            started.append(delta)
            await gate.wait()
            return True

        w._sender = self._make_sender(slow)

        t1 = asyncio.create_task(w.process(2.0))
        await asyncio.sleep(0.05)            # 第一次发送进行中
        assert w._processing is True
        await w.process(2.1)                 # 重叠帧 → 跳过
        assert len(started) == 1, f"in-flight 应跳过重叠帧, started={started}"
        gate.set()
        await t1

    async def test_send_success_clears_cooldown_state(self):
        """发送成功时 _processing 复位, 不残留状态"""
        from scope.io.feedback_worker import FeedbackWorker, Ad9910Target
        w = FeedbackWorker(FeedbackConfig(
            worker_id="cd3", measurement_key="m1",
            pid_config=PidConfig(preset_value=3.3),
            target=Ad9910Target(ip="127.0.0.1", port=1),
        ))
        await w.start()

        async def ok_send(delta):
            return True

        w._sender = self._make_sender(ok_send)
        await w.process(1.0)
        assert w._processing is False
        assert w._next_retry_ts == 0.0, "成功不应进入冷却"
