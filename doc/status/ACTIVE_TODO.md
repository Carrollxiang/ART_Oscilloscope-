---
kind: reference
status: active
scope: project.backlog
authority: canonical
owner: ART Oscilloscope maintainers
last_verified: "2026-10-05"
applies_to: ART Oscilloscope active backlog
source: project-maintained
source_commit: a3c2e33ee3640c5d23b2782756c68735ad8ee824
---

# 活跃待办

本页只维护未完成事项和验收方式，不累计历史已完成项。已实现状态见 [NOW](../current/NOW.md)，历史清单见 [归档](../archive/INDEX.md)。优先级是整理后的维护顺序，不改变原有实现。

## P1：架构差异与投递可靠性

| 待办 | 当前依据 | 完成标准 |
| --- | --- | --- |
| 通讯测试离开 Qt 主线程 | DevicePanel._run_test 直接加载 DLL / ArtDevice | 经 runtime 命令运行，UI 不做阻塞设备 I/O，有成功/失败状态回传 |
| device.status 统一设备健康通知 | 现在是设备回调 → ScopeApp Qt signals；topic 未注册 | 定义载荷、注册/发布/消费路径，更新 EventBus 规范与回归 |
| 明确配置应用时序与确认 | ConfigWorker executor 重建，无帧边界屏障/ack | 记录停止/配置/恢复顺序、应用失败语义，以回归验证承诺范围 |
| 控制命令满队列处理 | BLOCK topic 经 publish 零超时入队，失败未反馈 | 明确拒绝/重试/确认机制，满队列不静默丢失关键命令 |

## P2：端到端与交互验收

- [ ] RTMQ 服务契约和 set_sideband 参数/单位兼容性确认；完成端到端发送记录。
- [ ] DAC read/b_voltage/set_ab(0,b) 服务兼容性、反馈极性与 B 通道限幅实机验收，记录 A 通道归零行为。
- [ ] mock UI 完整流程：新增/编辑/移除 worker、暂停/恢复、保存/加载、改测量类型/时间窗、相同测量 key 读数一致。
- [ ] 多 worker 负载验收：先覆盖 10 个 worker 的并发与独立暂停，再按需要测量 50 个 worker 的分发延迟；旧清单的 <20 ms 是目标，不是当前保证。
- [ ] 保存配置和修改测量规格不卡顿的交互记录；同类错误弹窗去重/限流。
- [ ] 后续 ART/反馈长跑记录固定代码、配置、设备、时长和原始日志；历史长跑陈述未在本轮刷新。

## P3：明确需求后再实现的扩展

- [ ] 连接池 min_size / idle_timeout 生效或明确移除无效配置。
- [ ] Vrms/Freq/Period/DutyCycle 等测量扩展；当前有四种标量特征及 Ratio 派生比值。
- [ ] 反馈卡片内 MiniChart 趋势图、数据到达状态灯高亮、拖拽排序。
- [ ] ring_buffer/watchdog、批量发送、多级 PID、Web/REST 监控、串口/Modbus 反馈、HDF5 波形记录与回放、更多触发类型、exe 打包发布：保留规划，不视为当前功能。

以上事项不在本轮文档迁移中修改代码。功能推进时按 [AGENTS](../../AGENTS.md) 的职责边界实现，更新对应规范；项目状态只更新 NOW，待办只在本页关闭。
