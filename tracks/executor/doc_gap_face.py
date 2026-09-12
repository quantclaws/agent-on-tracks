"""Doc-gap lifecycle slice 2 (run-loop/resume group) extracted from the
Executor (mixin ``ExecDocGapMixin``)."""

from __future__ import annotations

from tracks.discuss.parser import parse_threads
from tracks.executor.doc_comment import (
    DocCommentOrigin,
    QuarantinedChange,
    QuarantineDescriptor,
    ResumeDecision,
    combined_design_identity,
    decide_quarantine_resume,
)
from tracks.executor.doc_gap_runtime import legal_delta_targets
from tracks.kernel.events import Command
from tracks.kernel.machine import State
from tracks.store import new_ulid


class ExecDocGapMixin:
    """SM-02 pre-decide orchestration, quarantine resume and doc-gap dispatch
handlers, consuming the DocGapRuntime facade built in __init__."""

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
