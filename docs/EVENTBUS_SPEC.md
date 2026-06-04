# EventBus 数据路由架构

> v0.4 重构核心 — 数据面/控制面分离，消除采集线程阻塞与双路径不一致

---

## 1. 设计目标

| 目标 | 说明 |
|------|------|
| **数据面/控制面分离** | 采集发出原始帧后不阻塞，所有计算/反馈/渲染解耦为独立 Worker |
| **单一数据源** | 所有消费者（测量面板、反馈、迷你图）从同一份 `FittedSnapshot` 读取，消除双路径不一致 |
| **有界背压** | 每个 topic 队列有界，下游慢时丢旧保新，不允许无界堆积 |
| **可观测性** | 每个队列暴露 `qsize / drop_count / latency_ms`，用于实时诊断 |
| **Worker 隔离** | 一个 Worker 崩溃不影响其他 Worker 和采集线程 |

---

## 2. 架构总览

```
  采集线程 (_on_frame)
      │  make_analysis_result(chunk) → AnalysisResult (channels + trigger, 无 measurements)
      │
      ▼
  EventBus.publish("frame.measured", AnalysisResult)
  ┌─────────────────┬────────────────────┬──────────────────┐
  ▼                  ▼                    ▼                  ▼
FitWorker         UIBridge            FeedbackWorker    ConfigWorker
(独立线程)          (采集线程内           (asyncio loop)    (asyncio loop)
                    qt信号桥接)
      │                  │                    │                  │
      │  订阅             │ 订阅                 │ 订阅              │ 订阅
      │ frame.measured    │ frame.measured       │ frame.fitted      │ config.change
      │                   │ + frame.fitted       │                   │
      ▼                   ▼                     ▼                  ▼
  ProcessingPipeline   Qt 主线程:            直接消费              调用
  + EventWindowSpec    波形/面板/迷你图      FittedSnapshot        _on_art_config()
      │                                      → dispatch
      ▼
  EventBus.publish("frame.fitted", FittedSnapshot)
```

### 2.1 Topic 定义

| Topic | Payload 类型 | maxsize | drop 策略 | 生产者 | 消费者 |
|-------|-------------|---------|-----------|--------|--------|
| `frame.measured` | `AnalysisResult`（channels + trigger，measurements 为空） | 2 | drop_oldest | `_on_frame()` | FitWorker, UIBridge |
| `frame.fitted` | `FittedSnapshot` | 2 | drop_oldest | FitWorker | UIBridge, FeedbackWorker |
| `config.change` | `ConfigChange`（`DeviceConfig` + params dict） | 8 | block | UI 面板 | ConfigWorker |

### 2.2 线程边界

```
┌─────────────────────────────────────────────────────────┐
│  采集线程（DONE 回调 / QTimer）                           │
│    _on_frame() → publish(frame.measured)                │
│    UIBridge.poll() → emit QSignal (非阻塞)               │
│                                                         │
├─────────────────────────────────────────────────────────┤
│  FitWorker 线程                                          │
│    while: queue.get() → Pipeline → publish(frame.fitted)│
│                                                         │
├─────────────────────────────────────────────────────────┤
│  asyncio 线程                                            │
│    FeedbackWorker: queue.get() → dispatch()              │
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

## 4. 数据类型

### 4.1 FittedSnapshot（新建）

```python
@dataclass
class FittedSnapshot:
    """FitWorker 产出 — 全部计算结果，不含原始波形。"""

    sequence_num: int

    # 通道级测量 (AutoMeasure / Pipeline 产出)
    #   {"CH1_Vpp": 3.3, "CH1_Freq": 1000.0, ...}
    channel_measurements: dict[str, float] = field(default_factory=dict)

    # 事件窗口测量 (EventWindowSpec 产出)
    #   {"A_power": 5.3, "B_power": 2.1, ...}
    event_measurements: dict[str, float] = field(default_factory=dict)

    # 元信息
    timestamp: float = field(default_factory=time.monotonic)
    pipeline_latency_ms: float = 0.0       # Pipeline + 窗口计算耗时

    def as_flat_dict(self) -> dict[str, float]:
        """合并所有测量值为扁平 dict（用于 MiniChart / 日志）。"""
        result = {}
        result.update(self.channel_measurements)
        result.update(self.event_measurements)
        return result
```

### 4.2 ConfigChange（新建）

```python
@dataclass
class ConfigChange:
    """硬件配置变更指令（走控制面）。"""
    device_config: DeviceConfig
    art_params: dict           # 当前 _art_params 全量
    change_id: int             # 单调递增，用于去重
    timestamp: float = field(default_factory=time.monotonic)
```

### 4.3 AnalysisResult（已有，在 publish 时 measurements 字段为空）

`frame.measured` 发布的是**原始** `AnalysisResult`，`measurements` / `fft` / `math_channels` 字段均为空（或未被填充），只有 `channels`（原始波形）和 `trigger`（元信息）。所有计算结果由 FitWorker 填充到 `FittedSnapshot`。

---

## 5. Worker 规范

### 5.1 通用约束

| 约束 | 说明 |
|------|------|
| `on_data()` 禁止阻塞 | 所有 Worker 的消费回调不得执行长时间阻塞 I/O |
| 固定线程池 | FeedbackWorker 的 rpyc 调用使用固定 `max_workers=4` 的 `ThreadPoolExecutor` |
| 异常隔离 | 单个 Worker 异常不传播，只打日志，不中断其他 Worker |
| 可停止 | 每个 Worker 实现 `stop()` 方法，设置停止标记，消费完当前帧后退出 |
| 可观测 | 每个 Worker 暴露 `metrics: dict`（处理帧数、延迟、错误数） |

### 5.2 FitWorker

```
运行位置：独立线程
输入：    frame.measured 队列 → AnalysisResult
处理：
  1. 调用 ProcessingPipeline.process(result) （AutoMeasure / MathOp / FFT）
  2. 执行 EventWindowSpec 列表（时间窗切片 + 特征计算）
  3. 组装 FittedSnapshot
输出：    publish("frame.fitted", FittedSnapshot)
```

**EventWindowSpec 计算**：
```python
@dataclass
class EventWindowSpec:
    tag: str              # 语义名，如 "A_power"
    channel: str          # "CH1"~"CH16"
    start_ms: float       # 窗口起始（相对帧起点）
    end_ms: float         # 窗口结束
    feature: str          # "Vpp" | "Vrms" | "Mean" | "Integral" | "Vmax" | "Vmin"
    semantic: str = ""

    def compute(self, channel_data: ChannelData) -> float:
        """从原始波形中切片并计算特征值。"""
        start_idx = int(self.start_ms / 1000 * channel_data.sample_rate)
        end_idx   = int(self.end_ms   / 1000 * channel_data.sample_rate)
        segment   = channel_data.raw[start_idx:end_idx]
        if self.feature == "Vpp":   return float(np.ptp(segment))
        if self.feature == "Vrms":  return float(np.sqrt(np.mean(np.square(segment))))
        if self.feature == "Mean":  return float(np.mean(segment))
        if self.feature == "Integral": return float(np.trapz(segment)) / channel_data.sample_rate
        ...
```

**订阅来源**：`MeasurementPanel` 中每行的配置 `(name, channel, meas_key, start_ms, end_ms)` 映射为一个 `EventWindowSpec`。Worker 启动时同步一次，运行中通过 `config.change` 更新。

### 5.3 UIBridge

```
运行位置：采集线程（无额外线程，非阻塞轮询）
输入：    frame.measured 队列 + frame.fitted 队列
处理：    get_nowait() 轮询，有数据则 emit QSignal
输出：    Qt Signal → UI 主线程
```

**信号定义**：
```python
class UIBridge(QObject):
    signal_raw_frame = pyqtSignal(object)   # AnalysisResult → WaveformView
    signal_fitted    = pyqtSignal(object)   # FittedSnapshot  → MeasurementPanel + MiniChart
```

**约束**：
- `poll()` 只做 `get_nowait()`，不得阻塞
- 一帧最多 emit 一次，不重复
- emit 时若队列中积压了旧帧，先丢弃再 emit 最新帧（`snapshot()` 方法）

### 5.4 FeedbackWorker

```
运行位置：asyncio 线程（扩展当前的 _async_worker）
输入：    frame.fitted 队列 → FittedSnapshot
处理：
  1. 从 snapshot 中提取测量值（结构化 key）
  2. 调用 feedback_mgr.dispatch(snapshot.as_flat_dict())
```

**关键变更**：
- 不再重建 `AnalysisResult`（删除当前 `_feedback_consumer` 中的 proxy 构建代码）
- `FeedbackManager.dispatch()` 接收 `dict[str, float]` 直接作为 payload 数据源
- `_resolve_value` 增加结构化 key 支持

### 5.5 ConfigWorker

```
运行位置：asyncio 线程（与 FeedbackWorker 共享）
输入：    config.change 队列 → ConfigChange
处理：    调用 ScopeApp._on_art_config(change.device_config, change.art_params)
```

---

## 6. 迁移步骤（ScopeApp._on_frame 改造前后对比）

### 改造前（当前代码 `scope/main.py`）

```python
def _on_frame(self, chunk):
    result = self.device.make_analysis_result(chunk)
    result = self._pipeline.process(result)            # ← 阻塞采集线程
    self.main_win.measure_panel.update_from_result(result)  # ← 非 UI 线程操作 Qt
    self.main_win.mini_chart.add_data(filtered)             # ← 同上
    self.main_win.data_received.emit(result)                # ← Qt Signal OK
    self._feedback_queue.put(snap)                          # ← 额外重建一次
```

### 改造后

```python
def _on_frame(self, chunk):
    result = self.device.make_analysis_result(chunk)   # 原始数据，无 measurements
    self._event_bus.publish("frame.measured", result)  # 唯一动作
    # ✓ 不再阻塞，不再直接操作 UI，不再重建数据
```

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
| `ScopeApp._pipeline` | 迁移到 FitWorker | `ScopeApp.__init__` 不再创建 Pipeline |
| `ScopeApp._feedback_queue` | 被 EventBus 替代 | 删除，统一走 `frame.fitted` |
| `_feedback_consumer` | 被 FeedbackWorker 替代 | 清理重建 AnalysisResult 的 hack |
| `data_received.emit` | 被 UIBridge 替代 | 移除 |
| `mini_chart._timer` | 由 UIBridge.signal_fitted 驱动 | QTimer 保留仅作为渲染节流（非数据驱动） |
| `SimulatorDevice` 的 QTimer 降级 | 不变 | `_on_frame()` 接收 `chunk` 的接口不变 |

---

## 9. 验收标准

| 验收项 | 方法 |
|--------|------|
| 采集线程不执行 Pipeline | `_on_frame()` 中无 `pipeline.process` 调用 |
| FitWorker 独立线程运行 | 日志中 `thread_name=fit-worker` |
| UI 更新由 UIBridge 信号驱动 | `measure_panel.update_from_result` 只在 Qt 主线程被调用 |
| 反馈消费 frame.fitted | `FeedbackWorker` 不引用 `AnalysisResult` |
| 队列背压生效 | 模拟下游慢时 `total_drops > 0` |
| 现有测试原样通过 | `pytest tests/` = 72 tests pass |
