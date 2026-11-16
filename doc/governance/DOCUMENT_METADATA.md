---
kind: policy
status: active
scope: documentation.metadata
authority: canonical
owner: ART Oscilloscope maintainers
last_verified: "2026-10-05"
applies_to: repository documentation
source: project-maintained
source_commit: a3c2e33ee3640c5d23b2782756c68735ad8ee824
---

# 文档分类、生命周期与权威规则

采用 D:\RTMQ 的统一入口、唯一当前状态、分类目录和显式 authority 模式；选择记录在 [ADR 0001](../adr/0001-document-governance.md)。这是本仓库的维护规则，RTMQ 的硬件/仿真规则不适用于本项目。

## 必需元数据

doc/ 下所有 Markdown 和根 README 都以单层 YAML front matter 开头：

```yaml
---
kind: reference
status: active
scope: runtime.architecture
authority: canonical
owner: ART Oscilloscope maintainers
last_verified: "2026-10-05"
applies_to: ART Oscilloscope v0.9.1
source: project-maintained
source_commit: a3c2e33ee3640c5d23b2782756c68735ad8ee824
---
```

| 字段 | 规则 |
| --- | --- |
| kind | policy / decision / reference / procedure / record / index，选择主要用途 |
| status | draft / proposed / active / deprecated / superseded / archived；表示文档生命周期，不表示功能完成程度 |
| scope | 小写点分知识范围，可使用连字符；足够窄，避免跨规范重复权威 |
| authority | canonical 唯一现行规范；supporting 解释/导航辅助；evidence 历史或观察证据 |
| owner | 长期维护角色 |
| last_verified | active + canonical 必填有效日期；未核对历史内容填 null，移动文件不能刷新它 |
| applies_to | active + canonical 必填；功能版本、代码基线或维护范围 |

可选 source/source_commit 记录来源与代码证据；review_after 表示复核提醒，过期不自动改变生命周期。deprecated 必填 replacement；superseded 必填 superseded_by，指向实际替代材料。record_state 可标记录本身的 open/resolved 等状态。

## 分类目录

| 用途 | 目录 |
| --- | --- |
| 文档导航 | doc/README.md |
| 当前已提交项目状态 | doc/current/NOW.md（唯一） |
| 架构、技术栈 | doc/project/ |
| 行为/接口规范 | doc/specs/ |
| 可复用操作步骤 | doc/guides/ |
| 持久决策与理由 | doc/adr/ |
| 文档规则 | doc/governance/ |
| 待办、路线图、带日期记录 | doc/status/；record 文件名含 YYYY-MM-DD |
| 历史方案与迁移前快照 | doc/archive/ |

根目录文档白名单为 README.md、AGENTS.md 和可选 CONTEXT.md。本项目目前只需要前两项；requirements*.txt、pyproject.toml 等安装/配置输入属于运行资源，保留原位，不作为文档权威。

## 权威、生命周期和冲突

同一 `scope + applies_to` 只能有一个 active + canonical。current/NOW 维护现状，ACTIVE_TODO 维护未完成项，其他入口链接它们，避免多个测试基线和进度清单。

正常生命周期可由 draft → proposed → active → deprecated → superseded → archived；明确替代时可直接归档。archived/superseded 不得作为当前依据；deprecated 仅用于迁移兼容。归档索引本身可 active，但索引中的历史正文没有现行权威。

冲突处理：先确认问题和适用范围，再查唯一现行规范，并以代码/测试核对实际行为。时间戳和文件路径不能解决双重权威；未核对的硬件陈述不能因迁移或测试通过而升级。

AGENTS 是 loader-facing bootstrap policy，不加 front matter；架构禁止规则保留在那里，其他文档引用而不建立第二份规则正文。

## 创建和更新流程

1. 先确定 kind/status/scope/authority/owner/applies_to；检查是否已有相同主题的现行文档。
2. 按用途落位，添加元数据并从文档入口或索引建立入口。历史材料保留，record 文件名带日期。
3. 内容核对后才填写 last_verified；状态报告固定 source_commit，区分离线测试、历史报告和新硬件验收。
4. 更新受影响规范、NOW 或待办；不要在索引复制测试数和状态。
5. 运行 `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/validate_doc_governance.ps1` 和 `git diff --check`，检查本地链接和迁移映射。

非 Markdown 资源在 doc/README.md 登记路径、用途和解释文档。机器证据与带日期记录配对。治理脚本校验结构、链接和导航可达性，不代替技术正确性与硬件验收。
