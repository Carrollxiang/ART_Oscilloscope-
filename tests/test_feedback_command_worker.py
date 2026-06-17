"""
FeedbackCommandWorker 单元测试
"""

import pytest

from scope.io import FeedbackCommand, FeedbackCommandWorker, FeedbackManager
from scope.io.feedback_worker import FeedbackConfig
from scope.runtime import DropStrategy, EventBus, PidConfig


def make_worker():
    event_bus = EventBus()
    event_bus.register_topic(
        "feedback.worker.command",
        maxsize=32,
        on_drop=DropStrategy.BLOCK,
    )
    manager = FeedbackManager()
    worker = FeedbackCommandWorker(event_bus, manager)
    return worker, manager


@pytest.fixture
def sample_config():
    return FeedbackConfig(
        worker_id="w1",
        measurement_key="m0",
        pid_config=PidConfig(preset_value=1.0, kp=0.1),
    )


async def test_add_pause_resume_remove(sample_config):
    worker, manager = make_worker()

    await worker._apply_command(
        FeedbackCommand(
            action="add",
            worker_id="w1",
            config=sample_config,
            change_id=1,
        )
    )
    assert manager.get_active_count() == (1, 1)

    await worker._apply_command(
        FeedbackCommand(action="pause", worker_id="w1", change_id=2)
    )
    assert manager.get_active_count() == (0, 1)

    await worker._apply_command(
        FeedbackCommand(action="resume", worker_id="w1", change_id=3)
    )
    assert manager.get_active_count() == (1, 1)

    await worker._apply_command(
        FeedbackCommand(action="remove", worker_id="w1", change_id=4)
    )
    assert manager.get_active_count() == (0, 0)
    assert worker.metrics["commands_applied"] == 4


async def test_update_pid(sample_config):
    worker, manager = make_worker()
    await worker._apply_command(
        FeedbackCommand(
            action="add",
            worker_id="w1",
            config=sample_config,
            change_id=1,
        )
    )

    new_pid = PidConfig(preset_value=2.0, kp=0.2)
    await worker._apply_command(
        FeedbackCommand(
            action="update_pid",
            worker_id="w1",
            pid_config=new_pid,
            change_id=2,
        )
    )

    assert manager.list_workers()[0]["preset_value"] == 2.0
    assert manager.list_workers()[0]["kp"] == 0.2


async def test_skip_old_command(sample_config):
    worker, manager = make_worker()
    await worker._apply_command(
        FeedbackCommand(
            action="add",
            worker_id="w1",
            config=sample_config,
            change_id=2,
        )
    )
    await worker._apply_command(
        FeedbackCommand(action="remove", worker_id="w1", change_id=1)
    )

    assert manager.get_active_count() == (1, 1)
    assert worker.metrics["commands_skipped"] == 1
