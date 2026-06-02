# 实施路线图 — freq_lock_with_stm32 分支

> 基于 master 分支的示波器框架，适配 STM32 串口 + RTMQ 射频卡扫频锁频。

---

## ✅ Phase 0 — 项目骨架 + 数据模型

搭起项目目录结构，定义所有核心数据类。

- ✅ `AnalysisResult`, `ChannelData`, `TriggerInfo` 数据模型
- ✅ `AcquisitionDevice` ABC 接口
- ✅ `SimulatorDevice` 模拟设备

## ✅ Phase 1 — STM32 串口采集

- ✅ `Stm32Device` 串口采集设备
  - 门控触发 (CH1 高→采集, CH1 低→封帧)
  - in_waiting 轮询 + read() 批量读取 (解决 readline 阻塞 6.7s)
  - 预分配 numpy buffer (可配置大小)
  - 时间窗口出帧 + buffer 满强制封帧
  - stdout 抑制
- ✅ 串口诊断脚本 (`diag_serial.py`, `diag_timing.py`)
- ✅ 采样率 / 缓存长度 UI 可编辑，动态更新

## ✅ Phase 2 — 扫频协调器 + 射频卡

- ✅ `ScanCoordinator` 全局单例 (线程安全)
- ✅ `ScanConfig` 参数模型 (base_freq, scan_freq_amp, scan_dur)
- ✅ `RtmqDevice` intf_usb 单例封装
- ✅ `ScanPanel` UI (参数设置 + 🚀下发按钮 + 反馈开关 + 拟合结果显示)

## ✅ Phase 3 — V(f) 映射 + 线型拟合

- ✅ `map_to_frequency_domain()` — V(t)→V(f) 线性映射
- ✅ `fit_lorentzian()` — scipy curve_fit + 峰值回退
- ✅ `ScanFitResult` (f0, gamma, amplitude, offset, R²)
- ✅ 每帧自动执行拟合，结果更新到 ScanPanel

## ✅ Phase 4 — 反馈分叉

- ✅ 反馈开关 (`feedback_enabled`)，默认关闭
- ✅ 关闭时只做拟合不进入反馈链路
- ✅ 开启后 FeedbackQueue → FeedbackManager.dispatch()

## ✅ Phase 5 — 1 通道 UI 适配

- ✅ 通道数 16→1 (CH0)
- ✅ 设备面板改为串口设置 (COM口/波特率/采样率/缓存长度)
- ✅ 测量面板通道列表 → ["CH0"]

---

## 🔲 TODO — STM32 固件问题 (需与固件开发协调)

- [ ] **采样间隔确认**: 当前实测 ~6.7ms/点 (149 Sa/s)，与设计目标 1ms 差距大
- [ ] **门控逻辑修复**: TTL 低电平期间 STM32 未停止发送 (`b''` 从未出现)
- [ ] **协议升级: 时间戳/序号**: 每条数据附带 n 或 t，消除均匀采样假设

## 🔲 TODO — 后续功能

- [ ] 扫频参数 RF 功率可调
- [ ] 扫频结束后自动标记 DONE 状态
- [ ] Mini Chart 触发驱动更新 (每帧一次)
- [ ] 控制面隔离 (参数修改走 ControlQueue)
- [ ] 错误弹窗限流
