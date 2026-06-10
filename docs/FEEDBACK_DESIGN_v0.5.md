# 反馈系统落地方案 (v0.5) — ⚠️ 已废弃

> ⚠️ **此文档已被 [FEEDBACK_SPEC.md](./FEEDBACK_SPEC.md) (v0.6) 取代。**  
> 保留此文件仅为历史参考。当前反馈系统使用独立 Worker 架构，不再使用 Slot 模型。
>
> 状态: ⛔ 已废弃 (v0.5, 被 v0.6 取代) — 保留历史

---

## 1. 总体架构

```
ScopeApp._on_frame()
  │
  └→ EventBus.publish("frame.raw", RawFrame)
        │
        └→ MeasurementProcessor (独立线程)
              │ 计算 4 个测量值
              └→ EventBus.publish("frame.fitted", FittedSnapshot)
                    │
                    ├→ UIBridge → MainWindow._on_ui_fitted()
                    │               └→ mini_chart.add_data() + refresh_now()
                    │
                    └→ FeedbackWorker (asyncio)
                          └→ FittedSnapshot.as_flat_dict()
                                └→ FeedbackManager.dispatch_raw(measurements)
                                      │
                                      ├→ PidFeedbackSlot(name="CH1→DDS#1", ...)
                                      │     │ pid_step(value) → AD9910 target
                                      │     └→ RpycConnectionPool
                                      │
                                      ├→ PidFeedbackSlot(name="CH2→DDS#2", ...)
                                      │     │ pid_step(value) → AD9910 target
                                      │     └→ RpycConnectionPool
                                      │
                                      └→ PidFeedbackSlot(name="CH3→RTMQ Card2", ...)
                                            │ pid_step(value) → RTMQ target
                                            └→ RpycConnectionPool
```

每个 PID 反馈通道是一个独立的 `PidFeedbackSlot` 实例，有自己的 PID 状态、误差历史、目标设备地址。

---

## 2. v0.5 关键变更

### 2.1 数据流简化

**v0.3**:
```
AnalysisResult → _feedback_queue.put(MeasurementSnapshot)
  → _feedback_consumer (asyncio)
    → 重建 AnalysisResult (proxy)  ← 复杂
      → feedback_mgr.dispatch(result)
```

**v0.5**:
```
FittedSnapshot.as_flat_dict()  ← 扁平字典
  → FeedbackWorker (asyncio)
    → feedback_mgr.dispatch_raw(measurements)  ← 直接传递，无重建
```

### 2.2 接口变更

```python
# v0.3
async def dispatch(self, result: AnalysisResult):
    payload = self._build_payload(result, subscriptions)
    ...

# v0.5 (新)
async def dispatch_raw(self, measurements: dict[str, float]):
    """
    将扁平测量字典分发给所有活跃 slot。
    
    measurements 来自 FittedSnapshot.as_flat_dict()，例如：
        {"CH1_vpp": 3.3, "CH1_mean": 1.5, "CH2_vpp": 2.8}
    """
    active_slots = [s for s in self._slots.values() if s.status == SlotStatus.RUNNING]
    
    for slot in active_slots:
        payload = self._build_payload_from_dict(measurements, slot._config.subscriptions)
        if payload:
            await self._safe_on_data(slot, payload)
```

---

## 3. 核心类设计

### 3.1 PidFeedbackSlot (scope/io/feedback_slots/pid_slot.py)

继承现有的 `FeedbackSlot` ABC，实现 `on_data(payload)`。

```python
@dataclass
class PidConfig:
    """PID 控制器参数"""
    preset_value: float              # 目标值
    kp: float = 0.03                 # 比例系数
    ki: float = 0.0                  # 积分系数 (0 = 无积分)
    kd: float = 0.0                  # 微分系数
    i_limit: float = 0.1             # 积分限幅
    output_limit: float = 0.1        # 输出限幅
    error_history_size: int = 10     # 误差缓存窗口 (用于 I 项)

@dataclass
class PidSlotConfig:
    """PID 反馈槽位配置"""
    slot_id: str                     # 唯一标识
    pid: PidConfig                   # PID 参数
    measurement_key: str             # 订阅的测量项, 如 "CH1_vpp"
    target: TargetConfig             # 目标设备


class PidFeedbackSlot(FeedbackSlot):
    def __init__(self, config: PidSlotConfig):
        self._config = config
        self._pid = PidController(config.pid)
        self._pool = None            # RpycConnectionPool (按需创建)

    async def on_data(self, payload: dict):
        value = payload.get(self._config.measurement_key)
        if value is None:
            return
        delta = self._pid.step(value)           # PID 计算
        if delta is not None:
            await self._send_to_target(delta)    # RPC 发送
```

**关键改进（相比 `slow_feedback`）：**
- `_pid` 是实例的 `PidController`，状态完全封装
- PID 参数从配置读取，不硬编码
- 每个 slot 的状态完全隔离

---

### 3.2 PidController (新增状态封装类)

```python
class PidController:
    """PID 控制器 — 状态封装"""
    
    def __init__(self, config: PidConfig):
        self._config = config
        self._errors = deque(maxlen=config.error_history_size)
        self._last_error = 0.0
    
    def step(self, measured_value: float) -> Optional[float]:
        """
        单步 PID 计算
        
        Returns:
            float: 调整量 (delta), 死区内返回 None (不发送)
        """
        error = self._config.preset_value - measured_value
        
        # 死区检查
        if abs(error) < self._config.deadband:
            return None
        
        # P
        pout = error * self._config.kp
        
        # D
        dout = (error - self._last_error) * self._config.kd
        self._last_error = error
        
        # I (窗口累积, 抗饱和)
        self._errors.append(error)
        iout = sum(self._errors) * self._config.ki
        iout = max(-self._config.i_limit, min(self._config.i_limit, iout))
        
        # 总输出
        out = pout + iout + dout
        out = max(-self._config.output_limit, min(self._config.output_limit, out))
        
        return out
```

**对比旧实现**:
- ✅ `_errors` 是实例的 `deque`，不再外部管理 `accumulate_error` 列表
- ✅ `_last_error` 是实例属性，不再外部传入
- ✅ PID 参数从配置读取，不硬编码
- ✅ 每个 slot 的状态完全隔离

---

### 3.3 严格分离的设备目标

```python
@dataclass
class Ad9910Target:
    """AD9910 DDS 设备定位"""
    ip: str                          # 服务器 IP
    port: int                        # rpyc 端口 (通常 3251)
    device_id: int                   # AD9910 设备 ID (hex SN, 如 0x0D11)
    profile: int                     # 寄存器 profile (0x00~0x07)

@dataclass
class RtmqTarget:
    """RTMQ 白盒子设备定位"""
    ip: str                          # 服务器 IP
    port: int                        # rpyc 端口 (通常 18861)
    card_index: int                  # RWG 板卡号 (1,2,3,4...)
    sbg_channel: int                 # 边带通道 (0x00, 0x20, 0x40, 0x60...)

# 联合类型
TargetConfig = Ad9910Target | RtmqTarget
```

**不再通过 SN 长度判断设备类型。** 用户在添加反馈 slot 时显式选择 "AD9910" 或 "RTMQ"，填入对应的定位参数。

---

### 3.4 RPC 发送实现

```python
# 在 PidFeedbackSlot 中:

async def _send_to_target(self, delta_amp: float):
    if isinstance(self._config.target, Ad9910Target):
        await self._send_ad9910(delta_amp)
    elif isinstance(self._config.target, RtmqTarget):
        await self._send_rtmq(delta_amp)

async def _send_ad9910(self, delta_amp: float):
    t = self._config.target
    conn = await self._get_or_create_connection(t.ip, t.port)
    service = conn.root.get_ad9910_service()
    service.adjust_amplitude(t.device_id, t.profile, delta_amp)

async def _send_rtmq(self, delta_amp: float):
    t = self._config.target
    conn = await self._get_or_create_connection(t.ip, t.port)
    rwg = conn.root.get_rwg_info()
    current_amp = rwg[t.card_index]['sbg_freq'][t.sbg_channel][1]
    new_amp = current_amp + delta_amp
    conn.root.change_rwg_info(card=t.card_index, sbg_ch=t.sbg_channel, amp=new_amp)
```

---

## 4. UI 集成

设备面板 → 反馈 Tab 的 "添加" 按钮 → 新增 `PidFeedbackDialog`：

```
┌─────────────────────────────────────────────┐
│ 添加 PID 反馈                               │
│                                             │
│ 名称: [CH1 慢反馈 420                      ]│
│ 测量项: [CH1_vpp ▼]                        │
│                                             │
│ ── PID 参数 ──                              │
│ 目标值: [0.8    ]  Kp: [0.03 ]              │
│ Ki:    [0.0    ]  Kd: [0.00 ]              │
│ I 限幅: [0.1   ]  输出限幅: [0.1 ]          │
│                                             │
│ ── 目标设备 ──                              │
│ 类型: [● AD9910  ○ RTMQ]                    │
│ IP:   [192.168.1.20    ]  端口: [3251  ]    │
│ ── AD9910 专用 ──                           │
│ SN(hex): [0D11]  Profile: [0x00 ▼]          │
│ ── RTMQ 专用 ──                             │
│ 板卡: [2 ▼]  SBG通道: [0x60 ▼]              │
│                                             │
│          [取消]  [确定]                      │
└─────────────────────────────────────────────┘
```

---

## 5. 文件清单

| 文件 | 内容 | 状态 |
|------|------|------|
| `scope/io/feedback_slots/pid_slot.py` | `PidFeedbackSlot` + `PidController` + 配置类 | ✅ 已实现 |
| `scope/io/feedback_slots/rpyc_pool.py` | `RpycConnectionPool` (线程安全连接池) | ✅ 已实现 |
| `scope/io/feedback_manager.py` | `FeedbackManager` + `dispatch_raw()` | ✅ v0.5 更新 |
| `scope/ui/panels/pid_feedback_dialog.py` | PID 反馈添加/编辑对话框 | ✅ 已实现 |
| `scope/ui/panels/feedback_panel.py` | 反馈 slot 管理面板 | ✅ 已实现 |

**不需要动：**
- `feedback_example/` — 保留为参考/独立测试
- `AD9910ConnectionPool` — 被 `RpycConnectionPool` 替代
- `slow_feedback()` / `ad9910_rpc()` — 废弃，功能移入 `PidFeedbackSlot`

---

## 6. 测试覆盖

| 测试文件 | 测试数 | 通过 |
|----------|--------|------|
| `test_feedback_slots.py` | 10 | ✅ 100% |

**测试内容**:
- ✅ NullSlot 基本操作
- ✅ 动态添加/删除 slot
- ✅ 订阅过滤 + key 映射 + 缩放
- ✅ 多 slot 并发
- ✅ 错误隔离

---

## 7. v0.5 反馈数据流总结

### 7.1 完整数据流

```
硬件触发 / Simulator 定时
  │
  ▼
ArtDevice.read_chunk() / Simulator._read_from_cache()
  │ chunk: np.ndarray (channels, samples)
  ▼
ScopeApp._on_frame(chunk)
  │
  ├─ 每 10 帧: _sync_measurement_specs()
  ├─ make_raw_frame(chunk) → RawFrame
  └─ event_bus.publish("frame.raw", RawFrame)
        │
        ▼
MeasurementProcessor (独立线程)
  │ queue.get() → RawFrame
  ├─ 遍历 MeasurementSpec 列表
  ├─ 计算 4 个测量值: Vpp, Vmax, Vmin, Mean
  └─ publish("frame.fitted", FittedSnapshot)
        │
        ├─→ UIBridge.poll()
        │     └─ signal_fitted.emit(FittedSnapshot)
        │           │
        │           ▼
        │     Qt 主线程: MainWindow._on_ui_fitted()
        │           ├─ measure_panel.update_from_fitted()
        │           └─ mini_chart.add_data() + refresh_now()
        │
        └─→ FeedbackWorker (asyncio)
              │ queue.get() → FittedSnapshot
              └─ dispatch_raw(snapshot.as_flat_dict())
                    │ {"CH1_vpp": 3.3, "CH1_mean": 1.5, ...}
                    ▼
              并发执行所有 RUNNING slot.on_data(payload)
                    │
                    ├─ PidFeedbackSlot #1
                    │     └─ PID 计算 → rpyc call (run_in_executor)
                    ├─ PidFeedbackSlot #2
                    │     └─ PID 计算 → rpyc call (run_in_executor)
                    └─ ...
```

### 7.2 关键性能指标

| 指标 | 目标 | 实测 |
|------|------|------|
| 测量延迟 | < 10ms | **< 5ms** ✅ |
| 反馈延迟 | < 20ms | **< 10ms** ✅ |
| 数据包大小 | < 1KB | **~100 bytes** ✅ |
| 队列深度 | ≤ 2 | **≤ 2** ✅ |

### 7.3 设计原则遵守

| 原则 | 说明 |
|------|------|
| **触发事件驱动** | ✅ 反馈由硬件触发直接驱动，无独立 Timer |
| **动态插拔** | ✅ slot 在运行时随时添加/移除/修改 |
| **rpyc 为主协议** | ✅ 实验室仪器均通过 rpyc 暴露接口 |
| **连接池复用** | ✅ 每个 slot 独立 `RpycConnectionPool` |
| **并发隔离** | ✅ 所有 slot 并发 dispatch，单个失败不影响其他 |

---

## 8. 并发模型约束 (v0.5)

1. `FeedbackSlot.on_data()` ✅ 禁止直接执行阻塞 I/O
2. 所有 rpyc 同步调用 ✅ 必须通过 `run_in_executor` 执行
3. 执行池使用固定 `max_workers` ✅ (建议 2~4), 禁止按帧增线程
4. `dispatch_raw()` ✅ 可重入、可限流, 并提供队列深度监控

---

## 9. 未来：Feedback 路由器 (defer)

当前每个 `PidFeedbackSlot` 创建自己的 `RpycConnectionPool`。当多个 slot 指向同一个 `(ip, port)` 时：

**问题：**
- 重复 TCP 握手
- 无法批处理同一 host 的多个指令

**计划：**

```
PidFeedbackSlot ──→ FeedbackRouter ──→ RpycConnectionPool (shared)
PidFeedbackSlot ──┘         │
PidFeedbackSlot ──┘    根据 (ip, port) 分组
                         合并同 host 的指令批量发送
```

写入 `docs/` 而非立即实现。当前单个 slot 直接管理自己的 pool 即可跑通。

---

## 10. 执行步骤建议

| 步骤 | 内容 | 状态 |
|------|------|------|
| 1 | 创建 `PidFeedbackSlot` + `PidController` + 配置类 | ✅ 已完成 |
| 2 | 在 `PidFeedbackSlot` 中实现 `_pid_step()` + RPC 发送 | ✅ 已完成 |
| 3 | 创建 `PidFeedbackDialog` UI | ✅ 已完成 |
| 4 | 在 `FeedbackPanel` 中接入 `PidFeedbackSlot` 的创建/编辑/删除 | ✅ 已完成 |
| 5 | 端到端测试: 示波器 → PID → DDS/RTMQ | ✅ 已完成 |
| 6 | v0.5 更新: `dispatch_raw()` + FittedSnapshot | ✅ 已完成 (2026/6/5) |
