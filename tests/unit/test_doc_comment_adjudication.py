"""Unit tests for the SM-02 adjudication marker protocol and resume decision.

Covers the #62 closure contracts that the integration journeys cannot reach
individually: marker grammar (fail-closed), record binding rules, and the
full decide_quarantine_resume reason matrix (empty/identity_current restores;
design_stale/run_stale/content_conflict discards).
"""

from __future__ import annotations

import hashlib

from tests.unit.doc_comment_support import (
    _HELD_CHANGE,
    _NESTED_MARKER,
    _THREAD,
    _checkpoint_executor,
    _cmd,
    _descriptor,
    _design_dispatched_with_pre_identity,
    _doc,
    _doc_gap_crash_window_events,
    _doc_gap_executor,
    _marker,
    _project,
    _resume,
)
from tracks.executor.doc_comment import (
    classify_design_document_deltas,
    combined_design_identity,
    legal_anchor_pairs,
    match_adjudication_marker,
    scan_adjudication_markers,
)


def test_scan_finds_nested_prism_marker():
    markers, errors = scan_adjudication_markers(_doc())
    assert errors == []
    assert len(markers) == 1
    assert markers[0].route == "design_gap"
    assert markers[0].responsible_role == "archer"
    assert markers[0].quarantine_id == "q-abc123"
    assert markers[0].thread_ids == ("T-001",)
    assert markers[0].host_thread_id == "T-001"


def test_scan_empty_without_marker():
    markers, errors = scan_adjudication_markers(f"# Plan\n\n{_THREAD}")
    assert markers == []
    assert errors == []


def test_top_level_marker_is_not_inside_the_original_thread():
    """A depth-1 Prism marker opens its own thread — not an adjudication."""
    top_level = (
        "> **Prism:** SM-02-ADJUDICATION | route=design_gap"
        " | responsible_role=archer | quarantine_id=q-abc123 | threads=T-001\n"
    )
    markers, _ = scan_adjudication_markers(f"# Plan\n\n{_THREAD}\n{top_level}")
    assert markers == []


def test_non_prism_marker_is_ignored():
    rogue = _NESTED_MARKER.replace("**Prism:**", "**Shield:**")
    markers, _ = scan_adjudication_markers(_doc(marker=rogue))
    assert markers == []


def test_malformed_marker_fields_fail_closed():
    bad_bodies = (
        "SM-02-ADJUDICATION | route=design_gap",  # missing fields
        "SM-02-ADJUDICATION | route=human_gate | responsible_role=aaron"
        " | quarantine_id=q-x | threads=T-001",  # unknown route
        "SM-02-ADJUDICATION | route=design_gap | responsible_role=archer"
        " | quarantine_id=q-x | threads=T-001 | bogus=1",  # unknown field
        "SM-02-ADJUDICATION | route=design_gap | responsible_role=archer"
        " | threads=T-001",  # missing quarantine_id
        "SM-02-ADJUDICATION | route=design_gap | responsible_role=archer"
        " | quarantine_id=q-x | threads=first-thread",  # malformed label
    )
    for body in bad_bodies:
        _, errors = scan_adjudication_markers(
            _doc(marker=f">> **Prism:** {body}")
        )
        assert errors, f"expected fail-closed error for {body!r}"


def test_multiple_markers_are_ambiguous():
    doubled = (
        f"# Plan\n\n{_THREAD}{_NESTED_MARKER}"
        ">> **Prism:** SM-02-ADJUDICATION | route=agent_correction"
        " | responsible_role=shield | quarantine_id=q-abc123 | threads=T-001\n"
    )
    markers, errors = scan_adjudication_markers(doubled)
    assert errors == []
    assert len(markers) == 2
    matched, error = match_adjudication_marker(
        markers, quarantine_id="q-abc123", thread_ids=["T-001"], origin_role="shield"
    )
    assert matched is None
    assert error is not None and "ambiguous" in error


def test_historical_marker_does_not_block_current_record():
    """A previous record's marker (other quarantine) must be ignored (#62 r2).

    Documents keep every historical adjudication marker: relevance filtering
    by quarantine_id happens BEFORE the ambiguity decision, so a new record
    on the same document can still be adjudicated.
    """
    historical = _NESTED_MARKER.replace("q-abc123", "q-old000")
    doc = f"# Plan\n\n{_THREAD}{historical}{_NESTED_MARKER}"
    markers, errors = scan_adjudication_markers(doc)
    assert errors == []
    assert len(markers) == 2
    matched, error = match_adjudication_marker(
        markers, quarantine_id="q-abc123", thread_ids=["T-001"], origin_role=None
    )
    assert matched is not None and error is None
    assert matched.quarantine_id == "q-abc123"


def test_malformed_marker_in_unrelated_thread_is_ignored():
    """A broken marker in an UNRELATED thread never blocks this record (#62 r2)."""
    doc = (
        "# Plan\n\n"
        "> **Shield:** old unrelated discussion.\n"
        ">> **Prism:** SM-02-ADJUDICATION | route=bogus\n"
        "\n"
        "> **Shield:** current record discussion.\n"
        ">> **Prism:** SM-02-ADJUDICATION | route=agent_correction"
        " | responsible_role=shield | quarantine_id=q-abc123 | threads=T-002\n"
    )
    markers, errors = scan_adjudication_markers(doc)
    assert len(markers) == 1
    assert len(errors) == 1 and errors[0][0] == "T-001", (
        "the broken marker must be attributed to the unrelated thread"
    )
    matched, error = match_adjudication_marker(
        markers,
        errors,
        quarantine_id="q-abc123",
        thread_ids=["T-002"],
        origin_role="shield",
    )
    assert matched is not None and error is None


def test_malformed_marker_in_record_thread_fails_closed():
    valid_then_broken = (
        f"# Plan\n\n{_THREAD}{_NESTED_MARKER}"
        ">> **Prism:** SM-02-ADJUDICATION | route=bogus\n"
    )
    markers, errors = scan_adjudication_markers(valid_then_broken)
    assert len(markers) == 1 and len(errors) == 1
    matched, error = match_adjudication_marker(
        markers,
        errors,
        quarantine_id="q-abc123",
        thread_ids=["T-001"],
        origin_role="shield",
    )
    assert matched is None
    # Fail-closed: the broken marker was written into THIS record's thread.
    assert error is not None and error.startswith("T-001:")


def test_match_requires_record_quarantine_and_threads():
    marker = _marker()
    # A foreign-quarantine marker is IGNORED (still waiting), not an error:
    # relevance filtering by quarantine_id precedes every other decision.
    matched, error = match_adjudication_marker(
        [marker], quarantine_id="q-other", thread_ids=["T-001"], origin_role=None
    )
    assert matched is None and error is None

    matched, error = match_adjudication_marker(
        [_marker(thread_ids=("T-002",))],
        quarantine_id="q-abc123",
        thread_ids=["T-001"],
        origin_role=None,
    )
    assert matched is None and error is not None

    matched, error = match_adjudication_marker(
        [_marker(host_thread_id="T-009")],
        quarantine_id="q-abc123",
        thread_ids=["T-001"],
        origin_role=None,
    )
    assert matched is None and error is not None


def test_match_enforces_responsible_role_rules():
    matched, error = match_adjudication_marker(
        [_marker(responsible_role="devon")],
        quarantine_id="q-abc123",
        thread_ids=["T-001"],
        origin_role=None,
    )
    assert matched is None and error is not None and "archer" in error

    correction = _marker(route="agent_correction", responsible_role="prism")
    matched, error = match_adjudication_marker(
        [correction],
        quarantine_id="q-abc123",
        thread_ids=["T-001"],
        origin_role="shield",
    )
    assert matched is None and error is not None and "shield" in error

    ok, error = match_adjudication_marker(
        [_marker(route="agent_correction", responsible_role="shield")],
        quarantine_id="q-abc123",
        thread_ids=["T-001"],
        origin_role="shield",
    )
    assert ok is not None and error is None


def test_match_without_candidates_is_still_waiting_not_error():
    matched, error = match_adjudication_marker(
        [], quarantine_id="q-abc123", thread_ids=["T-001"], origin_role="shield"
    )
    assert matched is None and error is None




def test_empty_quarantine_restores_trivially():
    decision = _resume(_descriptor(status="empty"))
    assert (decision.action, decision.reason) == ("restore", "empty")




def test_held_current_identities_restore():
    decision = _resume(
        _descriptor(changes=(_HELD_CHANGE,)), paths={"src/x.py": "c1"}
    )
    assert (decision.action, decision.reason) == ("restore", "identity_current")


def test_design_drift_discards():
    decision = _resume(_descriptor(changes=(_HELD_CHANGE,)), design="design-b")
    assert (decision.action, decision.reason) == ("discard", "design_stale")


def test_run_drift_discards():
    decision = _resume(_descriptor(changes=(_HELD_CHANGE,)), run="run-b")
    assert (decision.action, decision.reason) == ("discard", "run_stale")


def test_content_conflict_discards():
    decision = _resume(
        _descriptor(changes=(_HELD_CHANGE,)), paths={"src/x.py": "changed"}
    )
    assert (decision.action, decision.reason) == ("discard", "content_conflict")


def test_adjudicated_projection_and_first_decision_wins():
    """decision_ref is projected; conflicting re-adjudications never flip."""
    from tests.unit.helpers import ev
    from tracks.kernel.machine import State, apply

    s = State()
    apply(
        s,
        ev(
            1,
            "doc_comment.detected",
            {
                "record_id": "dg-1",
                "origin": {"role": "shield", "dispatch_id": "D1"},
                "document_paths": ["test-plan.md"],
                "thread_ids": ["T-001"],
            },
        ),
    )
    apply(
        s,
        ev(
            2,
            "doc_comment.adjudicated",
            {
                "record_id": "dg-1",
                "route": "agent_correction",
                "responsible_role": "shield",
                "decision_ref": "ref-1",
            },
        ),
    )
    record = s.doc_gaps["dg-1"]
    assert record["state"] == "AGENT_CORRECTION"
    assert record["decision_ref"] == "ref-1"
    assert record["responsible_role"] == "shield"

    # Conflicting re-adjudication (different ref): rejected, first stands.
    apply(
        s,
        ev(
            3,
            "doc_comment.adjudicated",
            {
                "record_id": "dg-1",
                "route": "design_gap",
                "responsible_role": "archer",
                "decision_ref": "ref-2",
            },
        ),
    )
    assert s.doc_gaps["dg-1"]["route"] == "agent_correction"
    assert s.doc_gaps["dg-1"]["decision_ref"] == "ref-1"

    # Identical replay: idempotent no-op.
    apply(
        s,
        ev(
            4,
            "doc_comment.adjudicated",
            {
                "record_id": "dg-1",
                "route": "agent_correction",
                "responsible_role": "shield",
                "decision_ref": "ref-1",
            },
        ),
    )
    assert s.doc_gaps["dg-1"]["state"] == "AGENT_CORRECTION"


def test_design_anchor_covers_the_discussed_doc_not_the_first_alphabetical():
    """Storage and resume sides must anchor the SAME document set (MAJOR-2).

    Shield discussed test-plan.md while interfaces.md (alphabetically first
    protected doc, classification none) is also in the delta set.  The
    storage-side anchor from legal_anchor_pairs must equal the resume-side
    recomputation over the same paths' current identities when nothing
    drifted — and differ when the discussed doc changed.
    """
    baseline = {
        "interfaces.md": b"# interfaces\n",
        "test-plan.md": b"# plan\n\n> **Prism:** finding one\n",
    }
    current = {
        "interfaces.md": baseline["interfaces.md"],
        "test-plan.md": (
            b"# plan\n\n> **Prism:** finding one\n"
            b"\n> **Shield:** new discussion root on the plan\n"
        ),
    }
    deltas = classify_design_document_deltas(
        role="shield", baseline_documents=baseline, current_documents=current
    )
    pairs = legal_anchor_pairs(deltas)
    assert [path for path, _ in pairs] == ["test-plan.md"], (
        "the design anchor must cover exactly the legal discussion docs"
    )

    stored = combined_design_identity(list(pairs))
    # Same canonical construction over identical content -> equal identities.
    resumed_current = combined_design_identity(
        [
            (path, hashlib.sha256(current[path]).hexdigest())
            for path, _ in pairs
        ]
    )
    assert stored == resumed_current, (
        "unchanged design docs must restore: storage and resume anchors agree"
    )
    drifted = combined_design_identity(
        [
            (path, hashlib.sha256(b"drifted").hexdigest())
            for path, _ in pairs
        ]
    )
    assert stored != drifted, "a changed design doc must not restore"


# -- SM-02 design_gap nested workflow reducers (#62 finding 1) --------------




def test_design_dispatched_sets_revision_substate():
    """doc_gap.design_dispatched projects the revision sub-progress."""
    s = _project([
        ("doc_comment.detected", {"record_id": "dg-1", "thread_ids": ["T-001"]}),
        ("doc_comment.adjudicated", {
            "record_id": "dg-1", "route": "design_gap",
            "responsible_role": "archer", "decision_ref": "abc",
        }),
        ("doc_gap.design_dispatched", {
            "record_id": "dg-1", "dispatch_id": "d-1",
            "phase": "design_revision", "role": "archer",
        }),
    ])
    rec = s.doc_gaps["dg-1"]
    assert rec["state"] == "DESIGN_GAP"
    assert rec["revision"] == "archer_dispatched"
    assert rec["design_dispatch_id"] == "d-1"


def test_design_revised_projects_revised_identity():
    """doc_gap.design_revised records the checkpointed design identity."""
    s = _project([
        ("doc_comment.detected", {"record_id": "dg-1", "thread_ids": ["T-001"]}),
        ("doc_comment.adjudicated", {
            "record_id": "dg-1", "route": "design_gap",
            "responsible_role": "archer", "decision_ref": "abc",
        }),
        ("doc_gap.design_dispatched", {
            "record_id": "dg-1", "dispatch_id": "d-1",
            "phase": "design_revision", "role": "archer",
        }),
        ("doc_gap.design_revised", {
            "record_id": "dg-1",
            "revised_design_identity": "sha-revised",
            "changed_paths": ["test-plan.md"],
        }),
    ])
    rec = s.doc_gaps["dg-1"]
    assert rec["revision"] == "design_revised"
    assert rec["revised_design_identity"] == "sha-revised"


def test_design_reviewed_pass_reaches_prism_reviewed():
    """doc_gap.design_reviewed(pass) reaches the resume-gating substate."""
    s = _project([
        ("doc_comment.detected", {"record_id": "dg-1", "thread_ids": ["T-001"]}),
        ("doc_comment.adjudicated", {
            "record_id": "dg-1", "route": "design_gap",
            "responsible_role": "archer", "decision_ref": "abc",
        }),
        ("doc_gap.design_reviewed", {
            "record_id": "dg-1", "verdict": "pass",
        }),
    ])
    rec = s.doc_gaps["dg-1"]
    assert rec["revision"] == "prism_reviewed"
    assert rec["prism_verdict"] == "pass"


def test_design_reviewed_revise_resets_for_redispatch():
    """doc_gap.design_reviewed(revise) resets revision to re-dispatch Archer."""
    s = _project([
        ("doc_comment.detected", {"record_id": "dg-1", "thread_ids": ["T-001"]}),
        ("doc_comment.adjudicated", {
            "record_id": "dg-1", "route": "design_gap",
            "responsible_role": "archer", "decision_ref": "abc",
        }),
        ("doc_gap.design_reviewed", {
            "record_id": "dg-1", "verdict": "revise",
        }),
    ])
    rec = s.doc_gaps["dg-1"]
    assert rec["revision"] is None
    assert rec["prism_verdict"] == "revise"


def test_design_failure_retries_then_parks_after_three_attempts():
    """Failed nested revisions close WAL and consume a bounded retry budget."""
    events = [
        ("doc_comment.detected", {"record_id": "dg-1", "thread_ids": ["T-001"]}),
        ("doc_comment.adjudicated", {
            "record_id": "dg-1", "route": "design_gap",
            "responsible_role": "archer", "decision_ref": "abc",
        }),
    ]
    for attempt in range(1, 4):
        events.extend([
            ("doc_gap.design_dispatched", {
                "record_id": "dg-1", "dispatch_id": f"D-{attempt}",
                "phase": "design_revision", "role": "archer",
            }),
            ("doc_gap.design_failed", {
                "record_id": "dg-1", "reason": "no adjudicated design document changed",
            }),
        ])
    rec = _project(events).doc_gaps["dg-1"]
    assert rec["design_attempts"] == 3
    assert rec["revision"] == "design_failed"
    assert rec["reason"] == "no adjudicated design document changed"


def test_agent_correction_has_no_nested_workflow():
    """AGENT_CORRECTION records never enter the design_gap nested workflow."""
    s = _project([
        ("doc_comment.detected", {"record_id": "dg-1", "thread_ids": ["T-001"]}),
        ("doc_comment.adjudicated", {
            "record_id": "dg-1", "route": "agent_correction",
            "responsible_role": "shield", "decision_ref": "abc",
        }),
    ])
    rec = s.doc_gaps["dg-1"]
    assert rec["state"] == "AGENT_CORRECTION"
    assert rec["revision"] is None


# -- SM-02 design_gap crash-window WAL recovery (#62 finding 1) -------------






def test_crash_window_recovery_reissues_with_same_dispatch_id(tmp_path, monkeypatch):
    """A crash between doc_gap.design_dispatched and command.issued must not
    park the record permanently: the next iteration re-issues the nested
    dispatch with the SAME design_dispatch_id (idempotent) and emits NO
    duplicate audit event."""
    from tests.unit.helpers import git_repo as _repo

    repo = _repo(tmp_path)
    ex, store, issued = _doc_gap_executor(
        repo,
        _doc_gap_crash_window_events(dispatch_id="D-ARCHER"),
        monkeypatch,
    )
    state = store.state("RUN")
    rec = state.doc_gaps["dg-1"]
    assert rec["revision"] == "archer_dispatched"
    assert rec["design_dispatch_id"] == "D-ARCHER"

    handled = ex._advance_doc_gap_design_revision(state)
    assert handled, "crash-window record must be re-issued (handled=True)"

    assert len(issued) == 1, "expected exactly one re-issue"
    assert issued[0]["command_id"] == "D-ARCHER", (
        "re-issue must use the SAME persisted design_dispatch_id"
    )
    assert issued[0]["params"].get("doc_gap", {}).get("phase") == "design_revision"

    audit = [e for e in store.events("RUN") if e.type == "doc_gap.design_dispatched"]
    assert len(audit) == 1, (
        "crash-window recovery must NOT emit a duplicate audit event"
    )
    store.close()


def test_crash_window_recovery_skips_when_already_issued(tmp_path, monkeypatch):
    """When command.issued IS present for the design_dispatch_id, the record
    is genuinely waiting for its outcome - no re-issue."""
    from tests.unit.helpers import git_repo as _repo

    repo = _repo(tmp_path)
    ex, store, issued = _doc_gap_executor(
        repo,
        _doc_gap_crash_window_events(
            dispatch_id="D-ARCHER", with_command_issued=True,
        ),
        monkeypatch,
    )
    state = store.state("RUN")
    rec = state.doc_gaps["dg-1"]
    assert rec["revision"] == "archer_dispatched"

    handled = ex._advance_doc_gap_design_revision(state)
    assert not handled, "an already-issued dispatch must fall through (no re-issue)"
    assert issued == [], "no re-issue when command.issued is present"
    store.close()


def test_crash_window_recovery_reissues_prism_review_phase(tmp_path, monkeypatch):
    """The crash-window recovery also covers the Prism review dispatch
    (revision=prism_dispatched): re-issue with phase=design_review."""
    from tests.unit.helpers import git_repo as _repo

    repo = _repo(tmp_path)
    ex, store, issued = _doc_gap_executor(
        repo,
        _doc_gap_crash_window_events(
            dispatch_id="D-PRISM", phase="design_review",
        ),
        monkeypatch,
    )
    state = store.state("RUN")
    rec = state.doc_gaps["dg-1"]
    assert rec["revision"] == "prism_dispatched"
    assert rec["design_dispatch_id"] == "D-PRISM"

    handled = ex._advance_doc_gap_design_revision(state)
    assert handled
    assert len(issued) == 1
    assert issued[0]["command_id"] == "D-PRISM"
    assert issued[0]["params"].get("doc_gap", {}).get("phase") == "design_review"
    store.close()


def test_dispatch_issued_checks_command_issued_type_only(tmp_path, monkeypatch):
    """_dispatch_issued must match command.issued events specifically, not any
    event carrying the command_id (doc_gap.design_dispatched is now bound to
    the dispatch id via its envelope command_id)."""
    from tests.unit.helpers import git_repo as _repo

    repo = _repo(tmp_path)
    # Audit event persisted with command_id=D-ARCHER but NO command.issued.
    ex, store, _ = _doc_gap_executor(
        repo,
        _doc_gap_crash_window_events(dispatch_id="D-ARCHER"),
        monkeypatch,
    )
    assert not ex._dispatch_issued("D-ARCHER"), (
        "an audit event alone must not count as an issued command"
    )

    # After a command.issued is written, _dispatch_issued returns True.
    store.append(
        "RUN", "v0.5", "command.issued",
        {"command": {"kind": "dispatch_agent", "params": {}, "command_id": "D-ARCHER"}},
        command_id="D-ARCHER",
    )
    assert ex._dispatch_issued("D-ARCHER"), (
        "a command.issued event must count as an issued command"
    )
    store.close()


def test_design_dispatched_audit_event_binds_command_id(tmp_path, monkeypatch):
    """The doc_gap.design_dispatched audit event's envelope command_id must be
    bound to the dispatch_id so the WAL trail explicitly links the audit
    record to the command.issued it announces."""
    from tests.unit.helpers import git_repo as _repo

    repo = _repo(tmp_path)
    ex, store, _ = _doc_gap_executor(
        repo,
        _doc_gap_crash_window_events(dispatch_id="D-ARCHER"),
        monkeypatch,
    )
    audit = [e for e in store.events("RUN") if e.type == "doc_gap.design_dispatched"]
    assert len(audit) == 1
    assert audit[0].command_id == "D-ARCHER", (
        "doc_gap.design_dispatched envelope command_id must equal dispatch_id"
    )
    store.close()


# -- SM-02 design_gap fail-closed checkpoint (#62 findings 1/2/3) -----------




def test_design_dispatched_persists_pre_dispatch_identities():
    """#62 finding 2: the reducer persists the nested Archer pre-dispatch
    identity snapshot (over the full design trio) so the checkpoint can stage
    only identity-changed design docs (Human/pre-dirty/unattributed changes
    stay out)."""
    s = _project([
        ("doc_comment.detected", {"record_id": "dg-1", "thread_ids": ["T-001"]}),
        ("doc_comment.adjudicated", {
            "record_id": "dg-1", "route": "design_gap",
            "responsible_role": "archer", "decision_ref": "abc",
        }),
        ("doc_gap.design_dispatched", {
            "record_id": "dg-1", "dispatch_id": "d-1",
            "phase": "design_revision", "role": "archer",
            "pre_dispatch_identities": {
                "architecture.md": "sha-arch",
                "interfaces.md": "sha-if",
                "test-plan.md": "sha-pre",
            },
        }),
    ])
    rec = s.doc_gaps["dg-1"]
    assert rec["pre_dispatch_identities"] == {
        "architecture.md": "sha-arch",
        "interfaces.md": "sha-if",
        "test-plan.md": "sha-pre",
    }
    assert rec["revision"] == "archer_dispatched"


def test_design_dispatched_design_review_carries_no_pre_identity():
    """The Prism review dispatch (design_review) carries no design write, so
    pre_dispatch_identities stays None (the snapshot is Archer-scoped)."""
    s = _project([
        ("doc_comment.detected", {"record_id": "dg-1", "thread_ids": ["T-001"]}),
        ("doc_comment.adjudicated", {
            "record_id": "dg-1", "route": "design_gap",
            "responsible_role": "archer", "decision_ref": "abc",
        }),
        ("doc_gap.design_dispatched", {
            "record_id": "dg-1", "dispatch_id": "d-prism",
            "phase": "design_review", "role": "prism",
        }),
    ])
    rec = s.doc_gaps["dg-1"]
    assert rec["revision"] == "prism_dispatched"
    assert rec["pre_dispatch_identities"] is None






def test_checkpoint_fail_closed_when_archer_outcome_not_successful(tmp_path):
    """#62 finding 1: a failed nested Archer outcome never emits
    doc_gap.design_revised (fail-closed - the held origin is never resumed on
    a failed revision)."""
    ex, store, _ = _checkpoint_executor(
        tmp_path,
        pre_identities={"architecture.md": "pre", "interfaces.md": "pre", "test-plan.md": "pre"},
        docs={"test-plan.md": "plan v2\n"},
    )
    rec = store.state("RUN").doc_gaps["dg-1"]
    ex._checkpoint_doc_gap_revision(
        _cmd(), None, "dg-1", rec, ["test-plan.md"],
        {"status": "failed", "failure_class": "agent_failed"},
    )
    revised = [e for e in store.events("RUN") if e.type == "doc_gap.design_revised"]
    assert not revised, "failed Archer outcome must NOT emit doc_gap.design_revised"
    store.close()


def test_checkpoint_fail_closed_when_no_design_doc_changed(tmp_path):
    """#62 finding 1: when no adjudicated design doc (a record document_path)
    changed identity during the dispatch, no doc_gap.design_revised is emitted
    - an un-attributable no-op revision never advances the record (and never
    commits Human / pre-dirty content as Archer output)."""
    from tracks import paths
    from tracks.executor.file_identity import path_identity

    repo = __import__("tests.unit.helpers", fromlist=["git_repo"]).git_repo(tmp_path)
    vdir = paths.version_dir(paths.tracks_home(repo), "v0.5")
    vdir.mkdir(parents=True, exist_ok=True)
    doc = vdir / "test-plan.md"
    doc.write_text("unchanged\n", encoding="utf-8")
    live_identity = path_identity(doc)
    # Pre-snapshot equals the live identity for test-plan.md -> no change.
    pre = {
        "architecture.md": "pre", "interfaces.md": "pre",
        "test-plan.md": live_identity,
    }
    ex, store, _ = _doc_gap_executor(
        repo,
        _design_dispatched_with_pre_identity(dispatch_id="D-ARCHER", pre_identities=pre),
        None,
    )
    rec = store.state("RUN").doc_gaps["dg-1"]
    ex._checkpoint_doc_gap_revision(
        _cmd(), None, "dg-1", rec, ["test-plan.md"], {"status": "done"},
    )
    revised = [e for e in store.events("RUN") if e.type == "doc_gap.design_revised"]
    assert not revised, (
        "no adjudicated design-doc identity change must NOT emit "
        "doc_gap.design_revised (would commit un-attributable / Human / "
        "pre-dirty content)"
    )
    store.close()


def test_checkpoint_emits_revised_when_changed_and_commit_succeeds(tmp_path):
    """#62 finding 1: the happy path - successful outcome, a changed
    adjudicated design doc, and a successful git checkpoint commit - emits
    doc_gap.design_revised carrying the revised identity and changed paths."""
    ex, store, repo = _checkpoint_executor(
        tmp_path,
        pre_identities={"architecture.md": "pre", "interfaces.md": "pre", "test-plan.md": "pre"},
        docs={"test-plan.md": "plan v2\n"},
    )
    rec = store.state("RUN").doc_gaps["dg-1"]
    ex._checkpoint_doc_gap_revision(
        _cmd(), None, "dg-1", rec, ["test-plan.md"], {"status": "done"},
    )
    revised = [e for e in store.events("RUN") if e.type == "doc_gap.design_revised"]
    assert len(revised) == 1, "successful revision must emit doc_gap.design_revised"
    payload = revised[0].payload
    assert "test-plan.md" in payload["changed_paths"]
    assert payload["revised_design_identity"]
    assert payload.get("commit_sha"), "design_revised must carry the commit sha"
    # The revised design doc IS committed (HEAD advanced).
    import subprocess

    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True,
    ).stdout.strip()
    show = subprocess.run(
        ["git", "show", "-s", "--format=%s", head], cwd=repo, capture_output=True,
        text=True,
    ).stdout.strip()
    assert "doc-gap design revision" in show
    store.close()


def test_commit_doc_gap_revision_raises_when_nothing_staged(tmp_path):
    """#62 finding 3: a git checkpoint commit failure is NOT swallowed
    (raises) and the caller never emits success.  Nothing-on-disk-to-stage
    and an empty changed-paths list are the deterministic failure cases for
    the unit-level contract (the live git-commit failure surfaces the same
    raise via ``check=True``)."""
    from tests.unit.helpers import git_repo as _repo

    repo = _repo(tmp_path)
    pre = {"architecture.md": "pre", "interfaces.md": "pre", "test-plan.md": "pre"}
    ex, store, _ = _doc_gap_executor(
        repo,
        _design_dispatched_with_pre_identity(
            dispatch_id="D-ARCHER", pre_identities=pre,
        ),
        None,
    )
    import pytest

    # No design doc written to disk -> nothing to stage -> raises (not None,
    # not a stale HEAD sha sold as success).
    with pytest.raises(RuntimeError):
        ex._commit_doc_gap_revision(["test-plan.md"])
    # Empty changed_paths must also raise (never silently return success).
    with pytest.raises(RuntimeError):
        ex._commit_doc_gap_revision([])
    store.close()


def test_commit_doc_gap_revision_excludes_unchanged_predirty_design_doc(tmp_path):
    """#62 finding 2: only identity-changed design docs are staged.  A sibling
    design doc carrying Human / pre-dirty content (UNCHANGED from the
    pre-dispatch snapshot) is NOT swept into the Archer commit, while the
    Archer-revised docs ARE committed (attribution via identity drift)."""
    from tracks import paths
    from tracks.executor.file_identity import path_identity

    repo = __import__("tests.unit.helpers", fromlist=["git_repo"]).git_repo(tmp_path)
    vdir = paths.version_dir(paths.tracks_home(repo), "v0.5")
    vdir.mkdir(parents=True, exist_ok=True)
    plan = vdir / "test-plan.md"
    plan.write_text("plan revised by Archer\n", encoding="utf-8")
    arch = vdir / "architecture.md"
    arch.write_text("human pre-dirty content\n", encoding="utf-8")
    iface = vdir / "interfaces.md"
    iface.write_text("if revised by Archer\n", encoding="utf-8")
    # Pre-snapshot: architecture.md matches its live (Human pre-dirty,
    # unchanged by Archer) -> NOT staged.  test-plan.md / interfaces.md have
    # sentinel pre-identities -> changed -> staged.
    pre = {
        "architecture.md": path_identity(arch),
        "interfaces.md": "pre-iface",
        "test-plan.md": "pre-plan",
    }
    ex, store, _ = _doc_gap_executor(
        repo,
        _design_dispatched_with_pre_identity(dispatch_id="D-ARCHER", pre_identities=pre),
        None,
    )
    rec = store.state("RUN").doc_gaps["dg-1"]
    ex._checkpoint_doc_gap_revision(
        _cmd(), None, "dg-1", rec, ["test-plan.md"], {"status": "done"},
    )
    import subprocess

    staged = subprocess.run(
        ["git", "show", "--name-only", "--format=", "HEAD"], cwd=repo,
        capture_output=True, text=True,
    ).stdout
    assert "test-plan.md" in staged, "Archer-revised doc must be committed"
    assert "interfaces.md" in staged, "Archer-revised sibling doc must be committed"
    assert "architecture.md" not in staged, (
        "a Human / pre-dirty design doc UNCHANGED from the pre-dispatch "
        "snapshot must NOT be committed as Archer output (AC-FR0236-01)"
    )
    store.close()


# -- SM-02 quarantine allowed-path prefix match (#62 finding 4) -------------


def test_path_in_allowed_matches_exact_and_directory_prefix():
    """#62 finding 4: the M-TEST Shield WRITE allowed_paths fall back to
    [layout.shield] directory prefixes; the quarantine must hold test-file
    changes under those dirs (exact match still serves the M-IMPL manifest)."""
    from tracks.executor.doc_comment import _path_in_allowed

    allowed = {"tests/integration", ".github/workflows/ci.yml"}
    assert _path_in_allowed("tests/integration/foo.py", allowed)
    assert _path_in_allowed("tests/integration/sub/bar.py", allowed)
    assert _path_in_allowed(".github/workflows/ci.yml", allowed)
    assert not _path_in_allowed("tracks/something.py", allowed)
    assert not _path_in_allowed("tests.txt", allowed)


def test_quarantine_holds_test_files_under_layout_dir_prefix(tmp_path):
    """A Shield WRITE whose allowed_paths are [layout.shield] directory
    prefixes holds its test-file changes (non-design origin artifact) so the
    design-stale discard path is reachable after a nested Archer revision."""
    from tracks.executor.doc_comment import (
        DocCommentOrigin,
        QuarantinedChange,
        quarantine_authorized_changes,
    )

    origin = DocCommentOrigin(
        run_id="RUN", role="shield", task_id=None, phase="WRITE",
        dispatch_id="ORIG-1", attempt=1,
    )
    descriptor = quarantine_authorized_changes(
        origin=origin,
        allowed_paths=("tests/integration/", "tests/e2e/"),
        pre_dirty_identities={},
        agent_changes=(
            QuarantinedChange(
                path="tests/integration/test_foo.py", operation="modify",
                baseline_identity=None, content_identity="sha-foo",
            ),
        ),
        design_identity="sha-design",
        run_identity="RUN",
    )
    assert descriptor.status == "held"
    assert len(descriptor.changes) == 1
    assert descriptor.changes[0].path == "tests/integration/test_foo.py"
