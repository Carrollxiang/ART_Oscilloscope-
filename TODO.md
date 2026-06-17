# TODO — EventBus v0.4 重构状态 + 反馈系统 v0.6

> 更新于 Phase 1~6 代码实施完成后。见 docs/EVENTBUS_SPEC.md 架构设计。

## ✅ 已完成 — EventBus 数据路由重构

### Phase 1 — EventBus 基础设施 + 新数据类型 ✅
- [x] `BoundedQueue` → 扩展为 `EventBus` 类（多 topic / 多 subscriber 独立队列 / publish / metrics）
- [x] `FittedSnapshot` — FitWorker 产出物（channel_measurements + event_measurements）
- [x] `ConfigChange` — 控制面配置变更指令
- [x] EventBus 注册核心 topic：`frame.raw`/`frame.fitted`/`config.change`/`measurement.specs.changed`/`measurement.remove`/`feedback.worker.command`

### Phase 2 — FitWorker（接管全部计算） ✅
- [x] 独立线程运行 `ProcessingPipeline`（AutoMeasure / MathOp / FFT）
- [x] `EventWindowSpec` 时间窗切片计算（Vpp/Vrms/Mean/Integral 等特征）
- [x] `_on_frame()` 精简为仅 `make_analysis_result` + `publish("frame.measured")`
- [x] 采集线程不再执行 Pipeline / 直接 UI 调用

### Phase 3 — UIBridge（统一 Qt 桥接） ✅
- [x] `UIBridge(QObject)` — `signal_raw_frame` + `signal_fitted`，非阻塞 `poll()`
- [x] `MainWindow.connect_ui_bridge()` — 连接信号到波形/测量面板/MiniChart
- [x] 移除旧 `data_received` 信号和 `update_display` 方法
- [x] MiniChart QTimer 仅作为渲染节流（20fps），数据由 signal_fitted 驱动

### Phase 4 — FeedbackWorker（新数据路径） ✅
- [x] 在 asyncio loop 中订阅 `frame.fitted` → 消费 `FittedSnapshot`
- [x] `FeedbackManager.dispatch_raw(flat_dict)` — 不再重建 AnalysisResult
- [x] `_resolve_value_from_dict` 支持 `event:` / `raw:` / `meta:` 结构化 key
- [x] 删除旧 `_feedback_consumer` 中的 AnalysisResult proxy 重建 hack

### Phase 5 — ConfigWorker + 控制面隔离 ✅
- [x] `ConfigWorker` — 订阅 `config.change`，`change_id` 去重，`run_in_executor` 调用
- [x] `MeasurementConfigWorker` — 订阅 `measurement.specs.changed`，更新 MeasurementProcessor specs
- [x] `FeedbackCommandWorker` — 订阅 `feedback.worker.command`，统一应用 add/pause/resume/remove/update_pid
- [x] 在 asyncio loop 中与 FeedbackWorker 并行运行

---

## 遗留项

### P2 - 交互流畅度（低优先级，可后续补充）

- [ ] **错误弹窗限流**（同类错误 N 秒内只提示一次） — `FeedbackPanel._notified_auto_pause` 已有去重思路，可强化
- [x] **设备配置 UI → config.change 发布** — DevicePanel 本地信号由 MainWindow 包装为 `ConfigChange`，经 ConfigWorker 应用
- [x] **测量规格 UI → measurement.specs.changed 发布** — MeasurementPanel 发布完整 specs 快照，经 MeasurementConfigWorker 应用
- [x] **反馈控制 UI → feedback.worker.command 发布** — FeedbackPanel 发布命令，经 FeedbackCommandWorker 应用
- [ ] **ControlQueue 帧边界原子生效** — ConfigWorker 可用，但 UI 端未改为异步发送

### 验收指标（待长跑验证）

- [ ] 长时间运行反馈队列 qsize 不持续堆积
- [ ] 反馈延迟不随时间增长
- [ ] 修改测量类型/时间参数流畅
- [ ] 保存配置无明显卡顿
- [ ] 同一订阅项在测量面板与反馈面板读数一致

---

## ✅ 已完成 — 反馈系统 v0.6 重构

### 核心变更

| 变更 | v0.5 (旧) | v0.6 (新) |
|------|-----------|-----------|
| 反馈单元 | `FeedbackSlot` 基类 | `FeedbackWorker` 独立单元 |
| PID 封装 | 在 Slot 内部 | **独立 `PidController`** (`scope/runtime/pid_controller.py`) |
| EventBus 订阅 | 每个 Slot 各自订阅 | **唯一订阅** (Manager 持有) |
| `as_flat_dict()` | N 次/帧 | **1 次/帧** |
| 数据分发 | `dispatch_raw()` + `DataSubscription` | Worker 直接按 `measurement_key` 取值 |

### 新增文件
- `scope/runtime/pid_controller.py` — PidConfig + PidController (11 测试)
- `tests/test_pid_controller.py`
- `tests/test_feedback_worker.py` (15 测试)
- `tests/test_feedback_manager.py` (16 测试)

### 重写文件
- `scope/io/feedback_worker.py`
- `scope/io/feedback_manager.py`

### 删除文件
- `scope/io/feedback_slots/` (base.py, null_slot.py, __init__.py)
- `tests/test_feedback_slots.py`

### 测试覆盖
- **81/81 测试通过** (新增测量规格控制面与反馈命令控制面测试)

### 待完成
- [x] **FeedbackDialog 升级** — 已实现配置表单，调用 `feedback_manager.add_worker()`
- [x] **FeedbackPanel.refresh_slots()** — 已实现 UI 刷新显示 worker 列表
- [ ] Mock 模式完整测试（添加 worker、暂停/恢复、配置保存/加载）
