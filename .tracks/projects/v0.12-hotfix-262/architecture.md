---
envelope: tracks-envelope:v2
architecture_id: ARCH-012-HF262
spec_ref: SPEC-012
created: 2026-10-27
status: draft
sha:
---

# v0.12-hotfix-262 — 架构：v0.12.1 发布后修复（release gate 权威 + attention 停顿 + 进程边界 env + 阶段事件 + UI）

本文是 ARCH-012 的 hotfix delta。基线 = v0.12（`44c14de`，tag `v0.12.0`，release 422449714，均不可变）。本文的唯一变更来源是已批准的 design-delta `.tracks/projects/v0.12.1/design-delta.md`（Prism re-review PASS，advisories A01/A02）；本文把该 delta 落成 M-DESIGN 三文档形态。不变量 INV-1..INV-10 逐字继承 design-delta §1，不重述；本文只写模块边界、接线、closure 与机器合同。不新增 tracks.db 事件类型、不 retag、不改写历史 payload；`tracks.db` 历史差异由消费端谓词 + v0.12.0 erratum 校正（INV-10）。

## 0. 延续性声明（什么不变）

- ARCH-001～ARCH-012 的全部合同逐字继承：唯一生产路径、事件溯源、单写者、canonical 状态机、supervisor 串行驱动、发布闭环、质量守卫 registry、测试流水线、CLI/HTTP 交付面、v0.12 安装包/项目版本锁定/OOB 工作台全部不变。
- 不新增 EVENT_TYPES / service_events / COMMAND_KINDS；不新增 CLI 子命令；不新增 HTTP 端点；既有端点 schema 不变（只修订投影/读取路径的行为与性能，不改响应形状）。
- `.tracks/projects/project.toml` 测试执行合同逐字继承（本 delta 零 bytes 变更；digest 不重锚）。
- CI required checks 七名（lint/coverage/test/deliverables/trace/reach/ui-e2e）与 `release-evidence` milestone 硬门禁不变；零 workflow 变更。
- 质量守卫八类 registry 不变；`pyproject.toml` 在 M-VERIFY 前做版本 bump（`0.12.0 → 0.12.1`，version gate 绑定对象），其余守卫配置/digest 继承（bump 触发的 pyproject-backed digest 重锚由实现期按 §4.2 机制执行，不属于本设计变更集）。
- v0.12.0 的 tag/release/事件历史不可变（INV-10）；erratum 是新增文档 artifact，不是历史改写。

### 0.1 本 delta 的变更清单

- 新共享谓词模块 `tracks/executor/full_f_authority.py`（`is_authoritative_full_f`，INV-1）与 `tracks/executor/review_findings.py`（`has_blocking_findings`，INV-3）。
- 新进程边界包装 `tracks/executor/host_env.py`（`host_process_env()` allowlist，INV-8），10 个既有 subprocess 站点全部改经它。
- 既有模块的定点修订：`m_impl_full_chain._finalize_full_round`、`verify_gates._producer_candidate`、`release_preview._evidence_key/evidence_digests/_evidence_is_success`、`release_authorization._gate_status/_recomputed_preview/preview_inputs_match`、`validate_cmd._closure_evidence/_merge_stream_event`、`release_cmd._release_gate_blocked/_prism_final_status`、`publish_face._publish_security_review_verdict/_assess_security_with_review`、`verdict_face._publish_verify_final_verdict/_emit_verdict`、`result_payload._m_test_prism_payload`、`run_loop._run_loop_preflight`、`release_tail._ensure_issue_backend`、`supervisor/service_intake.submit_intake`、`supervisor/intake.create_run`、`publish_runtime`（INV-9 事件发射）、`milestone_chain._do_close_milestone/_complete_milestone`、`milestone.clean_temp_refs`、kernel `release.py` reducers（继承权威）、`projections_core._chain_rows/project_todos`、`projections_timeline`（slim）、`projections_run._backlog_group`、`timeline.js`（legacy stage fallback + 选中样式）、`styles.css`（选中环）、`releases.js`（backlog 标题）、`views.js`（Todos inbox）。全部按 design-delta §2 的接缝表执行，不允许超接缝改动。
- Scaffold：仅三个新模块的接口桩（§2）；erratum 为发布期 artifact，不入 scaffold。

## 1. 模块边界

### 1.0.1 增长轴归属

| # | 包/文件 | 本 delta 增长 | 触发 | IF |
|:--|:--|:--|:--|:--|
| 1 | `tracks/executor/full_f_authority.py`（新，stub→Devon） | `is_authoritative_full_f(payload, candidate_sha=None)` 唯一权威谓词（INV-1） | AC-FR0267-01/AC-FR0268-01..03/AC-FR0276-01 | IF-HOTFIX262-FULLF-001 |
| 2 | `tracks/executor/review_findings.py`（新，stub→Devon） | `has_blocking_findings(findings)` 阻塞发现谓词（INV-3） | AC-FR0271-01/02、AC-FR0272-01/02、AC-FR0040-03、AC-FR0140-02 | IF-HOTFIX262-REVIEW-001 |
| 3 | `tracks/executor/host_env.py`（新，stub→Devon） | `host_process_env(extra=None, contract_allowlist=())` 单 allowlist 包装（INV-8） | AC-NFR0143-02、AC-FR0315-01 | IF-HOTFIX262-HOSTENV-001 |
| 4 | `tracks/executor/m_impl_full_chain.py` | `_finalize_full_round` 保留 `serves_as_full_f` 与 `full_f_eligible` 双键 | AC-FR0268-01..03 | IF-HOTFIX262-FULLF-001 |
| 5 | `tracks/executor/release_preview.py` / `release_authorization.py` / `tracks/cli/release_cmd.py` / `tracks/cli/validate_cmd.py` / `verify_gates.py` | 必需证据键完备性、split prism 键、legacy 单键 read-back、candidate 绑定（INV-2/4/5） | AC-FR0270-02、AC-FR0273-01/02、AC-FR0274-02 | IF-HOTFIX262-RELEV-001 |
| 6 | `tracks/executor/publish_face.py` / `verdict_face.py` / `result_payload.py` | ingest fail-closed 拒绝 pass+blocking（INV-3）；下游硬不变量 | AC-FR0271-01/02、AC-FR0272-01/02、AC-FR0040-03、AC-FR0140-02 | IF-HOTFIX262-REVIEW-001 |
| 7 | `tracks/executor/run_loop.py` / `release_tail.py` / `tracks/supervisor/service_intake.py` / `supervisor/intake.py` | attention 停顿/去重/凭据 preflight（INV-6/7）；`GITHUB_TOKEN` canonical | AC-FR0283-02、AC-FR0329-02、AC-FR0298-02 | IF-HOTFIX262-ATTN-001 |
| 8 | `tracks/executor/publish_runtime.py` / `milestone_chain.py` / `tracks/kernel/release.py` | INV-9 阶段事件 exactly-once 发射（kernel reducer 权威不变） | AC-FR0276-01、AC-FR0335-01/02、AC-FR0333-01 | IF-HOTFIX262-STAGE-001 |
| 9 | `tracks/server/projections_timeline.py` / `static/app/timeline.js` | legacy run 的阶段转移合成回退（INV-9 后半） | AC-FR0335-01/02、AC-FR0333-01 | IF-HOTFIX262-STAGE-001 |
| 10 | `tracks/server/projections_core.py` / `projections_run.py` / `projections_timeline.py` / `static/styles.css` / `static/app/releases.js` / `static/app/views.js` | ac-chain 短路、slim timeline 收紧、选中环、backlog 标题、统一 Todos inbox | AC-FR0335-01/02、AC-NFR0150-02、AC-FR0340-01、AC-FR0307-01 | IF-HOTFIX262-UI-001 |
| 11 | `tracks/executor/milestone.py` | `clean_temp_refs` 扩到 `refs/trac/rgr/{run_id}/*` 与临时 worktree/staging | AC-FR0276-01 | IF-HOTFIX262-CLEANUP-001 |

### 1.1 Composition Root

本 delta 不新增交付面/入口；全部修订挂在既有 composition root 上（CLI → kernel.machine.decide → executor.execute → store.append；HTTP 只读投影面）。关键接线：

```text
发布证据链（B01/B02）
  cli/validate_cmd._closure_evidence
    -> full_f_authority.is_authoritative_full_f(payload, candidate_digest)   [唯一权威]
  executor/verify_gates._producer_candidate
    -> full_f_authority.is_authoritative_full_f(...)
  executor/release_preview._evidence_is_success / evidence_digests
    -> full_f_authority.is_authoritative_full_f(...)
    -> _evidence_key(scope) ∈ {prism.verify_final, prism.security, None}     [INV-4]
  executor/release_authorization._gate_status/_recomputed_preview
    -> required keys {full_f, ci, prism.verify_final, prism.security, security}
    -> preview_inputs_match（legacy 单 "prism" 键 read-back 容忍, INV-5）
  cli/release_cmd._release_gate_blocked -> +full_f 检查

阻塞评审（B03）
  ingest: publish_face._publish_security_review_verdict
          verdict_face._publish_verify_final_verdict / _emit_verdict
          result_payload._m_test_prism_payload
    -> review_findings.has_blocking_findings(findings)             [INV-3]
    -> pass ∧ blocking ⇒ verdict.failed/attention.required（不落地 prism.verdict pass）
  下游: release_preview._evidence_is_success / release_authorization._prism_status
        _security_status / release_cmd._prism_final_status / milestone_chain
    -> verdict != "pass" ∨ has_blocking_findings ⇒ failed

attention / 凭据（M01）
  supervisor/service_intake.submit_intake(source="issue") / supervisor/intake.create_run(hotfix_*)
    -> 凭据 preflight：GITHUB_TOKEN 唯一 canonical；缺失且 GITHUB_ACCESS_TOKEN/GH_TOKEN 在场
       ⇒ 仅诊断 hint（INV-6），不产生静默回退
  executor/run_loop._run_loop_preflight（每 loop 迭代）
    -> _pending_operator_attention(state) 未决且非 auto-retryable ⇒ 停在 needs_attention（≤1 次发射）
  executor/release_tail._ensure_issue_backend
    -> 已存在未决 attention.required(area="issue_creation", reason="missing_token") ⇒ 不重发

进程边界 env（M02）
  10 个既有 subprocess 站点（quality_gate×2 / test_execute / test_collect×2 / helpers.run_collect_command
  / result_audit._collect_test_modules / host_contract._run_declared / security._run_command
  / closure_evidence._execute_selected_nodes / m_impl_dispatch._run_host_commit_hook）
    -> host_env.host_process_env(extra, contract_allowlist)         [INV-8 唯一 allowlist]

阶段事件（M03）
  executor/publish_runtime（首个 publish.planned 之前，visit 守卫）
    -> stage.exited(M-RELEASE) + stage.entered(M-PUBLISH) exactly-once
  executor/milestone_chain._do_close_milestone（_block_incomplete_publish 之后、trace_closed 之前）
    -> stage.exited(M-PUBLISH) + stage.entered(M-MILESTONE)
  executor/milestone_chain._complete_milestone（run.completed 前/同时）
    -> stage.exited(M-MILESTONE)
  kernel/release.py reducers：仍是状态权威（不搬移）
  server/projections_timeline + static/app/timeline.js（foldStageFacts/stageWorkflowWindow）
    -> legacy run 无显式事件时按 publish.planned/milestone.trace_closed/run.completed 合成转移

UI/perf
  projections_core._chain_rows：acs 为空 ⇒ 直接 return []（先决短路）
  projections_timeline：_SLIM_KEYS 去 evidence、收紧 _SLIM_MAX_ROWS、slim 外不读 $ref blob
  static/styles.css：.stage-node-selected/[data-selected="true"] 去背景/前景覆写，只留 outline ring + aria
  projections_run._backlog_group(conn, repo_path)：join intakes.report_json.title；fallback 首行/本地化占位
  projections_core.project_todos：统一 inbox（awaiting 集 ∪ attention blocks ∪ evaluated intakes）
```

### 1.2 Required AC closure (ISLAND_GATE_1)

锚定集 = assignment 的 26 条 `anchor_acs`（跨版本引用，逐字登记，不分配新 AC 编号）。每条的 owner/surface/composition/wiring/test/evidence 指向本 delta 的接缝与出口；IF- ID 见 interfaces.md §5（IF-HOTFIX262-* 为本 delta 注册）。

- **FR-0267** owner=tracks/executor/verify_gates.py+tracks/executor/full_f_authority.py:AC-FR0267-01@v0.8 surface=trac run（M-VERIFY 入口）+ trac status/replay composition=verify_gates._producer_candidate 经 is_authoritative_full_f 绑定 candidate_sha wiring=M-IMPL 干净 FULL_F→candidate.frozen→后续门禁/preview/外部操作携带同一 candidate_sha→is_authoritative_full_f 重算权威（不轻信 serves_as_full_f） test=integration:tests/integration/test_hotfix262_full_f_authority.py::test_candidate_binding_from_frozen_not_manifest evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix262_full_f_authority.py` 输出 passed 且 replay v0.12.0 切片中 candidate_digest==44c14de…、seq 7362 被信用、seq 5896 被拒绝 IF-HOTFIX262-FULLF-001
- **FR-0268** owner=tracks/executor/full_f_authority.py:AC-FR0268-01@v0.8 surface=trac run（M-VERIFY evidence.reused）+ trac status composition=is_authoritative_full_f（passed∧eligible∧round∈{FULL_1空ledger,fallback_full,FULL_F}∧candidate 绑定∧evidence_ids 非空∧identity 四元组） wiring=candidate 未漂移且 identity 一致→evidence.reused kind=full_f→status full_reuse=full_f→本地不重跑 test=integration:tests/integration/test_hotfix262_full_f_authority.py::test_full_f_reuse_authoritative_matrix evidence=同上命令输出 passed 且 7362-shape 接受、5896-shape 以两个独立理由拒绝、serves_as_full_f=true∧full_f_eligible=false 拒绝 IF-HOTFIX262-FULLF-001
- **FR-0268** owner=tracks/executor/full_f_authority.py+tracks/executor/verify_gates.py:AC-FR0268-02@v0.8 surface=trac run（重跑分支）+ trac status composition=同一谓词的否定分支 wiring=candidate 漂移/identity 不一致/STALE→无 evidence.reused→落 full.executed 按 LOCAL_GATES 重跑→status full_rerun 及判定依据 test=integration:tests/integration/test_hotfix262_full_f_authority.py::test_drift_or_stale_forces_full_rerun evidence=同上命令输出 passed 且漂移样本无 evidence.reused、status 含 drift/stale/identity_mismatch IF-HOTFIX262-FULLF-001
- **FR-0268** owner=tracks/executor/full_f_authority.py:AC-FR0268-03@v0.8 surface=trac replay + trac run composition=谓词内 STALE 拒绝子句 wiring=STALE 标记证据即使 candidate_sha 相同→复用判定 blocked/重跑→replay 显示 stale 且无 evidence.reused test=integration:tests/integration/test_hotfix262_full_f_authority.py::test_stale_full_f_not_reused evidence=同上命令输出 passed 且 stale 样本 replay 标记 stale、无复用事件 IF-HOTFIX262-FULLF-001
- **NFR-0143** owner=tracks/executor/host_env.py+tracks/executor/milestone_chain.py:AC-NFR0143-02@v0.8 surface=trac report --format md（release.trace 导出）+ 跨语言工具链子进程 composition=host_process_env allowlist 包裹全部 10 站点；trace 同一性证明继承 wiring=M-PUBLISH 外部操作经 allowlist 子进程执行→trace 含 candidate/artifact/evidence/preview/approval/operation digests→report 导出与 tag/release 互验 test=integration:tests/integration/test_hotfix262_host_env.py::test_cross_language_toolchain_env_allowlist evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix262_host_env.py` 输出 passed 且 Python/uv/Node/cargo/go/java 探针子进程 env 无 operator 密钥、工具链功能正常 IF-HOTFIX262-HOSTENV-001
- **FR-0270** owner=tracks/executor/release_preview.py+tracks/cli/release_cmd.py:AC-FR0270-02@v0.8 surface=trac run（CI 证据绑定）+ trac release preview composition=release_preview 必需键完备性（full_f/ci/prism.verify_final/prism.security/security）+ release_cmd._release_gate_blocked 增加 full_f wiring=head SHA 与 candidate 不一致/缺失/stale→ci.run_observed failed→status blocked→不一致证据不被 preview/外部操作引用 test=integration:tests/integration/test_hotfix262_release_evidence.py::test_missing_required_keys_block_release evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix262_release_evidence.py` 输出 passed 且缺任一键的 preview/authorization 判 invalid/blocked IF-HOTFIX262-RELEV-001
- **FR-0273** owner=tracks/executor/release_preview.py:AC-FR0273-01@v0.8 surface=trac release preview + trac status + release.previewed 事件 composition=evidence_digests 无条件必需键完备性检查；preview_digest 聚合不变 wiring=M-RELEASE 聚合 candidate/CI/artifact/FULL_F/计划→release.previewed 含 preview_digest→AWAITING_RELEASE→status 报告 stage=awaiting_release test=integration:tests/integration/test_hotfix262_release_evidence.py::test_preview_requires_full_key_set evidence=同上命令输出 passed 且新 preview 缺 split 键即 invalid、preview_digest 可重算一致 IF-HOTFIX262-RELEV-001
- **FR-0273** owner=tracks/executor/release_authorization.py:AC-FR0273-02@v0.8 surface=trac release preview（stale 报告）+ 历史 preview blob composition=preview_inputs_match 按 preview blob 记录的键集 schema 比对；legacy 单 "prism" 键 read-back 容忍（INV-5），无 DB 迁移 wiring=前置漂移/计划变更→status=stale 且 stale_reason 含漂移项→stale preview 不可授权；v0.12.0 历史 blob 读回不报 operation_plan_changed test=integration:tests/integration/test_hotfix262_release_evidence.py::test_legacy_single_prism_key_preview_readable evidence=同上命令输出 passed 且 legacy blob read-back 成功、新 preview 必须 split 键 IF-HOTFIX262-RELEV-001
- **FR-0271** owner=tracks/executor/verdict_face.py+tracks/executor/review_findings.py:AC-FR0271-01@v0.8 surface=trac run（PRISM_FINAL 派发）+ prism.verdict 事件 composition=ingest fail-closed：pass∧blocking ⇒ 矛盾 verdict=协议违规，拒绝且不落地 passing verdict；下游 verdict!="pass"∨has_blocking_findings ⇒ failed wiring=本地/CI 通过→派发 Prism 复审同一 candidate→prism.verdict pass candidate=<SHA>→status prism=pass→进入 M-SECURITY test=integration:tests/integration/test_hotfix262_review_ingest.py::test_verify_final_pass_with_blocker_rejected evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix262_review_ingest.py` 输出 passed 且注入 pass+blocker 时 verdict.failed/attention.required、无 passing prism.verdict、attempt 被消费 IF-HOTFIX262-REVIEW-001
- **FR-0272** owner=tracks/executor/publish_face.py+tracks/executor/review_findings.py:AC-FR0272-01@v0.8 surface=trac run（M-SECURITY）+ security.assessed 事件 composition=同一 ingest 拒绝覆盖 security scope；_evidence_key(security)=prism.security wiring=M-SECURITY 按合同执行扫描→security.assessed status=passed→status security=passed→进入 M-RELEASE；pass+blocker 注入被拒 test=integration:tests/integration/test_hotfix262_review_ingest.py::test_security_pass_with_blocker_rejected_and_replay_7342 evidence=同上命令输出 passed 且 replay seq 7342（SEC-001）经 publish_face 后 security 门失败 IF-HOTFIX262-REVIEW-001
- **FR-0271** owner=tracks/executor/result_payload.py+tracks/executor/release_authorization.py:AC-FR0271-02@v0.8 surface=trac status/replay（prism=failed） composition=_m_test_prism_payload ingest 拒绝 + 下游 _prism_status 硬不变量 wiring=prism.verdict failed/revise 或 pass+blocking→不进入 M-SECURITY→status prism=failed→修复后新 candidate 重走 M-VERIFY test=integration:tests/integration/test_hotfix262_review_ingest.py::test_downstream_gates_treat_blocking_as_failed evidence=同上命令输出 passed 且 preview/authorization/milestone 对 pass+blocker 一律判 failed IF-HOTFIX262-REVIEW-001
- **FR-0272** owner=tracks/executor/publish_face.py+tracks/cli/release_cmd.py:AC-FR0272-02@v0.8 surface=trac status（security=failed|unknown blocked） composition=_assess_security_with_review 硬不变量 + _release_gate_blocked wiring=策略缺失/结果未知/畸形/pass+blocking→security.assessed failed|unknown→status blocked→不进入 M-RELEASE→trac release --action release 拒绝 test=integration:tests/integration/test_hotfix262_review_ingest.py::test_malformed_or_blocking_security_blocks_release evidence=同上命令输出 passed 且 release 命令对 blocked security 退出非零 IF-HOTFIX262-REVIEW-001
- **FR-0274** owner=tracks/cli/release_cmd.py:AC-FR0274-02@v0.8 surface=trac release --action release（终端） composition=_release_gate_blocked 增加 full_f 键检查 wiring=前置失败或 preview stale→退出非零+`error: release rejected — gate failed or preview stale`→不落 release.decided→状态保持 AWAITING_RELEASE test=integration:tests/integration/test_hotfix262_release_evidence.py::test_release_rejected_on_gate_failed_or_stale evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix262_release_evidence.py` 输出 passed 且拒绝样本 stderr 含固定文案、无 release.decided 事件 IF-HOTFIX262-RELEV-001
- **FR-0040** owner=tracks/executor/verdict_face.py:AC-FR0040-03@v0.4 surface=trac run（M-TEST PRISM 路由） composition=_emit_verdict ingest 拒绝覆盖 M-TEST wiring=prism.verdict(pass)∧无 blocking→RED_CHECK；pass∧blocking→拒绝（不落 passing verdict）；revise→回 WRITE 重派 test=integration:tests/integration/test_hotfix262_review_ingest.py::test_m_test_ingest_pass_with_blocker_rejected evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix262_review_ingest.py` 输出 passed 且 M-TEST 注入样本被拒、路由不前进 IF-HOTFIX262-REVIEW-001
- **FR-0140** owner=tracks/executor/result_payload.py:AC-FR0140-02@v0.5 surface=trac run（M-IMPL PRISM_FINAL） composition=_m_test_prism_payload ingest 拒绝覆盖 M-IMPL wiring=PRISM_FINAL pass∧无 blocking→TASK_DONE；pass∧blocking→拒绝；revise→回 GREEN/RED lineage test=integration:tests/integration/test_hotfix262_review_ingest.py::test_m_impl_ingest_pass_with_blocker_rejected evidence=同上命令输出 passed 且 M-IMPL 注入样本被拒、无 task.completed IF-HOTFIX262-REVIEW-001
- **FR-0283** owner=tracks/supervisor/service_intake.py+tracks/executor/release_tail.py:AC-FR0283-02@v0.8 surface=trac status（needs_attention）+ attention.required 事件 composition=GITHUB_TOKEN 唯一 canonical；缺失时 preflight 诊断 hint（INV-6）；_ensure_issue_backend 去重未决 attention（INV-7） wiring=缺凭据→不产生 issue.created 成功记录→status needs_attention reason=missing_token+恢复指引→凭据恢复后 resume 重建 test=integration:tests/integration/test_hotfix262_attention.py::test_missing_token_single_attention_and_hint evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix262_attention.py` 输出 passed 且 ≤1 条 attention.required(area="issue_creation", reason="missing_token")、stderr/hint 含 export GITHUB_TOKEN 指引、无 FAKE-N 映射 IF-HOTFIX262-ATTN-001
- **FR-0298** owner=tracks/executor/run_loop.py:AC-FR0298-02@v0.9 surface=trac status（needs_attention 停顿） composition=_run_loop_preflight 每迭代检查 _pending_operator_attention wiring=未决非 auto-retryable attention→run_loop 停在 needs_attention（≤1 次发射）→无热循环（无 1,808 循环、无 CommandStallError） test=integration:tests/integration/test_hotfix262_attention.py::test_loop_halts_on_unresolved_attention evidence=同上命令输出 passed 且迭代间 attention 事件计数 ≤1、run 停于 needs_attention、无 CommandStallError IF-HOTFIX262-ATTN-001
- **FR-0329** owner=tracks/supervisor/intake.py+tracks/supervisor/service_intake.py:AC-FR0329-02@v0.10 surface=trac hotfix/triage 前检报告 composition=create_run(hotfix_*) 不再把 missing-token 吞为 issue_not_found；分类经 IF-HOTFIX-011 映射 wiring=抓取失败（缺凭据/认证失败/限流/网络失败）→前检 issue_fetch_failed 携分类恢复指引→重试路径保持开启 test=integration:tests/integration/test_hotfix262_attention.py::test_issue_fetch_failed_classification_preserved evidence=同上命令输出 passed 且缺 token 样本报告 issue_fetch_failed（非 issue_not_found）、指引含 export GITHUB_TOKEN IF-HOTFIX262-ATTN-001
- **FR-0315** owner=tracks/executor/host_env.py:AC-FR0315-01@v0.9 surface=宿主子进程 env 观察（探针） composition=allowlist 唯一来源；denylist 废止；[host-contract].env_allowlist escape hatch wiring=全部 10 站点经 host_process_env→子进程 env 无 GITHUB_*/GH_*/TRAC_GITHUB_*/TRAC_SERVE_*/TRAC_AGENT_*/GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE/*_TOKEN/*_SECRET/*_API_KEY→工具链变量保留 test=integration:tests/integration/test_hotfix262_host_env.py::test_all_ten_sites_strip_operator_secrets evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix262_host_env.py` 输出 passed 且 10 站点逐一探针断言泄漏变量缺席 IF-HOTFIX262-HOSTENV-001
- **FR-0276** owner=tracks/executor/publish_runtime.py+tracks/executor/milestone_chain.py+tracks/executor/milestone.py:AC-FR0276-01@v0.8 surface=trac status（terminal=released）+ 事件流 composition=INV-9 阶段事件 exactly-once（visit 守卫，retry_tail 安全）+ clean_temp_refs 扩到 refs/trac/rgr/* wiring=M-PUBLISH 完成→进入 M-MILESTONE→milestone.trace_closed→milestone.sealed→refs.cleaned→status terminal=released；阶段事件按 INV-9 序恰好一次 test=integration:tests/integration/test_hotfix262_stage_events.py::test_exactly_once_stage_events_under_retry_tail + tests/integration/test_hotfix262_stage_events.py::test_clean_temp_refs_remaining_zero evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix262_stage_events.py` 输出 passed 且事件序含恰一次 exited(M-RELEASE)/entered(M-PUBLISH)/exited(M-PUBLISH)/entered(M-MILESTONE)/exited(M-MILESTONE)、`git for-each-ref refs/trac/tmp refs/trac/rgr` 为空 IF-HOTFIX262-STAGE-001 IF-HOTFIX262-CLEANUP-001
- **FR-0335** owner=tracks/server/projections_timeline.py+tracks/server/static/app/timeline.js:AC-FR0335-01@v0.11 surface=run 时间线 UI + GET /api/runs/{id}/timeline composition=legacy fallback 合成转移（publish.planned∧current=M-RELEASE⇒合成关闭/开启；trace_closed∧current=M-PUBLISH⇒合成；run.completed released∧current=M-MILESTONE⇒关闭 done） wiring=打开 legacy run（v0.12.0 形、无显式阶段事件）→13 阶段 canonical 时间线五态可区分→M-MILESTONE 以 done 关闭→stageWorkflowWindow 非空 test=integration:tests/integration/test_hotfix262_stage_events.py::test_legacy_run_timeline_synthesizes_transitions + e2e:tests/e2e/test_hotfix262_release_ui.py::test_legacy_run_stage_window_nonempty evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix262_stage_events.py` 输出 passed 且 legacy 种子的投影含合成 visits、M-MILESTONE closed=done IF-HOTFIX262-STAGE-001
- **FR-0335** owner=tracks/server/projections_timeline.py:AC-FR0335-02@v0.11 surface=timeline/ac-chain API + UI 一致性 composition=slim 收紧（_SLIM_KEYS 去 evidence、_SLIM_MAX_ROWS 收紧、slim 外不读 $ref）；单一数据源 wiring=timeline 内容与 API 数据一致→无 mock→数据缺失显式降级→slim payload <50KB/<500ms test=integration:tests/integration/test_hotfix262_ui_perf.py::test_slim_timeline_payload_bounded evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix262_ui_perf.py` 输出 passed 且 slim 响应 <50KB、p95 <500ms、无 $ref 外读 IF-HOTFIX262-UI-001 IF-HOTFIX262-STAGE-001
- **NFR-0150** owner=tracks/server/projections_core.py:AC-NFR0150-02@v0.9 surface=/api/runs/{id}/ac-chain + run detail 查询 composition=_chain_rows 空 acs 先决短路（在 _run_events/project_state 之前 return []） wiring=0-AC run 的 ac-chain 请求→短路返回→p95 <1s（目标 <5ms）→run detail 同样受益 test=integration:tests/integration/test_hotfix262_ui_perf.py::test_ac_chain_short_circuits_empty_registry evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix262_ui_perf.py` 输出 passed 且 0-AC run 的 ac-chain 响应 <5ms、无事件重放调用 IF-HOTFIX262-UI-001
- **FR-0333** owner=tracks/server/static/app/views.js+tracks/server/projections_core.py:AC-FR0333-01@v0.11 surface=Releases 窗口 + Todos inbox composition=project_todos 统一 inbox（awaiting 集 ∪ attention blocks ∪ evaluated intakes）；releases.js 渲染 display_title wiring=打开 Releases→sidebar 分组呈现→选中 run 进入详情→Todos 卡片可点击跳转（`<version> · <title> — <stage> · <kind_label>`） test=integration:tests/integration/test_hotfix262_ui_perf.py::test_unified_todos_inbox_coverage + e2e:tests/e2e/test_hotfix262_release_ui.py::test_todos_cards_clickable_readable evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix262_ui_perf.py` 输出 passed 且 inbox 覆盖全部 awaiting 状态+attention blocks+evaluated intakes、data-testid=todo-* 保留 IF-HOTFIX262-UI-001
- **FR-0340** owner=tracks/server/projections_run.py+tracks/server/static/app/releases.js:AC-FR0340-01@v0.11 surface=backlog sidebar 折叠组 composition=_backlog_group(conn, repo_path) join intakes.report_json.title；service_intake 存储 title 入 backlog.recorded payload；fallback=首行非空内容（去 #/-/*/>）截断；最终 fallback 本地化"待办条目 (<version/date>)"；永不 ULID wiring=backlog.recorded→sidebar 聚合呈现可读标题→归档区分可查 test=integration:tests/integration/test_hotfix262_ui_perf.py::test_backlog_title_never_ulid evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix262_ui_perf.py` 输出 passed 且无 title 样本呈现本地化 fallback、任何路径不渲染 ULID IF-HOTFIX262-UI-001
- **FR-0307** owner=tracks/server/projections_core.py+tracks/server/static/app/views.js:AC-FR0307-01@v0.9 surface=待办中心（Todos inbox） composition=project_todos 统一列表项 {version,title,stage,kind_label,summary/context,updated_ts,action_ref} wiring=合法人工决定（批准/发布等）出现于待办中心→附理由/上下文/跳转入口→candidate 可发布即出现发布待办 test=integration:tests/integration/test_hotfix262_ui_perf.py::test_todos_items_carry_context_and_action_ref evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix262_ui_perf.py` 输出 passed 且列表项字段齐备、action_ref 可导航 IF-HOTFIX262-UI-001

## 2. Scaffold 宣言

本节是本 delta 在宿主项目创建/修改文件的唯一合同。以下为声明/配置/数据，无业务行为；行为实现全部是 Devon 任务。

- `tracks/executor/full_f_authority.py` — 接口桩：`is_authoritative_full_f(payload: Mapping, candidate_sha: str | None = None) -> bool`，行为体仅 `raise NotImplementedError("IF-HOTFIX262-FULLF-001")`（kind: stub）
- `tracks/executor/review_findings.py` — 接口桩：`has_blocking_findings(findings: Iterable[Mapping]) -> bool`（blocker/high/critical 判定），行为体仅 `raise NotImplementedError("IF-HOTFIX262-REVIEW-001")`（kind: stub）
- `tracks/executor/host_env.py` — 接口桩：`host_process_env(extra: Mapping | None = None, contract_allowlist: Iterable[str] = ()) -> dict`，行为体仅 `raise NotImplementedError("IF-HOTFIX262-HOSTENV-001")`；模块 docstring 内以注释登记 INV-8 的 base allowlist 与 strip 集（kind: stub）

`tests/ground_truth/` 不适用（test-plan §3 判定）。既有模块（§1.0.1 表 #4..#11）的修订不是 scaffold，属于 Devon 实现任务；`pyproject.toml`/`tracks/__init__.py` 的版本 bump（0.12.1）由 M-VERIFY 前 version gate 机制承载，不属于本设计 scaffold。v0.12.0 erratum 文档（`.tracks/projects/v0.12/errata-v0.12.0.md`）**不是** M-DESIGN scaffold：它是发布期 artifact，由 human 评审内容后按 design-delta §7.6 在发布流程中提交（并 `gh release edit v0.12.0` 附加到 release notes），本设计只声明其内容合同（§1h 与 interfaces §3），不在本阶段写盘。

## 3. 技术选型

零新运行时依赖。谓词/包装只用标准库；进程边界包装复用 `os.environ`/`subprocess` 既有调用面；UI 修订在既有静态 JS/CSS 内（无新构建链）；legacy 时间线合成在投影层与既有 `timeline.js` 折叠函数内完成。备选「DB 迁移修正历史」被 INV-10 否决（历史不可变，消费端谓词 + erratum 替代）。备选「denylist 补丁」被 INV-8 否决（allowlist 唯一来源，denylist 不可枚举）。

## 4. 交付与运行合同（machine contracts）

### 4.1 测试执行合同（`.tracks/projects/project.toml`）

逐字继承 v0.12（本 delta 零 bytes 变更）：`[integration]` framework=pytest、paths=`["tests/integration/"]`、collect=`.venv/bin/python -m pytest --collect-only tests/integration/`、run=`.venv/bin/python -m pytest tests/integration/ --tb=short -q -n 4 --dist loadscope --junitxml={result}`、cwd=`.`；`[e2e]` paths=`["tests/e2e/"]`、collect 带 `-m 'not performance'`、run `-n 8`。`ui` marker 通道隔离与电池墙钟继承。本 delta 新测试全部落既有 paths，合同不扩容。

### 4.2 CI / pre-commit / release

- Required checks 七名不变；`release-evidence` milestone 硬门禁不变；零 workflow 变更。新 integration 资产落 `test` job；`ui` 标记的 e2e 落 `ui-e2e` job。
- pre-commit path-scoped lint-only 继承；新增 `tracks/executor/full_f_authority.py`/`review_findings.py`/`host_env.py` 自动纳入 ruff/flake8/pylint/coverage scope。
- 质量守卫 registry 继承 v0.12 §4.2 的 TOML block；`pyproject.toml` 版本 bump（0.12.1）触发的六条 pyproject-backed `config_digest` 重锚按既有机制在 M-VERIFY 前执行，不属本设计变更集。
- version gate：`pyproject.toml:project.version` 与 `tracks/__init__.py:__version__` 绑定 `{release_version}=0.12.1`。
- 发布流程：v0.12.1 走正常 preview→authorization→publish→milestone；tag `v0.12.1` 新建；v0.12.0 tag/release 不可变；erratum（interfaces §3 #4）在发布期提交并附加到 v0.12.0 release notes（`gh release edit v0.12.0`，不 retag、不改 artifact）；AWAITING_RELEASE human 门不变。

## 5. 有意识简化与风险

### 5.1 有意识简化

- 不做 DB 迁移/历史改写（INV-10）；legacy preview 的容忍以 blob 记录的键集 schema 为限，无 schema 元数据的更老 blob 回退到 legacy 单键比对（design-delta R2，已接受的残留）。
- `host_process_env` base allowlist 可能遗漏个别宿主机 exotic 工具链变量；以 `[host-contract].env_allowlist` escape hatch 兜底，不打补丁（design-delta R1）。
- `release_evidence._REQUIRED_PHASES` 不作为 v0.12.1 的阻塞门禁（hotfix/verification-only run 会误阻断）；保留 cleanup + 必需键检查，门禁化留 v0.13。
- 阶段事件只覆盖 M-RELEASE→M-PUBLISH→M-MILESTONE 三段；其余阶段转移语义不变。

### 5.2 风险

- **谓词回放回归**：`is_authoritative_full_f` 重算历史 run 的权威判定可能改变旧 run 的 closure 报告；这是本 hotfix 的预期校正（5896 拒绝/7362 信用），由 replay 切片测试与 erratum 固定。
- **ingest 拒绝的兼容性**：历史 pass+blocking verdict 在回放中会被重新判拒；只影响消费端判定，不改写事件。
- **阶段事件重复风险**：`retry_tail` 重入下由 visit 守卫保证 exactly-once；kernel reducer 语义不变（entered 在 publish.planned 之前，`_on_stage_entered` reset 一致）。
- **legacy fallback 与显式事件并存**：投影合成必须先检查显式事件存在性，显式事件优先，合成只补缺；重复合成的风险由合成规则以 `current` 状态为前置控制。
- **多版本锚定的 M-DESIGN EXIT 校验**：本 delta §8 的 26 条锚定 AC 分布于 v0.4～v0.11 六个版本的 acceptance.md；per-version 存在性解析是 IF-HOTFIX-004 既定语义（`validate_anchor_refs`/`check_hotfix_plan_closure` 均已实现 per-version 解析）。若 EXIT 期 test_tasks 合同校验仅按 baseline（v0.12）acceptance 判定 unknown-AC，则该判定属于消费端合同缺口（与本 hotfix 修复的 anchor 校验族同源），按 test-plan §8 的锚定集合解析合同处理；delta 三文档本体已逐条通过 `trac validate --file` 的 template/schema 面（architecture/interfaces valid；test-plan 的 template/schema/trace 面无其它问题）。
