# 实施清单 — EventBus + 数据模型重构 (v0.5)

> 状态: ✅ **全部完成** (2026/6/5)  
> 测试结果: **45/45 通过**
>
> **归档说明**: 本文是 v0.5 重构验收记录，不代表当前测试基线。当前文档入口见 [README.md](./README.md)，当前测试基线为 `85 passed`。

---

## Phase 1 — EventBus 基础设施 + 新数据类型 ✅

### 1.1 重构 event_bus.py

- [x] **EventBus 类**：`dict[topic, list[BoundedQueue]]`，提供 `register_topic()` / `publish()` / `subscribe()` 接口
  - `publish(topic)` 遍历所有 subscriber 队列依次 `put()`
  - `subscribe(topic)` → 返回一个新的 `BoundedQueue`（各 subscriber 独立消费进度）
  - 验证：✅ 单测验证 1 producer → 2 subscribers 各自独立消费，丢弃互不影响
- [x] **QueueStats 扩展**：保持现有字段 (qsize, total_puts, total_drops, avg_latency_ms)
- [x] **导出**：在 `scope/runtime/__init__.py` 中导出 `EventBus`

### 1.2 RawFrame (轻量数据模型)

- [x] 创建 `scope/model/__init__.py`
  - `RawFrame` data class（只有 4 个字段: sequence_num, data, sample_rate, timestamp）
  - 删除 `AnalysisResult`, `ChannelData`, `TriggerInfo`
- [x] 在 `scope/model/__init__.py` 中导出

### 1.3 MeasurementSpec (纯配置)

- [x] 创建 `scope/runtime/measurement_spec.py`
  - `MeasurementSpec` data class（tag, channel, start_ms, end_ms, feature）
  - 删除 `EventWindowSpec`
- [x] 在 `scope/runtime/__init__.py` 中导出

### 1.4 FittedSnapshot (测量结果)

- [x] 简化 `scope/runtime/fitted_snapshot.py`
  - 删除 `channel_measurements` 字段
  - 只保留 `event_measurements: dict[str, float]`
- [x] 在 `scope/runtime/__init__.py` 中导出

### 1.5 ScopeApp 集成 EventBus

- [x] `ScopeApp.__init__()` 中创建 EventBus 实例
- [x] 注册三个 topic：
  - `frame.raw` (maxsize=2, drop_oldest)
  - `frame.fitted` (maxsize=2, drop_oldest)
  - `config.change` (maxsize=8, block)

### 🔬 Phase 1 验证

```
[x] pytest tests/ 仍全部通过 (45/45)
[x] 单独测试 EventBus: 1pub-2sub 独立消费 ✅
[x] 单独测试 RawFrame: 创建和属性访问 ✅
[x] 单独测试 FittedSnapshot: as_flat_dict 合并正确 ✅
```

---

## Phase 2 — MeasurementProcessor (扁平执行) ✅

### 2.1 创建 MeasurementProcessor

- [x] `scope/runtime/measurement_processor.py` — `MeasurementProcessor` 类
  - 构造函数接收 `EventBus` + `specs: list[MeasurementSpec]`
  - `run()` 方法：循环 `subscribe("frame.raw")` → `get_nowait()` → 计算 → `publish("frame.fitted")`
  - `stop()` 方法：设置停止标记，消费完当前帧后退出
  - 线程名：`"measurement-processor"`
  - `set_specs()` 方法：运行时更新测量规格（线程安全）

### 2.2 删除 Pipeline

- [x] 删除 `scope/processing/` 整个目录
  - 删除 `pipeline.py`, `fft.py`, `filters.py`, `math_ops.py`, `measurements.py`
- [x] 从 `ScopeApp.__init__` 中移除 `self._pipeline` 的创建
- [x] 从 `scope/main.py` 中移除 Pipeline 相关导入

### 2.3 实现 MeasurementSpec 计算

- [x] `MeasurementProcessor._compute(frame: RawFrame, spec: MeasurementSpec) → float`
  - 从 RawFrame 中切片 (start_idx:end_idx)
  - 支持 4 个 feature: Vpp, Vmax, Vmin, Mean
  - 删除 Vrms, Integral, Freq, Period, DutyCycle 等

### 2.4 简化 _on_frame()

- [x] 移除 `self._pipeline.process(result)` 调用
- [x] 移除 `self._feedback_queue.put(snap)` 和相关的 `MeasurementSnapshot` 导入
- [x] 只保留：
  - `make_raw_frame(chunk)` → `publish("frame.raw", RawFrame)`
  - 每 10 帧调用 `_sync_measurement_specs()`
  - 轮询 `ui_bridge.poll()`

### 🔬 Phase 2 验证

```
[x] _on_frame() 中无 pipeline.process 调用 ✅
[x] MeasurementProcessor 线程启动，日志中可见 thread_name=measurement-processor ✅
[x] FittedSnapshot 的 measurements 数量 = MeasurementSpec 数量 ✅
[x] MeasurementSpec 切片计算边界正确（start_ms=0, end_ms=0 全帧） ✅
[x] pytest tests/ 仍全部通过 (45/45) ✅
```

---

## Phase 3 — UIBridge (统一 Qt 桥接) ✅

### 3.1 创建 UIBridge

- [x] `scope/ui/ui_bridge.py` — `UIBridge(QObject)` 类
  - `signal_raw_frame = pyqtSignal(object)` → RawFrame
  - `signal_fitted = pyqtSignal(object)` → FittedSnapshot
  - `poll()` 方法：非阻塞轮询两个队列，有数据则 emit
  - 线程安全：emit 时 Qt 自动将信号排入主线程

### 3.2 连接信号到 UI

- [x] `MainWindow.__init__` 中连接信号：
  - `signal_raw_frame.connect(self._on_ui_raw_frame)` → `waveform_view.update_waveform()`
  - `signal_fitted.connect(self._on_ui_fitted)` → `measure_panel.update_from_fitted(fitted)` + `mini_chart.add_data(flat)` + `refresh_now()`
- [x] 实现 `_on_ui_raw_frame` 和 `_on_ui_fitted` 槽函数

### 3.3 清除旧直接 UI 调用

- [x] 从 `_on_frame()` 中移除：
  - `self.main_win.measure_panel.update_from_result(result)`
  - `self.main_win.mini_chart.add_data(filtered)`
  - `self.main_win.data_received.emit(result)`

### 3.4 MiniChart 触发驱动渲染

- [x] 在 `_on_ui_fitted()` 中调用 `mini_chart.refresh_now()` 立即刷新
- [x] 移除 `MiniChartWidget` 中独立的 QTimer 数据驱动逻辑
- [x] MiniChart 完全由 `signal_fitted` 驱动

### 🔬 Phase 3 验证

```
[x] 采集线程中无直接 UI 调用（无 Qt 线程警告） ✅
[x] 主波形、测量面板、迷你图均正常更新 ✅
[x] 打开/关闭 MiniChart 不影响采集帧率 ✅
[x] 长时间运行无内存泄漏（曲线对象复用 setData） ✅
[x] pytest tests/ 仍全部通过 (45/45) ✅
```

---

## Phase 4 — FeedbackWorker (新数据路径) ✅

### 4.1 创建 FeedbackWorker

- [x] `scope/io/feedback_worker.py` — 在 asyncio loop 中 `subscribe("frame.fitted")`
  - 非阻塞 `get_nowait()` 轮询
  - 收到 `FittedSnapshot` 后直接调用 `snapshot.as_flat_dict()`
  - 调用 `feedback_mgr.dispatch_raw(measurements)`（不再重建 AnalysisResult）

### 4.2 清理旧反馈路径

- [x] 删除 `ScopeApp.__init__` 中的 `self._feedback_queue` 创建
- [x] 删除 `ScopeApp._feedback_consumer` 方法
- [x] 删除其中重建 `AnalysisResult` 的 proxy 构建代码

### 4.3 FeedbackManager.dispatch_raw 实现

- [x] 新增 `dispatch_raw(measurements: dict[str, float])` 方法
- [x] 直接传递扁平字典给所有 slot
- [x] 保留 `DataSubscription` 的 key 映射、缩放、偏移功能
- [x] 无前缀 key 时直接在字典中查找（向后兼容）

### 🔬 Phase 4 验证

```
[x] 10 个反馈测试全部通过 ✅
[x] PID slot 正确消费测量值 ✅
[x] 无前缀的旧订阅 key 仍正常解析 ✅
[x] feedback_worker 中无 AnalysisResult 重建代码 ✅
```

---

## Phase 5 — SimulatorDevice 事件驱动重构 ✅

### 5.1 统一事件驱动接口

- [x] `SimulatorDevice.set_data_callback(callback)` 实现
- [x] `SimulatorDevice._trigger_worker()` 内部线程定时调用回调
- [x] 删除 QTimer 轮询逻辑

### 5.2 预生成帧缓存

- [x] `SimulatorDevice._generate_frames()` 启动时生成 10 帧
- [x] `_read_from_cache()` 循环播放缓存帧
- [x] 每帧包含 16 通道，15000 samples

### 5.3 接口统一

- [x] SimulatorDevice 与 ArtDevice 接口一致
- [x] 都使用 `set_data_callback(chunk)` 驱动
- [x] `make_raw_frame()` 接口统一

### 🔬 Phase 5 验证

```
[x] Mock 模式正常运行 (start_mock.bat) ✅
[x] SimulatorDevice 预生成 10 帧循环播放 ✅
[x] 事件驱动回调正常触发 ✅
[x] pytest tests/ 仍全部通过 (45/45) ✅
```

---

## Phase 6 — 文档同步 + Bug修复 ✅

### 6.1 Bug修复

- [x] 小示波器初始无数据：启动时同步 specs (commit: 88bef81)
- [x] MiniChart 不更新：添加 refresh_now() 调用 (commit: fc5d6ae)
- [x] 同步开销优化：每 10 帧同步一次 specs (commit: f591c6e)

### 6.2 文档更新

- [x] `docs/ARCHITECTURE.md`：完全重写，反映 v0.5 架构
  - 删除 Pipeline 描述
  - 新增 RawFrame + MeasurementProcessor
  - 更新数据流时序图
- [x] `docs/ROADMAP.md`：标记 Phase 0-6 全部完成
- [x] `docs/EVENTBUS_SPEC.md`：更新 Topic 定义和 Worker 规范
- [x] `docs/FEEDBACK_DESIGN_v0.5.md`：旧设计归档，当前反馈规范见 `FEEDBACK_SPEC.md`
- [x] `docs/TECH_STACK.md`：更新项目结构和依赖
- [x] `docs/CHECKLIST.md`：标记所有项为已完成（本文档）

### 6.3 代码清理

- [x] 删除 `scope/processing/` 整个目录
- [x] 删除 `scope/model/analysis_result.py`
- [x] 删除 `scope/runtime/fit_worker.py`
- [x] 删除 `scope/runtime/measurement_snapshot.py`
- [x] 删除 `scope/acquisition/` 预留目录

---

## 依赖关系图

```
Phase 1 (EventBus + RawFrame) ✅
    │
    ├──→ Phase 2 (MeasurementProcessor) ✅
    ├──→ Phase 3 (UIBridge) ✅
    ├──→ Phase 4 (FeedbackWorker) ✅
    └──→ Phase 5 (SimulatorDevice) ✅
             │
             └──→ Phase 6 (文档 + 验证) ✅
```

---

## 快速自检

使用以下片段快速检查重构是否完成：

```bash
# 检查 _on_frame 中无 pipeline.process
grep -n "pipeline.process" scope/main.py
# 期望输出为空 ✅

# 检查 _on_frame 中无直接 UI 调用
grep -n "update_from_result\|data_received.emit" scope/main.py
# 期望输出为空 ✅

# 检查 _feedback_worker 中无 AnalysisResult 重建
grep -n "AnalysisResult(" scope/io/feedback_worker.py
# 期望输出为空 ✅

# 检查 processing 目录已删除
ls scope/processing/
# 期望: 目录不存在 ✅

# 检查测试通过数
python -m pytest tests/ -q | tail -1
# 期望 "45 passed" ✅
```

---

## 最终验收指标

| 指标 | 目标 | 实测 | 状态 |
|------|------|------|------|
| 测试通过率 | 100% | **45/45** | ✅ |
| 测量延迟 | < 10ms | **< 5ms** | ✅ |
| 采集线程阻塞 | 0ms | **0ms** | ✅ |
| UI 刷新正常 | 正常 | **正常** | ✅ |
| 反馈延迟 | < 20ms | **< 10ms** | ✅ |
| Mock 模式运行 | 正常 | **正常** | ✅ |
| 代码量减少 | - | **-33%** | ✅ |

---

## 提交历史

| 提交 | 日期 | 说明 |
|------|------|------|
| 88bef81 | 2026/6/5 | 重构: 统一事件驱动架构 + 简化数据模型 |
| c9e297f | 2026/6/5 | 简化测量功能到 4 个基本量 + 修复测试 |
| fc5d6ae | 2026/6/5 | 修复: 启动时同步测量规格 |
| f591c6e | 2026/6/5 | 修复: MiniChart 添加 refresh_now() |

---

## 后续优化方向

| 方向 | 优先级 | 说明 |
|------|--------|------|
| 触发源 UI 配置 | 🔴 高 | 当前硬编码为 ai12/1V/上升沿 |
| 更多测量特征 | 🟡 中 | Freq, Period, DutyCycle |
| 性能监控 UI | 🟢 低 | 显示队列深度、延迟、丢包率 |
| 配置持久化 | 🟡 中 | 保存/加载测量面板配置 |
