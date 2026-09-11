"""Command executor: write-ahead `command.issued` (FR-30), per-kind
execute + reconcile (D-13), agent dispatch via the effects backend seam
(NFR-01; ARCH-003 §4), validate pass-through (D-16).
"""


import fnmatch
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import tomllib

from tracks import paths
from tracks.baseline import baseline_summary, revision_digest
from tracks.discuss.locate import locate
from tracks.discuss.parser import parse_threads
from tracks.effects import oob, select_backend
from tracks.effects import publish as publish_effects
from tracks.effects.backend import valid_test_tasks
from tracks.effects.dispatch_parity import (
    is_declared,
    static_parity_check,
    static_parity_referenced,
)
from tracks.effects.github import (
    FakeIssueBackend,
    GithubIssuesError,
    close_issue,
    create_issue_verified,
    issue_items,
    judge_ci_binding,
    persist_issue_mapping,
    readback_ci_run,
    reject_fake_artifact,
    select_issue_backend,
)
from tracks.effects.github import (
    close_project_milestone as close_project_milestone_api,
)
from tracks.executor import m_verify
from tracks.executor import repair as _repair
from tracks.executor.breaker import RunBreaker
from tracks.executor.code_stamp import code_stamp
from tracks.executor.doc_comment import (
    DocCommentOrigin,
    QuarantinedChange,
    QuarantineDescriptor,
    ResumeDecision,
    combined_design_identity,
    decide_quarantine_resume,
)
from tracks.executor.doc_face import (
    _NEXT_STAGE,  # noqa: F401
    ExecDocMixin,
)
from tracks.executor.doc_gap_runtime import (
    DocGapCapabilities,
    DocGapRuntime,
    legal_delta_targets,
)
from tracks.executor.escape import quarantine_late_outcome
from tracks.executor.failure_review import (
    acknowledge_failure,
    inject_into_assignment,
    next_failure_id,
    record_failure,
    review_failure_chain,
    select_failure,
    supersede_acked,
)
from tracks.executor.helpers import (
    _DIAGNOSE_TARGET,
    _dispatch_payload,
    _scoped_commit_if_staged,
    git,
)
from tracks.executor.host_contract import (
    CANONICAL_CONTRACT_RELPATH,
    NormalizedGateResult,
    execute_gate,
    load_host_contract,
    validate_host_contract,
)
from tracks.executor.hotfix_face import (
    ExecHotfixMixin,
    _hotfix_state_line,
    _resolve_run_version,
    hotfix_entry_output,  # noqa: F401
    hotfix_feature_route,  # noqa: F401
    precheck_hotfix_report,
)
from tracks.executor.local_gate_evidence import (
    gate_identity,
    has_complete_passed_gates,
    normalized_result_payload,
)
from tracks.executor.m_impl_runtime import MImplRuntimeMixin
from tracks.executor.milestone import (
    build_release_trace,
    clean_temp_refs,
    close_issues_with_comment,
    close_project_milestone,
    release_trace_comment,
    seal_evidence_readonly,
    skipped_issue_entries,
)
from tracks.executor.observe import ExecObserveMixin
from tracks.executor.publish_runtime import execute_publish_operations, resolve_publish_authority
from tracks.executor.release_gate import (
    active_release_branch,
    build_operation_plan,
    complete_version_facts,
    operation_plan_needs_n,
    remote_patch_n,
    version_facts,
)
from tracks.executor.release_preview import (
    _contract_table,
    _journey,
    _version_facts,
    assemble_preview,
    preview_blob_matches,
    preview_inputs_match,
)
from tracks.executor.result_checkpoint import ResultCheckpointMixin
from tracks.executor.run_loop import ExecRunLoopMixin
from tracks.executor.security import (
    aggregate_security_status,
    run_security_scans,
)
from tracks.executor.stall import CommandStallTracker
from tracks.executor.test_collect import (
    _R2_BASIS,  # noqa: F401
    _R2_SCOPE,  # noqa: F401
    ExecTestCollectMixin,
    _resolve_contract_argv0,  # noqa: F401
)
from tracks.executor.test_execute import ExecTestRunMixin
from tracks.executor.test_select import (
    TestResultError,
    TestSelectError,
    collect_node_source_digests,
    make_selection_id,
    rebuild_ledger,
)
from tracks.executor.worktree import (
    _RUNTIME_ASSETS,
    WorktreeHandle,
    _writer_worktree_path,
    cleanup_worktree,
    create_devon_worktree,
    create_test_authority_worktree,
    ensure_runtime_assets,
    seed_worktree_with_cycle_wip,
)

# T-001 face (E) wiring seam (architecture §1.1 Envelope/failure chain —
# executor.py is the single writer/consumer): the kernel envelope faces and
# the failure-review chain are imported here so the dispatch loop consumes
# them; their behavior bodies land with IF-ENVELOPE-001/002 and the
# failure-chain anchors (T-007/T-035).
from tracks.kernel.envelope import (
    DIAGNOSE_KINDS,
    EnvelopeFormatError,
    parse_agent_output,
    validate_envelope,
)
from tracks.kernel.events import Command
from tracks.kernel.machine import (
    _REVIEW_SUBSTATE,
    State,
    canonical_stage_order,
)
from tracks.project import (
    ContractError,
    lint_check_command,
    load_contract,
)
from tracks.project import layout_paths as _layout_paths
from tracks.store import Store, new_ulid

# Runtime-materialized host contract (IF-HOSTCONTRACT-001): when the host
# declares no ``[host-contract]`` table of its own, the M-VERIFY chain
# materializes this baseline so the release pipeline can proceed — the
# runtime executes only what it materialized (never guessed host language
# semantics). An undeclared host MUST NOT pass quality verification
# silently, but its quality face cannot be guessed either: the
# materialized default gate is a declared no-op (``true``) and the
# fail-closed stop moves to the CI face, where the host's credentials
# stand-in (TRAC_GITHUB_REPO / TRAC_GITHUB_API_BASE / GITHUB_TOKEN) decides
# the A-class outcome (missing credentials -> attention.required
# reason=missing_token, never a silent pass) — every downstream producer
# stays reachable from the fixture environment while an unseen
# quality/toolchain claim still fails at its natural gate. Persisted
# under .tracks/runtime (untracked, never dirties the frozen tree) with
# the host_contract.materialized event recording its digest and source.
_DEFAULT_HOST_CONTRACT_TOML = """\
[host-contract]
version = 1
language = "runtime-default"
toolchain = "runtime-default"
install = "true"

[[host-contract.local_gate]]
kind = "quality"
command = "true"
result_channel = "exit_code"
timeout_seconds = 60

[[host-contract.security_scan]]
id = "runtime-default-scan"
tool = "runtime-default"
install = "true"
command = "true"
result_channel = "exit_code"
threshold = "violations=0"
timeout_seconds = 60

[host-contract.ci]
repo_env = "TRAC_GITHUB_REPO"
workflow = "ci.yml"
required_checks = []
conclusion = "success"

[host-contract.tracker]
repo_env = "TRAC_GITHUB_REPO"
project_env = "TRAC_GITHUB_PROJECT"

[host-contract.operations.feature]
steps = ["merge:main"]

[host-contract.operations.post_release]
steps = ["merge:main"]

[host-contract.operations.dev]
steps = ["merge:main"]
"""



# T-015 v0.7 runtime assembly (architecture §1.0.9/§1.1): the Executor is the
# composition root. Importing this module re-registers the v0.7 extension
# delivered by ``v07_runtime`` (T-013) with the Phase 0 entry-guard
# capability added on top -- the T-013 file itself is consumed by import
# only and never modified. Re-registering the same version replaces its
# binding idempotently (version_extensions contract), so the trace /
# island_gate_2 / render_closure capabilities are inherited unchanged and
# kernel.machine's generic seam call point can resolve ``before_mtest``.
from tracks.executor import v07_runtime as _v07_runtime  # noqa: E402
from tracks.executor import version_extensions as _version_extensions  # noqa: E402


class _V07RuntimeExtension(_v07_runtime.V07Extension):
    """v0.7 extension assembled with the Phase 0 entry guard (T-015).

    ``before_mtest`` delegates to the T-004 ``kernel.phase0.decide_phase0``
    entry guard (pure, no I/O): any non-SEALED Phase 0 projection routes to
    ``Command(phase0_validate)``; SEALED/BLOCKED park (§1c).

    ``after_m_impl`` is declared explicitly as the below-threshold no-route:
    v0.7 predates the RELEASE pipeline, so the M-IMPL boundary completes the
    run instead of re-routing to M-VERIFY (FR-0267 keeps below-threshold
    versions byte-identical). Without the explicit callback the required
    capability resolution fail-closes v0.7 runs at M-IMPL/EXIT with
    CapabilityBlockedError — a stall, not the contracted boundary completion.
    """

    @staticmethod
    def before_mtest(state):
        from tracks.kernel.phase0 import decide_phase0

        return decide_phase0(state)

    @staticmethod
    def after_m_impl(state):
        return None


_version_extensions.register_extension(
    _v07_runtime.EXTENSION_VERSION, _V07RuntimeExtension()
)


def island_gate_2_dispatch(version, arguments=(), **kwargs):
    """ISLAND_GATE_2 capability call point (IF-FAILCLOSED-001).

    Resolves the target version's ``island_gate_2`` callback on the generic
    capability seam (architecture 1.0.9 / interfaces 1b kind 4) and invokes
    it with *arguments* plus keywords -- through the registered v0.7
    extension (T-013) this lazily dispatches ``demonstrate_failclosed``
    (T-016).  Early versions select no callback and return None, keeping
    classic behaviour byte-identical (FR-0264-02); a registered extension
    that lacks the callback fails closed via ``CapabilityBlockedError``.
    """
    callback = _version_extensions.resolve_capability(version or "", "island_gate_2")
    if callback is None:
        return None
    return callback(arguments, **kwargs)

# Baseline requirement documents digested into the Phase 0 seal manifest
# (§1c phase0.sealed document_digests input): the six project templates.
_PHASE0_BASELINE_DOCS = (
    "story.md",
    "spec.md",
    "acceptance.md",
    "architecture.md",
    "interfaces.md",
    "test-plan.md",
)


def _phase0_baseline_version(version: str | None) -> str | None:
    """Previous minor of a ``vX.Y`` run version (v0.7 -> v0.6, §1c
    phase0_validate inputs); None when the shape carries no baseline."""
    body = (version or "").removeprefix("v").split(".")
    if len(body) != 2 or not all(part.isdigit() for part in body):
        return None
    major, minor = int(body[0]), int(body[1])
    if minor <= 0:
        return None
    return f"v{major}.{minor - 1}"


def _phase0_planned_bindings(baseline_dir: Path) -> dict[str, str]:
    """AC -> planned node bindings from the baseline test-plan §8 rows (§1d
    scan input); only node-level ``path::test`` targets bind (file-only cells
    carry no node identity)."""
    plan = baseline_dir / "test-plan.md"
    if not plan.is_file():
        return {}
    from tracks.executor.test_tasks import _coverage_rows_with_test

    return {
        ac: test.strip()
        for ac, _layer, test, _if_ids in _coverage_rows_with_test(
            plan.read_text(encoding="utf-8")
        )
        if test and "::" in test
    }


def _phase0_approved_acs(baseline_dir: Path) -> set[str]:
    """Approved AC ids from the baseline acceptance.md (§1d scan input)."""
    acc = baseline_dir / "acceptance.md"
    if not acc.is_file():
        return set()
    from tracks.executor.test_tasks import _known_ac_ids

    return _known_ac_ids(acc.read_text(encoding="utf-8"))


def _phase0_document_digests(baseline_dir: Path) -> dict[str, str]:
    """sha256 of every present baseline requirement document (seal input)."""
    digests: dict[str, str] = {}
    for name in _PHASE0_BASELINE_DOCS:
        path = baseline_dir / name
        if path.is_file():
            digests[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digests


def _phase0_frozen_test_digests(repo: Path, frozen_paths: list[str]) -> dict[str, str]:
    """sha256 of every frozen test file under the declared layer paths."""
    digests: dict[str, str] = {}
    for declared in frozen_paths:
        root = repo / declared.rstrip("/")
        if not root.is_dir():
            continue
        for file in sorted(root.rglob("*")):
            if file.is_file():
                digests[str(file.relative_to(repo))] = hashlib.sha256(
                    file.read_bytes()
                ).hexdigest()
    return digests



# reviewer role -> its verdict event type
_VERDICT_EVENT = {"sage": "sage.verdict", "lex": "lex.verdict", "prism": "prism.verdict"}

# D-29 criteria pack identity (architecture.md §3.4): echoed by Prism and
# read back by the executor to enforce the anti-self-report triple.
_CRITERIA_PACK = {"name": "tracks-prism-test", "version": "0.1"}

# D-35 / PRISM-D35-R1-ADV1 review fields threaded into the M-DESIGN
# prism.verdict publish (kept in parity with the ResultCheckpointMixin
# publish branches; the hotfix override below prepends anchor_verdict,
# FR-0243 / IF-HOTFIX-009, interfaces §1a).
_DESIGN_PRISM_THREAD_KEYS = ("review_summary", "findings", "review_ref", "discussion_refs")

# IF-JOURNEY-001 §1m: the version_scheme label that derives each journey's
# tag/release target (architecture §1.0.3/§1.0.8). A tag/release step target
# must be exactly this template rendered with the run's version facts.
_JOURNEY_VERSION_TEMPLATE = {
    "feature": "feature_tag",
    "post_release": "patch_line",
    "dev": "prerelease_tag",
}
_VERSION_STEP_KINDS = ("tag", "release")





_SAGE_OUTCOME_FIELDS = (
    "acs",
    "outcome",
    "searched_versions",
    "corpus_digests",
    "rationale_refs",
)


def _sage_anchor_outcome_fields(result: dict) -> dict:
    """The anchor-search fields a SAGE_TRIAGE outcome carries into the
    persisted outcome.received (IF-HOTFIX-004)."""
    return {key: result[key] for key in _SAGE_OUTCOME_FIELDS if result.get(key) is not None}




def hotfix_entry_run(repo, store, issue_number: int, scenario: str) -> str:
    """v0.6 hotfix entry (IF-HOTFIX-001, interfaces §2a #1/#2): establish the
    hotfix run with its resolved identity version (``{target}-hotfix-{issue}``,
    ARCH-006 §1.0.3) and drive the synchronous triage run_loop. A REJECTED
    precheck still establishes the run for audit (SM-01.1/.4) — the loop
    completes it immediately. Returns the run_id."""
    report, _issue = precheck_hotfix_report(repo, store, issue_number, scenario)
    run_id = new_ulid()
    version = (
        f"{report.target_version}-hotfix-{issue_number}"
        if report.status == "pass"
        else f"hotfix-{issue_number}"
    )
    store.append(run_id, version, "hotfix.requested", {"issue": issue_number, "scenario": scenario})
    store.append(run_id, version, "stage.entered", {"stage": "M-HOTFIX-TRIAGE"})
    ex = Executor(store, repo, run_id)
    ex.version = version  # pre-PRECHECK state cannot re-derive the target
    ex.run_loop()
    return run_id






def hotfix_human_anchor(repo, store, run_id: str, refs: list[str]) -> int:
    """Form #4 (interfaces §2a): human manual anchor of cross-version AC refs
    on the active hotfix run at awaiting=hotfix_triage. human.anchor(manual) ->
    validate_anchor -> ANCHORED completion (SM-01.8). Untruthful refs keep the
    run at AWAIT_HUMAN (retryable) and exit 1."""
    state = store.state(run_id)
    version = _resolve_run_version(state)
    actor = git(repo, "config", "user.name", check=False).stdout.strip() or "Human"
    store.append(
        run_id, version, "human.anchor",
        {
            "mode": "manual",
            "acs": list(refs),
            "issue": state.hotfix_issue,
            "actor": actor,
        },
    )
    state = Executor(store, repo, run_id).run_loop()
    if state.awaiting == "hotfix_triage":
        print(
            "anchor invalid: refs do not resolve to existing ACs in the "
            "referenced versions; retry with valid refs",
            file=sys.stderr,
        )
        return 1
    print(f"anchor recorded: {', '.join(refs)}")
    print(f"run {run_id}: {_hotfix_state_line(state)}")
    return 0



def _assignment_budget() -> int:
    """M5: the dispatch-side assignment byte budget (0 disables).

    Default 16384, calibrated against the POST-enrichment materialized
    card (the bytes the backend actually receives). Healthy writer cards
    legitimately run ~6-12KB (task payload + manifest + ref lists), so
    16KB keeps the teeth on the pathological class (b92's 50KB+ prompt
    blowups) with margin while never false-firing on a healthy card;
    authority-role (archer/prism) cards run ~1-3KB since the M5 card diet.
    Environment-overridable via TRAC_ASSIGNMENT_BUDGET for test fixtures
    and emergency bumps."""
    try:
        return int(os.environ.get("TRAC_ASSIGNMENT_BUDGET", "").strip() or 16384)
    except ValueError:
        return 16384


class Executor(
    MImplRuntimeMixin,
    ExecObserveMixin,
    ExecDocMixin,
    ExecHotfixMixin,
    ExecRunLoopMixin,
    ExecTestCollectMixin,
    ExecTestRunMixin,
    ResultCheckpointMixin,
):
    """Drives one run: project -> decide -> issue -> execute -> observe."""

    def __init__(
        self,
        store: Store,
        repo: Path,
        run_id: str,
        assignment_overlay: dict | None = None,
        max_dispatches: int | None = None,
    ):
        if assignment_overlay is not None and not isinstance(assignment_overlay, dict):
            raise TypeError("assignment_overlay must be a dict or None")
        if max_dispatches is not None and (
            isinstance(max_dispatches, bool)
            or not isinstance(max_dispatches, int)
            or max_dispatches < 1
        ):
            raise ValueError("max_dispatches must be a positive integer or None")
        self.store = store
        self.repo = repo
        self.run_id = run_id
        self.version = _resolve_run_version(store.state(run_id))
        # run_id 传入 opencode backend 启用 D-39 用户简化版（#44）session
        # 复用（fake backend 忽略该参数）。
        self.backend = select_backend(repo, self.version, run_id=run_id)
        self.assignment_overlay = deepcopy(assignment_overlay)
        self.max_dispatches = max_dispatches
        self._issue_backend = None  # lazy: created on first create_issues
        # D-36 浅版（#43）：OOB 观察点。初始化为当前 HEAD——run 停止期间
        # （静默期）的操作者提交按既有语义由下一次 baseline 冻结吸收，
        # 不发 oob.accepted；只有本进程存活期间（派发窗口内/外）观察到
        # 的前移才入账。
        self._oob_head = oob.head_sha(Path(self.repo))
        # B43（#45）：进程代码版本戳。宿主项目（无 tracks/ 包）为 None，
        # 漂移检查跳过。
        self._code_stamp = code_stamp(Path(self.repo))
        # B44（#46）：run 级熔断器计数（窗口：上次 human.retry/recover 之后；
        # 进程重启后从事件流重播种子，计数不丢）。
        self._breaker = RunBreaker.from_events(store.events(run_id))
        # B86/B88（#77）：非派发命令紧循环熔断（STALL）。循环内内存计数，
        # 无需持久化（崩溃重启清零可接受——紧循环 20 次在数秒内达成）。
        self._stall = CommandStallTracker()
        # SM-02 doc-gap lifecycle runtime (physical slice 1, DESIGN.md
        # appendix 2026-09-09): the capture -> classify -> quarantine ->
        # nested design revision -> adjudication-ingestion lifecycle lives in
        # tracks/executor/doc_gap_runtime.py behind this explicit narrow
        # capability facade. Only genuinely external capabilities are
        # injected (repo/run_id/store/backend/emit/issue/doc_path/
        # path_identity/dirty_snapshot/dispatch_issued/layout_paths) — no
        # complete Executor, no mixin, no reverse import. Executor keeps
        # thin same-name forwarders for dynamic dispatch and API stability.
        self._doc_gap = DocGapRuntime(
            DocGapCapabilities(
                repo=repo,
                run_id=run_id,
                store=store,
                backend=self.backend,
                emit=self._emit,
                issue=self.issue,
                doc_path=self._doc_path,
                path_identity=self._path_identity,
                dirty_snapshot=self._dirty_snapshot,
                dispatch_issued=self._dispatch_issued,
                layout_paths=_layout_paths,
            )
        )

    # -- main loop (FR-29/FR-30) -------------------------------------------

    # NOTE (B32/#32): recover_stage deliberately gets NO similar boundary
    # special case. Unlike the stub_gap rollback -- which would auto-reenter a
    # defective downstream cycle -- recover_stage is the HUMAN-CHOSEN forward
    # path back into M-IMPL (stage.recovered resets to BASELINE and Archer
    # re-decomposes the task graph). run_loop may continue in the same
    # invocation; nothing requires an external correction first.

    # -- per-kind handlers ---------------------------------------------------

    def _handle_no_diff_outcome(self, substate, result, cmd, task_id, state):
        """v0.5 no_diff peer review: emit explain/review events without
        entering the ResultCheckpoint pipeline. Returns True if handled."""
        if substate not in ("NO_DIFF_EXPLAIN", "NO_DIFF_REVIEW"):
            return False
        result_id = (state.active_result or {}).get("result_id")
        if substate == "NO_DIFF_EXPLAIN":
            if result.get("status") == "done":
                self._emit(
                    "no_diff.explained",
                    {"explanation": result.get("self_report", ""), "result_id": result_id},
                    command_id=cmd.command_id,
                    task_id=task_id,
                )
            else:
                # Failed explanation: treat as a rejected review (revise).
                self._emit(
                    "no_diff.reviewed",
                    {"verdict": "revise", "result_id": result_id},
                    command_id=cmd.command_id,
                    task_id=task_id,
                )
        else:  # NO_DIFF_REVIEW
            verdict = (
                result.get("verdict", "revise") if result.get("status") == "done" else "revise"
            )
            self._emit(
                "no_diff.reviewed",
                {"verdict": verdict, "result_id": result_id},
                command_id=cmd.command_id,
                task_id=task_id,
            )
        return True

    def _do_dispatch_agent(self, cmd, state, task_id, reconcile):
        p = cmd.params
        # SM-02 design_gap nested workflow (#62 finding 1): doc_gap dispatches
        # (Archer revision, Prism review) route to a dedicated handler that
        # checkpoints the outcome without emitting design.committed/prism.verdict
        # (those clobber origin stage state) and without a Human gate.
        if p.get("doc_gap"):
            self._do_doc_gap_dispatch(cmd, state, task_id, p)
            return
        role, substate, doc = p["role"], p["substate"], p.get("doc")
        doc_path = self._doc_path(doc) if doc else None
        assignment = p.get("assignment")
        assignment = self._assignment_with_evidence(assignment, p)
        materialization_error = (
            self._invalid_m_impl_assignment(
                role,
                substate,
                assignment,
            )
            if state.stage == "M-IMPL"
            else None
        )
        if materialization_error is not None:
            self._emit_stale_assignment(cmd, task_id, role, materialization_error)
            return
        # M5 (convergence plan 2026-09-05): dispatch-side hard budget on
        # the CARD itself. This is the POST-enrichment materialized card
        # (issue() already ran _materialize_m_impl_assignment), i.e. the
        # bytes the backend actually receives; the FR-11 evidence channel
        # merges afterwards in _assignment_with_evidence and is not the
        # card's liability. A card JSON over TRAC_ASSIGNMENT_BUDGET bytes
        # (default 16KB) is a structural task-graph defect (scope bloat:
        # revision archaeology and escalation add-ons living in the prompt
        # instead of the event log, b92's 200-600k token dispatches).
        # Since the M5 card diet, non-writer (archer/prism) cards are lean
        # by construction (manifest=None, slim task identity): the
        # budget's teeth are for writer (devon/shield) cards, where an
        # oversized card means real prompt blowup.
        # Reject before any backend I/O; routes as plan_defect (scope
        # replan, no agent attempt burned).
        card = p.get("assignment")
        budget = _assignment_budget()
        card_bytes = (
            len(json.dumps(card, ensure_ascii=False, default=str))
            if isinstance(card, dict)
            else 0
        )
        if isinstance(card, dict) and budget and card_bytes > budget:
            # M5 audit-first rule (operator OOB 2026-09-06): the rejection
            # carries a per-section byte breakdown so the FIRST response to
            # an over-budget card is a dedup audit (which sections cost
            # what, which are boilerplate/history riding the card instead
            # of the event log), never a budget raise.
            sections = sorted(
                (
                    (key, len(json.dumps(value, ensure_ascii=False, default=str)))
                    for key, value in card.items()
                ),
                key=lambda item: item[1],
                reverse=True,
            )
            breakdown = ", ".join(f"{key}={size}B" for key, size in sections[:6])
            self._emit(
                "verdict.failed",
                {
                    "check": "plan_defect",
                    "target_stage": state.stage,
                    "task_id": task_id or state.current_task_id or "",
                    "reason": (
                        "assignment card over hard budget (M5): "
                        f"{card_bytes}"
                        f" bytes > {budget} -- dedup audit first (see "
                        "evidence breakdown), then split the card; the "
                        "event log carries the history, not the prompt"
                    ),
                    "evidence": (
                        "dispatch-side assignment budget check; section "
                        f"bytes [{breakdown}]; audit zero-reader duplicates "
                        "and boilerplate before re-planning"
                    ),
                    "attempt": state.current_attempt + 1,
                },
                command_id=cmd.command_id,
                task_id=task_id,
            )
            return
        if self._release_empty_hotfix_shield(cmd, state, task_id, role, substate, assignment):
            return
        # D-32: fail-closed M-DESIGN→M-TEST gate. After the persisted
        # assignment.test_tasks enrichment (issue()), an invalid test-task
        # contract must NOT reach the (production or fake) backend: emit a
        # stub_gap failed outcome instead, which the reducer routes straight
        # to DIAGNOSE/stub_gap → rollback M-DESIGN (no attempt, no Human).
        if self._reject_invalid_test_tasks(state, role, substate, assignment, cmd, task_id):
            return
        self._dispatch_agent_backend(cmd, state, task_id, role, substate, doc, doc_path, assignment)

    def _release_empty_hotfix_shield(
        self, cmd, state, task_id, role, substate, assignment
    ) -> bool:
        """Declare a unit-only hotfix M-TEST increment without Shield I/O."""
        if not (
            state.stage == "M-TEST"
            and state.hotfix_issue is not None
            and role == "shield"
            and substate == "WRITE"
            and isinstance(assignment, dict)
            and not assignment.get("test_tasks")
        ):
            return False
        from tracks.executor.test_tasks import parse_hotfix_unit_rows

        plan = self._vdir() / "test-plan.md"
        unit_rows = parse_hotfix_unit_rows(
            plan.read_text(encoding="utf-8", errors="replace") if plan.exists() else ""
        )
        anchors = set(state.hotfix_anchor_acs or [])
        if (
            not unit_rows
            or {row.get("ac") for row in unit_rows} != anchors
            or not self._valid_hotfix_unit_rows(unit_rows)
        ):
            return False
        self._emit(
            "red.validated",
            {"status": "valid", "findings": [], "basis": "unit-only hotfix increment"},
            command_id=cmd.command_id,
            task_id=task_id,
        )
        return True

    @staticmethod
    def _assignment_with_evidence(assignment: dict | None, params: dict) -> dict | None:
        # D-35: carry the dispatch's stage into the assignment context so the
        # backend can apply stage-scoped review contracts (M-TEST/M-IMPL
        # structured findings vs M-DESIGN doc-anchored threads, SC-D35 §2.1).
        enriched_stage = params.get("stage")
        if params.get("evidence") is None and enriched_stage is None:
            return assignment
        enriched = dict(assignment or {})
        if params.get("evidence") is not None:
            enriched["evidence"] = params["evidence"]
        if enriched_stage is not None:
            enriched["stage"] = enriched_stage
        return enriched

    def _emit_stale_assignment(
        self,
        cmd: Command,
        task_id: str | None,
        role: str,
        reason: str,
    ) -> None:
        self._emit(
            "outcome.received",
            {"role": role, "status": "failed", "failure_class": "stale", "self_report": reason},
            command_id=cmd.command_id,
            task_id=task_id,
        )

    # -- SM-02 doc-comment-first (IF-DOCGAP-001 / IF-QUARANTINE-001) ---------

    def _snapshot_design_docs(self, role: str) -> dict[str, bytes]:
        """Forward to DocGapRuntime (physical slice 1, doc_gap_runtime.py)."""
        return self._doc_gap._snapshot_design_docs(role)

    def _capture_doc_gap_context(
        self, state: State, role: str
    ) -> tuple[dict[str, bytes], dict[str, str]] | None:
        """Forward to DocGapRuntime (physical slice 1, doc_gap_runtime.py)."""
        return self._doc_gap._capture_doc_gap_context(state, role)

    def _doc_gap_origin(
        self, state: State, role: str, substate: str, cmd: Command
    ) -> DocCommentOrigin:
        """Forward to DocGapRuntime (physical slice 1, doc_gap_runtime.py)."""
        return self._doc_gap._doc_gap_origin(state, role, substate, cmd)

    def _handle_doc_gap_outcome(
        self,
        cmd: Command,
        state: State,
        task_id: str | None,
        role: str,
        substate: str,
        result: dict,
        doc_gap: tuple[dict[str, bytes], dict[str, str]] | None,
    ) -> bool:
        """Forward to DocGapRuntime (physical slice 1, doc_gap_runtime.py)."""
        return self._doc_gap._handle_doc_gap_outcome(
            cmd, state, task_id, role, substate, result, doc_gap
        )

    def _rollback_doc_gap_round(
        self,
        baseline_documents: dict[str, bytes],
        pre_dirty: dict[str, str] | None,
    ) -> list[str]:
        """Forward to DocGapRuntime (physical slice 1, doc_gap_runtime.py)."""
        return self._doc_gap._rollback_doc_gap_round(baseline_documents, pre_dirty)

    def _reject_over_reach(
        self,
        cmd: Command,
        state: State,
        task_id: str | None,
        role: str,
        deltas: tuple,
        baseline_documents: dict[str, bytes],
        pre_dirty: dict[str, str] | None,
    ) -> None:
        """Forward to DocGapRuntime (physical slice 1, doc_gap_runtime.py)."""
        self._doc_gap._reject_over_reach(
            cmd, state, task_id, role, deltas, baseline_documents, pre_dirty
        )

    def _pause_for_legal_discussion(
        self,
        cmd: Command,
        state: State,
        task_id: str | None,
        role: str,
        substate: str,
        deltas: tuple,
        legal_deltas: list,
        pre_dirty: dict[str, str] | None,
    ) -> None:
        """Forward to DocGapRuntime (physical slice 1, doc_gap_runtime.py)."""
        self._doc_gap._pause_for_legal_discussion(
            cmd, state, task_id, role, substate, deltas, legal_deltas, pre_dirty
        )

    def _quarantine_legal_changes(
        self,
        state: State,
        origin: DocCommentOrigin,
        record,
        pre_dirty: dict[str, str] | None,
    ) -> tuple:
        """Forward to DocGapRuntime (physical slice 1, doc_gap_runtime.py)."""
        return self._doc_gap._quarantine_legal_changes(state, origin, record, pre_dirty)

    def _legal_delta_targets(self, legal_deltas: list) -> tuple[list[str], list[str]]:
        """Forward to the pure doc_gap_runtime.legal_delta_targets function."""
        return legal_delta_targets(legal_deltas)

    def _emit_doc_gap_pause(
        self,
        cmd: Command,
        task_id: str | None,
        origin: DocCommentOrigin,
        legal_deltas: list,
        record,
        descriptor,
        manifest_ref: str | None,
    ) -> None:
        """Forward to DocGapRuntime (physical slice 1, doc_gap_runtime.py)."""
        self._doc_gap._emit_doc_gap_pause(
            cmd, task_id, origin, legal_deltas, record, descriptor, manifest_ref
        )

    # -- SM-02 design_gap nested workflow (#62 finding 1) --------------------

    def _sm02_pre_decide(self, state: State) -> bool:
        """Run the SM-02 doc-gap lifecycle checks before decide().

        Returns True when any check handled the iteration (continue the run
        loop).  Order: adjudication ingestion -> nested design-revision ->
        thread-resolution resume -> crash-window recovery.
        """
        if self._ingest_doc_gap_adjudications(state):
            return True
        if self._advance_doc_gap_design_revision(state):
            return True
        if self._resume_doc_gap_if_ready(state):
            return True
        return bool(self._resume_interrupted_dispatch(state))

    def _advance_doc_gap_design_revision(self, state: State) -> bool:
        """Forward to DocGapRuntime (physical slice 1, doc_gap_runtime.py)."""
        return self._doc_gap._advance_doc_gap_design_revision(state)

    def _recover_doc_gap_dispatch(
        self, record_id: str, rec: dict, state: State, revision: str
    ) -> bool:
        """Forward to DocGapRuntime (physical slice 1, doc_gap_runtime.py)."""
        return self._doc_gap._recover_doc_gap_dispatch(record_id, rec, state, revision)

    def _dispatch_doc_gap_agent(
        self, record_id: str, rec: dict, state: State, *, phase: str,
        dispatch_id: str | None = None, emit_audit: bool = True,
    ) -> None:
        """Forward to DocGapRuntime (physical slice 1, doc_gap_runtime.py)."""
        self._doc_gap._dispatch_doc_gap_agent(
            record_id, rec, state, phase=phase,
            dispatch_id=dispatch_id, emit_audit=emit_audit,
        )

    def _do_doc_gap_dispatch(
        self, cmd: Command, state: State, task_id: str | None, p: dict
    ) -> None:
        """Forward to DocGapRuntime (physical slice 1, doc_gap_runtime.py)."""
        self._doc_gap._do_doc_gap_dispatch(cmd, state, task_id, p)

    def _checkpoint_doc_gap_revision(
        self,
        cmd: Command,
        task_id: str | None,
        record_id: str,
        rec: dict,
        document_paths: list[str],
        result: dict,
    ) -> None:
        """Forward to DocGapRuntime (physical slice 1, doc_gap_runtime.py)."""
        self._doc_gap._checkpoint_doc_gap_revision(
            cmd, task_id, record_id, rec, document_paths, result
        )

    def _fail_doc_gap_revision(
        self, cmd, task_id, record_id: str, phase: str, reason: str
    ) -> None:
        """Forward to DocGapRuntime (physical slice 1, doc_gap_runtime.py)."""
        self._doc_gap._fail_doc_gap_revision(cmd, task_id, record_id, phase, reason)

    def _commit_doc_gap_revision(self, changed_paths: list[str]) -> str:
        """Forward to DocGapRuntime (physical slice 1, doc_gap_runtime.py)."""
        return self._doc_gap._commit_doc_gap_revision(changed_paths)

    def _ingest_doc_gap_adjudications(self, state: State) -> bool:
        """Forward to DocGapRuntime (physical slice 1, doc_gap_runtime.py)."""
        return self._doc_gap._ingest_doc_gap_adjudications(state)

    def _ingest_record_adjudication(self, record_id: str) -> bool:
        """Forward to DocGapRuntime (physical slice 1, doc_gap_runtime.py)."""
        return self._doc_gap._ingest_record_adjudication(record_id)

    def _resume_doc_gap_if_ready(self, state: State) -> bool:
        """SM-02.9: resume an adjudicated doc-gap whose threads are resolved.

        Scans adjudicated doc-gap records (DESIGN_GAP / AGENT_CORRECTION —
        both routes converge on thread closure). If every thread_id in the
        record is resolved in the current doc text, emits the
        decide_quarantine_resume decision pair and issues a NEW dispatch for
        the same logical role/task/phase. Open threads stay paused
        (NFR-0090-02 fail-closed). Shield pauses fire in M-TEST WRITE, Devon
        RGR pauses in M-IMPL — both stages resume here.
        """
        if state.stage not in ("M-IMPL", "M-TEST") or state.status != "active":
            return False
        for record_id, rec in state.doc_gaps.items():
            if rec.get("state") not in ("DESIGN_GAP", "AGENT_CORRECTION"):
                continue
            # SM-02 design_gap nested workflow (#62 finding 1): a DESIGN_GAP
            # record must complete the nested Archer revision + Prism review
            # (revision == "prism_reviewed") before thread resolution may
            # drive the resume - otherwise an Archer revision that resolves
            # threads early would short-circuit past Prism's review.
            if (
                rec.get("state") == "DESIGN_GAP"
                and rec.get("revision") != "prism_reviewed"
            ):
                continue
            if not self._threads_resolved_in_docs(
                rec.get("document_paths") or [], rec.get("thread_ids") or []
            ):
                continue  # open thread — fail-closed: stay paused (NFR-0090-02).
            self._resume_doc_gap_record(record_id, rec)
            return True
        return False

    def _resume_interrupted_dispatch(self, state: State) -> bool:
        """Re-issue a resume decision whose dispatch never got issued.

        The resume sequence (restored/discarded -> resumed -> issue()) spans
        three store writes; a crash in between leaves the record terminal
        (RESTORED/DISCARDED/RESUMED) with the decision identity persisted but
        NO command.issued — decide() would park forever.  When no open WAL
        pending exists and the recorded next_dispatch_id was never issued,
        emit the missing outcome.resumed (audit closure) and issue the
        dispatch with the SAME id (idempotent: an already-issued id is
        skipped, so a completed resume is never duplicated).
        """
        if state.pending is not None or state.status != "active":
            return False
        if state.stage not in ("M-IMPL", "M-TEST"):
            return False
        for record_id, rec in state.doc_gaps.items():
            if rec.get("state") not in ("RESTORED", "DISCARDED", "RESUMED"):
                continue
            next_dispatch_id = rec.get("next_dispatch_id")
            next_attempt = int(rec.get("next_attempt") or 1)
            if not next_dispatch_id or self._dispatch_issued(next_dispatch_id):
                continue
            origin = rec.get("origin") or {}
            if rec.get("state") != "RESUMED":
                self._emit(
                    "outcome.resumed",
                    {
                        "record_id": record_id,
                        "origin_dispatch_id": origin.get("dispatch_id", ""),
                        "next_dispatch_id": next_dispatch_id,
                        "next_attempt": next_attempt,
                    },
                )
            self._issue_resume_dispatch(origin, next_dispatch_id, next_attempt)
            return True
        return False

    def _dispatch_issued(self, command_id: str) -> bool:
        """True when a ``command.issued`` with this id was already written
        (WAL).

        Checks the ``command.issued`` event type specifically so audit
        events that carry the same command_id (e.g.
        ``doc_gap.design_dispatched`` bound to the dispatch id) do not
        falsely report an issued command — the crash-window recovery
        relies on this to detect a missing WAL command after the audit
        event was persisted.
        """
        return any(
            event.type == "command.issued" and event.command_id == command_id
            for event in self.store.events(self.run_id)
        )

    def _rebuild_quarantine_descriptor(
        self, rec: dict
    ) -> QuarantineDescriptor | None:
        """Rebuild the persisted quarantine manifest blob for the resume call.

        Returns None when no reference is recorded or the blob is unreadable
        (legacy records, evidence filesystem loss) — the caller then decides
        fail-closed from the projected quarantine status alone.
        """
        ref = rec.get("manifest_ref")
        if not ref:
            return None
        try:
            manifest = self.store.load_payload({"$ref": ref})
        except (OSError, ValueError):
            return None
        if not isinstance(manifest, dict):
            return None
        changes = tuple(
            QuarantinedChange(
                path=change.get("path", ""),
                operation=change.get("operation", "modify"),
                baseline_identity=change.get("baseline_identity"),
                content_identity=change.get("content_identity"),
            )
            for change in manifest.get("changes") or []
        )
        status = manifest.get("status")
        if status not in ("empty", "held"):
            status = "held"
        return QuarantineDescriptor(
            quarantine_id=manifest.get("quarantine_id", ""),
            origin=rec.get("origin") or {},
            design_identity=manifest.get("design_identity", ""),
            run_identity=manifest.get("run_identity", ""),
            changes=changes,
            manifest_ref=ref,
            manifest_sha256=ref,
            status=status,
        )

    def _quarantine_resume_decision(
        self, rec: dict, next_dispatch_id: str, next_attempt: int
    ) -> ResumeDecision:
        """decide_quarantine_resume over rebuilt evidence, fail-closed.

        An empty quarantine restores trivially even without readable blob
        evidence; a held quarantine whose manifest cannot be rebuilt discards
        as content_conflict (AC-FR0236-04 fail-closed default).

        #62 finding 4: the ``design_stale`` discard fires ONLY after an actual
        nested Archer design revision (``revised_design_identity`` set on the
        record).  For an AGENT_CORRECTION pause (no Archer revision) the
        adjudication marker Prism appends to the design doc post-pause is
        not a substantive revision - the held agent output is still current
        relative to its task, so the design-drift check is short-circuited
        (the marker's drift must not falsely discard a held quarantine).
        """
        descriptor = self._rebuild_quarantine_descriptor(rec)
        if descriptor is None:
            if rec.get("quarantine_status") == "empty":
                return ResumeDecision(
                    "restore", "empty", next_dispatch_id, next_attempt
                )
            return ResumeDecision(
                "discard", "content_conflict", next_dispatch_id, next_attempt
            )
        document_paths = rec.get("document_paths") or []
        current_pairs = [
            (name, self._path_identity(self._doc_path(name)))
            for name in document_paths
        ]
        current_paths = {
            change.path: self._path_identity(self.repo / change.path)
            for change in descriptor.changes
        }
        # Only an actual nested Archer design revision (revised_design_identity)
        # may discard a held quarantine as design_stale; otherwise the pause-time
        # anchor is the comparison baseline (marker-only drift is ignored).
        if rec.get("revised_design_identity"):
            current_design_identity = combined_design_identity(current_pairs)
        else:
            current_design_identity = descriptor.design_identity
        return decide_quarantine_resume(
            descriptor,
            current_design_identity=current_design_identity,
            current_run_identity=self.run_id,
            current_path_identities=current_paths,
            next_dispatch_id=next_dispatch_id,
            next_attempt=next_attempt,
        )

    def _resume_doc_gap_record(self, record_id: str, rec: dict) -> None:
        """Emit the resume decision pair and issue the NEW dispatch."""
        origin = rec.get("origin") or {}
        next_dispatch_id = new_ulid()
        next_attempt = int(origin.get("attempt", 1)) + 1
        decision = self._quarantine_resume_decision(
            rec, next_dispatch_id, next_attempt
        )
        self._emit(
            "outcome.restored" if decision.action == "restore" else "outcome.discarded",
            {
                "record_id": record_id,
                "quarantine_id": rec.get("quarantine_id") or "",
                "reason": decision.reason,
                "next_dispatch_id": decision.next_dispatch_id,
                "next_attempt": decision.next_attempt,
            },
        )
        self._emit(
            "outcome.resumed",
            {
                "record_id": record_id,
                "origin_dispatch_id": origin.get("dispatch_id", ""),
                "next_dispatch_id": decision.next_dispatch_id,
                "next_attempt": decision.next_attempt,
            },
        )
        self._issue_resume_dispatch(
            origin, decision.next_dispatch_id, decision.next_attempt
        )

    def _threads_resolved_in_docs(
        self, document_paths: list, thread_ids: list
    ) -> bool:
        """Instance helper: parse docs and check all thread_ids resolved."""
        if not thread_ids:
            return False
        found: dict[str, str] = {}
        for name in document_paths:
            path = self._doc_path(name)
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for t in parse_threads(text):
                found[t.thread_id] = t.status
        return all(found.get(tid) == "resolved" for tid in thread_ids)

    def _issue_resume_dispatch(
        self, origin: dict, command_id: str, attempt: int
    ) -> None:
        """Issue the resume dispatch for the same logical role/task/phase."""
        role = origin.get("role", "devon")
        substate = origin.get("phase", "RED")
        # Shield WRITE pauses fire inside M-TEST; Devon RGR phases in M-IMPL.
        # Resuming into the wrong stage would misroute the outcome pipeline.
        stage = "M-TEST" if role == "shield" else "M-IMPL"
        cmd = Command(
            kind="dispatch_agent",
            params={
                "role": role,
                "substate": substate,
                "stage": stage,
                "attempt": attempt,
                "assignment": {"kind": substate, "phase": substate.lower()},
            },
        )
        self.issue(cmd, command_id=command_id)

    def _dispatch_agent_backend(
        self,
        cmd,
        state,
        task_id,
        role,
        substate,
        doc,
        doc_path,
        assignment,
    ) -> None:
        p = cmd.params
        self._dispatch_log_start(p, state)
        t0 = time.monotonic()
        # T-001 face (E) / architecture §1.1 Envelope/failure chain: pre-
        # dispatch parity gate (dispatch.rejected on version mismatch) and
        # the failure-evidence injection seam (select -> review -> inject).
        if not self._dispatch_parity_ok(cmd, task_id, assignment):
            return
        assignment = self._inject_failure_evidence(assignment, role, state, task_id, cmd)
        pre_dirty = self._resolve_pre_dirty(state, substate, p)
        # SM-02 doc-comment-first (IF-DOCGAP-001): capture design-doc baseline
        # and dirty snapshot BEFORE act() so deltas can be classified after.
        doc_gap = self._capture_doc_gap_context(state, role)
        # B1 (issue #2, user ruling 2026-08-18): writer agents (Devon RGR in
        # M-IMPL, Shield WRITE in M-TEST) run in a spatially isolated
        # worktree created from the current main HEAD; the work is replayed
        # onto the main tree afterwards, so every existing pipeline
        # (pre_dirty, manifest audit, R/G refs, atomic rollback) keeps
        # operating on the main tree unchanged. Per-dispatch lifecycle:
        # each phase re-creates from HEAD, so REFACTOR and PRISM-revise
        # re-dispatches naturally see committed work; a crash leaks only a
        # worktree that the B2 start-time sweep reclaims. Audited events.
        handle, wt_task_id = self._open_writer_worktree(state, role, substate, cmd)
        result, replay_error = self._act_in_worktree(
            cmd, role, substate, doc, doc_path, assignment, handle, wt_task_id
        )
        if replay_error is not None:
            self._emit(
                "verdict.failed",
                {
                    "check": "worktree",
                    "reason": f"worktree replay failed: {replay_error}",
                    "evidence": replay_error,
                    "task_id": task_id,
                    "attempt": state.current_attempt + 1,
                },
                command_id=cmd.command_id,
                task_id=task_id,
            )
            return
        # IF-ENVELOPE-002 finalization seam: after act() (and any result-
        # enriching subclass) returned, and BEFORE any raw_output is consumed
        # below, give the backend the last word on its declared reply bytes.
        result = self._finalize_backend_result(result, role, substate, assignment)
        # T-001 face (E) / IF-ENVELOPE-002: post-act short-circuit gates
        # (dispatch log end, prepared-artifact parity rejection, collection-
        # time format_error). Each short-circuit keeps the outcome out of the
        # ordinary pipeline (never a semantic success).
        if self._post_act_gates(p, result, cmd, task_id, assignment, time.monotonic() - t0):
            return
        # IF-ENVELOPE-002: the single full-gate dispatch.parity success
        # event (complete audit) of a declared dispatch.
        self._emit_dispatch_parity_success(result, cmd, task_id, assignment)
        # OOB b89 OB-4 — tasks.md projection guard (incremental attribution)
        # Must run before any early-return handling so attribution is correct
        # and restoration is performed in the same command_id. A violation
        # fail-closes the turn; a system_repaired heals inherited dirty state.
        _guard = self._handle_tasks_md_projection(cmd, state, task_id, p)
        if _guard == "violation":
            return
        # v0.5 no_diff peer review: NO_DIFF_EXPLAIN and NO_DIFF_REVIEW outcomes
        # do NOT enter the ResultCheckpoint pipeline. The explanation/review
        # events drive the state machine directly.
        if self._handle_no_diff_outcome(substate, result, cmd, task_id, state):
            return
        # D-29 anti-self-report triple ③: M-TEST PRISM_REVIEW criteria-pack
        # mismatch -> verdict.failed, re-dispatch Prism (no prism.verdict).
        # Must be checked before emitting outcome.received so a mismatch
        # short-circuits without entering the pipeline.
        if self._criteria_pack_mismatch(
            role, substate, state, result.get("verdict"), assignment, result, cmd
        ):
            self._emit_criteria_pack_failure(
                cmd,
                task_id,
                p,
                assignment,
                result,
                state,
            )
            return
        # SM-02 doc-comment-first pre-check (§1k): classify design-doc deltas
        # BEFORE ordinary validation. illegal_body_edit > legal_discussion.
        if self._handle_doc_gap_outcome(
            cmd, state, task_id, role, substate, result, doc_gap,
        ):
            return
        # v0.5 ResultCheckpoint pipeline (batch 1: M-STORY/M-SPEC/M-ACC):
        # embed the full pipeline capture in outcome.received so the reducer
        # atomically sets active_result in the same event — no crash window
        # between outcome.received and a separate result.submitted event.
        # Failed outcomes (status != "done") are handled by _on_outcome_received
        # (attempt consumed, substate reset) — no pipeline payload.
        result.setdefault("self_report", "")
        payload = _dispatch_payload(self.store, p, result)
        # T-001 face (B) / AC-FR0287-02: dispatch-loop cutover consumption —
        # with an established escape barrier, an outcome whose originating
        # seq <= cutover_seq was dispatched before the barrier and is
        # quarantined: no outcome.received, no checkpoint/publish, no State
        # overwrite (escape.late_outcome records the quarantine).
        if self._consume_escape_cutover(
            {"dispatch_id": cmd.command_id or "", "seq": None}
        ):
            return
        # v0.6 hotfix SAGE_TRIAGE (IF-HOTFIX-004): carry the Sage anchor-search
        # outcome fields (acs / no_anchor / searched_versions / corpus_digests /
        # rationale_refs) into the persisted outcome so _do_validate_anchor can
        # programmatically validate them.
        if role == "sage" and substate == "SAGE_TRIAGE":
            payload.update(_sage_anchor_outcome_fields(result))
        # Expose the agent I/O blob ref on the result so downstream verdict
        # emissions can point the next agent at the full transcript (critic
        # reviews and diagnoses live in blobs - long-form content must be
        # passed by reference, never re-derived or inlined).
        agent_io = payload.get("agent_io") or {}
        if agent_io.get("output_ref"):
            result.setdefault("output_ref", agent_io["output_ref"])
        if self._checkpoint_eligible(state, result, substate):
            payload["result_checkpoint"] = self._result_checkpoint_payload(
                cmd, state, result, p, substate, role, doc, pre_dirty
            )
        self._emit("outcome.received", payload, command_id=cmd.command_id, task_id=task_id)
        # T-001 face (E) / IF-FAILURE-001: emitted -> stored (append-only
        # failure evidence at production time) and consumed -> acked (the
        # outcome's evidence_ack closes the loop on injected evidence).
        self._record_failure_stored(result, role, state, task_id, cmd)
        self._consume_evidence_ack(result, role)
        if "result_checkpoint" in payload:
            return  # pipeline drives the domain event
        self._emit_dispatch_verdict(result, role, state, p, cmd, task_id)
        shield_committed = self._emit_shield_commit(result, role, state, cmd, task_id)
        self._maybe_transition_full_ledger(cmd, state, role, result, shield_committed)

    # ------------------------------------------------------------------
    # OOB b89 OB-4 — tasks.md projection guard helpers
    # ------------------------------------------------------------------

    def _extract_pre_snapshot_for_guards(self, params: dict) -> dict | None:
        """Return the pre-dispatch dirty snapshot dict for incremental attribution.

        Carries the same identity map the runtime persisted in the dispatched
        assignment (or top-level params). ``None`` means the guard cannot
        attribute and must fall back to ``system_repaired`` semantics for a
        mismatched projection (never a false violation).
        """
        # 1) M-IMPL assignment carries pre_dirty_snapshot both top-level and in manifest
        assignment = params.get("assignment")
        if isinstance(assignment, dict):
            snap = assignment.get("pre_dirty_snapshot")
            if isinstance(snap, dict):
                return snap
            manifest = assignment.get("manifest")
            if isinstance(manifest, dict):
                snap = manifest.get("pre_dirty_snapshot")
                if isinstance(snap, dict):
                    return snap
        # 2) Shield WRITE / other dispatches stash at top-level
        snap = params.get("pre_dirty_snapshot")
        if isinstance(snap, dict):
            return snap
        # 3) fallback: no attribution baseline
        return None

    @staticmethod
    def _is_path_in_diff(rel_path: str, pre: dict | None, post: dict) -> bool:
        """Whether *rel_path* identity changed between *pre* and *post*."""
        if pre is None:
            # without a baseline we cannot attribute → treat as not in diff
            # (system_repaired path, never a false violation)
            return False
        pre_id = pre.get(rel_path)
        post_id = post.get(rel_path)
        if pre_id is None and post_id is None:
            return False
        if pre_id is None or post_id is None:
            return True
        return pre_id != post_id

    def _dispatch_parity_ok(self, cmd, task_id, assignment) -> bool:
        """(E) IF-ENVELOPE-002 pre-dispatch version-parity gate.

        Collects the four static parity faces this process controls — the
        assignment declaration, the FakeBackend class literal, the
        OpencodeBackend class literal (BOTH collected even when only one
        backend is selected: one stale backend declaration blocks both
        modes) and the Runtime validator — and asks the kernel
        check_envelope_parity to compare them. On a declared dispatch all
        four faces are REQUIRED (a partial map never passes an enforced
        dispatch); any mismatch emits dispatch.rejected
        (reason=version_parity_mismatch) and blocks the agent execution
        path. Artifact faces are NOT prechecked here (canonical scans are
        not proof of what the agent consumes) — they are bound to the
        actual materialized bytes and checked inside backend.act, before
        the spawn (dispatch_parity seam).

        TRAC_ENVELOPE_DECLARE=0 / schema-pending kinds: undeclared, the
        gate keeps today's staged semantics (nothing is enforced).

        IF-ENVELOPE-002 event timing: this STATIC gate never emits the
        dispatch.parity success event of a DECLARED dispatch — that event
        is emitted exactly once per dispatch, only after the COMPLETE gate
        (static faces + every selected artifact face bound to the actual
        materialized bytes + pre-spawn readback) has passed, with the full
        audit payload (see _emit_dispatch_parity_success). A static-only
        pass produces no success record, so an artifact-face rejection can
        never be preceded by a dispatch.parity success. Undeclared
        dispatches keep today's staged legacy event.
        """
        verdict = static_parity_check(assignment)
        if not verdict.get("consistent", True):
            self._emit(
                "dispatch.rejected",
                {
                    "reason": "version_parity_mismatch",
                    "mismatches": list(verdict.get("mismatches", [])),
                    "task_id": task_id,
                },
                command_id=cmd.command_id,
                task_id=task_id,
            )
            return False
        if is_declared(assignment):
            return True
        self._emit(
            "dispatch.parity",
            {"task_id": task_id, "referenced": verdict.get("referenced")},
            command_id=cmd.command_id,
            task_id=task_id,
        )
        return True

    def _emit_dispatch_parity_success(
        self, result, cmd, task_id, assignment
    ) -> None:
        """(E) IF-ENVELOPE-002: the single dispatch.parity success event of
        a declared dispatch, emitted only after the COMPLETE gate has
        passed — the four static faces at dispatch plus every selected
        artifact face bound to the ACTUAL materialized bytes and re-read
        before spawn (the backend attaches its passed-gate evidence as
        result["parity"]). The payload is the full audit: the referenced
        face map, the manifest (face/path(absolute)/sha256/token per
        consumed material) and the prompt digest actually handed to the
        spawn. A fake declared dispatch consumes no materialized artifacts
        (artifact faces genuinely absent): its audit is the four static
        faces with an empty manifest — no file or version evidence is
        invented. Undeclared dispatches keep their staged legacy event
        (see _dispatch_parity_ok) and emit nothing here. A declared
        dispatch the backend rejected (parity_rejected) never reaches this
        emitter: the short-circuit above returns first.
        """
        if not is_declared(assignment):
            return
        parity = result.get("parity") if isinstance(result, dict) else None
        if not isinstance(parity, dict):
            return
        referenced = dict(parity.get("referenced") or {})
        if not referenced:
            # FakeBackend declared dispatch: no referenced map is carried —
            # audit the four static faces the kernel gate actually checked.
            referenced = static_parity_referenced(assignment)
        self._emit(
            "dispatch.parity",
            {
                "task_id": task_id,
                "referenced": referenced,
                "manifest": list(parity.get("manifest") or []),
                "prompt_sha256": parity.get("prompt_sha256"),
            },
            command_id=cmd.command_id,
            task_id=task_id,
        )

    def _dispatch_parity_rejected(self, result, cmd, task_id) -> bool:
        """(E) IF-ENVELOPE-002: emit the Runtime rejection for a dispatch the
        backend's prepared-artifact gate failed (readback or artifact-face
        mismatch, rejected BEFORE any agent spawn). Emits
        dispatch.rejected reason=version_parity_mismatch with the parity
        manifest/mismatches for audit and never reaches the semantic
        outcome pipeline. Returns True when the caller must stop."""
        if not result.get("parity_rejected"):
            return False
        parity = result.get("parity") or {}
        self._emit(
            "dispatch.rejected",
            {
                "reason": "version_parity_mismatch",
                "mismatches": list(parity.get("mismatches") or []),
                "kind": parity.get("kind"),
                "manifest": list(parity.get("manifest") or []),
                "task_id": task_id,
            },
            command_id=cmd.command_id,
            task_id=task_id,
        )
        return True

    def _post_act_gates(
        self, p, result, cmd, task_id, assignment, elapsed: float
    ) -> bool:
        """(E) T-001 face (E): the post-act short-circuit gates of a dispatch.

        Ends the dispatch log, then runs the two short-circuits that keep a
        broken outcome out of the semantic pipeline: the prepared-artifact
        parity rejection (dispatch.rejected, no agent success) and the
        collection-time format_error (single kernel parse path). Returns
        True when a gate handled the outcome and the caller must stop.
        """
        self._dispatch_log_end(p, result, elapsed)
        if self._dispatch_parity_rejected(result, cmd, task_id):
            return True
        return self._format_error_shortcircuit(result, cmd, task_id, assignment)

    def _inject_failure_evidence(self, assignment, role, state, task_id, cmd):
        """(E) IF-FAILURE-001 select -> review -> inject seam.

        Picks the shared-rule failure evidence for this role/round, fail-
        closes on a review-inconsistent chain (review.failed), and injects
        the failure id into the assignment's evidence segment before the
        Agent runs. No stored evidence for this role -> assignment unchanged.
        """
        failure = select_failure(self.run_id, role, getattr(state, "review_round", 0))
        if failure is None:
            return assignment
        chain = [
            {"type": e.type, "payload": dict(e.payload or {}), "seq": e.seq}
            for e in self.store.events(self.run_id)
        ]
        outcome, gaps = review_failure_chain(chain)
        if outcome != "consistent":
            self._emit(
                "review.failed",
                {
                    "area": "failure_evidence",
                    "outcome": outcome,
                    "detail": "; ".join(gaps),
                    "gaps": gaps,
                    "task_id": task_id,
                },
                command_id=cmd.command_id,
                task_id=task_id,
            )
            return assignment
        self._emit(
            "failure.selected",
            {
                "failure_id": failure.get("failure_id", ""),
                "round": failure.get("round"),
                "source": failure.get("source"),
                "role": role,
                "rule": "round/source-latest-unacked-lookback-3",
            },
            command_id=cmd.command_id,
            task_id=task_id,
        )
        injected = inject_into_assignment(assignment, failure)
        fid = failure.get("failure_id", "")
        if fid:
            # The M-IMPL assignment's ``evidence`` segment carries the
            # last_failure dict, not a plain id list; record the injected
            # failure ids on a dedicated key so the outcome's evidence_ack
            # can only reference real chain records.
            refs = list(injected.get("failure_evidence") or [])
            if fid not in refs:
                refs.append(fid)
            injected["failure_evidence"] = refs
        self._emit(
            "failure.injected",
            {
                "failure_id": fid,
                "round": failure.get("round"),
                "source": failure.get("source"),
                "role": role,
                "task_id": task_id,
            },
            command_id=cmd.command_id,
            task_id=task_id,
        )
        return injected

    def _format_error_shortcircuit(
        self, result, cmd, task_id, assignment=None
    ) -> bool:
        """(E) IF-ENVELOPE-001/002 collection-time single parse path.

        EnvelopeFormatError -> format_error event and True (handled: never a
        semantic attempt — no attempt consumed, no business-state mutation,
        the ordinary validation pipeline never runs). A successful parse
        enriches the result with the envelope payload and falls through.

        Rollout gate: only a DECLARED dispatch (assignment carries the
        kernel.envelope declaration, see _enrich_envelope_params) hard-fails
        on structural breakage. T-024 collection-face tightening: on a
        declared dispatch every non-parsable reply shape is a compliance
        miss classified by the kernel parser itself — empty, missing and
        non-string raw_output and free-form JSON (no fenced block) included
        — never ambiguity handed to the legacy channels; undeclared
        dispatches keep the legacy channels entirely.
        """
        declared = bool(isinstance(assignment, dict) and assignment.get("envelope"))
        declared_kind = (
            (assignment.get("envelope") or {}).get("kind") if declared else None
        )
        raw = result.get("raw_output")
        if result.get("status") == "failed" and not raw and result.get("failure_class"):
            return False  # No agent reply: preserve the existing failure classification.
        if not declared and (not isinstance(raw, str) or not raw):
            return False  # undeclared: legacy channels keep handling it
        try:
            # Declared dispatch: even empty/missing/non-string replies go
            # through the single kernel parse path, which classifies them
            # (malformed_json / no_envelope_block) — no duplicated logic.
            envelope = parse_agent_output(raw)
            if declared_kind:
                # Declared dispatch: the reply must speak the kind the
                # assignment declared (schema already payload-validated).
                envelope = validate_envelope(envelope, declared_kind)
        except EnvelopeFormatError as exc:
            if not declared:
                return False
            self._emit(
                "format_error",
                {"kind": exc.kind, "detail": exc.detail, "task_id": task_id},
                command_id=cmd.command_id,
                task_id=task_id,
            )
            # A declared review/diagnose dispatch whose reply violates the
            # declared schema is a CONTRACT violation of that role: without
            # a routable event the reviewer_dispatched flag stays set and
            # decide() awaits a verdict that already failed classification
            # forever (live 01M280CV: DIAGNOSE schema_violation stranded
            # active/DIAGNOSE across process restarts — the exact B62 #80
            # stall class). Emit the stage-routable verdict.failed so the
            # projection applies the accepted contract-violation routing
            # (reset review flags, consume attempt, stay for re-dispatch;
            # budget exhaustion escalates). Audit trail keeps both events.
            if declared_kind in DIAGNOSE_KINDS:
                self._emit(
                    "verdict.failed",
                    {
                        "check": "diagnose_contract_violation",
                        "target_stage": "M-IMPL",
                        "task_id": task_id or "",
                        "reason": (
                            "declared DIAGNOSE reply failed the kernel "
                            f"schema ({exc.kind}: {exc.detail})"
                        ),
                        "evidence": (
                            "format_error on declared prism:diagnose reply; "
                            "the assignment carried the payload schema"
                        ),
                    },
                    command_id=cmd.command_id,
                    task_id=task_id,
                )
            return True
        except NotImplementedError:
            return False  # T-035 module pending; deferred-only-pass
        result.setdefault("envelope", envelope)
        return False

    def _record_failure_stored(self, result, role, state, task_id, cmd) -> None:
        """(E) IF-FAILURE-001: append-only storage at failure production.

        Every non-done outcome is recorded into the shared failure-review
        store (round/source rule input) and reflected as a failure.stored
        event; rich evidence already awaiting consumption is never silently
        overwritten by ordinary failures (the store keeps every record).
        """
        if result.get("status") in (None, "done"):
            return
        record = {
            "task_id": task_id,
            "failure_class": result.get("failure_class", ""),
            "self_report": str(result.get("self_report", ""))[:2000],
        }
        round_no = state.review_round
        self._emit(
            "failure.emitted",
            {
                "failure_id": next_failure_id(self.run_id, round_no, role),
                "round": round_no,
                "source": role,
                "role": role,
                "record_ref": result.get("output_ref"),
            },
            command_id=cmd.command_id,
            task_id=task_id,
        )
        stored = record_failure(self.run_id, round_no, role, record)
        self._emit(
            "failure.stored",
            stored,
            command_id=cmd.command_id,
            task_id=task_id,
        )
        # Replaced evidence: an ACKed older record of the same (round, source)
        # is closed with an invalidated proof; un-ACKed evidence stays
        # selectable (never silently overwritten).
        for invalidated in supersede_acked(
            self.run_id, round_no, role, exclude_id=stored["failure_id"]
        ):
            for ack_role in invalidated["roles"]:
                self._emit(
                    "failure.invalidated",
                    {
                        "failure_id": invalidated["failure_id"],
                        "round": invalidated["round"],
                        "source": invalidated["source"],
                        "role": ack_role,
                        "reason": invalidated["reason"],
                    },
                    command_id=cmd.command_id,
                    task_id=task_id,
                )

    def _consume_evidence_ack(self, result, role) -> None:
        """(E) IF-FAILURE-001: outcome evidence_ack consumption -> acked.

        The outcome's evidence_ack names the injected failure ids the agent
        actually consumed; each is acknowledged for the role so the shared
        selection rule stops re-injecting it.
        """
        for fid in result.get("evidence_ack") or []:
            self._emit(
                "failure.consumed",
                {"failure_id": str(fid), "role": role},
            )
            acknowledge_failure(self.run_id, str(fid), role)
            self._emit(
                "failure.acked",
                {"failure_id": str(fid), "role": role},
            )

    def _act_in_worktree(
        self,
        cmd,
        role,
        substate,
        doc,
        doc_path,
        assignment,
        handle,
        wt_task_id,
    ):
        """Run backend.act() (inside the writer worktree when one is open),
        replay the worktree delta onto the main tree, and always close the
        worktree (audited via ``worktree.closed``). Returns
        ``(result, replay_error)``; replay_error None means clean/applied."""
        replay_error: str | None = None
        replayed = False
        try:
            result = self.backend.act(
                role,
                substate,
                doc,
                doc_path,
                assignment=assignment,
                worktree=Path(handle.path) if handle is not None else None,
            )
            if handle is not None:
                replay_error, replayed = self._replay_worktree_to_main(handle)
        finally:
            if handle is not None:
                cleanup_worktree(handle)
                self._emit(
                    "worktree.closed",
                    {
                        "kind": handle.kind,
                        "path": handle.path,
                        "task_id": wt_task_id,
                        "replayed": replayed,
                    },
                    command_id=cmd.command_id,
                    task_id=wt_task_id,
                )
        return result, replay_error

    def _finalize_backend_result(self, result, role, substate, assignment):
        """IF-ENVELOPE-002 finalization seam (see AgentBackend.finalize_act).

        Called after act() succeeded and before the collection face parses
        raw_output, so a declared reply assembled outside act() (or enriched
        by a backend subclass afterwards) is the reply the Runtime classifies
        and the parity faces read. Test doubles without the hook keep their
        exact legacy result."""
        finalize = getattr(self.backend, "finalize_act", None)
        if not callable(finalize):
            return result
        finalized = finalize(result, role, substate, assignment)
        return finalized if isinstance(finalized, dict) else result

    @staticmethod
    def _checkpoint_eligible(state, result, substate) -> bool:
        """v0.5 ResultCheckpoint pipeline eligibility: batch-1 stages on a
        done outcome for writer/review substates (payload embeds the capture)."""
        return (
            state.stage in ("M-STORY", "M-SPEC", "M-ACC", "M-DESIGN", "M-TEST")
            and result.get("status") == "done"
            and (substate in ("DRAFT", "RESPOND", "WRITE") or substate in _REVIEW_SUBSTATE)
        )

    def _maybe_transition_full_ledger(
        self, cmd, state, role, result, shield_committed
    ) -> None:
        """Full-chain ledger: a done Shield repair under an active full-chain
        round advances CLASSIFIED -> FIXED."""
        if (
            state.stage == "M-IMPL"
            and state.full_chain_round is not None
            and state.substate == "SHIELD_FIX"
            and role == "shield"
            and result.get("status") == "done"
            and shield_committed
        ):
            self._transition_full_ledger(
                cmd,
                "CLASSIFIED",
                "FIXED",
                "Shield repair outcome completed",
                "shield",
            )

    def _open_writer_worktree(self, state, role: str, substate: str, cmd):
        """B1 (issue #2): open the per-dispatch worktree for writer agents.

        Devon RED/GREEN/REFACTOR (M-IMPL) get a devon_candidate worktree
        keyed by the task lease; Shield WRITE (M-TEST) gets a
        test_authority worktree. Both are created from the main HEAD —
        NOT a stale C_design — so re-dispatches after G/R commits see
        the committed work. Returns (handle, task_id) or (None, None)
        for every other dispatch.
        """
        kind: str | None = None
        task_id: str | None = None
        if (
            state.stage == "M-IMPL"
            and role == "devon"
            and substate in ("RED", "GREEN", "REFACTOR")
        ):
            kind, task_id = "devon", state.current_task_id or ""
        elif state.stage == "M-TEST" and role == "shield" and substate == "WRITE":
            kind = "test_authority"
        if kind is None or (kind == "devon" and not task_id):
            return None, None
        head = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        seed_note: dict | None = None
        if kind == "devon":
            self._clear_stale_worktree_path(
                _writer_worktree_path(str(self.repo), self.run_id, task_id, "devon"),
                "devon_candidate",
            )
            handle = create_devon_worktree(str(self.repo), head, self.run_id, task_id)
            seed_note = self._seed_devon_worktree_wip(state, handle.path)
        else:
            self._clear_stale_worktree_path(
                _writer_worktree_path(str(self.repo), self.run_id, None, "test_authority"),
                "test_authority",
            )
            handle = create_test_authority_worktree(str(self.repo), head, self.run_id)
        ensure_runtime_assets(str(self.repo), handle.path)
        self._emit(
            "worktree.opened",
            {
                "kind": handle.kind,
                "path": handle.path,
                "task_id": task_id,
                "base_sha": head,
                "attempt": state.current_attempt + 1,
                # Prism P4: the writer's actual input surface is HEAD + the
                # seeded cycle WIP -- the audit event records it so replay
                # can reconstruct what the writer saw.
                **({"devon_wip_seed": seed_note} if seed_note is not None else {}),
            },
            command_id=cmd.command_id,
            task_id=task_id,
        )
        return handle, task_id

    def _seed_devon_worktree_wip(self, state, worktree_path: str) -> dict:
        """OOB 2026-09-06: seed a fresh Devon worktree with the current
        cycle's accumulated WIP so a re-dispatched writer resumes from it
        instead of clean HEAD (worktree blindness, run
        01M19FJVES7G113RD8QXXY3PQZ: one 2h20m round produced zero net
        change). Prism P4: the seeded surface is recorded for the audit
        event -- replay reconstructs what the writer actually saw; a
        missing manifest (no scope derivable) degrades loudly, never
        silently blind."""
        manifest = state.current_manifest or {}
        scope = [p for p in (manifest.get("allowed_paths") or []) if isinstance(p, str)]
        seeded = seed_worktree_with_cycle_wip(str(self.repo), worktree_path, scope)
        note = {"seeded_wip_paths": seeded}
        if seeded:
            print(
                f"  [worktree] seeded {seeded} in-scope WIP path(s) into the "
                f"devon worktree (cycle accumulation)",
                file=sys.stderr,
                flush=True,
            )
        elif manifest.get("allowed_paths") is None:
            note["seeded_wip_paths"] = "degraded: no manifest scope"
        return note

    def _clear_stale_worktree_path(self, path: str, kind: str) -> None:
        """Reclaim this exact path if a crashed dispatch left it behind.

        Deliberately NOT the B2 sweep (user ruling: full sweeps run only at
        ``trac start``) — this reclaims only the one path this dispatch is
        about to occupy, so a crash between two runs cannot brick the
        loop's next writer dispatch.
        """
        if not os.path.exists(path):
            return
        cleanup_worktree(WorktreeHandle(path=path, base_sha="", kind=kind))

    def _replay_worktree_to_main(self, handle: WorktreeHandle) -> tuple[str | None, bool]:
        """Apply the worktree's working-tree delta onto the main tree.

        ``git add -A`` + ``git diff --cached --binary`` captures tracked
        edits plus new files; ``git apply`` (bytes mode — binary patches
        must never be decoded as text) writes them into the main tree
        WITHOUT staging, so downstream pipelines see exactly the state the
        agent would have left had it worked in the main tree.
        Returns (error, had_delta); error None means clean or applied ok.

        D-36 浅版（#43/B36 反碾压守卫）：主树在派发窗口内被操作者提交
        改动过的文件，mirror 回退不再覆盖——同名冲突 fail-closed 报错
        （主树侧内容保留，人工裁决后重试），杜绝 concurrent-collision
        replay 静默吞掉操作者已提交的工作。

        B60 (#76)：``ensure_runtime_assets`` 在 agent 运行**前**把
        ``_RUNTIME_ASSETS`` 链接进 worktree（声明的环境目录是符号链接），而
        canonical ``.gitignore`` 的该目录尾斜杠模式只匹配目录、
        不匹配符号链接——``git add -A`` 会把它 stage 进 replay diff，
        ``git apply`` 拒绝后 mirror 兜底 copy2 目录直接 Errno 21。故
        add 后对每个 runtime asset 显式 ``git reset -q``（只动 index，
        不碰 working tree）：replay diff 只携带 agent 工作增量，环境
        管线永不入镜。reset 对未 staged 的 asset 是无害 no-op 错误。
        """
        wt = handle.path
        subprocess.run(
            ["git", "-C", wt, "add", "-A"],
            capture_output=True,
            check=False,
        )
        for asset in _RUNTIME_ASSETS:
            subprocess.run(
                ["git", "-C", wt, "reset", "-q", "--", asset],
                capture_output=True,
                check=False,
            )
        diff = subprocess.run(
            ["git", "-C", wt, "diff", "--cached", "--binary"],
            capture_output=True,
            check=False,
        )
        if not diff.stdout.strip():
            # No file delta, but the agent may still have created empty
            # directories (git diffs cannot carry them; e.g. Shield's
            # tests/assets scaffold). Sync those so the replayed state
            # matches what working directly in the main tree would leave.
            return self._sync_worktree_dirs(handle), False
        applied = subprocess.run(
            ["git", "-C", str(self.repo), "apply", "--whitespace=nowarn", "-"],
            input=diff.stdout,
            capture_output=True,
            check=False,
        )
        if applied.returncode != 0:
            # Fast path refused (e.g. the main tree holds an uncommitted
            # file the diff creates). Mirror the changed files instead:
            # per-file copy/delete from the worktree's final state, which
            # is exactly the state the agent would have left had it
            # worked directly in the main tree (a direct agent edit also
            # overwrites a pre-existing dirty file) — EXCEPT paths the
            # operator committed on main during the dispatch window
            # (#43 anti-clobber guard: those conflict fail-closed).
            return self._mirror_worktree_changes(handle), True
        return self._sync_worktree_dirs(handle), True

    def _main_committed_since(self, base_sha: str) -> set[str]:
        """派发窗口内主树侧被提交改动过的路径（base_sha..HEAD，B36 守卫用）。

        Runtime 是单写者且在派发期间阻塞，窗口内主树 HEAD 前移只能是
        操作者提交（声明与否在守卫处不区分——碾压数据是更重的伤害）。
        base_sha 不可解析（历史改写）时返回空集（守卫退化为旧行为）。
        """
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(self.repo),
                "diff",
                "--name-only",
                "-z",
                f"{base_sha}..HEAD",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            return set()
        return {p for p in proc.stdout.split("\0") if p}

    def _sync_worktree_dirs(self, handle: WorktreeHandle) -> str | None:
        """mkdir -p every directory present in the worktree but missing in
        the main tree (agent-created scaffolding; empty dirs are invisible
        to git diffs). Never deletes anything; .git and symlinks are not
        traversed. Returns an error string or None."""
        wt = Path(handle.path)
        for dirpath, dirnames, _filenames in os.walk(wt, followlinks=False):
            dirnames[:] = [d for d in dirnames if d != ".git"]
            for name in dirnames:
                src = Path(dirpath) / name
                rel = src.relative_to(wt)
                dst = self.repo / rel
                if not dst.exists():
                    try:
                        dst.mkdir(parents=True, exist_ok=True)
                    except OSError as exc:
                        return f"mkdir {rel}: {exc}"
        return None

    def _mirror_worktree_changes(self, handle: WorktreeHandle) -> str | None:
        """Per-file sync of the worktree's staged changes onto the main
        tree (replay fallback). Returns an error string or None.

        D-36 浅版（#43）反碾压守卫：与派发窗口内主树侧提交重叠的路径
        fail-closed——不镜像、保留主树内容、报冲突清单（all-or-nothing：
        发现任一冲突即整个 mirror 不执行，避免留下半镜像的混合状态）。
        """
        names = subprocess.run(
            ["git", "-C", handle.path, "diff", "--cached", "--name-only", "-z"],
            capture_output=True,
            text=True,
            check=False,
        )
        changed = [n for n in names.stdout.split("\0") if n]
        guarded = self._main_committed_since(handle.base_sha)
        conflicts = sorted(set(changed) & guarded)
        if conflicts:
            return (
                "main tree moved during dispatch (operator commits) conflict "
                f"with agent worktree delta: {', '.join(conflicts)} — "
                "operator content preserved on main; resolve manually "
                "(re-dispatch will re-run the agent on the new HEAD)"
            )
        for name in changed:
            src = Path(handle.path) / name
            dst = self.repo / name
            try:
                if src.exists():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                else:
                    dst.unlink(missing_ok=True)
            except OSError as exc:
                return f"mirror {name}: {exc}"
        return None

    def _emit_criteria_pack_failure(
        self,
        cmd,
        task_id,
        params,
        assignment,
        result,
        state,
    ) -> None:
        payload = _dispatch_payload(self.store, params, result)
        self._emit("outcome.received", payload, command_id=cmd.command_id, task_id=task_id)
        assigned = (assignment or {}).get("criteria_pack")
        self._emit(
            "verdict.failed",
            {
                "check": "criteria_pack_mismatch",
                "reason": f"expected {assigned}, got {result.get('criteria_pack')}",
                "attempt": state.current_attempt + 1,
                "evidence": str(result.get("criteria_pack")),
            },
            command_id=cmd.command_id,
            task_id=task_id,
        )

    def _emit_dispatch_verdict(self, result, role, state, params, cmd, task_id):
        if role not in _VERDICT_EVENT:
            return
        if state.stage == "M-IMPL" and role == "prism" and state.substate == "DIAGNOSE":
            self._emit_diagnose_verdict(result, state, cmd, task_id)
            return
        # IF-VERIFY-005 / AC-FR0270: the same-candidate final-review verdict
        # is scoped verify_final and drives the M-VERIFY exit (pass ->
        # security; anything else stops the chain blocked). The dispatch is
        # identified by its OWN substate token (VERIFY_FINAL rides the
        # command params) -- the projected State substate stays VERIFYING
        # (the M-VERIFY StageDef initial_substate), so keying on
        # ``state.substate`` here would never fire and the scoped verdict
        # would fall through to an unscoped prism.verdict (T-042 wiring).
        if role == "prism" and (params or {}).get("substate") == "VERIFY_FINAL":
            self._publish_verify_final_verdict(
                result.get("verdict"),
                None,
                False,
                result.get("result_id"),
                cmd,
                task_id,
                result=result,
                assignment=params,
            )
            return
        if not result.get("verdict"):
            return
        self._emit_verdict(role, result["verdict"], result, state, params, cmd, task_id)

    def _gate_failed_nodes(self, task_id: str) -> list[str]:
        """OOB 2026-09-05: the triggering gate's failing anchors, so the
        diagnosis verdict can carry them on its payload (machine-readable;
        the evidence string carries Prism's prose). The GREEN re-dispatch
        objective names them -- the writer targets what the gate will
        re-run (M2 live-evidence rule). No store attached (isolated verdict
        surface, e.g. the diagnose-contract test double) = no recorded gate
        history = nothing to carry."""
        from tracks.executor.oscillation import last_failed_nodes

        store = getattr(self, "store", None)
        if store is None:
            return []
        return last_failed_nodes(
            [
                {"type": e.type, "payload": dict(e.payload or {})}
                for e in store.events(self.run_id)
            ],
            task_id,
        )

    def _emit_diagnose_verdict(self, result, state, cmd, task_id):
        # Real channel (opencode): the Prism DIAGNOSE reply ends with a
        # {"classification", "reason", "evidence"} JSON (skill contract)
        # extracted into result["verdict"] / result["diagnosis"]. The fixer
        # dispatch receives the diagnostic's actual reason/evidence via FR-11
        # last_failure - without them it only sees the classification label
        # and has to re-derive the whole analysis (run 01KZTHE7 T-008: Shield
        # burned 52 minutes re-archaeologying what Prism had already found).
        verdict = result.get("verdict")
        if verdict not in (
            "test_defect",
            "stub_gap",
            "ac_gap",
            "spec_gap",
            "impl_defect",
            "red_defect",
            "plan_defect",
            # M2 (convergence plan 2026-09-05): the honest exit -- Prism
            # could not reproduce the failure on the forensic package, so
            # it must NOT guess an owner. Routes to a forensic-forced
            # re-DIAGNOSE; a repeated unknown escalates to Archer RULING
            # (see _diagnose_unknown_streak below).
            "unknown",
        ):
            # Prism DIAGNOSE contract violation: no valid classification JSON
            # in final reply. Fail-closed (consume attempt, redispatch Prism;
            # budget exhaustion escalates) — do NOT fallback-derive a
            # classification from prose (user stance: agents honor contracts).
            self._emit(
                "verdict.failed",
                {
                    "check": "diagnose_contract_violation",
                    "target_stage": "M-IMPL",
                    "task_id": task_id or state.current_task_id or "",
                    "reason": (
                        "Prism DIAGNOSE returned no "
                        "{classification,reason,evidence} JSON; "
                        "contract violation"
                    ),
                    "evidence": (
                        "final reply missing bare JSON object per "
                        "Prism.md DIAGNOSE contract"
                    ),
                    "attempt": state.current_attempt + 1,
                },
                command_id=cmd.command_id,
                task_id=task_id,
            )
            return
        classification = verdict
        diagnosis = result.get("diagnosis") if isinstance(result.get("diagnosis"), dict) else {}
        prior = state.last_failure or {}
        fallback = "Prism diagnosis: " + classification
        reason = diagnosis.get("reason") or prior.get("reason") or fallback
        evidence = diagnosis.get("evidence") or prior.get("evidence") or fallback
        # Point the fixer at the full transcript blob: the verdict's
        # reason/evidence are a summary; Prism's complete step-by-step
        # analysis lives in the session blob (user stance: long-form critic
        # output is passed by blob reference, not re-derived downstream).
        ref = result.get("output_ref")
        if ref:
            if not isinstance(evidence, str):
                evidence = json.dumps(evidence, ensure_ascii=False, sort_keys=True)
            evidence += f"; full diagnosis transcript: .tracks/runtime/blobs/{ref}"
        evidence, emitted_check, reason = self._m2_diagnosis_envelope(
            task_id or state.current_task_id, evidence, classification, reason
        )
        gate_nodes = self._gate_failed_nodes(task_id or state.current_task_id or "")
        target = (
            "M-IMPL"
            if classification in ("test_defect", "impl_defect")
            else _DIAGNOSE_TARGET.get(classification, "M-IMPL")
        )
        if state.full_chain_round is not None:
            self._transition_full_ledger(
                cmd,
                "OPEN",
                "CLASSIFIED",
                f"Prism attributed FULL failure as {classification}",
                "prism",
            )
        self._emit(
            "verdict.failed",
            {
                "check": emitted_check,
                "target_stage": target,
                "task_id": task_id or state.current_task_id or "",
                "reason": reason,
                "evidence": evidence,
                "attempt": state.current_attempt + 1,
                **({"failed_nodes": gate_nodes} if gate_nodes else {}),
            },
            command_id=cmd.command_id,
            task_id=task_id,
        )

    def _m2_diagnosis_envelope(
        self, task_id, evidence, classification: str, reason: str
    ) -> tuple:
        """M2 (convergence plan 2026-09-05): surface the latest forensic
        package on every diagnosis and resolve the unknown honest exit.

        The ruling must be grounded in the live git state captured at
        failure time, never in post-teardown static inference (T-042 :78
        misdiagnosis). First unknown -> check="unknown" (kernel
        re-dispatches DIAGNOSE with the package). A repeated consecutive
        unknown means the diagnosis extracted no new information ->
        diagnosis_exhausted (M1-S2): the contract authority carries it
        instead of the remaining diagnosis budget.
        """
        forensics = self._latest_forensics_ref(task_id)
        if forensics:
            if not isinstance(evidence, str):
                evidence = json.dumps(evidence, ensure_ascii=False, sort_keys=True)
            evidence += f"; forensic package: {forensics}"
        emitted_check = classification
        if classification == "unknown" and self._diagnose_unknown_streak(task_id):
            emitted_check = "diagnosis_exhausted"
            reason = (
                "repeated unknown attribution (S2): DIAGNOSE could not "
                "reproduce on the forensic package twice in a row; Archer "
                "RULING carries the paired-delta decision"
            )
        return evidence, emitted_check, reason

    def _diagnose_unknown_streak(self, task_id) -> bool:
        """M2: True when the most recent prior verdict for this task is
        already check=unknown (i.e. THIS unknown is a repeat).

        Pure event-log arithmetic: scan backward for this task's
        verdict.failed events; stop at the first one -- a prior unknown
        means the streak is live, anything else resets it."""
        if not task_id:
            return False
        store = getattr(self, "store", None)
        if store is None:
            return False
        for ev in reversed(list(store.events(self.run_id))):
            if ev.type != "verdict.failed":
                continue
            payload = ev.payload or {}
            if payload.get("task_id") != task_id:
                continue
            return payload.get("check") == "unknown"
        return False

    def _latest_forensics_ref(self, task_id) -> str | None:
        """M2: the most recent forensics_ref recorded in this task's
        verdict.failed evidence chain (the GREEN_GATE failure evidence
        carries it; see _raise_hard_failure)."""
        if not task_id:
            return None
        store = getattr(self, "store", None)
        if store is None:
            return None
        for ev in reversed(list(store.events(self.run_id))):
            if ev.type != "verdict.failed":
                continue
            payload = ev.payload or {}
            if payload.get("task_id") != task_id:
                continue
            evidence = payload.get("evidence")
            if not isinstance(evidence, str):
                continue
            try:
                data = json.loads(evidence)
            except ValueError:
                continue
            if isinstance(data, dict) and data.get("forensics_ref"):
                return str(data["forensics_ref"])
            # A verdict for this task without a forensics_ref (older or
            # non-gate shape): keep scanning -- the package may ride an
            # earlier gate verdict.
        return None

    def _shield_fix_manifest_mismatch(self, result: dict, changed: list) -> str | None:
        """PRISM-B28-R2-02: SHIELD_FIX include vs observed tests/ files,
        with the result_checkpoint dirty-retry tolerance (a retry may verify
        pre-existing files instead of creating new ones). Returns the
        evidence string on mismatch, None to pass."""
        manifest = result.get("artifact_manifest") or {}
        include = [e.get("path") for e in (manifest.get("include") or []) if e.get("path")]
        if not include:
            return None
        code_include = [p for p in include if p.startswith("tests/")]
        if set(code_include) == set(changed):
            return None
        dirty = self._dirty_files()
        # PRISM-B28-R3-01: an include that declares no tests/ path at all is
        # the extreme under-report — the grace branch must not pass vacuously
        # on all([]).
        if code_include and all(
            p in dirty and (self.repo / p).is_file() for p in code_include
        ):
            return None
        return (
            "artifact_manifest include paths do not match observed tests/ "
            f"files: include={sorted(code_include)}, observed={changed}"
        )

    def _emit_shield_commit(self, result, role, state, cmd, task_id):
        if not (
            state.stage == "M-IMPL"
            and role == "shield"
            and state.substate == "SHIELD_FIX"
            and result.get("status") == "done"
        ):
            return False
        # Stage only tests/ files
        tests_dir = self.repo / "tests"
        # Get list of changed files under tests/
        proc = git(self.repo, "status", "--porcelain", "--", "tests/")
        changed = (
            [line[3:] for line in proc.stdout.splitlines() if line.strip()]
            if proc.stdout.strip()
            else []
        )
        if not changed:
            # No tests/ changes - fail closed
            self._emit(
                "verdict.failed",
                {
                    "check": "scope",
                    "reason": "shield_fix_no_diff",
                    "evidence": "Shield SHIELD_FIX produced no tests/ diff",
                    "attempt": state.current_attempt + 1,
                },
                command_id=cmd.command_id,
                task_id=task_id,
            )
            return False
        # PRISM-B28-R2-02: the SHIELD_FIX manifest contract promises an
        # include==observed comparison — enforce it here (M-IMPL shield
        # WRITE bypasses the ResultCheckpoint pipeline).
        if self._shield_fix_manifest_mismatch(result, changed):
            self._emit(
                "verdict.failed",
                {
                    "check": "manifest",
                    "reason": "include_mismatch",
                    "evidence": self._shield_fix_manifest_mismatch(result, changed),
                    "attempt": state.current_attempt + 1,
                },
                command_id=cmd.command_id,
                task_id=task_id,
            )
            return False
        # M4 (convergence plan 2026-09-05): birth-time anchor
        # satisfiability lint on the freshly revised test assets --
        # same two rule-1 signatures as the M-TEST WRITE gate. Routes
        # as test_defect so Shield rewrites the anchor in-domain.
        from tracks.checks.anchor_lint import anchor_static_violations

        _anchor_violations: list[str] = []
        for rel in changed:
            if rel.endswith(".py"):
                _anchor_violations.extend(
                    anchor_static_violations(self.repo / rel)
                )
        if _anchor_violations:
            self._emit(
                "verdict.failed",
                {
                    "check": "test_defect",
                    "reason": (
                        "anchor_static (M4 rule 1): "
                        + "; ".join(_anchor_violations[:3])
                    ),
                    "evidence": "\n".join(_anchor_violations),
                    "attempt": state.current_attempt + 1,
                },
                command_id=cmd.command_id,
                task_id=task_id,
            )
            return False
        # Stage all changed tests/ files
        git(self.repo, "add", "--", "tests/")
        # Create commit with trailers
        task_id_val = state.current_task_id or ""
        attempt_val = str(state.current_attempt + 1)
        message = (
            f"Shield fix: {task_id_val} attempt {attempt_val}\n\n"
            f"Tracks-Task: {task_id_val}\n"
            f"Tracks-Attempt: {attempt_val}\n"
            f"command_id: {cmd.command_id}"
        )
        proc = _scoped_commit_if_staged(self.repo, message, paths=["tests/"])
        if proc is not None and proc.returncode != 0:
            self._emit(
                "verdict.failed",
                {
                    "check": "scope",
                    "reason": "shield_fix_commit_failed",
                    "evidence": proc.stderr or proc.stdout,
                    "attempt": state.current_attempt + 1,
                },
                command_id=cmd.command_id,
                task_id=task_id,
            )
            return False
        commit_sha = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        test_count = sum(1 for _ in tests_dir.rglob("test_*.py")) if tests_dir.exists() else 0
        self._emit(
            "test.committed",
            {"commit_sha": commit_sha, "test_count": test_count},
            command_id=cmd.command_id,
            task_id=task_id,
        )
        # B91 follow-up (re-baseline): the sanctioned fix commit joins the
        # task's immutable R family so the eventual G binds a provable anchor
        # that is ALSO the tree that gated it (no-op without a prior RED).
        if state.stage == "M-IMPL":
            self._rebaseline_red_family(task_id, commit_sha, cmd.command_id)
        return True

    def _reject_invalid_test_tasks(self, state, role, substate, assignment, cmd, task_id) -> bool:
        """D-32 fail-closed gate: return True (and emit a stub_gap failed
        outcome) when an M-TEST Shield WRITE assignment carries an invalid
        test-task contract, so the backend is never called."""
        applies = (
            state.stage in ("M-TEST", "M-IMPL")
            and substate == "WRITE"
            and role == "shield"
        )
        if not applies:
            return False
        valid = valid_test_tasks((assignment or {}).get("test_tasks"))
        if state.hotfix_issue is not None:
            from tracks.executor.test_tasks import _coverage_rows_with_test

            plan = self._vdir() / "test-plan.md"
            rows = _coverage_rows_with_test(
                plan.read_text(encoding="utf-8", errors="replace") if plan.exists() else ""
            )
            valid = valid and {row[0] for row in rows} == set(state.hotfix_anchor_acs or [])
        if valid:
            return False
        self._emit(
            "outcome.received",
            {
                "role": role,
                "status": "failed",
                "failure_class": "stub_gap",
                "self_report": "test-task contract invalid",
                "audit_evidence": (
                    "assignment.test_tasks must be a non-empty list of "
                    "{ac_id, layers, if_ids} with non-empty layers "
                    "(integration/e2e) and registered IF- ids; got "
                    f"{assignment.get('test_tasks') if assignment else None}"
                ),
            },
            command_id=cmd.command_id,
            task_id=task_id,
        )
        return True

    @staticmethod
    def _dispatch_log_start(p: dict, state: State) -> None:
        """Fix 5: emit a start line to stderr before the backend call so the
        operator sees what is being dispatched during a long ``trac run``.
        Flushed immediately; does not print agent stdout."""
        role = p.get("role", "?")
        substate = p.get("substate", "?")
        attempt = p.get("attempt", "?")
        objective = p.get("objective") or ""
        if not objective:
            assignment = p.get("assignment") or {}
            objective = assignment.get("kind") or ""
            if not objective:
                docs = p.get("docs")
                doc = p.get("doc")
                if docs:
                    objective = ", ".join(docs)
                elif doc:
                    objective = doc
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z")
        print(
            f"  {ts} [{state.stage}] dispatch {role} ({substate}, attempt {attempt})"
            f" {objective}".rstrip(),
            file=sys.stderr,
            flush=True,
        )

    @staticmethod
    def _dispatch_log_end(p: dict, result: dict, elapsed: float) -> None:
        """Fix 5: emit a completion line to stderr after the backend returns.
        For failures, includes failure_class and a one-line self_report."""
        role = p.get("role", "?")
        status = result.get("status", "?")
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z")
        line = f"  {ts} {role} {status} ({elapsed:.1f}s)"
        if status != "done":
            fc = result.get("failure_class") or "?"
            report = (result.get("self_report") or "").strip()
            report = report.splitlines()[0] if report else ""
            line += f" [{fc}]"
            if report:
                line += f" {report}"
        print(line, file=sys.stderr, flush=True)

    def _criteria_pack_mismatch(
        self, role, substate, state, verdict, assignment, result, cmd
    ) -> bool:
        """Return True when the criteria-pack identity mismatch was emitted
        (caller should skip the normal verdict emission).

        Only DONE outcomes carry the echo — a failed result (manifest
        malformed, provider error) legitimately lacks criteria_pack, and
        re-classifying it as a pack mismatch double-punishes the same
        attempt (live T-002 PRISM_FINAL: three 140-cap rejects each also
        burned as criteria_pack_mismatch, 2026-08-19)."""
        if result.get("status") != "done":
            return False
        if not (
            role == "prism"
            and (
                (verdict and substate == "PRISM_REVIEW" and state.stage == "M-TEST")
                or (
                    state.stage == "M-IMPL"
                    and substate in ("PRISM_PLAN", "PRISM_RED", "PRISM_FINAL", "DIAGNOSE")
                )
            )
        ):
            return False
        assigned_pack = (assignment or {}).get("criteria_pack")
        outcome_pack = result.get("criteria_pack")
        return bool(assigned_pack and outcome_pack != assigned_pack)

    def _apply_prism_review_fields(self, payload: dict, verdict: str, result: dict) -> None:
        """D-35 (SC-D35 §2.2/§2.3): prism verdict events carry the criteria
        pack plus, on revise, the structured review fields — including the
        routing key defect_classification (PRISM-D35-R2-01: without it the
        M-IMPL routers see None and every revise falls to the default route,
        red_defect/design_gap/ac_gap/spec_gap unreachable — fail-wrong) —
        and the blobs ref for the full body. M-IMPL reviews
        (PRISM_PLAN/RED/FINAL) emit through this legacy path rather than the
        ResultCheckpoint pipeline."""
        payload["criteria_pack"] = result.get("criteria_pack")
        # FR-0243 / IF-HOTFIX-009 (interfaces §1a): the M-DESIGN Prism hotfix
        # review carries the anchor_verdict routing field — 缺省 "upheld",
        # preserved "overturned" when Prism overturns the hotfix anchor so the
        # kernel routes M-DESIGN -> M-HOTFIX-TRIAGE/SAGE_TRIAGE without
        # consuming the M-DESIGN redispatch budget (kernel/machine.py L840).
        # Set before the pass early-return so a pass verdict still exposes the
        # default "upheld" for AC-FR0243-02 review observability.
        payload["anchor_verdict"] = result.get("anchor_verdict") or "upheld"
        if verdict == "pass":
            return
        for key in ("review_summary", "findings", "discussion_refs"):
            val = result.get(key)
            if val:
                payload[key] = val
        # Non-None guard mirrors _m_test_prism_payload: a present null would
        # defeat the reducers' absent-key defaults.
        if result.get("defect_classification") is not None:
            payload["defect_classification"] = result["defect_classification"]
        review_body = result.get("review_body")
        if isinstance(review_body, str) and review_body.strip():
            review_ref = self.store.write_audit_blob(review_body)
            if review_ref:
                payload["review_ref"] = review_ref

    def _emit_verdict(self, role, verdict, result, state, p, cmd, task_id):
        """Emit the verdict event (+ review.round_started for Prism revise)."""
        ev = _VERDICT_EVENT[role]
        payload = {"verdict": verdict, "diff_ref": result.get("diff_ref")}
        if role == "prism" and state.stage in ("M-TEST", "M-IMPL"):
            self._apply_prism_review_fields(payload, verdict, result)
        self._emit(ev, payload, command_id=cmd.command_id, task_id=task_id)
        if ev == "prism.verdict" and verdict != "pass":
            # flow.md §8.2: a revise verdict opens the next review round
            # (reducer bumps review_round and resets the reviewer flags).
            self._emit(
                "review.round_started",
                {"stage": p.get("stage"), "round": state.review_round + 1},
                command_id=cmd.command_id,
                task_id=task_id,
            )

    def _publish_prism_verdict(
        self, verdict, commit_sha, created_commit, result_id, state, cmd, task_id
    ):
        """Hotfix extension of the ResultCheckpoint publish (FR-0243 /
        IF-HOTFIX-009, interfaces §1a): every M-DESIGN prism.verdict event
        carries the anchor verdict — 缺省 "upheld", preserved "overturned" —
        so Prism's hotfix-anchor review is observable (AC-FR0243-02) and the
        kernel routes an overturned anchor back to M-HOTFIX-TRIAGE/SAGE_TRIAGE
        without consuming the M-DESIGN redispatch budget (AC-FR0243-03). The
        mixin branch threads only the D-35 review fields, and its checkpoint
        domain payload skips _apply_prism_review_fields on pass
        (PRISM-V06-R1-T003-01), so a pass checkpoint has no explicit
        anchor_verdict key and defaults to upheld here. Non-M-DESIGN publish
        keeps the mixin semantics via delegation."""
        if state.stage == "M-VERIFY":
            self._publish_verify_final_verdict(
                verdict, commit_sha, created_commit, result_id, cmd, task_id
            )
            return
        if state.stage != "M-DESIGN":
            ResultCheckpointMixin._publish_prism_verdict(
                self, verdict, commit_sha, created_commit, result_id, state, cmd, task_id
            )
            return
        domain_payload = cmd.params.get("domain_event", {}).get("payload", {})
        payload = {
            "verdict": verdict,
            "diff_ref": commit_sha if created_commit and commit_sha else None,
            "result_id": result_id,
            "anchor_verdict": domain_payload.get("anchor_verdict") or "upheld",
        }
        for key in _DESIGN_PRISM_THREAD_KEYS:
            val = domain_payload.get(key)
            if val:
                payload[key] = val
        self._emit("prism.verdict", payload, command_id=cmd.command_id, task_id=task_id)
        if verdict != "pass":
            # flow.md §8.2: a revise verdict opens the next review round.
            round_payload = {"stage": "M-DESIGN", "round": state.review_round + 1}
            self._emit(
                "review.round_started",
                round_payload,
                command_id=cmd.command_id,
                task_id=task_id,
            )

    def _publish_verify_final_verdict(
        self,
        verdict,
        commit_sha,
        created_commit,
        result_id,
        cmd,
        task_id,
        *,
        result=None,
        assignment=None,
    ):
        """IF-VERIFY-005 / AC-FR0270: the same-candidate final-review verdict
        is scoped verify_final and bound to the frozen candidate. A pass
        exits M-VERIFY into M-SECURITY (all gates green, §1.1); any other
        verdict stops the chain blocked with the findings on the stream (no
        silent retry, no guessed next)."""
        result = result if isinstance(result, dict) else {}
        assignment = assignment if isinstance(assignment, dict) else {}
        frozen = self._latest_event("candidate.frozen")
        frozen_sha = (frozen.payload or {}).get("candidate_sha", "") if frozen else ""
        assigned_sha = assignment.get("candidate_sha")
        if assigned_sha is None:
            assigned_sha = (assignment.get("assignment") or {}).get("candidate_sha")
        if assigned_sha is None and isinstance(getattr(cmd, "params", None), dict):
            assigned_sha = cmd.params.get("candidate_sha")
        returned_sha = result.get("candidate_sha")
        candidate_sha = assigned_sha or frozen_sha
        identity_error = (
            not frozen_sha
            or not assigned_sha
            or assigned_sha != frozen_sha
            or (returned_sha is not None and returned_sha != assigned_sha)
        )
        if verdict not in ("pass", "revise"):
            identity_error = True
        if identity_error:
            self._emit(
                "attention.required",
                {
                    "reason": "verify_final_identity_mismatch"
                    if verdict in ("pass", "revise")
                    else "verify_final_invalid_verdict",
                    "stage": "M-VERIFY",
                    "candidate_sha": candidate_sha or frozen_sha,
                    "next": (
                        "re-dispatch VERIFY_FINAL with the frozen candidate and "
                        "a valid pass/revise review"
                    ),
                },
                command_id=cmd.command_id,
            )
            return
        payload = {
            "verdict": verdict,
            "diff_ref": commit_sha if created_commit and commit_sha else None,
            "result_id": result_id,
            "scope": "verify_final",
            "candidate_sha": candidate_sha,
        }
        for key in _DESIGN_PRISM_THREAD_KEYS:
            val = result.get(key)
            if val:
                payload[key] = val
        if result.get("review_body") and not payload.get("review_ref"):
            review_ref = self.store.write_audit_blob(result["review_body"])
            if review_ref:
                payload["review_ref"] = review_ref
        if verdict == "revise" and not self._verify_final_discussion_anchor(result):
            self._emit(
                "prism.verdict", payload, command_id=cmd.command_id, task_id=task_id
            )
            self._emit(
                "attention.required",
                {
                    "reason": "revise_without_findings",
                    "stage": "M-VERIFY",
                    "candidate_sha": candidate_sha,
                    "next": (
                        "anchor the blocking findings via trac discuss start; "
                        "trac run re-dispatches the review"
                    ),
                },
                command_id=cmd.command_id,
            )
            return
        self._emit(
            "prism.verdict", payload, command_id=cmd.command_id, task_id=task_id
        )
        if verdict == "pass":
            self._advance_verify_chain(cmd, candidate_sha)
        else:
            # FR-0271-03: a revise/failed final review is a valid block only
            # when the reviewer ANCHORED the blocking findings (thread keys on
            # the verdict payload — the same anchoring the v0.3 discuss
            # protocol requires). A bare revise/failed with no anchored
            # findings is judged revise_without_findings: it blocks the chain
            # but is recorded as an invalid block, never consumed as review
            # evidence (the re-dispatch owns the retry).
            self._emit(
                "attention.required",
                {
                    "reason": "verify_final_rejected",
                    "stage": "M-VERIFY",
                    "candidate_sha": candidate_sha,
                    "next": "address findings; trac run re-dispatches the review",
                },
                command_id=cmd.command_id,
            )

    def _verify_final_discussion_anchor(self, result: dict) -> bool:
        """Accept a blocker finding tied to a fresh open Prism/Lex thread.

        Findings marked ``simulated`` (the FakeBackend's synthesized revise
        content, effects/fake.py ``_ensure_verify_final_revise_fields``) are
        excluded from the blocker set: a simulation can never anchor itself as
        a real review block."""
        refs = result.get("discussion_refs")
        if not isinstance(refs, list) or not refs:
            return False
        findings = result.get("findings")
        blockers = {
            finding.get("id")
            for finding in findings
            if isinstance(finding, dict)
            and finding.get("severity") == "blocker"
            and finding.get("simulated") is not True
            and isinstance(finding.get("id"), str)
        } if isinstance(findings, list) else set()
        if not blockers:
            return False
        for ref in refs:
            if not isinstance(ref, dict):
                continue
            file_name = ref.get("file")
            thread_id = ref.get("thread_id")
            token = ref.get("token")
            finding_id = ref.get("finding_id")
            if (
                not isinstance(file_name, str)
                or not isinstance(thread_id, str)
                or not isinstance(token, dict)
                or finding_id not in blockers
            ):
                continue
            path = (self.repo / file_name).resolve()
            try:
                path.relative_to(self.repo.resolve())
            except ValueError:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            try:
                located = locate(text, token, thread_id)
            except (KeyError, TypeError, ValueError, AttributeError):
                continue
            if located.status != "unique":
                continue
            thread = next(
                (item for item in parse_threads(text) if item.thread_id == thread_id),
                None,
            )
            if thread is None or thread.status not in ("open", "reopen"):
                continue
            if thread.initiator.strip().lower() not in ("prism", "lex"):
                continue
            return True
        return False

    # -- v0.6 hotfix handlers (IF-HOTFIX-002/003/004/005/007) -----------------

    def _rollback_write_scope(self) -> list[str]:
        """B59 (#75): write-scope patterns of every dispatched task in this
        run (manifest allowed_paths + red_test_paths). The union bounds what
        the discarded M-IMPL cycles could legitimately have touched in the
        MAIN tree — dirty files inside it at rollback are residue of the
        discarded work."""
        patterns: set[str] = set()
        for ev in self.store.events(self.run_id):
            if ev.type != "task.started":
                continue
            manifest = ev.payload.get("manifest")
            if not isinstance(manifest, dict):
                continue
            for key in ("allowed_paths", "red_test_paths"):
                value = manifest.get(key)
                if isinstance(value, list):
                    patterns.update(
                        p for p in value if isinstance(p, str) and p.strip()
                    )
        return sorted(patterns)

    def _red_test_scope(self) -> list[str]:
        """B64 (#82): red_test_paths of every dispatched task in this run —
        Archer-authored manifest data (language-neutral; nothing hardcoded).
        These are Devon's RED unit artifacts: only Devon may rewrite them
        (red_defect -> RED re-pin). Shield's SHIELD_FIX write domain excludes
        them deterministically — the ownership wall is data, not prompt
        discipline (user ruling 2026-08-25; run 01M0S0FQ T-002: a test_defect
        misroute sent Shield into Devon's RED unit test)."""
        patterns: set[str] = set()
        for ev in self.store.events(self.run_id):
            if ev.type != "task.started":
                continue
            manifest = ev.payload.get("manifest")
            if not isinstance(manifest, dict):
                continue
            value = manifest.get("red_test_paths")
            if isinstance(value, list):
                patterns.update(
                    p for p in value if isinstance(p, str) and p.strip()
                )
        return sorted(patterns)

    @staticmethod
    def _path_in_write_scope(path: str, patterns: list[str]) -> bool:
        """Exact file, directory-prefix, or glob match against scope patterns
        (manifest patterns mix all three: ``tracks/project.py``,
        ``tests/unit``, ``.tracks/projects/**``)."""
        for pat in patterns:
            if path == pat:
                return True
            if path.startswith(pat.rstrip("/") + "/"):
                return True
            if fnmatch.fnmatch(path, pat):
                return True
        return False

    def _quarantine_rollback_residue(self) -> list[str]:
        """B59 (#75): stash main-tree residue intersecting the discarded
        stage's write scope. Run 01M0S0FQ: the previous T-001 cycle's
        implementation residue (tracks/project.py) survived every rollback
        and retry --clear-evidence in the main tree, then leaked into the new
        baseline via the M-TEST freeze and into Devon's environment checks.
        Untracked files count as residue too (Prism OOB R1 Blocker 01): the
        designed flow commits agent output during the pipeline, so anything
        dirty in scope at rollback is the discarded cycle's leftover.
        Stash (never destroy): the content stays recoverable and the
        discarded-scope tree returns to the committed baseline state.
        Best-effort: git failures leave the tree untouched and are reported
        by simply not listing the file as quarantined."""
        status = git(self.repo, "status", "--porcelain", check=False)
        dirty: list[str] = []
        for line in status.stdout.splitlines():
            if not line.strip():
                continue
            path = line[3:].strip()
            if " -> " in path:  # rename: keep both ends in scope checks
                path = path.split(" -> ")[-1]
            if path:
                dirty.append(path)
        scope = self._rollback_write_scope()
        matched = [p for p in dirty if self._path_in_write_scope(p, scope)]
        if not matched:
            return []
        proc = git(
            self.repo,
            "stash",
            "push",
            "-u",
            "-m",
            f"trac B59 rollback residue quarantine ({self.run_id})",
            "--",
            *matched,
            check=False,
        )
        return matched if proc.returncode == 0 else []

    def _do_rollback_stage(self, cmd, state, task_id, reconcile):
        # Reconcile idempotency: if stage.rolled_back was already persisted for
        # this command (crash between event commit and return), do not emit a
        # duplicate. Under normal recovery the pending command means the event
        # has not been logged yet, so this guard only fires on the edge case.
        if reconcile:
            already = any(
                e.type == "stage.rolled_back" and e.command_id == cmd.command_id
                for e in self.store.events(self.run_id)
            )
            if already:
                return
        # B57 re-fix defense in depth (review 2026-08-27): the CLI validates a
        # closed target set and the kernel adopts the approval payload's
        # to_stage -- but a hand-crafted out-of-band human.approval could
        # smuggle an arbitrary stage in. Mirror _do_recover_stage: reject
        # out-of-set targets with an audit blob, no event, no state change.
        # T-001 face (B) / IF-RELEASE-003: the closed set is derived from the
        # canonical stage order (machine table + the five release stages), so
        # the impl-cycle targets M-TEST/M-IMPL and the release stages are
        # legal return destinations; M-REQ-APPROVAL is never a target.
        allowed_targets = canonical_stage_order()
        if cmd.params.get("to_stage") not in allowed_targets:
            self.store.write_audit_blob(
                {
                    "event": "stage.rolled_back",
                    "command_id": cmd.command_id,
                    "rejected": True,
                    "reason": (
                        "rollback_stage target not in canonical closed set "
                        f"({'|'.join(allowed_targets)}): "
                        f"{cmd.params.get('to_stage')!r}"
                    ),
                }
            )
            return
        # B59 (#75): discarding M-IMPL progress must also discard its main-tree
        # residue, or the "discarded" work leaks into the next cycle's
        # environment (baseline freeze + agent verification).
        quarantined = (
            self._quarantine_rollback_residue() if state.stage == "M-IMPL" else []
        )
        self._emit(
            "stage.rolled_back",
            {
                "from_stage": state.stage,
                "to_stage": cmd.params["to_stage"],
                "reason": cmd.params.get("reason", ""),
                "quarantined": quarantined,
            },
            command_id=cmd.command_id,
        )

    def _consume_escape_cutover(self, outcome: dict) -> bool:
        """T-001 face (B) / AC-FR0287-02: dispatch-loop cutover consumption.

        With an established escape barrier (escape.barrier_established), an
        outcome whose originating seq <= cutover_seq was dispatched before
        the barrier: quarantine it (escape.late_outcome, status=quarantined;
        never checkpointed, never published, never overwriting State) and
        return True so the caller drops the outcome. Outcomes originating
        after the barrier (and barrier-less runs) pass through untouched.
        """
        barrier = None
        for ev in self.store.events(self.run_id):
            if ev.type == "escape.barrier_established":
                barrier = ev.payload
        if not barrier:
            return False
        seq = outcome.get("seq")
        if seq is None:
            # Resolve the originating dispatch's seq from its command.issued
            # event (the loop guard passes only the dispatch id).
            dispatch_id = outcome.get("dispatch_id") or ""
            for ev in self.store.events(self.run_id):
                if ev.type == "command.issued" and ev.command_id == dispatch_id:
                    seq = ev.seq
                    break
            outcome = {**outcome, "seq": seq}
        verdict = quarantine_late_outcome(barrier, outcome)
        if not verdict.get("quarantined"):
            return False
        self._emit(
            "escape.late_outcome",
            {
                "dispatch_id": verdict.get("dispatch_id", ""),
                "status": "quarantined",
                "outcome_seq": seq,
                "cutover_seq": verdict.get("cutover_seq"),
            },
        )
        return True

    # -- T-001 faces (G/I/J): release-domain handler registrations ----------
    #
    # must-not-drop wiring (plan_defect rounds): the handlers consume the
    # Command kinds routed by decide_release_stage (T-039) and emit the
    # domain event families; deep behavior lands with their owning anchors
    # (T-021 publish / T-029 known-issue / T-034 security).

    def _assert_agent_forbidden(self) -> bool:
        """(G) True when the selected backend is an Agent backend — the
        publish execution path must run on non-Agent infrastructure only."""
        backend_kind = type(self.backend).__name__.lower()
        return "opencode" in backend_kind or "agent" in backend_kind

    def _publish_fail(
        self, cmd, reason: str, *, candidate_sha: str = "", key: str = "", remote_check=None
    ):
        payload = {
            "reason": reason,
            "candidate_sha": candidate_sha,
            "idempotency_key": key,
        }
        if remote_check is not None:
            payload["remote_check"] = remote_check
        self._emit("publish.failed", payload, command_id=cmd.command_id)

    def _blocked_publish_payload(self, params: dict, events: list) -> dict:
        """publish.blocked payload; preview/candidate only when resolvable."""
        payload = {"reason": "agent_forbidden"}
        preview_digest = params.get("preview_digest")
        if not isinstance(preview_digest, str) or not preview_digest:
            previews = [event for event in events if event.type == "release.previewed"]
            preview_digest = (
                (previews[-1].payload or {}).get("preview_digest")
                if previews
                else None
            )
        if isinstance(preview_digest, str) and preview_digest:
            payload["preview_digest"] = preview_digest
        frozen = [event for event in events if event.type == "candidate.frozen"]
        candidate_sha = (
            str((frozen[-1].payload or {}).get("candidate_sha") or "")
            if frozen
            else ""
        )
        if candidate_sha:
            payload["candidate_sha"] = candidate_sha
        return payload

    def _do_execute_publish(self, cmd, state, task_id, reconcile):
        """(G) Consume Command(execute_publish) (bound to a preview digest):
        the agent gate runs first (publish.blocked reason=agent_forbidden,
        zero side effects), then the ordered batch — publish.planned ->
        publish.executed(done|reconciled_skip) per operation / publish.failed.
        All operations are validated before any effect; the batch stops on
        the first failed/conflicting effect and never emits an aggregate
        success over a failure."""
        params = dict(cmd.params or {})
        events = list(self.store.events(self.run_id))
        if self._assert_agent_forbidden():
            self._emit(
                "publish.blocked",
                self._blocked_publish_payload(params, events),
                command_id=cmd.command_id,
            )
            return
        facts = self._release_version_facts(state)
        if not facts.get("version"):
            envelope_version = next(
                (event.version for event in reversed(events) if event.version), ""
            )
            facts = version_facts(envelope_version, self.run_id)
        authority, error = resolve_publish_authority(
            self.repo,
            events,
            params.get("preview_digest"),
            facts,
            self.store.home,
        )
        if authority is None:
            if error == "ls_remote_failed":
                self._emit(
                    "attention.required",
                    {
                        "area": "release_facts",
                        "reason": error,
                        "stage": "M-PUBLISH",
                        "detail": "remote patch-tag census failed for the publish rebuild",
                        "next": "check origin access; trac run --resume retries publish",
                    },
                    command_id=cmd.command_id,
                )
            self._publish_fail(cmd, error or "malformed")
            return

        def publish_fail(reason, remote_check, key):
            self._publish_fail(
                cmd,
                reason,
                candidate_sha=authority["candidate_sha"],
                key=key,
                remote_check=remote_check,
            )

        effects = {
            "read_remote": publish_effects.read_remote_state,
            "push_tag": publish_effects.push_tag,
            "push_merge": publish_effects.push_merge,
            "create_release": publish_effects.create_release,
            "upload_artifact": publish_effects.upload_artifact,
        }
        execute_publish_operations(
            authority,
            cmd.command_id,
            lambda: list(self.store.events(self.run_id)),
            effects,
            self._emit,
            publish_fail,
            None,
        )

    def _do_register_known_issue(self, cmd, state, task_id, reconcile):
        """(I) Consume Command(register_known_issue): known_issue.registered
        on acceptance, known_issue.rejected on rejection."""
        params = dict(cmd.params or {})
        rejected = params.get("rejected") is True or params.get("decision") == "reject"
        self._emit(
            "known_issue.rejected" if rejected else "known_issue.registered",
            {
                "title": params.get("title", ""),
                "reason": params.get("reason", ""),
                "issue": params.get("issue"),
            },
            command_id=cmd.command_id,
        )

    def _do_assess_security(self, cmd, state, task_id, reconcile):
        """(J) Consume Command(assess_security) (IF-SECURITY-001,
        AC-FR0272-01/02): run the contract-declared scans, aggregate
        fail-closed (passed iff EVERY declared scan passed; malformed/missing
        -> unknown) and emit security.assessed bound to the frozen candidate
        with the policy digest. A non-passing aggregate stops the chain
        blocked -- the repair route (cve -> Archer advisory) owns the
        disposition (§1.0.4/§1.0.14, never a silent pass)."""
        params = dict(cmd.params or {})
        candidate_sha = params.get("candidate_sha") or ""
        if not candidate_sha:
            candidate_sha = str(getattr(state, "candidate_sha", "") or "")
        if not candidate_sha:
            frozen = self._latest_event("candidate.frozen")
            candidate_sha = (
                (frozen.payload or {}).get("candidate_sha", "") if frozen else ""
            )
        contract, digest, _source = self._load_or_default_contract(
            cmd, candidate_sha
        )
        if contract is None:
            return
        results = run_security_scans(contract, self.repo, candidate_sha)
        status = aggregate_security_status(results)
        payload = {
            "status": status,
            "candidate_sha": candidate_sha,
            "policy_digest": digest,
            "scans": [
                {
                    "id": r.gate_id,
                    "status": r.status,
                    "exit_code": r.exit_code,
                }
                for r in results
            ],
            "repair_route": "none",
        }
        if status != "passed":
            # §1.0.14 B: a failing/unknown scan is a cve-class finding ->
            # Archer advisory repair route (in-place, never a rollback).
            payload["repair_route"] = {
                "exit_class": "defect_repair",
                "defect_class": "cve",
                "owner": "Archer",
                "discipline": "cve_advisory",
                "budget_remaining": None,
            }
        self._emit(
            "security.assessed",
            payload,
            command_id=cmd.command_id,
        )

    # -- v0.8 M-VERIFY chain (IF-VERIFY-001/003/004, architecture §1.1) -------

    def _verify_chain_park_evidence(self, state) -> None:
        """M-IMPL escalation park evidence chain (FR-0287 wiring, §1.0.14):
        the parked run itself produces the machine-checkable M-VERIFY
        evidence (candidate freeze -> contract gates -> CI readback ->
        security -> release preview) so the Human escalation decision sees
        facts, not claims. Runs synchronously at the decide()->None park
        return; events only -- no stage change, no command queueing (the
        parked State the walk asserts stays untouched). Every step is
        idempotent per candidate_sha and any fail-closed block (dirty tree,
        contract refusal, gate failure, CI/security stop) halts the chain
        at that step (§1.0.14)."""
        if (
            state.stage,
            state.substate,
            getattr(state, "awaiting", None),
        ) != ("M-IMPL", "DIAGNOSE", "escalation"):
            return
        park_cmd = Command(kind="park_evidence_chain")
        candidate_sha = self._park_freeze_candidate(park_cmd)
        if candidate_sha is None:
            return
        # §1.1 chain order: freeze -> FULL_F judgment -> contract gates.
        self._emit_full_f_judgment(park_cmd, candidate_sha)
        contract, contract_digest = self._run_contract_gates(
            park_cmd, candidate_sha, state, resume=True
        )
        if contract is None:
            # Classify the stop by its actual stream evidence: a contract
            # refusal (host_contract.invalid) is the contract class, a
            # failing declared gate (local_gate.failed) the gate class —
            # the rewalk policy keys on this distinction.
            self._park_repair_route(
                park_cmd, candidate_sha, self._verify_stop_class(candidate_sha),
                self._park_stop_reason("local_gate.failed"),
            )
            return
        # OOB 2026-09-05 (origin disposition): a run parked at
        # M-IMPL/DIAGNOSE/escalation arrived here through a task failure
        # verdict. When the verify-side gates all pass -- e.g. the
        # runtime-materialized default contract's declared no-op gate
        # (6ec2dc3) -- that origin defect still requires its observable
        # disposition (interfaces §1d unified repair route: every non-pass
        # exit has one). Without this, the walked undeclared-host scenario
        # parked at CI credentials attention with the M-IMPL defect never
        # classified, and the frozen inplace-repair anchors
        # (test_no_auto_rollback_in_place_rounds et al.) lost their
        # repair.round_started (run 01M19FJVES7G113RD8QXXY3PQZ, red since
        # 6ec2dc3 landed). Idempotency/budget/closed-set guards all apply
        # inside _park_repair_route; the chain always proceeds to its
        # CI / security / preview stops unchanged (9ba8dc9 contract) -- a
        # fresh round re-arms the parked walk through its reducer and the
        # repair dispatch plays out AFTER the release face has produced its
        # evidence; yielding here instead stranded every walked scenario
        # before the M-VERIFY producers could fire (GREEN gate, run
        # 01M19FJVES7G113RD8QXXY3PQZ attempt log: preserved DBs show
        # candidate.frozen/local_gate.passed then nothing).
        self._park_origin_repair_route(park_cmd, candidate_sha, state)
        if not self._park_observe_ci(park_cmd, candidate_sha, contract):
            return
        if not self._park_assess_security(
            park_cmd, candidate_sha, contract, contract_digest
        ):
            security_status = self._park_stop_payload("security.assessed").get(
                "status"
            )
            if security_status == "failed":
                # §1.0.14 B: a failing scan is a cve-class finding -> Archer
                # advisory repair route (in-place, never a rollback); the
                # Known-Issue registration kind stays security_finding --
                # zero known issue for security (FR-0286-8).
                self._park_repair_route(
                    park_cmd,
                    candidate_sha,
                    "cve",
                    self._park_stop_reason("security.assessed"),
                    registration_kind="security_finding",
                )
            return
        self._park_preview(park_cmd, candidate_sha, contract_digest)

    def _try_freeze(self, cmd) -> m_verify.CandidateIdentity | None:
        """freeze_candidate with the dirty-tree refusal mapped to
        attention.required (interfaces §1d; §1.0.14 A in-place retry)."""
        try:
            return m_verify.freeze_candidate(self.repo)
        except m_verify.FreezeBlocked as err:
            self._emit(
                "attention.required",
                {
                    "area": "freeze",
                    "reason": "dirty_tree",
                    "stage": "M-VERIFY",
                    "detail": str(err),
                    "next": "clean tracked changes; trac run retries in place",
                },
                command_id=cmd.command_id,
            )
            return None

    def _park_freeze_candidate(self, cmd) -> str | None:
        """Park chain step 1 (IF-VERIFY-001): bind the full HEAD SHA as the
        candidate. Same-HEAD re-entry is idempotent (no re-freeze,
        SM-01.17); a drifted HEAD never re-freezes on its own -- it is
        marked candidate.stale and the chain halts fail-closed (§1d
        idempotent no-refreeze). Only an OPEN repair round (§1.0.14 B,
        SM-01.20: a fix commit is a new candidate) legitimizes the
        re-freeze: the old frozen candidate is marked stale, its
        downstream evidence goes stale with reason=fix_new_candidate
        (§1.0.14 B), and the new HEAD freezes as a fresh candidate so the
        chain re-walks it -- never a reuse of stale evidence."""
        frozen = self._latest_event("candidate.frozen")
        frozen_sha = ""
        if frozen is not None:
            frozen_sha = (frozen.payload or {}).get("candidate_sha", "")
        identity = self._try_freeze(cmd)
        if identity is None:
            return None
        if frozen is not None and identity.candidate_sha == frozen_sha:
            return frozen_sha
        if frozen is not None:
            if not self._repair_rewalk_allowed(frozen_sha):
                # SM-01.17: no repair context -- mark the drift, never
                # re-freeze; the walk's repair routing owns what happens
                # next.
                self._emit(
                    "candidate.stale",
                    {
                        "candidate_sha": frozen_sha,
                        "reason": "head_moved",
                        "detail": (
                            f"HEAD {identity.candidate_sha[:12]} moved past "
                            f"frozen candidate {frozen_sha[:12]}"
                        ),
                    },
                    command_id=cmd.command_id,
                )
                return None
            self._emit(
                "candidate.stale",
                {
                    "candidate_sha": frozen_sha,
                    "reason": "head_moved",
                    "detail": (
                        f"HEAD {identity.candidate_sha[:12]} moved past "
                        f"frozen candidate {frozen_sha[:12]}"
                    ),
                },
                command_id=cmd.command_id,
            )
            # §1.0.14 B / SM-01.20: a repair commit is a new candidate --
            # the old candidate's downstream evidence goes stale with
            # reason=fix_new_candidate (idempotent per old candidate).
            already_staled = any(
                e.type == "evidence.staled"
                and (e.payload or {}).get("reason") == "fix_new_candidate"
                and (e.payload or {}).get("old_candidate") == frozen_sha
                for e in self.store.events(self.run_id)
            )
            if not already_staled:
                staled = _repair.mark_fix_new_candidate(self.run_id, frozen_sha)
                self._emit(
                    staled["event"],
                    {k: v for k, v in staled.items() if k != "event"},
                    command_id=cmd.command_id,
                )
        self._emit(
            "candidate.frozen",
            {
                "candidate_sha": identity.candidate_sha,
                "clean_tree": identity.clean_tree,
                "branch": identity.branch,
                "frozen_at_seq": self._next_event_seq(),
            },
            command_id=cmd.command_id,
        )
        return identity.candidate_sha

    def _park_observe_ci(self, cmd, candidate_sha: str, contract) -> bool:
        """Park chain step 3 (IF-VERIFY-004, AC-FR0270-01..03): API readback
        of the required-CI run bound to the frozen candidate; a resumed run
        never repeats the API call (FR-0270). Returns False when the chain
        is blocked with the attention/binding verdict already on the
        stream (missing config/credentials, network error, binding
        mismatch -- never a silent pass)."""
        seen = [
            e
            for e in self.store.events(self.run_id)
            if e.type == "ci.run_observed"
            and (e.payload or {}).get("candidate_sha") == candidate_sha
        ]
        if seen:
            return (seen[-1].payload or {}).get("api_verified") is True
        run_payload = self._readback_ci_binding(
            cmd, candidate_sha, contract.ci or {}
        )
        if run_payload is None:
            return False
        self._emit("ci.run_observed", run_payload, command_id=cmd.command_id)
        return True

    def _park_assess_security(
        self, cmd, candidate_sha: str, contract, contract_digest: str
    ) -> bool:
        """Park chain step 4 (IF-SECURITY-001 face): run the contract-
        declared scans and aggregate fail-closed -- no declared scans or
        any malformed result aggregates to unknown, never a pass (§1.0.4).
        Emits security.assessed bound to the candidate; returns True only
        for a passed assessment so the preview can never imply a green
        policy that was never verified."""
        seen = [
            e
            for e in self.store.events(self.run_id)
            if e.type == "security.assessed"
            and (e.payload or {}).get("candidate_sha") == candidate_sha
        ]
        if seen:
            return (seen[-1].payload or {}).get("status") == "passed"
        results = run_security_scans(contract, self.repo, candidate_sha)
        status = aggregate_security_status(results)
        payload = {
            "status": status,
            "candidate_sha": candidate_sha,
            "policy_digest": contract_digest,
            "findings": [
                {
                    "scan_id": r.gate_id,
                    "status": r.status,
                    "exit_code": r.exit_code,
                }
                for r in results
            ],
            "repair_route": "none",
        }
        if status != "passed":
            # §1.0.14 B / interfaces §1d: a non-passing assessment always
            # carries its unified repair route (cve-class -> Archer advisory),
            # never a silent "none".
            payload["repair_route"] = {
                "exit_class": "defect_repair",
                "defect_class": "cve",
                "owner": "Archer",
                "discipline": "cve_advisory",
                "budget_remaining": None,
            }
        self._emit("security.assessed", payload, command_id=cmd.command_id)
        return status == "passed"

    def _park_stop_payload(self, event_type: str) -> dict:
        """Latest park-chain stop event payload (empty when absent)."""
        event = self._latest_event(event_type)
        if event is None or not isinstance(event.payload, dict):
            return {}
        return event.payload

    def _park_stop_reason(self, event_type: str) -> str:
        """Reason token of the latest stop event (fail-closed detail)."""
        return str(self._park_stop_payload(event_type).get("reason", ""))

    def _park_repair_budget_used(self) -> int:
        """Repair rounds already opened in this run (FR-0286 §4: the budget
        is finite, default 3 -- ``repair=in_place round=<n>/3``)."""
        return sum(
            1
            for e in self.store.events(self.run_id)
            if e.type == "repair.round_started"
        )

    def _park_candidate_seen(
        self, event_types: tuple, candidate_sha: str, task_id: str | None = None
    ) -> bool:
        """True when any of the event types is already bound to the
        candidate -- the per-candidate idempotency guard of the repair
        route (a re-entered park never re-opens a round or re-registers).

        With ``task_id`` the match narrows to a registration of the SAME
        task (FR-0286 §5 per-defect waiver: a new task's defect registers
        its own known issue while a re-entered park of an already-registered
        task stays one-shot). A legacy registration without a task id is
        treated as covering the whole candidate (fail-safe)."""
        for event in self.store.events(self.run_id):
            if event.type not in event_types:
                continue
            payload = event.payload or {}
            if payload.get("candidate_sha") != candidate_sha:
                continue
            if task_id is None:
                return True
            event_task = payload.get("task_id")
            if event_task is None or str(event_task) == task_id:
                return True
        return False

    def _park_current_task_id(self) -> str:
        """The task the current failed disposition belongs to (latest lease)."""
        for event in reversed(list(self.store.events(self.run_id))):
            if event.type == "task.started":
                return str((event.payload or {}).get("task_id") or "")
            if event.type == "ledger.opened" and (event.payload or {}).get("task_id"):
                return str(event.payload["task_id"])
        return ""

    def _park_next_known_issue_number(self) -> int:
        """Durable issue numbering: max recorded number + 1 (never reused).

        The process-local counter cannot number registrations across the M7
        handover boundary (two drives would both mint #100); the event
        stream is the durable authority (FR-0286 §5)."""
        numbers = [
            int(payload["issue_number"])
            for event in self.store.events(self.run_id)
            if event.type == "known_issue.registered"
            for payload in [(event.payload or {})]
            if isinstance(payload.get("issue_number"), int)
            and not isinstance(payload.get("issue_number"), bool)
        ]
        return max(numbers, default=99) + 1

    def _park_task_waiver_refs(self, task_id: str) -> tuple[list[str], list[str]]:
        """(ac_refs, node_refs) the current disposition waives.

        AC refs come from the task's declared ac_refs; node refs are the
        latest FULL failed nodes bound to those ACs by the test markers --
        exact per-AC association, never the whole failed set (a different
        task's defect must register independently)."""
        acs: list[str] = []
        for event in reversed(list(self.store.events(self.run_id))):
            if event.type == "task.started" and str(
                (event.payload or {}).get("task_id") or ""
            ) == task_id:
                acs = [
                    str(ac)
                    for ac in ((event.payload.get("task") or {}).get("ac_refs") or [])
                    if str(ac).strip()
                ]
                break
        failed: list[str] = []
        for event in reversed(list(self.store.events(self.run_id))):
            if event.type == "full.executed":
                failed = [
                    str(node)
                    for node in ((event.payload or {}).get("failed_nodes") or [])
                    if str(node).strip()
                ]
                break
        if not acs:
            return [], failed
        waived_acs = set(acs)
        return acs, [
            node
            for node in failed
            if self._file_test_markers(node.partition("::")[0]) & waived_acs
        ]

    def _file_test_markers(self, rel_path: str) -> set[str]:
        """TRACKS-TRACE AC markers bound to one test file (empty on miss).

        The file-level binding is the trace gate's own convention
        (``_scan_test_markers``): every AC marker in the file binds the
        file's nodes."""
        if not rel_path:
            return set()
        try:
            text = (self.repo / rel_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return set()
        from tracks.checks.trace import _MARKER_LINE  # lazy: checks -> executor import

        return {match.group(2) for match in _MARKER_LINE.finditer(text)}

    def _waived_nodes(self, nodes: list[str]) -> set[str]:
        """Nodes waived by registered known issues (FR-0286 §5, single truth).

        Consumes the event-derived association the same way for producer
        selection, required-green judgment and ledger recording: direct
        ``node_refs`` always apply; AC refs resolve through the file-level
        TRACKS-TRACE markers of the given nodes."""
        assoc = _repair.waived_associations(self.store.events(self.run_id))
        waived = {node for node in nodes if node in assoc["nodes"]}
        acs = assoc["acs"]
        if not acs:
            return waived
        markers: dict[str, set[str]] = {}
        for node in nodes:
            if node in waived:
                continue
            path = node.partition("::")[0]
            if path not in markers:
                markers[path] = self._file_test_markers(path)
            if markers[path] & acs:
                waived.add(node)
        return waived

    def _park_prism_attribution(self) -> dict:
        """Prism attribution confirmation (IF-KNOWNISSUE-001): a registered
        failed verdict is the park chain's only Prism signal -- without one
        the empty attribution keeps judge_irreparable False (no confirmed
        product defect is ever registered on an unconfirmed attribution)."""
        for event in self.store.events(self.run_id):
            if event.type in ("verdict.failed", "prism.verdict"):
                return {"attribution_unchanged": True}
        return {}

    def _park_repair_route(
        self,
        cmd,
        candidate_sha: str,
        finding_kind: str,
        reason: str,
        registration_kind: str | None = None,
    ) -> bool:
        """§1.0.14 B/C-class in-place repair face of the park chain.

        Classifies the chain stop through the FR-0286 closed mapping
        (classify_defect), opens one repair round per chain run within the
        budget (repair.round_started + repair_route), and on budget
        exhaustion routes the irreparable verdict to Known Issue
        registration. ``registration_kind`` overrides the Known-Issue
        attribution kind when the finding's registration vocabulary differs
        from its repair class (a failed security scan classifies cve ->
        Archer advisory, but registers -- and is rejected -- as
        ``security_finding``: zero known issue for security, FR-0286-8).
        Never a stage rollback (AC-FR0286-01); never a guessed route for an
        unknown finding (fail closed). Returns True when a fresh round
        opened -- the round's reducer re-arms the parked walk (the repair
        dispatch owns the next move); False when the disposition resolved
        without one (known issue registered/rejected, or a fail-closed
        classification)."""
        classification = _repair.classify_defect({"kind": finding_kind}, {})
        if classification.get("failed_class"):
            return False
        task_id = self._park_current_task_id()
        if self._park_candidate_seen(
            ("known_issue.registered",), candidate_sha, task_id or None
        ):
            return False  # task registration idempotency: one disposition per
            # task; the ROUND budget is the bounded loop -- each re-entered
            # park of a still-unresolved disposition opens the next round (up
            # to the budget) so attempt-exhausted walks reach the C-class
            # exit, and a NEW task's defect registers its own disposition
            # (FR-0286 §5 per-defect waiver).
        budget = 3
        used = self._park_repair_budget_used()
        if used >= budget:
            self._park_known_issue(
                cmd,
                candidate_sha,
                registration_kind or finding_kind,
                reason,
                used,
                budget,
                task_id=task_id,
            )
            return False
        result = _repair.open_repair_round(self.run_id, classification, budget)
        payload = {k: v for k, v in result.items() if k != "event"}
        # The round number is event-derived (persistent across processes),
        # never the in-memory counter of this walk invocation.
        payload["round"] = used + 1
        payload["candidate_sha"] = candidate_sha
        payload["repair_route"] = {
            "defect_class": classification["defect_class"],
            "owner": classification["owner"],
            "discipline": classification["discipline"],
            "budget_remaining": budget - (used + 1),
        }
        if reason:
            payload["reason"] = reason
        self._emit(result["event"], payload, command_id=cmd.command_id)
        return True

    def _park_origin_repair_route(self, cmd, candidate_sha: str, state) -> bool:
        """OOB 2026-09-05: open the repair disposition for the ORIGIN defect
        that parked the run (M-IMPL task failure) when the verify-side gates
        all passed. Classifies behaviour (owner Devon, red_first) from the
        latest impl_defect verdict; every _park_repair_route guard applies
        (budget, closed-set classification). test_defect origins stay
        fail-closed (not in the verify-side classification table -- the
        ruling channel owns them). Returns True when a fresh round opened
        (the round re-arms the walk; the chain yields to the repair).

        §11.4 irreparability #1 (AC-FR0286-04/05, OOB 2026-09-06): a re-park
        on a candidate whose repair round is already open, with the repair
        dispatches' attempt budget burned (state.current_attempt >= 3, the
        shared M-IMPL budget whose exhaustion parked the run), means the fix
        attempts were spent WITHOUT landing a fix commit -- the round count
        alone can never advance past one (only a fix commit mints a new
        candidate). Route the C-class known-issue exit instead of silently
        stopping at the one-shot round guard."""
        round_seen = self._park_candidate_seen(
            ("repair.round_started",), candidate_sha
        )
        task_id = self._park_current_task_id()
        if round_seen and state.current_attempt >= 3:
            budget = 3
            self._park_known_issue(
                cmd,
                candidate_sha,
                "behavior",
                (
                    "repair attempts exhausted without a fix commit "
                    "(§11.4 irreparability #1: attempt budget spent, "
                    "candidate unchanged)"
                ),
                # §11.4 #1 measures the budget in FIX ATTEMPTS, not landed
                # fix commits: the attempt exhaustion IS the budget
                # exhaustion for this criterion (round-counting alone can
                # never reach it without a fix -- judge_irreparable would
                # silently veto the C-class exit).
                used=budget,
                budget=budget,
                task_id=task_id,
            )
            return False
        if self._park_candidate_seen(
            ("known_issue.registered",), candidate_sha, task_id or None
        ):
            return False
        origin_reason = ""
        origin = False
        for event in self.store.events(self.run_id):
            if event.type != "verdict.failed":
                continue
            payload = event.payload or {}
            if payload.get("check") == "impl_defect":
                origin = True
                origin_reason = str(payload.get("reason") or "")
        if not origin:
            return False
        return self._park_repair_route(
            cmd,
            candidate_sha,
            "behavior",
            origin_reason or "park origin: M-IMPL task failure verdict",
        )

    def _park_known_issue(
        self,
        cmd,
        candidate_sha: str,
        finding_kind: str,
        reason: str,
        used: int,
        budget: int,
        task_id: str | None = None,
    ) -> None:
        """C-class exit (§1.0.14): with the budget exhausted and Prism
        confirming the attribution, a product-quality defect registers a
        Known Issue (known-issue label, candidate+evidence linked); the
        excluded classes (mechanism/security) are rejected with
        not_product_defect -- zero known issue for security.

        FR-0286 §5: the registration carries the waived task and its
        AC/node refs so the machine closes the task as waived and the FULL
        gates exclude exactly those bound nodes."""
        task_id = task_id or self._park_current_task_id()
        if self._park_candidate_seen(
            ("known_issue.registered", "known_issue.rejected"),
            candidate_sha,
            task_id or None,
        ):
            return
        attribution = self._park_prism_attribution()
        if not _repair.judge_irreparable(used, budget, attribution):
            return
        verdict = self._park_stop_payload("verdict.failed")
        ac_refs, node_refs = self._park_task_waiver_refs(task_id)
        # FR-0286 §5 two-drive disposition: the first exhausted park records
        # the observation (association refs included) and leaves the run
        # parked -- the escalation remains a legal Human escape source
        # (FR-0287). The next drive commits the registration, whose machine
        # projection lifts the park and waives the task.
        if not self._park_candidate_seen(
            ("known_issue.pending",), candidate_sha, task_id or None
        ):
            self._emit(
                "known_issue.pending",
                {
                    "candidate_sha": candidate_sha,
                    "task_id": task_id or None,
                    "ac_refs": ac_refs,
                    "node_refs": node_refs,
                    "reason": reason,
                },
                command_id=cmd.command_id,
            )
            return
        item_or_ac = str(
            verdict.get("item_or_ac")
            or (ac_refs[0] if ac_refs else "")
            or verdict.get("classification")
            or reason
        )
        registration = _repair.register_known_issue(
            "repo",
            {
                "kind": finding_kind,
                "item_or_ac": item_or_ac,
                "task_id": task_id or None,
                "ac_refs": ac_refs,
                "node_refs": node_refs,
                "issue_number": self._park_next_known_issue_number(),
            },
            candidate_sha,
        )
        payload = {
            k: v for k, v in registration.items() if k != "event"
        }
        self._emit(registration["event"], payload, command_id=cmd.command_id)
        if registration["event"] == "known_issue.registered":
            # FR-0283 mapping face: a known issue is durable run state too --
            # it enters the map explicitly non-authoritative so the M-MILESTONE
            # closer audits it as an unclosable identity instead of leaving an
            # invisible waiver (never api_verified=true).
            persist_issue_mapping(
                self.repo,
                f"known_issue:{registration.get('issue_number')}",
                {
                    "issue_number": registration.get("issue_number"),
                    "url": registration.get("url", ""),
                    "api_verified": False,
                    "authoritative": False,
                    "source": "known_issue",
                    "task_id": task_id or None,
                    "baseline_digest": candidate_sha,
                },
            )
            # AC-FR0286-05 informed consent: the registered known issue must
            # appear in the release preview. Regenerate ONLY when a prior
            # preview already exists for this candidate -- that is the one
            # case the listing can be stale (the chain completed CI/security
            # before the registration landed). Without a prior preview the
            # chain's own tail preview (after the CI readback and security
            # steps, which a Known Issue never waives) lists the issue
            # through list_known_issues_for_preview; previewing here would
            # short-circuit those gates (live 01M284A3: waiver preview with
            # no CI/prism-final/security evidence, correctly rejected by
            # the release gate and unreachable forever after).
            prior = next(
                (
                    event
                    for event in reversed(list(self.store.events(self.run_id)))
                    if event.type == "release.previewed"
                    and (event.payload or {}).get("candidate_sha") == candidate_sha
                ),
                None,
            )
            if prior is not None:
                # _park_preview re-resolves the (materialized default)
                # contract from its stable source; the prior digest pins
                # the expectation and assemble_preview fails closed on any
                # contract drift.
                self._park_preview(cmd, candidate_sha, "")

    def _release_journey(self, state, cmd) -> str | None:
        """Resolve the preview's declared journey (IF-JOURNEY-001 §1m).

        A hotfix run owns its journey on the State (``hotfix_scenario``:
        post-release -> ``post_release``, dev -> ``dev``); every other run
        keeps the command-declared journey (default resolution happens in
        ``release_preview._journey`` -- feature when declared)."""
        scenario = getattr(state, "hotfix_scenario", None)
        if scenario:
            return {
                "post-release": "post_release",
                "post_release": "post_release",
                "dev": "dev",
            }.get(str(scenario), str(scenario))
        return (cmd.params or {}).get("journey")

    def _park_preview(
        self, cmd, candidate_sha: str, contract_or_digest, contract_digest: str | None = None
    ) -> None:
        """Park chain step 5 (IF-RELEASE-002 face): assemble the content-
        addressed preview from the verified evidence digests; reached only
        after gates + CI + security are green. Regenerates when the
        registered known-issue set changed since the last preview
        (AC-FR0286-05 informed consent -- the listing must never be stale);
        regeneration appends a new event, the old preview is never
        overwritten (§1.0.5)."""
        if isinstance(contract_or_digest, str):
            contract, contract_digest, _source = self._load_or_default_contract(
                cmd, candidate_sha
            )
            if contract is None:
                return
        else:
            contract = contract_or_digest
        events = list(self.store.events(self.run_id))
        known_issues = _repair.list_known_issues_for_preview(self.run_id, events)
        state = self.store.state(self.run_id)
        version = getattr(state, "version", None) or next(
            (event.version for event in reversed(events) if event.version), ""
        )
        journey = self._release_journey(state, cmd)
        facts = version_facts(version, self.run_id)
        patch_line = str(
            getattr(getattr(contract, "version", None), "patch_line", "") or ""
        )
        table = _contract_table(contract)
        facts, error = complete_version_facts(
            self.repo,
            facts,
            patch_line,
            needs_n=operation_plan_needs_n(table, journey or ""),
        )
        if facts is None:
            # Honest attention: a configured remote whose tag census cannot
            # be read must not silently resolve {n}=1 (patch identity guess).
            self._emit(
                "attention.required",
                {
                    "area": "release_facts",
                    "reason": error or "release_facts_unresolved",
                    "stage": "M-RELEASE",
                    "candidate_sha": candidate_sha,
                    "detail": "remote patch-tag census failed for patch_line "
                    f"{patch_line!r}",
                    "next": "check origin access; trac run retries the preview",
                },
                command_id=cmd.command_id,
            )
            return
        preview = assemble_preview(
            self.repo,
            contract,
            candidate_sha,
            contract_digest,
            facts,
            events,
            journey=journey,
            known_issues=known_issues,
        )
        if preview is None:
            return
        seen = [
            event for event in events
            if event.type == "release.previewed"
            and (event.payload or {}).get("candidate_sha") == candidate_sha
        ]
        if seen:
            last = seen[-1].payload or {}
            if preview_inputs_match(last, preview) and preview_blob_matches(
                self.store.home, last
            ):
                return
        blob = self.store.write_audit_blob(preview)
        if not blob:
            return
        preview = dict(preview)
        preview["blob_ref"] = f".tracks/runtime/blobs/{blob}"
        self._emit("release.previewed", preview, command_id=cmd.command_id)

    def _release_version_facts(self, state) -> dict:
        """Placeholder scope for contract gate commands ({version}/{major}/...

        Shared base facts (IF-JOURNEY-001 §1m): a hotfix identity inherits
        its target's ``{major}/{minor}`` (``v0.8-hotfix-42`` -> ``0.8``)
        while ``{version}`` keeps the run identity, and ``{ulid}`` carries
        the run id. ``{n}`` is completed by the release face through
        :func:`complete_version_facts` (remote tag census). A gate command
        referencing an undeclared placeholder fails closed through the
        per-gate execution guard (unknown placeholder -> local_gate.failed),
        never a guessed substitution (§1.0.4)."""
        return version_facts(getattr(state, "version", None) or "", self.run_id)

    def _fail_verify_block(self, cmd, candidate_sha, reason, detail):
        """Fail-closed M-VERIFY contract refusal (AC-FR0269-02): the
        contract-level refusal lands host_contract.invalid +
        local_gate.failed and halts the chain blocked -- no M-SECURITY
        entry, no guessed commands (§1.0.4)."""
        self._emit(
            "host_contract.invalid",
            {"candidate_sha": candidate_sha, "reason": reason, "detail": detail},
            command_id=cmd.command_id,
        )
        self._emit(
            "local_gate.failed",
            {"kind": "contract", "candidate_sha": candidate_sha, "reason": reason},
            command_id=cmd.command_id,
        )

    def _do_freeze_candidate(self, cmd, state, task_id, reconcile):
        """M-VERIFY chain head (IF-VERIFY-001, AC-FR0267-01/02): bind the
        candidate identity on a clean tree and hand to the local-gate chain.
        A dirty tree lands attention.required(reason=dirty_tree) with no
        freeze; cleanup + ``trac run`` retries in place (§1.0.14 A,
        candidate unchanged). Idempotent (SM-01.17, interfaces §1d): an
        existing successful freeze is never re-frozen and a drifted HEAD
        never mints a candidate without an OPEN repair round -- the drift
        is marked candidate.stale and the chain stops fail-closed (the
        gate lives in the shared freeze face so the plain entry and the
        park evidence chain cannot disagree). Only §1.0.14 B / SM-01.20
        (an open repair round: a fix commit is a new candidate that
        re-walks the full M-VERIFY chain) legitimizes the re-freeze with
        its evidence.staled(reason=fix_new_candidate) semantics."""
        if reconcile and self._latest_event("candidate.frozen") is not None:
            return
        candidate_sha = self._park_freeze_candidate(cmd)
        if candidate_sha is None:
            return
        # architecture §1.1 chain order: freeze -> judge_full_f_reuse
        # (evidence.reused | full.executed) -> run_local_gates.
        self.issue(
            Command(
                kind="judge_full_f_reuse",
                params={"candidate_sha": candidate_sha},
            )
        )

    def _full_f_producer(self, candidate_sha):
        """Find a complete producer execution still selected for this candidate."""
        events = list(self.store.events(self.run_id))
        selections = [
            event
            for event in events
            if event.type == "test.selected" and event.payload.get("scope") == "full"
        ]
        latest_selection = selections[-1] if selections else None
        for event in reversed(events):
            payload = event.payload or {}
            if event.type != "full.executed":
                continue
            if payload.get("execution_commit") != candidate_sha:
                continue
            if payload.get("passed") is not True or not payload.get("full_f_eligible"):
                continue
            selection_seq = payload.get("selection_event_seq")
            if not isinstance(selection_seq, int) or latest_selection is None:
                continue
            if latest_selection.seq != selection_seq:
                # A newer full selection supersedes the producer, even when it
                # has no execution proof of its own.
                continue
            if event.seq <= selection_seq:
                continue
            selection = next((item for item in selections if item.seq == selection_seq), None)
            if selection is None:
                continue
            if event.command_id != selection.command_id:
                continue
            if selection.payload.get("selection_id") != payload.get("selection_id"):
                continue
            if selection.command_id != payload.get("selection_command_id"):
                continue
            if selection.payload.get("commit") != payload.get("execution_commit"):
                continue
            if not payload.get("identity_basis") or not payload.get("outcomes_ref"):
                continue
            try:
                outcomes = self._read_runtime_blob(payload["outcomes_ref"])
            except (OSError, TypeError, ValueError, TestSelectError):
                continue
            selected_nodes = {
                str(node) for node in (selection.payload.get("nodes") or [])
            }
            if not self._producer_execution_valid(outcomes, selected_nodes):
                continue
            return event, selection
        return None, None

    def _producer_execution_valid(self, outcomes, selected_nodes: set[str]) -> bool:
        """A producer's outcome WAL proves a complete passed-or-waived run.

        FR-0286 §5: failures bound to a registered known issue are waived --
        they no longer disqualify the producer. The waiver resolution is the
        same single-truth helper the required-green judgment consumes."""
        if not isinstance(outcomes, list):
            return False
        waived = self._waived_nodes(
            [str(item.get("node")) for item in outcomes if isinstance(item, dict)]
        )
        if any(
            not isinstance(item, dict)
            or (
                item.get("status") != "passed"
                and str(item.get("node")) not in waived
            )
            for item in outcomes
        ):
            return False
        outcome_nodes = {str(item.get("node")) for item in outcomes}
        if not selected_nodes or outcome_nodes != selected_nodes:
            return False
        return all(item.get("evidence_id") for item in outcomes)

    def _current_full_f_identity(self, selection):
        """Recompute FULL identity from the live contract/tree."""
        contract = load_contract(self.repo)
        sections = {"unit": contract.unit, "integration": contract.integration, "e2e": contract.e2e}
        if any(section is None for section in sections.values()):
            raise TestSelectError("FULL requires unit, integration, and e2e sections")
        inventory, collect_error = self._collect_all_declared_layers()
        if inventory is None:
            raise TestSelectError(collect_error or "FULL collect failed")
        nodes = sorted(inventory)
        baseline = self._current_baseline_digest()
        commit = git(self.repo, "rev-parse", "HEAD", check=False).stdout.strip()
        tree_stamp = self._dirty_tree_stamp()
        basis = str(selection.payload.get("basis") or "")
        if not basis:
            raise TestSelectError("FULL selection lacks its basis")
        selection_id = make_selection_id(
            nodes=nodes,
            scope="full",
            basis=basis,
            baseline=baseline,
            commit=commit,
            tree_stamp=tree_stamp,
        )
        command = self._declared_full_identity(sections, inventory)
        env = self._gate_environment_identity()
        return {
            "tree": tree_stamp,
            "command": list(command),
            "env": env,
            "selection_id": selection_id,
        }

    def _emit_full_f_judgment(self, cmd, candidate_sha, state=None) -> str:
        """FULL_F reuse judgment emission (IF-VERIFY-002, architecture §1.1):
        judge the stored FULL_F evidence against the current identity
        quadruple and land ``evidence.reused`` (kind=full_f,
        identity_basis) or ``full.executed`` (rerun) -- shared by the walk
        chain (judge_full_f_reuse command) and the park evidence chain
        (§1.0.14), both idempotent per candidate. With no stored FULL_F
        evidence (first verification) no judgment event is emitted and the
        local gates run directly -- the reuse vocabulary never fabricates a
        battery that was never run."""
        producer, selected = self._full_f_producer(candidate_sha)
        evidence = dict(producer.payload) if producer is not None else {}
        try:
            quadruple = self._current_full_f_identity(selected) if selected is not None else {}
        except (ContractError, OSError, TestSelectError, UnicodeError, ValueError):
            quadruple = {}
        producer_seq = producer.seq if producer is not None else -1
        stale_marks = tuple(
            event.type
            for event in self.store.events(self.run_id)
            if event.seq > producer_seq
            and event.type in ("candidate.stale", "evidence.staled")
            and (event.payload.get("candidate_sha") in (None, candidate_sha))
        )
        decision = m_verify.judge_full_f_reuse(candidate_sha, evidence, quadruple, stale_marks)
        if decision.decision == "reuse":
            prior = next(
                (
                    event
                    for event in reversed(list(self.store.events(self.run_id)))
                    if event.type == "evidence.reused"
                    and event.payload.get("kind") == "full_f"
                    and event.payload.get("candidate_sha") == candidate_sha
                    and event.payload.get("source_event_seq") == producer_seq
                ),
                None,
            )
            if prior is not None and json.dumps(
                list(decision.identity_basis), sort_keys=True
            ) == json.dumps(
                prior.payload.get("identity_basis") or [], sort_keys=True
            ):
                return "reused"
            self._emit(
                "evidence.reused",
                {
                    "kind": "full_f",
                    "candidate_sha": candidate_sha,
                    "identity_basis": list(decision.identity_basis),
                    "source_event_seq": producer.seq,
                },
                command_id=cmd.command_id,
            )
            return "reused"
        else:
            if producer is not None and (evidence.get("stale") or stale_marks):
                # AC-FR0268-03: the rejected FULL_F evidence is marked stale
                # on the stream (append-only) so replay shows WHY it was not
                # reused -- a stale rerun is never silent.
                self._emit(
                    "evidence.staled",
                    {
                        "candidate_sha": candidate_sha,
                        "reason": decision.reason,
                        "source": "full_f",
                        "identity_basis": list(decision.identity_basis),
                    },
                    command_id=cmd.command_id,
                )
            ledger = rebuild_ledger(self.store.events(self.run_id))
            try:
                full = self._execute_full_round(
                    cmd,
                    state or self.store.state(self.run_id),
                    "FULL_F",
                    ledger,
                    candidate_sha=candidate_sha,
                    judgment_reason=decision.reason,
                )
            except (ContractError, TestSelectError, TestResultError, OSError, UnicodeError) as exc:
                self._emit(
                    "full.executed",
                    {
                        "candidate_sha": candidate_sha,
                        "passed": False,
                        "full_f_eligible": False,
                        "reason": f"rerun_error:{type(exc).__name__}",
                        "judgment_reason": decision.reason,
                    },
                    command_id=cmd.command_id,
                )
                return "failed"
            if not full.get("passed") or not full.get("full_f_eligible"):
                return "failed"
            return "rerun"

    def _do_judge_full_f_reuse(self, cmd, state, task_id, reconcile):
        """M-VERIFY FULL_F reuse judgment (IF-VERIFY-002, architecture
        §1.1): judge the stored FULL_F evidence against the current
        identity quadruple. Reuse-eligible lands evidence.reused
        (kind=full_f, identity_basis); otherwise full.executed records
        the rerun and the local gates execute it."""
        params = dict(cmd.params or {})
        candidate_sha = params.get("candidate_sha", "")
        result = self._emit_full_f_judgment(cmd, candidate_sha, state)
        if result == "failed":
            return
        self.issue(
            Command(
                kind="run_local_gates",
                params={"candidate_sha": candidate_sha},
            )
        )

    def _repair_rewalk_allowed(self, frozen_sha: str) -> bool:
        """True only when the moved HEAD carries repair provenance
        (§1.0.14 B, SM-01.20).

        The repair channel is machine-identifiable, never guessed from
        content: an OPEN repair round must exist for the frozen candidate
        AND the moved HEAD must carry a ``Tracks-Repair-Round:
        <run_id>/<round>`` trailer matching that open round (a repair
        commit is a new candidate; the trailer is how the runtime tells
        a repair commit from an unrelated drift — SM-01.20). Any
        unmarked HEAD move is drift: candidate.stale, no re-freeze,
        fail-closed (SM-01.17, interfaces §1d 幂等不重冻).
        """
        if self._park_repair_budget_used() == 0:
            return False
        if self._park_candidate_seen(
            ("known_issue.registered", "known_issue.rejected"), frozen_sha
        ):
            return False
        return self._head_carries_repair_trailer(frozen_sha)

    def _head_carries_repair_trailer(self, frozen_sha: str) -> bool:
        """SM-01.20 repair provenance: the moved HEAD carries a
        ``Tracks-Repair-Round: <run_id>/<round>`` trailer matching the
        OPEN repair round bound to the frozen candidate (the
        round_started payload carries candidate_sha; one round per
        candidate within the budget)."""
        round_no = None
        for event in self.store.events(self.run_id):
            if event.type != "repair.round_started":
                continue
            payload = event.payload or {}
            if payload.get("candidate_sha") == frozen_sha:
                round_no = payload.get("round")
        if round_no is None:
            return False
        expected = f"Tracks-Repair-Round: {self.run_id}/{round_no}"
        proc = git(
            self.repo, "log", "-1", "--format=%B", "HEAD", check=False
        )
        body = proc.stdout or ""
        return any(line.strip() == expected for line in body.splitlines())

    def _do_run_local_gates(self, cmd, state, task_id, reconcile):
        """M-VERIFY local-gate chain (IF-VERIFY-003, AC-FR0269-01/02): load +
        validate the host contract (missing/malformed fail closed with
        host_contract.invalid + local_gate.failed and no M-SECURITY entry),
        materialize it (host_contract.materialized bound to the candidate),
        then execute each declared gate in contract order. Any gate failure
        stops the chain blocked -- security is routed only from a fully
        green M-VERIFY (§1.1). The stop itself is a defect finding
        (§1.0.14 B): it opens an in-place repair round on the closed
        classification (contract refusal -> contract delta, failing gate ->
        verification-only), never a stage rollback (AC-FR0286-01)."""
        params = dict(cmd.params or {})
        candidate_sha = params.get("candidate_sha", "")
        contract, _digest = self._run_contract_gates(
            cmd, candidate_sha, state, resume=reconcile
        )
        if contract is not None:
            self.issue(
                Command(
                    kind="observe_ci_runs",
                    params={"candidate_sha": candidate_sha},
                )
            )
            return
        self._park_repair_route(
            cmd,
            candidate_sha,
            self._verify_stop_class(candidate_sha),
            self._park_stop_reason("local_gate.failed"),
        )

    def _verify_stop_class(self, candidate_sha: str) -> str:
        """Closed defect class of the M-VERIFY stop bound to the candidate
        (§1.0.14 B): host_contract.invalid is the contract class, a failing
        local gate the verification-only gate class; anything else stays
        unlabeled (classify_defect fails closed, never a guessed route)."""
        contract_invalid = False
        gate_failed = False
        for event in self.store.events(self.run_id):
            payload = event.payload or {}
            if payload.get("candidate_sha") != candidate_sha:
                continue
            if event.type == "host_contract.invalid":
                contract_invalid = True
            elif event.type == "local_gate.failed":
                gate_failed = True
        if contract_invalid:
            return "contract"
        if gate_failed:
            return "gate"
        return ""

    def _load_or_default_contract(self, cmd, candidate_sha):
        """Load the host-declared canonical contract, or the
        runtime-materialized default when the host declares NONE
        (IF-HOSTCONTRACT-001: the default is written under .tracks/runtime,
        untracked — never dirties the frozen tree). A DECLARED-but-invalid
        contract fail-closes (never silently replaced by the default).
        Returns (contract, digest, source) with source in ("host",
        "runtime_default"), or (None, None, None) after the fail-closed
        block lands on the stream."""
        contract_path = self.repo.joinpath(*CANONICAL_CONTRACT_RELPATH)
        declared = None
        try:
            raw = contract_path.read_bytes()
        except OSError:
            raw = None
        if raw is not None:
            try:
                declared = tomllib.loads(raw.decode("utf-8")).get("host-contract")
            except (UnicodeDecodeError, tomllib.TOMLDecodeError):
                declared = "malformed"  # declared but unreadable: fail closed
        if declared is not None:
            # The host DECLARED a contract (valid or malformed): the
            # canonical load outcome is authoritative — materialization
            # never overrides a declared table.
            try:
                contract = load_host_contract(contract_path)
                return (
                    contract,
                    hashlib.sha256(raw).hexdigest(),
                    "host",
                )
            except (OSError, ValueError) as err:
                self._fail_verify_block(
                    cmd, candidate_sha, "missing_contract", str(err)
                )
                return None, None, None
        try:
            runtime = paths.runtime_dir(paths.tracks_home(self.repo))
            runtime.mkdir(parents=True, exist_ok=True)
            path = runtime / "materialized-host-contract.toml"
            path.write_text(_DEFAULT_HOST_CONTRACT_TOML, encoding="utf-8")
            contract = load_host_contract(path)
        except (OSError, ValueError) as err:
            self._fail_verify_block(cmd, candidate_sha, "missing_contract", str(err))
            return None, None, None
        digest = hashlib.sha256(_DEFAULT_HOST_CONTRACT_TOML.encode("utf-8")).hexdigest()
        return contract, digest, "runtime_default"

    def _run_contract_gates(self, cmd, candidate_sha: str, state, resume: bool = False):
        """Shared contract-gate face of M-VERIFY (IF-VERIFY-003): load +
        validate the host contract (the runtime-materialized default when
        the host declares none — IF-HOSTCONTRACT-001), materialize it and
        execute every declared local gate in order. Malformed contracts
        and any failing gate fail closed (host_contract.invalid /
        local_gate.failed; the chain stays blocked, §1.0.4 -- never a
        guessed command). Returns (contract, contract_digest) only when
        every gate is green, else (None, None). resume=True (park evidence
        chain) reuses existing passed-gate evidence for the same candidate
        instead of re-running it (SM-01.17 idempotency)."""
        contract, contract_digest, source = self._load_or_default_contract(
            cmd, candidate_sha
        )
        if contract is None:
            return None, None
        errors = validate_host_contract(contract, self.repo)
        if errors:
            self._fail_verify_block(cmd, candidate_sha, "malformed", "; ".join(errors))
            return None, None
        if resume and has_complete_passed_gates(
            self.store.events(self.run_id), candidate_sha, contract_digest, contract
        ):
            return contract, contract_digest
        contract_path = (
            self.repo.joinpath(*CANONICAL_CONTRACT_RELPATH)
            if source == "host"
            else paths.runtime_dir(paths.tracks_home(self.repo))
            / "materialized-host-contract.toml"
        )
        self._emit(
            "host_contract.materialized",
            {
                "candidate_sha": candidate_sha,
                "contract_path": str(contract_path),
                "contract_digest": contract_digest,
                "version": contract.contract_version,
                "source": source,
                "language": contract.language,
                "toolchain": contract.toolchain,
            },
            command_id=cmd.command_id,
        )
        if not self._execute_verify_gates(
            cmd, candidate_sha, contract_digest, contract, state
        ):
            self._emit_host_contract_failure(cmd, candidate_sha, contract_digest)
            return None, None
        return contract, contract_digest

    def _execute_verify_gates(self, cmd, candidate_sha, contract_digest, contract, state):
        """Run the contract's local gates in order (AC-FR0269-01), emitting
        local_gate.passed|failed per gate bound to the candidate. Returns
        True when every gate passed; any failure or unknown stops the chain
        blocked (fail closed, §1.0.4 -- never a guessed command).

        Source-based gates resolve their executable through the declared
        single truth: ``source=guard_registry`` quality gates resolve via
        ``lint_check_command`` (the project's declared [lint] command);
        ``source=version_decl`` gates are executed natively by the Runtime
        (tag-template derivation + remote absence probe, see
        :meth:`_execute_version_decl_gate`). A registry gate that resolves to
        nothing is skipped too (gates skip, never guess a host toolchain
        invocation — IF-HOSTCONTRACT-001 NFR-0147). Inline-command gates
        render and run verbatim.
        """
        scope = self._release_version_facts(state)
        scope["candidate_sha"] = candidate_sha
        all_passed = True
        for ordinal, gate in enumerate(contract.local_gates):
            command = gate.command
            if gate.source == "guard_registry":
                resolved_lint = lint_check_command(self.repo)
                if not resolved_lint:
                    continue  # skip, never guess
                command = resolved_lint
            elif gate.source == "version_decl":
                if not self._execute_version_decl_gate(
                    cmd, candidate_sha, contract_digest, contract, state, gate, ordinal
                ):
                    all_passed = False
                continue
            try:
                result = execute_gate(
                    replace(gate, command=command), self.repo, scope
                )
            except Exception as err:  # noqa: BLE001 -- fail closed per gate
                self._emit(
                    "local_gate.failed",
                    {
                        "kind": gate.kind,
                        "gate_identity": gate_identity(gate.kind, ordinal),
                        "candidate_sha": candidate_sha,
                        "contract_digest": contract_digest,
                        "command_echo": [],
                        "normalized_result": normalized_result_payload(
                            NormalizedGateResult(
                                gate_id=gate.kind,
                                result_version=1,
                                status="failed",
                                exit_code=None,
                                summary={"error": str(err)},
                            )
                        ),
                        "reason": "unknown",
                        "detail": str(err),
                    },
                    command_id=cmd.command_id,
                )
                all_passed = False
                continue
            payload = {
                "kind": gate.kind,
                "gate_identity": gate_identity(gate.kind, ordinal),
                "candidate_sha": candidate_sha,
                "contract_digest": contract_digest,
                "command_echo": list(result.command_echo or ()),
                "normalized_result": normalized_result_payload(result),
                "status": result.status,
                "exit_code": result.exit_code,
                "summary": result.summary,
            }
            if result.status == "passed":
                self._emit(
                    "local_gate.passed", payload, command_id=cmd.command_id
                )
            else:
                payload["reason"] = (
                    "malformed" if result.status == "malformed" else "failed"
                )
                self._emit(
                    "local_gate.failed", payload, command_id=cmd.command_id
                )
                all_passed = False
        return all_passed

    def _execute_version_decl_gate(
        self, cmd, candidate_sha, contract_digest, contract, state, gate, ordinal
    ) -> bool:
        """Native ``source=version_decl`` local gate (FR-0269, NFR-0147).

        The gate has no executable command (tracks/reference contracts declare
        none): the Runtime derives this run's operation plan from the declared
        ``[host-contract.version_scheme]`` templates + version facts and
        requires every ``tag:``/``release:`` step target to be exactly the
        journey's template rendering — a literal or cross-template target is
        ``version_mismatch``, never silently published. Each derived tag is
        then probed with ``git ls-remote``: a present tag is
        ``tag_already_exists`` and a configured-but-unreachable remote is
        ``remote_unavailable`` (fail closed, never a guessed pass). A
        remote-less local demo records ``attention.required`` and passes with
        an explicit ``remote_check=skipped_no_remote`` skip. The ``{n}``
        census input is recorded on the payload. The verdict event
        (``local_gate.passed``/``local_gate.failed``) is bound to the
        candidate like every other declared gate.
        """
        identity = gate_identity(gate.kind, ordinal)
        table = _contract_table(contract)
        scheme = table.get("version_scheme") or {}
        journey = _journey(contract, self._release_journey(state, cmd))
        base_facts = self._release_version_facts(state)
        needs_n = bool(journey) and operation_plan_needs_n(table, journey)
        census = {"requested": needs_n, "n": None, "error": None}
        if needs_n and "n" not in base_facts:
            n, error = remote_patch_n(
                self.repo, str(scheme.get("patch_line") or ""), base_facts
            )
            census = {"requested": True, "n": n, "error": error}
            if error is not None:
                return self._emit_version_decl_verdict(
                    cmd,
                    candidate_sha,
                    contract_digest,
                    gate,
                    identity,
                    journey=journey,
                    derived_tags=[],
                    census=census,
                    remote_check={"status": "unavailable", "tags": []},
                    reason="remote_unavailable",
                    detail=f"remote patch-tag census failed: {error}",
                    command_echo=["version_decl", journey or "feature"],
                )
            if n is not None:
                base_facts = {**base_facts, "n": n}
        facts = _version_facts(contract, base_facts)
        template_name = _JOURNEY_VERSION_TEMPLATE.get(journey or "")
        expected = str(facts.get(template_name) or "") if template_name else ""
        plan = (
            build_operation_plan(
                table,
                journey,
                facts,
                active_release_branch=active_release_branch(self.repo, facts),
            )
            if journey
            else {"steps": []}
        )
        mismatches: list[dict] = []
        derived: list[str] = []
        for step in plan.get("steps") or ():
            kind, _sep, target = str(step).partition(":")
            if kind not in _VERSION_STEP_KINDS:
                continue
            if not expected or target != expected:
                mismatches.append(
                    {
                        "step": str(step),
                        "kind": kind,
                        "target": target,
                        "expected": expected,
                    }
                )
            elif target not in derived:
                derived.append(target)
        command_echo = ["version_decl", journey or "feature", *derived]
        if mismatches:
            return self._emit_version_decl_verdict(
                cmd,
                candidate_sha,
                contract_digest,
                gate,
                identity,
                journey=journey,
                derived_tags=derived,
                census=census,
                remote_check={"status": "not_checked", "tags": derived},
                reason="version_mismatch",
                detail=(
                    "tag/release targets are not derived from the journey's "
                    "version_scheme template"
                ),
                command_echo=command_echo,
                extra={"mismatches": mismatches},
            )
        if not derived:
            return self._emit_version_decl_verdict(
                cmd,
                candidate_sha,
                contract_digest,
                gate,
                identity,
                journey=journey,
                derived_tags=[],
                census=census,
                remote_check={"status": "not_applicable", "tags": []},
                reason=None,
                detail="",
                command_echo=command_echo,
            )
        remote = git(self.repo, "config", "--get", "remote.origin.url", check=False)
        if remote.returncode != 0 or not remote.stdout.strip():
            self._emit(
                "attention.required",
                {
                    "area": "version_gate",
                    "reason": "remote_unavailable",
                    "stage": "M-VERIFY",
                    "candidate_sha": candidate_sha,
                    "detail": (
                        "no origin remote configured; derived tag absence is "
                        "not verifiable"
                    ),
                    "next": "configure origin; trac run --resume re-checks the gate",
                },
                command_id=cmd.command_id,
            )
            return self._emit_version_decl_verdict(
                cmd,
                candidate_sha,
                contract_digest,
                gate,
                identity,
                journey=journey,
                derived_tags=derived,
                census=census,
                remote_check={"status": "skipped_no_remote", "tags": derived},
                reason=None,
                detail="",
                command_echo=command_echo,
            )
        for tag in derived:
            probe = git(
                self.repo,
                "ls-remote",
                "--tags",
                "origin",
                f"refs/tags/{tag}",
                check=False,
            )
            if probe.returncode != 0:
                return self._emit_version_decl_verdict(
                    cmd,
                    candidate_sha,
                    contract_digest,
                    gate,
                    identity,
                    journey=journey,
                    derived_tags=derived,
                    census=census,
                    remote_check={
                        "status": "unavailable",
                        "tags": derived,
                        "failed_tag": tag,
                    },
                    reason="remote_unavailable",
                    detail=f"git ls-remote failed for refs/tags/{tag}",
                    command_echo=command_echo,
                )
            if f"refs/tags/{tag}" in probe.stdout:
                return self._emit_version_decl_verdict(
                    cmd,
                    candidate_sha,
                    contract_digest,
                    gate,
                    identity,
                    journey=journey,
                    derived_tags=derived,
                    census=census,
                    remote_check={
                        "status": "exists",
                        "tags": derived,
                        "existing": tag,
                    },
                    reason="tag_already_exists",
                    detail=f"remote already carries refs/tags/{tag}",
                    command_echo=command_echo,
                )
        return self._emit_version_decl_verdict(
            cmd,
            candidate_sha,
            contract_digest,
            gate,
            identity,
            journey=journey,
            derived_tags=derived,
            census=census,
            remote_check={"status": "verified_absent", "tags": derived},
            reason=None,
            detail="",
            command_echo=command_echo,
        )

    def _emit_version_decl_verdict(
        self,
        cmd,
        candidate_sha,
        contract_digest,
        gate,
        identity,
        *,
        journey,
        derived_tags,
        census,
        remote_check,
        reason,
        detail,
        command_echo,
        extra=None,
    ) -> bool:
        """Emit the candidate-bound verdict of the native version gate.

        The payload mirrors the command-gate shape (normalized
        ``tracks-gate-result`` v1 + non-empty command_echo) so the resume
        completeness judge and the release evidence digests consume it like
        every other declared gate; ``journey``/``derived_tags``/
        ``remote_patch_n``/``remote_check`` carry the derivation audit.
        """
        status = "failed" if reason else "passed"
        exit_code = None if reason else 0
        summary = {
            "journey": journey,
            "derived_tags": list(derived_tags),
            "remote_check": dict(remote_check),
            "remote_patch_n": dict(census),
        }
        if reason:
            summary["reason"] = reason
            summary["detail"] = detail
        result = NormalizedGateResult(
            gate_id=gate.kind,
            result_version=1,
            status=status,
            exit_code=exit_code,
            summary=summary,
            command_echo=tuple(command_echo),
        )
        payload = {
            "kind": gate.kind,
            "gate_identity": identity,
            "candidate_sha": candidate_sha,
            "contract_digest": contract_digest,
            "command_echo": list(command_echo),
            "normalized_result": normalized_result_payload(result),
            "status": status,
            "exit_code": exit_code,
            "summary": summary,
            "journey": journey,
            "derived_tags": list(derived_tags),
            "remote_patch_n": dict(census),
            "remote_check": dict(remote_check),
        }
        if extra:
            payload.update(extra)
        if reason:
            payload["reason"] = reason
            payload["detail"] = detail
            self._emit("local_gate.failed", payload, command_id=cmd.command_id)
            return False
        self._emit("local_gate.passed", payload, command_id=cmd.command_id)
        return True

    def _emit_host_contract_failure(self, cmd, candidate_sha, contract_digest):
        """FR-0281-03 machine evidence + attention route for a contract whose
        declared gate did not pass: ``host_contract.failed`` re-projects the
        machine evidence captured on the bound ``local_gate.failed`` event
        (exit code, stdout/stderr tails, normalized result);
        ``attention.required`` (area host_contract) renders
        ``needs_attention=host_contract:...`` so the status surface names the
        contract revision route (never a guessed command)."""
        failure = next(
            (
                event
                for event in reversed(list(self.store.events(self.run_id)))
                if event.type == "local_gate.failed"
                and (event.payload or {}).get("candidate_sha") == candidate_sha
                and (event.payload or {}).get("contract_digest") == contract_digest
            ),
            None,
        )
        payload = dict(failure.payload or {}) if failure is not None else {}
        normalized = payload.get("normalized_result") or {}
        summary = payload.get("summary") or normalized.get("summary") or {}
        reason = (
            "malformed_result"
            if payload.get("reason") == "malformed" or normalized.get("status") == "malformed"
            else "gate_failed"
        )
        kind = str(payload.get("kind") or "")
        self._emit(
            "host_contract.failed",
            {
                "candidate_sha": candidate_sha,
                "contract_digest": contract_digest,
                "kind": kind,
                "reason": reason,
                "exit_code": payload.get("exit_code"),
                "stdout_tail": str(summary.get("stdout", ""))[-500:],
                "stderr_tail": str(summary.get("stderr", ""))[-500:],
                "normalized_result": payload.get("normalized_result"),
            },
            command_id=cmd.command_id,
        )
        self._emit(
            "attention.required",
            {
                "area": "host_contract",
                "reason": reason,
                "stage": "M-VERIFY",
                "detail": f"declared gate {kind or 'unknown'} did not pass",
                "next": "revise the host-contract, then trac run --resume",
            },
            command_id=cmd.command_id,
        )

    def _do_observe_ci_runs(self, cmd, state, task_id, reconcile):
        """M-VERIFY CI readback (IF-VERIFY-004, AC-FR0270-01..03): API
        readback of the required-CI run bound to the frozen candidate;
        ``ci.run_observed`` carries the repo/workflow/run/head quadruple
        binding (api_verified). Missing credentials, CI config and network
        errors land attention.required (never a silent pass); a
        mismatch/missing/stale binding stops the chain blocked; a bound run
        hands to the Prism same-candidate final review (FR-0270: bound
        evidence is kept, a resumed run never repeats the API call)."""
        params = dict(cmd.params or {})
        candidate_sha = params.get("candidate_sha", "")
        contract, _digest, _source = self._load_or_default_contract(
            cmd, candidate_sha
        )
        if contract is None:
            return
        ci = contract.ci or {}
        if reconcile:
            previous = self._latest_event("ci.run_observed")
            if previous is not None:
                payload = previous.payload or {}
                configured_repo = os.environ.get(
                    str(ci.get("repo_env", "")), ""
                ).strip()
                configured_workflow = str(ci.get("workflow", "")).strip()
                observed_workflow = str(payload.get("workflow", "")).strip()
                observed_path = str(payload.get("workflow_path", "")).strip()
                observed_id = str(payload.get("workflow_id", "")).strip()
                workflow_match = (
                    observed_workflow == configured_workflow
                    or observed_id == configured_workflow
                    or observed_path == configured_workflow
                    or (
                        bool(configured_workflow)
                        and Path(observed_path).name
                        == Path(configured_workflow).name
                    )
                )
                command_match = previous.command_id == cmd.command_id
                checks = payload.get("checks") or {}
                required_checks = list(ci.get("required_checks", []))
                reusable = (
                    command_match
                    and payload.get("candidate_sha") == candidate_sha
                    and payload.get("head_sha") == candidate_sha
                    and payload.get("status") == "passed"
                    and payload.get("api_verified") is True
                    and not payload.get("stale")
                    and bool(configured_repo)
                    and payload.get("repo") == configured_repo
                    and isinstance(payload.get("run_id"), int)
                    and not isinstance(payload.get("run_id"), bool)
                    and workflow_match
                    and payload.get("required_checks", required_checks)
                    == required_checks
                    and all(checks.get(name) == "success" for name in required_checks)
                )
                if reusable:
                    return
        run_payload = self._readback_ci_binding(cmd, candidate_sha, ci)
        if run_payload is None:
            return
        self._emit("ci.run_observed", run_payload, command_id=cmd.command_id)
        assignment = m_verify.build_prism_final_review_assignment(
            candidate_sha, self._verify_evidence_digests()
        )
        self.issue(
            Command(
                kind="dispatch_agent",
                params={
                    "role": "prism",
                    "substate": "VERIFY_FINAL",
                    "stage": "M-VERIFY",
                    "assignment": dict(assignment),
                    "objective": (
                        "same-candidate final review of the frozen candidate "
                        "(IF-VERIFY-005)"
                    ),
                    **assignment,
                },
            )
        )

    def _readback_ci_binding(self, cmd, candidate_sha, ci):
        """API readback + binding judge (IF-VERIFY-004, AC-FR0270-01..03).
        Returns the bound ci.run_observed payload, or None with the block
        already on the stream: missing CI config/credentials and network
        errors land attention.required (never a silent pass); a
        mismatch/missing/stale binding stops the chain blocked.

        The readback is the real GitHub-API face (stand-in base honored via
        TRAC_GITHUB_API_BASE) for every agent channel: the agent backend
        selection scopes who writes, never what the Runtime observes.
        A fake agent therefore cannot synthesize api_verified evidence.
        """
        repo_id = os.environ.get(str(ci.get("repo_env", "")), "")
        workflow = str(ci.get("workflow", ""))
        if not repo_id or not workflow:
            # AC-FR0270-03 locked vocabulary (missing_token|network_error):
            # an undeclared/unresolved CI target is the credentials face,
            # never a guessed configuration class.
            self._emit(
                "attention.required",
                {
                    "area": "ci_readback",
                    "reason": "missing_token",
                    "stage": "M-VERIFY",
                    "candidate_sha": candidate_sha,
                    "detail": "[host-contract.ci] repo_env/workflow unresolved",
                    "next": (
                        "set the CI target/token; trac run retries in place"
                    ),
                },
                command_id=cmd.command_id,
            )
            return None
        try:
            observed = readback_ci_run(repo_id, workflow, candidate_sha)
        except GithubIssuesError as err:
            attention_reason = err.classification
            self._emit(
                "attention.required",
                {
                    "area": "ci_readback",
                    "reason": attention_reason,
                    "stage": "M-VERIFY",
                    "candidate_sha": candidate_sha,
                    "detail": str(err),
                    "next": (
                        "set the CI token; trac run retries in place"
                        if attention_reason == "missing_token"
                        else "resolve the CI API error; trac run retries in place"
                    ),
                },
                command_id=cmd.command_id,
            )
            return None
        except (OSError, ValueError) as err:
            self._emit(
                "attention.required",
                {
                    "area": "ci_readback",
                    "reason": "network_error",
                    "stage": "M-VERIFY",
                    "candidate_sha": candidate_sha,
                    "detail": str(err),
                    "next": "retry when the CI API is reachable",
                },
                command_id=cmd.command_id,
            )
            return None
        binding = judge_ci_binding(
            observed, candidate_sha, ci.get("required_checks", [])
        )
        if binding.get("status") != "passed":
            # AC-FR0270-02 fail-closed event face: a binding failure lands
            # ci.run_observed(status=failed) with its closed reason
            # (mismatch|missing|stale|failed_conclusion|check_failed) so the
            # evidence stream carries WHAT failed and status can render
            # ci=mismatch|missing|stale — the readback happened, only the
            # binding refused. No credentials/no target never reaches here
            # (attention.required above, no API call, no event).
            self._emit(
                "ci.run_observed",
                {
                    "candidate_sha": candidate_sha,
                    "repo": repo_id,
                    "workflow": workflow,
                    "workflow_name": observed.get("workflow_name"),
                    "workflow_path": observed.get("workflow_path"),
                    "workflow_id": observed.get("workflow_id"),
                    "run_id": observed.get("run_id"),
                    "head_sha": observed.get("head_sha"),
                    "conclusion": observed.get("conclusion"),
                    "checks": observed.get("checks") or {},
                    "required_checks": list(ci.get("required_checks", [])),
                    "status": "failed",
                    "reason": str(binding.get("reason", "mismatch")),
                    "api_verified": False,
                },
                command_id=cmd.command_id,
            )
            self._emit(
                "attention.required",
                {
                    "area": "ci_readback",
                    "reason": "ci_binding_blocked",
                    "stage": "M-VERIFY",
                    "candidate_sha": candidate_sha,
                    "detail": binding.get("reason", "mismatch"),
                    "next": "re-run CI on the frozen candidate; trac run retries",
                },
                command_id=cmd.command_id,
            )
            return None
        return {
            "candidate_sha": candidate_sha,
            "repo": repo_id,
            "workflow": workflow,
            "workflow_name": observed.get("workflow_name"),
            "workflow_path": observed.get("workflow_path"),
            "workflow_id": observed.get("workflow_id"),
            "run_id": observed.get("run_id"),
            "head_sha": observed.get("head_sha"),
            "conclusion": observed.get("conclusion"),
            "checks": observed.get("checks") or {},
            "required_checks": list(ci.get("required_checks", [])),
            "status": "passed",
            "reason": "bound",
            "api_verified": binding.get("api_verified") is True,
        }

    def _verify_evidence_digests(self) -> dict:
        """Evidence manifest for the final-review envelope (IF-VERIFY-005):
        the contract digest of every passed local gate, keyed by gate kind."""
        digests = {}
        for event in self.store.events(self.run_id):
            if event.type == "local_gate.passed":
                gate_payload = event.payload or {}
                digests[str(gate_payload.get("kind"))] = gate_payload.get(
                    "contract_digest"
                )
        return digests

    def _advance_verify_chain(self, cmd, candidate_sha):
        """All M-VERIFY gates green (§1.1): exit M-VERIFY, enter M-SECURITY
        and run the security assessment; a passing assessment exits to
        M-RELEASE where the kernel release routing takes over (preview ->
        release decision -> publish). A failed assessment stops blocked with
        its repair_route on the stream (§1.0.14 classification; never an
        automatic rollback)."""
        self._emit("stage.exited", {"stage": "M-VERIFY"}, command_id=cmd.command_id)
        self._emit(
            "stage.entered", {"stage": "M-SECURITY"}, command_id=cmd.command_id
        )
        self.issue(
            Command(kind="assess_security", params={"candidate_sha": candidate_sha})
        )
        assessed = self._latest_event("security.assessed")
        if assessed is None or (assessed.payload or {}).get("status") not in (
            "pass",
            "passed",
        ):
            return
        self._emit(
            "stage.exited", {"stage": "M-SECURITY"}, command_id=cmd.command_id
        )
        self._emit(
            "stage.entered", {"stage": "M-RELEASE"}, command_id=cmd.command_id
        )
        self._release_preview(cmd, candidate_sha)

    def _release_preview(self, cmd, candidate_sha: str) -> None:
        """M-RELEASE entry preview (SM-01.6, FR-0284/FR-0286): the fully
        green chain aggregates the verified evidence into the
        content-addressed release preview and lands release.previewed
        (known issues listed, awaiting the Human release decision). A
        contract reload refusal fails closed (attention.required, never a
        guessed preview digest); the materialized default contract feeds
        its digest when the host declared none."""
        contract, digest, _source = self._load_or_default_contract(
            cmd, candidate_sha
        )
        if contract is None:
            return
        self._park_preview(cmd, candidate_sha, contract, digest)

    def _load_authoritative_issue_map(self) -> dict:
        """The authoritative issue map (IF-ISSUE-001): the closer vocabulary
        lives at .tracks/runtime/issue-map.json; missing/corrupt -> empty
        (nothing closable, never guessed)."""
        path = self.repo / ".tracks" / "runtime" / "issue-map.json"
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _do_close_milestone(self, cmd, state, task_id, reconcile):
        """Consume Command(close_milestone) (IF-MILESTONE-001, §1i): itemized,
        idempotent closing tail.

        Sub-steps: trace_closed -> issue/project closed -> sealed ->
        refs.cleaned -> run.completed. Each sub-step uses its OWN event as the
        completion witness (from this command or any prior one), so an
        interrupted tail is continued by the next close_milestone command
        without re-emitting a completed step, and already-persisted external
        effects are never repeated. The M-MILESTONE decider re-issues this
        command until run.completed flips the run to completed.
        """
        events = list(self.store.events(self.run_id))
        params = dict(cmd.params or {})
        candidate_sha = str(params.get("candidate_sha") or "")
        if not candidate_sha:
            for event in reversed(events):
                if event.type == "candidate.frozen":
                    candidate_sha = str((event.payload or {}).get("candidate_sha") or "")
                    break
        trace = self._milestone_trace(cmd, task_id, events, candidate_sha)
        if not trace.get("trace_digest"):
            return
        closed_numbers = [
            (event.payload or {}).get("issue_number")
            for event in events
            if event.type == "issue.closed"
        ]
        issue_map = self._load_authoritative_issue_map()
        known_issues = [
            event.payload or {} for event in events if event.type == "known_issue.registered"
        ]
        self._close_milestone_issues(
            cmd, task_id, trace, issue_map, known_issues, closed_numbers
        )
        project = self._close_milestone_project(cmd, task_id, trace, params, events)
        self._emit_milestone_closed(cmd, task_id, trace, project, events)
        if not self._seal_milestone(cmd, task_id, trace, events, candidate_sha):
            return
        if not self._clean_milestone_refs(cmd, task_id, events):
            return
        self._complete_milestone(cmd, task_id, trace, events)

    def _milestone_trace(self, cmd, task_id, events, candidate_sha):
        """The trace close sub-step: reuse any landed milestone.trace_closed,
        else build the candidate-bound §1i trace and land it."""
        for event in reversed(events):
            if event.type == "milestone.trace_closed":
                return dict(event.payload or {})
        trace = build_release_trace(
            [
                {"type": event.type, "payload": dict(event.payload or {}), "seq": event.seq}
                for event in events
            ],
            candidate_sha,
        )
        self._emit(
            "milestone.trace_closed",
            dict(trace),
            command_id=cmd.command_id,
            task_id=task_id,
        )
        return trace

    def _close_milestone_issues(
        self, cmd, task_id, trace, issue_map, known_issues, closed_numbers
    ):
        """Close authoritative issues for real; audit every non-authoritative
        identity as skipped. `issue.closed` events are the per-issue witness:
        a number that already has an event (closed or skipped) is settled and
        never re-processed."""
        done = {str(number) for number in closed_numbers if number is not None}
        pending_map = {
            item_id: mapping
            for item_id, mapping in (issue_map or {}).items()
            if isinstance(mapping, dict)
            and str(mapping.get("issue_number")) not in done
        }
        for entry in close_issues_with_comment(
            self.repo, pending_map, trace, closer=self._real_issue_closer(cmd, task_id, trace)
        ):
            if str(entry.get("issue_number")) in done:
                continue
            done.add(str(entry.get("issue_number")))
            self._emit("issue.closed", dict(entry), command_id=cmd.command_id, task_id=task_id)
        for entry in skipped_issue_entries(issue_map, known_issues, trace, done):
            done.add(str(entry.get("issue_number")))
            self._emit("issue.closed", dict(entry), command_id=cmd.command_id, task_id=task_id)

    def _real_issue_closer(self, cmd, task_id, trace):
        """The irreversible issue close seam: comment + close + API readback.

        A verified remote close yields state=closed; any classified failure
        yields an audited state=skipped (reason) plus attention.required and
        NEVER claims a close (FR-0284-01)."""

        def closer(mapping, entry):
            repo_id = str(mapping.get("repo") or os.environ.get("TRAC_GITHUB_REPO", ""))
            number = mapping.get("issue_number")
            comment = release_trace_comment(trace)
            try:
                result = close_issue(repo_id, int(number), comment)
            except GithubIssuesError as exc:
                result = {
                    "state": "",
                    "api_verified": False,
                    "error": f"{exc.classification}: {exc}",
                }
            except (TypeError, ValueError) as exc:
                result = {
                    "state": "",
                    "api_verified": False,
                    "error": f"malformed_issue_number: {exc}",
                }
            if result.get("api_verified") and result.get("state") == "closed":
                return {
                    "state": "closed",
                    "comment": comment,
                    "comment_ref": (
                        f"comment:{number}:"
                        f"{result.get('comment_id') or trace.get('trace_digest', '')}"
                    ),
                    "remote_state": result.get("state"),
                    "api_verified": True,
                }
            reason = str(result.get("error") or "close_unconfirmed")
            self._emit(
                "attention.required",
                {
                    "area": "issue_close",
                    "reason": reason,
                    "issue_number": number,
                    "next": "repair GitHub access and trac run --resume",
                },
                command_id=cmd.command_id,
                task_id=task_id,
            )
            return {
                "state": "skipped",
                "reason": reason,
                "api_verified": False,
            }

        return closer

    def _close_milestone_project(self, cmd, task_id, trace, params, events):
        """Close the Project/milestone when the host declared an authoritative
        tracker + credentials; otherwise land an audited skipped identity."""
        if any(event.type == "project.closed" for event in events):
            return next(
                dict(event.payload or {})
                for event in reversed(events)
                if event.type == "project.closed"
            )
        tracker = params.get("tracker") if isinstance(params.get("tracker"), dict) else {}
        repo_id = str(tracker.get("repo") or os.environ.get("TRAC_GITHUB_REPO", ""))
        milestone = tracker.get("milestone")
        if repo_id and milestone not in (None, "") and os.environ.get("GITHUB_TOKEN"):
            def closer(_tracker, entry):
                try:
                    result = close_project_milestone_api(
                        repo_id, tracker.get("project", ""), milestone
                    )
                except GithubIssuesError as exc:
                    result = {
                        "state": "",
                        "api_verified": False,
                        "error": f"{exc.classification}: {exc}",
                    }
                if result.get("api_verified") and result.get("state") == "closed":
                    return {"state": "closed", "remote_state": "closed", "api_verified": True}
                reason = str(result.get("error") or "close_unconfirmed")
                self._emit(
                    "attention.required",
                    {
                        "area": "project_close",
                        "reason": reason,
                        "next": "repair GitHub access and trac run --resume",
                    },
                    command_id=cmd.command_id,
                    task_id=task_id,
                )
                return {"state": "skipped", "reason": reason, "api_verified": False}

            entry = close_project_milestone(self.repo, tracker, trace, closer=closer)
        else:
            entry = close_project_milestone(self.repo, tracker, trace)
            entry.update({"state": "skipped", "reason": "not_authoritative"})
        self._emit("project.closed", dict(entry), command_id=cmd.command_id, task_id=task_id)
        return entry

    def _emit_milestone_closed(self, cmd, task_id, trace, project, events):
        if any(event.type == "milestone.closed" for event in events):
            return
        self._emit(
            "milestone.closed",
            {
                "milestone": (project or {}).get("milestone", ""),
                "state": (project or {}).get("state", "closed"),
                "trace_digest": trace.get("trace_digest", ""),
            },
            command_id=cmd.command_id,
            task_id=task_id,
        )

    def _seal_milestone(self, cmd, task_id, trace, events, candidate_sha) -> bool:
        if any(
            event.type == "milestone.sealed"
            and (event.payload or {}).get("readonly") is True
            for event in events
        ):
            return True
        sealed = seal_evidence_readonly(
            self.repo, candidate_sha, trace=trace, events=events
        )
        if sealed.get("readonly") is not True:
            # Emit the attention once; a repeated failure must leave the loop
            # with no progress event so the stall breaker (B86/B88) stops a
            # permanently unwritable blob store instead of spinning forever.
            if not any(
                event.type == "attention.required"
                and (event.payload or {}).get("area") == "milestone_seal"
                for event in events
            ):
                self._emit(
                    "attention.required",
                    {
                        "area": "milestone_seal",
                        "reason": str(sealed.get("error") or "seal_failed"),
                        "seal_blob": str(sealed.get("seal_blob") or ""),
                        "next": "fix the runtime blob store and trac run",
                    },
                    command_id=cmd.command_id,
                    task_id=task_id,
                )
            return False
        self._emit(
            "milestone.sealed",
            dict(sealed),
            command_id=cmd.command_id,
            task_id=task_id,
        )
        return True

    def _clean_milestone_refs(self, cmd, task_id, events) -> bool:
        if any(
            event.type == "refs.cleaned"
            and (event.payload or {}).get("remaining") == 0
            for event in events
        ):
            return True
        cleaned = clean_temp_refs(self.repo, self.run_id)
        prior_remaining = next(
            (
                (event.payload or {}).get("remaining_refs")
                for event in reversed(events)
                if event.type == "refs.cleaned"
            ),
            None,
        )
        if cleaned.get("remaining") != 0 and prior_remaining == cleaned.get("remaining_refs"):
            # Same stuck refs as the previous attempt: emit nothing so the
            # stall breaker stops the retry loop instead of appending
            # duplicate audit events forever.
            return False
        self._emit("refs.cleaned", dict(cleaned), command_id=cmd.command_id, task_id=task_id)
        if cleaned.get("remaining") != 0:
            self._emit(
                "attention.required",
                {
                    "area": "milestone_refs",
                    "reason": "refs_remaining",
                    "remaining_refs": cleaned.get("remaining_refs", []),
                    "next": "inspect refs/trac/tmp owners and trac run",
                },
                command_id=cmd.command_id,
                task_id=task_id,
            )
            return False
        return True

    def _complete_milestone(self, cmd, task_id, trace, events):
        if any(event.type == "run.completed" for event in events):
            return
        # §1.0.7 terminal face: the closing tail completes with
        # run.completed(terminal_state=released, release_tag).
        self._emit(
            "run.completed",
            {
                "terminal_state": "released",
                "release_tag": trace.get("release_tag", ""),
                "trace_digest": trace.get("trace_digest", ""),
            },
            command_id=cmd.command_id,
            task_id=task_id,
        )

    # -- v0.7 Phase 0 pre-gate handler (architecture §1.0.2/§1.1, T-015) -------

    def _emit_phase0_blocked(self, cmd, reason: str, detail: str, recoverable: bool):
        """Emit ``phase0.blocked`` (§1c-5): the run parks for Human repair."""
        return self._emit(
            "phase0.blocked",
            {"reason": reason, "detail": detail, "recoverable": recoverable},
            command_id=cmd.command_id,
        )

    def _phase0_guard_violations(self) -> tuple[list[str], list[str], bool]:
        """Guard hardening input (§1d; registry surface IF-GUARD-001/002).

        Returns ``(violations, guard_refs, registry_present)``. A PRESENT but
        unloadable §4.2 registry yields violations (fail closed -- the run
        parks at phase0.blocked rather than proceeding under a substituted
        registry, interfaces §1c/§1e fail-closed evidence authenticity). An
        ABSENT §4.2 registry means no guard/parity evidence exists on this
        host: the Phase 0 pre-gate must park (``registry_present=False``)
        rather than fabricate a passed hardening or a seal.

        No packaged-asset substitution is allowed to mask an unloadable host
        registry into a pass (Prism R9-01): the host seed must itself carry a
        valid §4.2 block ("demo asset 同源"); a seed that does not is a
        test/seed defect (SHIELD_FIX), never a silent implementation pass.
        """
        arch = paths.projects_dir(self.store.home) / self.version / "architecture.md"
        if not arch.is_file():
            return [], [], False
        from tracks.executor.guard_registry import load_guard_registry

        try:
            load_guard_registry(arch)
        except (ValueError, OSError, UnicodeError) as exc:
            return [
                f"architecture.md §4.2 registry block failed to load: {exc}"
            ], [str(arch)], True
        return [], [str(arch)], True

    def _repair_phase0_gap(
        self, cmd, gap, baseline_version: str, node_digests: dict
    ) -> None:
        """Bind a marker-only baseline gap to its real collected node through a
        genuine adapter-selected execution (interfaces §1c phase0.baseline_
        repaired; §1e/§1h adapter protocol).

        Emits ``phase0.baseline_repaired`` carrying the version-qualified
        ``ac``, the bound node (node_id + digest), and a selection identity
        derived deterministically from the ACTUALLY executed selection
        (``make_selection_id`` over the adapter-run nodes with the canonical
        R2 scope/basis) plus the command echo of the executed argv -- so the
        evidence cannot be forged as canned identity strings."""
        contract = load_contract(self.repo)
        section = getattr(contract, "integration", None) or contract.unit
        from tracks.adapters.base import UnknownAdapterError, resolve_adapter

        declared = contract.adapter
        if declared is None:
            raise UnknownAdapterError(
                "host project contract declares no [adapter] section "
                "(IF-ADAPTER-001); the executor consumes only the host "
                "declaration and never a built-in reference adapter"
            )
        adapter = resolve_adapter(
            declared.id, declared.protocol, declared.version
        )
        cwd = (
            Path(self.repo)
            if section.cwd == "."
            else Path(self.repo) / section.cwd
        )
        result_path = self._result_staging_path(cmd.command_id or "", "phase0-repair")
        try:
            argv = list(
                adapter.run_selected(
                    section.run_selected,
                    [gap.planned_node_id],
                    str(result_path),
                    cwd,
                )
            )
        finally:
            result_path.unlink(missing_ok=True)
        nodes = [gap.planned_node_id]
        selection_id = make_selection_id(
            nodes=nodes,
            scope=_R2_SCOPE,
            basis=_R2_BASIS,
            baseline=baseline_version,
            commit="",
            tree_stamp="",
        )
        evidence_id = hashlib.sha256(
            f"{gap.planned_node_id}@{baseline_version}".encode()
        ).hexdigest()
        self._emit(
            "phase0.baseline_repaired",
            {
                "ac": f"{gap.ac}@{baseline_version}",
                "bound_node": {
                    "node_id": gap.planned_node_id,
                    "digest": node_digests.get(gap.planned_node_id, ""),
                },
                "selection_id": selection_id,
                "evidence_id": evidence_id,
                "command_echo": argv,
                "status": "repaired",
            },
            command_id=cmd.command_id,
        )

    def _phase0_blocked_resume(self, state) -> bool:
        """SM-01.3 resume preflight (IF-FAILCLOSED-001 / interfaces §1c).

        When a drive starts with ``phase0_status == "BLOCKED"`` the kernel
        parks by design (``decide_phase0`` returns no commands at BLOCKED), so
        only the executor effects layer can re-issue the validation once Human
        repairs the repo facts. Appends one fresh ``phase0_validate`` command
        (command.issued WAL) and executes it, returning True when a resume was
        kicked; a no-op (False) for any other status. The run-loop entry calls
        this at most once per drive -- no tick-level hot loop -- and the
        re-validation runs the FULL hard checks: a still-broken host keeps
        parking at phase0.blocked rather than receiving a watered-down SEALED
        pass (SM-01.3, §1c fail-closed).
        """
        if getattr(state, "phase0_status", None) != "BLOCKED":
            return False
        self.issue(Command(kind="phase0_validate"))
        return True

    def _do_phase0_validate(self, cmd, state, task_id, reconcile):
        """Runtime Phase 0 pre-gate for v0.7 hosts (interfaces §1c/§1d).

        Emits the append-only ``phase0.*`` sequence over the delivered domain
        functions: gap scan over the baseline's real collected-node inventory
        (unrecoverable identities BLOCK, §1d), the collected-coverage gate,
        guard hardening, then the seal. Every event goes through the store
        (append-only, AC-NFR0140-01): dropping the projection and replaying
        rebuilds the identical phase0_status/seal/blocked fields."""
        if reconcile and state.phase0_status in ("SEALED", "BLOCKED"):
            return
        from tracks.executor.phase0 import judge_real_coverage, scan_trace_gaps
        from tracks.executor.test_select import TestSelectError

        baseline_version = _phase0_baseline_version(self.version)
        if baseline_version is None:
            self._emit_phase0_blocked(
                cmd,
                "baseline_unresolvable",
                f"cannot derive a baseline version from {self.version!r}",
                True,
            )
            return
        node_layer, collect_error = self._collect_all_declared_layers(
            capture_absent_ok=True
        )
        if collect_error is not None:
            self._emit_phase0_blocked(cmd, "collect_failed", collect_error, True)
            return
        nodes = sorted(node_layer)
        try:
            node_digests = collect_node_source_digests(Path(self.repo), nodes)
        except TestSelectError as exc:
            self._emit_phase0_blocked(cmd, "identity_unrecoverable", str(exc), True)
            return
        baseline_dir = paths.projects_dir(self.store.home) / baseline_version
        planned = _phase0_planned_bindings(baseline_dir)
        approved = _phase0_approved_acs(baseline_dir)
        gaps = scan_trace_gaps(baseline_version, approved, planned, node_digests, {})
        fatal = [gap for gap in gaps if gap.reason != "marker_only"]
        if fatal:
            self._emit_phase0_blocked(
                cmd,
                fatal[0].reason,
                "; ".join(f"{gap.ac}: {gap.planned_node_id}" for gap in fatal),
                False,
            )
            return
        # Marker-only gaps (planned node collected with a real digest but no
        # persisted evidence) are REPAIRED into real collected-node evidence
        # before the run may continue; a repair-less seal is illegal (§1d).
        for gap in gaps:
            if gap.reason == "marker_only" and gap.planned_node_id in node_digests:
                self._repair_phase0_gap(cmd, gap, baseline_version, node_digests)
        planned_nodes = set(planned.values())
        ratio = (
            len([node for node in planned_nodes if node_digests.get(node)])
            / len(planned_nodes)
            if planned_nodes
            else 1.0
        )
        judgement = judge_real_coverage(ratio, 1.0, ())
        self._phase0_emit_coverage(
            cmd, judgement, ratio, node_layer, node_digests, nodes
        )
        if not judgement.passed:
            self._emit_phase0_blocked(
                cmd, "coverage_below_threshold", judgement.reason or "", True
            )
            return
        if self._phase0_guard_harden(cmd, baseline_version, baseline_dir):
            return
        self._emit_phase0_sealed(cmd, baseline_version, baseline_dir)

    def _phase0_emit_coverage(
        self, cmd, judgement, ratio: float, node_layer, node_digests, nodes
    ) -> None:
        """Emit the ``phase0.coverage`` gate event (§1d, by=collected)."""
        outcomes_blob = self.store.write_audit_blob(
            {
                "nodes": [
                    {"node": node, "layer": node_layer[node], "digest": node_digests[node]}
                    for node in nodes
                ]
            }
        )
        self._emit(
            "phase0.coverage",
            {
                "status": "passed" if judgement.passed else "failed",
                "ratio": ratio,
                "threshold": judgement.threshold,
                "by": "collected",
                "exclude": "none",
                "excluded_sources": list(judgement.excluded_sources),
                "outcomes_ref": f".tracks/runtime/blobs/{outcomes_blob}"
                if outcomes_blob
                else "",
            },
            command_id=cmd.command_id,
        )

    def _phase0_guard_harden(
        self, cmd, baseline_version: str, baseline_dir: Path
    ) -> bool:
        """Guard-hardening gate (§1d): park when no registry/parity evidence
        exists or the registry is invalid; otherwise harden. Returns True when
        the run was parked (blocked) and must stop."""
        violations, guard_refs, registry_present = self._phase0_guard_violations()
        if not registry_present:
            self._emit_phase0_blocked(
                cmd,
                "guard_registry_invalid",
                "no §4.2 guard registry/parity evidence on this host",
                True,
            )
            return True
        self._emit(
            "phase0.guard_hardened",
            {
                "status": "passed" if not violations else "blocked",
                "violations": len(violations),
                "revised": 0,
                "guard_evidence_refs": guard_refs,
                "parity_event_seq": 0,
            },
            command_id=cmd.command_id,
        )
        if violations:
            self._emit_phase0_blocked(
                cmd, "guard_registry_invalid", "; ".join(violations), False
            )
            return True
        return False

    def _emit_phase0_sealed(self, cmd, baseline_version: str, baseline_dir: Path) -> None:
        """Emit the terminal ``phase0.sealed`` event (interfaces §1c).

        Builds the seal manifest from the baseline documents and the frozen
        test digests via the T-005 facade, persists it (plus the frozen-test
        digest map) to audit blobs, and references both from the emitted
        event. The ``seal_id`` is the stable content digest of the remaining
        manifest fields (IF-PHASE-003)."""
        from tracks.executor.phase0 import build_seal_manifest

        contract_toml = paths.project_toml_path(paths.tracks_home(Path(self.repo)))
        env_digest = hashlib.sha256(contract_toml.read_bytes()).hexdigest()
        manifest = build_seal_manifest(
            baseline_version,
            _phase0_document_digests(baseline_dir),
            _phase0_frozen_test_digests(Path(self.repo), self._frozen_test_paths()),
            (),
            env_digest,
        )
        seal_blob = self.store.write_audit_blob(
            {
                "baseline_version": manifest.baseline_version,
                "document_digests": dict(manifest.document_digests),
                "frozen_test_digests": dict(manifest.frozen_test_digests),
                "marks": list(manifest.marks),
                "environment_contract_digest": manifest.environment_contract_digest,
                "seal_id": manifest.seal_id,
            }
        )
        frozen_blob = self.store.write_audit_blob(dict(manifest.frozen_test_digests))
        self._emit(
            "phase0.sealed",
            {
                "baseline_version": baseline_version,
                "seal_id": manifest.seal_id,
                "seal_manifest_blob": f".tracks/runtime/blobs/{seal_blob}"
                if seal_blob
                else "",
                "marks": "registered",
                "env_contract": "pass",
                "frozen_tests_blob": f".tracks/runtime/blobs/{frozen_blob}"
                if frozen_blob
                else "",
            },
            command_id=cmd.command_id,
        )

    # -- M-TEST handlers (flow.md §9, FR-0030/0050/0070) -----------------------

    # -- M-REQ-APPROVAL handlers (FR-0180/0190/0200) ---------------------------

    def _vdir(self) -> Path:
        return paths.version_dir(self.store.home, self.version)

    def _do_generate_preview(self, cmd, state, task_id, reconcile):
        if state.stage == "M-RELEASE":
            # M-RELEASE face (SM-01.6, §1.0.5): the aggregate release
            # preview -- evidence digests + contract policy + operation
            # plan, content-addressed, then AWAITING_RELEASE via the
            # kernel reducer. The approval preview never fires here.
            candidate_sha = str(getattr(state, "candidate_sha", "") or "")
            if not candidate_sha:
                # Fail closed: a release preview without a frozen candidate
                # would mint an unbound preview digest (NFR-0143).
                self._emit(
                    "attention.required",
                    {
                        "area": "freeze",
                        "reason": "candidate_missing",
                        "stage": "M-RELEASE",
                        "detail": "release preview reached with no frozen candidate",
                        "next": "re-run M-VERIFY; trac run retries in place",
                    },
                    command_id=cmd.command_id,
                )
                return
            self._release_preview(cmd, candidate_sha)
            return
        if reconcile and state.preview_ready:
            return
        vdir = self._vdir()
        self._emit(
            "preview.generated",
            {"digest": revision_digest(vdir), "summary": baseline_summary(vdir)},
            command_id=cmd.command_id,
        )

    def _stale_regenerate(self, cmd, approved_digest: str) -> bool:
        """FR-0190 entry gate (D-02/D-03): post-approval commands recompute the
        trio digest; a mismatch means the approval is stale — regenerate the
        preview (back to the human gate) instead of proceeding downstream."""
        vdir = self._vdir()
        current = revision_digest(vdir)
        if current == approved_digest:
            return False
        self._emit(
            "preview.generated",
            {"digest": current, "summary": baseline_summary(vdir)},
            command_id=cmd.command_id,
        )
        return True

    def _do_record_approval(self, cmd, state, task_id, reconcile):
        if reconcile and state.substate == "ISSUES":
            return
        if self._stale_regenerate(cmd, cmd.params["digest"]):
            return
        self._emit(
            "approval.recorded",
            {
                "actor": cmd.params["actor"],
                "digest": cmd.params["digest"],
                "ts": datetime.now(timezone.utc).isoformat(),
                "readonly": True,
            },
            command_id=cmd.command_id,
        )

    def _map_issue_item(self, backend, item_id, title, body, digest, cmd, task_id):
        """(F) IF-ISSUE-001 per-item create + verify + persist.

        Real channel: create -> immediate API readback -> issue.mapped with
        api_verified=true and the authoritative issue-map persistence. The
        explicit fake stand-in channel is persisted api_verified=false so
        closers never consume it. A FAKE artifact surfacing on the real
        channel is rejected fail-closed (fake_rejected + blocked outcome);
        returns None then, else the issue id.
        """
        verified = create_issue_verified(backend, title, body, [self.version])
        issue_id = verified.get("issue_number")
        api_verified = bool(verified.get("api_verified"))
        if not api_verified and not isinstance(backend, FakeIssueBackend):
            rejection = reject_fake_artifact(issue_id)
            if rejection.get("status") == "rejected":
                self._emit(
                    "fake_rejected",
                    {
                        "item_id": item_id,
                        "issue_id": issue_id,
                        "reason": "fake_rejected",
                    },
                    command_id=cmd.command_id,
                    task_id=task_id,
                )
                self._emit(
                    "outcome.received",
                    {
                        "role": "github",
                        "status": "failed",
                        "failure_class": "fake_rejected",
                        "self_report": (
                            f"FAKE artifact refused on the real channel: {issue_id}"
                        ),
                    },
                    command_id=cmd.command_id,
                )
                return None
        backend.add_to_project(issue_id, backend.project)
        repo_id = str(
            getattr(backend, "gh_repo", "") or os.environ.get("TRAC_GITHUB_REPO", "")
        )
        url = (
            f"https://github.com/{repo_id}/issues/{issue_id}"
            if repo_id and not str(issue_id).startswith(("FAKE-", "fake"))
            else ""
        )
        mapping = {
            "issue_number": issue_id,
            "api_verified": api_verified,
            "repo": repo_id,
            "url": url,
            "baseline_digest": digest,
            "source": "issue_create",
            "authoritative": api_verified,
        }
        if api_verified:
            # FR-0283-02: only API-verified creations enter the authoritative
            # map; a FAKE/unverified artifact is never persisted (the closer
            # audits any stale legacy disk entry as non-authoritative).
            persist_issue_mapping(self.repo, item_id, dict(mapping))
        self._emit(
            "issue.created",
            {
                "item_id": item_id,
                "issue_id": issue_id,
                "digest": digest,
                "repo": repo_id,
                "url": url,
                "baseline_digest": digest,
            },
            command_id=cmd.command_id,
        )
        self._emit(
            "issue.mapped",
            {**mapping, "item_id": item_id, "digest": digest},
            command_id=cmd.command_id,
        )
        return issue_id

    def _do_create_issues(self, cmd, state, task_id, reconcile):
        """(F) M-REQ-APPROVAL ISSUES execution path (IF-ISSUE-001).

        Backend selection is fail-closed: missing credentials surface
        attention.required(area=issue_creation, reason=missing_token) and
        no silent Fake fallback is consumed. Each item goes through
        create_issue_verified + persist_issue_mapping (authoritative map,
        crash-idempotent dedup by item id); unverified real-channel
        artifacts are rejected fake_rejected + blocked.
        """
        digest = cmd.params["digest"]
        if reconcile and state.issues_created:
            return
        if self._stale_regenerate(cmd, digest):
            return
        if self._issue_backend is None:
            try:
                self._issue_backend = select_issue_backend(self.repo, self.version)
            except GithubIssuesError as exc:
                if exc.classification == "missing_token":
                    self._emit(
                        "attention.required",
                        {
                            "area": "issue_creation",
                            "reason": "missing_token",
                            "next": (
                                "export GITHUB_TOKEN (and TRAC_GITHUB_REPO) then "
                                "re-run; no silent Fake fallback is consumed"
                            ),
                        },
                        command_id=cmd.command_id,
                        task_id=task_id,
                    )
                    return
                raise
        backend = self._issue_backend
        # D-06 breakpoint resume: item_ids already logged are never rebuilt.
        done = {
            e.payload["item_id"]: e.payload["issue_id"]
            for e in self.store.events(self.run_id)
            if e.type == "issue.created"
        }
        try:
            for item_id, title, body in issue_items(self._vdir(), digest):
                if item_id in done:
                    continue
                issue_id = self._map_issue_item(
                    backend, item_id, title, body, digest, cmd, task_id
                )
                if issue_id is None:
                    return
                done[item_id] = issue_id
        except GithubIssuesError as e:
            # NFR-0030 style: no half-written summary; the reducer counts the
            # failed outcome as an attempt (3rd escalates to Human).
            self._emit(
                "outcome.received",
                {
                    "role": "github",
                    "status": "failed",
                    "failure_class": e.classification,
                    "self_report": str(e),
                },
                command_id=cmd.command_id,
            )
            return
        self._emit(
            "issues.created",
            {"digest": digest, "mapping": done, "project": backend.project},
            command_id=cmd.command_id,
        )

    # -- git helpers -----------------------------------------------------------

