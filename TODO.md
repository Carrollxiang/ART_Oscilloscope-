# TODO — EventBus v0.4 重构状态

> 更新于 Phase 1~5 代码实施完成后。见 docs/EVENTBUS_SPEC.md 架构设计。

## ✅ 已完成 — EventBus 数据路由重构

### Phase 1 — EventBus 基础设施 + 新数据类型 ✅
- [x] `BoundedQueue` → 扩展为 `EventBus` 类（多 topic / 多 subscriber 独立队列 / publish / metrics）
- [x] `FittedSnapshot` — FitWorker 产出物（channel_measurements + event_measurements）
- [x] `ConfigChange` — 控制面配置变更指令
- [x] EventBus 注册 3 个 topic：`frame.measured`/`frame.fitted`/`config.change`

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
- [x] 在 asyncio loop 中与 FeedbackWorker 并行运行

---

## 遗留项

### P2 - 交互流畅度（低优先级，可后续补充）

- [ ] **错误弹窗限流**（同类错误 N 秒内只提示一次） — `FeedbackPanel._notified_auto_pause` 已有去重思路，可强化
- [ ] **UI 面板操作 → config.change 发布** — 当前 DevicePanel 仍直接 emit `art_config_applied`，未走 ConfigWorker publish 路径
- [ ] **ControlQueue 帧边界原子生效** — ConfigWorker 可用，但 UI 端未改为异步发送

### 验收指标（待长跑验证）

- [ ] 长时间运行反馈队列 qsize 不持续堆积
- [ ] 反馈延迟不随时间增长
- [ ] 修改测量类型/时间参数流畅
- [ ] 保存配置无明显卡顿
- [ ] 同一订阅项在测量面板与反馈面板读数一致
