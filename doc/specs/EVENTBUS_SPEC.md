---
kind: reference
status: active
scope: runtime.eventbus-contract
authority: canonical
owner: ART Oscilloscope maintainers
last_verified: "2026-10-05"
applies_to: ART Oscilloscope v0.9.3
source: project-maintained
source_commit: c283021c01601d59bee7478596f860049da78ba8
---

# EventBus 接口与队列语义

以 [ScopeApp topic 注册](../../scope/main.py) 和 [EventBus 实现](../../scope/runtime/event_bus.py) 核对。架构约束见 [AGENTS](../../AGENTS.md)，线程分工见 [架构](../project/ARCHITECTURE.md)。

2026-11-16 按上述提交的实现与离线回归补入比值测量、无效输入与 DAC 读取回退相关增量；原有硬件证据未刷新。

## 现行 topics

maxsize 是每个订阅队列的容量，不是全 topic 共用容量。

| Topic | Payload | maxsize | 策略 | 生产者 → 消费者 |
| --- | --- | --- | --- | --- |
| `frame.raw` | RawFrame | 2 | drop_oldest | ScopeApp 回调 → MeasurementProcessor、UIBridge |
| `frame.fitted` | FittedSnapshot | 2 | drop_oldest | MeasurementProcessor → UIBridge、FeedbackManager |
| `config.change` | ConfigChange | 8 | block | MainWindow 配置/加载路径 → ConfigWorker |
| `measurement.specs.changed` | MeasurementSpecsChanged | 4 | drop_oldest | UI 完整规格快照 → MeasurementConfigWorker |
| `measurement.remove` | str（测量 tag） | 8 | block | MeasurementPanel → MainWindow/MiniChart |
| `feedback.worker.command` | FeedbackCommand | 32 | block | FeedbackPanel/批量加载 → FeedbackCommandWorker |
| `feedback.status` | FeedbackStatusSnapshot | 2 | drop_oldest | FeedbackManager → UIBridge → FeedbackPanel |
| `runtime.metrics` | RuntimeMetricsSnapshot | 2 | drop_oldest | ScopeApp 周期任务 → 诊断消费者预留 |

`device.status` 是计划项，当前没有注册；健康回调现走 ScopeApp → Qt signal。不能把它列为已交付 topic。

## API 与背压

- `register_topic(topic, maxsize, on_drop)` 幂等；重复注册保留首次配置。
- `subscribe(topic)` 返回独立 BoundedQueue；未注册 topic 抛 KeyError。`unsubscribe(topic, queue)` 幂等移除订阅；topic 注册保留。
- `publish(topic, item)` 在总线锁外依次入队；没有订阅时静默丢弃，没有返回投递确认。EventBus 不做 payload 类型检查。
- DROP_OLDEST 满时丢旧保新；DROP_NEWEST 满时拒绝新项。
- BLOCK 只有调用 `BoundedQueue.put(item, timeout>0)` 才会等待。当前 `EventBus.publish` 调用 `put(item)`，默认 timeout=0，满队列立即失败，发布者不会获知失败。因此控制面没有可靠投递或重试保证。
- `get_nowait()` 空时返回 None；`get(timeout)` 等待至有数据或超时。消费者自行轮询，没有同步业务回调。
- `metrics()` 返回各订阅 QueueStats：队列长度、put/get/drop 次数、最高长度、平均队列延迟。该延迟不是完整硬件闭环时延。

EventBus 是线程安全的进程内 pub/sub，没有网络或跨进程传输功能。RPyC 属于反馈发送器的外部通信层。

## 测量结果载荷

`frame.fitted` 的 `FittedSnapshot` 自 v0.9.2 起包含 `invalid_reasons: dict[str, str]`，以测量 tag 映射本帧无效原因；有效的标量与比值放在 `event_measurements: dict[str, float]`，无效项不在其中。其余字段为 `sequence_num`、`timestamp` 和 `pipeline_latency_ms`。`as_flat_dict()` 只返回有效测量值副本，`get_invalid(tag)` 读取无效原因。FeedbackManager 分发时先查无效原因，命中则调用 `report_invalid()` 暂停对应 worker，该帧不进入 PID；详见 [反馈规范](FEEDBACK_SPEC.md)。topic 名称和队列参数没有变化。

## 控制载荷

`ConfigChange(device_config, art_params, change_id, timestamp)`：完整 DeviceConfig 与 ART 参数；ConfigWorker 以 change_id 去重并交给 executor 应用。现代码无显式帧边界屏障和应用结果 ack。

`MeasurementSpecsChanged(specs, change_id, source, timestamp)`：完整规格列表。覆盖旧快照符合 drop_oldest 语义；不是逐条增量命令。

`FeedbackCommand(action, worker_id, change_id, config, pid_config, config_list, timestamp)`：支持 add / pause / resume / remove / update_pid / update_target / load_batch。字段约束和生命周期见 [反馈规范](FEEDBACK_SPEC.md)。

`FeedbackStatusSnapshot` 包含 workers、running_count、total_count 和 timestamp；各 worker 状态包括读数、误差、PID 参数、目标信息、sender_error、stop_reason、frames_skipped。帧分发触发的全量快照限至 10 Hz，控制变更可立即发布。

## 验证与修改规则

现有相关回归文件包括 [test_event_bus.py](../../tests/test_event_bus.py)、[test_measurement_config_worker.py](../../tests/test_measurement_config_worker.py)、[test_feedback_command_worker.py](../../tests/test_feedback_command_worker.py)、[test_feedback_manager.py](../../tests/test_feedback_manager.py) 和 [test_config_manager.py](../../tests/test_config_manager.py)。测试总数及最新结果只在 [NOW](../current/NOW.md) 维护。

topic 改动须同时更新本表、生产/消费路径、必要测试和 AGENTS 的 topic 列表；状态/命令可靠性缺口见 [待办](../status/ACTIVE_TODO.md)。
