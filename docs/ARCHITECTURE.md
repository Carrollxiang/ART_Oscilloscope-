# 数字示波器 — 系统架构文档 (v0.5)

> 最后更新: 2026/6/5  
> 重构里程碑: 简化数据模型 + 统一事件驱动架构

## 1. 概述

基于 Python 的数字示波器软件，驱动 ART 多通道 USB 采集卡，提供多通道波形显示、基础测量计算、以及灵活的网络数据反馈功能。

### v0.5 重构核心变更

| 变更项 | v0.3 (旧) | v0.5 (新) |
|--------|-----------|-----------|
| **数据模型** | `AnalysisResult` (复杂) | `RawFrame` (轻量: seq, data, sample_rate, timestamp) |
| **处理管道** | `ProcessingPipeline` 责任链 | `MeasurementProcessor` 扁平执行 |
| **测量功能** | 12个 (Vpp/Vrms/Freq/Period等) | 4个 (Vpp/Vmax/Vmin/Mean) |
| **数据流** | 多层 Pipeline | 单线程顺序计算 |
| **事件驱动** | Simulator用QTimer | 统一为 `set_data_callback()` |

### 核心设计原则

| 原则 | 说明 |
|------|------|
| **硬件触发 + 事件驱动** | NI-DAQmx `register_done_event` 回调驱动采集线程, 零轮询, 无 QTimer 阻塞 |
| **硬件抽象隔离** | 通过 `AcquisitionDevice` 接口隔离硬件差异，上位机开发可先跑模拟器 |
| **反馈即插即用 (Hot-plug Feedback)** | 反馈通道可在运行时随时添加、移除、修改，不阻塞主采集流程 |
| **数据模型最轻化** | RawFrame 只有 4 个字段，测量计算延迟 < 5ms |

---

## 2. 总体分层

```
┌──────────────────────────────────────────────────────────────────────┐
│                         UI 层 (PyQt6 + pyqtgraph)                    │
│  波形视图 │ 通道面板 │ 设备设置 │ 测量面板 │ 反馈管理面板           │
└───────────────────────────┬──────────────────────────────────────────┘
                            │ Qt Signal (UIBridge)
┌───────────────────────────▼──────────────────────────────────────────┐
│                      运行时层 (EventBus)                             │
│  ┌──────────────────────┐  ┌───────────────────────────────┐        │
│  │  MeasurementProcessor │  │  FeedbackManager               │        │
│  │  (独立线程, CPU密集)   │  │  (asyncio, 唯一订阅+并发分发)  │        │
│  │                       │  │  ┌─────────────────────────┐   │        │
│  │                       │  │  │ FeedbackWorker_1        │   │        │
│  │                       │  │  │ ├─ PidController        │   │        │
│  │                       │  │  │ └─ target (v0.7)        │   │        │
│  │                       │  │  ├─ FeedbackWorker_2 ...   │   │        │
│  │                       │  │  └─ asyncio.gather()       │   │        │
│  └──────────────────────┘  └───────────────────────────────┘        │
│                                                                      │
│  EventBus topics: frame.raw → frame.fitted                          │
│  MeasurementSpec: 纯配置数据类 (tag, channel, start_ms, feature)   │
└───────────────────────────┬──────────────────────────────────────────┘
                            │ RawFrame (numpy array)
┌───────────────────────────▼──────────────────────────────────────────┐
│                      硬件抽象层 (HAL)                                 │
│  AcquisitionDevice (ABC)                                             │
│  ├─ ArtDevice         ← ART USB 采集卡 (artdaq/NI-DAQmx)             │
│  ├─ SimulatorDevice   ← 模拟器 (事件驱动, 预生成帧)                  │
│  │                                                                   │
│  └─ 统一接口: set_data_callback(chunk) → 回调驱动                   │
│                                                                      │
│  接口: open/close/start_acquisition/stop_acquisition/read_chunk      │
│        configure/reset/ping/restore_state/set_data_callback         │
└──────────────────────────────────────────────────────────────────────┘
```

### 第 1 层 — 硬件抽象层 (HAL)

**职责**: 封装与 ART 采集卡的 USB 通信细节，统一事件驱动接口

```python
class AcquisitionDevice(ABC):
    """所有采集设备的统一接口"""
    def open(self) -> bool
    def close(self)
    def start_acquisition(self)
    def stop_acquisition(self)
    def read_chunk(self) -> np.ndarray          # shape: (channels, samples)
    def configure(self, params: DeviceConfig)
    
    # v0.5 新增：统一事件驱动
    def set_data_callback(self, callback: Callable[[np.ndarray], None])
    
    # Watchdog 支持
    def reset(self) -> bool
    def ping(self) -> bool
    def restore_state(self, last_config: DeviceConfig)
    
    # v0.5 简化：直接输出 RawFrame
    def make_raw_frame(self, chunk: np.ndarray) -> RawFrame
```

**实现对比**:

| 实现 | v0.3 | v0.5 |
|------|------|------|
| `SimulatorDevice` | QTimer 轮询 | **事件驱动** + 预生成10帧循环播放 |
| `ArtDevice` | 事件驱动 | 事件驱动 (无变化) |
| **接口统一** | ❌ 不一致 | ✅ 都使用 `set_data_callback()` |

**ArtDevice 关键细节**:
- 使用 `artdaq.Task` API 直接操作设备
- `read_chunk()` 调 `task.read()` → 返回 `list of lists` → 转为 `(ch, samples) float32 ndarray`
- 硬件触发由 `task.triggers.start_trigger.cfg_anlg_edge_start_trig()` 配置
- 采集模式: `AcquisitionType.FINITE` (有限点采集), 触发后采集 `samps_per_chan` 个点后自动停止
- `rearm()` 每帧读取后重建整个 Task (调用 `_close_task()` + `start_acquisition()`), 重新等待触发
- `read_timeout` 超时抛 `TimeoutError` → 上层捕获后跳过此帧继续下一帧
- **默认配置**: 16 通道 (ai0:15), 30k Sa/s, 触发源 ai12 上升沿 1V
- **DLL 路径**: `C:\Program Files (x86)\ART Technology\ArtDAQ\Lib\x64\Art_DAQ.dll`

**SimulatorDevice v0.5 新设计**:
- **预生成缓存**: 启动时生成 10 帧，循环播放
- **事件驱动**: 内部线程定时调用 `_data_callback(chunk)`
- **波形类型**: 正弦波、方波、三角波、噪声 (每通道独立配置)
- **故障注入**: `fail_on_read_every_n` 模拟硬件断连
- **触发间隔**: 默认 500ms (可配置)

---

## 3. 核心数据模型

### 3.1 RawFrame — 轻量级原始数据包

```python
@dataclass
class RawFrame:
    """
    原始数据帧 — 最小数据包，无测量结果。
    
    设计约束:
      - 只有 4 个字段，易于传递
      - 不包含任何分析结果
      - 由 MeasurementProcessor 独立线程消费
    """
    sequence_num: int              # 单调递增, 下游用于检测丢帧
    data: np.ndarray               # shape: (n_channels, n_samples), float32
    sample_rate: float             # 实际采样率
    timestamp: float = field(default_factory=time.monotonic)
    
    @property
    def n_channels(self) -> int
    @property
    def n_samples(self) -> int
    @property
    def duration_ms(self) -> float  # n_samples / sample_rate * 1000
    
    def time_axis(self) -> np.ndarray:
        """返回相对时间轴 (秒)"""
```

**对比 AnalysisResult (v0.3)**:

| 字段 | AnalysisResult (旧) | RawFrame (新) |
|------|---------------------|---------------|
| 原始波形 | ✅ `channels: dict[str, ChannelData]` | ✅ `data: np.ndarray` |
| 触发信息 | ✅ `trigger: TriggerInfo` | ❌ 删除 |
| 测量结果 | ✅ `measurements: dict` | ❌ 删除 |
| 频谱 | ✅ `fft: dict` | ❌ 删除 |
| 数学通道 | ✅ `math_channels: dict` | ❌ 删除 |
| 协议解码 | ✅ `decoded_protocols: dict` | ❌ 删除 |
| **字段总数** | 8 | **4** |

---

### 3.2 MeasurementSpec — 测量规格 (纯配置)

```python
@dataclass
class MeasurementSpec:
    """
    测量规格 — 纯配置数据类（无计算逻辑）。
    
    定义如何在 RawFrame 上切片并计算单个测量值。
    """
    tag: str                        # 语义名，如 "CH1_vpp"
    channel: int                    # 通道索引 (0-based)
    start_ms: float = 0.0           # 时间窗起始 (毫秒)
    end_ms: float = 0.0             # 时间窗结束 (0 表示帧结尾)
    feature: str = "Vpp"            # 特征类型: Vpp, Vmax, Vmin, Mean
    semantic: str = ""              # 可选说明
```

**特性**:
- ✅ 纯数据类，不包含计算逻辑
- ✅ 由 MeasurementPanel 动态创建
- ✅ 运行时可修改（每10帧同步一次）
- ✅ 对应测量面板的一行配置

**支持的测量特征** (v0.5):

| feature | 说明 | 实现 |
|---------|------|------|
| `Vpp` | 峰峰值 | `np.ptp(segment)` |
| `Vmax` | 最大值 | `np.max(segment)` |
| `Vmin` | 最小值 | `np.min(segment)` |
| `Mean` | 平均值 | `np.mean(segment)` |

**已删除** (v0.3):
- ❌ `Vrms` (有效值)
- ❌ `Integral` (积分)
- ❌ `Freq` (频率)
- ❌ `Period` (周期)
- ❌ `DutyCycle` (占空比)
- ❌ `RiseTime` / `FallTime`

---

### 3.3 FittedSnapshot — 测量结果快照

```python
@dataclass
class FittedSnapshot:
    """
    测量结果快照 — MeasurementProcessor 的输出。
    
    包含所有测量结果，不含原始波形。
    """
    sequence_num: int
    event_measurements: dict[str, float] = field(default_factory=dict)
    pipeline_latency_ms: float = 0.0
    timestamp: float = field(default_factory=time.monotonic)
    
    def get(self, tag: str) -> Optional[float]:
        """获取单个测量值"""
        return self.event_measurements.get(tag)
    
    def as_flat_dict(self) -> dict[str, float]:
        """返回扁平字典 (用于 FeedbackManager / MiniChart)"""
        return dict(self.event_measurements)
```

**特性**:
- ✅ 只包含测量结果，不包含原始波形
- ✅ 扁平结构：`{"CH1_vpp": 3.3, "CH1_mean": 1.5, ...}`
- ✅ 直接被 FeedbackManager 消费
- ✅ 通过 UIBridge 传递到 UI 主线程

---

## 4. 数据流时序 (v0.5)

```
[ART 采集卡 / SimulatorDevice]
    │
    │ Task.start() → 等待硬件触发 (FINITE模式)
    │ 触发信号 → Task 采集完成 → DONE 事件
    ▼
[NI-DAQmx DONE 回调 / Simulator 内部线程]
    │ Threading.Event.set()
    ▼
[ArtDevice._acquire_worker / Simulator._trigger_worker]
    │ Event.wait() → 唤醒 → read_chunk() → _data_callback(chunk)
    ▼
[ScopeApp._on_frame(chunk)]          ← 采集线程调用
    │
    ├→ make_raw_frame(chunk) → RawFrame
    │
    ├→ event_bus.publish("frame.raw", RawFrame)
    │
    └→ ui_bridge.poll() → emit Qt Signal
        │
        ▼
[MeasurementProcessor (独立线程)]
    │
    │ subscribe("frame.raw") → RawFrame
    │
    ├→ 遍历 MeasurementSpec 列表:
    │     ├─ 切片: segment = frame.data[channel, start_idx:end_idx]
    │     ├─ 计算: np.ptp/max/min/mean(segment)
    │     └─ 写入: measurements[tag] = value
    │
    └→ publish("frame.fitted", FittedSnapshot)
        │
        ▼
[UIBridge (采集线程)]
    │
    │ subscribe("frame.fitted") → FittedSnapshot
    │
    └→ signal_fitted.emit(snapshot)
        │
        ▼
[Qt 主线程]
    │
    ├→ MainWindow._on_ui_fitted(snapshot)
    │     ├─ measure_panel.update_from_fitted() → 显示测量值
    │     └─ mini_chart.add_data() + refresh_now() → 更新趋势图
    │
    └→ FeedbackManager._dispatch_loop() (asyncio)
          └─ snapshot.as_flat_dict() → 并发分发
                  │
                  └─ asyncio.gather(worker.process(value) for each worker)
```

**关键特性**:
- ✅ 采集由 **register_done_event 事件驱动**, 零 CPU 轮询
- ✅ MeasurementProcessor 在**独立线程**运行，不阻塞采集
- ✅ 每次触发一帧 (30k Sa/s × 0.5s = 15000 点 × 16ch)
- ✅ 测量延迟 < 5ms (4个测量项)
- ✅ 反馈周期 = 硬件触发频率

---

## 5. MeasurementProcessor 设计

### 5.1 职责

**对比 v0.3 Pipeline**:

| 项 | v0.3 Pipeline | v0.5 MeasurementProcessor |
|----|---------------|---------------------------|
| 架构 | 责任链模式 (Stage1→Stage2→...) | 扁平执行 (单线程循环) |
| 输入 | `AnalysisResult` (复杂数据包) | `RawFrame` (轻量数据包) |
| 配置 | 每个 Stage 独立配置 | `MeasurementSpec` 统一配置 |
| 可扩展性 | 高 (可插入新 Stage) | 低 (需修改 feature 枚举) |
| 延迟 | 多层累积 | 单层顺序 < 5ms |
| 复杂度 | 高 | **低** |

**设计决策**:
- ✅ 简化为单线程顺序计算
- ✅ 只支持 4 个基本测量量
- ✅ 删除 FFT、滤波、数学运算等复杂功能
- ✅ 降低维护成本，提高稳定性

### 5.2 核心代码

```python
class MeasurementProcessor:
    """测量处理器 — 消费 RawFrame，按规格计算，输出 FittedSnapshot。"""
    
    def __init__(self, event_bus: EventBus, specs: list[MeasurementSpec]):
        self._event_bus = event_bus
        self._specs = specs
        self._queue = event_bus.subscribe("frame.raw")
        self._thread: Optional[threading.Thread] = None
    
    def set_specs(self, specs: list[MeasurementSpec]):
        """运行时更新测量规格（线程安全）"""
        with self._lock:
            if len(self._specs) != len(specs):
                logger.info(f"MeasurementSpec 已更新: {len(specs)} 项")
            self._specs = list(specs)
    
    def _run_loop(self):
        """主循环：消费 frame.raw → 计算 → publish frame.fitted"""
        while not self._stop_event.is_set():
            frame = self._queue.get(timeout=0.1)
            if frame is not None:
                self._process_frame(frame)
    
    def _process_frame(self, frame: RawFrame):
        """处理一帧：遍历所有 spec 计算"""
        t0 = time.monotonic()
        
        measurements = {}
        for spec in self._specs:
            value = self._compute(frame, spec)
            if value is not None:
                measurements[spec.tag] = value
        
        latency_ms = (time.monotonic() - t0) * 1000
        snap = FittedSnapshot(
            sequence_num=frame.sequence_num,
            event_measurements=measurements,
            pipeline_latency_ms=latency_ms,
        )
        self._event_bus.publish("frame.fitted", snap)
    
    @staticmethod
    def _compute(frame: RawFrame, spec: MeasurementSpec) -> Optional[float]:
        """单个 spec 的计算逻辑"""
        if spec.channel < 0 or spec.channel >= frame.n_channels:
            return None
        
        raw = frame.data[spec.channel]
        fs = frame.sample_rate
        
        # 计算切片索引
        start_idx = max(0, int(spec.start_ms / 1000.0 * fs))
        end_idx = frame.n_samples if spec.end_ms <= 0 else min(frame.n_samples, int(spec.end_ms / 1000.0 * fs))
        
        if end_idx <= start_idx:
            return None
        
        segment = raw[start_idx:end_idx]
        if len(segment) == 0:
            return None
        
        # 特征计算
        feature = spec.feature.lower()
        if feature == "vpp":
            return float(np.ptp(segment))
        elif feature == "vmax":
            return float(np.max(segment))
        elif feature == "vmin":
            return float(np.min(segment))
        elif feature == "mean":
            return float(np.mean(segment))
        else:
            logger.warning(f"未知特征类型: {spec.feature}")
            return None
```

### 5.3 性能分析

**单帧计算时间** (15000 samples, 4 measurements):

| 操作 | 耗时 |
|------|------|
| 切片 segment | < 0.1ms |
| `np.ptp()` | < 0.5ms |
| `np.max()` | < 0.5ms |
| `np.min()` | < 0.5ms |
| `np.mean()` | < 0.5ms |
| **总计** | **< 5ms** |

**对比帧周期**: 500ms → 延迟占比 **< 1%**

---

## 6. 反馈系统设计 (v0.6)

### 6.1 架构变更 (v0.5 → v0.6)

| 特性 | v0.5 (旧) | v0.6 (新) |
|------|-----------|-----------|
| 反馈单元 | `FeedbackSlot` (基类) | `FeedbackWorker` (独立单元) |
| PID 封装 | 在 Slot 内部 | **独立 `PidController` 组件** |
| EventBus 订阅 | 每个 Slot 各自订阅 | **唯一订阅** (FeedbackManager 持有) |
| `as_flat_dict()` 调用 | N 次/帧 (每个 Slot) | **1 次/帧** (Manager 预过滤) |
| 数据分发 | `dispatch_raw()` + `DataSubscription` | Worker 直接通过 `measurement_key` 获取值 |

### 6.2 数据流

```
FittedSnapshot (测量结果)
  │
  ↓
EventBus (frame.fitted topic)
  │
  ↓
**1 个共享订阅** → FeedbackManager._dispatch_loop()
  │
  ├─ snapshot.as_flat_dict()  ← 只调用 1 次
  │
  └─ 并发分发给所有 worker
        │
        ├─→ FeedbackWorker_1
        │     ├─ 提取 "CH1_vpp" 值
        │     ├─ PidController.step(value)
        │     └─ 发送调整指令 (v0.7 实现)
        │
        ├─→ FeedbackWorker_2
        │     └─ ...
        │
        └─→ asyncio.gather()  ← 并发执行
```

### 6.3 组件层次

```
┌─────────────────────────────────────────────────────────┐
│              FeedbackManager (生命周期管理)             │
│  - 持有唯一的 EventBus 订阅                             │
│  - 管理 worker 生命周期                                 │
│  - 并发分发数据                                         │
└───────────────────┬─────────────────────────────────────┘
                    │
        ┌───────────┼───────────┬─────────────┐
        │           │           │             │
        ↓           ↓           ↓             ↓
  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
  │ Worker_1 │ │ Worker_2 │ │ Worker_3 │ │ Worker_N │
  ├──────────┤ ├──────────┤ ├──────────┤ ├──────────┤
  │PidControl│ │PidControl│ │PidControl│ │PidControl│
  │  _errors │ │  _errors │ │  _errors │ │  _errors │
  │  deque(10)│ │  deque(10)│ │  deque(10)│ │  deque(10)│
  ├──────────┤ ├──────────┤ ├──────────┤ ├──────────┤
  │Target:   │ │Target:   │ │Target:   │ │Target:   │
  │ AD9910   │ │ RTMQ     │ │ Null     │ │ AD9910   │
  └──────────┘ └──────────┘ └──────────┘ └──────────┘
```

### 6.4 核心组件

**PidController** (`scope/runtime/pid_controller.py`):
- 封装 PID 计算逻辑（P/I/D + 死区 + 积分限幅 + 输出限幅）
- `deque(maxlen=window_size)` 自动丢弃旧误差
- 独立组件，不依赖反馈系统

**FeedbackWorker** (`scope/io/feedback_worker.py`):
- 被动接收测量值（不订阅 EventBus）
- 内部持有 `PidController`
- 状态管理: `IDLE → RUNNING ↔ PAUSED`
- `process(value)` 由 Manager 调用
- v0.6 阶段 `_send_to_target()` 只记录日志（v0.7 实现）

**FeedbackManager** (`scope/io/feedback_manager.py`):
- 持有唯一 `frame.fitted` 订阅
- `_dispatch_loop()` — asyncio 协程，提取数据后并发分发
- Worker 管理: `add_worker / remove_worker / pause_worker / resume_worker`
- 配置管理: `get_config() / load_config()` 支持 JSON 导入导出

### 6.5 关键接口

```python
# 配置
@dataclass
class FeedbackConfig:
    worker_id: str                    # 唯一标识符
    measurement_key: str              # 如 "CH1_vpp"
    pid_config: PidConfig             # PID 参数
    target: Optional[TargetConfig]    # v0.7 预留

# Worker 生命周期（由 Manager 调用）
await worker.start()   # → RUNNING
await worker.pause()   # → PAUSED
await worker.resume()  # → RUNNING
await worker.stop()    # → IDLE

# Manager 核心接口
class FeedbackManager:
    async def add_worker(self, config: FeedbackConfig) -> str
    async def remove_worker(self, worker_id: str)
    async def pause_worker(self, worker_id: str)
    async def resume_worker(self, worker_id: str)
    async def load_config(self, config_list: list[dict])
    def get_config(self) -> list[dict]
    def list_workers(self) -> list[dict]
```

### 6.6 性能对比

| 指标 | v0.5 | v0.6 |
|------|------|------|
| EventBus 订阅数 | N 个 | **1 个** |
| `as_flat_dict` 调用/帧 | N 次 | **1 次** |
| Worker 隔离性 | ✅ | ✅ |
| 目标设备接口 | 未统一 | **预留 AD9910/RTMQ** (v0.7) |

---

---

## 7. UI 层架构

### 7.1 UIBridge — 采集线程 → Qt 主线程桥接

```python
class UIBridge(QObject):
    """采集线程 → Qt 主线程桥接"""
    
    signal_raw_frame = pyqtSignal(object)   # RawFrame
    signal_fitted = pyqtSignal(object)      # FittedSnapshot
    
    def poll(self):
        """非阻塞轮询两个队列，有数据则 emit"""
        # 1. 原始帧（主波形）
        raw = self._raw_queue.get_nowait()
        while raw is not None:
            self.signal_raw_frame.emit(raw)
            raw = self._raw_queue.get_nowait()
        
        # 2. 拟合结果（测量面板 + MiniChart）
        fitted = self._fitted_queue.get_nowait()
        while fitted is not None:
            self.signal_fitted.emit(fitted)
            fitted = self._fitted_queue.get_nowait()
```

### 7.2 面板构成

**主窗口布局** (main_window.ui):
```
┌──────────────────────────────────────────────────────────────┐
│                       波形视图                                │
│  右上角图例 (2列): CH1~CH16 点击切换显隐                      │
│  自动降采样: >2000 点/通道时压缩至 ~1500 点                   │
├─────────────┬────────────────────────────────────────────────┤
│ 迷你图      │  配置 Tabs                                     │
│ (左下角)    │  [通道] [设备] [测量] [反馈]                    │
│ 触发驱动刷新│                                                │
└─────────────┴────────────────────────────────────────────────┘
```

**各面板说明**:

| 面板 | 文件 | v0.5 特性 |
|------|------|-----------|
| 通道 | `channel_panel.py` | 16 通道 2 列网格, 逐通道电压量程 |
| 设备 | `device_panel.py` | 4 列布局: 设备 \| 触发 \| 采集 \| 测试 |
| 测量 | `measurement_panel.py` | 动态行: 名称+通道+测量项+时间窗 → 值 |
| 反馈 | `feedback_panel.py` | PID 反馈卡片: 开始/暂停/继续三态 |

### 7.3 MiniChart 触发驱动策略

**v0.5 设计**:
- ✅ 每次硬件触发最多更新一次 (与测量更新同节拍)
- ✅ 数据更新时调用 `refresh_now()` 刷新曲线
- ✅ 禁用独立 QTimer，完全由 `signal_fitted` 驱动
- ✅ 最近 N 点 (建议 300~3600)
- ✅ 曲线对象复用 (`setData`), 禁止频繁重建

**关键修复** (v0.5):
```python
# main_window.py
def _on_ui_fitted(self, fitted_snapshot: FittedSnapshot):
    if hasattr(self, 'measure_panel'):
        self.measure_panel.update_from_fitted(fitted_snapshot)
    
    flat = fitted_snapshot.as_flat_dict()
    if flat and hasattr(self, 'mini_chart'):
        self.mini_chart.add_data(flat)
        self.mini_chart.refresh_now()  # ← 新增：立即刷新
```

---

## 8. EventBus 设计

### 8.1 Topic 定义

| Topic | Payload 类型 | maxsize | drop 策略 | 生产者 | 消费者 |
|-------|-------------|---------|-----------|--------|--------|
| `frame.raw` | `RawFrame` | 2 | drop_oldest | `_on_frame()` | MeasurementProcessor, UIBridge |
| `frame.fitted` | `FittedSnapshot` | 2 | drop_oldest | MeasurementProcessor | **FeedbackManager**, UIBridge |
| `config.change` | `ConfigChange` | 8 | block | UI 面板 | ConfigWorker |
| `measurement.specs.changed` | `MeasurementSpecsChanged` | 4 | drop_oldest | MeasurementPanel | MeasurementConfigWorker |
| `feedback.worker.command` | `FeedbackCommand` | 32 | block | FeedbackPanel | FeedbackCommandWorker |
| `measurement.remove` | `str` | 8 | block | MeasurementPanel | MainWindow / MiniChart |

### 8.2 线程边界

```
┌─────────────────────────────────────────────────────────┐
│  采集线程（DONE 回调 / Simulator 内部线程）              │
│    _on_frame() → publish(frame.raw)                     │
│    UIBridge.poll() → emit QSignal (非阻塞)              │
│                                                         │
├─────────────────────────────────────────────────────────┤
│  MeasurementProcessor 线程                               │
│    queue.get() → 计算 → publish(frame.fitted)           │
│                                                         │
├─────────────────────────────────────────────────────────┤
│  asyncio 线程                                            │
│    FeedbackManager._dispatch_loop(): queue → dispatch()  │
│    ConfigWorker: config.change → _on_art_config()        │
│    MeasurementConfigWorker: specs.changed → set_specs()  │
│    FeedbackCommandWorker: worker.command → manager API   │
│                                                         │
├─────────────────────────────────────────────────────────┤
│  Qt 主线程                                               │
│    DevicePanel.config_applied → publish(config.change)   │
│    MeasurementPanel edits → publish(specs.changed)       │
│    FeedbackPanel actions → publish(worker.command)       │
│    UIBridge.signal → waveform_view / panel / mini_chart │
└─────────────────────────────────────────────────────────┘
```

### 8.3 BoundedQueue 特性

| 特性 | 说明 |
|------|------|
| 线程安全 | `threading.Lock` 保护读写 |
| 背压策略 | `drop_oldest` (丢弃最旧) / `block` (阻塞) |
| 统计指标 | `qsize`, `total_puts`, `total_drops`, `avg_latency_ms` |

---

## 9. 已删除功能 (从 v0.3 到 v0.5)

以下功能在 v0.5 重构中被删除，暂不实现：

| 功能 | 文件 | 原因 |
|------|------|------|
| **Pipeline 责任链** | `scope/processing/pipeline.py` | 复杂度高，维护成本大 |
| **FFT 频谱分析** | `scope/processing/fft.py` | 当前无需求 |
| **数字滤波** | `scope/processing/filters.py` | 当前无需求 |
| **数学运算** | `scope/processing/math_ops.py` | 当前无需求 |
| **协议解码** | `scope/processing/` (规划中) | 当前无需求 |
| **事件窗口模型** | `EventWindowSpec` | 合并到 `MeasurementSpec` |
| **复杂数据模型** | `AnalysisResult`, `ChannelData`, `TriggerInfo` | 简化为 `RawFrame` |

**目录清理**:
- ✅ 删除 `scope/processing/` 整个目录
- ✅ 删除 `scope/model/analysis_result.py`
- ✅ 删除 `scope/runtime/fit_worker.py`
- ✅ 删除 `scope/runtime/measurement_snapshot.py`

---

## 10. 性能指标 (v0.5)

| 指标 | 目标 | 实测 |
|------|------|------|
| 单帧测量延迟 | < 10ms | **< 5ms** |
| 采集线程阻塞 | 0ms | **0ms** (事件驱动) |
| UI 刷新率 | ≥ 2 Hz | **2 Hz** (帧周期 500ms) |
| 反馈延迟 | < 20ms | **< 10ms** |
| 内存占用 | < 500MB | **~200MB** |

**对比 v0.3**:
- ❌ Pipeline 累积延迟: 5-20ms
- ✅ 测量延迟: < 5ms (单一处理器)
- ✅ 简化架构: 删除 2000+ 行代码

---

## 11. 测试覆盖

| 测试文件 | 测试数 | 通过率 |
|----------|--------|--------|
| `test_phase0.py` | 16 | ✅ 100% |
| `test_pid_controller.py` | 11 | ✅ 100% |
| `test_feedback_worker.py` | 15 | ✅ 100% |
| `test_feedback_manager.py` | 16 | ✅ 100% |
| `test_art_device.py` | 18 | ✅ 100% (部分需要硬件) |
| **总计** | **76** | **✅ 100%** |

---

## 12. 反馈系统扩展方向 (v0.7+)

| 方向 | 优先级 | 说明 |
|------|--------|------|
| 更多测量特征 | 🟡 中 | Freq, Period, DutyCycle (需过零检测算法) |
| 触发源 UI 配置 | 🔴 高 | 当前硬编码为 ai12/1V/上升沿 |
| 单点/连续模式 | 🟡 中 | 当前仅 FINITE 模式 |
| 预设场景 | 🟢 低 | 保存/加载示波器配置 |
| 数据回放 | 🟢 低 | 加载 HDF5 文件模拟实时采集 |
| REST API | 🟢 低 | 远程查询状态/获取波形快照 |
