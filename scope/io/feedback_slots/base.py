"""
反馈插槽基类 — FeedbackSlot ABC

所有协议实现继承此基类。
核心接口是 on_data(), 由 FeedbackManager 在每次采集完成后调用。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from scope.model.enums import SlotStatus

logger = logging.getLogger(__name__)


@dataclass
class DataSubscription:
    """
    单个测量项的订阅描述。

    每个 slot 订阅一组测量项, FeedbackManager 在 dispatch 时
    从 AnalysisResult.measurements 中提取对应的值, 传给 slot。
    """
    local_key: str         # 本系统内的测量项 key, 如 "CH1_Vpp"
    remote_key: str = ""   # 远程仪器上的参数名, 为空时与 local_key 相同
    scale: float = 1.0     # 缩放系数
    offset: float = 0.0    # 偏移量

    def __post_init__(self):
        if not self.remote_key:
            self.remote_key = self.local_key


@dataclass
class SlotConfig:
    """插槽通用配置"""
    slot_id: str
    label: str = ""                     # 人类可读标签
    subscriptions: list[DataSubscription] = field(default_factory=list)


@dataclass
class SlotInfo:
    """插槽运行时快照 — 用于 UI 显示和日志"""
    slot_id: str
    label: str
    protocol: str
    status: str
    target: str
    subscriptions: list[str]
    sent_count: int
    error_count: int
    last_error: str = ""
    last_sent_at: float = 0.0


class FeedbackSlot(ABC):
    """
    反馈插槽抽象基类

    生命周期:
        create → start() → on_data() → on_data() → ... → stop() → delete
                          → reconfigure() (运行时修改参数)

    每个 slot 在独立 asyncio task 中管理自己的生命周期,
    但 on_data() 由 FeedbackManager 在同一调度协程中同步调用。
    """

    def __init__(self, config: SlotConfig):
        self._config = config
        self._status = SlotStatus.IDLE
        self._sent_count = 0
        self._error_count = 0
        self._last_error = ""
        self._last_sent_at = 0.0

    # ── 生命周期 ────────────────────────────────────────────────

    @abstractmethod
    async def start(self):
        """
        启动插槽。
        创建连接、打开端口等初始化动作在此完成。
        """
        ...

    @abstractmethod
    async def stop(self):
        """
        停止插槽。
        关闭连接、释放资源。必须可重入 (多次调用安全)。
        """
        ...

    # ── 暂停/恢复 ──────────────────────────────────────────────

    async def pause(self):
        """
        暂停推送。

        连接池保持打开, 但不再发送数据。
        dispatch() 会跳过 PAUSED 状态的 slot。
        """
        if self._status == SlotStatus.RUNNING:
            self._status = SlotStatus.PAUSED
            logger.info(f"[{self._config.slot_id}] 已暂停")

    async def resume(self):
        """
        恢复推送。
        """
        if self._status == SlotStatus.PAUSED:
            self._status = SlotStatus.RUNNING
            logger.info(f"[{self._config.slot_id}] 已恢复")

    # ── 数据推送 ───────────────────────────────────────────────

    @abstractmethod
    async def on_data(self, payload: dict[str, Any]):
        """
        推送一帧数据。

        payload: 根据 subscriptions 从 AnalysisResult 提取的 {remote_key: value} 字典。
                 由 FeedbackManager 在 dispatch() 中预组装好, slot 只需发送。
        """
        ...

    # ── 运行时重配 ─────────────────────────────────────────────

    @abstractmethod
    async def reconfigure(self, config: SlotConfig):
        """
        运行时修改配置。
        修改目标地址、订阅项、连接池大小等时调用。
        """
        ...

    # ── 公共方法 ───────────────────────────────────────────────

    def get_info(self) -> SlotInfo:
        """获取运行快照"""
        return SlotInfo(
            slot_id=self._config.slot_id,
            label=self._config.label or self._config.slot_id,
            protocol=self.protocol,
            status=self._status.value,
            target=self._get_target(),
            subscriptions=[s.local_key for s in self._config.subscriptions],
            sent_count=self._sent_count,
            error_count=self._error_count,
            last_error=self._last_error,
            last_sent_at=self._last_sent_at,
        )

    @property
    def slot_id(self) -> str:
        return self._config.slot_id

    @property
    def status(self) -> SlotStatus:
        return self._status

    @property
    @abstractmethod
    def protocol(self) -> str:
        """返回协议标识, 如 "rpyc" """
        ...

    @abstractmethod
    def _get_target(self) -> str:
        """返回目标地址摘要, 用于 UI 显示"""
        ...

    # ── 子类辅助 ───────────────────────────────────────────────

    def _count_sent(self):
        self._sent_count += 1
        self._last_sent_at = __import__("time").monotonic()

    def _count_error(self, msg: str):
        self._error_count += 1
        self._last_error = msg
        logger.error(f"[{self._config.slot_id}] {msg}")
