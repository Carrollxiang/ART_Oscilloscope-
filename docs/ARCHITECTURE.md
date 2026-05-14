# 数字示波器 — 系统架构文档

## 1. 概述

基于 Python 的数字示波器软件，驱动 ART 多通道 USB 采集卡，提供多通道波形显示、信号分析计算、以及灵活的网络数据反馈功能。

### 核心设计原则

| 原则 | 说明 |
|------|------|
| **硬件触发驱动** | 所有上层数据流由硬件触发事件驱动，无需软件定时器打拍子 |
| **硬件抽象隔离** | 通过 `AcquisitionDevice` 接口隔离硬件差异，上位机开发可先跑模拟器 |
| **反馈即插即用 (Hot-plug Feedback)** | 反馈通道可在运行时随时添加、移除、修改，不阻塞主采集流程 |
| **Watchdog 自愈** | 采集链路具备超时检测 → 自动重连 → 状态恢复的闭环能力 |

---

## 2. 总体分层

```
┌──────────────────────────────────────────────────────────────────────┐
│                         UI 层 (PyQt6 + pyqtgraph)                    │
│  波形视图 │ 通道面板 │ 触发面板 │ 测量面板 │ 反馈管理面板 │ 配置    │
└───────────────────────────┬──────────────────────────────────────────┘
                            │ Qt Signal → asyncio Queue
┌───────────────────────────▼──────────────────────────────────────────┐
│                      数据流管理层                                     │
│  触发引擎(硬件触发已由ART完成,软件层做触发位置标记)                    │
│  通道管理器 │ 水平/垂直缩放 │ 光标测量                                │
└───────────────────────────┬──────────────────────────────────────────┘
                            │ numpy array
┌───────────────────────────▼──────────────────────────────────────────┐
│                      分析计算层 (Pipeline)                           │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌─────────┐  │
│  │  自动测量 │ │  FFT     │ │ 数学运算 │ │  滤波    │ │ 协议解码│  │
│  │ Vpp/Freq │ │ 频谱分析 │ │ + - × ÷  │ │ FIR/IIR │ │ UART/SPI│  │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └─────────┘  │
│  责任链模式,每个通道可独立配置 Pipeline                               │
└───────────────────────────┬──────────────────────────────────────────┘
                            │ numpy array
┌───────────────────────────▼──────────────────────────────────────────┐
│                    缓存与采集层                                       │
│  ┌────────────────────┐ ┌────────────────────┐ ┌───────────────────┐│
│  │  Ring Buffer       │ │  Watchdog + 自动重连│ │  时间戳管理       ││
│  │  (每通道独立环形缓冲)│ │  健康监测状态机    │ │  time.monotonic   ││
│  └────────────────────┘ └────────────────────┘ └───────────────────┘│
└───────────────────────────┬──────────────────────────────────────────┘
                            │ raw bytes from USB
┌───────────────────────────▼──────────────────────────────────────────┐
│                      硬件抽象层 (HAL)                                 │
│  AcquisitionDevice (ABC)                                             │
│  ├─ ArtUsbDevice      ← ART 多通道 USB 采集卡 (真实硬件)              │
│  └─ SimulatorDevice   ← 生成测试信号用于软件开发调试                    │
│                                                                      │
│  接口: open/close/start_acquisition/stop_acquisition/read_chunk       │
│        reset/ping/restore_state/on_health_event                      │
└──────────────────────────────────────────────────────────────────────┘
```

### 第 1 层 — 硬件抽象层 (HAL)

**职责**: 封装与 ART 采集卡的 USB 通信细节

```python
class AcquisitionDevice(ABC):
    """所有采集设备的统一接口"""
    def open(self) -> bool
    def close(self)
    def start_acquisition(self)
    def stop_acquisition(self)
    def read_chunk(self) -> np.ndarray          # shape: (channels, samples)
    def configure(self, params: DeviceConfig)
    
    # Watchdog 支持
    def reset(self) -> bool                     # USB 级重置
    def ping(self) -> bool                      # 探活
    def restore_state(self, last_config: DeviceConfig)  # 重连后恢复配置
    
    @property
    def info(self) -> DeviceInfo                # 通道数、位宽、最大采样率
    
    # 事件回调
    on_health_event: Signal(DeviceHealthEvent)
```

**关键设计决策**:
- 硬件未就绪时使用 `SimulatorDevice` 开发上层逻辑
- `SimulatorDevice` 内置"故障注入"能力（随机断流、丢包），用于测试 Watchdog

### 第 2 层 — 缓存与采集层

**职责**: 管理 USB 数据流的接收、缓冲、健康监测

**核心组件**:

| 组件 | 说明 |
|------|------|
| `RingBuffer` | 每通道独立的环形缓冲区，支持快速读写 |
| `Watchdog` | 独立线程，监测数据流健康，触发自动重连状态机 |
| `TimestampManager` | 为每个采样块分配绝对时间戳 |

**Watchdog 自动重连状态机**:

```
         ┌──────────┐
         │  正常采集  │ ←──────────────────┐
         └────┬─────┘                    │
              │ 无数据超过 T1             │
              ▼                          │
         ┌──────────┐                   │
         │  探活中   │──── 收到 ping 回复 ─┘
         └────┬─────┘
              │ 无回复超过 T2
              ▼
         ┌──────────┐
         │  USB重置  │──── 重置成功 ──────┐
         └────┬─────┘                    │
              │ 重置失败                   │
              ▼                          │
         ┌──────────┐                   │
         │ 重新初始化 │──── 初始化成功 ────┘
         └────┬─────┘
              │ 多次失败
              ▼
         ┌──────────┐
         │ 硬件离线  │── 用户点击"重连" ──┘
         └──────────┘
```

### 第 3 层 — 分析计算层 (Pipeline)

**职责**: 对每帧数据进行信号处理和分析

**Pipeline 设计模式** — 责任链 (Chain of Responsibility):

```python
class PipelineStage(ABC):
    def process(self, result: AnalysisResult) -> AnalysisResult

# 每个通道可配置独立的处理链
ch1_pipeline = [
    LowPassFilter(cutoff=1e6),
    AutoMeasure(["Vpp", "Freq", "Vrms", "RiseTime"]),
]
ch2_pipeline = [
    AutoMeasure(["Vpp", "Freq"]),
]
math_pipeline = [
    MathOp("CH1 + CH2"),
]
```

**分析功能矩阵**:

| 类别 | 功能 | 实现 |
|------|------|------|
| 自动测量 | Vpp, Vmax, Vmin, Vrms | `numpy.ptp`, `numpy.max`, `numpy.std` |
| 自动测量 | 频率, 周期 | 过零检测 + 时间差 |
| 自动测量 | 占空比, 正/负脉宽 | 脉宽统计 |
| 频谱 | FFT, 频谱峰值 | `numpy.fft.rfft`, 峰值搜索 |
| 数学运算 | CH1+CH2, CH1-CH2, CH1×CH2, CH1/CH2 | numpy array ops |
| 数学运算 | 反相, 绝对值, 包络 | numpy array ops |
| 滤波 | 低通/高通/带通 | `scipy.signal` |
| 协议解码 | UART, I2C, SPI (扩展) | 软件采样分析 |

### 第 4 层 — 数据流管理层

**职责**: 协调 UI 状态与采集逻辑的映射

处理的核心映射关系:

```
硬件通道 (ADC CH1~CH4)
  └→ 软件通道 (CH1~CH4) — 垂直档位、探头比、耦合、开关
  └→ 数学通道 (MATH1~MATH4) — 来源于通道间的运算结果
  └→ 参考通道 (REF1~REF2) — 来源于已存储的波形
```

### 第 5 层 — UI 层

**职责**: 用户交互与波形显示

采用 PyQt6 + pyqtgraph：
- pyqtgraph 的 `PlotWidget` 内置高性能波形渲染（OpenGL 加速、自动降采样）
- Qt 的 `Signal/Slot` 机制天然适合跨线程通信
- Dock 系统支持面板自由拖拽布局

**面板构成**:

```
┌─────────────────────────────────────────────────────────┐
│  主窗口                                                   │
│  ┌──────────────────────────────────┐ ┌──────────────┐  │
│  │                                  │ │  通道控制面板   │  │
│  │   波形视图 (pyqtgraph)            │ │  CH1 ☑ 1V/div │  │
│  │   多通道叠加显示                   │ │  CH2 ☑ 2V/div │  │
│  │   时间轴/触发标记/光标             │ │  CH3 ☐       │  │
│  │                                  │ │  MATH1 ☐     │  │
│  │                                  │ └──────────────┘  │
│  ├──────────────────────────────────┤ ┌──────────────┐  │
│  │  触发设置面板                     │ │  反馈管理面板   │  │
│  │  类型: 边沿│源: CH1│电平: 0V     │ │  ┌─UDP:1.2.3.4┐│  │
│  │  斜率: 上升                       │ │  │● Modbus... ││  │
│  └──────────────────────────────────┘ │  │○ 串口...   ││  │
│  ┌──────────────────────────────────┐ │  └────────────┘│  │
│  │  测量读数面板                     │ │  [+ 添加反馈]  │  │
│  │  CH1 Vpp: 3.30V  Freq: 1.000kHz  │ └──────────────┘  │
│  │  CH2 Vpp: 1.65V  Freq: 1.000kHz  │                   │
│  └──────────────────────────────────┘                   │
└─────────────────────────────────────────────────────────┘
```

### 第 6 层 — 网络与 I/O 层

**职责**: 系统对外接口

| 子系统 | 技术 | 用途 |
|--------|------|------|
| FeedbackManager | asyncio | 管理所有反馈插槽的生命周期 |
| FeedbackSlot (N个) | asyncio | 每个协议独立实现，运行时动态增删 |
| REST API (可选) | FastAPI | 远程查询状态/配置 |
| 数据记录 | HDF5 | 原始数据存档与回放 |

---

## 3. 核心数据模型

```python
@dataclass
class ChannelData:
    """单个通道的一帧数据"""
    raw: np.ndarray                # 原始 ADC 码值或电压值
    time_axis: np.ndarray          # 相对时间轴 (秒), 长度同 raw
    sample_rate: float             # 实际采样率
    resolution: int                # ADC 位宽
    vertical_scale: float          # V/div
    vertical_offset: float         # 垂直偏移
    probe_attenuation: float = 1.0 # 探头衰减系数

@dataclass
class TriggerInfo:
    trigger_type: str              # "edge" | "pulse" | "immediate"
    trigger_source: int            # 通道索引
    trigger_level: float           # 触发电平
    trigger_slope: str             # "rising" | "falling"
    trigger_position: float        # 触发点在帧中的位置 (0~1, 通常 0.5)
    trigger_timestamp: float       # 绝对时间戳 (time.monotonic)

@dataclass
class AnalysisResult:
    """每完成一次硬件触发,产生一个此对象——是整个系统的黄金数据包"""
    sequence_num: int              # 单调递增, 下游用于检测丢帧
    trigger: TriggerInfo           # 本次触发的信息
    channels: dict[str, ChannelData]  # {"CH1": ChannelData, ...}
    
    # 分析结果 (由 Pipeline 填充)
    measurements: dict[str, float] # {"CH1_Vpp": 3.3, "CH1_Freq": 1000.0}
    fft: dict[str, np.ndarray]     # {"CH1": (freqs, magnitudes)}
    math_channels: dict[str, np.ndarray]  # {"MATH1": ...}
    decoded_protocols: dict[str, DecodeResult]
    
    # 元信息
    processing_latency: float      # 本次处理耗时 (debug 用)
```

---

## 4. 数据流时序

```
[ART 采集卡]
    │
    │ USB Bulk Transfer (硬件触发模式)
    ▼
[采集线程 — threading.Thread]
    │ 解析 USB 数据包 → numpy array
    │ 写入 RingBuffer
    │ 组装 AnalysisResult (channels + trigger_info)
    │
    ▼
[asyncio.Queue 跨线程传递]
    │
    ▼
[主事件循环 — asyncio + qasync]
    │
    ├→ Pipeline.process(result)
    │   ├─ 自动测量 (Vpp, Freq...)
    │   ├─ FFT 分析
    │   ├─ 数学运算
    │   └─ 更新 result.measurements / result.fft
    │
    ├→ UI 刷新 (通过 QThread.signal)
    │   └─ pyqtgraph.update() → 屏幕绘制
    │
    ├→ FeedbackManager.dispatch(result)
    │   ├─ Slot A (UDP):     on_data(result) → 立即发送
    │   ├─ Slot B (Modbus):  on_data(result) → 立即发送
    │   └─ Slot C (串口):    on_data(result) → 立即发送
    │   ※ 每次触发 = 每个 active slot 发送一次, 无节流/无缓存
    │
    └→ Recorder.record(result) (可选)
        └─ HDF5 文件存储
```

**关键特性**: 反馈的"周期"完全由硬件触发频率决定。1kHz 信号 → 每秒 1000 次反馈。工频 50Hz → 每秒 50 次。没有信号 → 零次反馈。不需要任何软件定时器参与同步。

---

## 5. Feedback 系统设计 (核心)

### 架构

```python
class FeedbackSlot(ABC):
    """一个独立的数据反馈通道, 运行时动态插拔"""
    
    slot_id: str
    status: SlotStatus  # idle | running | error
    
    @abstractmethod
    async def start(self):
        """启动"""
    @abstractmethod
    async def stop(self):
        """停止——可随时安全调用"""
    @abstractmethod
    async def on_data(self, result: AnalysisResult):
        """由 FeedbackManager 每次采集完成后调用, 事件驱动"""
    @abstractmethod
    async def reconfigure(self, config: FeedbackConfig):
        """运行时修改目标地址、协议参数、订阅项等"""

class FeedbackManager:
    """管理所有 slot 的生命周期"""
    
    def add_slot(self, slot: FeedbackSlot) -> str    # 返回 slot_id
    def remove_slot(self, slot_id: str)               # 运行中移除
    def get_slot(self, slot_id: str) -> FeedbackSlot
    def list_slots(self) -> list[SlotInfo]
    
    def dispatch(self, result: AnalysisResult):       # 由采集完成事件调用
        """遍历所有 running slot, 调用 on_data"""
        
    async def start_slot(self, slot_id: str)
    async def stop_slot(self, slot_id: str)
    async def reconfigure_slot(self, slot_id: str, config: FeedbackConfig)
```

### 协议实现优先级

| 阶段 | 协议 | 复杂度 |
|------|------|--------|
| Phase 1 | UDP (最简, 纯 socket) | ★☆☆☆☆ |
| Phase 1 | 串口 (serial) | ★★☆☆☆ |
| Phase 2 | Modbus TCP | ★★★☆☆ |
| Phase 3 | MQTT / HTTP / 自定义协议 | 按需 |

### 事件循环集成

使用 **qasync** 桥接 PyQt 事件循环和 asyncio:

```python
import qasync

async def main():
    app = QApplication(sys.argv)
    
    device = SimulatorDevice()
    pipeline = ProcessingPipeline()
    feedback_mgr = FeedbackManager()
    main_win = MainWindow()
    
    # 采集完成 → 分析 → 反馈 的事件链
    device.on_acquisition_complete.connect(
        lambda result: pipeline.process(result, callback=lambda processed:
            feedback_mgr.dispatch(processed)
        )
    )
    
    with qasync.QApplicationExecutor(app):
        await asyncio.gather(
            device.start(),
            main_win.show()
        )

qasync.run(main())
```

采集线程 (USB 同步读取) 在独立线程中运行, 通过 `asyncio.Queue` 跨线程传递 `AnalysisResult`。

---

## 6. 硬件 Watchdog (自动重连)

### 触发条件

采集线程每收到一个 USB 数据包, 调用 `watchdog.on_data()` 重置内部计数器。
如果超过 `T_TIMEOUT` 未收到任何数据包:

### 重连策略

| 级别 | 动作 | 超时 |
|------|------|------|
| 1 — 探活 | 发送 CMD_PING | 等待 T_PING=500ms |
| 2 — USB Reset | 调用 `device.reset()` (libusb 重置端口) | 等待 T_RESET=2s |
| 3 — 重新初始化 | 关闭设备 → `device.open()` → `restore_state()` | 等待 T_INIT=5s |
| 4 — 上报离线 | 持续失败 N 次 → UI 标记"硬件离线", 等待用户手动重连 | — |

每次升级级别前, 向 UI 发送 `DeviceHealthEvent`, 用户可实时看到当前重连进度。
