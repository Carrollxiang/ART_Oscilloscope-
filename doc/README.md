---
kind: index
status: active
scope: documentation.navigation
authority: canonical
owner: ART Oscilloscope maintainers
last_verified: "2026-10-05"
applies_to: repository documentation
source: project-maintained
source_commit: a3c2e33ee3640c5d23b2782756c68735ad8ee824
---

# 文档导航

本页是文档导航唯一入口。当前状态只在 [current/NOW.md](current/NOW.md) 维护，待办只在 [status/ACTIVE_TODO.md](status/ACTIVE_TODO.md) 维护；本页不重复测试数或功能进度。

## 按任务阅读

| 任务 | 阅读顺序 |
| --- | --- |
| 首次了解项目 | [根 README](../README.md) → [NOW](current/NOW.md) → [架构](project/ARCHITECTURE.md) |
| 修改实现 | [AGENTS](../AGENTS.md) → NOW → 对应规范 → 代码与测试 |
| 配置环境、启动或测试 | [环境与运行指南](guides/ENVIRONMENT.md) |
| 修改事件与线程路由 | [EventBus 规范](specs/EVENTBUS_SPEC.md) → 架构 |
| 修改 PID、反馈命令或发送器 | [反馈规范](specs/FEEDBACK_SPEC.md) → [待办](status/ACTIVE_TODO.md) |
| 查看演进与后续方向 | [路线图](status/ROADMAP.md) → 待办 |
| 整理或新增文档 | [元数据规则](governance/DOCUMENT_METADATA.md) → [ADR 0001](adr/0001-document-governance.md) |
| 查历史方案与记录 | [归档索引](archive/INDEX.md)，不作为当前实现依据 |

## 权威来源

| 问题 | 唯一现行来源 |
| --- | --- |
| Agent 架构边界 | [AGENTS.md](../AGENTS.md) |
| 项目当前状态与测试基线 | [NOW.md](current/NOW.md) |
| 系统组件、数据模型和线程分工 | [ARCHITECTURE.md](project/ARCHITECTURE.md) |
| topic、载荷和队列语义 | [EVENTBUS_SPEC.md](specs/EVENTBUS_SPEC.md) |
| 反馈生命周期、PID 与目标设备接口 | [FEEDBACK_SPEC.md](specs/FEEDBACK_SPEC.md) |
| 文档分类、生命周期和权威 | [DOCUMENT_METADATA.md](governance/DOCUMENT_METADATA.md) |
| 活跃待办 | [ACTIVE_TODO.md](status/ACTIVE_TODO.md) |

技术栈与操作指南是 supporting 材料；实际行为由代码与可执行测试核对。规范与代码不一致时记录冲突，不按日期猜测哪个正确。

## 目录角色

```text
doc/
  README.md       文档导航
  current/        当前已提交代码基线和验证边界
  project/        架构与技术栈
  specs/          接口和行为规范
  guides/         可复用操作流程
  adr/            持久决策及理由
  governance/     文档维护规则
  status/         活跃待办、路线图、带日期的记录
  archive/        历史材料及索引
```

## 迁移与资源

- [2026-10-05 迁移记录](status/2026-10-05_DOCUMENT_MIGRATION.md)：每份旧文档的去向、核对依据和已发现的差异。
- 根目录 [requirements.txt](../requirements.txt)、[requirements-dev.txt](../requirements-dev.txt) 是安装输入，保留原位；依赖声明以 [pyproject.toml](../pyproject.toml) 为准。
- 文档树当前没有非 Markdown 资源。新增 PDF、HTML 或 JSON 证据时，必须在本页登记路径、用途和解释它的文档。

维护后运行 `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/validate_doc_governance.ps1`。归档、废弃和被替代材料不进入正常任务阅读集。
