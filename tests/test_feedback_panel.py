"""反馈面板新增 ID 的离线回归，不创建硬件 sender。"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication, QDialog

from scope.io.feedback_command_worker import FeedbackCommandWorker
from scope.io.feedback_manager import FeedbackManager
from scope.io.feedback_worker import FeedbackConfig
from scope.runtime import EventBus, PidConfig
from scope.ui.panels.feedback_panel import FeedbackDialog, FeedbackPanel


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


class MeasurementPanelStub:
    def get_measurement_specs(self):
        return [{"tag": "new_measurement"}]


@pytest.mark.parametrize(
    "existing_numbers,counter,expected_numbers",
    [
        ([0, 2, 6, 7, 8, 9, 12, 13, 1, 3, 4, 5], 6, [10, 11, 14]),
        ([0, 2, 6, 7, 8, 9, 12, 13, 1, 3, 4, 5], 0, [10, 11, 14]),
        ([0, 1, 2], 0, [3, 4, 5]),
        ([], 0, [0, 1, 2]),
    ],
)
async def test_add_skips_restored_ids_before_status_refresh(
    qt_app, monkeypatch, existing_numbers, counter, expected_numbers
):
    bus = EventBus()
    bus.register_topic("feedback.worker.command", maxsize=32)
    manager = FeedbackManager()
    command_worker = FeedbackCommandWorker(bus, manager)
    panel = FeedbackPanel(measurement_panel=MeasurementPanelStub(), event_bus=bus)

    for number in existing_numbers:
        await manager.add_worker(FeedbackConfig(
            worker_id=f"w{number}", measurement_key=f"m{number}",
            pid_config=PidConfig(preset_value=1), target=None,
        ))
    panel.on_status_update(manager._build_status_snapshot())
    panel._worker_counter = counter

    def accept_dialog(dialog):
        dialog._on_accept()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(FeedbackDialog, "exec", accept_dialog)
    try:
        # 多次新增期间不刷新状态，仍应生成互不冲突的 ID。
        for number in expected_numbers:
            panel._on_add()
            command = command_worker._queue.get_nowait()
            assert command.worker_id == f"w{number}"
            assert command.config.worker_id == command.worker_id
            await command_worker._apply_command(command)
        assert command_worker.metrics["commands_failed"] == 0
        assert manager.get_active_count()[1] == len(existing_numbers) + len(expected_numbers)
        assert {item["worker_id"] for item in manager.get_config()} == {
            f"w{number}" for number in existing_numbers + expected_numbers
        }
    finally:
        await manager.stop_all_workers()
        panel.close()
        panel.deleteLater()
