# TODO

## P0 - 反馈优先与卡顿修复

- [ ] 为反馈链路引入有界队列 (`FeedbackQueue`, `maxsize=1~2`, `drop_oldest`)
- [ ] 为 UI 渲染引入有界队列 (`UIQueue`, `maxsize=1`, `drop_oldest`)
- [ ] 为 Mini Chart 引入有界队列 (`MiniChartQueue`, `maxsize=1`, `drop_oldest`)
- [ ] 将 `PidFeedbackSlot` 的阻塞 rpyc 调用改为 `run_in_executor`
- [ ] 给 `dispatch` 增加背压保护，避免 `run_coroutine_threadsafe` 无界堆积

## P1 - 数据一致性

- [ ] 引入 `MeasurementSnapshot` 作为测量与反馈的单一数据源
- [ ] 统一测量面板与反馈面板读取同一份 snapshot
- [ ] 订阅模型升级为结构化 key (`event:*`, `raw:*`, `meta:*`)
- [ ] 新增事件窗口测量模型 (`EventWindowSpec`)

## P2 - 交互流畅度

- [ ] Mini Chart 改为触发驱动更新（每次硬件触发最多更新一次）
- [ ] Mini Chart 在硬件/`mock` 模式均保持与主示波器同节拍（1触发=1更新）
- [ ] 去除 Mini Chart 独立刷新节拍 `QTimer`（仅保留采集事件驱动）
- [ ] Mini Chart 每次仅绘制最近 N 点（建议 300~1000）
- [ ] 参数修改/保存走 `ControlQueue`，在帧边界原子生效
- [ ] 错误弹窗限流（同类错误短时间内只提示一次）

## 验收指标

- [ ] 长时间运行反馈队列不持续堆积
- [ ] 反馈延迟不随时间增长
- [ ] 修改测量类型/时间参数流畅
- [ ] 保存配置无明显卡顿
- [ ] 同一订阅项在测量面板与反馈面板读数一致
