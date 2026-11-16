---
kind: record
status: active
scope: documentation.migration
authority: evidence
owner: ART Oscilloscope maintainers
last_verified: "2026-10-05"
applies_to: master@a3c2e33ee3640c5d23b2782756c68735ad8ee824, documentation migration on 2026-10-05
source: project-maintained
source_commit: a3c2e33ee3640c5d23b2782756c68735ad8ee824
---

# 2026-10-05 文档迁移记录

## 范围和依据

按用户要求参照 D:\RTMQ/doc/README.md、doc/governance/DOCUMENT_METADATA.md、AGENTS 文档门禁和 scripts/validate_doc_governance.ps1。仅借用文档组织和维护模式；没有修改 RTMQ，也没有修改本项目生产代码、依赖或设备配置。

迁移输入为 master@a3c2e33ee3640c5d23b2782756c68735ad8ee824 的全部 11 份普通 Markdown 与 AGENTS。requirements.txt/requirements-dev.txt 保留为安装输入。新增根 README、当前状态、治理规则、ADR 和校验入口。

## 旧路径到新入口

所有旧正文都保存在 archive/baseline；现行文档按代码核对重写，过时清单的未完成项合并到唯一活跃待办。

| 旧路径 | 当前入口 | 原文存档 |
| --- | --- | --- |
| `docs/ARCHITECTURE.md` | [doc/project/ARCHITECTURE.md](../project/ARCHITECTURE.md) | [2026-10-05_ARCHITECTURE.md](../archive/baseline/2026-10-05_ARCHITECTURE.md) |
| `docs/CHECKLIST.md` | [doc/archive/INDEX.md](../archive/INDEX.md) | [2026-10-05_CHECKLIST.md](../archive/baseline/2026-10-05_CHECKLIST.md) |
| `docs/EVENTBUS_SPEC.md` | [doc/specs/EVENTBUS_SPEC.md](../specs/EVENTBUS_SPEC.md) | [2026-10-05_EVENTBUS_SPEC.md](../archive/baseline/2026-10-05_EVENTBUS_SPEC.md) |
| `docs/FEEDBACK_DESIGN_v0.5.md` | [doc/specs/FEEDBACK_SPEC.md](../specs/FEEDBACK_SPEC.md) | [2026-10-05_FEEDBACK_DESIGN_v0.5.md](../archive/baseline/2026-10-05_FEEDBACK_DESIGN_v0.5.md) |
| `docs/FEEDBACK_SPEC.md` | [doc/specs/FEEDBACK_SPEC.md](../specs/FEEDBACK_SPEC.md) | [2026-10-05_FEEDBACK_SPEC.md](../archive/baseline/2026-10-05_FEEDBACK_SPEC.md) |
| `docs/FEEDBACK_TODO.md` | [doc/status/ACTIVE_TODO.md](ACTIVE_TODO.md) | [2026-10-05_FEEDBACK_TODO.md](../archive/baseline/2026-10-05_FEEDBACK_TODO.md) |
| `docs/README.md` | [doc/README.md](../README.md) | [2026-10-05_README.md](../archive/baseline/2026-10-05_README.md) |
| `docs/ROADMAP.md` | [doc/status/ROADMAP.md](ROADMAP.md) | [2026-10-05_ROADMAP.md](../archive/baseline/2026-10-05_ROADMAP.md) |
| `docs/TECH_STACK.md` | [doc/project/TECH_STACK.md](../project/TECH_STACK.md) | [2026-10-05_TECH_STACK.md](../archive/baseline/2026-10-05_TECH_STACK.md) |
| `TODO.md` | [doc/status/ACTIVE_TODO.md](ACTIVE_TODO.md) | [2026-10-05_TODO.md](../archive/baseline/2026-10-05_TODO.md) |
| `VIRTUAL_ENV.md` | [doc/guides/ENVIRONMENT.md](../guides/ENVIRONMENT.md) | [2026-10-05_VIRTUAL_ENV.md](../archive/baseline/2026-10-05_VIRTUAL_ENV.md) |

## 核对与修正

- 旧 TODO 与 CHECKLIST 的 ProcessingPipeline/dispatch_raw/Slot 已完成条目属于历史，现状改为 MeasurementProcessor/Manager/Worker。
- 旧版本标题、测试计数和未来 v0.8 描述不再留在现行规范；测试证据集中到 NOW，原值在归档中保留。
- 据 EventBus.put/publish 实现注明 BLOCK topic 的零超时和无确认；没有声称控制命令可靠投递。
- ConfigWorker 没有显式帧边界屏障，删除现行文档中未经证据支持的原子生效承诺；待办记录后续时序契约。
- DevicePanel 通讯测试直接调用硬件、健康通知尚未走 device.status 的差异保留到 NOW 和待办，没有用文档整理掩盖。
- FittedSnapshot 字段与 key 语义、PID 无 dt 的离散步计算、DAC set_ab(0,b) 和构造参数 set_b 路径按实现修正。
- 本机解释器实测为 conda `.venv/python.exe`，纠正旧 AGENTS 中的标准 venv 路径，另在指南解释两种布局。
- 旧文档的硬件/长跑结论保留为历史陈述，本轮只具有静态代码核对及同基线离线回归证据。

## 验证方式

本轮验证结果：

- 治理脚本通过：2 份必需根控制文档、25 份带元数据的 Markdown、9 个现行 canonical 范围，文档树无非 Markdown 资源。
- 11 份归档逐份与基线正文比对通过；除 Markdown 相对链接调整外，原文完整保留。
- 校验器的 9 个正/负样例通过，覆盖有效结构、缺失元数据、权威重复、无效日期、断链、孤立文档、违规根文档、缺少替代信息和归档权威冲突。
- `git diff --check` 通过；本轮没有修改生产代码或重复运行同基线的离线回归。既有回归结果集中记录在 NOW。
