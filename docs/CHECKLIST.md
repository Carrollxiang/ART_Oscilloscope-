# 实施清单 — EventBus 数据路由重构

> 按阶段顺序执行，每个阶段产出是可独立验证的增量。
> 每个步骤前标有 `[ ]`，完成后标 `[x]` 并记录验证结果。

---

## Phase 1 — EventBus 基础设施 + 新数据类型

### 1.1 重构 event_bus.py

- [x] **EventBus 类**：`dict[topic, list[BoundedQueue]]`，提供 `register_topic()` / `publish()` / `subscribe()` 接口
  - `publish(topic)` 遍历所有 subscriber 队列依次 `put()`
  - `subscribe(topic)` → 返回一个新的 `BoundedQueue`（各 subscriber 独立消费进度）
  - 验证：单测验证 1 producer → 2 subscribers 各自独立消费，丢弃互不影响
- [ ] **QueueStats 扩展**：新增 `max_drop_rate` 或保持现有字段
- [ ] **导出**：在 `scope/runtime/__init__.py` 中导出 `EventBus`

### 1.2 新建 FittedSnapshot

- [x] 创建 `scope/runtime/fitted_snapshot.py`
  - `FittedSnapshot` data class（`sequence_num`, `channel_measurements`, `event_measurements`, `timestamp`, `pipeline_latency_ms`）
  - `as_flat_dict()` 方法
- [ ] 在 `scope/runtime/__init__.py` 中导出

### 1.3 新建 ConfigChange

- [ ] 创建 `scope/runtime/config_change.py`
  - `ConfigChange` data class（`device_config`, `art_params`, `change_id`, `timestamp`）
- [ ] 在 `scope/runtime/__init__.py` 中导出

### 1.4 ScopeApp 集成 EventBus

- [ ] `ScopeApp.__init__()` 中创建 EventBus 实例
- [ ] 注册三个 topic：`frame.measured` (maxsize=2)、`frame.fitted` (maxsize=2)、`config.change` (maxsize=8, block)
- [ ] 传递到各 Worker 的构造函数

### 🔬 Phase 1 验证

```
[ ] pytest tests/ 仍全部通过
[ ] 单独测试 EventBus: 1pub-2sub 独立消费
[ ] 单独测试 FittedSnapshot: as_flat_dict 合并正确
```

---

## Phase 2 — FitWorker（接管全部计算）

### 2.1 创建 FitWorker

- [ ] `scope/runtime/fit_worker.py` — `FitWorker` 类
  - 构造函数接收 `EventBus` + `ProcessingPipeline`
  - `run()` 方法：循环 `subscribe("frame.measured")` → `get_nowait()` → Pipeline → `publish("frame.fitted")`
  - `stop()` 方法：设置停止标记，消费完当前帧后退出
  - 线程名：`"fit-worker"`

### 2.2 迁移 Pipeline 实例

- [ ] 从 `ScopeApp.__init__` 中移除 `self._pipeline` 的创建和 AutoMeasure/MathOp/FFT 配置
- [ ] `FitWorker.__init__` 中创建 `ProcessingPipeline` 实例并添加 AutoMeasure/MathOp/FFT 阶段
- [ ] 传入 `MEASUREMENT_FUNCTIONS` 和通道列表的配置

### 2.3 实现 EventWindowSpec 计算

- [ ] 在 `scope/processing/` 或 `scope/runtime/` 中定义 `EventWindowSpec` data class
  - `tag`, `channel`, `start_ms`, `end_ms`, `feature`, `semantic`
  - `compute(channel_data: ChannelData) → float` 方法
- [ ] `FitWorker.run()` 中：从 `MeasurementPanel` 同步配置（或通过 `config.change` topic 接收），对每个 `EventWindowSpec` 从 `AnalysisResult.channels` 中切片计算
- [ ] 事件窗口结果写入 `FittedSnapshot.event_measurements`
- [ ] 通道级测量结果（Pipeline）写入 `FittedSnapshot.channel_measurements`

### 2.4 裁剪 _on_frame()

- [ ] 移除 `self._pipeline.process(result)` 调用
- [ ] 移除 `self._feedback_queue.put(snap)` 和相关的 `MeasurementSnapshot` 导入
- [ ] 只保留 `make_analysis_result(chunk)` → `publish("frame.measured", result)`

### 🔬 Phase 2 验证

```
[ ] _on_frame() 中无 pipeline.process 调用
[ ] FitWorker 线程启动，日志中可见 thread_name=fit-worker
[ ] FittedSnapshot 的 channel_measurements 与同步 Pipeline 结果一致
[ ] EventWindowSpec 切片计算边界正确（start_ms=0, end_ms=0 空切片）
[ ] pytest tests/ 仍全部通过（不影响反馈测试）
```

---

## Phase 3 — UIBridge（统一 Qt 桥接）

### 3.1 创建 UIBridge

- [ ] `scope/ui/ui_bridge.py` — `UIBridge(QObject)` 类
  - `signal_raw_frame = pyqtSignal(object)` → AnalysisResult
  - `signal_fitted = pyqtSignal(object)` → FittedSnapshot
  - `poll()` 方法：非阻塞轮询两个队列，有数据则 emit
  - 线程安全：emit 时 Qt 自动将信号排入主线程

### 3.2 连接信号到 UI

- [ ] `MainWindow.__init__` 中连接信号：
  - `signal_raw_frame.connect(self._on_ui_raw_frame)` → `waveform_view.update_waveform(result)`
  - `signal_fitted.connect(self._on_ui_fitted)` → `measure_panel.update_from_fitted(fitted)` + `mini_chart.add_data(fitted.as_flat_dict())`
- [ ] 实现 `_on_ui_raw_frame` 和 `_on_ui_fitted` 槽函数

### 3.3 清除旧直接 UI 调用

- [ ] 从 `_on_frame()` 中移除：
  - `self.main_win.measure_panel.update_from_result(result)`
  - `self.main_win.mini_chart.add_data(filtered)`
  - `self.main_win.data_received.emit(result)`

### 3.4 MiniChart 渲染节流

- [ ] 保留 `QTimer` 仅作为渲染节流（20fps 上限），但数据源改为 `signal_fitted` 驱动的脏标记
- [ ] 移除 `MiniChartWidget` 中独立的数据轮询逻辑

### 🔬 Phase 3 验证

```
[ ] 采集线程中无直接 UI 调用（无 Qt 线程警告）
[ ] 主波形、测量面板、迷你图均正常更新
[ ] 打开/关闭 MiniChart 不影响采集帧率
[ ] 长时间运行无内存泄漏（曲线对象复用 setData）
[ ] pytest tests/ 仍全部通过
```

---

## Phase 4 — FeedbackWorker（新数据路径）

### 4.1 创建 FeedbackWorker

- [ ] `scope/io/feedback_worker.py` — 在 asyncio loop 中 `subscribe("frame.fitted")`
  - 非阻塞 `get_nowait()` 轮询
  - 收到 `FittedSnapshot` 后直接提取 `channel_measurements` + `event_measurements`
  - 调用 `feedback_mgr.dispatch(flat_dict)`（不再重建 AnalysisResult）

### 4.2 清理旧反馈路径

- [ ] 删除 `ScopeApp.__init__` 中的 `self._feedback_queue` 创建
- [ ] 删除 `ScopeApp._feedback_consumer` 方法
- [ ] 删除其中重建 `AnalysisResult` 的 proxy 构建代码

### 4.3 FeedbackManager._resolve_value 结构化 key 支持

- [ ] 支持 `event:tag` → 从 `event_measurements` 取值
- [ ] 支持 `raw:key` → 从 `channel_measurements` 取值
- [ ] 支持 `meta:seq` → 返回 `sequence_num`
- [ ] 无前缀时：先查 `event_measurements`，再查 `channel_measurements`（向后兼容）

### 🔬 Phase 4 验证

```
[ ] 19 个反馈测试全部通过
[ ] PID slot 正确消费 event:tag 值
[ ] 无前缀的旧订阅 key 仍正常解析
[ ] feedback_consumer 中无 AnalysisResult 重建代码
```

---

## Phase 5 — ConfigWorker + 控制面隔离

### 5.1 创建 ConfigWorker

- [ ] `scope/runtime/config_worker.py` — `ConfigWorker` 类
  - 订阅 `config.change` 队列
  - 收到 `ConfigChange` → 调用 `ScopeApp._on_art_config()`
  - 使用 `change_id` 去重（防止重复应用同一配置）

### 5.2 UI 面板改为异步发送配置

- [ ] `FeedbackDialog.get_config()`/`PidFeedbackDialog` 的确认操作 → `publish("config.change", ...)` 替代直接调硬件
- [ ] `DevicePanel` 的"应用配置到设备"按钮 → 同样走 `config.change`
- [ ] 移除 UI 线程中对 `_on_art_config()` 的直接调用

### 5.3 帧边界原子生效

- [ ] ConfigWorker 在接收到 `frame.measured` 之间应用配置（利用采集间隔）
- [ ] 应用配置期间新的 `frame.measured` 暂缓处理或正常 drop（旧帧可丢）

### 🔬 Phase 5 验证

```
[ ] 修改设备参数时 UI 不卡顿
[ ] 配置变更在帧边界原子生效，不产生半帧状态
[ ] 高频配置变更不丢失、不重复执行
[ ] pytest tests/ 仍全部通过
```

---

## Phase 6 — 文档同步 + 收尾

### 6.1 更新设计文档

- [ ] `docs/ARCHITECTURE.md`：新增 EventBus 章节，替换 §7.4 旧有界队列描述
- [ ] `docs/TECH_STACK.md`：新增 `scope/runtime/` 章节
- [ ] `docs/ROADMAP.md`：Phase 6 完成标记
- [ ] `TODO.md`：标记 P0/P1/P2 对应完成项

### 6.2 长期运行验证

- [ ] 模拟器模式下运行 30 分钟，验证队列指标无异常增长
- [ ] 验证 `total_drops` 在合理范围（不持续增长表示背压有效）
- [ ] 验证 `avg_latency_ms` 不随时间增长

---

## 依赖关系图

```
Phase 1 (EventBus + 数据类型)
    │
    ├──→ Phase 2 (FitWorker)         ── 依赖 EventBus
    ├──→ Phase 3 (UIBridge)          ── 依赖 EventBus + Phase 2 (fitted 数据)
    ├──→ Phase 4 (FeedbackWorker)    ── 依赖 EventBus + Phase 2 (fitted 数据)
    └──→ Phase 5 (ConfigWorker)      ── 依赖 EventBus
             │
             └──→ Phase 6 (文档 + 验证)
```

---

## 快速自检

使用以下片段快速检查重构是否完成：

```bash
# 检查 _on_frame 中无 pipeline.process
grep -n "pipeline.process" scope/main.py
# 期望输出为空

# 检查 _on_frame 中无直接 UI 调用
grep -n "update_from_result\|add_data\|data_received.emit" scope/main.py
# 期望输出为空

# 检查 _feedback_consumer 中无 AnalysisResult 重建
grep -n "AnalysisResult(" scope/main.py
# 期望只有 make_analysis_result 调用

# 检查测试通过数
python -m pytest tests/ -q | tail -1
# 期望 "72 passed"
```
