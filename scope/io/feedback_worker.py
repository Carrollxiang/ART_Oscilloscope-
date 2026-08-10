"""
FeedbackWorker — 独立反馈单元

被动接收测量值，内部持有 PidController，调用 PID 计算后发送调整指令。
"""

from __future__ import annotations

import dataclasses
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional

from scope.model.enums import SlotStatus
from scope.runtime.pid_controller import PidConfig, PidController


logger = logging.getLogger(__name__)

# 发送失败后的冷却时间: 冷却期内跳过发送, 避免每帧重复尝试
# 占用 executor 线程 (建连失败最坏 ~18s)。
RETRY_COOLDOWN_S: float = 5.0


# ── 目标设备配置 ──────────────────────────────────────────

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


def target_to_dict(target: Optional[TargetConfig]) -> Optional[dict[str, Any]]:
    """将 TargetConfig 序列化为字典（含 type 字段用于反序列化）。"""
    if target is None:
        return None
    d = dataclasses.asdict(target)
    d["type"] = type(target).__name__  # "Ad9910Target" | "RtmqTarget"
    return d


def target_from_dict(d: Optional[dict[str, Any]]) -> Optional[TargetConfig]:
    """从字典反序列化 TargetConfig（不修改输入 dict）。"""
    if d is None:
        return None
    t = d.get("type", None)
    if t == "Ad9910Target":
        return Ad9910Target(
            ip=d["ip"], port=d["port"],
            device_id=d.get("device_id", 0), profile=d.get("profile", 0),
        )
    elif t == "RtmqTarget":
        return RtmqTarget(
            ip=d["ip"], port=d["port"],
            card_index=d.get("card_index", 0), sbg_channel=d.get("sbg_channel", 0),
        )
    else:
        logger.warning("unknown target type: %s", t)
        return None


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
        self._sender: Optional[Ad9910Sender] = None
        self._sender_error: str = ""
        self._last_value: Optional[float] = None
        self._last_error: Optional[float] = None
        self._frames_processed: int = 0
        self._frames_skipped: int = 0       # 单帧误差保护跳过的帧数
        self._first_send_logged: bool = False
        self._processing: bool = False          # 上一帧发送未完成时跳过本帧
        self._next_retry_ts: float = 0.0        # 发送失败冷却截止 (monotonic)
        self._stop_reason: str = ""             # 自动暂停原因 (误差趋势变差等)
        self._trend_errors: deque = deque(maxlen=self._config.pid_config.trend_window or 1)

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

    @property
    def last_value(self) -> Optional[float]:
        return self._last_value

    @property
    def last_error(self) -> Optional[float]:
        """当前误差 = preset_value - last_value（最近一次 process 计算后的值）"""
        return self._last_error

    @property
    def frames_processed(self) -> int:
        return self._frames_processed

    @property
    def frames_skipped(self) -> int:
        return self._frames_skipped

    @property
    def stop_reason(self) -> str:
        """自动暂停原因 (空串 = 未被自动暂停)"""
        return self._stop_reason

    @property
    def target(self) -> Optional[TargetConfig]:
        return self._target

    def update_pid_config(self, pid_config: PidConfig):
        """运行时更新 PID 参数，重置控制器状态"""
        self._config.pid_config = pid_config
        self._pid = PidController(pid_config)
        logger.info(f'Worker "{self.worker_id}" PID 参数已更新')

    # ── 生命周期 ───────────────────────────────────────────────

    async def start(self):
        """启动 worker"""
        self._status = SlotStatus.RUNNING
        self._pid.reset()
        self._sender_error = ""
        self._first_send_logged = False
        self._stop_reason = ""
        self._frames_skipped = 0
        self._trend_errors.clear()
        self._sender = self._create_sender()
        chain = type(self._target).__name__ if self._target else "none"
        status = "(sender OK)" if self._sender or self._target is None else "(sender FAILED)"
        logger.info(
            'FeedbackWorker "%s" started (target=%s) %s',
            self.worker_id, chain, status,
        )

    async def stop(self):
        """停止 worker，释放目标设备连接。"""
        self._status = SlotStatus.IDLE
        if self._sender:
            await self._sender.close()
            self._sender = None
        logger.info(f'FeedbackWorker "{self.worker_id}" stopped')

    async def pause(self):
        """暂停 worker（保留 PID 状态）"""
        if self._status == SlotStatus.RUNNING:
            self._status = SlotStatus.PAUSED
            logger.info(f'FeedbackWorker "{self.worker_id}" paused')

    async def resume(self):
        """恢复 worker (清除自动暂停原因与趋势窗口)"""
        if self._status == SlotStatus.PAUSED:
            self._status = SlotStatus.RUNNING
            self._stop_reason = ""
            self._trend_errors.clear()
            logger.info(f'FeedbackWorker "{self.worker_id}" resumed')

    # ── 核心处理 ───────────────────────────────────────────────

    async def process(self, value: float):
        """
        处理单个测量值。

        由 FeedbackManager 调用，传入已提取的测量值。
        先无条件刷新订阅值 (last_value/last_error); 非 RUNNING 状态
        (PAUSED/IDLE) 仅刷新值, 不执行 PID 计算与发送。
        """
        self._last_value = value
        self._last_error = self._config.pid_config.preset_value - value

        if self._status != SlotStatus.RUNNING:
            return
        if self._processing:
            # 上一帧仍在发送中 (网络慢/不可达), 跳过本帧避免并发堆积
            return
        if time.monotonic() < self._next_retry_ts:
            # 发送失败冷却期, 跳过发送 (last_value 仍更新)
            return

        self._processing = True
        try:
            pid_cfg = self._config.pid_config

            # ── 功能1: 单帧大幅误差保护 ──
            # 示波器偶发卡顿会产生单帧错误测量值; |error|/|preset| 超过
            # 阈值时跳过该帧不发送, 避免参数大幅跳跃 (preset=0 时跳过保护)。
            if pid_cfg.max_error_ratio > 0 and abs(pid_cfg.preset_value) > 1e-9:
                ratio = abs(self._last_error) / abs(pid_cfg.preset_value)
                if ratio > pid_cfg.max_error_ratio:
                    self._frames_skipped += 1
                    logger.warning(
                        'Worker "%s" 单帧误差保护: |误差|/|目标| = %.0f%% > %.0f%%, '
                        '跳过该帧 (累计跳过 %d)',
                        self.worker_id, ratio * 100, pid_cfg.max_error_ratio * 100,
                        self._frames_skipped,
                    )
                    return

            delta = self._pid.step(value)
            self._frames_processed += 1
            if delta is not None and self._target:
                ok = await self._send_to_target(delta)
                if ok:
                    # ── 功能2: 误差趋势检测 ──
                    # 每 trend_window 次成功反馈检查: |末误差| > |首误差|
                    # (不降反增) → 暂停 worker, 等待手动恢复。
                    self._check_error_trend()
                else:
                    self._next_retry_ts = time.monotonic() + RETRY_COOLDOWN_S
            elif delta is not None:
                logger.debug(
                    f'Worker "{self.worker_id}" computed delta={delta:.6f} '
                    f"(no target configured)"
                )
        except Exception as e:
            self._next_retry_ts = time.monotonic() + RETRY_COOLDOWN_S
            logger.error(f'FeedbackWorker "{self.worker_id}" error: {e}', exc_info=True)
        finally:
            self._processing = False

    async def _send_to_target(self, delta: float) -> bool:
        """
        发送调整指令到目标设备。

        Args:
            delta: PID 计算出的调整量

        Returns:
            True 发送成功; False 无 sender/发送失败 (用于失败冷却)
        """
        if self._sender is None or not self._target:
            logger.debug(f'Worker "{self.worker_id}" delta={delta:.6f} (no sender)')
            return False

        try:
            if isinstance(self._target, Ad9910Target):
                ok = await self._sender.adjust_delta(delta)
                if ok:
                    if not self._first_send_logged:
                        self._first_send_logged = True
                        logger.info(
                            'Worker "%s" 首次反馈成功 → AD9910 %s:%d delta=%+.6f',
                            self.worker_id, self._target.ip, self._target.port, delta,
                        )
                    else:
                        logger.debug(
                            'Worker "%s" delta=%+.6f → AD9910 %s:%d',
                            self.worker_id, delta,
                            self._target.ip, self._target.port,
                        )
                else:
                    logger.warning(
                        'Worker "%s" AD9910 发送失败 delta=%+.6f',
                        self.worker_id, delta,
                    )
                return ok
            elif isinstance(self._target, RtmqTarget):
                ok = await self._sender.adjust_delta(delta)
                if ok:
                    if not self._first_send_logged:
                        self._first_send_logged = True
                        logger.info(
                            'Worker "%s" 首次反馈成功 → RTMQ %s:%d card=%d ch=%d delta=%+.6f',
                            self.worker_id, self._target.ip, self._target.port,
                            self._target.card_index, self._target.sbg_channel, delta,
                        )
                    else:
                        logger.debug(
                            'Worker "%s" delta=%+.6f → RTMQ %s:%d card=%d ch=%d',
                            self.worker_id, delta,
                            self._target.ip, self._target.port,
                            self._target.card_index, self._target.sbg_channel,
                        )
                else:
                    logger.warning(
                        'Worker "%s" RTMQ 发送失败 delta=%+.6f',
                        self.worker_id, delta,
                    )
                return ok
        except Exception as e:
            logger.error(
                'Worker "%s" 发送异常: %s', self.worker_id, e, exc_info=True,
            )
            return False
        return False

    def _check_error_trend(self):
        """
        误差趋势检测 (功能2): 每 trend_window 次成功反馈检查一次,
        若 |末误差| > |首误差| (误差不降反增) → 暂停 worker 并记录原因。

        暂停后由用户手动恢复 (resume 会清空趋势窗口与原因)。
        """
        pid_cfg = self._config.pid_config
        if pid_cfg.trend_window <= 0:
            return
        self._trend_errors.append(self._last_error)
        if len(self._trend_errors) < pid_cfg.trend_window:
            return
        first, last = self._trend_errors[0], self._trend_errors[-1]
        self._trend_errors.clear()
        if abs(last) > abs(first):
            self._stop_reason = (
                f"误差趋势变差: |误差| {abs(first):.4f} → {abs(last):.4f} "
                f"({pid_cfg.trend_window} 次反馈后不降反增)"
            )
            self._status = SlotStatus.PAUSED
            logger.warning(
                'FeedbackWorker "%s" 已自动暂停: %s',
                self.worker_id, self._stop_reason,
            )

    def _create_sender(self) -> Optional[Ad9910Sender]:
        """
        根据目标配置创建发送器实例。

        使用 lazy import 避免循环依赖。
        失败时记录错误并设置 self._sender_error，返回 None。
        """
        if self._target is None:
            return None

        try:
            if isinstance(self._target, Ad9910Target):
                from scope.io.ad9910_sender import Ad9910Sender  # noqa: F811
                return Ad9910Sender(
                    ip=self._target.ip,
                    port=self._target.port,
                    device_id=self._target.device_id,
                    profile=self._target.profile,
                )

            if isinstance(self._target, RtmqTarget):
                from scope.io.rtmq_sender import RtmqSender
                return RtmqSender(
                    ip=self._target.ip,
                    port=self._target.port,
                    card_index=self._target.card_index,
                    sbg_channel=self._target.sbg_channel,
                )
        except Exception as e:
            self._sender_error = str(e)
            logger.error(
                'Worker "%s" 创建 sender 失败 (%s): %s',
                self.worker_id,
                type(self._target).__name__,
                e,
            )

        return None
