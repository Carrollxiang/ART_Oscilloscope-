---
kind: reference
status: active
scope: feedback.contract
authority: canonical
owner: ART Oscilloscope maintainers
last_verified: "2026-10-05"
applies_to: ART Oscilloscope v0.9.3
source: project-maintained
source_commit: c283021c01601d59bee7478596f860049da78ba8
---

# 反馈系统规范

本规范描述 Worker 架构、v0.9.2 比值反馈及 v0.9.3 的 DAC 读取行为。核对依据是 [FeedbackManager](../../scope/io/feedback_manager.py)、[FeedbackWorker](../../scope/io/feedback_worker.py)、[PidController](../../scope/runtime/pid_controller.py) 与 sender 实现；代码/测试状态见 [NOW](../current/NOW.md)。

2026-11-16 按上述提交的实现与离线回归补入比值测量、无效输入与 DAC 读取回退相关增量；原有硬件证据未刷新。

## 数据与命令

Manager 持有一个 `frame.fitted` 订阅，每帧只调用一次 `as_flat_dict()`，按 worker.measurement_key 获取值，再用 asyncio.gather 并发分发。key 必须与 MeasurementSpec.tag 一致；分发时先查 invalid_reasons，命中则调用 report_invalid() 暂停对应 worker 并记录原因，该帧不调用 process()；其余缺失值不调用该 worker。Worker 被动接收值，不独立订阅总线。Manager 停止时移除共享订阅。

UI 发布 `feedback.worker.command`，FeedbackCommandWorker 消费后调用 Manager；UI 不直接修改 worker。命令以 change_id 去重：

| action | 所需额外字段 | 行为 |
| --- | --- | --- |
| add | config: FeedbackConfig | 创建并启动 worker |
| pause / resume / remove | worker_id | 暂停、恢复或停止并移除 |
| update_pid | pid_config | 替换 PID 配置/控制器 |
| update_target | config 且 target 非空 | 更新目标并重建 sender |
| load_batch | config_list | 批量恢复持久化配置 |

`FeedbackConfig` 包含 worker_id、measurement_key、pid_config 和可选 target。状态通过 `feedback.status` 快照发布，UI 从快照更新卡片。

反馈面板新增时从本地递增计数器生成 `wN`，自动跳过当前状态快照和卡片中已有的 ID；加载配置后的已有编号不会阻止继续新增。分配后计数器继续递增，因此在下一次状态刷新前连续新增也不会复用刚分配的编号。界面仍禁止多个 worker 订阅同一测量项；worker 数量没有固定的 12 个上限。

## 生命周期与保护

- start：设为 running，重置 PID、错误趋势和计数，建立 sender 对象。
- pause：设为 paused，保留 PID 状态。Manager 仍将测量值分发给 paused worker，刷新 last_value / last_error；不执行 PID 或发送。
- resume：恢复 running，清除自动暂停原因与趋势窗口。
- stop/remove：设为 idle，关闭 sender 并释放连接池引用。
- `_processing` 防止重入发送；发送失败或异常后冷却 5 秒，再有数据才会继续尝试。
- 当 max_error_ratio>0 且目标绝对值>1e-9，单帧相对误差超限则跳过并增加 frames_skipped；目标接近零时不执行此相对保护。
- 每 trend_window 次成功反馈比较窗口首末误差绝对值，末误差增大则自动暂停。trend_window=0 禁用；等待人工恢复，不自动恢复。

## PID 数学

误差为 `preset_value - measured_value`。`step(value)` 在 `abs(error)<deadband` 时返回 None；否则计算 `kp*error + ki*sum(有限误差窗口) + kd*(error-last_error)`。I 项限制到 ±i_limit，最终输出限制到 ±output_limit。

这里是逐测量步计算，没有乘/除采样时间 dt，也不需要等窗口填满。PidConfig 默认 kp=0.03、ki=kd=0、i_limit=output_limit=0.1、window_size=10、deadband=0、max_error_ratio=0.30、trend_window=5；preset_value 必填。数学必须留在 PidController，保护与运行状态留在 Worker。

## 目标设备与发送行为

| target | 默认端口 | sender 与远端行为 |
| --- | --- | --- |
| None | — | 计算并记录 delta，不发送 |
| Ad9910Target | 3251 | [Ad9910Sender](../../scope/io/ad9910_sender.py)：频率路径计算绝对频率；幅度路径调用服务端 adjust_amplitude(device_id, profile, delta)，由服务端做增量和限幅 |
| RtmqTarget | 18861 | [RtmqSender](../../scope/io/rtmq_sender.py)：set_sideband(card_index, sbg_channel, delta)，delta 直接作为目标值；真实服务契约仍需验证 |
| DACTarget | 18863 | [DACSender](../../scope/io/dac_sender.py)：read() 获取 b_voltage，叠加 delta*scale 并限至 0–5 V，默认 set_ab(0, new_voltage) |

DAC 默认调用会同时将 A 通道设为 0 V。sender 的 `use_set_ab=False` 改用 set_b；这是构造参数，不是当前 DACTarget/UI 字段，不能理解为远端失败后自动回退。每次调整重新读取设备电压；读与写不是远端原子事务，并发外部写入仍可能竞争。

反馈面板允许 Kp/Ki/Kd 在 ±10000、DAC scale 在 ±1000 范围输入。负增益或负 scale 可反转反馈方向，二者同时翻转则相互抵消；方向应按实际被控系统确认。

v0.9.3 的 DAC 读取优先使用 `b_voltage`；该字段为 None 时回退解析 `device_response` 中的 `B=540mV` 等串口回显，支持 mV/V，避免误匹配 `ZEROB`。非 success 响应、非法电压或无法解析时不发送，由 worker 执行失败冷却；这与 `use_set_ab` 的发送方式选择相互独立。

## 比值反馈（v0.9.2）

`MeasurementSpec(tag="ratio", feature="Ratio", numerator_tag="m1", denominator_tag="m2")` 将两个不同标量测量项的同帧比值作为被控量；不能自引用或引用另一个比值。处理器先算标量，再算比值，源缺失、NaN 或分母绝对值小于 `1e-9` 时，将原因写入 `FittedSnapshot.invalid_reasons[tag]`，不产出该比值。

Manager 将无效原因交给 `FeedbackWorker.report_invalid()`：RUNNING 转为 PAUSED，`stop_reason` 为“输入无效: <原因>”；PAUSED 只刷新原因，IDLE 忽略。该帧不执行 PID 或发送。用户 resume 后清空原因与趋势窗口；若下一帧仍无效会再次暂停。有效帧仍按原有死区、误差保护、趋势检测和冷却规则处理。

测量面板选择「比值」后显示分子/分母下拉，只列标量项；源被删时保留原选择并标注缺失，源改名时刷新候选。比值行隐藏自身通道，默认分母与分子不同，留空名称时自动生成 A/B。配置保存/加载包含两个源 tag，旧标量配置仍兼容。

目标值填写期望比值（无量纲）；反馈极性可翻转增益或 DAC scale 其中之一。比值离目标较远时可能触发默认 `max_error_ratio=0.30` 的单帧保护。非 DAC 链路选择比值时 UI 给出非阻断提示；本次离线测试不构成任何链路的物理闭环验收。

## 连接池与持久化

[RpycConnectionPool / ConnectionPoolManager](../../scope/io/rpyc_pool.py) 按 ip/port 共享池、引用计数，最后引用释放时关闭；max_size 限制连接数，min_size 与 idle_timeout 尚未实现保底和回收。同步建连/RPC 放到 executor。发送返回成功表示调用路径成功，不等于物理闭环验收。

目标显式用 target_to_dict / target_from_dict 转换，JSON 中包含类型标记 Ad9910Target / RtmqTarget / DACTarget。Manager 的 get_config/load_config 显式处理 PidConfig、target 和 worker 配置，ConfigManager 使用 feedback_workers 字段保存，恢复通过 load_batch 命令路径应用。

## 验证入口

现有测试：[PID](../../tests/test_pid_controller.py)、[Worker](../../tests/test_feedback_worker.py)、[Manager](../../tests/test_feedback_manager.py)、[命令](../../tests/test_feedback_command_worker.py)、[反馈面板](../../tests/test_feedback_panel.py)、[AD9910](../../tests/test_ad9910_sender.py)、[RTMQ](../../tests/test_rtmq_sender.py)、[DAC](../../tests/test_dac_sender.py)、[连接池](../../tests/test_rpyc_pool.py)。新增 [比值测量回归](../../tests/test_ratio_measurement.py) 覆盖同帧计算、分母保护与无效暂停，[测量面板回归](../../tests/test_measurement_panel.py) 覆盖比值控件、候选、配置往返及非 DAC 提示；DAC 回归包含读取回退解析。离线回归与实机证据分开见 [NOW](../current/NOW.md)；未完成事项只在 [待办](../status/ACTIVE_TODO.md) 维护。旧 Slot 方案见 [归档索引](../archive/INDEX.md)。
