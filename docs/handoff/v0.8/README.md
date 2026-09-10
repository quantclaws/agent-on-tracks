# Tracks v0.8 接手入口

2026-09-10，原协调者按用户要求立即交接。用户报告只剩两个 5 小时额度窗口。**v0.8 尚未达到发布条件；本文件是交接指导，不是正式需求或发布证明。** 后续 Agent 从这里开始，不需要恢复旧对话或寻找 docs/status 的临时报告。

正式需求唯一权威是仓库 `.tracks/projects/v0.8/` 的 story、spec、acceptance、architecture、interfaces、test-plan；不得用本交接删减或改写锁定要求。用户已允许绕过 Tracks 自身的开发调度，仍要求需求完全实现、AC 与执行测试通过 ID 可追踪、全源码 coverage ≥95%、没有冗余或超长代码以及真实发布旅程。

先读 [按正式 spec 的逐项进度与缺口](SPEC-STATUS.md)，其中区分明确实现缺口与尚未完成验收的项目。

## 接手顺序

1. 阅读 [当前代码与未提交层](CURRENT-STATE.md)，核对 `git status --short` 和 [状态及文件摘要](data/state.json)。有差异先确认来源，不能覆盖后来工作。
2. 阅读 [下一步任务与完成条件](NEXT-STEPS.md)，先独立复核 parity 候选，再按依赖处理；不要直接重跑整套或复活旧 run。
3. 按 [验收和发布规则](VERIFICATION.md) 冻结自己的候选并验收。局部测试绿只证明其边界，不能换算为 81 条 AC 的完成率。
4. 按 [协作与恢复操作](OPERATIONS.md) 分配小任务。实施者只能接触源代码、正式契约及自己编写的 unit tests；冻结 integration/e2e 由独立验收者持有。

旧 run `01M19FJVES7G113RD8QXXY3PQZ` 保持停车。自动续工 `tracks-maestro` 已暂停，旧协调者不会与接手者并发开发。最后一个 OpenCode Micro-1 已结束，无新实施任务在运行。原来的 19 个调度任务不是当前完成度口径。

## 核心目标判断与推进方式

Story → spec/acceptance → 自主实施验收可以实现，但需求歧义仍需用户在确认阶段裁定。AC-ID 与测试绑定可实现。测试能为已明确、已覆盖的需求提供可审计证据，**有限测试不能数学证明任意需求 100% 实现**；应逐条核对 AC 完整子句、反例、执行身份与真实旅程，声明证据边界。RGR 和职责分离能降低作弊风险，必须辅以物理访问隔离和独立行为断言，角色提示或假 RED 不构成保障。

当前偏离主要是：调度修复占据大量投入；stub/默认放行与旅程前提缺陷并存；局部 evidence producer 已有成果，但上游真实性、最终发布 effects 和全局质量未闭合。快路径是小范围独立实现、一次验收一个明确行为、按依赖合入。不要把恢复旧流程当交付前置，也不要降低质量阈值。

## 数据与清理约定

[data](data) 中的日期型 JSON 是历史测量或定位索引，不是新的正式需求。AC ledger 最新保存状态为 81 条、97 个计划节点；其中 9 partial、67 unreviewed、5 reviewed-gap，均不能当作已完成认证。后续应根据正式完整子句重新逐条核实并更新证据。

`.tracks/runtime/forensics/` 保留只读原始 XML、日志、patch、源码摘要；[证据索引](data/evidence-index.json) 说明位置。历史 snapshot 内的文档是证据字节，不是继续执行的指令。可恢复代码在 `.tracks/runtime/handoff-assets/`，不包含凭据或运行数据库，不纳入本次文档提交。迁移到另一台机器时需单独携带该目录及所需 forensics。

[清理清单](data/cleanup-manifest.json) 记录已替代临时文档的路径和删除前摘要。docs/status 的 v0.8 临时报告已移除；三份原有长期文档保留。其他操作者较早的 runtime handoff 属于旧 run 历史，不作为当前入口，也未擅自删除。真实运行数据库 `.tracks/runtime/tracks.db` 和原有未跟踪 `.tracks/tracks.db` 均保留。
