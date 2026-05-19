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

## Phase 3 — UI 界面 (已完成 ✅)

### 最终产出

```
scope/ui/
├── main_window.py          # 主窗口控制器 + 跨线程 pyqtSignal
├── main_window.ui          # Qt Designer 布局: 波形上 / 配置下
├── waveform_view.py        # pyqtgraph 波形 + 右上角图例 + 点击切换
└── panels/
    ├── channel_panel.py    # 4 通道复选框/档位/耦合/探头 (同步波形)
    ├── device_panel.py     # 设备设置 (替代触发Tab, 2列布局)
    ├── measurement_panel.py    # 动态测量行 (名称+通道+时间窗口)
    ├── feedback_panel.py   # 反馈 slot 管理 + 暂停/继续 + 自动暂停
    ├── feedback_dialog.ui  # 添加/编辑 slot 对话框
    └── art_config_dialog.py    # (旧, 可清理)
```

### 与原始设计的关键变更

| 项 | 原始设计 | 最终实现 |
|----|---------|---------|
| 布局 | 左波形 + 右侧面板 | **上波形 + 下配置**, waveformContainer stretch=3 |
| 触发 | 独立的"触发"Tab | 合并入 **设备设置** Tab (device_panel.py) |
| 测量 | 固定表格 (行×通道) | **动态行**: 每行独立选名称/通道/测量项/时间段 |
| 波形图例 | 无 | **右上角图例**, 点击切换显隐, 隐藏变灰 |
| 通道数 | 4 (ai0:3) | **16 (ai0:15)**, 颜色循环 16 色 |
| 通道控制 | 无 | 复选框实时切换波形显隐, 图例同步, 默认全部开启 |
| 采集帧率 | 33ms (~30fps) | **500ms** (匹配 0.5s 帧时长, FINITE 模式) |

---

## Phase 4 — ART 硬件集成 (已完成 ✅)

### 说明

基于 artdaq (NI-DAQmx 兼容) 库实现了 `ArtDevice`, 
实际硬件已到货并完成端到端验证。

### 硬件实测结果

| 项目 | 验证结果 |
|------|---------|
| DLL 加载 | `Art_DAQ.dll` (923 KB) 位于 `C:\Program Files (x86)\ART Technology\ArtDAQ\Lib\x64`, `os.add_dll_directory()` 加载 |
| 设备名 | **Dev42**, `ArtDAQ_GetDeviceAttribute` 可用但属性码不兼容, 实际通信正常 |
| 通道数 | **16 通道** (ai0~ai15), `ai0:15` |
| 采样率上限 | **31250 Sa/s** (16 通道), 默认设为 **30000 Sa/s** |
| 触发信号 | 模拟触发 `cfg_anlg_edge_start_trig` 工作正常, 默认 ai12 上升沿 1V |
| 有限点采集 | FINITE 模式 + `rearm()` 重建 Task 实现帧循环 |
| 超时行为 | USB 断开后 `read()` 抛 `DaqError`, 上层捕获为 `TimeoutError` |

### 关键架构变更

| 项 | 原始设计 | 最终实现 |
|----|---------|---------|
| 采集模式 | CONTINUOUS (连续) | **FINITE** (有限点) + 硬件触发 |
| 帧循环 | QTimer 定时读取 | QTimer + **rearm()** (重建 Task 重新触发) |
| 设备切换 | 先建新设备→验证→停旧设备 | **先停旧设备→关 Task→建新设备→失败恢复旧设备** |
| 默认通道 | 4 通道 (ai0:3) | **16 通道 (ai0:15)** |
| 默认采样率 | 10 kSa/s | **30 kSa/s** |
| 硬件触发 | 无 (软件触发) | **ai12, 上升沿 1V** (默认开启) |
| 波形渲染 | 全点渲染 (30k/帧) | **自动降采样** (~1500 点/通道) |
| Mock 模式 | 无 | `python main.py --mock` 使用模拟器 |

### 已知限制

| 限制 | 说明 | 计划 |
|------|------|------|
| 触发源固定 | 默认 ai12, 1V, 上升沿, 需 UI 支持修改 | Phase 5 |
| 单通道速率 | 16 通道共享 ADC 转换时间, 通道越多单通道速率越低 | 架构限制 |
| rearm 重建开销 | 每帧重建整个 Task (~数 ms), 不适合 1kHz+ 触发 | 优化方向 |

---

## Phase 5 — 打磨与扩展 (持续)

### 可能的后续方向

| 方向 | 说明 | 优先级 |
|------|------|--------|
| 触发源 UI 配置 | 当前触发源 (ai12/1V/上升沿) 硬编码, 需 UI 支持修改 | 🔴 高 |
| 单点/连续模式切换 | 当前仅 FINITE 模式, 需 UI 支持 CONTINUOUS | 🟡 中 |
| 更多触发类型 | 脉宽触发, 逻辑触发, 视频触发 | 🟢 低 |
| 协议解码 | UART / I2C / SPI / CAN 软件解码 | 🟢 低 |
| 数学通道增强 | 积分, 微分, 对数, 指数 | 🟢 低 |
| 预设场景 | 保存/加载示波器配置 (通道设置, 触发条件, 反馈目标) | 🟡 中 |
| 数据回放 | 加载 HDF5 文件 → 模拟实时采集 | 🟢 低 |
| 脚本化 | Python 脚本接口, 用户自定义分析逻辑 | 🟢 低 |
| REST API | FastAPI 提供远程查询状态/获取当前波形快照 | 🟢 低 |
| 打包发布 | PyInstaller / Nuitka 打包为独立 exe | 🟡 中 |

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
