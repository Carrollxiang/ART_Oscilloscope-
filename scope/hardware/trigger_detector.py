"""
TriggerDetector — 软件触发检测器 (CONTINUOUS 模式帧化)

在连续样本流中检测触发通道的上升/下降沿, 以沿为帧起点截取
`frame_length` 个样本构成一帧。帧内锁定 (不再重复触发),
帧满后重新武装等待下一个沿。沿精度 = 一个采样间隔。

触发语义与硬件触发一致:
  - rising:  前一采样 < level 且 当前采样 >= level
  - falling: 前一采样 > level 且 当前采样 <= level

状态机:
  WAIT_TRIG   等待触发沿 (前采样已满足 disarm 条件)
  WAIT_DISARM 等待回落/回升 (前采样仍处于触发侧, 需先离开才允许下一次触发)
  COLLECTING  帧内收集 (锁定, 不检测沿)

用法 (采集线程):
    detector = TriggerDetector(trigger_channel=12, level=1.0,
                               frame_length=15000)
    while running:
        chunk = task.read(block_size, timeout=...)
        for frame in detector.feed(chunk):
            callback(frame)   # frame: (channels, frame_length) float32
"""

from __future__ import annotations

import logging
from typing import List

import numpy as np

logger = logging.getLogger(__name__)

# 状态常量
WAIT_TRIG = 0       # 等待触发沿
WAIT_DISARM = 1     # 等待回落/回升 (重新武装)
COLLECTING = 2      # 帧内收集 (锁定)


class TriggerDetector:
    """软件触发检测与帧组装 (纯逻辑, 无 I/O, 可独立单测)。"""

    def __init__(
        self,
        trigger_channel: int,
        level: float,
        frame_length: int,
        slope: str = "rising",
    ):
        if slope not in ("rising", "falling"):
            raise ValueError(f"slope 必须为 'rising' 或 'falling', 收到 {slope!r}")
        if frame_length <= 0:
            raise ValueError(f"frame_length 必须为正数, 收到 {frame_length}")
        self._ch = trigger_channel
        self._level = level
        self._frame_length = frame_length
        self._slope = slope

        # 初始: 等待第一个触发沿 (前采样视为已满足 disarm 条件)
        self._state = WAIT_TRIG
        self._buf: List[np.ndarray] = []
        self._collected = 0
        self._frames = 0            # 已产出帧数 (测试/诊断用)

    @property
    def frames(self) -> int:
        return self._frames

    @property
    def collecting(self) -> bool:
        """当前是否在帧内收集 (锁定) 状态。"""
        return self._state == COLLECTING

    def reset(self):
        """重置状态 (重新等待触发)。"""
        self._state = WAIT_TRIG
        self._buf = []
        self._collected = 0

    def feed(self, chunk: np.ndarray) -> List[np.ndarray]:
        """
        喂入一块连续样本 (channels, samples), 返回本块内完整的帧列表。

        帧 = 从触发沿起连续 frame_length 个样本 (跨块自动拼接)。
        """
        frames: List[np.ndarray] = []
        if chunk.ndim == 1:
            chunk = chunk.reshape(1, -1)
        n = chunk.shape[1]
        if n == 0:
            return frames
        if self._ch >= chunk.shape[0]:
            logger.error(
                "触发通道 %d 超出数据通道数 %d, 跳过本块",
                self._ch, chunk.shape[0],
            )
            return frames

        pos = 0
        while pos < n:
            row = chunk[self._ch, pos:]
            if self._state == COLLECTING:
                # ── 帧内收集 (锁定) ──
                need = self._frame_length - self._collected
                take = min(need, n - pos)
                self._buf.append(chunk[:, pos:pos + take])
                self._collected += take
                pos += take
                if self._collected >= self._frame_length:
                    frame = np.concatenate(self._buf, axis=1)
                    self._buf = []
                    self._collected = 0
                    self._state = WAIT_DISARM  # 帧满 → 先等离开触发侧
                    self._frames += 1
                    frames.append(frame)
                continue

            if self._slope == "rising":
                hit = row >= self._level
                disarm = row < self._level
            else:  # falling
                hit = row <= self._level
                disarm = row > self._level

            if self._state == WAIT_TRIG:
                # ── 等待触发沿 ──
                hit_idx = np.flatnonzero(hit)
                if hit_idx.size == 0:
                    # 本块无沿; 按最后采样更新状态 (避免漏掉跨块沿)
                    if hit[-1]:
                        self._state = WAIT_DISARM
                    break
                pos += int(hit_idx[0])
                self._state = COLLECTING
                self._buf = []
                self._collected = 0
                # 沿点包含在帧内 (下一轮从沿点开始收集)
            else:  # WAIT_DISARM
                # ── 等待回落/回升 (重新武装) ──
                disarm_idx = np.flatnonzero(disarm)
                if disarm_idx.size == 0:
                    if disarm[-1]:
                        self._state = WAIT_TRIG
                    break
                pos += int(disarm_idx[0])
                self._state = WAIT_TRIG

        return frames
