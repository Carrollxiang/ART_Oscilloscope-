---
kind: reference
status: active
scope: project.current-state
authority: canonical
owner: ART Oscilloscope maintainers
last_verified: "2026-11-16"
applies_to: master@c283021c01601d59bee7478596f860049da78ba8, code baseline excluding documentation migration
source: project-maintained
source_commit: c283021c01601d59bee7478596f860049da78ba8
---

# 当前项目状态

本页是已提交代码状态的唯一权威快照，不代替接口规范。文档整理随本次文档提交纳入版本管理，功能代码基线见下述 commit。

## 基线与证据

- 分支：`master`；功能基线：`c283021c01601d59bee7478596f860049da78ba8`（包含 v0.9.2 比值反馈和 v0.9.3 DAC 读取回退）。2026-11-16 从 `origin/feat/ratio-feedback` 快进合并，文档整理随后的文档提交保存，未改动功能代码。
- 2026-11-16 使用 `.venv\python.exe` 完整离线回归：**282 passed in 13.48s**。使用下述专用临时目录命令，未连接硬件。
- 历史核对日期：2026-10-05；本轮按代码检查数据流、topic 注册、配置应用、反馈发送和环境路径。
- 历史 v0.9.1 代码基线完成离线回归：**217 passed in 7.16s**。项目解释器为 `.venv\python.exe`，Python 3.10.20。
- 验证命令：`& .\.venv\python.exe -m pytest -q --ignore=.pytest_cache -p no:cacheprovider --basetemp=<新建且未使用的临时目录>`。缓存和系统 pytest 临时目录存在访问权限问题，因此绕开它们；未修改测试。
- 本轮整理未连接 ART、AD9910、RTMQ 或 DAC，没有产生新的硬件验收结论。

## 已实现

### 已提交：新增反馈自动跳过已有 ID

2026-10-05 工作树修改：反馈面板生成新增 `wN` 时跳过状态快照和卡片中已占用的编号，修复加载配置后计数器从零开始导致重复 ID、确认后没有新增卡片的问题。保留递增计数器，连续新增不依赖状态刷新。

新增 [反馈面板回归](../../tests/test_feedback_panel.py) 覆盖用户编号 `0,2,6,7,8,9,12,13,1,3,4,5`、加载后计数器为零/六、连续占用编号及空列表；通过实际 UI 新增命令与 FeedbackCommandWorker/Manager 验证新增成功。专项 **4 passed**；完整离线回归 **221 passed in 7.47s**，使用上述项目解释器及专用临时目录命令。未连接硬件。该修复现已提交为 `ccbe2a5`，包含在上方功能基线中。

### 比值测量与 DAC 读取回退

v0.9.2 新增 Ratio 规格、测量面板比值行、同帧标量相除及源 tag 配置持久化；无效源或近零分母通过 `invalid_reasons` 让对应 worker 暂停并显示原因。v0.9.3 在 `b_voltage` 为空时解析 DAC 的串口回显，保留原有电压限幅与发送方式。新增比值测量、面板和 DAC 解析回归随完整离线测试通过；没有新增硬件观察。具体契约见反馈和 EventBus 规范。

| 范围 | 当前实现 |
| --- | --- |
| 数据面 | `RawFrame` → `frame.raw` → `MeasurementProcessor` → `frame.fitted` → UI / 反馈 |
| 控制面 | `config.change`、`measurement.specs.changed`、`feedback.worker.command` 分别由 runtime worker 消费 |
| 状态面 | `feedback.status` 经 UIBridge 更新 UI；`runtime.metrics` 周期聚合发布 |
| 采集 | ART 默认 CONTINUOUS + TriggerDetector 软件触发帧化；FINITE 模式保留 |
| 恢复 | 读取失败重试/reset/rearm 与 fatal 保存配置后重启路径 |
| 测量 | 时间窗内 Vpp、Vmax、Vmin、Mean；两个标量的同帧 Ratio |
| 反馈 | 共享订阅、独立 PidController、暂停仍刷新有效读数、无效输入自动暂停、误差保护、趋势自动暂停、发送失败冷却 |
| 目标 | AD9910、RTMQ、DAC 发送器；DAC B 通道 0–5 V，支持负增益/负 scale |
| UI | 波形、测量、MiniChart、反馈卡片和参数面板；本地交互用 Qt signals |

## 证据边界与现存差异

迁移前文档记载 AD9910 端到端实测、CONTINUOUS 真机 6 小时长跑和 mock 内存修复后 2 小时长跑。本轮未复测，且未找到随文档保存的原始日志；这些结论保留为 [历史陈述](../archive/baseline/2026-10-05_TODO.md)，不由今天的离线测试刷新。

RTMQ 的远端 `set_sideband` 接口与真实服务兼容性、DAC 设备闭环效果仍需要端到端证据；sender 单元测试不能证明物理输出。

以下是实现与目标架构的已知差异：

- `device.status` 未注册；设备健康回调仍经 ScopeApp 的 Qt signal 显示。
- `DevicePanel` 通讯测试在 UI 路径加载 DLL 并操作 ArtDevice，偏离 AGENTS 的硬件调用与阻塞 I/O 规则。
- `ConfigWorker` 将重配置交给 executor，但没有显式帧边界屏障或配置结果确认协议；不能把旧注释中的“帧边界原子生效”当作已验收。
- EventBus 的 BLOCK topic 在 `publish()` 中以默认零超时入队；满队列可丢失命令且发布者未收到确认。
- 连接池 `min_size` / `idle_timeout` 只有配置定义，没有保底连接/空闲回收实现。

详见 [活跃待办](../status/ACTIVE_TODO.md)。架构见 [ARCHITECTURE](../project/ARCHITECTURE.md)，接口见 [EventBus](../specs/EVENTBUS_SPEC.md) 与 [反馈](../specs/FEEDBACK_SPEC.md)。
