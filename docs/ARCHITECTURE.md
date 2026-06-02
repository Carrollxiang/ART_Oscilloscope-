# 频率锁定示波器 — 系统架构文档 (freq_lock_with_stm32 分支)

## 1. 概述

基于 Python 的频率锁定示波器，STM32 串口采集 + RTMQ 射频卡扫频，实现 **V(f) 响应函数测量与 Lorentzian 线型拟合**，支持闭环 PID 反馈锁定。

### 硬件拓扑

```
RTMQ 射频卡 ── RF ──→ AOM ──→ 激光 ──→ PD ──→ STM32 ADC (24-bit)
    │                    TTL (同步)  →  STM32 CH1 (门控)
    │   COM8 (10Mbps)
    ▼
  Python (RtmqDevice)
    │
STM32 ── COM11 (115200) ──→ Python (Stm32Device) ──→ EventBus ──→ 拟合/反馈/UI
```

### 核心设计原则

| 原则 | 说明 |
|------|------|
| **STM32 时钟主控** | 采样时序由 STM32 硬件定时器决定，Python 只做数据消费 |
| **门控采集** | CH1 高电平 → 采集 CH0 电压，CH1 低电平 → 静默 → 封帧 |
| **EventBus 解耦** | 测量、拟合、反馈、UI 各自独立消费，互不阻塞 (v0.5) |
| **扫频协调** | ScanCoordinator 全局单例管理扫频参数，线程安全原子读写 |
| **反馈可开关** | FeedbackPanel 中启用/关闭反馈链路，FeedbackWorker 自持 enabled |
| **时间窗口出帧** | 数据连续场景下按 MAX_FRAME_DURATION (3s) 定时封帧 |

---

## 2. 总体分层

```
┌──────────────────────────────────────────────────────────────────────┐
│                       UI 层 (PyQt6 + pyqtgraph)                      │
│  波形视图 │ 通道面板 │ 设备设置 │ 测量面板 │ 扫频面板 │ 反馈面板    │
└───────────────────────────┬──────────────────────────────────────────┘
                            │ Qt Signal (UIBridge 回调 → emit)
┌───────────────────────────▼──────────────────────────────────────────┐
│                      分析计算层 (独立线程)                            │
│  ┌──────────┐ ┌──────────────┐ ┌──────────────────┐                 │
│  │ 自动测量 │ │ FitWorker    │ │ FeedbackWorker   │                 │
│  │ Pipeline │ │ V(t)→V(f)    │ │ PID step→RPC     │                 │
│  │ Vpp/Freq │ │ Lorentzian   │ │ 自持 enabled     │                 │
│  └──────────┘ └──────────────┘ └──────────────────┘                 │
└───────────────────────────┬──────────────────────────────────────────┘
                            │ EventBus pub/sub
┌───────────────────────────▼──────────────────────────────────────────┐
│                      运行时基础设施层 (runtime)                       │
│  EventBus (1:N pub/sub)                                             │
│  ├─ topic "frame.measured" → FitWorker, UIBridge                    │
│  ├─ topic "frame.fitted"  → FeedbackWorker, UIBridge                │
│  MeasurementSnapshot / FittedSnapshot (帧间数据载体)                 │
│  BoundedQueue (有界队列 + 背压策略)                                   │
└───────────────────────────┬──────────────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────────────┐
│                      协调层                                           │
│  ScanCoordinator (全局单例, 线程安全)                                  │
│  ├─ ScanConfig (base_freq, scan_freq_amp, scan_dur)                  │
│  ├─ feedback_enabled (bool, 保留兼容)                                 │
│  └─ state (IDLE / SCANNING / DONE)                                   │
└───────────────────────────┬──────────────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────────────┐
│                      硬件抽象层 (HAL)                                 │
│  AcquisitionDevice (ABC)                                             │
│  ├─ Stm32Device   ← STM32 串口 (COM11, 115200, 门控触发, 24-bit ADC) │
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
    │ CH1 高 → 采样 CH0 → printf("CH0:raw_adc") → UART TX
    │ CH1 低 → 静默 → b''
    ▼
[Stm32Device._acquire_worker]          ← 采集线程
    │ in_waiting 轮询 (0.5ms) → read() 批量读取
    │ 按 \n 分割 → 解析原始 ADC 码值 → V = raw * 5.0 / 2^24
    │ 门关闭 / buffer满 / 3s超时 → _emit_frame()
    ▼
[ScopeApp._on_frame(chunk)]            ← 采集线程, 最小工作量
    │
    ├→ make_analysis_result() → AnalysisResult (CH0)
    ├→ Pipeline.process(result) → AutoMeasure → result.measurements
    ├→ compute_event_measurements() → event_measurements
    │
    └→ 构建 MeasurementSnapshot
        (sequence_num, raw_measurements, event_measurements,
         ch0_raw, ch0_time_axis, _analysis_result)
        │
        ▼ EventBus.publish("frame.measured")

[UIBridge._on_measured]                ← 回调 (采集线程内调用, emit Qt signal)
    └→ data_received.emit(AnalysisResult) → 主线程: 大示波器波形刷新

[FitWorker]                            ← 独立线程, 从 BoundedQueue 消费
    │ subscribe "frame.measured"
    ├→ ScanCoordinator.snapshot() → ScanConfig
    ├→ map_to_frequency_domain() → V(t)→V(f)
    ├→ fit_lorentzian() → f0, gamma, R²
    └→ 构建 FittedSnapshot → EventBus.publish("frame.fitted")

[UIBridge._on_fitted]                  ← 回调 (FitWorker 线程内调用, emit Qt signal)
    ├→ scan_panel_update.emit(fit_result) → 主线程: 扫频面板 + 测量面板
    └→ trend_update.emit({"f0": v, "__timestamp__": ts}) → 主线程: 迷你趋势图

[FeedbackWorker]                       ← 独立 async worker, 从 BoundedQueue 消费
    │ subscribe "frame.fitted"
    ├→ 检查自身 enabled 开关
    └→ FeedbackManager.dispatch(snap) → PID step → RPC
```

### 3.1 Topic 定义

| Topic | 生产者 | 消费者 | 数据类型 | 语义 |
|-------|--------|--------|----------|------|
| `frame.measured` | 采集线程 (_on_frame) | FitWorker (队列), UIBridge (回调) | MeasurementSnapshot | 原始采集 + 测量值 |
| `frame.fitted` | FitWorker | FeedbackWorker (队列), UIBridge (回调) | FittedSnapshot | 拟合结果已产出 |

### 3.2 订阅模式

| 模式 | 适用场景 | 实现 |
|------|---------|------|
| **队列订阅** `subscribe()` | 需要独立线程慢消费 (拟合/反馈) | BoundedQueue, 背压隔离 |
| **回调订阅** `subscribe_callback()` | 只需 emit Qt signal (UI) | publish 时直接调用, 无延迟 |

---

## 4. 核心组件

### 4.1 Stm32Device (`scope/hardware/stm32_device.py`)

| 特性 | 说明 |
|------|------|
| 通信方式 | pyserial, in_waiting 轮询 + read() 批量读取 |
| 通道数 | 1 (CH0) |
| ADC 分辨率 | 24 位 |
| 采样率 | 可配置 (默认 149 Sa/s, UI 可调) |
| 缓存长度 | 可配置 (默认 450 点, UI 可调) |
| 串口协议 | `CH0:raw_adc\r\n`, 电压 = raw × 5.0 / 2²⁴ V |
| 触发模式 | 门控 (CH1 高→采集, CH1 低→静默→封帧) |
| 出帧条件 | 门关闭(0.15s) / buffer满 / 时间窗口(3s) |
| 预分配 | start_acquisition() 时按 config.record_length 分配 numpy 数组 |

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
    feedback_enabled: bool = False     # 保留兼容 (实际由 FeedbackWorker.enabled 控制)
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

### 4.5 EventBus (`scope/runtime/event_bus.py`)

```python
class EventBus:
    """1:N 发布-订阅事件总线, 各 subscriber 独立背压隔离。"""

    def subscribe(topic, maxsize=2, on_drop=DROP_OLDEST, name="") → BoundedQueue
        """队列订阅: 返回 subscriber 专用的 BoundedQueue。"""

    def subscribe_callback(topic, callback, name="") → None
        """回调订阅: publish 时直接调用 callback(item), 无队列延迟。"""

    def publish(topic, item) → None
        """向 topic 所有 subscriber 发布:
          - 队列 subscriber → put 到各自 BoundedQueue
          - 回调 subscriber → 直接调用 callback"""

    def unsubscribe(topic, queue) → None
```

### 4.6 MeasurementSnapshot / FittedSnapshot (`scope/runtime/measurement_snapshot.py`)

```python
@dataclass
class MeasurementSnapshot:
    """单帧测量快照 — frame.measured topic 的数据类型。"""
    sequence_num: int
    raw_measurements: dict[str, float]    # Pipeline AutoMeasure 输出
    event_measurements: dict[str, float]  # 窗口化测量
    ch0_raw: np.ndarray | None            # 原始波形引用 (拟合完释放)
    ch0_time_axis: np.ndarray | None
    _analysis_result: AnalysisResult      # UIBridge 直接 emit (用后释放)

@dataclass
class FittedSnapshot(MeasurementSnapshot):
    """帧测量 + 拟合结果 — frame.fitted topic 的数据类型。"""
    fit_result: ScanFitResult | None
    # ch0_raw = None (已释放)

    @property
    def f0 → float | None
    @property
    def r_squared → float | None
    def as_dict() → dict[str, float]      # 展平含拟合指标
```

### 4.7 Workers (`scope/runtime/workers.py`)

| Worker | 订阅 | 队列 | 线程模式 | 职责 |
|--------|------|------|---------|------|
| FitWorker | `frame.measured` | BoundedQueue(2, DROP_OLDEST) | threading.Thread | V(f)映射 + Lorentzian拟合 → publish frame.fitted |
| FeedbackWorker | `frame.fitted` | BoundedQueue(2, DROP_OLDEST) | asyncio (独立线程) | PID step → RPC, 自持 enabled 开关 |
| UIBridge | `frame.measured` + `frame.fitted` | 无 (回调模式) | 无 (回调在发布者线程) | emit Qt signal → 主线程刷新 UI |

---

## 5. UI 面板

| Tab | 面板 | 说明 |
|-----|------|------|
| 通道 | `ChannelPanel` | 1 通道 CH0, 开关 + 电压量程 |
| 设备设置 | `DevicePanel` | COM口 / 波特率 / 采样率 / 缓存长度 (可编辑) |
| 测量 | `MeasurementPanel` | 动态测量行 + **拟合结果区 (f₀ / R² / σ)** |
| **扫频** | **`ScanPanel`** | 中心频率 / 扫频范围 / 扫频时长 / 🚀下发按钮 / 拟合结果 |
| 反馈 | `FeedbackPanel` | **启用反馈链路开关** + PID 反馈 slot 管理 |

### MeasurementPanel 拟合结果区

```
┌─ 拟合结果 ──────────────────────────────────┐
│  f₀ (本次):      146.000123 Hz              │
│  R² (拟合误差):   0.9998                     │
│  σ(f₀) (近N次):  0.000234 Hz  (N=20)        │
└─────────────────────────────────────────────┘
```

### ScanPanel 布局

```
┌─ 扫频参数 ──────────────────────────────────┐
│  中心频率  [146.0] MHz                       │
│  扫频范围  [0.500] MHz  (实际 145.75~146.25) │
│  扫频时长  [1000000] μs  (= 1.000 s)         │
├─ 控制 ──────────────────────────────────────┤
│  [🚀 下发扫频配置]                           │
├─ 状态 ──────────────────────────────────────┤
│  ⏳ 等待下发 / 🟢 扫频中 / ✅ 扫频完成       │
├─ 最近拟合结果 ──────────────────────────────┤
│  f0 = 146.000123 MHz                        │
│  Γ  = 0.012345 MHz (HWHM)                   │
│  R² = 0.9998                                │
└─────────────────────────────────────────────┘
```

### FeedbackPanel 布局

```
┌─────────────────────────────────────────────┐
│  [+ 添加 PID 反馈]  [ ] 启用反馈链路        │
├─────────────────────────────────────────────┤
│  ▶ [●] pid-lock-1  standard  ●运行  sent:42│
│  ...                                        │
└─────────────────────────────────────────────┘
```

### 迷你趋势图 (MiniChartWidget)

- **横轴**: 实际时间 (距启动时间, 单位 min)
- **数据源**: `frame.fitted` → UIBridge → trend_update signal → 小示波器
- **展示内容**: 仅 f₀ 一个物理量
- **滑动窗口**: 20 次拟合结果用于标准差计算

---

## 6. 项目文件结构

```
scope/
├── main.py                     # ScopeApp 入口 (EventBus + Workers 编排)
├── scan/
│   ├── __init__.py             # ScanCoordinator + ScanConfig + ScanState
│   ├── rtmq_device.py          # RtmqDevice (intf_usb 单例)
│   └── analysis.py             # V(t)→V(f) + Lorentzian 拟合
├── hardware/
│   ├── device.py               # AcquisitionDevice (ABC)
│   ├── stm32_device.py         # Stm32Device (串口门控采集, 24-bit ADC)
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
│   ├── main_window.py          # 主窗口 (5 个 Tab + trend_update signal)
│   ├── waveform_view.py        # pyqtgraph 波形
│   ├── mini_chart.py           # 迷你趋势图 (时间轴 min)
│   └── panels/
│       ├── channel_panel.py    # 通道 CH0
│       ├── device_panel.py     # 串口设置 + 采集参数
│       ├── measurement_panel.py # 动态测量 + 拟合结果 (f₀/R²/σ)
│       ├── scan_panel.py       # 扫频控制 (无反馈开关)
│       └── feedback_panel.py   # 反馈管理 + 启用开关
├── model/
│   ├── analysis_result.py      # AnalysisResult, ChannelData, TriggerInfo
│   └── enums.py
├── runtime/
│   ├── __init__.py             # 导出 EventBus, BoundedQueue, Snapshots
│   ├── event_bus.py            # BoundedQueue + EventBus (pub/sub)
│   ├── measurement_snapshot.py # MeasurementSnapshot + FittedSnapshot
│   └── workers.py              # FitWorker, FeedbackWorker, UIBridge
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
