"""
FeedbackWorker — 独立反馈单元

被动接收测量值，内部持有 PidController，调用 PID 计算后发送调整指令。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from scope.model.enums import SlotStatus
from scope.runtime.pid_controller import PidConfig, PidController

logger = logging.getLogger(__name__)


# ── 目标设备配置（v0.7 预留） ──────────────────────────────────

@dataclass
class Ad9910Target:
    """AD9910 DDS 设备定位"""
    ip: str
    port: int = 3251
    device_id: int = 0       # hex SN, 如 0x0D11
    profile: int = 0         # 寄存器 profile (0x00~0x07)


@dataclass
class RtmqTarget:
    """RTMQ 白盒子设备定位"""
    ip: str
    port: int = 18861
    card_index: int = 0      # RWG 板卡号
    sbg_channel: int = 0     # 边带通道


TargetConfig = Ad9910Target | RtmqTarget


@dataclass
class FeedbackConfig:
    """反馈 worker 配置"""
    worker_id: str                            # 唯一标识符
    measurement_key: str                      # 订阅的测量项 key, 如 "CH1_vpp"
    pid_config: PidConfig                     # PID 控制器参数
    target: Optional[TargetConfig] = None     # 目标设备配置（v0.7 实现）


class FeedbackWorker:
    """独立反馈 worker — 被动接收，不订阅 EventBus"""

    def __init__(self, config: FeedbackConfig):
        self._config = config
        self._pid = PidController(config.pid_config)
        self._status = SlotStatus.IDLE
        self._target = config.target

    # ── 属性 ────────────────────────────────────────────────────

    @property
    def worker_id(self) -> str:
        return self._config.worker_id

    @property
    def status(self) -> SlotStatus:
        return self._status

    @property
    def measurement_key(self) -> str:
        return self._config.measurement_key

    @property
    def pid_config(self) -> PidConfig:
        return self._config.pid_config

    # ── 生命周期 ───────────────────────────────────────────────

    async def start(self):
        """启动 worker"""
        self._status = SlotStatus.RUNNING
        self._pid.reset()
        logger.info(f'FeedbackWorker "{self.worker_id}" started')

    async def stop(self):
        """停止 worker"""
        self._status = SlotStatus.IDLE
        logger.info(f'FeedbackWorker "{self.worker_id}" stopped')

    async def pause(self):
        """暂停 worker（保留 PID 状态）"""
        if self._status == SlotStatus.RUNNING:
            self._status = SlotStatus.PAUSED
            logger.info(f'FeedbackWorker "{self.worker_id}" paused')

    async def resume(self):
        """恢复 worker"""
        if self._status == SlotStatus.PAUSED:
            self._status = SlotStatus.RUNNING
            logger.info(f'FeedbackWorker "{self.worker_id}" resumed')

    # ── 核心处理 ───────────────────────────────────────────────

    async def process(self, value: float):
        """
        处理单个测量值。

        由 FeedbackManager 调用，传入已提取的测量值。
        """
        if self._status != SlotStatus.RUNNING:
            return

        try:
            delta = self._pid.step(value)
            if delta is not None and self._target:
                await self._send_to_target(delta)
            elif delta is not None:
                logger.debug(
                    f'Worker "{self.worker_id}" computed delta={delta:.6f} '
                    f"(no target configured)"
                )
        except Exception as e:
            logger.error(f'FeedbackWorker "{self.worker_id}" error: {e}', exc_info=True)

    async def _send_to_target(self, delta: float):
        """
        发送调整指令到目标设备（v0.7 实现）。

        Args:
            delta: PID 计算出的调整量
        """
        # TODO: v0.7 实现 AD9910 / RTMQ 目标发送
        logger.debug(f'Worker "{self.worker_id}" delta={delta:.6f}')
        pass
