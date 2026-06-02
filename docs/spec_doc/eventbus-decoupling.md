# EventBus 解耦改造规格说明书

> 状态: Draft | 分支: freq_lock_with_stm32 | 版本: v0.5

---

## 1. 动机与目标

### 1.1 现状问题

当前 `_on_frame` 在采集线程内串行执行全部后续处理：

```
_on_frame (采集线程)
  ├─ make_analysis_result()              ← 同步
  ├─ pipeline.process()                  ← 同步
  ├─ measure_panel.compute_event...()    ← 采集线程调 UI 对象
  ├─ fit_lorentzian()                    ← 同步, scipy curve_fit 可耗时
  ├─ UI signal emit                      ← fire-forget ✅
  └─ feedback_queue.put() + enabled 判断 ← 采集线程感知反馈开关
```

问题：
1. **拟合阻塞采集**：慢拟合延迟下一帧回调
2. **采集线程耦合 UI**：`compute_event_measurements()` 在采集线程调用 UI 面板方法
3. **职责泄漏**：采集线程需感知 `feedback_enabled` 开关
4. **不可独立扩展**：新增处理环节必须修改 `_on_frame`，违反 OCP

### 1.2 目标

测量、拟合、反馈、参数设置在执行上相互解耦，各自抛出任务，独立运行。

---

## 2. 数据流设计

```
_on_frame (采集线程, 最小工作量)
  │  make_analysis_result + pipeline.process
  │  构建 MeasurementSnapshot (含 ch0_raw 引用 + 测量值)
  ▼
EventBus.publish("frame.measured")

FitWorker (独立线程)
  │  subscribe "frame.measured"
  │  ScanCoordinator.snapshot() → 取扫频参数
  │  map_to_frequency_domain + fit_lorentzian
  │  构建 FittedSnapshot (含 fit_result, ch0_raw 置 None 释放)
  ▼
EventBus.publish("frame.fitted")

FeedbackWorker (async worker)
  │  subscribe "frame.fitted"
  │  检查自身 enabled 开关
  │  从 FittedSnapshot 取 f0 → PID step → RPC
  ▼
(完成)

UIBridge (独立线程 → Qt signal 桥接)
  │  subscribe "frame.measured" → 主波形视图 (大示波器)
  │  subscribe "frame.fitted"  → 扫频面板 + 迷你趋势图 (小示波器)
  ▼
Qt signal → 主线程刷新
```

### 2.1 Topic 定义

| Topic | 生产者 | 消费者 | 数据类型 | 语义 |
|-------|--------|--------|----------|------|
| `frame.measured` | 采集线程 | FitWorker, UIBridge | `MeasurementSnapshot` | 原始采集 + 测量值完成 |
| `frame.fitted` | FitWorker | FeedbackWorker, UIBridge | `FittedSnapshot` | 拟合结果已产出 |
| `config.change` | DevicePanel | ConfigWorker (后续) | dict | 设备参数变更 |

### 2.2 时序保证

- `frame.fitted` 严格在 `frame.measured` 之后，同一帧数据
- FeedbackWorker 订阅 `frame.fitted`，保证反馈基于同一帧的拟合结果
- UIBridge 双订阅：`frame.measured` 保证波形实时性（不等拟合），`frame.fitted` 保证拟合结果和趋势图更新

---

## 3. 核心组件规格

### 3.1 EventBus

**文件**: `scope/runtime/event_bus.py`（在现有 BoundedQueue 同文件新增）

```python
class EventBus:
    """
    发布-订阅事件总线。

    - 每个 subscriber 独立持有 BoundedQueue，背压隔离
    - publish 遍历 put，一个 subscriber 慢不影响其他
    - 线程安全
    """

    def __init__(self):
        self._subs: dict[str, list[BoundedQueue]] = {}
        self._lock = threading.Lock()

    def subscribe(
        self,
        topic: str,
        maxsize: int = 2,
        on_drop: DropStrategy = DropStrategy.DROP_OLDEST,
        name: str = "",
    ) -> BoundedQueue:
        """
        订阅 topic，返回 subscriber 专用的 BoundedQueue。

        subscriber 在自己的线程中从此 queue 消费。
        """

    def publish(self, topic: str, item: Any) -> None:
        """
        向 topic 所有 subscriber 的 queue 发布数据。

        遍历 put，每个 queue 独立按自身背压策略处理。
        若 subscriber 的 queue 已满且策略为 DROP_OLDEST，自动丢弃旧数据。
        """

    def unsubscribe(self, topic: str, queue: BoundedQueue) -> None:
        """取消订阅，移除对应 queue。"""

    def topic_stats(self, topic: str) -> list[dict]:
        """返回 topic 下所有 subscriber 的运行指标 (调试用)。"""
```

**设计约束**：
- publish 不阻塞：所有 subscriber 使用 `DROP_OLDEST` 策略（默认），避免 publish 被卡
- subscriber 各自消费：FitWorker、FeedbackWorker、UIBridge 在各自线程取数据
- 同一 topic 可被多个 subscriber 订阅，数据广播

### 3.2 MeasurementSnapshot 扩展

**文件**: `scope/runtime/measurement_snapshot.py`

#### 3.2.1 MeasurementSnapshot 新增字段

```python
@dataclass
class MeasurementSnapshot:
    sequence_num: int = 0
    raw_measurements: dict[str, float] = field(default_factory=dict)
    event_measurements: dict[str, float] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.monotonic)

    # v0.5 新增: 用于拟合的原始波形引用
    ch0_raw: np.ndarray | None = None
    ch0_time_axis: np.ndarray | None = None
```

- `ch0_raw` / `ch0_time_axis` 传引用，不复制 numpy 数组
- 拟合完成后由 FitWorker 将原始 snapshot 的 `ch0_raw` 置 None，释放引用

#### 3.2.2 FittedSnapshot

```python
@dataclass
class FittedSnapshot(MeasurementSnapshot):
    """帧测量 + 拟合结果。由 FitWorker 构建并发布到 frame.fitted。"""

    fit_result: ScanFitResult | None = None

    @classmethod
    def from_snapshot(
        cls,
        snap: MeasurementSnapshot,
        fit_result: ScanFitResult | None = None,
    ) -> FittedSnapshot:
        """从 MeasurementSnapshot 构建，继承全部字段。"""
        return cls(
            sequence_num=snap.sequence_num,
            raw_measurements=snap.raw_measurements,
            event_measurements=snap.event_measurements,
            timestamp=snap.timestamp,
            ch0_raw=None,          # 拟合完成，释放
            ch0_time_axis=None,
            fit_result=fit_result,
        )

    @property
    def f0(self) -> float | None:
        return self.fit_result.f0 if self.fit_result else None

    @property
    def gamma(self) -> float | None:
        return self.fit_result.gamma if self.fit_result else None

    @property
    def r_squared(self) -> float | None:
        return self.fit_result.r_squared if self.fit_result else None
```

### 3.3 Workers

**文件**: `scope/runtime/workers.py`（新建）

#### 3.3.1 FitWorker

```python
class FitWorker:
    """
    拟合工作线程。

    订阅 frame.measured，执行 V(f) 映射 + Lorentzian 拟合，
    发布 FittedSnapshot 到 frame.fitted。
    """

    def __init__(self, bus: EventBus, scan_coordinator: ScanCoordinator):
        self._bus = bus
        self._sc = scan_coordinator
        self._q: BoundedQueue = bus.subscribe(
            "frame.measured", maxsize=2,
            on_drop=DropStrategy.DROP_OLDEST, name="fit",
        )
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self): ...
    def stop(self): ...

    def _run(self):
        """循环: queue.get → fit → publish frame.fitted"""
        while self._running:
            snap = self._q.get(timeout=5.0)
            if snap is None:
                continue
            fit_result = self._do_fit(snap)
            fitted = FittedSnapshot.from_snapshot(snap, fit_result=fit_result)
            snap.ch0_raw = None  # 释放原始数据引用
            self._bus.publish("frame.fitted", fitted)

    def _do_fit(self, snap: MeasurementSnapshot) -> ScanFitResult | None:
        """
        从 snapshot 提取 ch0_raw，结合 ScanCoordinator 参数执行拟合。
        无有效数据时返回 None。
        """
        cfg = self._sc.snapshot()
        if snap.ch0_raw is None or len(snap.ch0_raw) <= 2:
            return None
        f_axis, v_f = map_to_frequency_domain(
            snap.ch0_raw, snap.ch0_time_axis,
            cfg.base_freq, cfg.scan_freq_amp, cfg.scan_dur,
        )
        return fit_lorentzian(f_axis, v_f)
```

**队列参数**：maxsize=2, DROP_OLDEST
- 扫频 1-2s + 处理 0.5s 间隔，队列深度 2 足够
- 若拟合慢导致积压，自动丢弃旧帧，保证实时性

#### 3.3.2 FeedbackWorker

```python
class FeedbackWorker:
    """
    反馈工作线程。

    订阅 frame.fitted，检查自身 enabled 开关，
    基于 fit_result.f0 执行 PID step → RPC 发送。
    """

    def __init__(self, bus: EventBus, feedback_manager: FeedbackManager):
        self._bus = bus
        self._mgr = feedback_manager
        self._q: BoundedQueue = bus.subscribe(
            "frame.fitted", maxsize=2,
            on_drop=DropStrategy.DROP_OLDEST, name="feedback",
        )
        self._enabled: bool = False           # 反馈开关（自身持有）
        self._async_loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._running = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool):
        """由 ScanPanel 的反馈开关直接设置，线程安全。"""
        self._enabled = value

    def start(self):
        """启动 async 工作线程。"""
        self._thread = threading.Thread(
            target=self._async_worker, daemon=True, name="feedback-worker",
        )
        self._thread.start()

    def _async_worker(self):
        """独立 asyncio loop：消费队列 → dispatch。"""
        self._async_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._async_loop)
        self._async_loop.run_until_complete(self._consume_loop())

    async def _consume_loop(self):
        while self._running:
            snap = await asyncio.to_thread(self._q.get, timeout=5.0)
            if snap is None or not self._enabled:
                continue
            await self._mgr.dispatch(snap)

    def stop(self): ...
```

**关键变更**：
- `enabled` 开关由 FeedbackWorker 自身持有，采集线程不再判断
- 订阅 `frame.fitted` 而非 `frame.measured`，反馈基于拟合后的 f0
- 实际反馈频率约 3-5 次采集一次，队列压力极低

#### 3.3.3 UIBridge

```python
class UIBridge:
    """
    Qt 信号桥接。

    订阅 frame.measured → 主波形视图 (大示波器)
    订阅 frame.fitted  → 扫频面板 + 迷你趋势图 (小示波器)
    """

    def __init__(
        self,
        bus: EventBus,
        data_received_signal: pyqtSignal,
        scan_panel_signal: pyqtSignal,
        mini_chart_widget: MiniChartWidget,
    ):
        self._bus = bus
        self._data_sig = data_received_signal
        self._scan_sig = scan_panel_signal
        self._mini_chart = mini_chart_widget

        # 双订阅
        self._q_measured = bus.subscribe(
            "frame.measured", maxsize=4,
            on_drop=DropStrategy.DROP_NEWEST, name="ui-waveform",
        )
        self._q_fitted = bus.subscribe(
            "frame.fitted", maxsize=4,
            on_drop=DropStrategy.DROP_NEWEST, name="ui-trend",
        )
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self): ...
    def stop(self): ...

    def _run(self):
        """轮询两个队列，emit Qt signal。"""
        while self._running:
            # 优先处理 measured（波形实时性优先）
            snap = self._q_measured.get_nowait()
            if snap is not None:
                self._emit_measured(snap)

            fitted = self._q_fitted.get_nowait()
            if fitted is not None:
                self._emit_fitted(fitted)

            time.sleep(0.016)  # ~60fps

    def _emit_measured(self, snap: MeasurementSnapshot):
        """从 snapshot 重构 AnalysisResult，emit 到主波形。"""
        # 构建 AnalysisResult → self._data_sig.emit(result)

    def _emit_fitted(self, fitted: FittedSnapshot):
        """
        从 FittedSnapshot:
        - fit_result → self._scan_sig.emit(fit_result)    (扫频面板)
        - f0, gamma  → mini_chart.add_batch(...)           (迷你趋势图)
        """
```

**双订阅策略差异**：
- `frame.measured` 队列：`DROP_NEWEST`（波形宁可跳帧也不能展示旧数据）
- `frame.fitted` 队列：`DROP_NEWEST`（趋势图同理，跳帧优于延迟）

---

## 4. ScopeApp 改造规格

### 4.1 __init__ 变更

```python
class ScopeApp:
    def __init__(self, mock: bool = False):
        # ... 现有初始化 ...

        # v0.5: EventBus
        from scope.runtime import EventBus
        self._bus = EventBus()

        # v0.5: Workers (延迟 start，在 start() 中启动)
        from scope.runtime.workers import FitWorker, FeedbackWorker, UIBridge
        self._fit_worker = FitWorker(self._bus, self.scan_coordinator)
        self._feedback_worker = FeedbackWorker(self._bus, self.feedback_mgr)
        # UIBridge 需在 main_win 创建后初始化
        self._ui_bridge: UIBridge | None = None

        # 删除旧 _feedback_queue, _feedback_ready
```

### 4.2 start() 变更

```python
def start(self):
    # ... 现有 main_win 创建 ...

    # v0.5: 创建 UIBridge (依赖 main_win)
    self._ui_bridge = UIBridge(
        bus=self._bus,
        data_received_signal=self.main_win.data_received,
        scan_panel_signal=self.main_win.scan_panel_update,
        mini_chart_widget=self.main_win.mini_chart,
    )

    # 启动 workers
    self._fit_worker.start()
    self._feedback_worker.start()
    self._ui_bridge.start()

    # ... 设备回调注册 ...
```

### 4.3 _on_frame 瘦身

```python
def _on_frame(self, chunk: np.ndarray):
    """
    采集线程回调 — 最小工作量。
    只做采集 + 测量，发布到 EventBus。
    """
    try:
        result = self.device.make_analysis_result(chunk)
        result = self._pipeline.process(result)

        ch0 = result.channels.get("CH0")
        snap = MeasurementSnapshot(
            sequence_num=result.sequence_num,
            raw_measurements=dict(result.measurements),
            ch0_raw=ch0.raw if ch0 else None,
            ch0_time_axis=ch0.time_axis if ch0 else None,
        )
        self._bus.publish("frame.measured", snap)

    except Exception as e:
        logger.error(f"数据处理错误: {e}", exc_info=True)
```

**删除的代码**：
- `event_measurements` 计算（移入 UIBridge._emit_fitted 或 FitWorker）
- `fit_lorentzian` 调用（移入 FitWorker）
- `self.main_win.data_received.emit`（移入 UIBridge）
- `self.main_win.scan_panel_update.emit`（移入 UIBridge）
- `feedback_enabled` 判断 + `_feedback_queue.put`（移入 FeedbackWorker）
- 旧 `_feedback_queue`, `_feedback_ready`, `_async_worker`, `_feedback_consumer`（全部删除）

### 4.4 FeedbackWorker 外部开关接入

ScanPanel 的 "启用反馈链路" checkbox 需连接到 FeedbackWorker.enabled：

```python
# ScanPanel 中:
self._feedback_checkbox.toggled.connect(
    scope_app._feedback_worker.enabled.fset  # 或通过 signal 桥接
)
```

不再通过 ScanCoordinator.feedback_enabled 间接控制。

---

## 5. 文件改动清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `scope/runtime/event_bus.py` | **改** | 保留 BoundedQueue，新增 EventBus 类 |
| `scope/runtime/measurement_snapshot.py` | **改** | 新增 ch0_raw/ch0_time_axis 字段 + FittedSnapshot 子类 |
| `scope/runtime/__init__.py` | **改** | 导出 EventBus, FittedSnapshot |
| `scope/runtime/workers.py` | **新建** | FitWorker, FeedbackWorker, UIBridge |
| `scope/main.py` | **改** | _on_frame 瘦身 + ScopeApp 持有 bus/workers + 删除旧反馈队列 |

**不动的文件**：
- `scope/io/feedback_manager.py` — dispatch 逻辑不变
- `scope/io/feedback_slots/` — 所有 slot 实现不变
- `scope/scan/analysis.py` — 拟合算法不变
- `scope/ui/` — UI 层不变，仍通过 Qt signal 接收数据

---

## 6. 执行步骤

| 步骤 | 内容 | 验证方式 |
|------|------|----------|
| Step 1 | event_bus.py 新增 EventBus | 单元测试：pub/sub 基本流程 |
| Step 2 | measurement_snapshot.py 扩展 | 单元测试：FittedSnapshot 构建与属性 |
| Step 3 | workers.py 新建三个 Worker | mock 模式跑通完整数据流 |
| Step 4 | main.py _on_frame 瘦身 + 接入 bus | mock 模式端到端验证 |

每个 Step 完成后可单独验证，Step 4 是切换点。

---

## 7. 背压与性能预估

| Consumer | 队列深度 | 背压策略 | 预期负载 |
|----------|---------|----------|---------|
| FitWorker | 2 | DROP_OLDEST | 每帧 1 次，拟合耗时 < 50ms |
| FeedbackWorker | 2 | DROP_OLDEST | 每 3-5 帧反馈 1 次，极低负载 |
| UIBridge (waveform) | 4 | DROP_NEWEST | 波形实时优先，跳帧可接受 |
| UIBridge (trend) | 4 | DROP_NEWEST | 趋势图实时优先 |

扫描周期 1-2s + 停止 0.5s 处理，队列满几乎不会发生。DROP 策略作为安全网而非常态路径。

---

## 8. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| MeasurementSnapshot 含 ch0_raw 引用，体积增大 | 1000 点 numpy 引用，内存压力极小 | 拟合完释放，用后即弃 |
| EventBus publish 同步遍历 put | 若某 subscriber 使用 BLOCK 策略会卡住 publish | 默认全部 DROP_OLDEST / DROP_NEWEST，禁止 BLOCK |
| UIBridge 需从 MeasurementSnapshot 重建 AnalysisResult | 需额外转换逻辑 | 可考虑让 emit 直接传 snapshot，UI 层适配 |
| FeedbackWorker.enabled 由外部设置 | 需确保线程安全 | 使用 Python bool 原子读写，或加 Lock |
