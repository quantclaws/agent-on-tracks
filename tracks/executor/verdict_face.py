"""Verdict/diagnose/shield emission extracted from the Executor (mixin
``ExecVerdictMixin``)."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone

from tracks.discuss.locate import locate
from tracks.discuss.parser import parse_threads
from tracks.effects.backend import valid_test_tasks
from tracks.executor.helpers import (
    _DIAGNOSE_TARGET,
    _dispatch_payload,
    _scoped_commit_if_staged,
    emit_test_committed,
    git,
)
from tracks.executor.result_checkpoint import ResultCheckpointMixin
from tracks.kernel.machine import State
from tracks.kernel.machine_outcomes import _INFRA_FAILURE_CLASSES

# reviewer role -> its verdict event type
_VERDICT_EVENT = {"sage": "sage.verdict", "lex": "lex.verdict", "prism": "prism.verdict"}


def _is_process_crash(result: dict) -> bool:
    """True when the result carries an opencode process-crash fact (OOB
    2026-09-19, run 01M2QTJB: opencode exit 1 with 429 quota noise produced
    an empty reply that was misread as diagnose_contract_violation, burning
    the DIAGNOSE attempt budget and escalating). A crash is machine-side
    (infra): the outcome.received failure_class already drives the bounded
    infra re-dispatch with backoff and no attempt consumed -- a
    contract-violation verdict on top would double-punish the same turn."""
    if not isinstance(result, dict):
        return False
    for field in ("returncode", "exit_code"):
        code = result.get(field)
        if isinstance(code, int) and not isinstance(code, bool) and code != 0:
            return True
    return result.get("failure_class") in _INFRA_FAILURE_CLASSES


# D-29 criteria pack identity (architecture.md §3.4): echoed by Prism and
# read back by the executor to enforce the anti-self-report triple.
_CRITERIA_PACK = {"name": "tracks-prism-test", "version": "0.1"}


# D-35 / PRISM-D35-R1-ADV1 review fields threaded into the M-DESIGN
# prism.verdict publish (kept in parity with the ResultCheckpointMixin
# publish branches; the hotfix override below prepends anchor_verdict,
# FR-0243 / IF-HOTFIX-009, interfaces §1a).
_DESIGN_PRISM_THREAD_KEYS = ("review_summary", "findings", "review_ref", "discussion_refs")


@dataclass(frozen=True)
class _VerifyFinalIdentity:
    """Resolved VERIFY_FINAL candidate identity + validity verdict."""

    candidate_sha: str
    frozen_sha: str
    error: bool


class ExecVerdictMixin:
    """Dispatch verdicts, DIAGNOSE envelopes and shield-commit emission; placed
before ResultCheckpointMixin so the hotfix prism override wins."""

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
        if role == "prism" and (params or {}).get("scope") == "security":
            self._publish_security_review_verdict(result, params, cmd, task_id)
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
            if _is_process_crash(result):
                # OOB 2026-09-19: the empty reply rides a crashed opencode
                # process (exit != 0 / non_zero_exit), not a violating agent.
                # No contract verdict here -- the outcome.received infra
                # classification owns the turn (streak + backoff re-dispatch,
                # no attempt burned).
                return
            self._emit_diagnose_contract_violation(cmd, state, task_id)
            return
        classification = verdict
        evidence, emitted_check, reason = self._diagnose_evidence(
            result, state, classification, task_id
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

    def _emit_diagnose_contract_violation(self, cmd, state, task_id) -> None:
        """Fail-closed verdict for a DIAGNOSE reply without a classification."""
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

    def _diagnose_evidence(self, result, state, classification: str, task_id) -> tuple:
        """Assemble (evidence, emitted_check, reason) for a valid diagnosis."""
        diagnosis = (
            result.get("diagnosis") if isinstance(result.get("diagnosis"), dict) else {}
        )
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
        return self._m2_diagnosis_envelope(
            task_id or state.current_task_id, evidence, classification, reason
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
        if self._shield_fix_rejected(result, state, cmd, task_id, changed):
            return False
        if not self._commit_shield_fix(state, cmd, task_id):
            return False
        commit_sha = emit_test_committed(self, cmd, tests_dir, task_id=task_id)
        # B91 follow-up (re-baseline): the sanctioned fix commit joins the
        # task's immutable R family so the eventual G binds a provable anchor
        # that is ALSO the tree that gated it (no-op without a prior RED).
        if state.stage == "M-IMPL":
            self._rebaseline_red_family(task_id, commit_sha, cmd.command_id)
        return True

    def _shield_fix_rejected(self, result, state, cmd, task_id, changed: list) -> bool:
        """Fail-closed SHIELD_FIX pre-commit checks: manifest + anchor lint."""
        # PRISM-B28-R2-02: the SHIELD_FIX manifest contract promises an
        # include==observed comparison — enforce it here (M-IMPL shield
        # WRITE bypasses the ResultCheckpoint pipeline).
        mismatch = self._shield_fix_manifest_mismatch(result, changed)
        if mismatch:
            self._emit(
                "verdict.failed",
                {
                    "check": "manifest",
                    "reason": "include_mismatch",
                    "evidence": mismatch,
                    "attempt": state.current_attempt + 1,
                },
                command_id=cmd.command_id,
                task_id=task_id,
            )
            return True
        # M4 (convergence plan 2026-09-05): birth-time anchor
        # satisfiability lint on the freshly revised test assets --
        # same two rule-1 signatures as the M-TEST WRITE gate. Routes
        # as test_defect so Shield rewrites the anchor in-domain.
        from tracks.checks.anchor_lint import anchor_static_violations

        violations: list[str] = []
        for rel in changed:
            if rel.endswith(".py"):
                violations.extend(anchor_static_violations(self.repo / rel))
        if violations:
            self._emit(
                "verdict.failed",
                {
                    "check": "test_defect",
                    "reason": "anchor_static (M4 rule 1): " + "; ".join(violations[:3]),
                    "evidence": "\n".join(violations),
                    "attempt": state.current_attempt + 1,
                },
                command_id=cmd.command_id,
                task_id=task_id,
            )
            return True
        return False

    def _commit_shield_fix(self, state, cmd, task_id) -> bool:
        """Stage tests/ + create the SHIELD_FIX commit with trailers."""
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
        identity = self._verify_final_identity(verdict, result, assignment, cmd)
        if identity.error:
            self._emit_verify_final_identity_error(cmd, verdict, identity)
            return
        payload = self._verify_final_payload(
            result, verdict, commit_sha, created_commit, result_id, identity.candidate_sha
        )
        if verdict == "revise" and not self._verify_final_discussion_anchor(result):
            self._emit(
                "prism.verdict", payload, command_id=cmd.command_id, task_id=task_id
            )
            self._emit(
                "attention.required",
                {
                    "reason": "revise_without_findings",
                    "stage": "M-VERIFY",
                    "candidate_sha": identity.candidate_sha,
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
            self._advance_verify_chain(cmd, identity.candidate_sha)
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
                    "candidate_sha": identity.candidate_sha,
                    "next": "address findings; trac run re-dispatches the review",
                },
                command_id=cmd.command_id,
            )

    def _verify_final_identity(self, verdict, result: dict, assignment: dict, cmd):
        """Resolve the frozen/assigned/returned candidate identity + validity."""
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
        return _VerifyFinalIdentity(candidate_sha, frozen_sha, bool(identity_error))

    def _emit_verify_final_identity_error(self, cmd, verdict, identity) -> None:
        self._emit(
            "attention.required",
            {
                "reason": "verify_final_identity_mismatch"
                if verdict in ("pass", "revise")
                else "verify_final_invalid_verdict",
                "stage": "M-VERIFY",
                "candidate_sha": identity.candidate_sha or identity.frozen_sha,
                "next": (
                    "re-dispatch VERIFY_FINAL with the frozen candidate and "
                    "a valid pass/revise review"
                ),
            },
            command_id=cmd.command_id,
        )

    def _verify_final_payload(
        self, result, verdict, commit_sha, created_commit, result_id, candidate_sha
    ) -> dict:
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
        return payload

    def _verify_final_discussion_anchor(self, result: dict) -> bool:
        """Accept a blocker finding tied to a fresh open Prism/Lex thread.

        Findings marked ``simulated`` (the FakeBackend's synthesized revise
        content, effects/fake.py ``_ensure_verify_final_revise_fields``) are
        excluded from the blocker set: a simulation can never anchor itself as
        a real review block."""
        refs = result.get("discussion_refs")
        if not isinstance(refs, list) or not refs:
            return False
        blockers = self._blocker_ids(result.get("findings"))
        if not blockers:
            return False
        return any(self._discussion_ref_anchors(ref, blockers) for ref in refs)

    @staticmethod
    def _blocker_ids(findings) -> set:
        """Non-simulated blocker finding ids (empty set for a bad shape)."""
        if not isinstance(findings, list):
            return set()
        return {
            finding.get("id")
            for finding in findings
            if isinstance(finding, dict)
            and finding.get("severity") == "blocker"
            and finding.get("simulated") is not True
            and isinstance(finding.get("id"), str)
        }

    def _discussion_ref_anchors(self, ref, blockers: set) -> bool:
        """True when *ref* resolves to a fresh open Prism/Lex thread."""
        if not isinstance(ref, dict):
            return False
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
            return False
        path = (self.repo / file_name).resolve()
        try:
            path.relative_to(self.repo.resolve())
        except ValueError:
            return False
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return False
        try:
            located = locate(text, token, thread_id)
        except (KeyError, TypeError, ValueError, AttributeError):
            return False
        if located.status != "unique":
            return False
        thread = next(
            (item for item in parse_threads(text) if item.thread_id == thread_id),
            None,
        )
        if thread is None or thread.status not in ("open", "reopen"):
            return False
        return thread.initiator.strip().lower() in ("prism", "lex")
