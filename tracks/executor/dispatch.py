"""Agent dispatch pipeline extracted from the Executor (mixin
``ExecDispatchMixin``): backend act, parity/failure chain, worktree guard."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass

from tracks.effects.dispatch_parity import (
    is_declared,
    static_parity_check,
    static_parity_referenced,
)
from tracks.executor.failure_review import (
    acknowledge_failure,
    inject_into_assignment,
    next_failure_id,
    record_failure,
    review_failure_chain,
    select_failure,
    supersede_acked,
)
from tracks.executor.helpers import _dispatch_payload
from tracks.kernel.envelope import (
    DIAGNOSE_KINDS,
    EnvelopeFormatError,
    parse_agent_output,
    validate_envelope,
)
from tracks.kernel.events import Command
from tracks.kernel.machine import _REVIEW_SUBSTATE

_SAGE_OUTCOME_FIELDS = (
    "acs",
    "outcome",
    "searched_versions",
    "corpus_digests",
    "rationale_refs",
)


@dataclass(frozen=True)
class _AgentDispatchCtx:
    """One acted agent dispatch, bound for outcome finalization."""

    cmd: object
    state: object
    task_id: str | None
    role: str
    substate: str
    doc: str | None
    p: dict
    assignment: dict | None
    pre_dirty: dict | None
    doc_gap: dict | None
    t0: float


def _sage_anchor_outcome_fields(result: dict) -> dict:
    """The anchor-search fields a SAGE_TRIAGE outcome carries into the
    persisted outcome.received (IF-HOTFIX-004)."""
    return {key: result[key] for key in _SAGE_OUTCOME_FIELDS if result.get(key) is not None}


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


class ExecDispatchMixin:
    """dispatch_agent backend seam, parity checks and failure-evidence wiring;
the envelope/failure faces stay re-exported by executor.py."""

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
        if self._reject_over_budget_card(cmd, state, task_id, p):
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

    def _reject_over_budget_card(self, cmd, state, task_id, p: dict) -> bool:
        """M5 (convergence plan 2026-09-05): dispatch-side hard budget on
        the CARD itself. This is the POST-enrichment materialized card
        (issue() already ran _materialize_m_impl_assignment), i.e. the
        bytes the backend actually receives; the FR-11 evidence channel
        merges afterwards in _assignment_with_evidence and is not the
        card's liability. A card JSON over TRAC_ASSIGNMENT_BUDGET bytes
        (default 16KB) is a structural task-graph defect (scope bloat:
        revision archaeology and escalation add-ons living in the prompt
        instead of the event log, b92's 200-600k token dispatches).
        Since the M5 card diet, non-writer (archer/prism) cards are lean
        by construction (manifest=None, slim task identity): the
        budget's teeth are for writer (devon/shield) cards, where an
        oversized card means real prompt blowup.
        Reject before any backend I/O; routes as plan_defect (scope
        replan, no agent attempt burned). Returns True when rejected."""
        card = p.get("assignment")
        budget = _assignment_budget()
        card_bytes = (
            len(json.dumps(card, ensure_ascii=False, default=str))
            if isinstance(card, dict)
            else 0
        )
        if not (isinstance(card, dict) and budget and card_bytes > budget):
            return False
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
        result = self._run_agent_act(
            cmd, state, task_id, role, substate, doc, doc_path, assignment
        )
        if result is None:
            return
        self._finalize_agent_outcome(
            _AgentDispatchCtx(
                cmd=cmd,
                state=state,
                task_id=task_id,
                role=role,
                substate=substate,
                doc=doc,
                p=p,
                assignment=assignment,
                pre_dirty=pre_dirty,
                doc_gap=doc_gap,
                t0=t0,
            ),
            result,
        )

    def _run_agent_act(
        self, cmd, state, task_id, role, substate, doc, doc_path, assignment
    ):
        """Open the writer worktree, run act(), and surface a replay failure.

        B1 (issue #2, user ruling 2026-08-18): writer agents (Devon RGR in
        M-IMPL, Shield WRITE in M-TEST) run in a spatially isolated worktree
        created from the current main HEAD; the work is replayed onto the main
        tree afterwards, so every existing pipeline (pre_dirty, manifest
        audit, R/G refs, atomic rollback) keeps operating on the main tree
        unchanged. Per-dispatch lifecycle: each phase re-creates from HEAD,
        so REFACTOR and PRISM-revise re-dispatches naturally see committed
        work; a crash leaks only a worktree that the B2 start-time sweep
        reclaims. Audited events. Returns None after emitting a replay
        failure (the turn is fail-closed)."""
        handle, wt_task_id = self._open_writer_worktree(state, role, substate, cmd)
        result, replay_error = self._act_in_worktree(
            cmd, role, substate, doc, doc_path, assignment, handle, wt_task_id
        )
        if replay_error is None:
            return result
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
        return None

    def _finalize_agent_outcome(self, ctx, result) -> None:
        cmd, state, task_id = ctx.cmd, ctx.state, ctx.task_id
        p = ctx.p
        # IF-ENVELOPE-002 finalization seam: after act() (and any result-
        # enriching subclass) returned, and BEFORE any raw_output is consumed
        # below, give the backend the last word on its declared reply bytes.
        result = self._finalize_backend_result(result, ctx.role, ctx.substate, ctx.assignment)
        # T-001 face (E) / IF-ENVELOPE-002: post-act short-circuit gates
        # (dispatch log end, prepared-artifact parity rejection, collection-
        # time format_error). Each short-circuit keeps the outcome out of the
        # ordinary pipeline (never a semantic success).
        if self._post_act_gates(
            p, result, cmd, task_id, ctx.assignment, time.monotonic() - ctx.t0
        ):
            return
        # IF-ENVELOPE-002: the single full-gate dispatch.parity success
        # event (complete audit) of a declared dispatch.
        self._emit_dispatch_parity_success(result, cmd, task_id, ctx.assignment)
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
        if self._handle_no_diff_outcome(ctx.substate, result, cmd, task_id, state):
            return
        # D-29 anti-self-report triple ③: M-TEST PRISM_REVIEW criteria-pack
        # mismatch -> verdict.failed, re-dispatch Prism (no prism.verdict).
        # Must be checked before emitting outcome.received so a mismatch
        # short-circuits without entering the pipeline.
        if self._criteria_pack_mismatch(
            ctx.role, ctx.substate, state, result.get("verdict"), ctx.assignment, result, cmd
        ):
            self._emit_criteria_pack_failure(
                cmd,
                task_id,
                p,
                ctx.assignment,
                result,
                state,
            )
            return
        # SM-02 doc-comment-first pre-check (§1k): classify design-doc deltas
        # BEFORE ordinary validation. illegal_body_edit > legal_discussion.
        if self._handle_doc_gap_outcome(
            cmd, state, task_id, ctx.role, ctx.substate, result, ctx.doc_gap,
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
        if ctx.role == "sage" and ctx.substate == "SAGE_TRIAGE":
            payload.update(_sage_anchor_outcome_fields(result))
        # Expose the agent I/O blob ref on the result so downstream verdict
        # emissions can point the next agent at the full transcript (critic
        # reviews and diagnoses live in blobs - long-form content must be
        # passed by reference, never re-derived or inlined).
        agent_io = payload.get("agent_io") or {}
        if agent_io.get("output_ref"):
            result.setdefault("output_ref", agent_io["output_ref"])
        if self._checkpoint_eligible(state, result, ctx.substate):
            payload["result_checkpoint"] = self._result_checkpoint_payload(
                cmd, state, result, p, ctx.substate, ctx.role, ctx.doc, ctx.pre_dirty
            )
        self._emit("outcome.received", payload, command_id=cmd.command_id, task_id=task_id)
        # T-001 face (E) / IF-FAILURE-001: emitted -> stored (append-only
        # failure evidence at production time) and consumed -> acked (the
        # outcome's evidence_ack closes the loop on injected evidence).
        self._record_failure_stored(result, ctx.role, state, task_id, cmd)
        self._consume_evidence_ack(result, ctx.role)
        if "result_checkpoint" in payload:
            return  # pipeline drives the domain event
        self._emit_dispatch_verdict(result, ctx.role, state, p, cmd, task_id)
        shield_committed = self._emit_shield_commit(result, ctx.role, state, cmd, task_id)
        self._maybe_transition_full_ledger(cmd, state, ctx.role, result, shield_committed)

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

        TRAC_ENVELOPE_DECLARE=0 or an unmapped (role, substate) pair:
        undeclared, the gate keeps today's staged semantics (nothing is
        enforced).

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
            # The stage-routable failure (same shape as the 792f70e
            # format_error routing): without it the dispatch flags set by
            # command.issued stay set, decide() awaits an outcome that never
            # comes, and every later run is a silent no-op (live 01M19FJ:
            # the pre-gate rejection of the recovered T-042 dispatch parked
            # M-IMPL/GREEN forever). verdict.failed lets each stage's own
            # routing reset the flags and consume the attempt budget.
            self._emit(
                "verdict.failed",
                {
                    "check": "version_parity_mismatch",
                    "reason": "declared-dispatch version parity gate rejected "
                    "the dispatch before agent execution",
                    "evidence": "dispatch.rejected mismatches on this command",
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
            self._emit_format_error(cmd, task_id, exc, declared_kind)
            return True
        except NotImplementedError:
            return False  # T-035 module pending; deferred-only-pass
        result.setdefault("envelope", envelope)
        return False

    def _emit_format_error(self, cmd, task_id, exc, declared_kind) -> None:
        """format_error plus the stage-routable verdict for declared kinds.

        A declared review/diagnose dispatch whose reply violates the declared
        schema is a CONTRACT violation of that role: without a routable event
        the reviewer_dispatched flag stays set and decide() awaits a verdict
        that already failed classification forever (live 01M280CV: DIAGNOSE
        schema_violation stranded active/DIAGNOSE across process restarts —
        the exact B62 #80 stall class). The verdict.failed makes the
        projection apply the accepted contract-violation routing (reset
        review flags, consume attempt, stay for re-dispatch; budget
        exhaustion escalates). Audit trail keeps both events.
        """
        self._emit(
            "format_error",
            {"kind": exc.kind, "detail": exc.detail, "task_id": task_id},
            command_id=cmd.command_id,
            task_id=task_id,
        )
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
