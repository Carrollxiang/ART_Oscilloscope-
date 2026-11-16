---
kind: decision
status: active
scope: documentation.layout
authority: canonical
owner: ART Oscilloscope maintainers
last_verified: "2026-10-05"
applies_to: repository documentation
source: project-maintained
source_commit: a3c2e33ee3640c5d23b2782756c68735ad8ee824
---

# ADR 0001：文档分类、生命周期与权威

## 背景

2026-10-05，用户要求按 D:\RTMQ 的文档管理模式整理全部项目文档。原 docs/ 及根 TODO 同时保存当前规范、旧 Slot 设计、阶段验收和多份测试基线，现行状态与历史描述互相混杂。

## 决策

采用 doc/README 统一导航、current/NOW 唯一现状、status/ACTIVE_TODO 唯一待办；按 project/specs/guides/adr/governance/status/archive 分类。所有受管 Markdown 添加 kind/status/scope/authority/owner 及验证边界，同 scope + applies_to 保持现行 canonical 唯一。

AGENTS 保留根目录 loader 格式和既有架构规则；根 README 提供快速入口。环境指南和旧 TODO 移入文档体系。旧文档原文归档，不直接删掉技术/历史内容；仅修正因搬迁失效的 Markdown 链接。

复用 RTMQ 文档治理脚本并适配本项目根目录资源、链接和导航可达性检查。完整规则见 [DOCUMENT_METADATA](../governance/DOCUMENT_METADATA.md)。本次不新增空 contracts 目录、无需求的 CONTEXT 或 RTMQ 专有硬件流程。

## 影响

旧 docs/ 路径不再存在；所有仓库内入口更新到新目录，迁移去向见 [迁移记录](../status/2026-10-05_DOCUMENT_MIGRATION.md)。新文档不得依赖历史快照的接口/测试数。后续功能改动更新对应规范、NOW 和待办，并运行治理校验。
