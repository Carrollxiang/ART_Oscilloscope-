#!/usr/bin/env python3
"""
rpyc 连接测试脚本 — AD9910 / RTMQ 设备连通性诊断。

独立运行，不依赖 Qt / EventBus / 项目其他模块。

用法:
    # 基础连通性测试
    python scripts/rpyc_test.py --ip 192.168.1.100 --port 3251

    # 完整测试（含 set_frequency / set_amplitude 调用）
    python scripts/rpyc_test.py --ip 192.168.1.100 --port 3251 --full

    # 指定设备参数
    python scripts/rpyc_test.py --ip 192.168.1.100 --port 3251 --device-id 0x0D11 --profile 0 --full

    # 测试多个地址
    python scripts/rpyc_test.py --ip 192.168.1.100 --port 3251 --ip 192.168.1.101 --port 18861
"""

from __future__ import annotations

import argparse
import socket
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

# ── 彩色输出 ────────────────────────────────────────────────────

_RESET = "\033[0m"
_RED = "\033[91m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_BLUE = "\033[94m"
_CYAN = "\033[96m"
_BOLD = "\033[1m"


def _color(code: str, text: str) -> str:
    # Windows cmd 可能不支持 ANSI，简单回退
    if sys.platform == "win32":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception:
            return text  # 不支持则跳过着色
    return f"{code}{text}{_RESET}"


def ok(text: str) -> str:
    return _color(_GREEN, f"✓ {text}")


def fail(text: str) -> str:
    return _color(_RED, f"✗ {text}")


def warn(text: str) -> str:
    return _color(_YELLOW, f"⚠ {text}")


def info(text: str) -> str:
    return _color(_CYAN, text)


def header(text: str) -> str:
    return _color(_BOLD + _BLUE, text)


# ── 测试结果 ────────────────────────────────────────────────────

@dataclass
class StepResult:
    name: str
    passed: bool
    duration_ms: float
    detail: str = ""
    error: str = ""


@dataclass
class TargetResult:
    ip: str
    port: int
    steps: list[StepResult] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return all(s.passed for s in self.steps)

    def summary_line(self) -> str:
        total = len(self.steps)
        passed = sum(1 for s in self.steps if s.passed)
        if passed == total:
            return ok(f"{self.ip}:{self.port} — {passed}/{total} 通过")
        else:
            return fail(f"{self.ip}:{self.port} — {passed}/{total} 通过")


# ── 测试步骤 ────────────────────────────────────────────────────

def _run_step(name: str, fn, *args) -> StepResult:
    """执行单个测试步骤，记录耗时和结果。"""
    t0 = time.monotonic()
    try:
        detail = fn(*args)
        dt = (time.monotonic() - t0) * 1000
        return StepResult(name=name, passed=True, duration_ms=dt, detail=str(detail) if detail else "")
    except Exception as e:
        dt = (time.monotonic() - t0) * 1000
        return StepResult(name=name, passed=False, duration_ms=dt, error=str(e))


def test_tcp_connect(ip: str, port: int, timeout: float = 3.0) -> str:
    """测试 TCP 原始连通性。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((ip, port))
        sock.close()
        return f"TCP {ip}:{port} 连接成功"
    except socket.timeout:
        raise TimeoutError(f"TCP 连接超时 ({timeout}s)")
    except ConnectionRefusedError:
        raise ConnectionRefusedError(f"TCP 连接被拒绝 — 服务器未运行？")
    except OSError as e:
        raise OSError(f"TCP 连接失败: {e}")


def test_rpyc_handshake(ip: str, port: int, timeout: float = 5.0) -> str:
    """测试 rpyc 握手（ping）。"""
    import rpyc
    conn = rpyc.connect(
        ip, port,
        config={"sync_request_timeout": timeout},
    )
    try:
        conn.ping()
        return "rpyc 握手成功"
    finally:
        conn.close()


def test_get_service(ip: str, port: int, timeout: float = 5.0) -> str:
    """测试获取 AD9910 service 对象。"""
    import rpyc
    conn = rpyc.connect(
        ip, port,
        config={"sync_request_timeout": timeout},
    )
    try:
        service = conn.root.get_ad9910_service()
        methods = [m for m in dir(service) if not m.startswith("_")]
        return f"获取 service 成功，可用方法: {methods}"
    finally:
        conn.close()


def test_set_frequency(
    ip: str, port: int,
    device_id: int, profile: int,
    freq_hz: float = 100e6,
    timeout: float = 5.0,
) -> str:
    """测试设置频率。"""
    import rpyc
    conn = rpyc.connect(
        ip, port,
        config={"sync_request_timeout": timeout},
    )
    try:
        service = conn.root.get_ad9910_service()
        result = service.set_frequency(device_id, profile, freq_hz)
        return f"set_frequency({device_id:#x}, {profile}, {freq_hz:.0f} Hz) → {result}"
    finally:
        conn.close()


def test_set_amplitude(
    ip: str, port: int,
    device_id: int, profile: int,
    amplitude: float = 0.5,
    timeout: float = 5.0,
) -> str:
    """测试设置幅度。"""
    import rpyc
    conn = rpyc.connect(
        ip, port,
        config={"sync_request_timeout": timeout},
    )
    try:
        service = conn.root.get_ad9910_service()
        result = service.set_amplitude(device_id, profile, amplitude)
        return f"set_amplitude({device_id:#x}, {profile}, {amplitude:.3f}) → {result}"
    finally:
        conn.close()


# ── 主流程 ──────────────────────────────────────────────────────

def test_target(ip: str, port: int, full: bool = False,
                device_id: int = 0, profile: int = 0,
                connect_timeout: float = 5.0) -> TargetResult:
    """对单个目标执行一系列测试。"""
    result = TargetResult(ip=ip, port=port)

    print(f"\n{'─' * 60}")
    print(header(f"测试目标: {ip}:{port}"))

    # 1. TCP 连通性
    step = _run_step("TCP 连接", test_tcp_connect, ip, port, connect_timeout)
    result.steps.append(step)
    print(f"  {'✓' if step.passed else '✗'} {step.name}: {step.detail or step.error}  ({step.duration_ms:.1f}ms)")
    if not step.passed:
        return result  # TCP 不通，后续没必要测试

    # 2. rpyc 握手
    step = _run_step("rpyc 握手", test_rpyc_handshake, ip, port, connect_timeout)
    result.steps.append(step)
    print(f"  {'✓' if step.passed else '✗'} {step.name}: {step.detail or step.error}  ({step.duration_ms:.1f}ms)")
    if not step.passed:
        return result

    # 3. 获取 service
    step = _run_step("获取 service", test_get_service, ip, port, connect_timeout)
    result.steps.append(step)
    print(f"  {'✓' if step.passed else '✗'} {step.name}: {step.detail or step.error}  ({step.duration_ms:.1f}ms)")

    # 4-5. 完整调用测试
    if full:
        step = _run_step(
            "set_frequency",
            test_set_frequency, ip, port, device_id, profile, 100e6, connect_timeout,
        )
        result.steps.append(step)
        print(f"  {'✓' if step.passed else '✗'} {step.name}: {step.detail or step.error}  ({step.duration_ms:.1f}ms)")

        step = _run_step(
            "set_amplitude",
            test_set_amplitude, ip, port, device_id, profile, 0.5, connect_timeout,
        )
        result.steps.append(step)
        print(f"  {'✓' if step.passed else '✗'} {step.name}: {step.detail or step.error}  ({step.duration_ms:.1f}ms)")

    return result


def main():
    parser = argparse.ArgumentParser(
        description="rpyc 连接测试 — AD9910/RTMQ 设备连通性诊断",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python scripts/rpyc_test.py --ip 192.168.1.100 --port 3251
  python scripts/rpyc_test.py --ip 192.168.1.100 --port 3251 --full
  python scripts/rpyc_test.py --ip 192.168.1.100 --port 3251 --device-id 0x0D11 --profile 0 --full
        """,
    )
    parser.add_argument("--ip", action="append", dest="ips", default=[],
                        help="目标 IP（可重复指定多个）")
    parser.add_argument("--port", type=int, action="append", dest="ports", default=[],
                        help="目标端口（与 --ip 一一对应，可重复）")
    parser.add_argument("--full", action="store_true",
                        help="执行完整测试（含 set_frequency / set_amplitude 调用）")
    parser.add_argument("--device-id", type=lambda x: int(x, 0), default=0,
                        help="AD9910 设备 ID（默认 0，支持十六进制如 0x0D11）")
    parser.add_argument("--profile", type=int, default=0,
                        help="AD9910 profile 编号（默认 0）")
    parser.add_argument("--timeout", type=float, default=5.0,
                        help="单步超时秒数（默认 5.0）")

    args = parser.parse_args()

    # 默认目标
    if not args.ips:
        args.ips = ["192.168.1.100"]
    if not args.ports:
        args.ports = [3251] * len(args.ips)

    if len(args.ips) != len(args.ports):
        print(fail("错误: --ip 和 --port 数量不一致"))
        sys.exit(1)

    print(header("=" * 60))
    print(header("  rpyc 连接测试"))
    print(header("=" * 60))
    print(f"  模式: {'完整测试' if args.full else '连通性测试'}")
    if args.full:
        print(f"  设备: device_id={args.device_id:#x}, profile={args.profile}")
    print(f"  超时: {args.timeout}s")

    # 检查 rpyc 是否安装
    try:
        import rpyc  # noqa: F401
    except ImportError:
        print(f"\n{fail('rpyc 未安装。请执行: pip install rpyc')}")
        sys.exit(1)

    results: list[TargetResult] = []
    for ip, port in zip(args.ips, args.ports):
        r = test_target(
            ip, port,
            full=args.full,
            device_id=args.device_id,
            profile=args.profile,
            connect_timeout=args.timeout,
        )
        results.append(r)

    # 汇总
    print(f"\n{'─' * 60}")
    print(header("测试汇总"))
    total_targets = len(results)
    passed_targets = sum(1 for r in results if r.all_passed)
    total_steps = sum(len(r.steps) for r in results)
    passed_steps = sum(sum(1 for s in r.steps if s.passed) for r in results)

    for r in results:
        print(f"  {r.summary_line()}")

    print(f"\n  目标: {passed_targets}/{total_targets} 通过")
    print(f"  步骤: {passed_steps}/{total_steps} 通过")

    if passed_targets == total_targets:
        print(f"\n{ok('全部通过！')}")
        sys.exit(0)
    else:
        print(f"\n{fail('存在失败项，请检查上述错误信息。')}")
        print(info("提示: 确认 AD9910 RPyC Server 正在目标机器上运行。"))
        print(info("      启动方式: python feedback_example/ad9910_rpyc_server.py"))
        sys.exit(1)


if __name__ == "__main__":
    main()
