# 接手、分工与恢复

## 第一次操作

从 README 开始，核对 `git rev-parse HEAD`、`git status --short`、`data/state.json`。此分支有有意保留的未提交产品/test 文件。不要 `git reset --hard`、`git clean`、整包覆盖或把所有 WIP 一次提交。`.tracks/tracks.db` 原有未跟踪空文件与 `.tracks/runtime/tracks.db` 真正运行库均不属于文档清理。

本地恢复包 `.tracks/runtime/handoff-assets/root-wip-files/` 保存每个改动文件当前字节，root-wip.patch 保存 tracked diff。恢复到 source_head 的独立目录时：先从 Git 检出 source_head，再逐个复制清单文件；不要同时 apply patch 又复制相同文件。恢复包用于 coordinator/reviewer，包含冻结验收测试，严禁提供给 implementation Agent。

迁机时 Git 提交只携带 handoff 文档与机器数据；需要额外安全复制 `.tracks/runtime/handoff-assets/` 和需要的 `.tracks/runtime/forensics/`。两类数据含测试源与历史源码，应只给协调者。runtime DB/凭据/OpenCode 全量 session 不包含在恢复包里，若真实旅程需要运行状态应单独受控迁移，不从文本报告重造。

## 小任务协作协议

协调者负责正式 AC 拆解、独立测试、审阅和合并。实施者可用用户建议的低成本 General/Luna。给实施者 source-only 的独立 checkout，独立 Git 历史且无 root 链接、只允许读取本任务范围的正式契约与源码；自己的 unit tests 允许。若系统同账号能读整个 root，这不满足物理隔离，需通过容器/账户/文件权限挂载真正限制；不能仅写“禁止读取”就声称达标。

每次任务书只含：明确行为、有限文件范围、输入/输出接口、需要保持的兼容性、负例和恢复条件、预期交付文件。不要含冻结验收源码/断言实现。交付 source diff + 自写测试 + 精确命令/结果；协调者在另一个 reviewer checkout 跑冻结验收，返回行为层缺陷而非测试答案。不能因为实施者自报 GREEN 就合并。

本轮 OpenCode 最后工作目录原为 `/tmp/tracks/parity-general-2026-09-10`，已结束；最终 source-only 副本在 handoff-assets/parity-candidate。Micro-1 session `ses_f75fca6acffeMkd3DQJvDbNoI0`，模型当时为 `napi/deepseek-v4-flash`，自写 21 tests 通过；root 独立 17 checks 实测仍 4 RED。后继开启新短任务，不恢复长旧 session，不重复派 Micro-1。

初次 General 与后两次 glm 长 session 曾消耗大量上下文而无有效修改；因此限制单包范围和输出，遇到反复规划而没有 diff，应停任务并缩小。模型可用性/额度由接手环境实际检查，不能用心跳次数推断额度窗口。

## 提交与并发

每个 accepted slice 在当前 HEAD 的隔离 reviewer 树中重放、检查 diff、运行对应测试与 lint，再合入。共享 Executor/CLI/envelope/opencode 一次只允许一个合并者写；独立实现可并行，但不能同时写 root。新分支使用 `codex/` 前缀。不推送或发布未通过最终质量的 candidate。

自动续工 `tracks-maestro` 已 PAUSED，prompt 已改为交接入口。接手者若选择新的续工方式，应先确保旧协调者不再写树；不能同时恢复旧 heartbeat 和另一个 harness。旧主 run 保持 parked，不恢复 watcher，不通过修改 DB 跳阶段。

## 数据用途和废弃文档

`data/*v0.8*json` 是有日期的历史 inventory，可能引用已删除报告或 `/tmp` 路径；这些字符串仅为历史来源字段，不是操作指令。实际当前定位用 README、CURRENT-STATE、NEXT-STEPS、VERIFICATION 和 state.json。

forensics 下 start/red/first-check/interrupted 都是失败或中间材料；只有匹配的 accepted/commit XML+manifest 才能支持其有界验收。仍须在新 HEAD 验证。文档清理不删除原始证据中的快照字节。其他操作者的旧 handoff 保留为旧 run 历史；不再让接手者阅读它们。自己的旧 docs/status v0.8 与 runtime 临时报告、handoff-current 草稿入口已按清单删除。
