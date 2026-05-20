"""
PID 反馈插槽 — 闭环 PID 控制器, 向 AD9910 DDS 或 RTMQ 白盒子推送控制量

设计:
  - 继承 FeedbackSlot ABC, 实现 on_data(payload)
  - PID 状态 (误差累积, 上次误差) 封装在实例内, 不泄露
  - AD9910 / RTMQ 严格分离, 通过 dataclass 区分配置
  - 每个 slot 管理自己的 RpycConnectionPool

用法:
    cfg = PidSlotConfig(
        slot_id="ch1-to-dds",
        pid=PidParams(preset_value=0.8, kp=0.03),
        measurement_key="CH1_Vpp",
        target=Ad9910Target(ip="192.168.1.20", port=3251, device_id=0x0D11, profile=0x00),
    )
    slot = PidFeedbackSlot(cfg)
    await slot.start()
    await slot.on_data({"CH1_Vpp": 0.82})
"""

from __future__ import annotations

import asyncio
import collections
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional, Union

from scope.model.enums import SlotStatus
from .base import FeedbackSlot, SlotConfig, DataSubscription, SlotInfo

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# 数据模型
# ═══════════════════════════════════════════════════════════════

@dataclass
class PidParams:
    """PID 控制器参数"""
    preset_value: float = 0.8          # 目标设定值
    kp: float = 0.03                   # 比例系数
    ki: float = 0.0                    # 积分系数 (0 = 纯 P 控制)
    kd: float = 0.0                    # 微分系数
    i_limit: float = 0.1               # 积分项限幅 (抗饱和)
    output_limit: float = 0.1          # 输出总限幅
    error_history_size: int = 10       # 误差缓存窗口

    # 死区
    deadband: float = 0.0              # |error| < deadband → 不输出 (0=禁用)


@dataclass
class Ad9910Target:
    """AD9910 DDS 设备定位"""
    ip: str                           # 服务器 IP
    port: int = 3251                  # rpyc 端口
    device_id: int = 0x0D11           # AD9910 设备 ID (hex, 如 0x0D11)
    profile: int = 0x00               # 寄存器 profile (0x00~0x07)

    def __str__(self):
        return f"AD9910({self.ip}:{self.port}, dev=0x{self.device_id:04X}, prof=0x{self.profile:02X})"


@dataclass
class RtmqTarget:
    """RTMQ 白盒子设备定位"""
    ip: str                           # 服务器 IP
    port: int = 18861                 # rpyc 端口
    card_index: int = 2               # RWG 板卡号 (1,2,3,4)
    sbg_channel: int = 0x60           # 边带通道号 (0x00, 0x20, 0x40, 0x60...)

    def __str__(self):
        return f"RTMQ({self.ip}:{self.port}, card={self.card_index}, sbg=0x{self.sbg_channel:02X})"


# 联合类型
TargetConfig = Union[Ad9910Target, RtmqTarget]


@dataclass
class PidSlotConfig(SlotConfig):
    """PID 反馈槽位完整配置"""
    pid: PidParams = field(default_factory=PidParams)
    measurement_key: str = ""          # 订阅的测量项, 如 "CH1_Vpp"
    target: Optional[TargetConfig] = None
    connect_timeout: float = 5.0       # rpyc 连接超时 (秒)


# ═══════════════════════════════════════════════════════════════
# PID 核心 (纯计算, 无 IO)
# ═══════════════════════════════════════════════════════════════

class PidController:
    """
    PID 控制器 — 无状态的纯计算单元。

    状态 (误差历史, 上次误差) 封装在实例内。
    """

    def __init__(self, params: PidParams):
        self._params = params
        self._errors: collections.deque = collections.deque(
            maxlen=params.error_history_size
        )
        self._last_error: float = 0.0
        # 为保持误差窗口大小正确, 预填零
        for _ in range(params.error_history_size):
            self._errors.append(0.0)

    def step(self, measured_value: float) -> Optional[float]:
        """
        单步 PID 计算。

        Returns:
            输出值 (delta), 或 None (死区内不输出)
        """
        error = self._params.preset_value - measured_value

        # 死区检查
        if self._params.deadband > 0 and abs(error) < self._params.deadband:
            return None

        self._errors.append(error)

        # P
        pout = error * self._params.kp

        # D
        dout = (error - self._last_error) * self._params.kd
        self._last_error = error

        # I (窗口累积 + 抗饱和限幅)
        iout = sum(self._errors) * self._params.ki
        iout = max(-self._params.i_limit, min(self._params.i_limit, iout))

        # 总输出 + 限幅
        out = pout + iout + dout
        out = max(-self._params.output_limit, min(self._params.output_limit, out))

        return out

    def reset(self):
        """重置 PID 状态"""
        self._errors.clear()
        for _ in range(self._params.error_history_size):
            self._errors.append(0.0)
        self._last_error = 0.0


# ═══════════════════════════════════════════════════════════════
# PidFeedbackSlot
# ═══════════════════════════════════════════════════════════════

class PidFeedbackSlot(FeedbackSlot):
    """
    PID 反馈插槽。

    生命周期: 与 FeedbackSlot 相同。
    每次 on_data() 执行: 提取测量值 → PID 计算 → RPC 发送。
    """

    protocol = "pid"

    def __init__(self, config: PidSlotConfig):
        super().__init__(config)
        self._pid_config: PidSlotConfig = config
        self._pid = PidController(config.pid)
        self._pool: Any = None  # RpycConnectionPool, 延迟创建
        self._latest_value: float = 0.0
        self._last_connection_check: float = 0.0

    # ── 覆盖 get_info 以正确读取 PidParams ──────────────────────

    def get_info(self) -> SlotInfo:
        """覆盖基类, 从 PidParams 读取 PID 字段。"""
        from .base import SlotInfo
        p = self._pid_config.pid
        return SlotInfo(
            slot_id=self._config.slot_id,
            label=self._config.label or self._config.slot_id,
            protocol=self.protocol,
            status=self._status.value,
            target=self._get_target(),
            subscriptions=[s.local_key for s in self._config.subscriptions],
            sent_count=self._sent_count,
            error_count=self._error_count,
            consecutive_errors=self._consecutive_errors,
            last_error=self._last_error,
            last_sent_at=self._last_sent_at,
            auto_paused=self._auto_paused,
            feedback_mode="PID",
            setpoint=p.preset_value,
            pid_kp=p.kp,
            pid_ki=p.ki,
            pid_kd=p.kd,
            feedback_threshold=p.deadband,
            feedback_limit=p.output_limit,
            latest_value=self._latest_value,
            measurement_status="unknown",
        )

    # ── 生命周期 ────────────────────────────────────────────────

    async def start(self):
        if self._status == SlotStatus.RUNNING:
            return
        t = self._pid_config.target
        if t is None:
            raise ValueError("target is required")

        from .rpyc_pool import RpycConnectionPool
        self._pool = RpycConnectionPool(
            host=t.ip,
            port=t.port,
            min_size=1,
            max_size=2,
            connect_timeout=self._pid_config.connect_timeout,
        )
        self._status = SlotStatus.RUNNING
        logger.info(f"[{self.slot_id}] PID 反馈已启动 → {self._get_target()}")

    async def stop(self):
        if self._pool is not None:
            self._pool.close()
            self._pool = None
        self._status = SlotStatus.IDLE
        logger.info(f"[{self.slot_id}] PID 反馈已停止")

    # ── 暂停 / 恢复 ────────────────────────────────────────────

    async def pause(self, auto: bool = False):
        self._pid.reset()
        await super().pause(auto)

    async def resume(self):
        self._pid.reset()
        await super().resume()

    # ── 数据推送 ───────────────────────────────────────────────

    async def on_data(self, payload: dict[str, Any]):
        t = self._pid_config.target
        if t is None:
            logger.warning(f"[{self.slot_id}] 无目标设备, 跳过")
            return

        # 提取测量值
        key = self._pid_config.measurement_key
        value = payload.get(key)
        if value is None:
            logger.warning(
                f"[{self.slot_id}] measurement_key='{key}' 不在 payload 中, "
                f"可用 keys: {list(payload.keys())}"
            )
            return

        self._latest_value = float(value)

        # PID 计算
        out = self._pid.step(self._latest_value)
        if out is None:
            logger.debug(f"[{self.slot_id}] 死区内, delta=0 (measured={self._latest_value:.4f})")
            return

        logger.info(
            f"[{self.slot_id}] PID output={out:.6f} "
            f"(measured={self._latest_value:.4f}, preset={self._pid_config.pid.preset_value:.4f})"
        )

        # RPC 发送
        if isinstance(t, Ad9910Target):
            await self._send_ad9910(t, out)
        elif isinstance(t, RtmqTarget):
            await self._send_rtmq(t, out)

    # ── 运行时重配 ─────────────────────────────────────────────

    async def reconfigure(self, config: SlotConfig):
        if not isinstance(config, PidSlotConfig):
            raise TypeError("PidFeedbackSlot 只能接受 PidSlotConfig")
        was_running = self._status == SlotStatus.RUNNING
        if was_running:
            await self.stop()
        self._pid_config = config
        self._pid = PidController(config.pid)
        if config.subscriptions:
            self._config.subscriptions = config.subscriptions
        if was_running:
            await self.start()

    # ── 公共方法 ────────────────────────────────────────────────

    def _get_target(self) -> str:
        t = self._pid_config.target
        return str(t) if t else "(无目标)"

    # ── RPC 发送 (内部) ────────────────────────────────────────

    async def _send_ad9910(self, t: Ad9910Target, delta_amp: float):
        """通过 rpyc 向 AD9910 推送幅度调整。"""
        try:
            conn = self._pool.acquire()
            try:
                service = conn.root.get_ad9910_service()
                service.adjust_amplitude(t.device_id, t.profile, delta_amp)
                self._count_sent()
                logger.debug(
                    f"[{self.slot_id}] adjust_amplitude("
                    f"dev=0x{t.device_id:04X}, prof=0x{t.profile:02X}, "
                    f"delta={delta_amp:.6f})"
                )
            finally:
                self._pool.release(conn)
        except Exception as e:
            self._count_error(str(e))

    async def _send_rtmq(self, t: RtmqTarget, delta_amp: float):
        """通过 rpyc 向 RTMQ 白盒子推送幅度调整。"""
        try:
            conn = self._pool.acquire()
            try:
                # 获取当前幅度, 计算新幅度
                rwg = conn.root.get_rwg_info()
                current_info = rwg[t.card_index]['sbg_freq'][t.sbg_channel]
                current_amp = float(current_info[1])
                new_amp = current_amp + delta_amp
                conn.root.change_rwg_info(
                    card=t.card_index,
                    sbg_ch=t.sbg_channel,
                    amp=new_amp,
                )
                self._count_sent()
                logger.debug(
                    f"[{self.slot_id}] RTMQ amp: {current_amp:.6f} → {new_amp:.6f} "
                    f"(delta={delta_amp:.6f})"
                )
            finally:
                self._pool.release(conn)
        except Exception as e:
            self._count_error(str(e))
