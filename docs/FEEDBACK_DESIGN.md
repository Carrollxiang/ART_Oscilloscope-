# 反馈系统落地方案

> 状态: ✅ 已实现 (v0.3) — 详细设计见 [ARCHITECTURE.md](./ARCHITECTURE.md)

---

## 1. 总体架构

```
ScopeApp._on_frame()
  │
  ├→ Pipeline.process(result)
  │
  └→ FeedbackManager.dispatch(result)
       │
       ├→ PidFeedbackSlot(name="CH1→DDS#1", ...)
       │     │ pid_step(value) → AD9910 target
       │     └→ RpycConnectionPool
       │
       ├→ PidFeedbackSlot(name="CH2→DDS#2", ...)
       │     │ pid_step(value) → AD9910 target
       │     └→ RpycConnectionPool  (另一个 slot 共享同一个 host)
       │
       └→ PidFeedbackSlot(name="CH3→RTMQ Card2", ...)
             │ pid_step(value) → RTMQ target
             └→ RpycConnectionPool
```

每个 PID 反馈通道是一个独立的 `PidFeedbackSlot` 实例，有自己的 PID 状态、误差历史、目标设备地址。

---

## 2. 核心类设计

### 2.1 PidFeedbackSlot (scope/io/feedback_slots/pid_slot.py)

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
    measurement_key: str             # 订阅的测量项, 如 "CH1_Vpp"
    target: TargetConfig             # 目标设备


class PidFeedbackSlot(FeedbackSlot):
    def __init__(self, config: PidSlotConfig):
        self._config = config
        self._pid = config.pid
        self._errors = deque(maxlen=self._pid.error_history_size)  # 误差历史
        self._last_error = 0.0
        self._pool = None            # RpycConnectionPool (按需创建)
        # 每个 slot 创建自己的 connection pool
        # Future: 共享 pool 由 FeedbackRouter 管理

    async def on_data(self, payload: dict):
        value = payload.get(self._config.measurement_key)
        if value is None:
            return
        out = self._pid_step(value)           # PID 计算
        await self._send_to_target(out)        # RPC 发送

    def _pid_step(self, value: float) -> float:
        """单步 PID 计算 (状态封装在实例内)"""
        error = self._pid.preset_value - value
        self._errors.append(error)
        # P
        pout = error * self._pid.kp
        # D
        dout = (error - self._last_error) * self._pid.kd
        self._last_error = error
        # I (窗口累积, 抗饱和)
        iout = sum(self._errors) * self._pid.ki
        iout = max(-self._pid.i_limit, min(self._pid.i_limit, iout))
        # 总输出
        out = pout + iout + dout
        out = max(-self._pid.output_limit, min(self._pid.output_limit, out))
        return out

    async def _send_to_target(self, out: float):
        """根据 target 类型分发到 AD9910 或 RTMQ"""
        ...
```

**关键改进（相比 `slow_feedback`）：**
- `_errors` 是实例的 `deque(maxlen=N)`，不再外部管理 `accumulate_error` 列表
- `_last_error` 是实例属性，不再外部传入
- PID 参数从配置读取，不硬编码
- 每个 slot 的状态完全隔离

---

### 2.2 严格分离的设备目标

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

### 2.3 RPC 发送实现

```python
# 在 PidFeedbackSlot 中:

async def _send_to_target(self, out: float):
    if isinstance(self._config.target, Ad9910Target):
        await self._send_ad9910(out)
    elif isinstance(self._config.target, RtmqTarget):
        await self._send_rtmq(out)

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

## 3. UI 集成

设备面板 → 反馈 Tab 的 "添加" 按钮 → 新增 `PidFeedbackDialog`：

```
┌─────────────────────────────────────────────┐
│ 添加 PID 反馈                               │
│                                             │
│ 名称: [CH1 慢反馈 420                      ]│
│ 测量项: [CH1_Vpp ▼]                        │
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

## 4. 文件清单

| 文件 | 内容 |
|------|------|
| `scope/io/feedback_slots/pid_slot.py` | `PidFeedbackSlot` + `PidConfig` + `Ad9910Target` + `RtmqTarget` |
| `scope/io/feedback_slots/rpyc_pool.py` | 已有 `RpycConnectionPool`，保持不变或微调 |
| `scope/ui/panels/pid_feedback_dialog.py` | PID 反馈添加/编辑对话框 |
| `scope/ui/panels/feedback_panel.py` | 扩展以支持创建 `PidFeedbackSlot` |

**不需要动：**
- `feedback_example/` — 保留为参考/独立测试
- `AD9910ConnectionPool` — 被 `RpycConnectionPool` 替代
- `slow_feedback()` / `ad9910_rpc()` — 废弃，功能移入 `PidFeedbackSlot`

---

## 5. 未来：Feedback 路由器 (defer to docs)

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

## 6. 执行步骤建议

| 步骤 | 内容 | 依赖 |
|------|------|------|
| 1 | 创建 `PidFeedbackSlot` + `PidConfig` + `Ad9910Target` + `RtmqTarget` | 无 |
| 2 | 在 `PidFeedbackSlot` 中实现 `_pid_step()` + `_send_ad9910()` + `_send_rtmq()` | 步骤 1 |
| 3 | 创建 `PidFeedbackDialog` UI | 步骤 2 |
| 4 | 在 `FeedbackPanel` 中接入 `PidFeedbackSlot` 的创建/编辑/删除 | 步骤 3 |
| 5 | 端到端测试: 示波器 → PID → DDS/RTMQ | 步骤 4 |
| 6 | 文档: 记录 FeedbackRouter 设计思路 (不实现) | 步骤 5 |

---

## 7. v0.4 反馈数据流重构 (实施中)

### 7.1 目标

1. 反馈闭环优先于 UI 刷新与 Mini Chart。
2. 订阅模型从“整通道测量”升级为“事件窗口语义值”。
3. 通过有界队列 + 背压策略避免任务堆积导致卡顿。

### 7.2 新订阅模型

新增结构化订阅键, 避免字符串歧义:

```python
@dataclass
class SubscriptionKey:
    source_type: str   # "event" | "raw" | "meta"
    key: str           # event tag / raw key / meta key
```

推荐使用:

- `event:A_power`
- `event:B_power`
- `raw:CH1_Vpp` (仅调试/监控)
- `meta:sequence_num`

说明:

- `event:*` 来自事件窗口处理结果, 用于反馈闭环。
- `raw:*` 来自整通道测量, 仅保留兼容。
- `meta:*` 为帧号、时间戳等元信息。

### 7.3 反馈优先数据流

```
AcqFrame
  ├─> EventWindowProcessor (高优先级)
  │     └─> FeedbackSnapshot
  │            └─> FeedbackQueue (maxsize=1~2, drop_oldest)
  │                   └─> FeedbackManager.dispatch()
  │
  └─> UIQueue (maxsize=1, drop_oldest)
         ├─> 主波形更新
         └─> MiniChartQueue (maxsize=1, 触发驱动渲染)
```

关键点:

- 反馈发送与 UI 渲染彻底解耦。
- 下游慢时优先丢旧帧, 保持反馈使用最新值。
- 控制命令 (保存/改参数/启停 slot) 走独立 `ControlQueue`。

### 7.4 队列与背压策略

| 队列 | maxsize | 满队列行为 | 备注 |
|------|---------|------------|------|
| `FeedbackQueue` | 1~2 | 丢最旧 (`drop_oldest`) | 闭环实时性优先 |
| `UIQueue` | 1 | 丢最旧 | UI 可容忍丢帧 |
| `MiniChartQueue` | 1 | 丢最旧 + 渲染节流 | 低优先级 |
| `ControlQueue` | 8 (可调) | 阻塞或显式拒绝 | 控制命令不可静默丢失 |

背压定义:

- 当发送/渲染处理能力低于采集速率时, 队列触发“丢旧保新/阻塞”等规则, 阻止延迟无限积累。

### 7.5 并发模型约束

1. `FeedbackSlot.on_data()` 禁止直接执行阻塞 I/O。
2. 所有 rpyc 同步调用必须通过 `run_in_executor` 执行。
3. 执行池使用固定 `max_workers` (建议 2~4), 禁止按帧增线程。
4. `dispatch()` 必须可重入、可限流, 并提供队列深度监控。

### 7.6 一致性规则 (测量面板 vs 反馈面板)

为解决“显示值与订阅值不一致”, 统一使用单一快照源:

```python
@dataclass
class MeasurementSnapshot:
    sequence_num: int
    raw_measurements: dict[str, float]
    event_measurements: dict[str, float]   # tag -> value
    timestamp: float
```

规则:

- 测量面板显示读取 `MeasurementSnapshot`。
- 反馈分发读取同一份 `MeasurementSnapshot`。
- 禁止“UI再算一份、反馈再算一份”的双路径计算。
