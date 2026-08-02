# 文档纲要

本目录保存数字示波器项目的架构、路线图、技术决策和反馈系统设计文档。

当前代码状态以仓库实际实现和测试结果为准。阅读文档时请优先使用本文档作为入口，避免被历史阶段的 v0.3/v0.5 设计带偏。

## 当前项目快照

| 项 | 当前状态 |
|----|----------|
| 主架构 | PyQt6 UI + EventBus 数据面/控制面 + 独立 runtime worker |
| 数据面 | `frame.raw` -> `MeasurementProcessor` -> `frame.fitted` |
| 控制面 | 设备配置、测量规格、反馈 worker 命令均已走 EventBus |
| 状态面 | `feedback.status` 事件驱动快照 + `runtime.metrics` 运行时指标 |
| 反馈系统 | v0.6 Worker 架构已实现，状态读取由 EventBus 快照驱动；v0.7 目标设备发送（AD9910/RTMQ）已实现 |
| 测试基线 | `142 passed` |
| 推荐测试命令 | `& .\.venv\python.exe -m pytest -q` |

## 推荐阅读顺序

1. [AGENTS.md](../AGENTS.md)  
   AI 协作和代码生成约束。所有新改动必须先遵守这里的架构边界。

2. [ARCHITECTURE.md](./ARCHITECTURE.md)  
   当前系统总架构。先读它理解 HAL、EventBus、MeasurementProcessor、UIBridge、FeedbackManager 的关系。

3. [EVENTBUS_SPEC.md](./EVENTBUS_SPEC.md)  
   EventBus topic、线程边界、队列策略。注意它起源于 v0.5，反馈部分以 `FEEDBACK_SPEC.md` 和实际代码为准。

4. [FEEDBACK_SPEC.md](./FEEDBACK_SPEC.md)  
   当前 v0.6 反馈系统规范。PID、FeedbackWorker、FeedbackManager 以此为主。

5. [ROADMAP.md](./ROADMAP.md)  
   阶段路线图和历史演进。用于看项目怎么走到当前状态，不作为接口细节的唯一依据。

## 文档索引

| 文档 | 用途 | 状态 |
|------|------|------|
| [ARCHITECTURE.md](./ARCHITECTURE.md) | 当前系统架构总览 | 当前主文档 |
| [EVENTBUS_SPEC.md](./EVENTBUS_SPEC.md) | EventBus 设计、topic、线程边界 | 当前参考，部分 v0.5 历史描述 |
| [FEEDBACK_SPEC.md](./FEEDBACK_SPEC.md) | v0.6 反馈系统规范 | 当前主文档 |
| [ROADMAP.md](./ROADMAP.md) | 项目阶段路线图 | 需持续同步 |
| [TECH_STACK.md](./TECH_STACK.md) | 技术栈与依赖说明 | 需整理，部分结构图过期 |
| [CHECKLIST.md](./CHECKLIST.md) | v0.5 EventBus 重构验收清单 | 历史归档 |
| [FEEDBACK_TODO.md](./FEEDBACK_TODO.md) | v0.6 反馈重构任务清单 | 基本完成，保留剩余验证项 |
| [FEEDBACK_DESIGN_v0.5.md](./FEEDBACK_DESIGN_v0.5.md) | v0.5 反馈旧方案 | 已废弃，仅作历史参考 |

## 当前 EventBus 边界

业务级、跨线程、跨模块事件走 EventBus：

- `frame.raw`
- `frame.fitted`
- `config.change`
- `measurement.remove`
- `feedback.status`
- `runtime.metrics`

局部 UI 交互继续走 Qt signal：

- 按钮点击
- 表单控件变化
- 面板内部联动
- `UIBridge` 发往 Qt 主线程的显示信号

当前控制面 topic：

- `measurement.specs.changed`
- `feedback.worker.command`

## 当前技术债

- `rpyc_pool` 的 `min_size`/`idle_timeout` 配置项尚未生效（无保底连接与空闲回收，预留后续实现）。
- `DevicePanel` 通讯测试路径在 Qt 主线程直接 `ctypes` 加载 `Art_DAQ.dll` 并操作 `ArtDevice`（绕过 EventBus，属诊断功能刻意设计，未文档化）。
- `MeasurementSpec.feature` 仅支持 Vpp/Vmax/Vmin/Mean；Vrms/Freq/Period 等特征未实现（默认 feature 已修正为 `Vpp`）。
- 触发源（ai12/1V/上升沿）仍硬编码，UI 配置未实现。
- `DeviceConfig` 默认值为 4 通道/10k 点，与 ScopeApp 运行时的 16 通道/15k 点配置不同（运行时显式覆盖，无实际影响）。
- Phase 0 规划的 `acquisition/ring_buffer.py`、`watchdog.py` 未实现（目录为空）。
- Mock 模式的完整 UI 操作长跑验证尚未形成自动化测试。
- ✅ AD9910 反馈端到端已实测通过（连接池自死锁修复后, 2026/6）；RTMQ 端到端验证仍待服务就绪。

## 文档维护规则

- 改 EventBus topic 时，同步更新本文件、`EVENTBUS_SPEC.md`、`ARCHITECTURE.md`。
- 改反馈系统时，同步更新 `FEEDBACK_SPEC.md` 和 `FEEDBACK_TODO.md`。
- 改项目状态或测试基线时，同步更新本文件、`ROADMAP.md`、根目录 `TODO.md`。
- 历史文档不要直接删除，先在顶部标记“历史归档”或“已废弃”。
