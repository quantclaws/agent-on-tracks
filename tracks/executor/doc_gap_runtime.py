"""Doc-gap lifecycle runtime (SM-02, IF-DOCGAP-001 / IF-QUARANTINE-001).

Physical slice 1 of the executor.py doc-gap extraction (DESIGN.md appendix,
2026-09-09): the capture -> classify -> quarantine/pause -> nested design
revision -> adjudication-ingestion lifecycle, moved out of the 8k-line
``Executor`` behind an explicit narrow capability facade
(``DocGapCapabilities``). Every genuinely external capability (repo, run_id,
store, backend, emit, issue, doc_path, path_identity, dirty_snapshot,
dispatch_issued, layout_paths) is injected once at construction; the
lifecycle logic below owns everything else internally.

This module must NEVER import ``tracks.executor.executor`` (that would form
an ``executor -> doc_gap_runtime -> executor`` cycle) and must not receive
the complete ``Executor`` as a host: the run-loop orchestrator
``_sm02_pre_decide`` and the resume-group methods
(``_resume_doc_gap_if_ready`` and friends) deliberately remain in Executor
(slice 2). Executor keeps thin same-name forwarders so dynamic dispatch
(``getattr(self, "_do_...")``) and the public/private method surface are
unchanged.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from tracks.executor.doc_comment import (
    ROLE_ALLOWED_DOCS,
    AdjudicationMarker,
    DocCommentOrigin,
    QuarantinedChange,
    classify_design_document_deltas,
    combined_design_identity,
    create_doc_gap_record,
    legal_anchor_pairs,
    match_adjudication_marker,
    quarantine_authorized_changes,
    scan_adjudication_markers,
)
from tracks.executor.helpers import git
from tracks.kernel.events import Command
from tracks.kernel.machine import DESIGN_DOCS, State
from tracks.store import new_ulid


class StoreLike(Protocol):
    """The slice of the event store the doc-gap lifecycle touches."""

    def state(self, run_id: str) -> State: ...

    def events(self, run_id: str) -> Iterable[Any]: ...

    def write_audit_blob(self, payload: dict | list | str) -> str | None: ...

    def load_payload(self, payload: dict) -> dict: ...


class BackendLike(Protocol):
    """The agent effects seam (tracks.effects.backend.AgentBackend)."""

    def act(
        self,
        role: str,
        substate: str,
        doc: str | None,
        doc_path: Path | None,
        assignment: dict | None = None,
        worktree: Path | None = None,
    ) -> dict: ...


@dataclass(frozen=True)
class DocGapCapabilities:
    """Narrow capability facade injected by Executor (DESIGN.md appendix).

    Each field is one genuinely external capability; the doc-gap lifecycle
    below owns all internal state transitions. ``repo``/``run_id`` are the
    shared run identity, ``store``/``backend`` are the effects seams, and
    the callables are the bound Executor methods (``_emit``, ``issue``,
    ``_doc_path``, ``_path_identity``, ``_dirty_snapshot``,
    ``_dispatch_issued``) plus the project ``[layout.<role>]`` projection.
    No complete Executor/self is passed, ever.
    """

    repo: Path
    run_id: str
    store: StoreLike
    backend: BackendLike
    emit: Callable[..., Any]
    issue: Callable[..., Any]
    doc_path: Callable[[str], Path]
    path_identity: Callable[[Path], str]
    dirty_snapshot: Callable[[], dict[str, str]]
    dispatch_issued: Callable[[str], bool]
    layout_paths: Callable[[Path, str], Iterable[str]]


def legal_delta_targets(legal_deltas: list) -> tuple[list[str], list[str]]:
    """Collect (document_paths, thread_ids) touched by legal deltas."""
    document_paths: list[str] = []
    thread_ids: list[str] = []
    for d in legal_deltas:
        document_paths.append(d.path)
        thread_ids.extend(d.new_thread_ids)
    return document_paths, thread_ids


class DocGapRuntime:
    """SM-02 doc-gap lifecycle (physical slice 1).

    Maintains the shared doc-gap record/revision/quarantine lifecycle:
    pre-dispatch capture, outcome classification (fixed precedence
    ``illegal_body_edit > legal_discussion > ordinary``), atomic rollback or
    legal-change quarantine + visible pause, the nested Archer design
    revision / Prism review workflow with WAL-safe crash-window recovery and
    fail-closed revision checkpointing, and adjudication-marker ingestion.
    All cross-cutting effects flow through the injected capabilities; the
    event names, payload shapes, record fields, quarantine blob and git
    commit policy are byte-identical to the pre-extraction Executor bodies.
    """

    def __init__(self, caps: DocGapCapabilities) -> None:
        self._caps = caps

    # -- capture --------------------------------------------------------------

    def _snapshot_design_docs(self, role: str) -> dict[str, bytes]:
        """Read the role's allowed design docs before/after a dispatch."""
        allowed = ROLE_ALLOWED_DOCS.get(role, frozenset())
        snapshot: dict[str, bytes] = {}
        for name in allowed:
            path = self._caps.doc_path(name)
            snapshot[name] = path.read_bytes() if path.exists() else b""
        return snapshot

    def _capture_doc_gap_context(
        self, state: State, role: str
    ) -> tuple[dict[str, bytes], dict[str, str]] | None:
        """Capture baseline design docs + dirty snapshot before act().

        Returns None when the role is not doc-gap-checked (IF-DOCGAP-001).
        The §1k contract (and architecture.md §3.8 entry path) covers every
        Devon or Shield outcome with NO stage qualifier: Shield WRITE fires
        in M-TEST, Devon RGR in M-IMPL. A `state.stage != "M-IMPL"` guard here
        would silently skip Shield WRITE deltas, leaving hook-injected legal
        discussions / illegal body edits unclassified before ordinary
        validation (AC-FR0234-01/04, AC-FR0237-02). Only the role allow-list
        gates which outcomes are doc-comment-checked.
        """
        if role not in ("devon", "shield"):
            return None
        return self._snapshot_design_docs(role), self._caps.dirty_snapshot()

    def _doc_gap_origin(
        self, state: State, role: str, substate: str, cmd: Command
    ) -> DocCommentOrigin:
        """Build the origin identity bound to the dispatch (§1m)."""
        return DocCommentOrigin(
            run_id=self._caps.run_id,
            role=role,
            task_id=state.current_task_id,
            phase=substate,
            dispatch_id=cmd.command_id,
            attempt=cmd.params.get("attempt", state.current_attempt + 1),
        )

    # -- classify + route the outcome -----------------------------------------

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
        """Classify design-doc deltas BEFORE ordinary validation (§1k).

        Returns True when the outcome is routed to doc-gap adjudication
        (rejected or paused) instead of the ordinary validate/checkpoint path.
        Precedence: illegal_body_edit > legal_discussion > ordinary.
        ``discussion_reply`` deltas (replies to pre-existing threads, no new
        roots) pass through to the ordinary path — no SM-02 pause (B26a/#28).
        """
        if doc_gap is None:
            return False
        baseline_documents, pre_dirty = doc_gap
        current_documents = self._snapshot_design_docs(role)
        deltas = classify_design_document_deltas(
            role=role,
            baseline_documents=baseline_documents,
            current_documents=current_documents,
        )
        if any(d.classification == "illegal_body_edit" for d in deltas):
            self._reject_over_reach(
                cmd, state, task_id, role, deltas, baseline_documents, pre_dirty
            )
            return True
        legal = [d for d in deltas if d.classification == "legal_discussion"]
        if legal:
            self._pause_for_legal_discussion(
                cmd, state, task_id, role, substate, deltas, legal, pre_dirty
            )
            return True
        return False

    def _rollback_doc_gap_round(
        self,
        baseline_documents: dict[str, bytes],
        pre_dirty: dict[str, str] | None,
    ) -> list[str]:
        """Atomic rollback of all agent-attributable changes (§1k).

        Restores design docs to pre-dispatch bytes and reverts non-doc repo
        changes attributable to this outcome. Human/pre-dirty content survives.
        """
        rejected: list[str] = []
        for name, data in baseline_documents.items():
            path = self._caps.doc_path(name)
            path.write_bytes(data)
            rejected.append(name)
        pre = pre_dirty or {}
        for rel in sorted(self._caps.dirty_snapshot()):
            if rel in pre:
                continue  # Human/pre-dirty content survives (AC-FR0237-02).
            tracked = git(
                self._caps.repo, "cat-file", "-e", f"HEAD:{rel}", check=False
            ).returncode == 0
            if tracked:
                # A file that existed pre-dispatch (committed bytes) is
                # restored from HEAD -- whether the agent modified or
                # deleted it. It must never be unlinked: that would leave a
                # bogus `D path` in the worktree (interfaces §1k).
                git(self._caps.repo, "checkout", "HEAD", "--", rel, check=False)
            elif (self._caps.repo / rel).exists():
                # Agent-created file (absent pre-dispatch): remove it.
                (self._caps.repo / rel).unlink()
            rejected.append(rel)
        return rejected

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
        """illegal_body_edit: atomic rollback + outcome.rejected (AC-FR0237-02).

        Emits outcome.received(status='rejected') so the machine's
        failed-outcome path drives the retry re-dispatch (reset_doc +
        consume_attempt). status != 'failed' avoids the DIAGNOSE mis-route
        for devon RED/GREEN (machine.py _handle_failed_outcome).
        """
        rejected_paths = self._rollback_doc_gap_round(baseline_documents, pre_dirty)
        origin = self._doc_gap_origin(state, role, state.substate, cmd)
        self._caps.emit(
            "outcome.received",
            {
                "role": role,
                "status": "rejected",
                "failure_class": "over_reach",
                "self_report": "illegal body edit: non-discussion design-doc content changed",
            },
            command_id=cmd.command_id,
            task_id=task_id,
        )
        self._caps.emit(
            "outcome.rejected",
            {
                "failure_class": "over_reach",
                "rollback": "atomic",
                "rejected_paths": sorted(set(rejected_paths)),
                "origin": asdict(origin),
            },
            command_id=cmd.command_id,
            task_id=task_id,
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
        """legal_discussion: visible pause before validation (FR-0234-01/02).

        Emits doc_comment.detected + outcome.quarantined and does NOT emit
        outcome.received: the ordinary validate/checkpoint path never runs
        (§1k). doc_dispatched stays True so decide() halts at the pause.
        """
        origin = self._doc_gap_origin(state, role, substate, cmd)
        record = create_doc_gap_record(
            origin=origin,
            deltas=deltas,
            quarantine_id=None,
        )
        descriptor, manifest_ref = self._quarantine_legal_changes(
            state, origin, record, pre_dirty
        )
        self._emit_doc_gap_pause(
            cmd, task_id, origin, legal_deltas, record, descriptor, manifest_ref
        )

    def _quarantine_legal_changes(
        self,
        state: State,
        origin: DocCommentOrigin,
        record,
        pre_dirty: dict[str, str] | None,
    ) -> tuple:
        """Quarantine authorized changes and persist the manifest blob."""
        allowed_paths: tuple = ()
        manifest_meta = (state.current_task_metadata or {}).get(
            "manifest",
        )
        if isinstance(manifest_meta, dict):
            allowed_paths = tuple(manifest_meta.get("allowed_paths", []))
        # SM-02 (#62 finding 1): when the manifest is absent (M-TEST Shield
        # WRITE has no task-level manifest), fall back to the project
        # contract's [layout.<role>] writable paths so authorized non-doc
        # changes are quarantined and the resume decision can detect
        # design-stale discard after a design revision.
        if not allowed_paths:
            allowed_paths = tuple(self._caps.layout_paths(self._caps.repo, origin.role))
        # Collect agent-attributable non-document changes from the dirty
        # snapshot: every file dirty after the dispatch that is not a
        # protected design doc. quarantine_authorized_changes filters by
        # allowed_paths and pre_dirty internally.
        design_docs = set(ROLE_ALLOWED_DOCS.get(origin.role, frozenset()))
        agent_changes: list[QuarantinedChange] = []
        for rel, identity in self._caps.dirty_snapshot().items():
            if rel in design_docs:
                continue
            agent_changes.append(
                QuarantinedChange(
                    path=rel,
                    operation="modify",
                    baseline_identity=None,
                    content_identity=identity,
                )
            )
        descriptor = quarantine_authorized_changes(
            origin=origin,
            allowed_paths=allowed_paths,
            pre_dirty_identities=pre_dirty or {},
            agent_changes=tuple(agent_changes),
            # Design anchor: combined AT-PAUSE identity of the LEGAL
            # discussion documents — the same set the resume decision
            # re-identifies from live bytes (never document_deltas[0],
            # the alphabetically-first protected doc, and never the
            # pre-dispatch baseline, which would count the discussion
            # itself as permanent drift).
            design_identity=combined_design_identity(
                list(legal_anchor_pairs(record.document_deltas))
            ),
            run_identity=self._caps.run_id,
        )
        manifest_ref = self._caps.store.write_audit_blob(
            {
                "quarantine_id": descriptor.quarantine_id,
                "origin": asdict(origin),
                # Identity anchors persisted for the resume decision
                # (decide_quarantine_resume rebuilds from this blob).
                "design_identity": descriptor.design_identity,
                "run_identity": descriptor.run_identity,
                "changes": [asdict(c) for c in descriptor.changes],
                "status": descriptor.status,
            }
        )
        return descriptor, manifest_ref

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
        """Emit doc_comment.detected + outcome.quarantined (§1m closed set)."""
        document_paths, thread_ids = legal_delta_targets(legal_deltas)
        self._caps.emit(
            "doc_comment.detected",
            {
                "record_id": record.record_id,
                "origin": asdict(origin),
                "document_paths": document_paths,
                "thread_ids": thread_ids,
            },
            command_id=cmd.command_id,
            task_id=task_id,
        )
        self._caps.emit(
            "outcome.quarantined",
            {
                "record_id": record.record_id,
                "quarantine_id": descriptor.quarantine_id,
                "status": descriptor.status,
                "manifest_ref": manifest_ref or descriptor.manifest_ref,
                "manifest_sha256": descriptor.manifest_sha256,
            },
            command_id=cmd.command_id,
            task_id=task_id,
        )

    # -- nested design-revision workflow (#62 finding 1) -----------------------

    def _advance_doc_gap_design_revision(self, state: State) -> bool:
        """Drive the nested Archer design-revision + Prism review workflow.

        A DESIGN_GAP record whose revision sub-progress is None (just
        adjudicated) or ``design_revised`` (Archer done, Prism pending) gets
        its nested dispatch issued here via WAL ``issue()``.  The dispatch's
        outcome is routed to ``_do_doc_gap_dispatch`` (not the ordinary
        M-DESIGN pipeline) so no ``design.committed`` / ``prism.verdict``
        event clobbers the paused origin stage.  Returns True (continue the
        run loop) when a dispatch was issued.

        WAL-safe recovery (#62 finding 1) is delegated to
        ``_recover_doc_gap_dispatch`` — see its docstring for the crash
        window and the idempotent re-issue contract.
        """
        if state.status != "active" or not state.doc_gaps:
            return False
        for record_id, rec in state.doc_gaps.items():
            if rec.get("state") != "DESIGN_GAP":
                continue
            revision = rec.get("revision")
            if revision is None:
                self._dispatch_doc_gap_agent(
                    record_id, rec, state, phase="design_revision"
                )
                return True
            if revision == "design_revised":
                self._dispatch_doc_gap_agent(
                    record_id, rec, state, phase="design_review"
                )
                return True
            if revision in ("archer_dispatched", "prism_dispatched"):
                if self._recover_doc_gap_dispatch(record_id, rec, state, revision):
                    return True
                break  # genuinely waiting for the nested outcome
            # prism_reviewed: fall through to the resume check.
            break
        return False

    def _recover_doc_gap_dispatch(
        self, record_id: str, rec: dict, state: State, revision: str
    ) -> bool:
        """WAL-safe recovery for a crash between ``doc_gap.design_dispatched``
        and ``command.issued`` (#62 finding 1).

        ``_dispatch_doc_gap_agent`` persists the audit event BEFORE
        ``issue()`` writes ``command.issued``.  A crash in between leaves
        the record at ``archer_dispatched``/``prism_dispatched`` with a
        persisted ``design_dispatch_id`` but NO WAL command - decide()
        cannot advance (revision != prism_reviewed) and no outcome ever
        arrives, so the record parks permanently.  When the recorded
        ``design_dispatch_id`` was never issued, re-issue the nested
        dispatch with the SAME id (idempotent: an already-issued id is
        skipped by the ``_dispatch_issued`` guard).  No duplicate audit
        event is emitted (the original is already in the WAL).  When the
        persisted id is missing (corrupted WAL), emit a fresh audit event
        with a new id instead.  Returns True when a re-issue was issued;
        False when the dispatch was already issued (wait for the outcome).
        """
        dispatch_id = rec.get("design_dispatch_id")
        if dispatch_id and self._caps.dispatch_issued(dispatch_id):
            return False
        phase = (
            "design_revision"
            if revision == "archer_dispatched"
            else "design_review"
        )
        self._dispatch_doc_gap_agent(
            record_id, rec, state, phase=phase,
            dispatch_id=dispatch_id,
            emit_audit=not bool(dispatch_id),
        )
        return True

    def _dispatch_doc_gap_agent(
        self, record_id: str, rec: dict, state: State, *, phase: str,
        dispatch_id: str | None = None, emit_audit: bool = True,
    ) -> None:
        """Emit the SM-02 dispatch audit event (when ``emit_audit``) and
        issue the nested agent.

        The audit event ``doc_gap.design_dispatched`` is bound to the
        dispatch id via its envelope ``command_id`` so the WAL trail
        explicitly links the audit record to the ``command.issued`` it
        announces (the reducer also stores ``design_dispatch_id`` on the
        record for replay-side verification).

        Crash-window recovery (``emit_audit=False``): re-issue the nested
        dispatch with the SAME persisted ``design_dispatch_id`` after a
        crash between ``doc_gap.design_dispatched`` and ``command.issued``
        left the record parked at ``archer_dispatched``/
        ``prism_dispatched`` with no WAL command.  No duplicate audit
        event is emitted (the original is already in the WAL).
        """
        origin = rec.get("origin") or {}
        document_paths = rec.get("document_paths") or []
        role = "archer" if phase == "design_revision" else "prism"
        substate = "RESPOND" if phase == "design_revision" else "PRISM_REVIEW"
        cid = dispatch_id or new_ulid()
        audit_payload = {
            "record_id": record_id,
            "origin_dispatch_id": origin.get("dispatch_id", ""),
            "dispatch_id": cid,
            "phase": phase,
            "role": role,
            "document_paths": list(document_paths),
        }
        if emit_audit:
            # SM-02 (#62 finding 2): persist a nested Archer pre-dispatch
            # identity snapshot of the design trio so the checkpoint can
            # stage ONLY the design docs whose identity drifted during this
            # dispatch.  The snapshot covers the full DESIGN_DOCS trio (the
            # Archer revises the whole set, not just the adjudicated
            # document_paths); identity comparison attributes changes to the
            # Archer while Human / pre-dirty / unattributed content (unchanged
            # from the snapshot) stays out of the commit (AC-FR0236-01).
            if phase == "design_revision":
                audit_payload["pre_dispatch_identities"] = {
                    name: self._caps.path_identity(self._caps.doc_path(name))
                    for name in DESIGN_DOCS
                }
            self._caps.emit(
                "doc_gap.design_dispatched",
                audit_payload,
                command_id=cid,
            )
        cmd = Command(
            kind="dispatch_agent",
            params={
                "role": role,
                "substate": substate,
                "stage": state.stage,
                "doc_gap": {"record_id": record_id, "phase": phase},
                "assignment": {"kind": substate, "doc_gap_revision": True},
            },
        )
        self._caps.issue(cmd, command_id=cid)

    def _do_doc_gap_dispatch(
        self, cmd: Command, state: State, task_id: str | None, p: dict
    ) -> None:
        """Execute a doc-gap nested dispatch and checkpoint its outcome.

        Archer (design_revision): the backend revises the design docs; the
        executor commits the revised trio to git (advancing HEAD so the
        subsequent origin-stage Prism review sees a clean baseline) and
        checkpoints the revised combined design identity
        (``doc_gap.design_revised``) so the resume decision can detect
        design-stale discard.  No ``design.committed`` event is emitted
        (that clobbers origin stage state).  Prism (design_review): the
        backend reviews and the executor emits ``doc_gap.design_reviewed``
        (pass/revise).  Neither path emits ``design.committed`` /
        ``prism.verdict``.

        Fail-closed (#62 finding 1): ``doc_gap.design_revised`` is emitted
        ONLY when (a) the nested Archer outcome succeeded, (b) at least one
        authorized adjudicated design doc in ``document_paths`` changed
        identity during this dispatch, and (c) the git checkpoint commit
        succeeded.  Otherwise no event is emitted and the record stays at
        ``archer_dispatched`` so the resume gate never proceeds (the held
        origin outcome is never resumed on a failed/stale revision).
        """
        gap = p["doc_gap"]
        record_id = gap["record_id"]
        phase = gap["phase"]
        role = p["role"]
        substate = p["substate"]
        assignment = p.get("assignment") or {}
        print(
            f"  [{state.stage}] doc-gap {phase} dispatch {role}/{substate}",
            file=sys.stderr,
            flush=True,
        )
        result = self._caps.backend.act(
            role, substate, None, None, assignment=assignment
        )
        rec = state.doc_gaps.get(record_id) or {}
        document_paths = rec.get("document_paths") or []
        if phase == "design_revision":
            self._checkpoint_doc_gap_revision(
                cmd, task_id, record_id, rec, document_paths, result
            )
        elif phase == "design_review":
            if result.get("status") != "done":
                self._fail_doc_gap_revision(
                    cmd, task_id, record_id, phase,
                    f"nested design review outcome status={result.get('status')!r}",
                )
                return
            verdict = result.get("verdict")
            if verdict not in ("pass", "revise"):
                self._fail_doc_gap_revision(
                    cmd, task_id, record_id, phase,
                    f"invalid nested design review verdict={verdict!r}",
                )
                return
            self._caps.emit(
                "doc_gap.design_reviewed",
                {
                    "record_id": record_id,
                    "verdict": verdict,
                    "review_summary": result.get("review_summary", ""),
                },
                command_id=cmd.command_id,
                task_id=task_id,
            )

    def _checkpoint_doc_gap_revision(
        self,
        cmd: Command,
        task_id: str | None,
        record_id: str,
        rec: dict,
        document_paths: list[str],
        result: dict,
    ) -> None:
        """Fail-closed checkpoint of Archer's design revision (#62 finding 1).

        Emits ``doc_gap.design_revised`` ONLY when the nested Archer outcome
        succeeded, at least one adjudicated design doc in ``document_paths``
        changed identity during the dispatch, and the git checkpoint commit
        succeeded.  Otherwise no event is emitted (fail-closed): the record
        parks at ``archer_dispatched`` and the resume gate never proceeds,
        so a failed / no-op / un-attributable revision never resumes the
        held origin outcome.
        """
        phase = (cmd.params.get("doc_gap") or {}).get("phase", "design_revision")
        # (a) nested Archer outcome must be successful.
        if result.get("status") != "done":
            self._fail_doc_gap_revision(
                cmd, task_id, record_id, phase,
                f"nested {phase} outcome status={result.get('status')!r}",
            )
            return
        pre_identities = rec.get("pre_dispatch_identities") or {}
        # (b) at least one authorized adjudicated design doc (a record
        # document_path) changed identity during the dispatch.
        changed_document_paths = [
            name for name in document_paths
            if self._caps.path_identity(self._caps.doc_path(name))
            != pre_identities.get(name)
        ]
        if not changed_document_paths:
            self._fail_doc_gap_revision(
                cmd, task_id, record_id, phase,
                "no adjudicated design document changed",
            )
            return
        # Stage EVERY design doc whose identity drifted from the pre-dispatch
        # snapshot (the Archer revises the design trio; only identity-changed
        # docs are staged so Human / pre-dirty / unattributed content is
        # never committed as Archer output — #62 finding 2, AC-FR0236-01).
        changed_design_docs = [
            name for name in DESIGN_DOCS
            if self._caps.path_identity(self._caps.doc_path(name))
            != pre_identities.get(name)
        ]
        # (c) git checkpoint commit must succeed (raises on failure — never
        # swallowed, never emits success).
        try:
            head = self._commit_doc_gap_revision(changed_design_docs)
        except RuntimeError as exc:
            self._fail_doc_gap_revision(cmd, task_id, record_id, phase, str(exc))
            return
        pairs = [
            (name, self._caps.path_identity(self._caps.doc_path(name)))
            for name in document_paths
        ]
        revised_identity = combined_design_identity(pairs)
        self._caps.emit(
            "doc_gap.design_revised",
            {
                "record_id": record_id,
                "revised_design_identity": revised_identity,
                "changed_paths": changed_design_docs,
                "commit_sha": head,
            },
            command_id=cmd.command_id,
            task_id=task_id,
        )

    def _fail_doc_gap_revision(
        self, cmd, task_id, record_id: str, phase: str, reason: str
    ) -> None:
        """Close the nested WAL command without fabricating a revision."""
        self._caps.emit(
            "doc_gap.design_failed",
            {"record_id": record_id, "phase": phase, "reason": reason},
            command_id=cmd.command_id,
            task_id=task_id,
        )

    def _commit_doc_gap_revision(self, changed_paths: list[str]) -> str:
        """Commit ONLY the identity-changed design docs to git.

        SM-02 (#62 finding 2): stages only the ``changed_paths`` (design docs
        whose identity drifted from the nested Archer pre-dispatch snapshot)
        so Human / pre-dirty / unattributed design changes are never swept
        into the Archer commit (AC-FR0236-01 attribution).  Untouched docs
        and non-document dirty content stay uncommitted.

        #62 finding 3: a git commit failure is NOT swallowed (``check=True``
        raises) and the caller never emits ``doc_gap.design_revised`` on
        failure — no success is fabricated from a stale HEAD.
        """
        staged_any = False
        for name in changed_paths:
            path = self._caps.doc_path(name)
            if path.exists():
                git(self._caps.repo, "add", "--", str(path))
                staged_any = True
        if not staged_any:
            raise RuntimeError(
                "doc-gap design revision commit attempted with no staged "
                "design docs (changed_paths resolved to nothing on disk)"
            )
        proc = git(self._caps.repo, "diff", "--cached", "--name-only", check=False)
        if not proc.stdout.strip():
            raise RuntimeError(
                "doc-gap design revision commit attempted with an empty index"
            )
        commit_paths = [
            str(self._caps.doc_path(name).relative_to(self._caps.repo))
            for name in changed_paths
            if self._caps.doc_path(name).exists()
        ]
        git(
            self._caps.repo,
            "commit",
            "--only",
            "-m",
            "doc-gap design revision (SM-02 design_gap nested workflow)",
            "--",
            *commit_paths,
        )
        return git(self._caps.repo, "rev-parse", "HEAD").stdout.strip()

    # -- adjudication ingestion -------------------------------------------------

    def _ingest_doc_gap_adjudications(self, state: State) -> bool:
        """SM-02 adjudication ingestion (IF-DOCGAP-001, AC-FR0235-01/02).

        Scans every DETECTED doc-gap record's touched documents for Prism's
        structured SM-02-ADJUDICATION marker reply.  Exactly one valid,
        record-bound marker emits ``doc_comment.adjudicated`` and moves the
        record to DESIGN_GAP/AGENT_CORRECTION.  Anything malformed, ambiguous
        or mismatched stays fail-closed waiting (no event, no state change).
        Only waiting records are consulted, so replays and re-reads of the
        same comment can never re-emit the adjudication.
        """
        if state.status != "active" or not state.doc_gaps:
            return False
        return any(
            self._ingest_record_adjudication(record_id)
            for record_id in sorted(state.doc_gaps)
        )

    def _ingest_record_adjudication(self, record_id: str) -> bool:
        """Ingest one waiting doc-gap record's marker; True when adjudicated."""
        state = self._caps.store.state(self._caps.run_id)
        rec = state.doc_gaps.get(record_id)
        if rec is None or rec.get("state") != "DETECTED":
            return False
        candidates: list[AdjudicationMarker] = []
        errors: list[tuple[str, str]] = []
        for name in rec.get("document_paths") or []:
            path = self._caps.doc_path(name)
            if not path.exists():
                continue
            markers, doc_errors = scan_adjudication_markers(
                path.read_text(encoding="utf-8", errors="replace")
            )
            candidates.extend(markers)
            errors.extend(doc_errors)
        # Relevance filtering (history vs this record) happens inside the
        # matcher, BEFORE any ambiguity decision.
        marker, error = match_adjudication_marker(
            candidates,
            errors,
            quarantine_id=rec.get("quarantine_id"),
            thread_ids=rec.get("thread_ids"),
            origin_role=(rec.get("origin") or {}).get("role"),
        )
        # Fail-closed: a malformed/mismatched marker never drives SM-02; the
        # outcome keeps waiting for a legal adjudication.
        if error is not None or marker is None:
            return False
        origin = rec.get("origin") or {}
        self._caps.emit(
            "doc_comment.adjudicated",
            {
                "record_id": record_id,
                "quarantine_id": rec.get("quarantine_id"),
                "origin_dispatch_id": origin.get("dispatch_id", ""),
                "route": marker.route,
                "responsible_role": marker.responsible_role,
                "thread_ids": list(marker.thread_ids),
                "decision_ref": marker.decision_ref,
            },
        )
        return True
