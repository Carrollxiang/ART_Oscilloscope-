# 技术栈与技术决策

## 1. 语言与运行时

| 项 | 选择 | 理由 |
|----|------|------|
| 语言 | **Python 3.11+** | 生态成熟, numpy/scipy 信号处理栈完整, asyncio 原生支持 |
| 包管理 | **uv** (推荐) 或 **poetry** | uv 速度快一个数量级, 锁文件可靠 |
| Python 分发 | embed Python (打包用) | 最终交付时可用 PyInstaller / Nuitka 打包成单文件 |

## 2. 核心框架

| 组件 | 选型 | 备选 | 理由 |
|------|------|------|------|
| GUI 框架 | **PyQt6** | PySide6 | PyQt6 的 GPL 许可对本项目无限制, pyqtgraph 对 PyQt6 支持成熟 |
| 波形渲染 | **pyqtgraph** | pyqtgraph 本身就是最佳选择 | OpenGL 加速渲染, 自动降采样, 专为示波器类应用设计 |
| 事件循环桥接 | **qasync** | — | 将 Qt 事件循环嫁接在 asyncio 上, 使 Feedback 网络 I/O 与 UI 共存 |
| USB 通信 | **libusb** (pyusb) | pywinusb (仅 Windows) | 跨平台, 支持 USB bulk/isochronous, 与 ART 硬件对接 |
| 数值计算 | **numpy** | — | 波形数据处理的基础, 无替代品 |
| 信号处理 | **scipy.signal** | — | FIR/IIR 滤波器设计, 可选依赖 |

## 3. 反馈系统

| 协议 | 库 | 说明 | 状态 |
|------|----|------|------|
| **rpyc** | **rpyc** | 实验室仪器标准 RPC 协议，带连接池复用 | ✅ 已实现 |
| UDP | 标准库 socket | 零依赖, 最简实现 | 🔲 后续 |
| 串口 RS-232/485 | **pyserial** | 标准库, 跨平台 | 🔲 后续 |
| Modbus TCP | **pymodbus** | 工业自动化场景 | 🔲 后续 |
| HTTP / MQTT | **httpx** / **paho-mqtt** | 按需引入 | 🔲 按需 |

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

## 4. 存储与记录

| 用途 | 选型 | 理由 |
|------|------|------|
| 数据记录 | **HDF5** (h5py) | 天生的多维数组容器, 超大文件友好, 支持分块压缩 |
| 配置文件 | **toml** (标准库 tomllib) | Python 3.11 原生支持 toml 解析, 无需第三方依赖 |
| 运行时状态缓存 | **msgpack** 或 pickle | HDF5 太大或不需要持久化时使用 |

## 5. 项目结构

```
scope/
├── pyproject.toml
├── requirements.txt
├── README.md
│
├── scope/                          # 主包
│   ├── __init__.py
│   ├── main.py                     # 应用入口
│   │
│   ├── model/                      # 数据模型 (零依赖, 纯数据类)
│   │   ├── __init__.py
│   │   ├── analysis_result.py      # AnalysisResult, ChannelData, TriggerInfo
│   │   └── enums.py                # 枚举 (通道状态、触发类型、反馈协议类型)
│   │
│   ├── hardware/                   # 硬件抽象层
│   │   ├── __init__.py
│   │   ├── device.py              # AcquisitionDevice (ABC)
│   │   ├── simulator.py           # SimulatorDevice (模拟器)
│   │   └── art_device.py          # ArtDevice (ART USB 卡)
│   │
│   ├── acquisition/               # 缓存与采集层
│   │   ├── __init__.py
│   │   ├── ring_buffer.py         # 环形缓冲区
│   │   ├── stream_reader.py       # USB 流读取线程
│   │   ├── watchdog.py            # 健康监测 + 自动重连
│   │   └── timestamp.py           # 时间戳管理
│   │
│   ├── trigger/                   # 触发引擎
│   │   ├── __init__.py
│   │   ├── engine.py              # 触发引擎主控
│   │   ├── edge.py                # 边沿触发
│   │   └── pulse.py               # 脉宽触发 (扩展)
│   │
│   ├── processing/                # 信号处理链
│   │   ├── __init__.py
│   │   ├── pipeline.py            # Pipeline 框架 (责任链模式)
│   │   ├── measurements.py        # 自动测量 (Vpp, Freq, Vrms...)
│   │   ├── math_ops.py            # 通道数学运算
│   │   ├── fft.py                 # FFT 频谱分析
│   │   └── filters.py             # 数字滤波
│   │
│   ├── io/                        # 网络与存储
│   │   ├── __init__.py
│   │   ├── feedback_manager.py    # FeedbackManager (调度器)
│   │   ├── feedback_slots/        # 反馈插槽实现
│   │   │   ├── __init__.py
│   │   │   ├── base.py            # FeedbackSlot (ABC)
│   │   │   ├── rpyc_slot.py       # ✅ rpyc 协议 (主)
│   │   │   ├── rpyc_pool.py       # ✅ rpyc 连接池
│   │   │   ├── null_slot.py       # ✅ 调试用
│   │   │   ├── udp_slot.py        # 🔲
│   │   │   ├── serial_slot.py     # 🔲
│   │   │   └── modbus_slot.py     # 🔲
│   │   ├── rest_api.py            # FastAPI (可选)
│   │   ├── recorder.py            # HDF5 记录
│   │   └── playback.py            # 数据回放 (可选)
│   │
│   ├── ui/                        # PyQt6 界面
│   │   ├── __init__.py
│   │   ├── main_window.py         # 主窗口
│   │   ├── waveform_view.py       # 波形显示 (pyqtgraph)
│   │   ├── channel_panel.py       # 通道控制面板
│   │   ├── trigger_panel.py       # 触发设置面板
│   │   ├── measurement_panel.py   # 测量读数面板
│   │   └── feedback_panel.py      # 反馈管理面板
│   │
│   └── config/                    # 配置
│       ├── __init__.py
│       └── settings.py            # 应用配置管理
│
└── tests/                         # 测试
    ├── test_phase0.py              # ✅ 数据模型 + 模拟器 (8 tests)
    ├── test_feedback_slots.py      # ✅ 反馈系统 (19 tests)
    ├── test_ring_buffer.py         # 🔲
    └── test_watchdog.py            # 🔲
```

## 6. 关键第三方依赖速查

| 包 | 版本要求 | 用途 |
|----|---------|------|
| `PyQt6` | ≥6.5 | GUI 框架 |
| `pyqtgraph` | ≥0.13 | 波形渲染 (OpenGL) |
| `qasync` | ≥0.27 | Qt + asyncio 桥接 |
| `numpy` | ≥1.24 | 数值计算基础库 |
| **`rpyc`** | **≥5.3** | **实验室仪器 RPC 协议 (主要反馈通道)** |
| **`artdaq`** | **内置** | **ART 采集卡驱动 (NI-DAQmx 兼容)** |
| `pyusb` | ≥1.3 | USB 通信 (备选) |
| `pyserial` | ≥3.5 (可选) | 串口通信 |
| `scipy` | ≥1.10 (可选) | 滤波器设计 |
| `h5py` | ≥3.8 (可选) | HDF5 记录 |
| `pymodbus` | ≥3.6 (可选) | Modbus 协议 |
| `httpx` | ≥0.25 (可选) | HTTP 异步客户端 |

## 7. 开发工具

| 工具 | 用途 |
|------|------|
| **pytest** | 测试框架 |
| **pytest-asyncio** | asyncio 测试支持 (已配置 `asyncio_mode = auto`) |
| **ruff** | 代码检查 + 格式化 |
| **mypy** | 类型检查 |
| **Wireshark** | 反馈网络抓包验证 |
