"""
STM32 串口通讯诊断脚本 (v2 — in_waiting + read 批量读取)

用法: python stm32/diag_serial.py [COM口] [波特率]
"""

import sys
import time
import serial

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM11"
BAUD = int(sys.argv[2]) if len(sys.argv) > 2 else 115200

ser = serial.Serial(port=PORT, baudrate=BAUD, timeout=0)
print(f"串口已打开: {PORT} @ {BAUD}")
print("开始诊断 (Ctrl+C 停止)...")
print()

# 统计
total_lines = 0
data_lines = 0
empty_reads = 0         # in_waiting==0 的次数
burst_sizes = []        # 每轮 burst 的数据行数
cycle_times = []        # burst 开始到空行触发的时间 (s)
burst_start = time.perf_counter()
burst_lines = 0
line_buf = bytearray()

last_report = time.perf_counter()
report_lines = 0

try:
    while True:
        # ── 等待数据 (0.15s 超时) ──
        t_wait_start = time.perf_counter()
        while ser.in_waiting == 0:
            if time.perf_counter() - t_wait_start >= 0.15:
                break
            time.sleep(0.0005)

        if ser.in_waiting == 0:
            # 无数据: 门关闭
            empty_reads += 1
            if burst_lines > 0:
                burst_dur = time.perf_counter() - burst_start
                cycle_times.append(burst_dur)
                burst_sizes.append(burst_lines)
                print(f"  ⬛ BURST #{len(burst_sizes)}: {burst_lines} 行, "
                      f"持续 {burst_dur*1000:.0f}ms")
                burst_start = time.perf_counter()
                burst_lines = 0
            continue

        # ── 批量读取 ──
        raw = ser.read(ser.in_waiting)
        if not raw:
            continue

        # ── 按行分割 ──
        line_buf.extend(raw)
        while True:
            nl = line_buf.find(b'\n')
            if nl < 0:
                break
            line = bytes(line_buf[:nl]).rstrip(b'\r')
            del line_buf[:nl + 1]

            total_lines += 1
            report_lines += 1

            if line:
                data_lines += 1
                burst_lines += 1
            # 空行也计入但不统计为 data

        # ── 每秒汇总 ──
        now = time.perf_counter()
        if now - last_report >= 1.0:
            rate = report_lines / (now - last_report)
            print(f"  ... {total_lines} 行 | data={data_lines} empty_reads={empty_reads} "
                  f"速率={rate:.0f} 行/s")
            last_report = now
            report_lines = 0

except KeyboardInterrupt:
    print()
    print("=" * 50)
    print("诊断结果")
    print("=" * 50)

    print(f"总行数: {total_lines}, 数据行: {data_lines}, 空读: {empty_reads}")

    if burst_sizes:
        print(f"\nBurst: {len(burst_sizes)} 次")
        print(f"  数据行 avg={sum(burst_sizes)/len(burst_sizes):.0f} "
              f"min={min(burst_sizes)} max={max(burst_sizes)}")

    if cycle_times:
        avg = sum(cycle_times) / len(cycle_times)
        print(f"  周期 avg={avg*1000:.0f}ms ({avg:.3f}s) "
              f"min={min(cycle_times)*1000:.0f}ms max={max(cycle_times)*1000:.0f}ms")

    ser.close()
    print("\n串口已关闭")
