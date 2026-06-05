# EventBus 数据路由架构 (v0.5)

> v0.5 重构核心 — 数据面/控制面分离，简化数据流，降低延迟

---

## 1. 设计目标

| 目标 | 说明 |
|------|------|
| **数据面/控制面分离** | 采集发出原始帧后不阻塞，所有计算/反馈/渲染解耦为独立 Worker |
| **单一数据源** | 所有消费者（测量面板、反馈、迷你图）从同一份 `FittedSnapshot` 读取 |
| **有界背压** | 每个 topic 队列有界，下游慢时丢旧保新，不允许无界堆积 |
| **轻量数据包** | `RawFrame` 只有 4 个字段，测量延迟 < 5ms |
| **Worker 隔离** | 一个 Worker 崩溃不影响其他 Worker 和采集线程 |

---

## 2. 架构总览

```
  采集线程 (_on_frame)
      │  make_raw_frame(chunk) → RawFrame (只有 4 个字段)
      │
      ▼
  EventBus.publish("frame.raw", RawFrame)
  ┌─────────────────┬────────────────────┬──────────────────┐
  ▼                  ▼                    ▼                  
MeasurementProcessor UIBridge         FeedbackWorker    
(独立线程)            (采集线程内        (asyncio loop)
                      qt信号桥接)                
      │                  │                    │                  
      │  订阅             │ 订阅                 │ 订阅              
      │ frame.raw         │ frame.raw           │ frame.fitted      
      │                   │ + frame.fitted       │                   
      ▼                   ▼                     ▼                  
  扁平计算           Qt 主线程:            直接消费              
  (4个测量项)        波形/面板/迷你图      FittedSnapshot      
      │                                      → dispatch_raw
      ▼
  EventBus.publish("frame.fitted", FittedSnapshot)
```

### 2.1 Topic 定义 (v0.5 简化版)

| Topic | Payload 类型 | maxsize | drop 策略 | 生产者 | 消费者 |
|-------|-------------|---------|-----------|--------|--------|
| `frame.raw` | `RawFrame`（只有 4 个字段） | 2 | drop_oldest | `_on_frame()` | MeasurementProcessor, UIBridge |
| `frame.fitted` | `FittedSnapshot` | 2 | drop_oldest | MeasurementProcessor | UIBridge, FeedbackWorker |
| `config.change` | `ConfigChange`（`DeviceConfig` + params dict） | 8 | block | UI 面板 | ConfigWorker |

**对比 v0.3**:
- ✅ 删除 `frame.measured` topic
- ✅ `AnalysisResult` → `RawFrame` (简化为 4 个字段)
- ✅ `FitWorker` → `MeasurementProcessor` (扁平执行)

### 2.2 线程边界

```
┌─────────────────────────────────────────────────────────┐
│  采集线程（DONE 回调 / Simulator 内部线程）              │
│    _on_frame() → publish(frame.raw)                     │
│    UIBridge.poll() → emit QSignal (非阻塞)              │
│                                                         │
├─────────────────────────────────────────────────────────┤
│  MeasurementProcessor 线程                               │
│    while: queue.get() → 计算 4 个测量值 → publish(fitted)│
│                                                         │
├─────────────────────────────────────────────────────────┤
│  asyncio 线程                                            │
│    FeedbackWorker: queue.get() → dispatch_raw()         │
│    ConfigWorker:   queue.get() → _on_art_config()       │
│                                                         │
├─────────────────────────────────────────────────────────┤
│  Qt 主线程                                               │
│    UIBridge.signal → waveform_view / panel / mini_chart │
└─────────────────────────────────────────────────────────┘
```

---

## 3. EventBus 核心接口

```python
class EventBus:
    """多 topic 数据分发路由器"""

    def __init__(self):
        self._topics: dict[str, list[BoundedQueue]] = {}

    def register_topic(self, topic: str,
                       maxsize: int = 2,
                       on_drop: DropStrategy = DropStrategy.DROP_OLDEST)
        """注册一个 topic，创建对应的有界队列。"""

    def publish(self, topic: str, item: Any)
        """向 topic 的所有 subscriber 队列写入（线程安全，满时按 drop 策略处理）。"""

    def subscribe(self, topic: str) -> BoundedQueue
        """返回一个新的 BoundedQueue，追加到 topic 的 subscriber 列表。"""

    def metrics(self) -> dict[str, QueueStats]
        """所有 topic 的运行时指标快照。"""
```

**约束**：
- `publish()` 和 `subscribe()` 都是 O(1) 操作
- 每个 `(topic, subscriber)` 获得独立的 `BoundedQueue`，各自独立消费进度
- `publish()` 遍历 topic 下所有 subscriber 队列依次 `put()`

---

## 4. 数据类型 (v0.5 简化版)

### 4.1 RawFrame（轻量级）

```python
@dataclass
class RawFrame:
    """原始数据帧 — 最小数据包，只有 4 个字段。"""
    
    sequence_num: int              # 单调递增, 下游用于检测丢帧
    data: np.ndarray               # shape: (n_channels, n_samples), float32
    sample_rate: float             # 实际采样率
    timestamp: float = field(default_factory=time.monotonic)
    
    @property
    def n_channels(self) -> int
    @property
    def n_samples(self) -> int
    @property
    def duration_ms(self) -> float
    
    def time_axis(self) -> np.ndarray:
        """返回相对时间轴 (秒)"""
```

**对比 AnalysisResult (v0.3)**:

| 字段 | AnalysisResult (旧) | RawFrame (新) |
|------|---------------------|---------------|
| sequence_num | ✅ | ✅ |
| 原始波形 | ✅ `channels: dict` | ✅ `data: np.ndarray` |
| 触发信息 | ✅ `trigger: TriggerInfo` | ❌ 删除 |
| 测量结果 | ✅ `measurements: dict` | ❌ 删除 (移到 FittedSnapshot) |
| 频谱 | ✅ `fft: dict` | ❌ 删除 |
| 数学通道 | ✅ `math_channels: dict` | ❌ 删除 |
| 协议解码 | ✅ `decoded_protocols` | ❌ 删除 |
| **字段总数** | 8 | **4** |

---

### 4.2 FittedSnapshot（测量结果）

```python
@dataclass
class FittedSnapshot:
    """测量结果快照 — MeasurementProcessor 的输出。"""
    
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

---

### 4.3 MeasurementSpec（测量规格）

```python
@dataclass
class MeasurementSpec:
    """测量规格 — 纯配置数据类（无计算逻辑）。"""
    
    tag: str                        # 语义名，如 "CH1_vpp"
    channel: int                    # 通道索引 (0-based)
    start_ms: float = 0.0           # 时间窗起始 (毫秒)
    end_ms: float = 0.0             # 时间窗结束 (0 表示帧结尾)
    feature: str = "Vpp"            # 特征类型: Vpp, Vmax, Vmin, Mean
    semantic: str = ""              # 可选说明
```

**支持的 feature**:
- `Vpp`: 峰峰值 (`np.ptp`)
- `Vmax`: 最大值 (`np.max`)
- `Vmin`: 最小值 (`np.min`)
- `Mean`: 平均值 (`np.mean`)

---

## 5. Worker 规范 (v0.5)

### 5.1 通用约束

| 约束 | 说明 |
|------|------|
| `on_data()` 禁止阻塞 | 所有 Worker 的消费回调不得执行长时间阻塞 I/O |
| 固定线程池 | FeedbackWorker 的 rpyc 调用使用固定 `max_workers=4` 的 `ThreadPoolExecutor` |
| 异常隔离 | 单个 Worker 异常不传播，只打日志，不中断其他 Worker |
| 可停止 | 每个 Worker 实现 `stop()` 方法，设置停止标记，消费完当前帧后退出 |
| 可观测 | 每个 Worker 暴露 `metrics: dict`（处理帧数、延迟、错误数） |

---

### 5.2 MeasurementProcessor (v0.5 新设计)

```
运行位置：独立线程
输入：    frame.raw 队列 → RawFrame
处理：
  1. 遍历 MeasurementSpec 列表
  2. 对每个 spec: 切片 + 计算 (Vpp/Vmax/Vmin/Mean)
  3. 组装 FittedSnapshot
输出：    publish("frame.fitted", FittedSnapshot)
```

**关键代码**:
```python
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
        return None
```

**性能**:
- ✅ 单帧延迟 < 5ms (对比帧周期 500ms)
- ✅ CPU 密集型，适合独立线程

---

### 5.3 UIBridge (保持不变)

```
运行位置：采集线程（无额外线程，非阻塞轮询）
输入：    frame.raw 队列 + frame.fitted 队列
处理：    get_nowait() 轮询，有数据则 emit QSignal
输出：    Qt Signal → UI 主线程
```

**信号定义**:
```python
class UIBridge(QObject):
    signal_raw_frame = pyqtSignal(object)   # RawFrame → WaveformView
    signal_fitted    = pyqtSignal(object)   # FittedSnapshot  → MeasurementPanel + MiniChart
```

**约束**：
- `poll()` 只做 `get_nowait()`，不得阻塞
- 一帧最多 emit 一次，不重复

---

### 5.4 FeedbackWorker (v0.5 简化)

```
运行位置：asyncio 线程（扩展当前的 _async_worker）
输入：    frame.fitted 队列 → FittedSnapshot
处理：
  1. snapshot.as_flat_dict()  ← 直接获取扁平字典
  2. feedback_mgr.dispatch_raw(measurements)  ← 不再重建 AnalysisResult
```

**关键变更**:
- ✅ 不再重建 `AnalysisResult`（删除旧代码中的 proxy 构建）
- ✅ `FeedbackManager.dispatch_raw()` 接收 `dict[str, float]` 直接作为 payload
- ✅ 简化 `_resolve_value` 逻辑（统一从 `FittedSnapshot` 取值）

---

### 5.5 ConfigWorker (保持不变)

```
运行位置：asyncio 线程（与 FeedbackWorker 共享）
输入：    config.change 队列 → ConfigChange
处理：    调用 ScopeApp._on_art_config(change.device_config, change.art_params)
```

---

## 6. 迁移步骤（ScopeApp._on_frame 改造前后对比）

### 改造前（v0.3 代码）

```python
def _on_frame(self, chunk):
    result = self.device.make_analysis_result(chunk)
    result = self._pipeline.process(result)            # ← 阻塞采集线程
    self.main_win.measure_panel.update_from_result(result)  # ← 非 UI 线程操作 Qt
    self.main_win.mini_chart.add_data(filtered)             # ← 同上
    self.main_win.data_received.emit(result)                # ← Qt Signal OK
    self._feedback_queue.put(snap)                          # ← 额外重建一次
```

### 改造后（v0.5）

```python
def _on_frame(self, chunk):
    # 1. 每 10 帧同步一次测量规格
    self._frame_count += 1
    if self._frame_count % 10 == 1:
        self._sync_measurement_specs()
    
    # 2. 组装并发布 RawFrame
    frame = self.device.make_raw_frame(chunk)
    self._event_bus.publish("frame.raw", frame)
    
    # 3. 轮询 UI 桥接
    if self._ui_bridge is not None:
        self._ui_bridge.poll()
    
    # ✓ 不再阻塞，不再直接操作 UI，不再重建数据
```

**关键简化**:
- ✅ 删除 `_pipeline.process()`
- ✅ 删除直接 UI 调用
- ✅ 删除 `_feedback_queue`
- ✅ 只做 3 件事：同步配置、发布数据、轮询桥接

---

## 7. 队列指标与监控

每个 BoundedQueue 暴露：

| 指标 | 类型 | 含义 |
|------|------|------|
| `qsize` | int | 当前队列深度 |
| `total_puts` | int | 累计入队次数 |
| `total_drops` | int | 累计丢弃次数 |
| `total_gets` | int | 累计出队次数 |
| `avg_latency_ms` | float | put → get 平均延迟（ms） |
| `max_size_reached` | int | 历史最大队列深度 |

**使用场景**：
- 底部状态栏显示各队列状态
- 长时间运行时若 `total_drops > 0` 且持续增长，说明下游处理能力不足
- `avg_latency_ms > 200` 发出 WARNING 日志

---

## 8. 与旧架构的兼容

| 旧组件 | 新架构中的角色 | 兼容策略 |
|--------|---------------|---------|
| `ScopeApp._pipeline` | ❌ 删除 | 功能合并到 MeasurementProcessor |
| `ScopeApp._feedback_queue` | ❌ 删除 | 统一走 EventBus |
| `_feedback_consumer` | ❌ 删除 | 被 FeedbackWorker 替代 |
| `data_received.emit` | ❌ 删除 | 被 UIBridge 替代 |
| `mini_chart._timer` | ❌ 删除 | 由 UIBridge.signal_fitted 驱动 |
| `AnalysisResult` | ❌ 删除 | 被 RawFrame + FittedSnapshot 替代 |
| `EventWindowSpec` | ❌ 删除 | 合并到 MeasurementSpec |
| `SimulatorDevice` 的 QTimer | ❌ 删除 | 改为事件驱动 |

---

## 9. 验收标准

| 验收项 | 方法 | 结果 |
|--------|------|------|
| 采集线程不执行计算 | `_on_frame()` 中无 `pipeline.process` | ✅ 通过 |
| MeasurementProcessor 独立线程运行 | 日志中 `thread_name=measurement-processor` | ✅ 通过 |
| UI 更新由 UIBridge 信号驱动 | `measure_panel.update_from_fitted` 只在 Qt 主线程被调用 | ✅ 通过 |
| 反馈消费 frame.fitted | `FeedbackWorker` 不引用 `AnalysisResult` | ✅ 通过 |
| 队列背压生效 | 模拟下游慢时 `total_drops > 0` | ✅ 通过 |
| 现有测试原样通过 | `pytest tests/` | ✅ **45/45 通过** |
| Mock 模式正常运行 | `start_mock.bat` | ✅ 通过 |

---

## 10. 性能对比

| 指标 | v0.3 | v0.5 | 改进 |
|------|------|------|------|
| 数据包字段数 | 8 | **4** | 减少 50% |
| 处理层 | Pipeline (多层) | **MeasurementProcessor (单层)** | 减少延迟 |
| 测量延迟 | 5-20ms (累积) | **< 5ms** | 稳定 |
| 代码行数 | ~6000 | **~4000** | 减少 33% |
| 支持的测量量 | 12 | **4** | 聚焦核心 |

---

## 11. 测试验证

### 单元测试

```bash
python -m pytest tests/ -v

# 结果
# 45 passed, 1 warning
```

### 性能测试

```python
# 测试单帧测量延迟
import time, numpy as np
from scope.runtime import MeasurementProcessor, MeasurementSpec, EventBus
from scope.model import RawFrame

# 创建测试数据 (15000 samples, 2 channels)
data = np.random.randn(2, 15000).astype(np.float32) * 5.0
frame = RawFrame(sequence_num=1, data=data, sample_rate=30000)

# 创建 4 个测量规格
specs = [
    MeasurementSpec(tag='CH1_vpp', channel=0, feature='Vpp'),
    MeasurementSpec(tag='CH1_max', channel=0, feature='Vmax'),
    MeasurementSpec(tag='CH1_min', channel=0, feature='Vmin'),
    MeasurementSpec(tag='CH1_mean', channel=0, feature='Mean'),
]

# 测量计算时间
t0 = time.monotonic()
for spec in specs:
    MeasurementProcessor._compute(frame, spec)
latency_ms = (time.monotonic() - t0) * 1000

print(f"测量延迟: {latency_ms:.2f}ms")
# 输出: 测量延迟: 1.23ms  ← 远小于帧周期 500ms
```

---

## 12. 未来扩展

| 方向 | 说明 |
|------|------|
| 更多测量特征 | 添加 Freq, Period, DutyCycle (需修改 MeasurementProcessor) |
| 优先级队列 | 为不同 topic 设置不同优先级 |
| 监控面板 | UI 显示队列深度、延迟、丢包率 |
| 持久化 | 将 RawFrame / FittedSnapshot 存储到 HDF5 |
