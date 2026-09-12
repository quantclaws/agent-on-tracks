"""Command executor: write-ahead `command.issued` (FR-30), per-kind
execute + reconcile (D-13), agent dispatch via the effects backend seam
(NFR-01; ARCH-003 §4), validate pass-through (D-16).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess  # noqa: F401  (test seam: subprocess.run monkeypatching)
import sys
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import tomllib

from tracks import paths
from tracks.baseline import baseline_summary, revision_digest
from tracks.effects import oob, select_backend
from tracks.effects import publish as publish_effects
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
from tracks.executor.dispatch import (
    ExecDispatchMixin,
    _assignment_budget,  # noqa: F401
    parse_agent_output,  # noqa: F401
    record_failure,  # noqa: F401
    review_failure_chain,  # noqa: F401
    select_failure,  # noqa: F401
    static_parity_check,  # noqa: F401
)
from tracks.executor.doc_face import (
    _NEXT_STAGE,  # noqa: F401
    ExecDocMixin,
)
from tracks.executor.doc_gap_face import ExecDocGapMixin
from tracks.executor.doc_gap_runtime import (
    DocGapCapabilities,
    DocGapRuntime,
)
from tracks.executor.escape_face import ExecEscapeMixin
from tracks.executor.helpers import git
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
from tracks.executor.phase0_face import ExecPhase0Mixin
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
    make_selection_id,
    rebuild_ledger,
)
from tracks.executor.verdict_face import ExecVerdictMixin
from tracks.executor.worktree_face import ExecWorktreeMixin

# T-001 face (E) wiring seam (architecture §1.1 Envelope/failure chain —
# executor.py is the single writer/consumer): the kernel envelope faces and
# the failure-review chain are imported here so the dispatch loop consumes
# them; their behavior bodies land with IF-ENVELOPE-001/002 and the
# failure-chain anchors (T-007/T-035).
from tracks.kernel.events import Command
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








# IF-JOURNEY-001 §1m: the version_scheme label that derives each journey's
# tag/release target (architecture §1.0.3/§1.0.8). A tag/release step target
# must be exactly this template rendered with the run's version facts.
_JOURNEY_VERSION_TEMPLATE = {
    "feature": "feature_tag",
    "post_release": "patch_line",
    "dev": "prerelease_tag",
}
_VERSION_STEP_KINDS = ("tag", "release")









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




class Executor(
    MImplRuntimeMixin,
    ExecObserveMixin,
    ExecDocMixin,
    ExecHotfixMixin,
    ExecRunLoopMixin,
    ExecTestCollectMixin,
    ExecTestRunMixin,
    ExecPhase0Mixin,
    ExecDispatchMixin,
    ExecWorktreeMixin,
    ExecVerdictMixin,
    ExecDocGapMixin,
    ExecEscapeMixin,
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

    # -- SM-02 doc-comment-first (IF-DOCGAP-001 / IF-QUARANTINE-001) ---------

    # -- SM-02 design_gap nested workflow (#62 finding 1) --------------------

    # ------------------------------------------------------------------
    # OOB b89 OB-4 — tasks.md projection guard helpers
    # ------------------------------------------------------------------

    # -- v0.6 hotfix handlers (IF-HOTFIX-002/003/004/005/007) -----------------

    # -- T-001 faces (G/I/J): release-domain handler registrations ----------
    #
    # must-not-drop wiring (plan_defect rounds): the handlers consume the
    # Command kinds routed by decide_release_stage (T-039) and emit the
    # domain event families; deep behavior lands with their owning anchors
    # (T-021 publish / T-029 known-issue / T-034 security).

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

