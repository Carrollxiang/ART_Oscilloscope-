"""
长跑内存/队列诊断脚本 (mock 模式)

用途: 复现并量化"长期运行内存增长"问题, 支持二分实验。

每 --interval 秒采样并输出一行 CSV:
  - 进程私有内存/工作集 (ctypes psapi, 无需 psutil)
  - EventBus 各 topic 队列统计 (qsize/drops/puts/gets)
  - UIBridge emit 计数 vs Qt 主线程实际处理数之差 (量化事件队列滞留)

实验开关 (monkey-patch, 不改生产代码):
  --skip-waveform   实验 A: 禁用主波形渲染
  --latest-only     实验 B: UIBridge.poll 只转发最新一帧 (丢旧留新)
  --skip-minichart  实验 C: 禁用 MiniChart 刷新
  --tracemalloc     每周期记录 top 分配, 结束时对比首尾快照

用法:
  .venv/Scripts/python.exe scripts/mem_profiler.py --minutes 30 --tag baseline
输出:
  <TEMP>/mem_profiler_out/<tag>.csv  与 <tag>.tracemalloc.txt
"""

from __future__ import annotations

import argparse
import csv
import ctypes
import os
import sys
import tempfile
import time
from ctypes import wintypes
from typing import Optional

# 确保可从仓库根目录 import scope
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Windows 进程内存采样 (psapi, 无需 psutil) ─────────────────────

class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


_psapi = ctypes.WinDLL("psapi", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_GetProcessMemoryInfo = _psapi.GetProcessMemoryInfo
_GetProcessMemoryInfo.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(PROCESS_MEMORY_COUNTERS_EX),
    wintypes.DWORD,
]
_GetProcessMemoryInfo.restype = wintypes.BOOL
_GetCurrentProcess = _kernel32.GetCurrentProcess
_GetCurrentProcess.restype = wintypes.HANDLE


def process_memory_mb() -> tuple[float, float]:
    """返回 (private_mb, working_set_mb)。失败时返回 (0, 0)。"""
    counters = PROCESS_MEMORY_COUNTERS_EX()
    counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS_EX)
    ok = _GetProcessMemoryInfo(
        _GetCurrentProcess(), ctypes.byref(counters), counters.cb
    )
    if not ok:
        return 0.0, 0.0
    return counters.PrivateUsage / 1048576.0, counters.WorkingSetSize / 1048576.0


# ── 实验 monkey-patch (在 ScopeApp.start() 之前安装) ─────────────

def _install_experiment_patches(args: argparse.Namespace):
    """按开关替换类方法。必须在 ScopeApp.start() 之前调用。"""
    if args.skip_waveform:
        from scope.ui.main_window import MainWindow

        def _no_waveform(self, frame):
            # 只更新状态栏, 跳过 16 通道 pyqtgraph setData/重绘
            try:
                self._update_status_bar(frame)
            except Exception:
                pass

        MainWindow._on_ui_raw_frame = _no_waveform
        print("[实验 A] 已禁用主波形渲染 (_on_ui_raw_frame 仅更新状态栏)")

    if args.latest_only:
        from scope.ui.ui_bridge import UIBridge

        def _poll_latest_only(self):
            # 每个队列只取最新一项转发, 丢弃中间积压帧
            import logging
            logger = logging.getLogger(__name__)
            for attr, signal_attr, counter_attr in (
                ("_raw_queue", "signal_raw_frame", "_raw_emitted"),
                ("_fitted_queue", "signal_fitted", "_fitted_emitted"),
                ("_status_queue", "signal_feedback_status", "_status_emitted"),
            ):
                q = getattr(self, attr)
                last = None
                while True:
                    item = q.get_nowait()
                    if item is None:
                        break
                    last = item
                if last is not None:
                    try:
                        getattr(self, signal_attr).emit(last)
                        setattr(self, counter_attr, getattr(self, counter_attr) + 1)
                    except Exception as e:
                        logger.error("latest-only emit 异常: %s", e)

        UIBridge.poll = _poll_latest_only
        print("[实验 B] UIBridge.poll 已改为只转发最新一帧 (丢旧留新)")

    if args.skip_minichart:
        from scope.ui.mini_chart import MiniChartWidget

        def _no_refresh(self):
            pass

        MiniChartWidget.refresh_now = _no_refresh
        print("[实验 C] 已禁用 MiniChart 刷新 (refresh_now -> no-op)")


# ── 指标收集 ─────────────────────────────────────────────────────

def collect_row(scope_app, counters: dict, elapsed_min: float) -> dict:
    private_mb, working_mb = process_memory_mb()

    eb = scope_app._event_bus.metrics()
    topics = {}
    for topic, queues in eb.items():
        qs = sum(q.qsize for q in queues)
        drops = sum(q.total_drops for q in queues)
        puts = sum(q.total_puts for q in queues)
        topics[f"{topic}#q"] = qs
        topics[f"{topic}#drops"] = drops
        topics[f"{topic}#puts"] = puts

    bridge = scope_app._ui_bridge
    raw_emitted = bridge._raw_emitted if bridge else -1
    fitted_emitted = bridge._fitted_emitted if bridge else -1
    status_emitted = bridge._status_emitted if bridge else -1

    row = {
        "t_min": round(elapsed_min, 2),
        "private_mb": round(private_mb, 1),
        "working_mb": round(working_mb, 1),
        "raw_emitted": raw_emitted,
        "raw_processed": counters["raw_processed"],
        "raw_pending": raw_emitted - counters["raw_processed"],
        "fitted_emitted": fitted_emitted,
        "fitted_processed": counters["fitted_processed"],
        "fitted_pending": fitted_emitted - counters["fitted_processed"],
        "status_emitted": status_emitted,
        "status_processed": counters["status_processed"],
        "status_pending": status_emitted - counters["status_processed"],
    }
    row.update(topics)
    return row


# ── 主流程 ───────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="长跑内存/队列诊断 (mock 模式)")
    parser.add_argument("--minutes", type=float, default=30.0, help="运行分钟数 (0=无限)")
    parser.add_argument("--interval", type=float, default=30.0, help="采样间隔秒")
    parser.add_argument("--tag", default="run", help="输出文件名标签")
    parser.add_argument("--skip-waveform", action="store_true", help="实验 A")
    parser.add_argument("--latest-only", action="store_true", help="实验 B")
    parser.add_argument("--skip-minichart", action="store_true", help="实验 C")
    parser.add_argument("--tracemalloc", action="store_true", help="记录 tracemalloc 快照")
    args = parser.parse_args()

    out_dir = os.path.join(tempfile.gettempdir(), "mem_profiler_out")
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, f"{args.tag}.csv")
    trac_path = os.path.join(out_dir, f"{args.tag}.tracemalloc.txt")

    import tracemalloc as tm
    if args.tracemalloc:
        tm.start(25)

    # 1. 安装实验 patch (必须在 start() 前)
    _install_experiment_patches(args)

    # 2. 启动应用 (mock)
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtCore import QTimer
    from scope.main import ScopeApp

    app = QApplication(sys.argv)
    scope_app = ScopeApp(mock=True)
    scope_app.start()

    # 3. 主线程处理计数器 (额外连接同一信号, 在主线程执行)
    counters = {
        "raw_processed": 0,
        "fitted_processed": 0,
        "status_processed": 0,
    }
    bridge = scope_app._ui_bridge
    bridge.signal_raw_frame.connect(lambda _f: counters.__setitem__("raw_processed", counters["raw_processed"] + 1))
    bridge.signal_fitted.connect(lambda _f: counters.__setitem__("fitted_processed", counters["fitted_processed"] + 1))
    bridge.signal_feedback_status.connect(lambda _f: counters.__setitem__("status_processed", counters["status_processed"] + 1))

    # 4. 周期采样
    f_csv = open(csv_path, "w", newline="", encoding="utf-8")
    writer = None
    t0 = time.monotonic()
    snapshots = []

    def sample():
        nonlocal writer
        elapsed_min = (time.monotonic() - t0) / 60.0
        row = collect_row(scope_app, counters, elapsed_min)
        if writer is None:
            writer = csv.DictWriter(f_csv, fieldnames=list(row.keys()))
            writer.writeheader()
        writer.writerow(row)
        f_csv.flush()
        line = (f"[{elapsed_min:7.1f}min] private={row['private_mb']:8.1f}MB "
                f"working={row['working_mb']:8.1f}MB "
                f"raw_pending={row['raw_pending']:6d} fitted_pending={row['fitted_pending']:6d} "
                f"status_pending={row['status_pending']:6d}")
        print(line, flush=True)

        if args.tracemalloc:
            snap = tm.take_snapshot()
            snapshots.append(snap)
            top = snap.statistics("lineno")[:15]
            with open(trac_path, "a", encoding="utf-8") as ft:
                ft.write(f"\n=== t={elapsed_min:.1f}min ===\n")
                for stat in top:
                    ft.write(f"{stat.size / 1048576.0:8.2f}MB  x{stat.count:<8d} {stat.traceback.format()[-1]}\n")

    timer = QTimer()
    timer.timeout.connect(sample)
    timer.start(int(args.interval * 1000))
    # 立即采一次 (t=0)
    QTimer.singleShot(1000, sample)

    print(f"CSV -> {csv_path}", flush=True)
    if args.tracemalloc:
        print(f"tracemalloc -> {trac_path}", flush=True)

    def finish():
        sample()
        f_csv.close()

        # 汇总
        rows = []
        with open(csv_path, newline="", encoding="utf-8") as fr:
            reader = csv.DictReader(fr)
            rows = list(reader)
        if len(rows) >= 2:
            first, last = rows[0], rows[-1]
            delta_mb = float(last["private_mb"]) - float(first["private_mb"])
            hours = (float(last["t_min"]) - float(first["t_min"])) / 60.0
            rate = delta_mb / hours if hours > 0 else 0.0
            print("\n===== 汇总 =====", flush=True)
            print(f"运行 {float(last['t_min']):.1f} min, 私有内存 {first['private_mb']} -> {last['private_mb']} MB "
                  f"(Δ {delta_mb:+.1f} MB, 约 {rate:+.1f} MB/h)", flush=True)
            print(f"raw_pending 首尾: {first['raw_pending']} -> {last['raw_pending']}", flush=True)
            print(f"fitted_pending 首尾: {first['fitted_pending']} -> {last['fitted_pending']}", flush=True)
            print(f"status_pending 首尾: {first['status_pending']} -> {last['status_pending']}", flush=True)

        if args.tracemalloc and len(snapshots) >= 2:
            growth = snapshots[-1].compare_to(snapshots[0], "lineno")
            print("\n===== tracemalloc 首尾对比 (增长 top 10) =====", flush=True)
            with open(trac_path, "a", encoding="utf-8") as ft:
                ft.write("\n\n===== 首尾对比 (增长) =====\n")
                for stat in growth[:10]:
                    line = f"{stat.size_diff / 1048576.0:+9.2f}MB  x{stat.count_diff:+8d} {stat.traceback.format()[-1]}"
                    print(line, flush=True)
                    ft.write(line + "\n")

        scope_app.stop()
        app.quit()

    total_ms = int(args.minutes * 60_000)
    if total_ms > 0:
        QTimer.singleShot(total_ms, finish)
    else:
        print("无限运行模式: Ctrl+C 终止 (终止时打印汇总)", flush=True)

    app.exec()


if __name__ == "__main__":
    main()
