"""Behavior coverage for the SM-02 doc-gap lifecycle runtime (doc_gap_runtime).

Drives the DocGapRuntime through its narrow DocGapCapabilities facade:
capture -> classify (illegal_body_edit > legal_discussion > ordinary) ->
atomic rollback / legal-change quarantine + pause -> nested Archer/Prism
design revision with WAL crash-window recovery -> adjudication ingestion.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.unit.helpers import git as _git
from tests.unit.helpers import git_repo
from tracks.executor.doc_comment import (
    DocCommentOrigin,
    combined_design_identity,
    create_doc_gap_record,
    legal_anchor_pairs,
)
from tracks.executor.doc_gap_runtime import (
    DocGapCapabilities,
    DocGapRuntime,
    legal_delta_targets,
)
from tracks.kernel.events import Command
from tracks.kernel.machine import DESIGN_DOCS, State


class _Store:
    def __init__(self, state: State | None = None, blob_ref: str | None = "blob-ref"):
        self._state = state or State(run_id="RUN")
        self.blob_ref = blob_ref
        self.audit_blobs: list[dict] = []

    def state(self, run_id: str) -> State:
        return self._state

    def events(self, run_id: str):
        return []

    def write_audit_blob(self, payload):
        self.audit_blobs.append(payload)
        return self.blob_ref

    def load_payload(self, payload):
        return payload


class _Backend:
    def __init__(self, result: dict | None = None):
        self.result = {"status": "done"} if result is None else result
        self.calls: list[dict] = []

    def act(self, role, substate, doc, doc_path, assignment=None, worktree=None):
        self.calls.append(
            {
                "role": role,
                "substate": substate,
                "doc": doc,
                "doc_path": doc_path,
                "assignment": assignment,
                "worktree": worktree,
            }
        )
        return self.result


@dataclass
class _Ctx:
    runtime: DocGapRuntime
    repo: Path
    docs: Path
    emitted: list = field(default_factory=list)
    issued: list = field(default_factory=list)
    store: _Store = field(default_factory=_Store)
    backend: _Backend = field(default_factory=_Backend)
    dirty: dict = field(default_factory=dict)
    issued_ids: set = field(default_factory=set)

    def emit_type(self, event_type: str) -> list[tuple[dict, dict]]:
        return [
            (payload, kwargs)
            for name, payload, kwargs in self.emitted
            if name == event_type
        ]


def _make_ctx(
    tmp_path: Path,
    *,
    state: State | None = None,
    dirty: dict | None = None,
    layout=("tests/",),
    result: dict | None = None,
    blob_ref: str | None = "blob-ref",
    repo: Path | None = None,
    docs: Path | None = None,
) -> _Ctx:
    state = state or State(run_id="RUN")
    repo = repo or (tmp_path / "repo")
    repo.mkdir(parents=True, exist_ok=True)
    docs = docs or (tmp_path / "docs")
    docs.mkdir(parents=True, exist_ok=True)
    store = _Store(state, blob_ref)
    backend = _Backend(result)
    emitted: list = []
    issued: list = []
    issued_ids: set = set()
    dirty_map = {} if dirty is None else dirty

    def _identity(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "missing"

    caps = DocGapCapabilities(
        repo=repo,
        run_id="RUN",
        store=store,
        backend=backend,
        emit=lambda event, payload=None, **kw: emitted.append((event, payload, kw)),
        issue=lambda cmd, command_id=None: issued.append((cmd, command_id)),
        doc_path=lambda name: docs / name,
        path_identity=_identity,
        dirty_snapshot=lambda: dict(dirty_map),
        dispatch_issued=lambda cid: cid in issued_ids,
        layout_paths=lambda repo_path, role: layout,
    )
    return _Ctx(
        runtime=DocGapRuntime(caps),
        repo=repo,
        docs=docs,
        emitted=emitted,
        issued=issued,
        store=store,
        backend=backend,
        dirty=dirty_map,
        issued_ids=issued_ids,
    )


def _command(command_id: str = "D-ORIG", **params) -> Command:
    return Command(kind="dispatch_agent", params=dict(params), command_id=command_id)


def _legal_delta(path: str = "architecture.md"):
    return SimpleNamespace(
        path=path,
        baseline_identity="base",
        current_identity="current",
        classification="legal_discussion",
        new_thread_ids=("T-001",),
    )


# ---------------------------------------------------------------------------
# capture
# ---------------------------------------------------------------------------


def test_legal_delta_targets_flattens_paths_and_threads():
    deltas = [
        SimpleNamespace(path="a.md", new_thread_ids=("T-001", "T-002")),
        SimpleNamespace(path="b.md", new_thread_ids=()),
    ]
    assert legal_delta_targets(deltas) == (["a.md", "b.md"], ["T-001", "T-002"])


def test_snapshot_design_docs_reads_allowed_missing_as_empty(tmp_path: Path):
    ctx = _make_ctx(tmp_path)
    (ctx.docs / "architecture.md").write_text("arch", encoding="utf-8")
    snapshot = ctx.runtime._snapshot_design_docs("devon")
    assert snapshot == {"architecture.md": b"arch", "interfaces.md": b""}
    assert ctx.runtime._snapshot_design_docs("archer") == {}


def test_capture_doc_gap_context_scope_is_role_only(tmp_path: Path):
    ctx = _make_ctx(tmp_path)
    ctx.dirty["x.py"] = "id"
    state = State(run_id="RUN", stage="M-TEST")
    for role in ("devon", "shield"):
        captured = ctx.runtime._capture_doc_gap_context(state, role)
        assert captured is not None
        assert captured[1] == {"x.py": "id"}
    assert ctx.runtime._capture_doc_gap_context(state, "archer") is None
    assert ctx.runtime._capture_doc_gap_context(state, "prism") is None


def test_doc_gap_origin_binds_attempt_and_dispatch(tmp_path: Path):
    ctx = _make_ctx(tmp_path)
    state = State(
        run_id="RUN", current_task_id="T-1", substate="WRITE", current_attempt=2
    )
    origin = ctx.runtime._doc_gap_origin(state, "devon", "RED", _command(attempt=7))
    assert origin == DocCommentOrigin(
        run_id="RUN", role="devon", task_id="T-1", phase="RED", dispatch_id="D-ORIG", attempt=7
    )
    fallback = ctx.runtime._doc_gap_origin(state, "devon", "RED", _command())
    assert fallback.attempt == 3


# ---------------------------------------------------------------------------
# classify + route
# ---------------------------------------------------------------------------


def _docs_case(ctx: _Ctx, baseline: str, current: str, name: str = "architecture.md"):
    path = ctx.docs / name
    path.write_text(current, encoding="utf-8")
    return {name: baseline.encode()}


def test_handle_doc_gap_outcome_none_and_ordinary_pass_through(tmp_path: Path):
    ctx = _make_ctx(tmp_path)
    state = State(run_id="RUN", substate="RED")
    cmd = _command()
    assert (
        ctx.runtime._handle_doc_gap_outcome(cmd, state, "T-1", "devon", "RED", {}, None)
        is False
    )
    baseline = _docs_case(ctx, "# Architecture\n", "# Architecture\n")
    assert (
        ctx.runtime._handle_doc_gap_outcome(
            cmd, state, "T-1", "devon", "RED", {}, (baseline, {})
        )
        is False
    )
    assert ctx.emitted == []


def test_illegal_body_edit_rejects_and_rolls_back(tmp_path: Path):
    ctx = _make_ctx(tmp_path)
    state = State(
        run_id="RUN", stage="M-IMPL", substate="RED", current_task_id="T-1"
    )
    baseline = _docs_case(ctx, "# Architecture\n", "# Architecture\nAgent wrote here\n")
    handled = ctx.runtime._handle_doc_gap_outcome(
        _command(), state, "T-1", "devon", "RED", {}, (baseline, {})
    )
    assert handled is True
    assert (ctx.docs / "architecture.md").read_text(encoding="utf-8") == "# Architecture\n"
    received = ctx.emit_type("outcome.received")[0][0]
    assert received["status"] == "rejected"
    assert received["failure_class"] == "over_reach"
    assert received["role"] == "devon"
    rejected = ctx.emit_type("outcome.rejected")[0][0]
    assert rejected["rollback"] == "atomic"
    assert rejected["rejected_paths"] == ["architecture.md"]
    assert rejected["origin"]["role"] == "devon"
    assert rejected["origin"]["dispatch_id"] == "D-ORIG"


def test_illegal_body_edit_precedes_legal_discussion(tmp_path: Path):
    ctx = _make_ctx(tmp_path)
    state = State(run_id="RUN", substate="RED")
    current = "# Architecture\nAgent body\n\n> **Shield:** new discussion.\n"
    baseline = _docs_case(ctx, "# Architecture\n", current)
    assert (
        ctx.runtime._handle_doc_gap_outcome(
            _command(), state, "T-1", "devon", "RED", {}, (baseline, {})
        )
        is True
    )
    assert ctx.emit_type("outcome.rejected")[0][0]["failure_class"] == "over_reach"
    assert ctx.emit_type("outcome.quarantined") == []
    assert ctx.emit_type("outcome.received")[0][0]["status"] == "rejected"


def test_legal_discussion_pauses_with_quarantine(tmp_path: Path):
    ctx = _make_ctx(tmp_path)
    ctx.dirty["tests/test_x.py"] = "id"
    state = State(
        run_id="RUN",
        stage="M-IMPL",
        substate="RED",
        current_task_id="T-1",
        current_attempt=1,
    )
    baseline = _docs_case(
        ctx, "# Architecture\n", "# Architecture\n\n> **Shield:** new discussion.\n"
    )
    handled = ctx.runtime._handle_doc_gap_outcome(
        _command(), state, "T-1", "devon", "RED", {}, (baseline, {})
    )
    assert handled is True
    assert ctx.emit_type("outcome.received") == []
    detected = ctx.emit_type("doc_comment.detected")[0][0]
    quarantined = ctx.emit_type("outcome.quarantined")[0][0]
    assert detected["record_id"] == quarantined["record_id"]
    assert detected["document_paths"] == ["architecture.md"]
    assert detected["thread_ids"] == ["T-001"]
    assert detected["origin"]["phase"] == "RED"
    assert quarantined["status"] == "held"
    assert ctx.store.audit_blobs[0]["run_identity"] == "RUN"
    assert ctx.store.audit_blobs[0]["design_identity"]


def test_reply_to_existing_thread_is_ordinary(tmp_path: Path):
    ctx = _make_ctx(tmp_path)
    state = State(run_id="RUN", substate="WRITE")
    thread = "> **Shield:** existing discussion.\n"
    baseline = _docs_case(
        ctx,
        f"# Test plan\n{thread}",
        f"# Test plan\n{thread}>> **Prism:** reply.\n",
        name="test-plan.md",
    )
    assert (
        ctx.runtime._handle_doc_gap_outcome(
            _command(), state, "T-1", "shield", "WRITE", {}, (baseline, {})
        )
        is False
    )
    assert ctx.emitted == []


def test_rollback_doc_gap_round_preserves_predirty_and_removes_untracked(tmp_path: Path):
    repo = git_repo(tmp_path)
    (repo / "tracked.txt").write_text("committed\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "add tracked")
    (repo / "tracked.txt").write_text("modified\n", encoding="utf-8")
    (repo / "untracked.txt").write_text("new\n", encoding="utf-8")
    ctx = _make_ctx(tmp_path, repo=repo, docs=repo)
    ctx.dirty.update({"tracked.txt": "id1", "untracked.txt": "id2"})
    rejected = ctx.runtime._rollback_doc_gap_round(
        {"architecture.md": b"restored"}, {"tracked.txt": "pre"}
    )
    assert rejected == ["architecture.md", "untracked.txt"]
    assert (repo / "architecture.md").read_bytes() == b"restored"
    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "modified\n"
    assert not (repo / "untracked.txt").exists()


def test_rollback_restores_agent_modified_tracked_file(tmp_path: Path):
    """A tracked file modified by the agent rolls back to its committed bytes
    and stays in the worktree -- no `D path` (interfaces §1k)."""
    repo = git_repo(tmp_path)
    (repo / "tracked.txt").write_text("committed\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "add tracked")
    (repo / "tracked.txt").write_text("agent edit\n", encoding="utf-8")
    ctx = _make_ctx(tmp_path, repo=repo, docs=repo)
    ctx.dirty.update({"tracked.txt": "id1"})
    rejected = ctx.runtime._rollback_doc_gap_round({}, {})
    assert rejected == ["tracked.txt"]
    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "committed\n"
    status = _git(repo, "status", "--porcelain", "--", "tracked.txt").stdout
    assert status == ""


def test_rollback_restores_agent_deleted_tracked_file(tmp_path: Path):
    """An agent-deleted tracked file is restored from HEAD, not left deleted
    (interfaces §1k)."""
    repo = git_repo(tmp_path)
    (repo / "tracked.txt").write_text("committed\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "add tracked")
    (repo / "tracked.txt").unlink()
    ctx = _make_ctx(tmp_path, repo=repo, docs=repo)
    ctx.dirty.update({"tracked.txt": "missing"})
    rejected = ctx.runtime._rollback_doc_gap_round({}, {})
    assert rejected == ["tracked.txt"]
    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "committed\n"
    status = _git(repo, "status", "--porcelain", "--", "tracked.txt").stdout
    assert status == ""


def test_rollback_removes_agent_created_untracked_file(tmp_path: Path):
    """A file the agent created (absent pre-dispatch) is removed; nothing
    tracked is left modified."""
    repo = git_repo(tmp_path)
    (repo / "agent-new.txt").write_text("new\n", encoding="utf-8")
    ctx = _make_ctx(tmp_path, repo=repo, docs=repo)
    ctx.dirty.update({"agent-new.txt": "id1"})
    rejected = ctx.runtime._rollback_doc_gap_round({}, {})
    assert rejected == ["agent-new.txt"]
    assert not (repo / "agent-new.txt").exists()
    status = _git(repo, "status", "--porcelain", "-uall").stdout
    assert status == ""


# ---------------------------------------------------------------------------
# quarantine manifest
# ---------------------------------------------------------------------------


def _record(tmp_path: Path, ctx: _Ctx):
    origin = DocCommentOrigin(
        run_id="RUN", role="devon", task_id="T-1", phase="RED", dispatch_id="D", attempt=1
    )
    delta = _legal_delta()
    return create_doc_gap_record(origin=origin, deltas=(delta,), quarantine_id=None)


def test_quarantine_uses_manifest_allowed_paths(tmp_path: Path):
    state = State(
        run_id="RUN",
        current_task_metadata={"manifest": {"allowed_paths": ["src/"]}},
    )
    ctx = _make_ctx(tmp_path, state=state)
    ctx.dirty.update({"src/x.py": "id1", "architecture.md": "id2", "other.py": "id3"})
    record = _record(tmp_path, ctx)
    descriptor, manifest_ref = ctx.runtime._quarantine_legal_changes(
        state, record.origin, record, {"src/x.py": "pre"}
    )
    assert descriptor.status == "empty"
    assert descriptor.changes == ()
    assert manifest_ref == "blob-ref"
    assert ctx.store.audit_blobs[0]["status"] == "empty"

    ctx.dirty["src/x.py"] = "id1"
    descriptor, _ = ctx.runtime._quarantine_legal_changes(
        state, record.origin, record, {}
    )
    assert [c.path for c in descriptor.changes] == ["src/x.py"]
    assert descriptor.status == "held"
    assert descriptor.design_identity == combined_design_identity(
        list(legal_anchor_pairs(record.document_deltas))
    )


def test_quarantine_falls_back_to_layout_paths(tmp_path: Path):
    ctx = _make_ctx(tmp_path, layout=("tests/",))
    state = State(run_id="RUN")
    ctx.dirty.update({"tests/test_x.py": "id1", "other.py": "id2", "interfaces.md": "id3"})
    record = _record(tmp_path, ctx)
    descriptor, _ = ctx.runtime._quarantine_legal_changes(
        state, record.origin, record, {}
    )
    assert [c.path for c in descriptor.changes] == ["tests/test_x.py"]
    assert descriptor.status == "held"


def test_emit_doc_gap_pause_falls_back_to_descriptor_manifest_ref(tmp_path: Path):
    ctx = _make_ctx(tmp_path, blob_ref=None)
    state = State(run_id="RUN", substate="WRITE")
    ctx.dirty["tests/test_x.py"] = "id1"
    record = _record(tmp_path, ctx)
    descriptor, manifest_ref = ctx.runtime._quarantine_legal_changes(
        state, record.origin, record, {}
    )
    assert manifest_ref is None
    ctx.runtime._emit_doc_gap_pause(
        _command(), "T-1", record.origin, [record.document_deltas[0]], record, descriptor, None
    )
    payload = ctx.emit_type("outcome.quarantined")[0][0]
    assert payload["manifest_ref"] == descriptor.manifest_ref
    assert payload["manifest_sha256"] == descriptor.manifest_sha256


# ---------------------------------------------------------------------------
# nested design revision dispatch
# ---------------------------------------------------------------------------


def _design_gap_state(revision, **record_extra) -> State:
    record = {
        "state": "DESIGN_GAP",
        "origin": {"dispatch_id": "ORIG", "role": "shield"},
        "document_paths": ["architecture.md"],
        "revision": revision,
    }
    record.update(record_extra)
    return State(run_id="RUN", stage="M-IMPL", substate="WRITE", doc_gaps={"dg-1": record})


def test_advance_design_revision_guard_clauses(tmp_path: Path):
    ctx = _make_ctx(tmp_path)
    assert ctx.runtime._advance_doc_gap_design_revision(State(run_id="RUN", status="completed")) is False
    assert ctx.runtime._advance_doc_gap_design_revision(State(run_id="RUN")) is False
    detected = State(
        run_id="RUN", doc_gaps={"dg-1": {"state": "DETECTED", "revision": None}}
    )
    assert ctx.runtime._advance_doc_gap_design_revision(detected) is False


def test_advance_design_revision_dispatches_archer_with_snapshot(tmp_path: Path):
    ctx = _make_ctx(tmp_path)
    (ctx.docs / "architecture.md").write_text("arch", encoding="utf-8")
    state = _design_gap_state(None)
    assert ctx.runtime._advance_doc_gap_design_revision(state) is True
    assert len(ctx.issued) == 1
    cmd, cid = ctx.issued[0]
    assert cmd.kind == "dispatch_agent"
    assert cmd.params["role"] == "archer"
    assert cmd.params["substate"] == "RESPOND"
    assert cmd.params["stage"] == "M-IMPL"
    assert cmd.params["doc_gap"] == {"record_id": "dg-1", "phase": "design_revision"}
    assert cmd.params["assignment"]["doc_gap_revision"] is True
    audit = ctx.emit_type("doc_gap.design_dispatched")[0]
    assert audit[0]["dispatch_id"] == cid
    assert set(audit[0]["pre_dispatch_identities"]) == set(DESIGN_DOCS)
    assert audit[1]["command_id"] == cid


def test_advance_design_revision_dispatches_prism_without_snapshot(tmp_path: Path):
    ctx = _make_ctx(tmp_path)
    state = _design_gap_state("design_revised")
    assert ctx.runtime._advance_doc_gap_design_revision(state) is True
    cmd, cid = ctx.issued[0]
    assert cmd.params["role"] == "prism"
    assert cmd.params["substate"] == "PRISM_REVIEW"
    audit = ctx.emit_type("doc_gap.design_dispatched")[0]
    assert "pre_dispatch_identities" not in audit[0]
    assert audit[1]["command_id"] == cid


def test_advance_design_revision_waits_for_issued_dispatch(tmp_path: Path):
    ctx = _make_ctx(tmp_path)
    ctx.issued_ids.add("D-1")
    state = _design_gap_state(
        "archer_dispatched", design_dispatch_id="D-1"
    )
    assert ctx.runtime._advance_doc_gap_design_revision(state) is False
    assert ctx.issued == []


def test_advance_design_revision_recovers_unissued_archer_dispatch(tmp_path: Path):
    ctx = _make_ctx(tmp_path)
    (ctx.docs / "architecture.md").write_text("arch", encoding="utf-8")
    state = _design_gap_state("archer_dispatched", design_dispatch_id="D-1")
    assert ctx.runtime._advance_doc_gap_design_revision(state) is True
    cmd, cid = ctx.issued[0]
    assert cid == "D-1"
    assert cmd.params["doc_gap"]["phase"] == "design_revision"
    assert ctx.emit_type("doc_gap.design_dispatched") == []


def test_advance_design_revision_recovers_unissued_prism_dispatch(tmp_path: Path):
    ctx = _make_ctx(tmp_path)
    state = _design_gap_state("prism_dispatched", design_dispatch_id="D-2")
    assert ctx.runtime._advance_doc_gap_design_revision(state) is True
    cmd, cid = ctx.issued[0]
    assert cid == "D-2"
    assert cmd.params["doc_gap"]["phase"] == "design_review"


def test_advance_design_revision_prism_reviewed_parks(tmp_path: Path):
    ctx = _make_ctx(tmp_path)
    state = _design_gap_state("prism_reviewed")
    assert ctx.runtime._advance_doc_gap_design_revision(state) is False
    assert ctx.issued == []


def test_advance_design_revision_skips_non_design_gap_records(tmp_path: Path):
    ctx = _make_ctx(tmp_path)
    state = State(
        run_id="RUN",
        doc_gaps={
            "a": {"state": "DETECTED", "revision": None},
            "b": {"state": "DESIGN_GAP", "revision": None, "origin": {}, "document_paths": []},
        },
    )
    assert ctx.runtime._advance_doc_gap_design_revision(state) is True
    assert len(ctx.issued) == 1


def test_recover_missing_dispatch_id_emits_new_audit(tmp_path: Path):
    ctx = _make_ctx(tmp_path)
    (ctx.docs / "architecture.md").write_text("arch", encoding="utf-8")
    state = _design_gap_state("archer_dispatched")
    assert (
        ctx.runtime._recover_doc_gap_dispatch("dg-1", state.doc_gaps["dg-1"], state, "archer_dispatched")
        is True
    )
    audit = ctx.emit_type("doc_gap.design_dispatched")[0]
    _cmd, cid = ctx.issued[0]
    assert audit[0]["dispatch_id"] == cid
    assert audit[0]["dispatch_id"] != ""


# ---------------------------------------------------------------------------
# nested outcome checkpointing
# ---------------------------------------------------------------------------


def _dispatch_params(phase: str = "design_revision") -> dict:
    return {
        "role": "archer" if phase == "design_revision" else "prism",
        "substate": "RESPOND" if phase == "design_revision" else "PRISM_REVIEW",
        "doc_gap": {"record_id": "dg-1", "phase": phase},
        "assignment": {"kind": "RESPOND", "doc_gap_revision": True},
    }


def _run_dispatch(tmp_path: Path, *, result, docs: dict, pre: dict | None = None, phase="design_revision"):
    ctx = _make_ctx(tmp_path, result=result)
    for name, text in docs.items():
        (ctx.docs / name).write_text(text, encoding="utf-8")
    state = State(
        run_id="RUN",
        stage="M-IMPL",
        substate="RESPOND",
        doc_gaps={
            "dg-1": {
                "document_paths": ["architecture.md"],
                "pre_dispatch_identities": pre if pre is not None else {},
            }
        },
    )
    cmd = _command("D-CMD", **{})
    ctx.runtime._do_doc_gap_dispatch(cmd, state, "T-1", _dispatch_params(phase))
    return ctx


def test_do_doc_gap_dispatch_design_revision_checkpoints(tmp_path: Path):
    ctx = _make_ctx(
        tmp_path,
        result={"status": "done"},
    )
    (ctx.docs / "architecture.md").write_text("revised", encoding="utf-8")
    (ctx.docs / "interfaces.md").write_text("stable", encoding="utf-8")
    identity = ctx.runtime._caps.path_identity(ctx.docs / "architecture.md")
    state = State(
        run_id="RUN",
        doc_gaps={
            "dg-1": {
                "document_paths": ["architecture.md"],
                "pre_dispatch_identities": {
                    "architecture.md": "old",
                    "interfaces.md": ctx.runtime._caps.path_identity(ctx.docs / "interfaces.md"),
                    "test-plan.md": ctx.runtime._caps.path_identity(ctx.docs / "test-plan.md"),
                },
            }
        },
    )
    ctx.runtime._commit_doc_gap_revision = lambda changed: "commit-sha"
    ctx.runtime._do_doc_gap_dispatch(
        _command(), state, "T-1", _dispatch_params("design_revision")
    )
    assert ctx.backend.calls[0]["role"] == "archer"
    assert ctx.backend.calls[0]["assignment"] == {"kind": "RESPOND", "doc_gap_revision": True}
    revised = ctx.emit_type("doc_gap.design_revised")[0][0]
    assert revised["record_id"] == "dg-1"
    assert revised["changed_paths"] == ["architecture.md"]
    assert revised["commit_sha"] == "commit-sha"
    assert revised["revised_design_identity"] == combined_design_identity(
        [("architecture.md", identity)]
    )


def test_checkpoint_fails_when_archer_status_not_done(tmp_path: Path):
    ctx = _run_dispatch(
        tmp_path,
        result={"status": "failed"},
        docs={"architecture.md": "revised"},
        pre={"architecture.md": "old"},
    )
    failed = ctx.emit_type("doc_gap.design_failed")[0][0]
    assert "nested design_revision outcome status" in failed["reason"]
    assert ctx.emit_type("doc_gap.design_revised") == []


def test_checkpoint_fails_when_no_adjudicated_doc_changed(tmp_path: Path):
    ctx = _make_ctx(tmp_path, result={"status": "done"})
    (ctx.docs / "architecture.md").write_text("unchanged", encoding="utf-8")
    identity = ctx.runtime._caps.path_identity(ctx.docs / "architecture.md")
    state = State(
        run_id="RUN",
        doc_gaps={
            "dg-1": {
                "document_paths": ["architecture.md"],
                "pre_dispatch_identities": {"architecture.md": identity},
            }
        },
    )
    ctx.runtime._do_doc_gap_dispatch(
        _command(), state, "T-1", _dispatch_params("design_revision")
    )
    failed = ctx.emit_type("doc_gap.design_failed")[0][0]
    assert failed["reason"] == "no adjudicated design document changed"


def test_checkpoint_fails_when_commit_raises(tmp_path: Path):
    ctx = _make_ctx(tmp_path, result={"status": "done"})
    (ctx.docs / "architecture.md").write_text("revised", encoding="utf-8")
    state = State(
        run_id="RUN",
        doc_gaps={
            "dg-1": {
                "document_paths": ["architecture.md"],
                "pre_dispatch_identities": {"architecture.md": "old"},
            }
        },
    )

    def _boom(_changed):
        raise RuntimeError("commit refused")

    ctx.runtime._commit_doc_gap_revision = _boom
    ctx.runtime._do_doc_gap_dispatch(
        _command(), state, "T-1", _dispatch_params("design_revision")
    )
    failed = ctx.emit_type("doc_gap.design_failed")[0][0]
    assert failed["reason"] == "commit refused"


def test_design_review_outcome_validation(tmp_path: Path):
    for result, fragment in (
        ({"status": "failed"}, "nested design review outcome status"),
        ({"status": "done", "verdict": "maybe"}, "invalid nested design review verdict"),
    ):
        ctx = _run_dispatch(
            tmp_path / fragment[:10].replace(" ", "_"),
            result=result,
            docs={},
            phase="design_review",
        )
        failed = ctx.emit_type("doc_gap.design_failed")[0][0]
        assert fragment in failed["reason"]
        assert ctx.emit_type("doc_gap.design_reviewed") == []


def test_design_review_pass_and_revise_emit_reviewed(tmp_path: Path):
    for verdict in ("pass", "revise"):
        ctx = _run_dispatch(
            tmp_path / verdict,
            result={"status": "done", "verdict": verdict, "review_summary": "why"},
            docs={},
            phase="design_review",
        )
        payload = ctx.emit_type("doc_gap.design_reviewed")[0][0]
        assert payload == {
            "record_id": "dg-1",
            "verdict": verdict,
            "review_summary": "why",
        }


def test_commit_doc_gap_revision_requires_staged_docs(tmp_path: Path):
    repo = git_repo(tmp_path)
    ctx = _make_ctx(tmp_path, repo=repo, docs=repo)
    with pytest.raises(RuntimeError, match="no staged design docs"):
        ctx.runtime._commit_doc_gap_revision(["architecture.md"])

    (repo / "architecture.md").write_text("committed\n", encoding="utf-8")
    _git(repo, "add", "architecture.md")
    _git(repo, "commit", "-m", "add doc")
    with pytest.raises(RuntimeError, match="empty index"):
        ctx.runtime._commit_doc_gap_revision(["architecture.md"])


def test_commit_doc_gap_revision_commits_only_changed_docs(tmp_path: Path):
    repo = git_repo(tmp_path)
    (repo / "architecture.md").write_text("base\n", encoding="utf-8")
    (repo / "interfaces.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "architecture.md", "interfaces.md")
    _git(repo, "commit", "-m", "docs")
    (repo / "architecture.md").write_text("revised\n", encoding="utf-8")
    (repo / "interfaces.md").write_text("untouched-but-dirty\n", encoding="utf-8")
    ctx = _make_ctx(tmp_path, repo=repo, docs=repo)
    head = ctx.runtime._commit_doc_gap_revision(["architecture.md"])
    assert head == _git(repo, "rev-parse", "HEAD").stdout.strip()
    message = _git(repo, "log", "-1", "--format=%s").stdout.strip()
    assert message == "doc-gap design revision (SM-02 design_gap nested workflow)"
    changed = _git(repo, "show", "--name-only", "--format=", "HEAD").stdout.split()
    assert changed == ["architecture.md"]
    status = _git(repo, "status", "--porcelain").stdout
    assert "interfaces.md" in status


def test_fail_doc_gap_revision_emits_closed_event(tmp_path: Path):
    ctx = _make_ctx(tmp_path)
    ctx.runtime._fail_doc_gap_revision(_command(), "T-1", "dg-1", "design_review", "why")
    payload, kwargs = ctx.emit_type("doc_gap.design_failed")[0]
    assert payload == {"record_id": "dg-1", "phase": "design_review", "reason": "why"}
    assert kwargs["task_id"] == "T-1"
    assert kwargs["command_id"] == "D-ORIG"


# ---------------------------------------------------------------------------
# adjudication ingestion
# ---------------------------------------------------------------------------

_MARKER_DOC = (
    "# Test plan\n\n"
    "> **Shield:** design gap discussion.\n"
    ">> **Prism:** SM-02-ADJUDICATION | route=design_gap"
    " | responsible_role=archer | quarantine_id=q-abc | threads=T-001\n"
)


def _detected_state(**overrides) -> State:
    record = {
        "state": "DETECTED",
        "document_paths": ["test-plan.md"],
        "thread_ids": ["T-001"],
        "quarantine_id": "q-abc",
        "origin": {"role": "shield", "dispatch_id": "ORIG-1"},
    }
    record.update(overrides)
    return State(run_id="RUN", status="active", doc_gaps={"dg-1": record})


def test_ingest_adjudications_guard_clauses(tmp_path: Path):
    ctx = _make_ctx(tmp_path)
    assert ctx.runtime._ingest_doc_gap_adjudications(State(run_id="RUN", status="completed")) is False
    assert ctx.runtime._ingest_doc_gap_adjudications(State(run_id="RUN")) is False
    ctx.store._state = State(
        run_id="RUN", status="active", doc_gaps={"dg-1": {"state": "DETECTED"}}
    )
    assert ctx.runtime._ingest_doc_gap_adjudications(ctx.store._state) is False


def test_ingest_record_adjudication_emits_closed_event(tmp_path: Path):
    ctx = _make_ctx(tmp_path, state=_detected_state())
    (ctx.docs / "test-plan.md").write_text(_MARKER_DOC, encoding="utf-8")
    assert ctx.runtime._ingest_doc_gap_adjudications(ctx.store._state) is True
    payload = ctx.emit_type("doc_comment.adjudicated")[0][0]
    assert payload["record_id"] == "dg-1"
    assert payload["quarantine_id"] == "q-abc"
    assert payload["origin_dispatch_id"] == "ORIG-1"
    assert payload["route"] == "design_gap"
    assert payload["responsible_role"] == "archer"
    assert payload["thread_ids"] == ["T-001"]
    assert payload["decision_ref"]


def test_ingest_record_adjudication_guards(tmp_path: Path):
    ctx = _make_ctx(tmp_path, state=State(run_id="RUN", status="active", doc_gaps={}))
    assert ctx.runtime._ingest_record_adjudication("missing") is False

    for overrides in (
        {"state": "DESIGN_GAP"},
        {"document_paths": ["absent.md"]},
        {"thread_ids": []},
        {"quarantine_id": "q-other"},
    ):
        ctx = _make_ctx(tmp_path, state=_detected_state(**overrides))
        (ctx.docs / "test-plan.md").write_text(_MARKER_DOC, encoding="utf-8")
        assert ctx.runtime._ingest_record_adjudication("dg-1") is False
        assert ctx.emit_type("doc_comment.adjudicated") == []


def test_ingest_record_adjudication_malformed_stays_waiting(tmp_path: Path):
    ctx = _make_ctx(tmp_path, state=_detected_state())
    (ctx.docs / "test-plan.md").write_text(
        "# Test plan\n\n"
        "> **Shield:** design gap discussion.\n"
        ">> **Prism:** SM-02-ADJUDICATION | route=bogus\n",
        encoding="utf-8",
    )
    assert ctx.runtime._ingest_record_adjudication("dg-1") is False
    assert ctx.emit_type("doc_comment.adjudicated") == []
