---
kind: index
status: active
scope: repository.entry
authority: canonical
owner: ART Oscilloscope maintainers
last_verified: "2026-10-05"
applies_to: repository documentation
source: project-maintained
source_commit: a3c2e33ee3640c5d23b2782756c68735ad8ee824
---

# ART 数字示波器

基于 PyQt6、pyqtgraph 和进程内 EventBus 的多通道数字示波器，支持 ART USB 采集、测量时间窗与 PID 反馈。

- [文档导航](doc/README.md)：按任务找到架构、接口、操作指南和历史材料。
- [当前状态](doc/current/NOW.md)：代码基线、测试证据和待验证边界。
- [Agent 规则](AGENTS.md)：修改代码前必须遵守的架构约束。

## 从现有项目环境启动

在仓库根目录的 PowerShell 中运行：

```powershell
& .\.venv\python.exe -m scope.main --mock
```

`--mock` 使用模拟采集；反馈目标若配置真实 IP，仍可产生网络发送。离线试用时保持反馈目标为空。实际设备运行、环境安装与验证步骤见 [运行指南](doc/guides/ENVIRONMENT.md)。

项目环境当前采用 conda 布局 `.venv\python.exe`；`pyproject.toml` 的包版本与文档功能里程碑独立，详见 [技术栈](doc/project/TECH_STACK.md)。
