"""Unit tests for the SM-02 adjudication marker protocol and resume decision.

Covers the #62 closure contracts that the integration journeys cannot reach
individually: marker grammar (fail-closed), record binding rules, and the
full decide_quarantine_resume reason matrix (empty/identity_current restores;
design_stale/run_stale/content_conflict discards).
"""

from __future__ import annotations

import hashlib

from tracks.executor.doc_comment import (
    AdjudicationMarker,
    QuarantinedChange,
    QuarantineDescriptor,
    ResumeDecision,
    classify_design_document_deltas,
    combined_design_identity,
    decide_quarantine_resume,
    legal_anchor_pairs,
    match_adjudication_marker,
    scan_adjudication_markers,
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


def test_empty_quarantine_restores_trivially():
    decision = _resume(_descriptor(status="empty"))
    assert (decision.action, decision.reason) == ("restore", "empty")


_HELD_CHANGE = QuarantinedChange(
    path="src/x.py", operation="modify",
    baseline_identity="b", content_identity="c1",
)


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
