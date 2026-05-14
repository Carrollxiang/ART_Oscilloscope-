# 实施路线图

> **总体策略**: 每阶段产出是可独立运行验证的增量，不依赖硬件到位。数据模型先行 → 反馈系统验证 → 信号分析 → UI 界面 → 硬件集成。

---

## Phase 0 — 项目骨架 + 数据模型 (Day 1)

### 目标

搭起项目目录结构，定义所有核心数据类，写一个模拟设备产生测试数据。**硬件开发期间即可开展此阶段**。

### 产出物

```
scope/
├── pyproject.toml
├── scope/
│   ├── __init__.py
│   ├── main.py                  # 控制台入口, 打印 AnalysisResult
│   ├── model/
│   │   ├── __init__.py
│   │   ├── analysis_result.py   # AnalysisResult, ChannelData, TriggerInfo
│   │   └── enums.py
│   ├── hardware/
│   │   ├── __init__.py
│   │   ├── device.py            # AcquisitionDevice (ABC)
│   │   └── simulator.py         # SimulatorDevice 生成正弦波/方波/三角波
│   └── acquisition/
│       ├── __init__.py
│       ├── ring_buffer.py       # 每通道的循环缓冲区
│       └── watchdog.py          # Watchdog 接口定义 + 状态机
└── tests/
    ├── test_simulator.py
    └── test_ring_buffer.py
```

### 验证标准

```
$ python -m scope.main
[INFO] SimulatorDevice started: 4ch @ 1MSa/s
[INFO] Frame #1  t=0.000s  CH1 Vpp=2.00  Freq=1000.0
[INFO] Frame #2  t=0.001s  CH1 Vpp=2.00  Freq=1000.0
...
```

---

## Phase 1 — 反馈系统核心 (已完成 ✅)

### 目标

实现 FeedbackManager + FeedbackSlot 框架，以 **rpyc** 为主协议（实验室仪器标准），配合连接池复用解决"运行时无法增删改"的老问题。

### 实际产出

```
scope/io/
├── feedback_manager.py          # FeedbackManager (asyncio 调度器)
├── feedback_slots/
│   ├── base.py                  # FeedbackSlot ABC + 数据订阅模型
│   ├── rpyc_slot.py             # ✅ rpyc 协议实现 (主协议)
│   ├── rpyc_pool.py             # ✅ 线程安全连接池
│   └── null_slot.py             # ✅ 调试用 (只打日志)

tests/test_feedback_slots.py     # 19 tests, all pass
```

### 已通过的测试

| 测试 | 说明 |
|------|------|
| 基本分发 | 5 帧数据全部送达 NullSlot ✅ |
| 订阅过滤 | 只发送订阅的 key, 未订阅的不发 ✅ |
| key 映射 + 缩放 | local_key → remote_key, scale 正确 ✅ |
| sequence_num 订阅 | 元信息字段可订阅 ✅ |
| **动态添加** | 运行中添加 slot → 下一帧开始接收 ✅ |
| **动态删除** | 运行中删除 slot → 后续帧不再接收 ✅ |
| 多 slot 并发 | 5 个 slot 同时运行, 各收 10 帧 ✅ |
| 错误隔离 | 异常 slot 不干扰其他 slot ✅ |
| **连接池超时** | 连不存在的服务器 → TimeoutError ✅ |
| **全流程集成** | Simulator → Pipeline → FeedbackManager → NullSlot ✅ |

### 关键架构决定

- **rpyc 替代 raw socket**: 实验室仪器均通过 rpyc 暴露接口，纯 socket 不友好
- **连接池池化**: 每个 slot 一个 `RpycConnectionPool`（min=1, max=4），避免反复握手
- **事件驱动**: `on_data` 由 dispatch 调用，无独立 Timer → 零过反馈
- **异步桥接**: rpyc 同步调用通过 `run_in_executor` 桥接到 asyncio，不阻塞主循环

---

## Phase 2 — 处理管道 (下一阶段)

### 前置条件

Phase 0 (数据模型 + 模拟器) 和 Phase 1 (反馈系统) 已完成。
本阶段与 UI 界面 (Phase 3) **可并行开发**, 无代码依赖。

### 目标

实现信号分析 Pipeline 框架和核心测量功能。输出结果将经由 Phase 1 的订阅模型反馈给远程仪器。

### 产出物

```
scope/processing/
├── __init__.py
├── pipeline.py          # Pipeline 框架: 责任链模式
├── measurements.py      # 自动测量: Vpp, Vmax, Vmin, Vrms, Freq, Period, DutyCycle
├── math_ops.py          # CH1 ±×÷ CH2, 反相, 绝对值
├── fft.py               # rFFT + 频谱峰值搜索
└── filters.py           # 低通/高通/带通 (scipy.signal, 可选)
```

### 测量功能清单

| 测量项 | 实现 | 精度目标 |
|--------|------|---------|
| Vpp (峰峰值) | `np.ptp()` | 100% 准确 (理论值) |
| Vmax | `np.max()` | 100% |
| Vmin | `np.min()` | 100% |
| Vrms | `np.sqrt(np.mean(sq))` | 100% |
| Freq (频率) | 过零检测 → 平均周期 | ±1% (对整数周期信号) |
| Period | Freq 倒数 | ±1% |
| Duty Cycle | 脉宽/周期 | ±2% |
| Positive/Negative Width | 上升沿到下降沿 | ±1 采样点 |

### 验证方式

SimulatorDevice 输出已知参数的标准波形：
- 1kHz 正弦波 1Vpp → Pipeline 输出 Freq=1000Hz, Vpp=1.0V
- 1kHz 方波 3.3Vpp 50% 占空比 → DutyCycle=50%
- 1kHz + 5kHz 叠加 → FFT 两个峰值位置正确

---

## Phase 3 — UI 界面 (Day 7~10)

### 目标

基于 PyQt6 + pyqtgraph 搭建完整的示波器桌面界面。

### 产出物

```
scope/ui/
├── main_window.py          # 主窗口: 标题栏 + 菜单 + Dock 布局
├── waveform_view.py        # pyqtgraph PlotWidget + 多条曲线
├── channel_panel.py        # 每通道: 开关/垂直档位/探头比/耦合/颜色
├── trigger_panel.py        # 触发源/电平/斜率/模式
├── measurement_panel.py    # 实时测量读数列表
├── feedback_panel.py       # 反馈通道列表 + 添加/编辑/删除按钮
└── status_bar.py           # 采样率/帧数/状态指示
```

### 界面布局

```
┌─────────────────────────────────────────────────────────────────┐
│  ██ 数字示波器 v0.1    [文件] [视图] [帮助]                      │
├──────────────────────────────────┬──────────────────────────────┤
│                                  │  通道         反馈            │
│  ┌────────────────────────────┐ │  ┌─CH1 ☑ 1V/d──┐ ┌────────┐  │
│  │                            │ │  │  CH2 ☑ 2V/d  │ │UDP     │  │
│  │      波形显示区域           │ │  │  CH3 ☐       │ │  ● 运行│  │
│  │                            │ │  │  MATH1 ☐     │ │Modbus  │  │
│  │  时间/格  触发位置标记      │ │  └──────────────┘ │  ○ 停止│  │
│  │  光标(可选)                │ │  ┌──────────────┐ │ [+ 添] │  │
│  │                            │ │  │  触发设置     │ └────────┘  │
│  └────────────────────────────┘ │  │ 源:CH1 电平:0V│             │
│  ┌────────────────────────────┐ │  │ 斜率:上升     │             │
│  │  测量读数                   │ │  └──────────────┘             │
│  │  CH1 Vpp: 3.30V  1.000kHz │ │                                │
│  │  CH2 Vrms: 1.15V          │ │                                │
│  └────────────────────────────┘ │                                │
├──────────────────────────────────┴──────────────────────────────┤
│  采样率: 1.0MSa/s  帧#: 142  状态: 正常运行                     │
└─────────────────────────────────────────────────────────────────┘
```

### 验证标准

- SimulatorDevice 运行 → UI 显示 4 通道实时滚动波形
- 切换通道开关 → 波形显示/隐藏
- 调整通道垂直档位 → 波形缩放正确
- 触发面板设置 → 触发位置标记正确移动
- 添加一个 UDP 反馈 → Wireshark 确认有数据包发出
- 关闭反馈 → 数据包停止

---

## Phase 4 — ART 硬件集成 (Day 11~13)

### 目标

替换 SimulatorDevice 为真实的 `ArtUsbDevice`。

### 前提条件

ART 采集卡固件已就绪, USB 数据包格式已确定。

### 产出物

```
scope/hardware/
├── art_usb.py               # ArtUsbDevice: pyusb 驱动
├── art_protocol.py          # 数据包解析: 原始 bytes → numpy array
└── art_constants.py         # USB VID/PID, 端点地址, 命令字
```

### 需要与硬件确定的接口

| 项目 | 说明 |
|------|------|
| USB VID / PID | 设备识别 |
| 端点配置 | Bulk IN 端点号, 最大包大小 |
| 数据包格式 | 包头结构, 通道数据排列, 是否存在校验和 |
| 触发信息 | 硬件触发标记如何嵌入数据流? (单独的端点? 数据包内标记位?) |
| 采样率控制 | 通过哪个命令字设置? |
| 通道使能 | 如何开关通道? |
| 探活命令 | CMD_PING 的实现定义 |
| 硬重置 | USB 级 reset 是否需要额外握手? |

### 验证标准

- 插入 ART 卡 → 软件自动识别 → 显示设备信息
- 开始采集 → 实时波形显示 (与 SimulatorDevice 时期 UI 表现一致)
- Watchdog: 断开 USB → 观察自动重连流程 → 恢复采集

---

## Phase 5 — 打磨与扩展 (持续)

### 可能的后续方向

| 方向 | 说明 |
|------|------|
| 更多触发类型 | 脉宽触发, 逻辑触发, 视频触发 |
| 协议解码 | UART / I2C / SPI / CAN 软件解码 |
| 数学通道增强 | 积分, 微分, 对数, 指数 |
| 预设场景 | 保存/加载示波器配置 (通道设置, 触发条件, 反馈目标) |
| 数据回放 | 加载 HDF5 文件 → 模拟实时采集 |
| 脚本化 | Python 脚本接口, 用户自定义分析逻辑 |
| REST API | FastAPI 提供远程查询状态/获取当前波形快照 |
| 打包发布 | PyInstaller / Nuitka 打包为独立 exe |

---

## 各阶段依赖关系图

```
Phase 0: 数据模型 + 模拟器
    │
    ├──→ Phase 1: 反馈系统 (不依赖 UI 和 Pipeline)
    │        │
    │        └──→ 可独立验证 "运行时动态增删改 slot"
    │
    ├──→ Phase 2: 处理管道 (不依赖 UI)
    │        │
    │        └──→ 可独立验证 "测量精度"
    │
    └──→ Phase 3: UI 界面 (依赖 Phase 0, 1, 2)
             │
             └──→ 完整的可交互桌面示波器
                      │
                      └──→ Phase 4: 替换真实硬件
                               │
                               └──→ 最终产品
```

Phase 1 和 Phase 2 可以并行开发, 因为它们没有直接的代码依赖。
