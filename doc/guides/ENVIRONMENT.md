---
kind: procedure
status: active
scope: development.environment
authority: supporting
owner: ART Oscilloscope maintainers
last_verified: "2026-10-05"
applies_to: Windows repository environment
source: project-maintained
source_commit: a3c2e33ee3640c5d23b2782756c68735ad8ee824
---

# 环境、运行与验证

在仓库根目录运行命令。依赖声明见 [技术栈](../project/TECH_STACK.md)，最新测试结果见 [NOW](../current/NOW.md)。

## 解释器选择

当前已有环境是 conda 布局，2026-10-05 实际检查 Python 3.10.20 位于 `.venv\python.exe`，使用：

```powershell
& .\.venv\python.exe -c "import sys; print(sys.executable); print(sys.version)"
```

若在另一台机器自行使用 `python -m venv .venv`，Windows 标准 venv 才使用 `.venv\Scripts\python.exe`。选择与实际环境一致的路径，不使用全局 Python 代跑测试。

当前 conda 环境可用 `conda activate D:\ART_Oscilloscope-\.venv` 激活；无需激活也可以直接调用解释器。

## 新环境安装

Python >=3.10。已创建环境后，通过该环境解释器安装所需 extras，例如：

```powershell
& .\.venv\python.exe -m pip install -e ".[all]"
& .\.venv\python.exe -m pip install -r requirements-dev.txt
```

安装输入不是冻结环境清单。厂商 ART 驱动/DLL 需另行配置；安装 Python extras 不会替代硬件驱动。

## 启动

```powershell
# 模拟采集
& .\.venv\python.exe -m scope.main --mock
# 实际 ART 采集（省略 --mock）
& .\.venv\python.exe -m scope.main
```

模拟模式只替换采集设备，反馈 sender 仍按目标配置发送。离线操作保持 target 为空；通讯测试按钮会加载 DLL 并操作 ArtDevice。

配置文件加载先恢复 UI，再通过 config.change/load_batch 应用设备与反馈；配置保存格式见 [架构](../project/ARCHITECTURE.md) / [反馈规范](../specs/FEEDBACK_SPEC.md)。

## 测试

```powershell
& .\.venv\python.exe -m pytest -q
```

若已有 `.pytest_cache` 或系统 pytest 临时目录拒绝访问，可不改 ACL，使用新的临时目录：

```powershell
$scopeTestTemp = Join-Path $PWD ('.venv\pytest-' + [guid]::NewGuid().ToString('N'))
& .\.venv\python.exe -m pytest -q --ignore=.pytest_cache -p no:cacheprovider --basetemp=$scopeTestTemp
```

`--basetemp` 会由 pytest 管理/清理该目录，所以每次使用新的专用路径，不指向已有工作文件目录。

## 纯文档验证

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/validate_doc_governance.ps1
git diff --check
```

治理校验覆盖 front matter、分类/生命周期、权威唯一性、根目录文档范围、导航可达性、本地 Markdown 链接目标和非 Markdown 资源登记。人工检查技术语义和证据日期。文档变更未改代码时无需重跑硬件测试。
