"""
FeedbackSlot — 反馈插槽基类

一个独立的反馈通道，运行时动态插拔。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
from enum import Enum

from scope.model.enums import SlotStatus


@dataclass
class DataSubscription:
    """数据订阅配置"""
    local_key: str
    remote_key: str = ""
    scale: float = 1.0
    offset: float = 0.0
    
    def __post_init__(self):
        if not self.remote_key:
            self.remote_key = self.local_key


@dataclass
class SlotConfig:
    """插槽配置基类"""
    slot_id: str
    subscriptions: list[DataSubscription] = field(default_factory=list)


@dataclass
class SlotInfo:
    """插槽运行信息快照"""
    slot_id: str
    protocol: str
    status: SlotStatus
    target: str


class FeedbackSlot:
    """
    反馈插槽基类。
    
    子类需实现:
      - start(): 创建连接、初始化资源
      - stop(): 关闭连接、释放资源
      - on_data(payload): 推送一帧数据
      - reconfigure(config): 运行时修改配置
    """
    
    protocol: str = "base"
    
    def __init__(self, config: SlotConfig):
        self._config = config
        self._status = SlotStatus.IDLE
    
    @property
    def slot_id(self) -> str:
        return self._config.slot_id
    
    @property
    def status(self) -> SlotStatus:
        return self._status
    
    async def start(self):
        """启动插槽"""
        raise NotImplementedError
    
    async def stop(self):
        """停止插槽"""
        raise NotImplementedError
    
    async def on_data(self, payload: dict[str, Any]):
        """推送数据"""
        raise NotImplementedError
    
    async def reconfigure(self, config: SlotConfig):
        """重新配置"""
        self._config = config
    
    async def pause(self, auto: bool = False):
        """暂停"""
        if self._status == SlotStatus.RUNNING:
            self._status = SlotStatus.PAUSED
    
    async def resume(self):
        """恢复"""
        if self._status == SlotStatus.PAUSED:
            self._status = SlotStatus.RUNNING
    
    def get_info(self) -> SlotInfo:
        """获取运行信息"""
        return SlotInfo(
            slot_id=self.slot_id,
            protocol=self.protocol,
            status=self._status,
            target=self._get_target(),
        )
    
    def _get_target(self) -> str:
        """获取目标描述"""
        return ""
