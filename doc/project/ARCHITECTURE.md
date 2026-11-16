---
kind: reference
status: active
scope: runtime.architecture
authority: canonical
owner: ART Oscilloscope maintainers
last_verified: "2026-10-05"
applies_to: ART Oscilloscope v0.9.3
source: project-maintained
source_commit: c283021c01601d59bee7478596f860049da78ba8
---

# 系统架构

本文描述当前代码的组件与线程分工；必须保持的架构约束由 [AGENTS](../../AGENTS.md) 定义。代码基线与验证证据见 [NOW](../current/NOW.md)。

2026-11-16 按上述提交的实现与离线回归补入比值测量、无效输入与 DAC 读取回退相关增量；原有硬件证据未刷新。

## 数据和控制路径

```text
ArtDevice / SimulatorDevice callback
  → ScopeApp._on_frame → RawFrame → EventBus frame.raw
                                    ├─ MeasurementProcessor → frame.fitted
                                    │                          ├─ UIBridge → Qt UI
                                    │                          └─ FeedbackManager → FeedbackWorker
                                    │                                              ├─ PidController
                                    │                                              └─ target sender
                                    └─ UIBridge → waveform UI

UI command → EventBus command topic → runtime worker → state/data → UI
```

`_on_frame()` 只封装/发布 RawFrame 并做最小 UIBridge 非阻塞轮询。测量计算属于 MeasurementProcessor，PID 数学属于 PidController，反馈生命周期与网络发送调度属于 FeedbackManager / FeedbackWorker。

## 组件职责

| 组件 | 实现位置 | 职责 |
| --- | --- | --- |
| ScopeApp | [scope/main.py](../../scope/main.py) | 组装设备、EventBus、worker、Qt 桥接及生命周期 |
| AcquisitionDevice / DeviceConfig | [device.py](../../scope/hardware/device.py) | 硬件抽象、配置数据类、健康回调 |
| ArtDevice / TriggerDetector | [art_device.py](../../scope/hardware/art_device.py)、[trigger_detector.py](../../scope/hardware/trigger_detector.py) | ART 采集、恢复、连续数据的软件触发帧化 |
| SimulatorDevice | [simulator.py](../../scope/hardware/simulator.py) | 线程驱动模拟采集回调 |
| EventBus | [event_bus.py](../../scope/runtime/event_bus.py) | 进程内多 topic pub/sub，每个订阅独立有界队列 |
| MeasurementProcessor | [measurement_processor.py](../../scope/runtime/measurement_processor.py) | 按规格切片、计算、发布结果 |
| ConfigWorker | [config_worker.py](../../scope/runtime/config_worker.py) | 配置去重、executor 中调用设备重配置 |
| MeasurementConfigWorker | [measurement_config_worker.py](../../scope/runtime/measurement_config_worker.py) | 规格去重并更新处理器 |
| FeedbackCommandWorker | [feedback_command_worker.py](../../scope/io/feedback_command_worker.py) | 消费反馈命令并调用 Manager API |
| FeedbackManager / Worker | [manager](../../scope/io/feedback_manager.py)、[worker](../../scope/io/feedback_worker.py) | 共享结果订阅、分发、PID 与发送生命周期 |
| UIBridge | [ui_bridge.py](../../scope/ui/ui_bridge.py) | 订阅原始帧、结果和反馈状态，通过 Qt signal 进入主线程 |
| ConfigManager | [settings.py](../../scope/config/settings.py) | JSON 保存/加载及配置恢复数据 |

## 线程边界

- Qt 主线程处理控件交互和显示。Qt signals 用于本地控件和主线程桥接。
- 采集设备线程调用 `_on_frame()`；UIBridge.poll 不执行业务计算。
- MeasurementProcessor 使用独立线程。
- ScopeApp 使用独立 asyncio loop 线程，运行配置、测量规格、反馈命令、反馈分发和指标发布任务；并未用 qasync 驱动主事件循环。
- 配置重建与发送器阻塞 RPC 通过 executor 执行。worker 并发由 asyncio 调度，不意味着每个 worker 有独立 OS 线程。

## 数据模型与计算

`RawFrame` 字段为 `sequence_num`、`data`、`sample_rate`、`timestamp`；`data` 是二维 `(channels, samples)` 数组，设备读取数据以伏特为单位。

`MeasurementSpec` 使用 `tag`、0-based `channel`、`start_ms`、`end_ms`、`feature` 与可选 `semantic`。`end_ms=0` 表示帧末。处理器将毫秒转换为采样索引，支持 Vpp / Vmax / Vmin / Mean；无效通道、空窗或未知特征不生成该测量值。

v0.9.2 新增 `feature="Ratio"`，通过 `numerator_tag` / `denominator_tag` 引用两个不同的标量规格，不能自引用。MeasurementProcessor 先计算本帧全部标量，再从标量结果副本计算比值，禁止 ratio 引用 ratio。源缺失、NaN 或分母绝对值小于 `RATIO_MIN_DENOM_ABS=1e-9` 时不产出比值，将原因写入 `invalid_reasons[tag]`；同一 tag 的告警按 1 秒节流。比值规格不使用自身通道。

`FittedSnapshot` 包含 `sequence_num`、`event_measurements`、`invalid_reasons`、`timestamp`、`pipeline_latency_ms`。无效项不出现在有效测量字典中，`get_invalid(tag)` 读取无效原因。FeedbackManager 遇到无效项时让对应 worker 自动暂停，该帧不进入 PID。测量字典以 `MeasurementSpec.tag` 为 key，不含波形。`get()` 另支持 `meta:seq` / `meta:timestamp` / `meta:latency`；`as_flat_dict()` 只复制测量字典，不为反馈生成旧 `event:` / `raw:` 前缀或 meta 项。

## 采集和配置

DeviceConfig 默认 16 通道、30,000 Sa/s、15,000 点，`acquisition_mode="continuous"`。ART CONTINUOUS 保持采集 Task，通过 TriggerDetector 切出触发帧；FINITE 使用有限采集及 rearm。读取失败恢复与 fatal 重启属于硬件/runtime 生命周期，不能移到 UI slot。

设备 UI 配置及文件加载后应用必须发布 `config.change`，再由 ConfigWorker 调用 ScopeApp 的重建路径；不存在允许面板直接调用 `_on_art_config` 的控制路径。JSON 对 dataclass 显式转换，加载 UI 与应用硬件是两个步骤。

## 差异与历史

当前健康通知、通讯测试和配置原子性差异统一记录在 [NOW](../current/NOW.md) / [待办](../status/ACTIVE_TODO.md)，本轮仅整理文档。`scope/processing/`、FeedbackSlot、DataSubscription、PidFeedbackSlot 和 dispatch_raw 已退出当前架构。历史代码示例留在 [归档](../archive/INDEX.md)。

topic 参数和队列语义只在 [EventBus 规范](../specs/EVENTBUS_SPEC.md) 维护；反馈行为只在 [反馈规范](../specs/FEEDBACK_SPEC.md) 维护。
