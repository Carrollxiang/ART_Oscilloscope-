# 技术栈与技术决策 (v0.5)

> 最后更新: 2026/6/5  
> Python 版本: 3.10.20
>
> **整理状态**: 本文保留 v0.5 技术决策背景，部分项目结构、反馈目录和测试数量已过期。当前文档入口见 [README.md](./README.md)，当前反馈系统见 [FEEDBACK_SPEC.md](./FEEDBACK_SPEC.md)。

## 1. 语言与运行时

| 项 | 选择 | 理由 |
|----|------|------|
| 语言 | **Python 3.10+** | 生态成熟, numpy/scipy 信号处理栈完整, asyncio 原生支持 |
| 包管理 | **pip** | 使用 requirements.txt + 清华镜像源 (https://pypi.tuna.tsinghua.edu.cn/simple) |
| 虚拟环境 | **.venv/** | Python 3.10.20, 已配置清华镜像源 |
| Python 分发 | embed Python (打包用) | 最终交付时可用 PyInstaller / Nuitka 打包成单文件 |

---

## 2. 核心框架

| 组件 | 选型 | 备选 | 理由 |
|------|------|------|------|
| GUI 框架 | **PyQt6** | PySide6 | PyQt6 的 GPL 许可对本项目无限制, pyqtgraph 对 PyQt6 支持成熟 |
| 波形渲染 | **pyqtgraph** | — | OpenGL 加速渲染, 自动降采样, 专为示波器类应用设计 |
| USB 通信 | **artdaq** | — | ART 采集卡官方驱动 (NI-DAQmx 兼容), 事件驱动 |
| 数值计算 | **numpy** | — | 波形数据处理的基础, 无替代品 |

**v0.5 删除的依赖**:
- ❌ **scipy.signal** — 滤波功能已删除
- ❌ **qasync** — 当前使用独立 asyncio 线程, 未引入 qasync

---

## 3. 反馈系统

| 协议 | 库 | 说明 | 状态 |
|------|----|------|------|
| **rpyc** | **rpyc** | 实验室仪器标准 RPC 协议，带连接池复用 | ✅ 已实现 |
| UDP | 标准库 socket | 零依赖, 最简实现 | 🔲 后续 |
| 串口 RS-232/485 | **pyserial** | 标准库, 跨平台 | 🔲 后续 |
| Modbus TCP | **pymodbus** | 工业自动化场景 | 🔲 后续 |

### rpyc 连接池

`RpycConnectionPool` 是反馈系统的核心基础设施：

| 特性 | 说明 |
|------|------|
| 线程安全 | `threading.Condition` + `Lock` 保护借还操作 |
| 温备 | `start()` 时预建 `min_size` 条连接 |
| 健康检查 | `acquire()` 时自动 ping，死连接从池中移除 |
| 超时保护 | `acquire_timeout` 防死等，`idle_timeout` 自动回收空闲连接 |
| 伸缩上限 | `max_size` 限制并发连接数，超限时 acquire 等待 |

所有反馈 slot 基于 asyncio 实现，rpyc 同步调用通过 `run_in_executor` 桥接。

---

## 4. 存储与记录

| 用途 | 选型 | 理由 |
|------|------|------|
| 配置文件 | **JSON** | 轻量级，易读，Python 标准库支持 |
| 运行时状态 | **dataclass** | Python 内置，类型安全 |
| 数据记录 | HDF5 (可选) | 未来可用于存储原始波形数据 |

---

## 5. 项目结构 (v0.5)

```
project-root/
├── main.py                     # 入口转发 (设置 DLL 路径 → 调用 scope.main.main)
├── pyproject.toml
├── requirements.txt
├── .gitignore
├── test_hardware.py            # ART 硬件诊断工具 (无 GUI)
├── start.bat                   # Windows 启动脚本 (硬件模式)
├── start_mock.bat              # Windows 启动脚本 (Mock 模式)
├── activate_env.bat            # 激活虚拟环境
│
├── artdaq/                     # ART 采集卡驱动 (NI-DAQmx 兼容封装, 已入库)
│
├── scope/                      # 主包
│   ├── __init__.py
│   ├── main.py                 # 应用入口 (ScopeApp + 参数解析)
│   │
│   ├── model/                  # 数据模型 (v0.5 简化)
│   │   ├── __init__.py         # ✅ RawFrame (轻量数据模型)
│   │   └── enums.py            # MeasurementFeature (4个基本量)
│   │
│   ├── hardware/               # 硬件抽象层
│   │   ├── __init__.py
│   │   ├── device.py           # AcquisitionDevice (ABC)
│   │   ├── simulator.py        # ✅ SimulatorDevice (事件驱动 + 预生成帧)
│   │   └── art_device.py       # ✅ ArtDevice (ART USB 卡)
│   │
│   ├── runtime/                # v0.5 核心运行时
│   │   ├── __init__.py
│   │   ├── event_bus.py        # ✅ EventBus + BoundedQueue + DropStrategy
│   │   ├── measurement_processor.py  # ✅ MeasurementProcessor (扁平执行)
│   │   ├── measurement_spec.py       # ✅ MeasurementSpec (纯配置)
│   │   ├── fitted_snapshot.py        # ✅ FittedSnapshot (测量结果)
│   │   ├── feedback_worker.py        # ✅ FeedbackWorker (asyncio)
│   │   └── config_worker.py          # ✅ ConfigWorker (asyncio)
│   │
│   ├── io/                     # 网络与反馈
│   │   ├── __init__.py
│   │   ├── feedback_manager.py # ✅ FeedbackManager + dispatch_raw()
│   │   └── feedback_slots/
│   │       ├── __init__.py
│   │       ├── base.py         # FeedbackSlot ABC + DataSubscription
│   │       ├── null_slot.py    # ✅ 调试用 (只打日志)
│   │       ├── pid_slot.py     # ✅ PidFeedbackSlot + PidController
│   │       ├── rpyc_slot.py    # ✅ rpyc 通用推送
│   │       └── rpyc_pool.py    # ✅ rpyc 连接池 (线程安全)
│   │
│   ├── ui/                     # PyQt6 界面
│   │   ├── __init__.py
│   │   ├── main_window.py      # ✅ 主窗口控制器 + 信号桥接
│   │   ├── main_window.ui      # Qt Designer 布局
│   │   ├── ui_bridge.py        # ✅ 采集线程 → Qt 主线程桥接
│   │   ├── waveform_view.py    # pyqtgraph 波形 + 2列图例 + 降采样
│   │   ├── mini_chart.py       # ✅ 迷你趋势图 (触发驱动)
│   │   └── panels/
│   │       ├── channel_panel.py       # 16 通道 2列
│   │       ├── device_panel.py        # 设备设置 4列
│   │       ├── measurement_panel.py   # ✅ 动态测量行
│   │       ├── feedback_panel.py      # PID 反馈卡片
│   │       └── pid_feedback_dialog.py # PID + AD9910/RTMQ 配置
│   │
│   └── config/
│       ├── __init__.py
│       └── settings.py         # 配置保存/加载 (JSON)
│
├── docs/                       # 文档
│   ├── ARCHITECTURE.md         # ✅ v0.5 架构文档
│   ├── ROADMAP.md              # ✅ 实施路线图
│   ├── EVENTBUS_SPEC.md        # ✅ EventBus 规范
│   ├── FEEDBACK_SPEC.md        # ✅ 当前反馈系统规范
│   ├── FEEDBACK_DESIGN_v0.5.md # ⚠️ 旧反馈设计归档
│   ├── TECH_STACK.md           # ✅ 本文档
│   └── CHECKLIST.md            # ✅ 实施清单
│
└── tests/
    ├── test_phase0.py           # ✅ 数据模型 + 模拟器 (16 tests)
    ├── test_feedback_slots.py   # ✅ 反馈系统 (10 tests)
    ├── test_art_device.py       # ✅ ART 硬件适配 (18 tests)
    └── pytest_cache/            # pytest 缓存 (可忽略)
```

**v0.5 删除的文件**:
- ❌ `scope/processing/` (整个目录: pipeline.py, fft.py, filters.py, math_ops.py, measurements.py)
- ❌ `scope/model/analysis_result.py`
- ❌ `scope/runtime/fit_worker.py`
- ❌ `scope/runtime/measurement_snapshot.py`
- ❌ `scope/acquisition/` (预留目录)

---

## 6. 关键第三方依赖速查

| 包 | 版本要求 | 用途 | 安装状态 |
|----|---------|------|----------|
| `PyQt6` | ≥6.5 | GUI 框架 | ✅ 已安装 |
| `pyqtgraph` | ≥0.13 | 波形渲染 (OpenGL) | ✅ 已安装 |
| `numpy` | ≥1.24 | 数值计算基础库 | ✅ 已安装 |
| **`rpyc`** | **≥5.3** | **实验室仪器 RPC 协议** | ✅ 已安装 |
| **`artdaq`** | **内置** | **ART 采集卡驱动** | ✅ 已入库 |
| `pytest` | ≥9.0 | 测试框架 | ✅ 已安装 |
| `pytest-asyncio` | ≥1.4 | asyncio 测试支持 | ✅ 已安装 |

**可选依赖 (未安装)**:
- `pyusb` — USB 通信 (备选)
- `pyserial` — 串口通信
- `h5py` — HDF5 数据存储
- `pymodbus` — Modbus 协议
- `httpx` — HTTP 异步客户端

---

## 7. 开发工具

| 工具 | 用途 | 版本 |
|------|------|------|
| **pytest** | 测试框架 | 9.0.3 |
| **pytest-asyncio** | asyncio 测试支持 | 1.4.0 (mode=auto) |
| **ruff** | 代码检查 + 格式化 | 可选 |
| **mypy** | 类型检查 | 可选 |
| **Wireshark** | 反馈网络抓包验证 | 外部工具 |

---

## 8. v0.5 技术决策总结

### 8.1 架构简化

| 决策 | v0.3 | v0.5 | 理由 |
|------|------|------|------|
| 数据模型 | AnalysisResult | RawFrame | 减少字段数 50% |
| 处理管道 | Pipeline 责任链 | MeasurementProcessor | 删除 2000+ 行代码 |
| 测量功能 | 12 个 | 4 个 | 聚焦核心需求 |
| 事件驱动 | Simulator 用 QTimer | 统一 callback | 零轮询 |

### 8.2 性能对比

| 指标 | v0.3 | v0.5 |
|------|------|------|
| 数据包大小 | ~1KB | **~100 bytes** |
| 测量延迟 | 5-20ms | **< 5ms** |
| 代码行数 | ~6000 | **~4000** |
| 测试通过率 | 72/72 | **45/45** |

### 8.3 技术债务清理

- ✅ 删除未使用的 `scipy.signal` 依赖
- ✅ 删除复杂的 Pipeline 责任链模式
- ✅ 删除 `AnalysisResult` 复杂数据包
- ✅ 统一事件驱动接口

---

## 9. 环境配置

### 9.1 Python 虚拟环境

```bash
# 创建虚拟环境 (Python 3.10+)
python -m venv .venv

# 激活虚拟环境 (Windows)
.\.venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 9.2 ART 驱动配置

**DLL 路径** (Windows):
```
C:\Program Files (x86)\ART Technology\ArtDAQ\Lib\x64\Art_DAQ.dll
```

**环境变量** (可选):
```bash
# 添加 DLL 目录到搜索路径
set PATH=C:\Program Files (x86)\ART Technology\ArtDAQ\Lib\x64;%PATH%
```

### 9.3 Mock 模式

无需硬件，使用 `SimulatorDevice`:
```bash
python -m scope.main --mock
# 或运行
start_mock.bat
```

---

## 10. 测试验证

### 运行所有测试

```bash
# 激活虚拟环境
.\.venv\Scripts\activate

# 运行测试
python -m pytest tests/ -v

# 期望结果
# 45 passed, 1 warning
```

### 测试覆盖率

| 测试文件 | 测试数 | 通过 |
|----------|--------|------|
| test_phase0.py | 16 | ✅ 100% |
| test_feedback_slots.py | 10 | ✅ 100% |
| test_art_device.py | 18 | ✅ 100%* |
| **总计** | **44** | **✅ 100%** |

*注: test_art_device.py 部分测试需要硬件支持

---

## 11. 未来技术方向

| 方向 | 技术选型 | 说明 |
|------|----------|------|
| 打包发布 | PyInstaller / Nuitka | 打包为独立 exe |
| Web 界面 | FastAPI + WebSocket | 远程监控 |
| 数据存储 | HDF5 (h5py) | 波形数据归档 |
| 更多协议 | pyserial / pymodbus | 支持串口/Modbus |
