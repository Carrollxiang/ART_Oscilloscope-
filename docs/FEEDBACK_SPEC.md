# 反馈系统架构规范 (v0.6)

> 状态: ✅ 已实现 (v0.6)  
> 最后更新: 2026/6/5

---

## 1. 设计目标

| 目标 | 说明 |
|------|------|
| **大规模反馈** | 支持 10+ 个独立反馈通道 |
| **独立 worker** | 每个反馈 worker 独立运行 |
| **PID 封装** | Worker 内部持有 PidController |
| **高效订阅** | 共享 EventBus 订阅，避免重复数据传递 |
| **错误隔离** | 单个 worker 异常不影响其他 worker |

---

## 2. 架构概览

### 2.1 数据流

```
FittedSnapshot (测量结果)
  │
  ↓
EventBus (frame.fitted topic)
  │
  ↓
**1 个共享订阅** → FeedbackManager._dispatch_worker()
  │
  ├─ snapshot.as_flat_dict()  ← 只调用 1 次
  │
  └─ 并发分发给所有 worker
        │
        ├─→ FeedbackWorker_1
        │     ├─ 提取 "CH1_vpp" 值
        │     ├─ PidController.step(value)
        │     └─ 发送调整指令 (Null/AD9910/RTMQ)
        │
        ├─→ FeedbackWorker_2
        │     └─ ...
        │
        └─→ asyncio.gather()  ← 并发执行
```

### 2.2 组件层次

```
┌─────────────────────────────────────────────────────────┐
│              FeedbackManager (生命周期管理)             │
│  - 持有唯一的 EventBus 订阅                             │
│  - 管理 worker 生命周期                                 │
│  - 并发分发数据                                         │
└───────────────────┬─────────────────────────────────────┘
                    │
        ┌───────────┼───────────┬─────────────┐
        │           │           │             │
        ↓           ↓           ↓             ↓
  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
  │ Worker_1 │ │ Worker_2 │ │ Worker_3 │ │ Worker_N │
  ├──────────┤ ├──────────┤ ├──────────┤ ├──────────┤
  │PidControl│ │PidControl│ │PidControl│ │PidControl│
  │  _errors │ │  _errors │ │  _errors │ │  _errors │
  │  deque(10)│ │  deque(10)│ │  deque(10)│ │  deque(10)│
  ├──────────┤ ├──────────┤ ├──────────┤ ├──────────┤
  │Target:   │ │Target:   │ │Target:   │ │Target:   │
  │ AD9910   │ │ RTMQ     │ │ Null     │ │ AD9910   │
  └──────────┘ └──────────┘ └──────────┘ └──────────┘
```

---

## 3. 核心组件设计

### 3.1 PidController（独立组件）

**文件**: `scope/runtime/pid_controller.py`

**职责**:
- 封装 PID 计算逻辑
- 管理误差历史（有限窗口）
- 提供单步计算接口

**配置**:

```python
@dataclass
class PidConfig:
    """PID 参数配置"""
    preset_value: float          # 目标值
    kp: float = 0.03             # 比例系数
    ki: float = 0.0              # 积分系数 (0 = 无积分)
    kd: float = 0.0              # 微分系数
    i_limit: float = 0.1         # 积分限幅（抗饱和）
    output_limit: float = 0.1    # 输出限幅
    window_size: int = 10        # 误差窗口大小
    deadband: float = 0.0        # 死区（|error| < deadband 返回 None）
```

**接口**:

```python
class PidController:
    """PID 控制器 — 无状态封装"""
    
    def __init__(self, config: PidConfig):
        self._config = config
        self._errors = deque(maxlen=config.window_size)
        self._last_error = 0.0
    
    def step(self, measured_value: float) -> Optional[float]:
        """
        单步 PID 计算
        
        Args:
            measured_value: 当前测量值
            
        Returns:
            float: 调整量 delta
            None: 在死区内或窗口未满
        
        假设:
            - 固定帧间隔（不考虑 dt）
            - 窗口未满时仍可计算，但 I 项可能不准确
        """
    
    def reset(self):
        """重置状态（用于重新启动）"""
        self._errors.clear()
        self._last_error = 0.0
    
    @property
    def metrics(self) -> dict:
        """运行时指标"""
        return {
            "errors_count": len(self._errors),
            "last_error": self._last_error,
        }
```

**计算公式**:

```
error = preset_value - measured_value

P = Kp * error
I = Ki * Σerrors[0...n]  （窗口内所有误差，限幅）
D = Kd * (error - last_error)

output = P + I + D （限幅）
```

**关键设计**:
- ✅ **有限窗口**: `deque(maxlen=window_size)` 自动丢弃旧误差
- ✅ **积分限幅**: 防止积分饱和
- ✅ **固定帧间隔**: 假设帧间隔固定（500ms），不考虑 dt
- ✅ **死区支持**: 小误差时不输出（避免频繁调整）

---

### 3.2 FeedbackWorker（独立反馈单元）

**文件**: `scope/io/feedback_worker.py`

**职责**:
- 接收测量值
- 调用 PID 计算
- 发送调整指令

**配置**:

```python
@dataclass  
class FeedbackConfig:
    """反馈 worker 配置"""
    worker_id: str               # 唯一标识符
    measurement_key: str         # 订阅的测量项 key，如 "CH1_vpp"
    pid_config: PidConfig        # PID 控制器参数
    target: Optional[TargetConfig] = None  # 目标设备配置（v0.6 暂不实现）
```

**接口**:

```python
class FeedbackWorker:
    """独立反馈 worker"""
    
    def __init__(self, config: FeedbackConfig):
        self._config = config
        self._pid = PidController(config.pid_config)
        self._status = SlotStatus.IDLE
        self._target = config.target
    
    @property
    def worker_id(self) -> str:
        return self._config.worker_id
    
    @property
    def status(self) -> SlotStatus:
        return self._status
    
    async def start(self):
        """启动 worker"""
        self._status = SlotStatus.RUNNING
        self._pid.reset()
        logger.info(f'FeedbackWorker "{self.worker_id}" started')
    
    async def stop(self):
        """停止 worker"""
        self._status = SlotStatus.IDLE
        logger.info(f'FeedbackWorker "{self.worker_id}" stopped')
    
    async def pause(self):
        """暂停 worker（保留 PID 状态）"""
        self._status = SlotStatus.PAUSED
    
    async def resume(self):
        """恢复 worker"""
        self._status = SlotStatus.RUNNING
    
    async def process(self, value: float):
        """
        处理单个测量值
        
        由 FeedbackManager 调用，传入已提取的测量值。
        """
        if self._status != SlotStatus.RUNNING:
            return
        
        try:
            # PID 计算
            delta = self._pid.step(value)
            
            # 发送调整指令
            if delta is not None and self._target:
                await self._send_to_target(delta)
                
        except Exception as e:
            logger.error(f'FeedbackWorker "{self.worker_id}" error: {e}')
    
    async def _send_to_target(self, delta: float):
        """发送调整指令到目标设备（v0.6 不实现）"""
        # TODO: v0.7 实现 AD9910 / RTMQ 目标发送
        logger.debug(f'Worker "{self.worker_id}" delta={delta:.6f}')
        pass
```

**关键设计**:
- ✅ **被动接收**: 不主动订阅 EventBus，由 Manager 调用
- ✅ **无队列管理**: Manager 统一管理订阅和分发
- ✅ **状态管理**: IDLE / RUNNING / PAUSED / ERROR
- ✅ **目标设备扩展**: 预留接口，v0.7 实现

---

### 3.3 FeedbackManager（简化版调度器）

**文件**: `scope/io/feedback_manager.py`（重写）

**职责**:
- ✅ 持有唯一的 EventBus 订阅
- ✅ 管理 worker 生命周期
- ✅ 并发分发数据给所有 worker
- ❌ 删除旧的 Slot 管理逻辑

**接口**:

```python
class FeedbackManager:
    """反馈管理器 — 数据分发 + 生命周期管理"""
    
    def __init__(self, event_bus: EventBus):
        self._event_bus = event_bus
        self._workers: dict[str, FeedbackWorker] = {}
        self._queue = event_bus.subscribe("frame.fitted")  # 唯一订阅
        self._lock = asyncio.Lock()
        self._running = False
    
    # ── 生命周期 ─────────────────────────────────────────────
    
    async def start(self):
        """启动管理器（开始分发协程）"""
        self._running = True
        asyncio.create_task(self._dispatch_loop())
        logger.info("FeedbackManager started")
    
    async def stop(self):
        """停止管理器"""
        self._running = False
        await self.stop_all_workers()
        logger.info("FeedbackManager stopped")
    
    # ── Worker 管理 ───────────────────────────────────────────
    
    async def add_worker(self, config: FeedbackConfig) -> str:
        """添加反馈 worker"""
        worker = FeedbackWorker(config)
        
        async with self._lock:
            self._workers[config.worker_id] = worker
        
        await worker.start()
        logger.info(f'FeedbackWorker "{config.worker_id}" added')
        return config.worker_id
    
    async def remove_worker(self, worker_id: str):
        """移除反馈 worker"""
        async with self._lock:
            worker = self._workers.pop(worker_id, None)
        
        if worker:
            await worker.stop()
            logger.info(f'FeedbackWorker "{worker_id}" removed')
    
    async def pause_worker(self, worker_id: str):
        """暂停指定 worker"""
        worker = self._workers.get(worker_id)
        if worker:
            await worker.pause()
    
    async def resume_worker(self, worker_id: str):
        """恢复指定 worker"""
        worker = self._workers.get(worker_id)
        if worker:
            await worker.resume()
    
    async def stop_all_workers(self):
        """停止所有 worker"""
        async with self._lock:
            for worker in self._workers.values():
                await worker.stop()
    
    # ── 配置管理 ───────────────────────────────────────────────
    
    def get_config(self) -> list[dict]:
        """导出所有 worker 配置（用于保存）"""
        return [
            {
                "worker_id": w.worker_id,
                "measurement_key": w._config.measurement_key,
                "pid_config": dataclasses.asdict(w._config.pid_config),
                "target": None,  # v0.7 实现
            }
            for w in self._workers.values()
        ]
    
    async def load_config(self, config: list[dict]):
        """加载配置（重建所有 worker）"""
        # 清空现有 worker
        await self.stop_all_workers()
        async with self._lock:
            self._workers.clear()
        
        # 重新创建
        for item in config:
            pid_config = PidConfig(**item["pid_config"])
            worker_config = FeedbackConfig(
                worker_id=item["worker_id"],
                measurement_key=item["measurement_key"],
                pid_config=pid_config,
                target=None,
            )
            await self.add_worker(worker_config)
    
    # ── 数据分发 ───────────────────────────────────────────────
    
    async def _dispatch_loop(self):
        """分发协程：订阅 → 提取 → 并发分发"""
        while self._running:
            try:
                snapshot = self._queue.get_nowait()
                if snapshot is not None:
                    # 只调用一次 as_flat_dict()
                    flat = snapshot.as_flat_dict()
                    
                    # 并发分发给所有 worker
                    tasks = []
                    async with self._lock:
                        for worker in self._workers.values():
                            if worker.status == SlotStatus.RUNNING:
                                value = flat.get(worker._config.measurement_key)
                                if value is not None:
                                    tasks.append(worker.process(value))
                    
                    if tasks:
                        await asyncio.gather(*tasks, return_exceptions=True)
                
                await asyncio.sleep(0)  # 让出控制权
                
            except Exception as e:
                logger.error(f"FeedbackManager dispatch error: {e}")
                await asyncio.sleep(0.1)
    
    # ── 状态查询 ───────────────────────────────────────────────
    
    def list_workers(self) -> list[dict]:
        """列出所有 worker 状态"""
        return [
            {
                "worker_id": w.worker_id,
                "status": w.status.value,
                "measurement_key": w._config.measurement_key,
            }
            for w in self._workers.values()
        ]
```

**关键设计**:
- ✅ **唯一订阅**: 只订阅一次 `frame.fitted`
- ✅ **预过滤**: 只调用一次 `as_flat_dict()`
- ✅ **并发分发**: `asyncio.gather()` 并发调用所有 worker
- ✅ **配置管理**: 支持导出/加载配置

---

## 4. 目标设备接口（v0.7 预留）

### 4.1 目标配置

```python
@dataclass
class Ad9910Target:
    """AD9910 DDS 设备定位"""
    ip: str
    port: int = 3251
    device_id: int        # hex SN, 如 0x0D11
    profile: int          # 寄存器 profile (0x00~0x07)

@dataclass
class RtmqTarget:
    """RTMQ 白盒子设备定位"""
    ip: str
    port: int = 18861
    card_index: int       # RWG 板卡号
    sbg_channel: int      # 边带通道

TargetConfig = Ad9910Target | RtmqTarget
```

### 4.2 连接池（v0.7）

**每个 worker 内部持有连接池**:

```python
class FeedbackWorker:
    def __init__(self, config: FeedbackConfig):
        self._connection_pool = None  # v0.7 实现
    
    async def _ensure_connection(self):
        """确保连接池已创建"""
        if self._connection_pool is None and self._target:
            # 根据 target 创建 RpycConnectionPool
            pass
```

---

## 5. 配置持久化

### 5.1 JSON 结构

```json
{
  "feedback_workers": [
    {
      "worker_id": "CH1_voltage_control",
      "measurement_key": "CH1_vpp",
      "pid_config": {
        "preset_value": 3.3,
        "kp": 0.03,
        "ki": 0.01,
        "kd": 0.0,
        "i_limit": 0.1,
        "output_limit": 0.1,
        "window_size": 10,
        "deadband": 0.01
      },
      "target": null
    },
    {
      "worker_id": "CH2_voltage_control",
      "measurement_key": "CH2_vpp",
      "pid_config": { ... },
      "target": null
    }
  ]
}
```

### 5.2 集成到 ConfigManager

**文件**: `scope/config/settings.py`

**修改**:

```python
@staticmethod
def save_to_file(main_window, filepath: str) -> bool:
    config = {}
    
    # 保存通道配置
    if hasattr(main_window, 'channel_panel'):
        config['channels'] = main_window.channel_panel.get_config()
    
    # 保存设备配置
    if hasattr(main_window, 'device_panel'):
        config['device'] = main_window.device_panel.get_config()
    
    # 保存测量配置
    if hasattr(main_window, 'measure_panel'):
        config['measurements'] = main_window.measure_panel.get_measurement_specs()
    
    # 保存反馈配置（新增）
    if hasattr(main_window, 'feedback_manager'):
        config['feedback_workers'] = main_window.feedback_manager.get_config()
    
    # 保存到文件
    ...

@staticmethod
def load_from_file(main_window, filepath: str) -> bool:
    # ... 加载其他配置 ...
    
    # 加载反馈配置（新增）
    if 'feedback_workers' in config and hasattr(main_window, 'feedback_manager'):
        await main_window.feedback_manager.load_config(config['feedback_workers'])
```

---

## 6. 性能考虑

### 6.1 EventBus 订阅数量

| 场景 | 订阅数量 | 队列数量 | publish 开销 |
|------|---------|---------|-------------|
| **v0.5（旧）** | N 个 worker | N 个 | O(N) 遍历 |
| **v0.6（新）** | 1 个共享 | 1 个 | O(1) |

### 6.2 as_flat_dict() 调用次数

| 场景 | 每帧调用次数 |
|------|-------------|
| **v0.5（旧）** | N 次（每个 worker） |
| **v0.6（新）** | 1 次（Manager） |

### 6.3 并发性

| 场景 | 并发机制 |
|------|---------|
| **v0.5（旧）** | asyncio.gather() |
| **v0.6（新）** | asyncio.gather() |

**结论**: 并发性相同，但避免了重复开销。

---

## 7. 测试策略

### 7.1 单元测试

| 测试文件 | 测试内容 |
|----------|----------|
| `test_pid_controller.py` (✅ 11 tests) | PID 计算正确性、窗口限制、限幅、死区 |
| `test_feedback_worker.py` (✅ 15 tests) | Worker 生命周期、状态切换、process() 调用 |
| `test_feedback_manager.py` (✅ 16 tests) | Manager 生命周期、配置导入导出、并发分发 |

### 7.2 集成测试

| 测试场景 | 验证内容 |
|----------|----------|
| **Mock 模式** | 添加 10 个 feedback worker，验证并发运行 ✅ |
| **暂停/恢复** | 某个 worker 暂停不影响其他 worker ✅ |
| **错误隔离** | 某个 worker 抛异常不影响其他 worker ✅ |
| **配置保存/加载** | 保存后重新加载，验证 worker 恢复 ✅ |
| **名称变更同步** | 测量面板改名后反馈面板自动同步 ✅ |
| **重复订阅检测** | 同一测量项被两个 Worker 订阅时弹窗阻止 ✅ |

### 7.3 性能测试

| 测试场景 | 指标 |
|----------|------|
| **10 个 worker** | 分发延迟 < 5ms |
| **50 个 worker** | 分发延迟 < 20ms |
| **100 个 worker** | 分发延迟 < 50ms |

---

## 8. 版本规划

| 版本 | 状态 | 功能 |
|------|------|------|
| **v0.5** | ✅ 已完成 | FeedbackSlot + FeedbackManager（旧架构） |
| **v0.6** | **✅ 已实现** | **独立 worker + 共享订阅 + PID 封装 (当前版本)** |
| **v0.7** | 🔲 未来 | 目标设备实现（AD9910 / RTMQ） |
| **v0.8** | 🔲 未来 | 连接池 + 批量发送 |

---

## 9. 迁移路径（v0.5 → v0.6）

### 9.1 删除文件

- ~~`scope/io/feedback_slots/`~~（✅ 已删除）
- ~~`scope/io/feedback_worker.py`~~（✅ 已重写）

### 9.2 新建文件

- ~~`scope/runtime/pid_controller.py`~~（✅ 已新建）
- ~~`scope/io/feedback_worker.py`~~（✅ 已重写）
- ~~`scope/io/feedback_manager.py`~~（✅ 已重写）

### 9.3 修改文件

- ~~`scope/main.py`~~（✅ 已更新初始化）
- ~~`scope/config/settings.py`~~（✅ 已添加反馈配置保存/加载）

---

## 10. 参考文档

- [ARCHITECTURE.md](./ARCHITECTURE.md) - 系统架构
- [EVENTBUS_SPEC.md](./EVENTBUS_SPEC.md) - EventBus 规范
- [FEEDBACK_DESIGN.md](./FEEDBACK_DESIGN.md) - 旧版设计（将被替换）
