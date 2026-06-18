"""
FeedbackCommandWorker — 消费 feedback.worker.command 控制面事件。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from scope.runtime import EventBus
from .feedback_command import FeedbackCommand
from .feedback_manager import FeedbackManager

logger = logging.getLogger(__name__)


class FeedbackCommandWorker:
    """将 EventBus 命令应用到 FeedbackManager。"""

    def __init__(self, event_bus: EventBus, feedback_manager: FeedbackManager):
        self._queue = event_bus.subscribe("feedback.worker.command")
        self._feedback_manager = feedback_manager
        self._running = False
        self._last_change_id: Optional[int] = None
        self._commands_received = 0
        self._commands_applied = 0
        self._commands_skipped = 0
        self._commands_failed = 0

    @property
    def metrics(self) -> dict:
        return {
            "commands_received": self._commands_received,
            "commands_applied": self._commands_applied,
            "commands_skipped": self._commands_skipped,
            "commands_failed": self._commands_failed,
            "queue_size": self._queue.qsize,
        }

    async def run(self):
        """在 asyncio loop 中运行，消费反馈控制命令。"""
        self._running = True
        logger.info("FeedbackCommandWorker 已启动")
        while self._running:
            try:
                command = self._queue.get_nowait()
                if command is not None:
                    self._commands_received += 1
                    await self._apply_command(command)
                    await asyncio.sleep(0)
                else:
                    await asyncio.sleep(0.05)
            except Exception as e:
                self._commands_failed += 1
                logger.error(f"FeedbackCommandWorker 异常: {e}", exc_info=True)
                await asyncio.sleep(0.2)

    async def _apply_command(self, command: FeedbackCommand):
        """应用单条命令。"""
        if (
            self._last_change_id is not None
            and command.change_id <= self._last_change_id
        ):
            self._commands_skipped += 1
            return

        self._last_change_id = command.change_id

        try:
            if command.action == "add":
                if command.config is None:
                    raise ValueError("add command requires config")
                await self._feedback_manager.add_worker(command.config)
            elif command.action == "pause":
                await self._feedback_manager.pause_worker(command.worker_id)
            elif command.action == "resume":
                await self._feedback_manager.resume_worker(command.worker_id)
            elif command.action == "remove":
                await self._feedback_manager.remove_worker(command.worker_id)
            elif command.action == "update_pid":
                if command.pid_config is None:
                    raise ValueError("update_pid command requires pid_config")
                await self._feedback_manager.update_worker_pid(
                    command.worker_id,
                    command.pid_config,
                )
            elif command.action == "load_batch":
                if command.config_list is None:
                    raise ValueError("load_batch requires config_list")
                await self._feedback_manager.load_config(command.config_list)
            else:
                raise ValueError(f"unknown feedback command: {command.action}")
        except Exception:
            self._commands_failed += 1
            logger.exception(
                "FeedbackCommandWorker 执行命令失败: action=%s worker_id=%s",
                command.action,
                command.worker_id,
            )
            return

        self._commands_applied += 1
        logger.info(
            "FeedbackCommandWorker 已应用命令 #%s: %s %s",
            command.change_id,
            command.action,
            command.worker_id,
        )

    def stop(self):
        """停止 Worker。"""
        self._running = False
        logger.info(
            "FeedbackCommandWorker 已停止 "
            "(received=%s, applied=%s, skipped=%s, failed=%s)",
            self._commands_received,
            self._commands_applied,
            self._commands_skipped,
            self._commands_failed,
        )
