# 反馈系统重构 TODO

> 创建时间: 2026/6/5  
> 目标版本: v0.6  
> 预计工期: 2-3 小时

---

## 总体目标

将反馈系统从 **Slot 架构** 重构为 **独立 Worker 架构**，支持大规模并发反馈。

**关键变更**：
- ❌ 删除 `FeedbackSlot` 基类及相关文件
- ✅ 实现独立 `PidController` 组件
- ✅ 实现独立 `FeedbackWorker` 单元
- ✅ 重写 `FeedbackManager` 为简化版调度器
- ✅ 共享 EventBus 订阅，避免重复数据传递

---

## Phase 0: 清理旧代码（15 分钟）

### 0.1 删除 Slot 相关文件

**任务**:
- [ ] 删除 `scope/io/feedback_slots/` 整个目录
  - 包括 `base.py`, `null_slot.py`, `__init__.py`
  - 保留 `SlotStatus` 枚举（移到 `scope/model/enums.py`）

**验证**:
- [ ] 测试编译通过：所有 import 无错误

---

### 0.2 移动 SlotStatus 枚举

**任务**:
- [ ] 从 `scope/io/feedback_slots/base.py` 提取 `SlotStatus` 枚举
- [ ] 移动到 `scope/model/enums.py`（已存在）

**验证**:
- [ ] `from scope.model.enums import SlotStatus` 成功

---

## Phase 1: 实现 PidController（45 分钟）

### 1.1 创建文件

**文件**: `scope/runtime/pid_controller.py`

**任务**:
- [ ] 创建 `PidConfig` dataclass
- [ ] 实现 `PidController` 类
  - [ ] `__init__(self, config: PidConfig)`
  - [ ] `step(self, measured_value: float) -> Optional[float]`
  - [ ] `reset(self)`
  - [ ] `metrics` property

**关键实现点**:
- 使用 `deque(maxlen=window_size)` 存储误差历史
- 积分限幅：`max(-i_limit, min(i_limit, iout))`
- 输出限幅：`max(-output_limit, min(output_limit, out))`
- 死区检查：`if abs(error) < deadband: return None`

---

### 1.2 编写单元测试

**文件**: `tests/test_pid_controller.py`

**测试用例**:
- [ ] `test_pid_step_basic` - 单步计算正确性
- [ ] `test_pid_window_size` - 窗口满后自动丢弃旧数据
- [ ] `test_pid_i_limit` - 积分限幅生效
- [ ] `test_pid_output_limit` - 输出限幅生效
- [ ] `test_pid_deadband` - 死区返回 None
- [ ] `test_pid_reset` - 重置后状态清空

**验证**:
- [ ] `pytest tests/test_pid_controller.py -v` 全部通过

---

## Phase 2: 实现 FeedbackWorker（60 分钟）

### 2.1 创建文件

**文件**: `scope/io/feedback_worker.py`

**任务**:
- [ ] 创建 `FeedbackConfig` dataclass
  - `worker_id: str`
  - `measurement_key: str`
  - `pid_config: PidConfig`
  - `target: Optional[TargetConfig] = None`
- [ ] 实现 `FeedbackWorker` 类
  - [ ] `__init__(self, config: FeedbackConfig)`
  - [ ] `worker_id` property
  - [ ] `status` property
  - [ ] `start(self)` async
  - [ ] `stop(self)` async
  - [ ] `pause(self)` async
  - [ ] `resume(self)` async
  - [ ] `process(self, value: float)` async - 核心处理方法
  - [ ] `_send_to_target(self, delta: float)` async - v0.6 留空

**关键实现点**:
- Worker **不订阅** EventBus，被动接收数据
- `process()` 方法由 Manager 调用
- v0.6 阶段 `_send_to_target()` 只记录日志

---

### 2.2 编写单元测试

**文件**: `tests/test_feedback_worker.py`

**测试用例**:
- [ ] `test_worker_init` - 初始化配置正确
- [ ] `test_worker_start_stop` - 生命周期正常
- [ ] `test_worker_pause_resume` - 暂停/恢复状态切换
- [ ] `test_worker_process_running` - RUNNING 状态调用 PID
- [ ] `test_worker_process_paused` - PAUSED 状态不处理
- [ ] `test_worker_process_error` - 异常处理不崩溃

**验证**:
- [ ] `pytest tests/test_feedback_worker.py -v` 全部通过

---

## Phase 3: 重写 FeedbackManager（40 分钟）

### 3.1 重写文件

**文件**: `scope/io/feedback_manager.py`（完全重写）

**任务**:
- [ ] 删除旧代码（Slot 管理、dispatch_raw 等）
- [ ] 实现简化版 `FeedbackManager`
  - [ ] `__init__(self, event_bus: EventBus)`
  - [ ] 持有唯一的 EventBus 订阅：`self._queue = event_bus.subscribe("frame.fitted")`
  - [ ] `start(self)` async - 启动分发协程
  - [ ] `stop(self)` async - 停止管理器
  - [ ] `add_worker(self, config: FeedbackConfig)` async
  - [ ] `remove_worker(self, worker_id: str)` async
  - [ ] `pause_worker(self, worker_id: str)` async
  - [ ] `resume_worker(self, worker_id: str)` async
  - [ ] `stop_all_workers(self)` async
  - [ ] `get_config(self) -> list[dict]` - 导出配置
  - [ ] `load_config(self, config: list[dict])` async - 加载配置
  - [ ] `_dispatch_loop(self)` async - 核心分发协程
  - [ ] `list_workers(self) -> list[dict]`

**关键实现点**:
- **唯一订阅**: 只订阅一次 `frame.fitted`
- **预过滤**: `_dispatch_loop` 中只调用一次 `snapshot.as_flat_dict()`
- **并发分发**: 使用 `asyncio.gather()` 并发调用所有 worker
- **配置管理**: 支持导出/加载 JSON 配置

---

### 3.2 编写单元测试

**文件**: `tests/test_feedback_manager.py`

**测试用例**:
- [ ] `test_manager_init` - 初始化正确
- [ ] `test_manager_start_stop` - 生命周期正常
- [ ] `test_manager_add_remove_worker` - 添加/删除 worker
- [ ] `test_manager_pause_resume` - 暂停/恢复 worker
- [ ] `test_manager_config_export` - 导出配置正确
- [ ] `test_manager_config_import` - 导入配置正确
- [ ] `test_manager_dispatch_concurrent` - 并发分发正确

**验证**:
- [ ] `pytest tests/test_feedback_manager.py -v` 全部通过

---

## Phase 4: 集成到 ScopeApp（30 分钟）

### 4.1 修改主程序

**文件**: `scope/main.py`

**任务**:
- [ ] 删除旧的 `FeedbackWorker` 导入（如果存在）
- [ ] 更新 `FeedbackManager` 初始化
  - 传入 `event_bus` 参数
  - 在 `start()` 中调用 `await self.feedback_mgr.start()`
- [ ] 添加示例 worker（可选）
  ```python
  # 示例：添加 2 个反馈 worker
  from scope.runtime import PidConfig
  from scope.io import FeedbackConfig
  
  worker1 = FeedbackConfig(
      worker_id="CH1_voltage_control",
      measurement_key="CH1_vpp",
      pid_config=PidConfig(
          preset_value=3.3,
          kp=0.03,
          ki=0.01,
          window_size=10,
      ),
  )
  await self.feedback_mgr.add_worker(worker1)
  ```

---

### 4.2 更新 imports

**文件**: `scope/io/__init__.py`

**任务**:
- [ ] 导出新的类：`FeedbackWorker`, `FeedbackConfig`
- [ ] 删除旧的导出：`FeedbackSlot`, `SlotConfig` 等

---

### 4.3 集成测试

**任务**:
- [ ] 运行 Mock 模式，验证启动无错误
- [ ] 手动添加 worker，验证日志输出
- [ ] 测试暂停/恢复功能

**验证**:
- [ ] `python -m scope.main --mock` 正常启动
- [ ] 控制台日志显示 worker 启动信息

---

## Phase 5: 配置持久化（20 分钟）

### 5.1 修改 ConfigManager

**文件**: `scope/config/settings.py`

**任务**:
- [ ] 在 `save_to_file()` 中添加反馈配置保存
  ```python
  if hasattr(main_window, 'feedback_manager'):
      config['feedback_workers'] = main_window.feedback_manager.get_config()
  ```
- [ ] 在 `load_from_file()` 中添加反馈配置加载
  ```python
  if 'feedback_workers' in config and hasattr(main_window, 'feedback_manager'):
      await main_window.feedback_manager.load_config(config['feedback_workers'])
  ```

**注意**:
- `load_config` 是 async 方法，需要处理 async 调用

---

### 5.2 测试配置保存/加载

**任务**:
- [ ] 添加几个 worker
- [ ] 保存配置到文件
- [ ] 验证 JSON 包含 `feedback_workers` 部分
- [ ] 重新加载配置
- [ ] 验证 worker 恢复

---

## Phase 6: 文档更新（20 分钟）

### 6.1 更新架构文档

**文件**: `docs/ARCHITECTURE.md`

**任务**:
- [ ] 更新反馈系统章节
- [ ] 删除旧的 Slot 描述
- [ ] 添加新的 Worker 架构图

---

### 6.2 替换旧文档

**文件**: `docs/FEEDBACK_DESIGN.md`

**任务**:
- [ ] 删除或重命名为 `FEEDBACK_DESIGN_v0.5.md`（保留历史）
- [ ] 将 `FEEDBACK_SPEC.md` 作为新设计文档

---

## Phase 7: 最终验证（15 分钟）

### 7.1 运行所有测试

**任务**:
- [ ] `pytest tests/ -v` 全部通过
- [ ] 确保无 regressions

---

### 7.2 Mock 模式完整测试

**任务**:
- [ ] 启动 Mock 模式
- [ ] 添加 10 个 worker
- [ ] 验证所有 worker 并发运行
- [ ] 暂停/恢复个别 worker
- [ ] 保存/加载配置

---

### 7.3 性能测试（可选）

**任务**:
- [ ] 添加 50 个 worker
- [ ] 观察分发延迟
- [ ] 验证 < 20ms

---

## 时间估算

| Phase | 任务 | 预计时间 |
|-------|------|---------|
| Phase 0 | 清理旧代码 | 15 分钟 |
| Phase 1 | PidController | 45 分钟 |
| Phase 2 | FeedbackWorker | 60 分钟 |
| Phase 3 | FeedbackManager | 40 分钟 |
| Phase 4 | 集成到 ScopeApp | 30 分钟 |
| Phase 5 | 配置持久化 | 20 分钟 |
| Phase 6 | 文档更新 | 20 分钟 |
| Phase 7 | 最终验证 | 15 分钟 |
| **总计** | | **~4 小时** |

---

## 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| **async 配置加载** | ConfigManager.load_from_file 是同步方法 | 改为 async 或用 asyncio.run |
| **Worker ID 冲突** | 重复添加相同 ID | Manager 中检查并报错 |
| **EventBus 订阅清理** | Manager 停止后队列未清理 | 在 stop() 中清理队列 |
| **性能问题** | worker 数量 > 100 时延迟 | 渐进优化，v0.7 添加批处理 |

---

## 后续版本规划

### v0.7（下一版本）

- [ ] 实现 AD9910 目标设备
- [ ] 实现 RTMQ 目标设备
- [ ] 实现连接池（每个 worker 内部持有）
- [ ] 完善错误处理和重连机制

### v0.8（未来）

- [ ] 批量发送优化（相同 target 批处理）
- [ ] Web 界面监控
- [ ] 多级 PID（串级控制）

---

## 进度追踪

- [ ] Phase 0 完成
- [ ] Phase 1 完成
- [ ] Phase 2 完成
- [ ] Phase 3 完成
- [ ] Phase 4 完成
- [ ] Phase 5 完成
- [ ] Phase 6 完成
- [ ] Phase 7 完成

**当前状态**: 📝 规划完成，待执行
