"""
TriggerDetector 单元测试 — 软件触发检测与帧组装
"""

import numpy as np
import pytest

from scope.hardware.trigger_detector import TriggerDetector


def make_stream(n_ch, trig, base=0.0):
    """构造 (n_ch, N) 数据, 通道 12 为给定波形。"""
    data = np.full((n_ch, len(trig)), base, dtype=np.float32)
    data[12] = trig
    return data


class TestTriggerDetector:
    def test_rising_edge_frame_start(self):
        """上升沿触发: 帧从沿位置开始, 长度正确"""
        n = 5000
        trig = np.zeros(n)
        trig[1000:] = 1.0                      # 上升沿在 1000
        det = TriggerDetector(12, 0.5, 2000)
        frames = det.feed(make_stream(16, trig))
        assert len(frames) == 1
        f = frames[0]
        assert f.shape == (16, 2000)
        # 帧起点 = 沿点: 帧内第 0 样本 = trig[1000] = 1.0 (前一样本 0)
        assert f[12, 0] == 1.0

    def test_no_trigger_no_frame(self):
        """信号一直低于电平 → 不产帧"""
        trig = np.zeros(5000)
        det = TriggerDetector(12, 0.5, 2000)
        assert det.feed(make_stream(16, trig)) == []

    def test_frame_locking(self):
        """帧内锁定: 帧长 2000 但沿每 500 样本一次 → 只产 1 帧"""
        n = 3000
        trig = np.zeros(n)
        trig[0::500] = 1.0
        det = TriggerDetector(12, 0.5, 2000)
        frames = det.feed(make_stream(16, trig))
        assert len(frames) == 1
        assert frames[0].shape == (16, 2000)

    def test_cross_block(self):
        """沿在块尾, 帧跨块自动拼接"""
        det = TriggerDetector(12, 0.5, 2000)
        n1 = 2500
        trig1 = np.zeros(n1)
        trig1[2499] = 1.0                      # 沿在块 1 末尾
        assert det.feed(make_stream(16, trig1)) == []   # 帧未满 (仅 1 样本)
        n2 = 2000
        trig2 = np.ones(n2)                    # 继续高电平
        frames = det.feed(make_stream(16, trig2))
        assert len(frames) == 1
        assert frames[0].shape == (16, 2000)
        assert frames[0][12, 0] == 1.0         # 帧起点 = 沿点

    def test_falling_slope(self):
        """下降沿触发"""
        n = 5000
        trig = np.ones(n)
        trig[1000:] = 0.0                      # 下降沿在 1000
        det = TriggerDetector(12, 0.5, 2000, slope="falling")
        frames = det.feed(make_stream(16, trig))
        assert len(frames) == 1
        assert frames[0].shape == (16, 2000)
        assert frames[0][12, 0] == 0.0         # 帧从下降沿开始

    def test_multiple_frames_one_block(self):
        """一个块内多个完整帧"""
        n = 4500
        trig = np.zeros(n)
        trig[0] = 1.0
        trig[2000] = 1.0                       # 两个沿, 间隔 2000 > 帧长 1000
        det = TriggerDetector(12, 0.5, 1000)
        frames = det.feed(make_stream(16, trig))
        assert len(frames) == 2
        assert all(f.shape == (16, 1000) for f in frames)

    def test_reset(self):
        """reset 后重新等待触发"""
        det = TriggerDetector(12, 0.5, 2000)
        trig = np.zeros(100)
        trig[50] = 1.0
        det.feed(make_stream(16, trig))
        assert det.collecting            # 已触发, 收集中
        det.reset()
        assert not det.collecting
        assert det.frames == 0           # reset 不保留已产帧计数? 计数保留由调用方看
        assert det.frames >= 0

    def test_invalid_args(self):
        with pytest.raises(ValueError):
            TriggerDetector(12, 0.5, -1)
        with pytest.raises(ValueError):
            TriggerDetector(12, 0.5, 100, slope="sideways")
