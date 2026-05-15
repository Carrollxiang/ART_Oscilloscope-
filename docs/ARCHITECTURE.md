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
│  波形视图 │ 通道面板 │ 设备设置 │ 测量面板 │ 反馈管理面板           │
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
│  ├─ ArtDevice         ← ART USB 采集卡 (artdaq/NI-DAQmx)             │
│  ├─ SimulatorDevice   ← 模拟器 (开发调试用)                           │
│  │                                                                   │
│  └─ 数据格式: read_chunk() → np.ndarray (channels, samples) float32   │
│                                                                      │
│  接口: open/close/start_acquisition/stop_acquisition/read_chunk       │
│        configure/reset/ping/restore_state/on_health_event            │
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

**实现**:

| 实现 | 说明 |
|------|------|
| `SimulatorDevice` | 纯 Python 模拟, 4 通道正弦/方波/三角/噪声, 故障注入, 无硬件依赖 |
| `ArtDevice` | ART USB 采集卡, 基于 `artdaq` (NI-DAQmx 兼容) 库, 通过 `Art_DAQ.dll` 通信 |

**ArtDevice 关键细节**:
- 使用 `artdaq.Task` API 直接操作设备 (绕过 artdaq_main.py 的全局 task)
- `read_chunk()` 调 `task.read()` → 返回 `list of lists` → 转为 `(ch, samples) float32 ndarray`
- 硬件触发由 `task.triggers.start_trigger.cfg_anlg_edge_start_trig()` 配置
- `read_timeout` 超时抛 `TimeoutError` → Watchdog 触发自动重连
- 未安装 `Art_DAQ.dll` 时 `open()` 返回 `False`, 程序可优雅降级

**关键设计决策**:
- 硬件未就绪时使用 `SimulatorDevice` 开发上层逻辑
- `SimulatorDevice` 内置"故障注入"能力（随机断流、丢包），用于测试 Watchdog
- `ArtDevice` 与 `SimulatorDevice` 互换只需改 `main.py` 一行代码

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
- 布局: 波形区在上 (3/4 空间), 配置面板在下 (1/4 空间)

**面板构成** (上下结构):

```
┌──────────────────────────────────────────────────────────────┐
│                       波形视图                                │
│  右上角图例: ■CH1(黄) ■CH2(青) ■CH3(紫) ■CH4(绿)           │
│  点击图例切换通道显隐, 隐藏→灰色+ (隐藏) 标注                │
│  触发位置标记 (白色虚线)                                      │
├──────────────────────────────────────────────────────────────┤
│  [通道]  [设备设置]  [测量]  [反馈]                           │
│  ┌──────┬──────────┬──────────┬────────────┐                 │
│  │CH1☑  │ 设备名   │名称/通道 │  rpyc:1.2.3│                 │
│  │1V/div│ Dev42    │/测量/时  │  ●运行     │                 │
│  │DC ▼  │AI ai0:3  │间段 → 值 │  ○暂停     │                 │
│  │1.0X  │采集参数  │[+添加]   │  [继续]    │                 │
│  │CH2☑  │触发配置  │[✕删除]  │  [+添加]   │                 │
│  │...   │[测试通讯]│          │  [编辑]    │                 │
│  │      │[✅应用]  │          │  [删除]    │                 │
│  └──────┴──────────┴──────────┴────────────┘                 │
├──────────────────────────────────────────────────────────────┤
│ 采样率: 10kHz │ 帧 #: 42 │ 触发: edge │ 反馈: 1/2 活跃      │
└──────────────────────────────────────────────────────────────┘
```

**各面板说明**:

| 面板 | 文件 | 说明 |
|------|------|------|
| 通道 | `channel_panel.py` | 4 通道复选框/垂直档位/耦合/探头比, 复选框同步波形显隐 |
| **设备设置** | `device_panel.py` | 替代原"触发"Tab, 含 ART 全部配置 + 通讯测试 + 应用按钮, 2 列布局 |
| 测量 | `measurement_panel.py` | 动态行: 名称+通道+测量项+起始/结束时间 → 窗口内计算值, 可任意增删 |
| 反馈 | `feedback_panel.py` | 动态 slot 列表, 添加/编辑/暂停/继续/删除, 支持 rpyc |

**波形视图特性** (waveform_view.py):
- 4 通道叠加, 黄/青/紫/绿 区分
- 右上角图例, 点击切换显隐 (隐藏时变灰)
- 触发位置白色虚线标记
- 通道面板复选框同步控制显隐
- OpenGL 加速渲染 (fallback 安全)

### 第 6 层 — 网络与 I/O 层

**职责**: 系统对外接口

| 子系统 | 技术 | 用途 |
|--------|------|------|
| FeedbackManager | asyncio | 管理所有反馈插槽的生命周期，事件驱动分发 |
| FeedbackSlot (N个) | asyncio + rpyc | 每个插槽对应一个远程仪器，运行时动态增删改 |
| RpycConnectionPool | threading + rpyc | 每个插槽维护一个连接池，复用 TCP 连接避免反复握手 |
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
    │   │ 根据每个 slot 的订阅从 result.measurements 提取 payload
    │   │
    │   ├─ Slot A (rpyc→192.168.1.10:18861)
    │   │   └─ on_data({"CH1_Vpp": 3.3})
    │   │      └─ pool.acquire() → rpyc.call("exposed_update", data) → pool.release()
    │   │
    │   ├─ Slot B (rpyc→10.0.0.5:18861)
    │   │   └─ on_data({"CH1_Freq": 1000.0})
    │   │      └─ pool.acquire() → rpyc.call("exposed_update", data) → pool.release()
    │   │
    │   └─ Slot C (Null, 调试用)
    │       └─ on_data(payload) → 写日志
    │
    │   ※ 全部 slot 并发 dispatch, 互不阻塞
    │   ※ 每次触发 = 每个 active slot 发一次, 无独立 Timer
    │   ※ rpyc 同步调用通过 run_in_executor 桥接到 asyncio
    │
    └→ Recorder.record(result) (可选)
        └─ HDF5 文件存储
```

**关键特性**: 反馈的"周期"完全由硬件触发频率决定。1kHz 信号 → 每秒 1000 次反馈。工频 50Hz → 每秒 50 次。没有信号 → 零次反馈。不需要任何软件定时器参与同步。

---

## 5. Feedback 系统设计 (核心)

### 设计原则

| 原则 | 说明 |
|------|------|
| **触发事件驱动** | 反馈由硬件触发直接驱动，无独立 Timer → 零过反馈 |
| **动态插拔** | slot 在运行时随时添加/移除/修改，不阻塞采集流程 |
| **rpyc 为主协议** | 实验室仪器均通过 rpyc 暴露接口，纯 socket 不友好 |
| **连接池复用** | 每个 slot 维护一个 rpyc 连接池，避免反复 TCP 握手 |
| **并发隔离** | 所有 slot 并发 dispatch，单个 slot 失败不影响其他 |

### 核心抽象

```python
class FeedbackSlot(ABC):
    """一个独立的数据反馈通道, 运行时动态插拔"""

    slot_id: str
    status: SlotStatus  # idle | running | error

    @abstractmethod
    async def start(self):
        """创建连接、初始化连接池"""

    @abstractmethod
    async def stop(self):
        """关闭连接池、释放资源"""

    @abstractmethod
    async def on_data(self, payload: dict[str, Any]):
        """
        推送一帧数据。
        payload 已由 FeedbackManager 根据 subscriptions 预组装好,
        slot 只管发送, 不涉及数据提取逻辑。
        """

    @abstractmethod
    async def reconfigure(self, config: SlotConfig):
        """运行时修改目标地址、订阅项、连接池参数"""

class FeedbackManager:
    """管理所有 slot 的生命周期 + 数据分发"""

    def add_slot(self, slot: FeedbackSlot, auto_start=True) -> str
    def remove_slot(self, slot_id: str) -> Optional[FeedbackSlot]
    def get_slot(self, slot_id: str) -> Optional[FeedbackSlot]
    def list_slots(self) -> list[SlotInfo]

    async def dispatch(self, result: AnalysisResult):
        """并发执行所有 active slot 的 on_data()"""
```

### 数据订阅模型

每个 slot **只接收它订阅的测量项**，由 FeedbackManager 在 dispatch 时过滤：

```python
@dataclass
class DataSubscription:
    local_key: str     # 本系统 key, 如 "CH1_Vpp"
    remote_key: str    # 远程仪器参数名 (为空则同 local_key)
    scale: float = 1.0 # 缩放
    offset: float = 0.0

# 一个 slot 的典型配置
RpycSlotConfig(
    slot_id="scope-to-oscillo",
    host="192.168.1.100",
    port=18861,
    remote_method="exposed_update",
    subscriptions=[
        DataSubscription(local_key="CH1_Vpp"),          # 原样发
        DataSubscription(local_key="CH1_Freq"),         # 原样发
        DataSubscription(local_key="CH1_Vpp",
                         remote_key="voltage_mv",
                         scale=1000.0),                 # V → mV
    ],
)
```

### Slot 状态管理

```
IDLE → start() → PAUSED (默认暂停, 连接池已创建, 不发送)
                        ↓ resume()
                   RUNNING (正常发送)
                        ↓ pause() 或 连续 3 次错误自动暂停
                   PAUSED (连接池保持, 不发送)
                        ↓ stop()
                   IDLE (连接池释放)
```

关键行为:
- `start()` 后默认 `PAUSED`, 用户需手动点"继续"开始发送 → 避免误推
- 连续 3 次 rpyc 调用失败 → 自动 `pause(auto=True)`, UI 弹窗提示
- dispatch 时跳过 `PAUSED` 和 `IDLE` 的 slot
- 订阅列表从测量面板动态读取 (`` MeasurementPanel.get_subscriptions() ``)

### RpycConnectionPool 连接池

**为什么需要连接池**:
- rpyc 握手涉及 TCP + 对象序列化协商，每次新建开销大
- 触发频率可达 KHz 级，不可能每个触发都建新连接
- 多 slot 各自独立池，避免端口耗尽

```
     ┌─────────────────────┐
     │  RpycConnectionPool │  ← 每个 slot 一个
     │                     │
     │  ┌─ conn #1 ─────┐  │  acquire() → 借一条
     │  │ in_use: false  │  │  release()  → 归还
     │  └────────────────┘  │
     │  ┌─ conn #2 ─────┐  │  健康检查: ping() 失败自动移除
     │  │ in_use: false  │  │  温备: start() 时预建 min_size 条
     │  └────────────────┘  │  超时: acquire 等待 > timeout → 抛异常
     │  ┌─ conn #3 ...   │  │
     └─────────────────────┘
```

```python
pool = RpycConnectionPool(
    host="192.168.1.100", port=18861,
    min_size=1,          # 启动时预建 1 条
    max_size=4,          # 最多 4 条并发
    acquire_timeout=10,  # 池满时等 10s
    idle_timeout=60,     # 60s 无使用自动关闭空闲连接
)

# 在同步线程中使用
conn = pool.acquire()
try:
    conn.root.exposed_update(data)
finally:
    pool.release(conn)
```

### 异步 ↔ 同步桥接

```
FeedbackManager.dispatch()        ← asyncio context
  └─ slot.on_data(payload)        ← async
       └─ run_in_executor(...)    ← 创建线程, 执行同步 rpyc 调用
            └─ _do_rpyc_call()
                 ├─ pool.acquire()    ← 阻塞直到拿到连接
                 ├─ conn.root.method(data)
                 └─ pool.release()
```

关键点: rpyc 是同步库，每个 `acquire` 可能阻塞等待。通过 `asyncio.get_event_loop().run_in_executor()` 将阻塞操作扔到线程池，不阻塞主事件循环。

### 协议支持现状

| 协议 | 状态 | 说明 |
|------|------|------|
| **rpyc** | ✅ 已实现 (Phase 1) | 主要协议，带连接池 |
| null (调试) | ✅ 已实现 (Phase 1) | 只打日志，不产生网络 I/O |
| UDP | 🔲 后续 | 标准库 socket，零依赖 |
| 串口 | 🔲 后续 | pyserial |
| Modbus | 🔲 后续 | pymodbus |
| MQTT / HTTP | 🔲 按需 | 暂未规划 |

### 事件循环集成

使用 **qasync** 桥接 PyQt 事件循环和 asyncio：

```python
import qasync

async def main():
    app = QApplication(sys.argv)

    device = SimulatorDevice()
    pipeline = ProcessingPipeline()
    feedback_mgr = FeedbackManager()
    main_win = MainWindow()

    # 添加一个 rpyc 反馈目标
    slot = RpycFeedbackSlot(RpycSlotConfig(
        slot_id="to-scope2",
        host="192.168.1.100", port=18861,
        subscriptions=[DataSubscription("CH1_Vpp")],
    ))
    await feedback_mgr.add_slot(slot)

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
