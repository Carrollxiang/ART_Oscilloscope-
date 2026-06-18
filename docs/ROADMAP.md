# 实施路线图

> **总体策略**: 每阶段产出是可独立运行验证的增量，不依赖硬件到位。数据模型先行 → 反馈系统验证 → UI 界面 → 硬件集成。
>
> **当前阅读入口**: 先看 [README.md](./README.md)。本文包含历史阶段记录，部分 v0.5 结构示例和测试数量不是最新状态。
>
> **当前基线**: v0.6 反馈 Worker 架构已实现；设备配置、测量规格、反馈 worker 命令已走 EventBus 控制面；`feedback.status` 与 `runtime.metrics` 状态面已接入；测试基线为 `85 passed`。

---

## Phase 0 — 项目骨架 + 数据模型 (Day 1) ✅

### 目标

搭起项目目录结构，定义所有核心数据类，写一个模拟设备产生测试数据。**硬件开发期间即可开展此阶段**。

### 产出物

```
scope/
├── pyproject.toml
├── scope/
│   ├── __init__.py
│   ├── main.py                  # 控制台入口
│   ├── model/
│   │   ├── __init__.py
│   │   └── enums.py             # 枚举定义
│   ├── hardware/
│   │   ├── __init__.py
│   │   ├── device.py            # AcquisitionDevice (ABC)
│   │   └── simulator.py         # SimulatorDevice
│   └── acquisition/
│       ├── __init__.py
│       ├── ring_buffer.py       # 环形缓冲区
│       └── watchdog.py          # Watchdog 接口定义
└── tests/
    ├── test_simulator.py
    └── test_ring_buffer.py
```

### 验证标准

```
$ python -m scope.main
[INFO] SimulatorDevice started: 16ch @ 30kSa/s
[INFO] Frame #1  seq=1 CH1 Vpp=2.00
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

tests/test_feedback_slots.py     # 19 tests, all pass (v0.3)
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

## Phase 2 — 处理管道 (已完成 ✅，v0.5 简化)

### 前置条件

Phase 0 (数据模型 + 模拟器) 和 Phase 1 (反馈系统) 已完成。

### v0.3 目标 → v0.5 实际实现

**v0.3 原计划**:
- 实现信号分析 Pipeline 框架和核心测量功能
- 责任链模式，每个通道可独立配置 Pipeline
- 12 个测量功能 (Vpp/Vrms/Freq/Period/DutyCycle...)

**v0.5 实际产出** (简化重构):
```
scope/runtime/
├── measurement_processor.py     # ✅ MeasurementProcessor (扁平执行)
├── measurement_spec.py          # ✅ MeasurementSpec (纯配置)
├── fitted_snapshot.py           # ✅ FittedSnapshot (测量结果)
└── event_bus.py                 # ✅ EventBus (数据路由)

# 删除 scope/processing/ 整个目录 (Pipeline, FFT, filters, math_ops)
```

### v0.5 测量功能清单 (精简)

| 测量项 | 实现 | 精度目标 |
|--------|------|---------|
| Vpp (峰峰值) | `np.ptp()` | 100% 准确 |
| Vmax | `np.max()` | 100% |
| Vmin | `np.min()` | 100% |
| Mean | `np.mean()` | 100% |

**已删除功能** (v0.3):
- ❌ **Vrms** (有效值) - 当前无需求
- ❌ **Freq** (频率) - 需过零检测算法
- ❌ **Period** (周期) - 依赖 Freq
- ❌ **DutyCycle** (占空比) - 需脉宽统计
- ❌ **FFT** (频谱) - 删除整个文件
- ❌ **滤波** (FIR/IIR) - 删除整个文件
- ❌ **数学运算** (CH1±CH2) - 删除整个文件

### 设计变更原因

| 项 | v0.3 设计 | v0.5 实现 | 原因 |
|----|-----------|-----------|------|
| 架构模式 | Pipeline 责任链 | MeasurementProcessor 扁平执行 | 降低复杂度，提高可维护性 |
| 数据流 | 多层 Stage 累积 | 单线程顺序计算 | 减少延迟 |
| 扩展性 | 高 (可插拔) | 低 (需修改代码) | 当前无复杂需求 |
| 代码量 | ~2000 行 | ~500 行 | **减少 75%** |

### 验证方式

v0.5 实测性能:
- ✅ 单帧测量延迟 < 5ms (对比帧周期 500ms, 占比 < 1%)
- ✅ 测试通过: 16/16 (test_phase0.py)

---

## Phase 3 — UI 界面 (已完成 ✅)

### 最终产出

```
scope/ui/
├── main_window.py          # 主窗口控制器 + 跨线程 pyqtSignal
├── main_window.ui          # Qt Designer 布局: 波形上 / 配置下
├── waveform_view.py        # pyqtgraph 波形 + 右上角图例 + 点击切换
├── mini_chart.py           # 迷你趋势图 (触发驱动)
└── panels/
    ├── channel_panel.py    # 16 通道复选框/档位
    ├── device_panel.py     # 设备设置 (4列布局)
    ├── measurement_panel.py    # 动态测量行 (名称+通道+时间窗口)
    └── feedback_panel.py   # 反馈 slot 管理
```

### 与原始设计的关键变更

| 项 | 原始设计 | 最终实现 |
|----|---------|---------|
| 布局 | 左波形 + 右侧面板 | **上波形 + 下配置** |
| 触发 | 独立 Tab | **设备设置** 4 列面板 (设备 \| 触发 \| 采集 \| 测试) |
| 测量 | 固定表格 | **动态行**: 名称+通道+测量项+ms时间窗 → 值 |
| 波形图例 | 无 | **2列图例** (colCount=2), sigSampleClicked 切换 |
| 通道数 | 4 | **16 (ai0:15)**, 2列网格, 逐通道电压量程 |
| 通道控制 | 垂直档位/耦合/探头 | **逐通道 min/max 电压量程** (硬件支持) |
| 采集驱动 | QTimer 轮询 | **register_done_event 事件驱动** (v0.3) |
| 反馈 | rpyc 通用推送 | **PID 闭环反馈** (AD9910/RTMQ 独立) |
| 采集帧率 | 33ms | 由**触发频率**决定 (非固定) |

### v0.5 新增修复

- ✅ MiniChart 刷新：添加 `refresh_now()` 调用
- ✅ 启动时同步 specs：确保第一帧就有测量值
- ✅ 每 10 帧同步 specs：降低配置同步开销

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

## Phase 5 — PID 反馈系统 (已完成 ✅)

### 实际产出

```
scope/io/feedback_slots/
├── pid_slot.py              # PidFeedbackSlot + PidController + Target dataclasses
scope/ui/panels/
│── pid_feedback_dialog.py   # AD9910/RTMQ PID 配置对话框
feedback_panel.py            # 扩展: + 添加 PID 按钮, idle/start/pause 三态

docs/FEEDBACK_DESIGN_v0.5.md # 旧设计方案归档
```

### 关键设计

| 项 | 说明 |
|----|------|
| AD9910 / RTMQ 分离 | `Ad9910Target(ip, port, device_id, profile)` vs `RtmqTarget(ip, port, card_index, sbg_channel)` |
| PID 状态封装 | `PidController` 类, deque 误差缓存, 死区, I 抗饱和 |
| 三态按钮 | 创建→IDLE, 点开始→RUNNING, 点暂停→PAUSED, 点继续→RUNNING |
| connection pool | 每个 slot 独立 `RpycConnectionPool` (min=1, max=2) |
| 语义名订阅 | local_key=CH1_Vpp (取值), remote_key=显示名 (payload key) |

---

## Phase 6 — EventBus + 数据模型重构 (已完成 ✅, 2026/6/5)

### 背景

v0.3 架构在高负载下存在:
- Pipeline 复杂度高，维护成本大
- AnalysisResult 数据包过重，包含未使用字段
- 测量功能过多（12个），部分无实际需求
- 数据流多层累积，延迟不稳定

### 目标

1. 简化数据模型：RawFrame 替代 AnalysisResult
2. 删除 Pipeline：改为扁平 MeasurementProcessor
3. 精简测量功能：只保留 4 个基本量
4. 统一事件驱动：Simulator 与 ART 接口一致

### 实际产出

```
scope/
├── model/
│   ├── __init__.py              # ✅ RawFrame (轻量数据模型)
│   └── enums.py                 # ✅ MeasurementFeature (只保留 4 个)
├── runtime/
│   ├── event_bus.py             # ✅ EventBus + BoundedQueue + DropStrategy
│   ├── measurement_processor.py # ✅ MeasurementProcessor (扁平执行)
│   ├── measurement_spec.py      # ✅ MeasurementSpec (纯配置)
│   └── fitted_snapshot.py       # ✅ FittedSnapshot
├── hardware/
│   ├── art_device.py            # ✅ 修改: make_raw_frame()
│   └── simulator.py             # ✅ 重写: 事件驱动 + 预生成帧
├── io/
│   └── feedback_manager.py      # ✅ 新增: dispatch_raw()
├── ui/
│   └── main_window.py           # ✅ 修复: MiniChart.refresh_now()
└── main.py                      # ✅ 重写: 统一事件驱动

# 删除文件
- scope/processing/ (整个目录)
- scope/model/analysis_result.py
- scope/runtime/fit_worker.py
- scope/runtime/measurement_snapshot.py
```

### 关键变更总结

| 项 | v0.3 | v0.5 | 影响 |
|----|------|------|------|
| **数据模型** | AnalysisResult (8字段) | RawFrame (4字段) | 减少传递开销 |
| **处理管道** | Pipeline 责任链 | MeasurementProcessor | 删除 2000+ 行代码 |
| **测量功能** | 12 个 | 4 个 | 聚焦核心需求 |
| **事件驱动** | Simulator 用 QTimer | 统一为 callback | 零轮询 |
| **代码量** | ~6000 行 | ~4000 行 | **减少 33%** |

### Bug 修复记录

| 问题 | 原因 | 修复 | 提交 |
|------|------|------|------|
| 小示波器初始无数据 | Processor 初始 specs=[] | 启动时同步 specs | 88bef81 |
| MiniChart 不更新 | 缺少 refresh_now() | 添加刷新调用 | fc5d6ae |
| 同步开销高 | 每帧都同步 UI 配置 | 每 10 帧同步一次 | f591c6e |

### 验收指标达成

| 指标 | 目标 | 实测 |
|------|------|------|
| 测量延迟 | < 10ms | **< 5ms** ✅ |
| 采集线程阻塞 | 0ms | **0ms** ✅ |
| 反馈延迟 | < 20ms | **< 10ms** ✅ |
| 测试通过率 | 100% | **45/45** ✅ |
| Mock 模式运行 | 正常 | **正常** ✅ |

---

## Phase 7 — 反馈系统架构重构 (核心已完成, v0.6)

### 目标

将反馈系统从 **Slot 架构** 重构为 **独立 Worker 架构**，支持大规模并发反馈。

### 核心变更

| 维度 | v0.5（当前） | v0.6（目标） |
|------|-------------|-------------|
| **架构模式** | Slot + Manager | Worker + Manager |
| **Worker 数量** | 1 个共享 | N 个独立 |
| **EventBus 订阅** | 1 个（共享） | 1 个（共享） |
| **PID 管理** | 未实现 | Worker 内部持有 |
| **并发机制** | asyncio.gather | asyncio.gather |
| **隔离性** | ❌ 单点风险 | ✅ 完全隔离 |

### 产出物

```
scope/
├── runtime/
│   ├── pid_controller.py       # ✅ 新建：独立 PID 组件
│   └── ...
│
├── io/
│   ├── feedback_manager.py     # 🔄 重写：简化为生命周期管理
│   ├── feedback_worker.py      # 🔄 重写：独立 worker
│   └── feedback_slots/         # ❌ 删除整个目录
```

### 详细规范

参见：[FEEDBACK_SPEC.md](./FEEDBACK_SPEC.md)

### 实施计划

参见：[FEEDBACK_TODO.md](./FEEDBACK_TODO.md)

### 预计工期

- **开发时间**: ~4 小时
- **测试验证**: ~1 小时
- **文档更新**: ~0.5 小时

---

## Phase 8 — 打磨与扩展 (持续)

### 可能的后续方向

| 方向 | 说明 | 优先级 |
|------|------|--------|
| 触发源 UI 配置 | 当前触发源 (ai12/1V/上升沿) 硬编码, 需 UI 支持修改 | 🔴 高 |
| 更多测量特征 | Freq, Period, DutyCycle (需过零检测算法) | 🟡 中 |
| 单点/连续模式切换 | 当前仅 FINITE 模式, 需 UI 支持 CONTINUOUS | 🟡 中 |
| 反馈目标实现 | AD9910 / RTMQ 实际设备发送 (v0.7) | 🔴 高 |
| 连接池管理 | 每个 worker 内部持有连接池 | 🟡 中 |
| 更多触发类型 | 脉宽触发, 逻辑触发, 视频触发 | 🟢 低 |
| 预设场景 | 保存/加载示波器配置 (通道设置, 触发条件, 反馈目标) | 🟢 低 |
| 数据回放 | 加载 HDF5 文件 → 模拟实时采集 | 🟢 低 |
| REST API | FastAPI 提供远程查询状态/获取当前波形快照 | 🟢 低 |
| 打包发布 | PyInstaller / Nuitka 打包为独立 exe | 🟡 中 |

---

## 各阶段依赖关系图

```
Phase 0: 数据模型 + 模拟器 ✅
    │
    ├──→ Phase 1: 反馈系统 ✅ (不依赖 UI 和 Pipeline)
    │        │
    │        └──→ 可独立验证 "运行时动态增删改 slot"
    │
    ├──→ Phase 2: 处理管道 ✅ (v0.5 简化为 MeasurementProcessor)
    │        │
    │        └──→ 可独立验证 "测量精度"
    │
    └──→ Phase 3: UI 界面 ✅ (依赖 Phase 0, 1, 2)
             │
             └──→ 完整的可交互桌面示波器
                      │
                      ├──→ Phase 4: 替换真实硬件 ✅
                      │        │
                      │        └──→ 硬件验证通过
                      │
                      ├──→ Phase 5: PID 反馈 ✅
                      │        │
                      │        └──→ 实验室仪器集成
                      │
                      └──→ Phase 6: 数据模型重构 ✅ (2026/6/5)
                               │
                               └──→ v0.5 架构简化完成
                                      │
                                      ├──→ Phase 7: 反馈系统重构 (规划中)
                                      │        │
                                      │        └──→ v0.6 独立 worker 架构
                                      │
                                      └──→ Phase 8: 持续优化 (进行中)
```

---

## 开发环境要求

### Python 版本
- **Python 3.10+** (已验证: 3.10.20)

### 虚拟环境
- ✅ 已创建 `.venv/` (Python 3.10.20)
- ✅ 使用清华镜像源: `https://pypi.tuna.tsinghua.edu.cn/simple`

### 核心依赖

| 包 | 版本要求 | 说明 |
|----|---------|------|
| PyQt6 | ≥6.5 | GUI 框架 |
| pyqtgraph | ≥0.13 | 波形渲染 (OpenGL) |
| numpy | ≥1.24 | 数值计算 |
| rpyc | ≥5.3 | 反馈 RPC 协议 |
| artdaq | 内置 | ART 采集卡驱动 |
| pytest | ≥9.0 | 测试框架 |

### 测试验证

```bash
# 运行所有测试
python -m pytest tests/ -v

# 期望结果
# 45 passed, 1 warning
```

---

## 快速启动

### Mock 模式 (无硬件)
```bash
# Windows
start_mock.bat

# 或直接运行
python -m scope.main --mock
```

### 硬件模式
```bash
# Windows
start.bat

# 或直接运行
python -m scope.main
```

---

## 版本历史

| 版本 | 日期 | 关键变更 |
|------|------|----------|
| v0.1 | 2026/5/15 | 初版架构，基础 UI |
| v0.2 | 2026/5/18 | 反馈系统基础 |
| v0.3 | 2026/5/21 | Pipeline + EventBus |
| v0.4 | 2026/6/4 | 文档更新尝试 |
| **v0.5** | **2026/6/5** | **数据模型重构 + Pipeline 删除** |
| **v0.6** | **2026/6/17** | **反馈 Worker 架构 + 设备配置控制面 EventBus 化** |

---

## 当前状态

- ✅ Phase 0-6 全部完成
- ✅ Phase 7 反馈 Worker 架构核心已实现
- ✅ 设备配置 UI 已发布 `config.change`，由 ConfigWorker 应用
- ✅ 测量规格 UI 已发布 `measurement.specs.changed`，由 MeasurementConfigWorker 应用
- ✅ 反馈控制 UI 已发布 `feedback.worker.command`，由 FeedbackCommandWorker 应用
- ✅ 反馈状态已发布 `feedback.status`，由 UIBridge 桥接到 FeedbackPanel / 状态栏
- ✅ 运行时指标已发布 `runtime.metrics`，供后续诊断面板消费
- ✅ 当前测试基线: 85/85 通过
