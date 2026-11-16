---
kind: reference
status: active
scope: project.technology
authority: supporting
owner: ART Oscilloscope maintainers
last_verified: "2026-10-05"
applies_to: ART Oscilloscope v0.9.1
source: project-maintained
source_commit: a3c2e33ee3640c5d23b2782756c68735ad8ee824
---

# 技术栈与代码目录

依赖声明以 [pyproject.toml](../../pyproject.toml)、[requirements.txt](../../requirements.txt) 和 [requirements-dev.txt](../../requirements-dev.txt) 为准。以下描述当前用途，不代表所有可选包已安装或功能已实现。

| 范围 | 当前使用 | 声明或边界 |
| --- | --- | --- |
| Python | 当前 conda 环境 3.10.20 | 项目要求 >=3.10；解释器见 [运行指南](../guides/ENVIRONMENT.md) |
| 数值计算 | NumPy | 基础依赖 numpy>=1.24 |
| UI | PyQt6、pyqtgraph | ui extra；Qt 主线程显示与交互 |
| 异步运行 | 标准库 asyncio + executor | 独立线程事件循环；qasync 虽声明在 ui extra，但当前主程序未用它驱动 Qt |
| 反馈通信 | RPyC | feedback extra；真实调用在 sender 层 |
| ART 采集 | 仓库 artdaq 包 + 厂商驱动/DLL | 运行 hardware 模式需实际安装与设备 |
| 配置 | JSON + dataclasses | 显式序列化，禁止直接 dump 数据类实例 |
| 测试 | pytest、pytest-asyncio | 使用项目环境 |
| 规划/可选 | pyserial、pymodbus、httpx、pyusb、scipy、h5py | extras 声明不能当作串口反馈、FFT 或 HDF5 存储已实现的证据 |

pyproject 包版本仍为 0.1.0；v0.9.1 是文档与提交使用的功能里程碑。本轮不修改包版本。

## 当前代码目录

```text
scope/
  main.py                 组装与生命周期
  model/                  RawFrame、枚举
  hardware/               DeviceConfig、ArtDevice、SimulatorDevice、TriggerDetector
  runtime/                EventBus、测量处理/规格、PID、配置 worker、状态载荷
  io/                     反馈 manager/worker/命令、RPyC 池、AD9910/RTMQ/DAC sender
  config/                 ConfigManager
  ui/                     MainWindow、UIBridge、WaveformView、MiniChart、panels
artdaq/                   ART 驱动包装
tests/                    离线回归
scripts/                  诊断工具与文档治理校验
feedback_example/         反馈服务示例
config/                   项目配置
doc/                      分类文档
```

不再列出已删除的 processing、feedback_slots 或计划中的 ring_buffer/watchdog 文件作为当前结构。详细分工见 [架构](ARCHITECTURE.md)，后续功能见 [待办](../status/ACTIVE_TODO.md)。
