"""
FeedbackCommand — 反馈 Worker 控制命令

UI 发布命令到 feedback.worker.command，FeedbackCommandWorker 消费后调用
FeedbackManager。这样 UI 不直接依赖 manager 的 async 写入 API。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal, Optional

from scope.runtime.pid_controller import PidConfig
from .feedback_worker import FeedbackConfig

FeedbackAction = Literal["add", "pause", "resume", "remove", "update_pid", "load_batch"]


@dataclass
class FeedbackCommand:
    """反馈 Worker 控制命令。"""

    action: FeedbackAction
    worker_id: str
    change_id: int
    config: Optional[FeedbackConfig] = None
    pid_config: Optional[PidConfig] = None
    config_list: Optional[list[dict]] = None
    timestamp: float = field(default_factory=time.monotonic)
