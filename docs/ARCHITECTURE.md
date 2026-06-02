# 频率锁定示波器 — 系统架构文档 (freq_lock_with_stm32 分支)

## 1. 概述

基于 Python 的频率锁定示波器，STM32 串口采集 + RTMQ 射频卡扫频，实现 **V(f) 响应函数测量与 Lorentzian 线型拟合**，支持闭环 PID 反馈锁定。

### 硬件拓扑

```
RTMQ 射频卡 ── RF ──→ AOM ──→ 激光 ──→ PD ──→ STM32 ADC
    │                    TTL (同步)  →  STM32 CH1 (门控)
    │   COM8 (10Mbps)
    ▼
  Python (RtmqDevice)
    │
STM32 ── COM11 (115200) ──→ Python (Stm32Device) ──→ 波形 + 拟合 + 反馈
```

### 核心设计原则

| 原则 | 说明 |
|------|------|
| **STM32 时钟主控** | 采样时序由 STM32 硬件定时器决定，Python 只做数据消费 |
| **门控采集** | CH1 高电平 → 采集 CH0 电压，CH1 低电平 → 静默 → 封帧 |
| **扫频协调** | ScanCoordinator 全局单例管理扫频参数，线程安全原子读写 |
| **反馈可开关** | 调试时关闭反馈链路，只看拟合结果；确认后开启 PID 闭环 |
| **时间窗口出帧** | 数据连续场景下按 MAX_FRAME_DURATION 定时封帧 |

---

## 2. 总体分层

```
┌──────────────────────────────────────────────────────────────────────┐
│                       UI 层 (PyQt6 + pyqtgraph)                      │
│  波形视图 │ 通道面板 │ 设备设置 │ 测量面板 │ 扫频面板 │ 反馈面板    │
└───────────────────────────┬──────────────────────────────────────────┘
                            │ Qt Signal
┌───────────────────────────▼──────────────────────────────────────────┐
│                      分析计算层                                       │
│  ┌──────────┐ ┌──────────────┐ ┌──────────────────┐                 │
│  │ 自动测量 │ │ 扫频分析      │ │ 反馈分发 (开关)   │                 │
│  │ Pipeline │ │ V(t)→V(f)    │ │ FeedbackManager  │                 │
│  │ Vpp/Freq │ │ Lorentzian   │ │ dispatch(snap)   │                 │
│  └──────────┘ └──────────────┘ └──────────────────┘                 │
└───────────────────────────┬──────────────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────────────┐
│                      协调层                                           │
│  ScanCoordinator (全局单例, 线程安全)                                  │
│  ├─ ScanConfig (base_freq, scan_freq_amp, scan_dur)                  │
│  ├─ feedback_enabled (bool, 原子读写)                                 │
│  └─ state (IDLE / SCANNING / DONE)                                   │
└───────────────────────────┬──────────────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────────────┐
│                      硬件抽象层 (HAL)                                 │
│  AcquisitionDevice (ABC)                                             │
│  ├─ Stm32Device   ← STM32 串口 (COM11, 115200, 门控触发)            │
│  ├─ RtmqDevice    ← RTMQ 射频卡 (COM8, 10Mbps, intf_usb 单例)       │
│  └─ SimulatorDevice ← 模拟器 (1ch, 开发调试用)                       │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 3. 数据流时序

```
[RTMQ 射频卡]
    │ single_card() → RWG 卡自主扫频
    │ RF 扫频 + DIO TTL 同步输出
    ▼
[STM32 串口]
    │ CH1 高 → 采样 CH0 → printf("CH0 Voltage=...") → UART TX
    │ CH1 低 → 静默 → b''
    ▼
[Stm32Device._acquire_worker]          ← 采集线程
    │ in_waiting 轮询 (0.5ms) → read() 批量读取
    │ 按 \n 分割 → 解析电压 → 填入 buffer
    │ 门关闭 / buffer满 / 时间窗口 → _emit_frame()
    ▼
[ScopeApp._on_frame(chunk)]            ← 采集线程调用
    │
    ├→ make_analysis_result() → AnalysisResult (CH0)
    │
    ├→ Pipeline.process(result)
    │   └─ AutoMeasure → result.measurements
    │
    ├→ 扫频分析 (始终执行)
    │   ├─ ScanCoordinator.snapshot() → ScanConfig
    │   ├─ map_to_frequency_domain() → V(t)→V(f)
    │   └─ fit_lorentzian() → f0, gamma, R²
    │
    ├→ UI 刷新 (pyqtSignal → 主线程)
    │   ├─ WaveformView.update_waveform()
    │   ├─ MeasurementPanel.update_from_result()
    │   └─ ScanPanel.update_fit_result()
    │
    └→ 反馈分支 (feedback_enabled 开关控制)
        └─ FeedbackQueue → FeedbackManager.dispatch()
```

---

## 4. 核心组件

### 4.1 Stm32Device (`scope/hardware/stm32_device.py`)

| 特性 | 说明 |
|------|------|
| 通信方式 | pyserial, in_waiting 轮询 + read() 批量读取 |
| 通道数 | 1 (CH0) |
| 采样率 | 可配置 (默认 149 Sa/s, UI 可调) |
| 缓存长度 | 可配置 (默认 450 点, UI 可调) |
| 触发模式 | 门控 (CH1 高→采集, CH1 低→静默→封帧) |
| 出帧条件 | 门关闭(0.15s) / buffer满 / 时间窗口(1.0s) |
| 预分配 | start_acquisition() 时按 config.record_length 分配 numpy 数组 |
| stdout 抑制 | _emit_frame 时临时重定向到 os.devnull |

### 4.2 RtmqDevice (`scope/scan/rtmq_device.py`)

| 特性 | 说明 |
|------|------|
| 通信方式 | uart_intf (10Mbps) |
| 实例化 | 全局单例, 持续占用 COM8 |
| 扫频下发 | single_card(scan_freq_amp, base_freq, scan_dur) |
| 线程安全 | Lock 保护 |
| 执行方式 | run_in_executor (不阻塞事件循环) |

### 4.3 ScanCoordinator (`scope/scan/__init__.py`)

```python
@dataclass
class ScanConfig:
    base_freq: float = 146.0           # 中心频率 (MHz)
    scan_freq_amp: float = 0.5         # 扫频范围 (MHz, 总跨度)
    scan_dur: float = 1_000_000.0      # 扫频时长 (μs)

class ScanCoordinator:
    scan_config: ScanConfig            # 原子读写
    feedback_enabled: bool = False     # 反馈开关
    rtmq_device: RtmqDevice | None     # 硬件接口
    state: ScanState                   # IDLE / SCANNING / DONE

    async def upload_scan(config)      # 下发到 RWG
    def snapshot() → ScanConfig        # 采集线程安全读取
```

### 4.4 扫频分析 (`scope/scan/analysis.py`)

```python
def map_to_frequency_domain(v_t, time_axis, base_freq,
                            scan_freq_amp, scan_dur) → (f_axis, v_f):
    """线性扫频: f(t) = f_start + (scan_freq_amp/scan_dur) * t"""
    f_start = base_freq - scan_freq_amp / 2
    f_end   = base_freq + scan_freq_amp / 2

def fit_lorentzian(f_axis, v_f) → ScanFitResult:
    """Lorentzian: V(f) = offset + amp * gamma²/((f-f0)²+gamma²)"""
    # scipy.optimize.curve_fit
    # 回退: 峰值查找

@dataclass
class ScanFitResult:
    f0: float          # 中心频率 (MHz)
    gamma: float       # 线宽 HWHM (MHz)
    amplitude: float   # 峰值幅度 (V)
    offset: float      # 基线 (V)
    r_squared: float   # 拟合优度 R²
```

---

## 5. UI 面板

| Tab | 面板 | 说明 |
|-----|------|------|
| 通道 | `ChannelPanel` | 1 通道 CH0, 开关 + 电压量程 |
| 设备设置 | `DevicePanel` | COM口 / 波特率 / 采样率 / 缓存长度 (可编辑) |
| 测量 | `MeasurementPanel` | 动态测量行 |
| **扫频** | **`ScanPanel`** | 中心频率 / 扫频范围 / 扫频时长 / 🚀下发按钮 / 反馈开关 / 拟合结果 |
| 反馈 | `FeedbackPanel` | PID 反馈 slot 管理 |

### ScanPanel 布局

```
┌─ 扫频参数 ──────────────────────────────────┐
│  中心频率  [146.0] MHz                       │
│  扫频范围  [0.500] MHz  (实际 145.75~146.25) │
│  扫频时长  [1000000] μs  (= 1.000 s)         │
├─ 控制 ──────────────────────────────────────┤
│  [🚀 下发扫频配置]    [ ] 启用反馈链路        │
├─ 状态 ──────────────────────────────────────┤
│  ⏳ 等待下发 / 🟢 扫频中 / ✅ 扫频完成       │
├─ 最近拟合结果 ──────────────────────────────┤
│  f0 = 146.000123 MHz                        │
│  Γ  = 0.012345 MHz (HWHM)                   │
│  R² = 0.9998                                │
└─────────────────────────────────────────────┘
```

---

## 6. 项目文件结构

```
scope/
├── main.py                     # ScopeApp 入口 (设备创建 + 数据流编排)
├── scan/
│   ├── __init__.py             # ScanCoordinator + ScanConfig + ScanState
│   ├── rtmq_device.py          # RtmqDevice (intf_usb 单例)
│   └── analysis.py             # V(t)→V(f) + Lorentzian 拟合
├── hardware/
│   ├── device.py               # AcquisitionDevice (ABC)
│   ├── stm32_device.py         # Stm32Device (串口门控采集)
│   ├── art_device.py           # ArtDevice (ART USB, master 分支遗留)
│   └── simulator.py            # SimulatorDevice (模拟器)
├── processing/
│   ├── pipeline.py             # Pipeline 框架 (责任链)
│   ├── measurements.py         # 自动测量 (Vpp, Freq...)
│   ├── fft.py / math_ops.py / filters.py
├── io/
│   ├── feedback_manager.py     # FeedbackManager (asyncio)
│   └── feedback_slots/         # FeedbackSlot 实现
├── ui/
│   ├── main_window.py          # 主窗口 (5 个 Tab)
│   ├── waveform_view.py        # pyqtgraph 波形
│   ├── mini_chart.py           # 迷你趋势图
│   └── panels/
│       ├── channel_panel.py    # 通道 CH0
│       ├── device_panel.py     # 串口设置 + 采集参数
│       ├── measurement_panel.py # 动态测量
│       ├── scan_panel.py       # 扫频控制 + 拟合结果
│       └── feedback_panel.py   # 反馈管理
├── model/
│   ├── analysis_result.py      # AnalysisResult, ChannelData, TriggerInfo
│   └── enums.py
├── runtime/
│   ├── event_bus.py            # 有界队列
│   └── measurement_snapshot.py # 快照数据模型
└── config/
    └── settings.py             # 配置保存/加载

stm32/
├── serial_test.py              # STM32 串口测试脚本 (参考)
├── diag_serial.py              # 串口速率诊断
└── diag_timing.py              # 数据到达间隔诊断

rtmq/
├── single_card.py              # RTMQ 扫频参考实现
├── pulser2.py                  # rwg_play / rwg_init 封装
└── register_utils.py           # FPGA 寄存器工具
```

---

## 7. 启动方式

```bash
# 硬件模式 (连接 STM32 COM11)
python -m scope.main

# Mock 模式 (模拟器, 无需硬件)
python -m scope.main --mock

# 诊断脚本
python stm32/diag_serial.py COM11 115200
python stm32/diag_timing.py COM11 115200
```
