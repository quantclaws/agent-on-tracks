"""Behavior coverage for the doc-gap slice-2 mixin (``ExecDocGapMixin``).

Drives the run-loop/resume group directly through a bare mixin host:
forwarders delegate to the slice-1 runtime facade, and the SM-02 pre-decide
orchestration, interrupted-resume WAL recovery, quarantine-manifest rebuild
and thread-resolution resume all behave per IF-DOCGAP-001 / NFR-0090-02.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from tracks.executor.doc_comment import (
    QuarantinedChange,
    ResumeDecision,
)
from tracks.executor.doc_gap_face import ExecDocGapMixin
from tracks.kernel.events import Command
from tracks.kernel.machine import State
from tracks.store import Store


class _DocGapSpy:
    """Records every forwarded capability call; configurable return values."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.results: dict = {}

    def __getattr__(self, name):
        def _call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return self.results.get(name)

        return _call


class _Host(ExecDocGapMixin):
    """Bare mixin host with only the external seams the slice-2 methods use."""

    def __init__(self, *, store=None, repo: Path | None = None, run_id: str = "RUN"):
        self._doc_gap = _DocGapSpy()
        self.repo = repo or Path(".")
        self.run_id = run_id
        self.store = store
        self.emitted: list[tuple] = []
        self.issued: list[tuple] = []
        self.doc_map: dict[str, Path] = {}
        self.identity_override: dict = {}

    def _emit(self, event, payload=None, **kwargs):
        self.emitted.append((event, payload, kwargs))

    def issue(self, cmd, command_id=None):
        self.issued.append((cmd, command_id))

    def _doc_path(self, name: str) -> Path:
        return self.doc_map[name]

    def _path_identity(self, path: Path) -> str:
        key = str(path)
        if key in self.identity_override:
            return self.identity_override[key]
        return f"id:{key}"


# ---------------------------------------------------------------------------
# forwarders
# ---------------------------------------------------------------------------


_FORWARD_CALLS = [
    ("_snapshot_design_docs", ("devon",), {}),
    ("_capture_doc_gap_context", (State(run_id="RUN"), "devon"), {}),
    ("_doc_gap_origin", (State(run_id="RUN"), "devon", "RED", Command(kind="x")), {}),
    (
        "_handle_doc_gap_outcome",
        (Command(kind="x"), State(run_id="RUN"), "T", "devon", "RED", {}, None),
        {},
    ),
    ("_rollback_doc_gap_round", ({}, {}), {}),
    ("_reject_over_reach", (Command(kind="x"), State(run_id="RUN"), "T", "devon", (), {}, {}), {}),
    (
        "_pause_for_legal_discussion",
        (Command(kind="x"), State(run_id="RUN"), "T", "devon", "RED", (), [], {}),
        {},
    ),
    ("_quarantine_legal_changes", (State(run_id="RUN"), SimpleNamespace(), SimpleNamespace(), {}), {}),
    ("_emit_doc_gap_pause", (Command(kind="x"), "T", SimpleNamespace(), [], {}, SimpleNamespace(), None), {}),
    ("_advance_doc_gap_design_revision", (State(run_id="RUN"),), {}),
    ("_recover_doc_gap_dispatch", ("R1", {}, State(run_id="RUN"), "rev"), {}),
    (
        "_dispatch_doc_gap_agent",
        ("R1", {}, State(run_id="RUN")),
        {"phase": "archer_design", "dispatch_id": None, "emit_audit": True},
    ),
    ("_do_doc_gap_dispatch", (Command(kind="x"), State(run_id="RUN"), "T", {}), {}),
    ("_checkpoint_doc_gap_revision", (Command(kind="x"), "T", "R1", {}, [], {}), {}),
    ("_fail_doc_gap_revision", (Command(kind="x"), "T", "R1", "archer", "why"), {}),
    ("_commit_doc_gap_revision", (["a.md"],), {}),
    ("_ingest_doc_gap_adjudications", (State(run_id="RUN"),), {}),
    ("_ingest_record_adjudication", ("R1",), {}),
]


@pytest.mark.parametrize("name,args,kwargs", _FORWARD_CALLS)
def test_slice2_forwarders_delegate_to_runtime_facade(name, args, kwargs):
    host = _Host()
    getattr(host, name)(*args, **kwargs)
    forwarded = [call[0] for call in host._doc_gap.calls]
    assert name in forwarded


def test_legal_delta_targets_forwarder_uses_pure_function():
    host = _Host()
    deltas = [SimpleNamespace(path="a.md", new_thread_ids=("T-001",))]
    assert host._legal_delta_targets(deltas) == (["a.md"], ["T-001"])


# ---------------------------------------------------------------------------
# SM-02 pre-decide ordering
# ---------------------------------------------------------------------------


def test_sm02_pre_decide_short_circuits_in_order():
    host = _Host()
    calls: list[str] = []

    def stage(name, result):
        def _run(state):
            calls.append(name)
            return result

        return _run

    host._ingest_doc_gap_adjudications = stage("ingest", False)
    host._advance_doc_gap_design_revision = stage("advance", False)
    host._resume_doc_gap_if_ready = stage("resume", False)
    host._resume_interrupted_dispatch = stage("interrupted", True)
    assert host._sm02_pre_decide(State(run_id="RUN")) is True
    assert calls == ["ingest", "advance", "resume", "interrupted"]

    calls.clear()
    host._ingest_doc_gap_adjudications = stage("ingest", True)
    assert host._sm02_pre_decide(State(run_id="RUN")) is True
    assert calls == ["ingest"]


# ---------------------------------------------------------------------------
# _resume_doc_gap_if_ready
# ---------------------------------------------------------------------------


def _ready_host() -> _Host:
    host = _Host()
    host.resumed_records: list[tuple] = []
    host._threads_resolved_in_docs = lambda docs, threads: True
    host._resume_doc_gap_record = lambda rid, rec: host.resumed_records.append((rid, rec))
    return host


def test_resume_doc_gap_if_ready_guards_stage_and_status():
    host = _ready_host()
    host.resumed_records = []
    assert host._resume_doc_gap_if_ready(State(run_id="RUN", stage="M-DESIGN")) is False
    assert (
        host._resume_doc_gap_if_ready(
            State(run_id="RUN", stage="M-IMPL", status="completed")
        )
        is False
    )


def test_resume_doc_gap_if_ready_fail_closed_on_open_design_gap():
    host = _ready_host()
    state = State(run_id="RUN", stage="M-IMPL")
    state.doc_gaps = {
        "R1": {"state": "DESIGN_GAP", "revision": "archer_revised"},
        "R2": {"state": "AWAITING_ADJUDICATION"},
    }
    assert host._resume_doc_gap_if_ready(state) is False
    assert host.resumed_records == []


def test_resume_doc_gap_if_ready_requires_resolved_threads():
    host = _ready_host()
    host._threads_resolved_in_docs = lambda docs, threads: False
    state = State(run_id="RUN", stage="M-TEST")
    state.doc_gaps = {"R1": {"state": "AGENT_CORRECTION"}}
    assert host._resume_doc_gap_if_ready(state) is False
    assert host.resumed_records == []


@pytest.mark.parametrize("stage", ["M-IMPL", "M-TEST"])
def test_resume_doc_gap_if_ready_resumes_prism_reviewed_design_gap(stage):
    host = _ready_host()
    state = State(run_id="RUN", stage=stage)
    rec = {"state": "DESIGN_GAP", "revision": "prism_reviewed"}
    state.doc_gaps = {"R1": rec}
    assert host._resume_doc_gap_if_ready(state) is True
    assert host.resumed_records == [("R1", rec)]


def test_resume_doc_gap_if_ready_resumes_agent_correction():
    host = _ready_host()
    state = State(run_id="RUN", stage="M-IMPL")
    rec = {"state": "AGENT_CORRECTION"}
    state.doc_gaps = {"R9": rec}
    assert host._resume_doc_gap_if_ready(state) is True
    assert host.resumed_records == [("R9", rec)]


# ---------------------------------------------------------------------------
# _resume_interrupted_dispatch
# ---------------------------------------------------------------------------


def _interrupted_host(tmp_path: Path) -> tuple[_Host, Store]:
    store = Store(tmp_path / ".tracks")
    host = _Host(store=store)
    host.interrupted: list[tuple] = []
    host._issue_resume_dispatch = lambda origin, cid, attempt: host.interrupted.append(
        (origin, cid, attempt)
    )
    return host, store


def test_resume_interrupted_dispatch_guards_pending_status_stage():
    host = _Host()
    state = State(run_id="RUN", stage="M-IMPL")
    state.pending = {"command_id": "C"}
    assert host._resume_interrupted_dispatch(state) is False
    assert host._resume_interrupted_dispatch(State(run_id="RUN", stage="M-IMPL", status="completed")) is False
    assert host._resume_interrupted_dispatch(State(run_id="RUN", stage="M-DESIGN")) is False


def test_resume_interrupted_dispatch_skips_missing_and_issued_ids(tmp_path: Path):
    host, store = _interrupted_host(tmp_path)
    state = State(run_id="RUN", stage="M-IMPL")
    state.doc_gaps = {
        "R1": {"state": "RESUMED"},  # no next_dispatch_id
        "R2": {"state": "RESTORED", "next_dispatch_id": "C-ISSUED"},
    }
    store.append("RUN", "v0.5", "command.issued", {"command": {"kind": "dispatch_agent"}}, command_id="C-ISSUED")
    assert host._resume_interrupted_dispatch(state) is False
    assert host.interrupted == []


def test_resume_interrupted_dispatch_emits_missing_resumed_and_issues(tmp_path: Path):
    host, store = _interrupted_host(tmp_path)
    state = State(run_id="RUN", stage="M-TEST")
    origin = {"dispatch_id": "D-ORIG", "role": "shield", "phase": "WRITE", "attempt": 2}
    state.doc_gaps = {
        "R1": {"state": "RESTORED", "next_dispatch_id": "C-NEXT", "next_attempt": 3, "origin": origin}
    }
    assert host._resume_interrupted_dispatch(state) is True
    assert host.emitted == [
        (
            "outcome.resumed",
            {
                "record_id": "R1",
                "origin_dispatch_id": "D-ORIG",
                "next_dispatch_id": "C-NEXT",
                "next_attempt": 3,
            },
            {},
        )
    ]
    assert host.interrupted == [(origin, "C-NEXT", 3)]


def test_resume_interrupted_dispatch_resumed_record_skips_duplicate_event(tmp_path: Path):
    host, _ = _interrupted_host(tmp_path)
    state = State(run_id="RUN", stage="M-IMPL")
    origin = {"dispatch_id": "D-ORIG", "fallback": True}
    state.doc_gaps = {
        "R1": {"state": "RESUMED", "next_dispatch_id": "C-NEXT", "origin": origin}
    }
    assert host._resume_interrupted_dispatch(state) is True
    assert host.emitted == []  # outcome.resumed already persisted
    assert host.interrupted == [(origin, "C-NEXT", 1)]


# ---------------------------------------------------------------------------
# _dispatch_issued
# ---------------------------------------------------------------------------


def test_dispatch_issued_matches_only_command_issued_type(tmp_path: Path):
    store = Store(tmp_path / ".tracks")
    host = _Host(store=store)
    store.append("RUN", "v0.5", "doc_gap.design_dispatched", {}, command_id="C1")
    assert host._dispatch_issued("C1") is False
    store.append("RUN", "v0.5", "command.issued", {"command": {"kind": "x"}}, command_id="C1")
    assert host._dispatch_issued("C1") is True
    assert host._dispatch_issued("C2") is False


# ---------------------------------------------------------------------------
# _rebuild_quarantine_descriptor
# ---------------------------------------------------------------------------


class _BlobStore:
    def __init__(self, manifest, *, raises=None):
        self.manifest = manifest
        self.raises = raises
        self.refs: list = []

    def load_payload(self, payload):
        self.refs.append(payload)
        if self.raises is not None:
            raise self.raises
        return self.manifest


def test_rebuild_quarantine_descriptor_missing_ref_returns_none():
    host = _Host(store=_BlobStore(None))
    assert host._rebuild_quarantine_descriptor({}) is None
    assert host._rebuild_quarantine_descriptor({"manifest_ref": ""}) is None


def test_rebuild_quarantine_descriptor_unreadable_or_nondict_returns_none():
    host = _Host(store=_BlobStore(None, raises=OSError("gone")))
    assert host._rebuild_quarantine_descriptor({"manifest_ref": "sha"}) is None
    host = _Host(store=_BlobStore(["not", "a", "dict"]))
    assert host._rebuild_quarantine_descriptor({"manifest_ref": "sha"}) is None


def test_rebuild_quarantine_descriptor_reconstructs_changes_and_status():
    manifest = {
        "quarantine_id": "q-1",
        "design_identity": "DID",
        "run_identity": "RID",
        "status": "weird",
        "changes": [
            {
                "path": "tests/test_x.py",
                "operation": "modify",
                "baseline_identity": "b",
                "content_identity": "c",
            },
            {"path": "tests/test_y.py"},
        ],
    }
    host = _Host(store=_BlobStore(manifest))
    rec = {"manifest_ref": "sha-ref", "origin": {"role": "shield"}}
    descriptor = host._rebuild_quarantine_descriptor(rec)
    assert descriptor.quarantine_id == "q-1"
    assert descriptor.origin == {"role": "shield"}
    assert descriptor.design_identity == "DID"
    assert descriptor.run_identity == "RID"
    assert descriptor.manifest_ref == "sha-ref"
    assert descriptor.manifest_sha256 == "sha-ref"
    assert descriptor.status == "held"  # unknown status fails closed
    assert descriptor.changes == (
        QuarantinedChange("tests/test_x.py", "modify", "b", "c"),
        QuarantinedChange("tests/test_y.py", "modify", None, None),
    )


def test_rebuild_quarantine_descriptor_preserves_empty_status():
    host = _Host(store=_BlobStore({"status": "empty", "changes": []}))
    descriptor = host._rebuild_quarantine_descriptor({"manifest_ref": "sha"})
    assert descriptor.status == "empty"
    assert descriptor.changes == ()


# ---------------------------------------------------------------------------
# _quarantine_resume_decision
# ---------------------------------------------------------------------------


def _descriptor(*changes, design_identity="DID", run_identity="RUN", status="held"):
    return SimpleNamespace(
        quarantine_id="q-1",
        origin={"role": "shield"},
        design_identity=design_identity,
        run_identity=run_identity,
        changes=tuple(changes),
        manifest_ref="ref",
        manifest_sha256="ref",
        status=status,
    )


def test_quarantine_resume_decision_without_manifest_fails_closed(tmp_path: Path):
    host = _Host()
    host._rebuild_quarantine_descriptor = lambda rec: None
    assert host._quarantine_resume_decision(
        {"quarantine_status": "empty"}, "C-NEXT", 2
    ) == ResumeDecision("restore", "empty", "C-NEXT", 2)
    assert host._quarantine_resume_decision(
        {"quarantine_status": "held"}, "C-NEXT", 2
    ) == ResumeDecision("discard", "content_conflict", "C-NEXT", 2)


def test_quarantine_resume_decision_ignores_marker_only_drift(tmp_path: Path):
    """Without an actual Archer revision the pause-time design anchor is the
    comparison baseline -- a post-pause Prism marker must not discard."""
    host = _Host(repo=tmp_path)
    descriptor = _descriptor(
        QuarantinedChange("tests/test_x.py", "modify", "b", "current")
    )
    host._rebuild_quarantine_descriptor = lambda rec: descriptor
    host.doc_map = {"architecture.md": tmp_path / "architecture.md"}
    host.identity_override = {str(tmp_path / "tests/test_x.py"): "current"}
    host.identity_override[str(tmp_path / "architecture.md")] = "drifted"
    rec = {
        "document_paths": ["architecture.md"],
        "quarantine_status": "held",
    }
    assert host._quarantine_resume_decision(rec, "C-NEXT", 2) == ResumeDecision(
        "restore", "identity_current", "C-NEXT", 2
    )


def test_quarantine_resume_decision_discards_on_revised_design_drift(tmp_path: Path):
    host = _Host()
    descriptor = _descriptor(
        QuarantinedChange("tests/test_x.py", "modify", "b", "current")
    )
    host._rebuild_quarantine_descriptor = lambda rec: descriptor
    host.doc_map = {"architecture.md": tmp_path / "architecture.md"}
    host.identity_override = {str(tmp_path / "tests/test_x.py"): "current"}
    host.identity_override[str(tmp_path / "architecture.md")] = "drifted"
    rec = {
        "document_paths": ["architecture.md"],
        "quarantine_status": "held",
        "revised_design_identity": "DID2",
    }
    decision = host._quarantine_resume_decision(rec, "C-NEXT", 2)
    assert decision.action == "discard"
    assert decision.reason == "design_stale"


def test_quarantine_resume_decision_discards_on_content_conflict(tmp_path: Path):
    host = _Host()
    descriptor = _descriptor(
        QuarantinedChange("tests/test_x.py", "modify", "b", "held-content")
    )
    host._rebuild_quarantine_descriptor = lambda rec: descriptor
    host.repo = tmp_path
    host.identity_override = {str(tmp_path / "tests/test_x.py"): "changed-since"}
    decision = host._quarantine_resume_decision({"quarantine_status": "held"}, "C-NEXT", 2)
    assert decision.action == "discard"
    assert decision.reason == "content_conflict"


# ---------------------------------------------------------------------------
# _resume_doc_gap_record
# ---------------------------------------------------------------------------


def test_resume_doc_gap_record_emits_decision_pair_and_issues(tmp_path: Path):
    host = _Host()
    decisions: list = []
    host._quarantine_resume_decision = lambda rec, cid, attempt: ResumeDecision(
        "restore", "identity_current", cid, attempt
    )
    host._issue_resume_dispatch = lambda origin, cid, attempt: decisions.append(
        (origin, cid, attempt)
    )
    rec = {
        "origin": {"dispatch_id": "D-ORIG", "attempt": 1, "role": "shield"},
        "quarantine_id": "q-1",
    }
    host._resume_doc_gap_record("R1", rec)
    assert [event for event, _payload, _kw in host.emitted] == [
        "outcome.restored",
        "outcome.resumed",
    ]
    restored = host.emitted[0][1]
    assert restored["record_id"] == "R1"
    assert restored["quarantine_id"] == "q-1"
    assert restored["reason"] == "identity_current"
    assert restored["next_attempt"] == 2
    assert decisions == [(rec["origin"], restored["next_dispatch_id"], 2)]
    assert host.emitted[1][1]["origin_dispatch_id"] == "D-ORIG"


def test_resume_doc_gap_record_discarded_action_emits_outcome_discarded():
    host = _Host()
    host._quarantine_resume_decision = lambda rec, cid, attempt: ResumeDecision(
        "discard", "content_conflict", cid, attempt
    )
    host._issue_resume_dispatch = lambda *args: None
    host._resume_doc_gap_record("R1", {"origin": {}})
    assert host.emitted[0][0] == "outcome.discarded"
    assert host.emitted[0][1]["quarantine_id"] == ""


# ---------------------------------------------------------------------------
# _threads_resolved_in_docs
# ---------------------------------------------------------------------------


def test_threads_resolved_in_docs_empty_ids_is_false():
    host = _Host()
    assert host._threads_resolved_in_docs(["architecture.md"], []) is False


def test_threads_resolved_in_docs_reads_each_doc(tmp_path: Path):
    host = _Host()
    resolved = tmp_path / "resolved.md"
    resolved.write_text("# Doc\n\n> **Shield [resolved]:** done\n", encoding="utf-8")
    open_thread = tmp_path / "open.md"
    open_thread.write_text("# Doc\n\n> **Shield [open]:** pending\n", encoding="utf-8")
    missing = tmp_path / "missing.md"
    host.doc_map = {"a.md": resolved, "b.md": open_thread, "c.md": missing}

    assert host._threads_resolved_in_docs(["a.md"], ["T-001"]) is True
    assert host._threads_resolved_in_docs(["b.md"], ["T-001"]) is False
    assert host._threads_resolved_in_docs(["c.md", "a.md"], ["T-001"]) is True
    assert host._threads_resolved_in_docs(["a.md"], ["T-999"]) is False


# ---------------------------------------------------------------------------
# _issue_resume_dispatch
# ---------------------------------------------------------------------------


def test_issue_resume_dispatch_routes_shield_to_m_test():
    host = _Host()
    origin = {"role": "shield", "phase": "WRITE"}
    host._issue_resume_dispatch(origin, "C-NEXT", 4)
    cmd, command_id = host.issued[0]
    assert command_id == "C-NEXT"
    assert cmd.kind == "dispatch_agent"
    assert cmd.params["stage"] == "M-TEST"
    assert cmd.params["role"] == "shield"
    assert cmd.params["substate"] == "WRITE"
    assert cmd.params["attempt"] == 4
    assert cmd.params["assignment"] == {"kind": "WRITE", "phase": "write"}


def test_issue_resume_dispatch_defaults_to_devon_m_impl():
    host = _Host()
    host._issue_resume_dispatch({}, "C-NEXT", 1)
    cmd, _ = host.issued[0]
    assert cmd.params["stage"] == "M-IMPL"
    assert cmd.params["role"] == "devon"
    assert cmd.params["substate"] == "RED"
