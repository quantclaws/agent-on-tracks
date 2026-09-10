# 当前代码、已验收边界与 WIP

交接源代码基线：`releases/v0.8`，`478bc1766473d9316906aafe9f26a46183681727`。之后的交接文档提交不代表新增产品功能。包版本仍是 0.5.0，尚未发布 v0.8。

## 已提交的有界成果

| Commit | 内容 | 当时验证范围 |
|---|---|---|
| f79e19c | tag effect 按真实远端对象识别、恢复 | 11 checks |
| 026d75a | CI head/workflow/repo/run/check 身份绑定 | 18 checks |
| 3d139bb | 单 tag Runtime authority、WAL、reconcile | 26 checks |
| a4a63b5 | 外部 machine.py 重构合入，9 模块 | 前后 725 tests，722 pass，同样 3 baseline failures |
| 0a95893 | 19 个 doc-gap 方法实际迁出，Executor 净减 513 行 | 65 checks |
| 314d413 | 有序多 tag 全计划预检、逐项 WAL、停止与重试 | 37 checks |
| c8bbf79 | mini-package anchor fixture 修正 import 路径 | 15 unit checks |
| 32b81c2 | declared raw reply、真实/假 backend 传播、transport 分类 | 156 checks；未完成六面 parity |
| 478bc17 | local gates evidence 绑定完整当前 contract | clean HEAD 18 direct checks |

上表是历史定向验收，不是当前整树重测。对应证据在 forensics 的 `tag-commit-*`、`ci-commit-*`、`runtime-commit-*`、`kernel-refactor-acceptance-*`、`docgap-accepted-*`、`multi-tag-accepted-*`、`anchor-fixture-*`、`envelope-collection-accepted-*`、`local-gates-commit-*`。

外部用户 Agent 的 `/Users/openclaw/workspace/tracks-external/v08-kernel-quality-2026-09-09` 成果已合入 a4a63b5，不要再次合并。machine.py 约 781 行；并不说明其他长模块已达标。

## 未提交代码必须保留并分层处理

精确文件清单/摘要见 `data/state.json`。完整当前文件副本在 `.tracks/runtime/handoff-assets/root-wip-files/`；tracked 差异在同目录上级 `root-wip.patch`。副本是恢复材料，不能将其整包作为产品提交。

原始 WIP 四文件：`tracks/cli/main.py`、`tracks/executor/executor.py`、`tracks/executor/m_verify.py`、`tracks/kernel/events.py`。它们已混有后续层，不能整文件 stage。原始接管快照在 `forensics/maestro-takeover-2026-09-07`。

| 待分批落地层 | 文件边界 | 已存证据与限制 |
|---|---|---|
| FULL_F | m_impl_runtime.py、m_verify.py、Executor 的 judge_full_f_reuse、test_fullf_evidence_oob.py、test_verify_fullf_reuse.py | fullf-accepted-2026-09-08：30 定向 +116 回归；不是完整 source authenticity |
| Prism final | envelope.py、opencode.py、Executor/m_verify.py、test_verify_final_oob.py、test_verify_prism_final.py | prism-accepted-2026-09-08：74；实际 result/kind/routing/candidate/scope，仍需上游真实性 |
| Preview | release_preview.py、Executor preview wiring、两个 test_release_preview_direct.py | direct-preview-accepted-2026-09-09：74；真实 plan/contract/blob/evidence bytes，不能手工造目标 preview |
| Authorization | release_authorization.py、CLI 对应 hunks、两个 test_release_authorization_direct.py、test_cli_release_decision_closed_red.py | direct-authorization-accepted-2026-09-09：84；canonical security/source 完整性仍开口 |
| 冻结 RED 验收 | test_envelope_closed_contract_direct.py、test_dispatch_parity_materialized_direct.py | 协调者所有，不给实施者读；尚未全绿 |

每层从最新 HEAD 隔离候选，依据对应 accepted manifest/patch 逐函数重放，验证该层及依赖后再提交。旧 patch 基于旧树，不能盲目 `git apply` 或 cherry-pick 整个当前 WIP。已提交 local gates 不再重复派工。

## 未合入的 parity 候选

持久副本 `.tracks/runtime/handoff-assets/parity-candidate/`，文件摘要 `data/parity-files.json`。包括初版 16 个 source 文件的改动及 Micro-1 后续修改；**整体未验收、未合入**。不要整包覆盖 root，候选基线可能缺少之后的提交。

Micro-1 自报：仅修改 kernel/envelope.py 与 effects/dispatch_parity.py；默认六面必须齐备、assignment 从 envelope.version/token 取身份，重复声明冲突拒绝。自写 21 tests pass；原候选 29 tests 中 1 条旧默认宽松行为断言失败。该报告不是独立验收结论。

根目录 frozen 测试验证脚本见 VERIFICATION；最新独立结果写在 `data/parity-review.json`。后续仍须解决提前发 dispatch.parity、实际消费材料 provenance、缺少 skill/template/Fake 面仍放行等问题。writer schema/opt-out 也未完成。

## 当前长度实测

本次只读盘点：Executor 7975 行、m_impl_runtime 5982 行、CLI 2736 行、opencode 2578 行（物理行数，含现有 WIP）。完整超 1000 行列表见 `data/current-module-lengths.json`。因此代码质量仍有大量工作，不能按 machine 重构完成推断总体达标。
