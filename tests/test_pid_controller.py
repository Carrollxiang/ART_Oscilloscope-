"""
PidController 单元测试
"""

import pytest

from scope.runtime.pid_controller import PidConfig, PidController


# ── 基础测试 ───────────────────────────────────────────────────

def test_pid_config_defaults():
    """PidConfig 默认值正确"""
    cfg = PidConfig(preset_value=3.3)
    assert cfg.preset_value == 3.3
    assert cfg.kp == 0.03
    assert cfg.ki == 0.0
    assert cfg.kd == 0.0
    assert cfg.i_limit == 0.1
    assert cfg.output_limit == 0.1
    assert cfg.window_size == 10
    assert cfg.deadband == 0.0


def test_pid_step_basic():
    """P 控制模式：误差按比例输出"""
    cfg = PidConfig(preset_value=3.3, kp=0.1, ki=0.0, kd=0.0)
    pid = PidController(cfg)

    # 测量值 = 3.0 → error = 0.3 → output = 0.1 * 0.3 = 0.03
    out = pid.step(3.0)
    assert out is not None
    assert out == pytest.approx(0.03)


def test_pid_step_negative():
    """负误差情况"""
    cfg = PidConfig(preset_value=3.3, kp=0.1, ki=0.0, kd=0.0)
    pid = PidController(cfg)

    # 测量值 = 3.6 → error = -0.3 → output = -0.03
    out = pid.step(3.6)
    assert out is not None
    assert out == pytest.approx(-0.03)


def test_pid_step_ki():
    """PI 控制模式：积分项累计"""
    cfg = PidConfig(preset_value=3.3, kp=0.0, ki=0.1, kd=0.0)
    pid = PidController(cfg)

    # error1 = 0.3, error2 = 0.2 → sum = 0.5 → output = 0.1 * 0.5 = 0.05
    out1 = pid.step(3.0)
    assert out1 is not None
    assert out1 == pytest.approx(0.03)  # I_sum = 0.3 → 0.1 * 0.3 = 0.03

    out2 = pid.step(3.1)
    assert out2 is not None
    assert out2 == pytest.approx(0.05)  # I_sum = 0.3 + 0.2 = 0.5 → 0.1 * 0.5 = 0.05


def test_pid_step_kd():
    """PD 控制模式：微分项"""
    cfg = PidConfig(preset_value=3.3, kp=0.0, ki=0.0, kd=0.1)
    pid = PidController(cfg)

    # 第一次: error=0.0 → D = 0.1 * 0.0 = 0  (last_error初始为0)
    # 但我们无法测试D的纯净情况，因为_last_error从0开始
    # 先走一步设定last_error
    pid.step(3.0)  # error = 0.3, last_error = 0.3

    # 第二次: error=0.2 → D = 0.1 * (0.2 - 0.3) = -0.01
    out = pid.step(3.1)  # error = 0.2
    assert out is not None
    assert out == pytest.approx(-0.01)


# ── 窗口和限幅 ─────────────────────────────────────────────────

def test_pid_window_size():
    """窗口满后自动丢弃旧数据"""
    cfg = PidConfig(preset_value=3.3, kp=0.0, ki=0.1, kd=0.0, window_size=3)
    pid = PidController(cfg)

    # 填充 window + 1 次
    pid.step(3.0)  # e=0.3, sum=0.3
    pid.step(3.1)  # e=0.2, sum=0.5
    pid.step(3.2)  # e=0.1, sum=0.6

    # 第4次：丢弃最早的 0.3，保留 0.2+0.1+新值
    out = pid.step(2.9)  # e=0.4, sum=0.2+0.1+0.4=0.7
    assert out == pytest.approx(0.07)


def test_pid_i_limit():
    """积分限幅生效"""
    limit = 0.05
    cfg = PidConfig(preset_value=3.3, kp=0.0, ki=1.0, kd=0.0, i_limit=limit)
    pid = PidController(cfg)

    # 多次累计，确保超过限幅
    out = None
    for _ in range(20):
        out = pid.step(0.0)  # 每次 error = 3.3

    # I 项应被限幅在 ±limit
    assert out is not None
    assert abs(out) <= limit + 1e-9


def test_pid_output_limit():
    """输出限幅生效"""
    limit = 0.02
    cfg = PidConfig(preset_value=3.3, kp=1.0, ki=0.0, kd=0.0, output_limit=limit)
    pid = PidController(cfg)

    # error=3.3 → P=3.3 → 被限幅到 ±0.02
    out = pid.step(0.0)
    assert out is not None
    assert abs(out) <= limit + 1e-9


# ── 死区 ───────────────────────────────────────────────────────

def test_pid_deadband():
    """死区内返回 None"""
    cfg = PidConfig(preset_value=3.3, kp=0.1, ki=0.0, kd=0.0, deadband=0.05)
    pid = PidController(cfg)

    # 误差 0.03 < 0.05 → 返回 None
    out1 = pid.step(3.27)
    assert out1 is None

    # 误差 0.06 >= 0.05 → 正常返回
    out2 = pid.step(3.24)
    assert out2 is not None


# ── 重置 ───────────────────────────────────────────────────────

def test_pid_reset():
    """重置后状态清空"""
    cfg = PidConfig(preset_value=3.3, kp=0.1, ki=0.1, kd=0.0, window_size=5)
    pid = PidController(cfg)

    pid.step(3.0)
    pid.step(3.1)
    assert pid.metrics["errors_count"] == 2

    pid.reset()

    assert pid.metrics["errors_count"] == 0
    assert pid.metrics["last_error"] == 0.0


# ── Metrics ────────────────────────────────────────────────────

def test_pid_metrics():
    """metrics 属性返回正确"""
    cfg = PidConfig(preset_value=5.0, kp=0.1)
    pid = PidController(cfg)

    assert pid.metrics["errors_count"] == 0
    assert pid.metrics["preset_value"] == 5.0

    pid.step(4.0)
    assert pid.metrics["errors_count"] == 1
    assert pid.metrics["last_error"] != 0.0
