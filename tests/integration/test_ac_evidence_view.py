"""AC evidence view (IF-QUERY-001).

The chain links the acceptance registry to plan anchors to run-plane
results; absent, superseded, or unreviewed evidence must be labelled,
never silently dropped (FR-0304).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit.test_server_projections_unit import _seed
from tracks.server.projections import project_ac_chain

pytestmark = pytest.mark.integration


# AC-FR0304-01@v0.9 TRACKS-TRACE ac chain complete
def test_ac_chain_complete(tmp_path: Path):
    """AC-FR0304-01: every registry AC resolves to nodes/result/candidate."""
    run = "run-chain-int"
    anchor_a = "tests/integration/test_ac_evidence_view.py::test_ac_chain_complete"
    anchor_b = "tests/integration/test_todo_center.py::test_waits_never_in_todos"
    registry = ["AC-FR0304-01", "AC-FR0304-02", "AC-FR0307-01"]
    plan = [
        {"ac_id": "AC-FR0304-01", "anchors": [anchor_a], "if_ids": ["IF-QUERY-001"]},
        {"ac_id": "AC-FR0304-02", "anchors": [anchor_b], "if_ids": ["IF-QUERY-001"]},
        {"ac_id": "AC-FR0307-01", "anchors": [], "if_ids": ["IF-QUERY-001"]},
    ]
    home, _repo = _seed(
        tmp_path,
        run_id=run,
        events=[{"type": "stage.entered", "payload": {"stage": "M-IMPL"}}],
        ac_ids=registry,
        test_tasks=plan,
    )

    chain = project_ac_chain(home, run)
    indexed = {entry["ac_id"]: entry for entry in chain}

    assert set(indexed) == set(registry)
    for entry in chain:
        assert "ac_id" in entry and "layer" in entry
        assert isinstance(entry["test_nodes"], list)
        assert isinstance(entry["evidence"], list)
        assert entry["latest_result"] in (None, "passed", "failed")
        candidate = entry["candidate_sha"]
        assert candidate is None or isinstance(candidate, str)
        for piece in entry["evidence"]:
            assert piece["status"] in ("ok", "missing", "stale", "unreviewed")
    assert anchor_a in indexed["AC-FR0304-01"]["test_nodes"]
    assert anchor_b in indexed["AC-FR0304-02"]["test_nodes"]
    assert indexed["AC-FR0307-01"]["test_nodes"] == []
    flagged = [
        piece["status"]
        for piece in indexed["AC-FR0304-01"]["evidence"]
        if piece["ref"] == anchor_a
    ]
    assert "missing" in flagged


# AC-FR0304-02@v0.9 TRACKS-TRACE missing stale unreviewed marked
def test_missing_stale_unreviewed_marked(tmp_path: Path):
    """AC-FR0304-02: absent/superseded/unreviewed proof is marked as such."""
    run = "run-marks-int"
    gone = "tests/integration/test_ac_evidence_view.py::test_missing_gone"
    old = "tests/integration/test_ac_evidence_view.py::test_missing_old"
    fresh = "tests/integration/test_ac_evidence_view.py::test_missing_fresh"
    registry = ["AC-FR0304-01", "AC-FR0304-02", "AC-FR0304-03"]
    plan = [
        {"ac_id": "AC-FR0304-01", "anchors": [gone], "if_ids": ["IF-QUERY-001"]},
        {"ac_id": "AC-FR0304-02", "anchors": [old], "if_ids": ["IF-QUERY-001"]},
        {"ac_id": "AC-FR0304-03", "anchors": [fresh], "if_ids": ["IF-QUERY-001"]},
    ]
    history = [
        {"type": "stage.entered", "payload": {"stage": "M-IMPL"}},
        {
            "type": "verdict.failed",
            "payload": {"node": old, "reason": "superseded"},
        },
        {
            "type": "prism.verdict",
            "payload": {"nodes": [fresh], "verdict": "revise"},
        },
    ]
    home, _repo = _seed(
        tmp_path,
        run_id=run,
        events=history,
        ac_ids=registry,
        test_tasks=plan,
    )

    chain = project_ac_chain(home, run)
    indexed = {entry["ac_id"]: entry for entry in chain}

    assert indexed["AC-FR0304-01"]["evidence"][0]["status"] == "missing"
    assert indexed["AC-FR0304-02"]["evidence"][0]["status"] == "stale"
    assert indexed["AC-FR0304-03"]["evidence"][0]["status"] == "unreviewed"
    seen = {piece["status"] for entry in chain for piece in entry["evidence"]}
    assert {"missing", "stale", "unreviewed"} <= seen
