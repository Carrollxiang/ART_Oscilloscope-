"""
STM32 串口数据到达间隔诊断

测量每条数据的精确到达时间戳，分析瓶颈位置。

用法: python stm32/diag_timing.py [COM口] [波特率]
"""

import sys
import time
import serial
import numpy as np

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM11"
BAUD = int(sys.argv[2]) if len(sys.argv) > 2 else 115200

ser = serial.Serial(port=PORT, baudrate=BAUD, timeout=0)
print(f"串口: {PORT} @ {BAUD}")
print(f"理论最大: {BAUD/10/30:.0f} 行/s (30字节/行, 8N1)")
print("采集 500 条数据, 分析到达间隔...")
print()

line_buf = bytearray()
timestamps = []     # 每条数据到达的 perf_counter 时间戳
N = 500

while len(timestamps) < N:
    if ser.in_waiting == 0:
        time.sleep(0.0001)
        continue

    raw = ser.read(ser.in_waiting)
    line_buf.extend(raw)

    while True:
        nl = line_buf.find(b'\n')
        if nl < 0:
            break
        line = bytes(line_buf[:nl]).rstrip(b'\r')
        del line_buf[:nl + 1]
        if line:
            timestamps.append(time.perf_counter())
            if len(timestamps) >= N:
                break

ser.close()

# ── 分析 ──
deltas = np.diff(timestamps) * 1000  # ms

print(f"样本数: {len(deltas)}")
print(f"总耗时: {(timestamps[-1] - timestamps[0]):.3f}s")
print(f"平均速率: {len(deltas)/(timestamps[-1]-timestamps[0]):.1f} 行/s")
print()
print("到达间隔统计 (ms):")
print(f"  min:   {np.min(deltas):.3f}")
print(f"  max:   {np.max(deltas):.3f}")
print(f"  mean:  {np.mean(deltas):.3f}")
print(f"  median:{np.median(deltas):.3f}")
print(f"  std:   {np.std(deltas):.3f}")
print()
print("分布:")
pcts = [10, 25, 50, 75, 90, 95, 99]
for p in pcts:
    print(f"  P{p:2d}: {np.percentile(deltas, p):.3f}ms")

# ── 突发检测 ──
# 如果间隔 < 2ms, 说明数据在串口缓冲区积压后批量到达
burst = deltas[deltas < 2.0]
print(f"\n间隔 <2ms 的样本: {len(burst)}/{len(deltas)} ({100*len(burst)/len(deltas):.0f}%)")
if len(burst) > 0:
    print(f"  这些平均间隔: {np.mean(burst):.3f}ms")  # 接近 STM32 采样周期
    # 串口理论字节间隔: 1/(115200/10) = 0.087ms/byte, 30字节 = 2.6ms
    print(f"  串口传输 30 字节理论耗时: {30/(BAUD/10)*1000:.2f}ms")

# 大间隔
slow = deltas[deltas > 10.0]
print(f"\n间隔 >10ms 的样本: {len(slow)}/{len(deltas)} ({100*len(slow)/len(deltas):.0f}%)")
if len(slow) > 0:
    print(f"  这些平均间隔: {np.mean(slow):.3f}ms")
