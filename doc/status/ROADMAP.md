---
kind: reference
status: active
scope: project.roadmap
authority: supporting
owner: ART Oscilloscope maintainers
last_verified: "2026-10-05"
applies_to: ART Oscilloscope development direction
source: project-maintained
source_commit: a3c2e33ee3640c5d23b2782756c68735ad8ee824
---

# 路线图

本文提供演进导航与后续方向，不重复当前状态、测试总数或详细待办。当前基线见 [NOW](../current/NOW.md)，未完成项见 [ACTIVE_TODO](ACTIVE_TODO.md)。

## 已发生的功能里程碑

下表按迁移前文档与提交历史归纳；不把当时的性能或硬件验证陈述提升为新的验收。

| 阶段 | 演进 |
| --- | --- |
| v0.3–v0.5 | 从 ProcessingPipeline/Slot 设计转向 RawFrame、MeasurementProcessor 和 EventBus；原始设计只保留在归档 |
| v0.6 | FeedbackManager 共享订阅、独立 FeedbackWorker 与 PidController；命令与状态桥接 |
| v0.7–v0.7.6 | AD9910/RTMQ 发送与连接池、连接池死锁修复、反馈误差保护/趋势暂停、rearm 与 fatal 重启 |
| v0.8–v0.8.2 | 默认 CONTINUOUS、软件触发帧化、连续采集稳定性与内存/UI 修复 |
| v0.9–v0.9.1 | DAC B 通道电压反馈、负增益和负 scale 输入范围 |
| v0.9.2–v0.9.3 | 同帧比值测量与反馈、无效输入自动暂停、DAC 串口回显读取回退 |
| 2026-10-05 文档整理 | 单一导航/状态/待办，分类目录、生命周期元数据与治理校验 |

## 后续推进顺序

先处理 UI 硬件诊断、设备状态 topic、配置应用确认和控制命令可靠性；再完成真实目标服务兼容性、UI 全流程及可追溯长跑验收。新的测量特征、存储和 Web/批量反馈扩展按实际需求安排，不在文档整理中顺手实现。

完整旧阶段计划、原始版本表和历史文件树见 [旧 ROADMAP](../archive/baseline/2026-10-05_ROADMAP.md)。历史中的 FeedbackSlot、dispatch_raw 与旧测试数只解释当时状态。
