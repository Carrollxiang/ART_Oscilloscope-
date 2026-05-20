# 技术栈与技术决策

## 1. 语言与运行时

| 项 | 选择 | 理由 |
|----|------|------|
| 语言 | **Python 3.10+** | 生态成熟, numpy/scipy 信号处理栈完整, asyncio 原生支持 |
| 包管理 | **pip** (当前) | 使用 requirements.txt + 清华镜像源 (https://pypi.tuna.tsinghua.edu.cn/simple) |
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
```
project-root/
├── main.py                     # 入口转发 (设置 DLL 路径 → 调用 scope.main.main)
├── pyproject.toml
├── requirements.txt
├── requirements-dev.txt
├── .gitignore
├── test_hardware.py            # ART 硬件诊断工具 (无 GUI)
│
├── artdaq/                     # ART 采集卡驱动 (NI-DAQmx 兼容封装, 已入库)
│
├── scope/                      # 主包
│   ├── __init__.py
│   ├── main.py                 # 应用入口 (ScopeApp + 参数解析)
│   │
│   ├── model/                  # 数据模型
│   │   ├── __init__.py
│   │   ├── analysis_result.py  # AnalysisResult, ChannelData, TriggerInfo
│   │   └── enums.py            # 枚举
│   │
│   ├── hardware/               # 硬件抽象层
│   │   ├── __init__.py
│   │   ├── device.py           # AcquisitionDevice (ABC)
│   │   ├── simulator.py        # SimulatorDevice (16 通道模拟信号)
│   │   └── art_device.py       # ArtDevice (ART USB 卡, artdaq)
│   │
│   ├── acquisition/            # 缓存与采集层 (预留)
│   │   ├── __init__.py
│   │   ├── ring_buffer.py
│   │   └── watchdog.py
│   │
│   ├── processing/             # 信号处理链
│   │   ├── __init__.py
│   │   ├── pipeline.py         # Pipeline 框架
│   │   ├── measurements.py     # 自动测量
│   │   ├── math_ops.py         # 通道数学运算
│   │   ├── fft.py              # FFT 频谱
│   │   └── filters.py          # 数字滤波
│   │
│   ├── io/                     # 网络与存储
│   │   ├── __init__.py
│   │   ├── feedback_manager.py # FeedbackManager (asyncio 调度)
│   │   └── feedback_slots/
│   │       ├── __init__.py
│   │       ├── base.py         # FeedbackSlot ABC + DataSubscription
│   │       ├── pid_slot.py     # ✅ PidFeedbackSlot + PidController
│   │       ├── rpyc_slot.py    # ✅ rpyc 通用推送
│   │       ├── rpyc_pool.py    # ✅ rpyc 连接池 (线程安全)
│   │       └── null_slot.py    # ✅ 调试用
│   │
│   ├── ui/                     # PyQt6 界面
│   │   ├── __init__.py
│   │   ├── main_window.py      # 主窗口控制器 + 信号桥接
│   │   ├── main_window.ui      # Qt Designer 布局
│   │   ├── waveform_view.py    # pyqtgraph 波形 + 2列图例 + 降采样
│   │   ├── mini_chart.py       # 迷你趋势图
│   │   └── panels/
│   │       ├── channel_panel.py       # 16 通道 2列, 逐通道电压量程
│   │       ├── device_panel.py        # 设备设置 4列 (设备|触发|采集|测试)
│   │       ├── measurement_panel.py   # 动态测量行 + 标准差
│   │       ├── feedback_panel.py      # PID 反馈卡片 (idle/run/pause)
│   │       ├── pid_feedback_dialog.py # PID + AD9910/RTMQ 配置
│   │       └── art_config_dialog.py   # (旧)
│   │
│   └── config/
│       ├── __init__.py
│       └── settings.py         # 配置保存/加载 (JSON)
│
└── tests/
    ├── test_phase0.py           # ✅ 数据模型 + 模拟器 (8 tests)
    ├── test_feedback_slots.py   # ✅ 反馈系统 (19 tests)
    ├── test_art_device.py       # ✅ ART 硬件适配 (18 tests)
    └── test_processing.py       # ✅ 信号处理管道 (27 tests)
```
```

## 6. 关键第三方依赖速查

| 包 | 版本要求 | 用途 |
|----|---------|------|
| `PyQt6` | ≥6.5 | GUI 框架 |
| `pyqtgraph` | ≥0.13 | 波形渲染 (OpenGL) |
| `qasync` | ≥0.27 | Qt + asyncio 桥接 |
| `numpy` | ≥1.24 | 数值计算基础库 |
| **`rpyc`** | **≥5.3** | **实验室仪器 RPC 协议 (主要反馈通道)** |
| **`artdaq`** | **内置** | **ART 采集卡驱动 (NI-DAQmx 兼容), register_done_event 事件驱动** |
| **`rpyc`** | **≥5.3** | **PID 反馈 RPC 协议 (AD9910 DDS / RTMQ 白盒子)** |
| `pyusb` | ≥1.3 | USB 通信 (备选, 已安装) |
| `pyserial` | ≥3.5 | 串口通信 (已安装) |
| `scipy` | ≥1.10 | 滤波器设计 (已安装) |
| `h5py` | ≥3.8 | HDF5 记录 (已安装) |
| `pymodbus` | ≥3.6 | Modbus 协议 (已安装) |
| `httpx` | ≥0.25 | HTTP 异步客户端 (已安装) |
| `six` | ≥1.17 | artdaq 库依赖 (已安装) |

## 7. 开发工具

| 工具 | 用途 |
|------|------|
| **pytest** | 测试框架 |
| **pytest-asyncio** | asyncio 测试支持 (已配置 `asyncio_mode = auto`) |
| **ruff** | 代码检查 + 格式化 |
| **mypy** | 类型检查 |
| **Wireshark** | 反馈网络抓包验证 |
