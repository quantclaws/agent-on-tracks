"""Command executor: write-ahead `command.issued` (FR-30), per-kind
execute + reconcile (D-13), agent dispatch via the effects backend seam
(NFR-01; ARCH-003 §4), validate pass-through (D-16).
"""

from __future__ import annotations

import os
import subprocess  # noqa: F401  (test seam: subprocess.run monkeypatching)
import sys
from copy import deepcopy
from pathlib import Path

from tracks.effects import oob, select_backend
from tracks.effects.github import (
    GithubIssuesError,
    judge_ci_binding,
    readback_ci_run,
)
from tracks.executor import m_verify

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
from tracks.executor.host_contract import NormalizedGateResult
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
    normalized_result_payload,
)
from tracks.executor.m_impl_runtime import MImplRuntimeMixin
from tracks.executor.milestone_chain import ExecMilestoneMixin
from tracks.executor.observe import ExecObserveMixin
from tracks.executor.phase0_face import ExecPhase0Mixin
from tracks.executor.publish_face import ExecPublishMixin
from tracks.executor.release_gate import (
    active_release_branch,
    build_operation_plan,
    operation_plan_needs_n,
    remote_patch_n,
)
from tracks.executor.release_preview import (
    _contract_table,
    _journey,
    _version_facts,
)
from tracks.executor.release_tail import ExecReleaseTailMixin
from tracks.executor.result_checkpoint import ResultCheckpointMixin
from tracks.executor.run_loop import ExecRunLoopMixin
from tracks.executor.stall import CommandStallTracker
from tracks.executor.test_collect import (
    _R2_BASIS,  # noqa: F401
    _R2_SCOPE,  # noqa: F401
    ExecTestCollectMixin,
    _resolve_contract_argv0,  # noqa: F401
)
from tracks.executor.test_execute import ExecTestRunMixin
from tracks.executor.verdict_face import ExecVerdictMixin
from tracks.executor.verify_gates import ExecVerifyGatesMixin
from tracks.executor.verify_park import ExecVerifyParkMixin
from tracks.executor.worktree_face import ExecWorktreeMixin

# T-001 face (E) wiring seam (architecture §1.1 Envelope/failure chain —
# executor.py is the single writer/consumer): the kernel envelope faces and
# the failure-review chain are imported here so the dispatch loop consumes
# them; their behavior bodies land with IF-ENVELOPE-001/002 and the
# failure-chain anchors (T-007/T-035).
from tracks.kernel.events import Command
from tracks.project import layout_paths as _layout_paths
from tracks.store import Store, new_ulid


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
    ExecPublishMixin,
    ExecMilestoneMixin,
    ExecReleaseTailMixin,
    ExecVerifyParkMixin,
    ExecVerifyGatesMixin,
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

    # -- v0.8 M-VERIFY chain (IF-VERIFY-001/003/004, architecture §1.1) -------

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

    # -- v0.7 Phase 0 pre-gate handler (architecture §1.0.2/§1.1, T-015) -------

    # -- M-TEST handlers (flow.md §9, FR-0030/0050/0070) -----------------------

    # -- M-REQ-APPROVAL handlers (FR-0180/0190/0200) ---------------------------

    # -- git helpers -----------------------------------------------------------

