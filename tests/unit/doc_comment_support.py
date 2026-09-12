"""Shared doc-comment event and executor fixtures."""

from __future__ import annotations

from tracks.executor.doc_comment import (
    AdjudicationMarker,
    QuarantinedChange,
    QuarantineDescriptor,
    ResumeDecision,
    decide_quarantine_resume,
)

_THREAD = "> **Shield:** Design gap discussion.\n"


_NESTED_MARKER = (
    ">> **Prism:** SM-02-ADJUDICATION | route=design_gap"
    " | responsible_role=archer | quarantine_id=q-abc123 | threads=T-001\n"
)


def _doc(marker: str = _NESTED_MARKER, thread: str = _THREAD) -> str:
    return f"# Plan\n\n{thread}{marker}"


def _descriptor(
    changes: tuple[QuarantinedChange, ...] = (),
    status: str = "held",
    design_identity: str = "design-a",
    run_identity: str = "run-a",
) -> QuarantineDescriptor:
    return QuarantineDescriptor(
        quarantine_id="q-abc123",
        origin={},
        design_identity=design_identity,
        run_identity=run_identity,
        changes=changes,
        manifest_ref="ref",
        manifest_sha256="ref",
        status=status,
    )


def _marker(host_thread_id: str = "T-001", **overrides) -> AdjudicationMarker:
    values: dict = {
        "route": "design_gap",
        "responsible_role": "archer",
        "quarantine_id": "q-abc123",
        "thread_ids": ("T-001",),
        "host_thread_id": host_thread_id,
        "decision_ref": "d",
    }
    values.update(overrides)
    return AdjudicationMarker(**values)


def _resume(descriptor, *, design="design-a", run="run-a", paths=None):
    decision = decide_quarantine_resume(
        descriptor,
        current_design_identity=design,
        current_run_identity=run,
        current_path_identities=paths or {},
        next_dispatch_id="next-1",
        next_attempt=2,
    )
    assert isinstance(decision, ResumeDecision)
    assert decision.next_dispatch_id == "next-1"
    assert decision.next_attempt == 2
    return decision


_HELD_CHANGE = QuarantinedChange(
    path="src/x.py", operation="modify",
    baseline_identity="b", content_identity="c1",
)


def _project(events):
    """Project a list of (type, payload) tuples into a State's doc_gaps."""
    from tracks.kernel.events import EventEnvelope
    from tracks.kernel.machine import State, apply

    s = State()
    for i, (etype, payload) in enumerate(events):
        ev = EventEnvelope(
            seq=i, ts="", run_id="r", version="v", type=etype,
            schema_version=1, command_id=None, task_id=None, payload=payload,
        )
        apply(s, ev)
    return s


def _doc_gap_crash_window_events(*, dispatch_id, phase="design_revision",
                                 with_command_issued=False):
    """Event log up to the crash window: detected + adjudicated(design_gap) +
    design_dispatched audit event persisted, with or without the matching
    command.issued (the crash point).

    Returns a list of ``(type, payload, command_id)`` tuples so the
    ``command.issued`` envelope carries the dispatch id (matching the real
    WAL write); audit events carry their dispatch id too (bound by the
    executor's ``_emit(command_id=...)``).
    """
    events = [
        ("doc_comment.detected", {
            "record_id": "dg-1",
            "origin": {"role": "shield", "dispatch_id": "ORIG-1"},
            "document_paths": ["test-plan.md"],
            "thread_ids": ["T-001"],
        }, None),
        ("doc_comment.adjudicated", {
            "record_id": "dg-1", "route": "design_gap",
            "responsible_role": "archer", "decision_ref": "abc",
        }, None),
        ("doc_gap.design_dispatched", {
            "record_id": "dg-1", "dispatch_id": dispatch_id,
            "phase": phase, "role": "archer" if phase == "design_revision" else "prism",
            "document_paths": ["test-plan.md"],
        }, dispatch_id),
    ]
    if with_command_issued:
        events.append((
            "command.issued",
            {"command": {"kind": "dispatch_agent", "params": {}, "command_id": dispatch_id}},
            dispatch_id,
        ))
    return events


def _doc_gap_executor(repo, events, monkeypatch):
    """Build an Executor seeded with ``events`` (3-tuples of type, payload,
    command_id) and a stub ``issue`` that records calls without executing the
    backend / git pipeline."""
    from tracks import paths
    from tracks.executor.executor import Executor
    from tracks.store import Store

    store = Store(paths.tracks_home(repo))
    for t, p, cid in events:
        store.append("RUN", "v0.5", t, p, command_id=cid)
    issued_calls = []

    def _recording_issue(cmd, command_id=None):
        issued_calls.append({"command_id": command_id, "params": dict(cmd.params)})
        # Persist a command.issued so _dispatch_issued can observe it on
        # subsequent iterations (mirrors the real WAL write).
        store.append(
            "RUN", "v0.5", "command.issued",
            {"command": {"kind": cmd.kind, "params": dict(cmd.params), "command_id": command_id}},
            command_id=command_id,
        )

    class RecordingExecutor(Executor):
        issue = staticmethod(_recording_issue)

    ex = RecordingExecutor(store, repo, "RUN")
    return ex, store, issued_calls


def _design_dispatched_with_pre_identity(*, dispatch_id, pre_identities):
    """Event log: story.requested + detected + adjudicated(design_gap) +
    design_dispatched carrying the nested Archer pre-dispatch identity
    snapshot (over the full design trio).  ``story.requested`` seeds
    ``state.version`` so ``_doc_path`` resolves to the v0.5 version dir the
    checkpoint writes/reads."""
    return [
        ("story.requested", {}, None),
        ("doc_comment.detected", {
            "record_id": "dg-1",
            "origin": {"role": "shield", "dispatch_id": "ORIG-1"},
            "document_paths": ["test-plan.md"],
            "thread_ids": ["T-001"],
        }, None),
        ("doc_comment.adjudicated", {
            "record_id": "dg-1", "route": "design_gap",
            "responsible_role": "archer", "decision_ref": "abc",
        }, None),
        ("doc_gap.design_dispatched", {
            "record_id": "dg-1", "dispatch_id": dispatch_id,
            "phase": "design_revision", "role": "archer",
            "document_paths": ["test-plan.md"],
            "pre_dispatch_identities": pre_identities,
        }, dispatch_id),
    ]


def _checkpoint_executor(tmp_path, *, pre_identities, docs=None):
    """Build an Executor seeded with a DESIGN_GAP record whose
    pre_dispatch_identities snapshot is *pre_identities* (over the design
    trio), with the given *docs* ({name: text}) written to the v0.5 version
    dir.  Returns (ex, store, repo)."""
    from tests.unit.helpers import git_repo as _repo

    repo = _repo(tmp_path)
    ex, store, _ = _doc_gap_executor(
        repo,
        _design_dispatched_with_pre_identity(
            dispatch_id="D-ARCHER", pre_identities=pre_identities,
        ),
        None,
    )
    # Write the design docs the checkpoint reads (_doc_path -> v0.5 dir).
    from tracks import paths

    vdir = paths.version_dir(paths.tracks_home(repo), "v0.5")
    vdir.mkdir(parents=True, exist_ok=True)
    for name, text in (docs or {"test-plan.md": "plan v1\n"}).items():
        (vdir / name).write_text(text, encoding="utf-8")
    return ex, store, repo


def _cmd(cid="D-ARCHER"):
    from tracks.kernel.events import Command

    return Command(
        kind="dispatch_agent",
        params={"doc_gap": {"record_id": "dg-1", "phase": "design_revision"}},
        command_id=cid,
    )
