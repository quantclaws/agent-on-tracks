"""Unit: M1-S1 oscillation detection (convergence plan 2026-09-05).

T-042 regression evidence: the :150 <-> rewalk anchor contradiction burned
~40 dispatches in a Devon ping-pong; the S1 signature must detect that
swap mechanically at the SECOND consecutive failure -- the current
failure (not yet stored) compared against the last recorded one.
"""

from __future__ import annotations

import json

from tracks.executor.oscillation import (
    OSCILLATION_CHECK,
    detect,
    detect_oscillation,
    last_failed_nodes,
    parse_failed_nodes,
)


def _ev(payload: dict) -> dict:
    return {"type": "verdict.failed", "payload": payload}


def _gate_failure(task_id: str, nodes: list) -> dict:
    """One GREEN_GATE TaskSelectionFailure verdict.failed.

    failed_nodes carries the production shape: a list of dicts with a
    "node" key (see _raise_hard_failure).
    """
    return _ev(
        {
            "check": "impl_defect",
            "task_id": task_id,
            "evidence": json.dumps(
                {"failed_nodes": nodes, "selection_id": "s"}
            ),
        }
    )


def _node(node: str) -> dict:
    return {"node": node, "status": "failed", "detail": ""}


def test_parse_failed_nodes_json_string_with_dict_nodes():
    ev = json.dumps({"failed_nodes": [_node("a.py::t1"), _node("b.py::t2")]})
    assert parse_failed_nodes(ev) == ["a.py::t1", "b.py::t2"]


def test_parse_failed_nodes_plain_string_nodes():
    ev = json.dumps({"failed_nodes": ["a.py::t1"]})
    assert parse_failed_nodes(ev) == ["a.py::t1"]


def test_parse_failed_nodes_fail_closed():
    assert parse_failed_nodes("not json") == []
    assert parse_failed_nodes({"no_key": 1}) == []
    assert parse_failed_nodes(None) == []
    assert parse_failed_nodes({"failed_nodes": "not-a-list"}) == []
    assert parse_failed_nodes(json.dumps({"failed_nodes": []})) == []


def test_detect_swap_signature():
    # T-042 shape: attempt 1 heals :150 but reddens rewalk; common stays.
    prev = [
        "test_verify_candidate.py::test_drift_marks_stale_no_refreeze",
        "test_inplace_repair.py::test_fix_new_candidate_rewalks_verify",
        "test_other.py::t",
    ]
    curr = [
        "test_inplace_repair.py::test_fix_new_candidate_rewalks_verify",
        "test_other.py::t",
        "test_release_preview.py::test_new_failure",
    ]
    out = detect(prev, curr)
    assert out is not None
    assert out["healed"] == [
        "test_verify_candidate.py::test_drift_marks_stale_no_refreeze"
    ]
    assert out["newly_red"] == ["test_release_preview.py::test_new_failure"]
    assert "test_inplace_repair.py::test_fix_new_candidate_rewalks_verify" in out["common"]


def test_detect_pure_progress_is_not_oscillation():
    # Shrinking failure set (pure healing) is normal convergence, not S1.
    assert detect(["a", "b"], ["a"]) is None


def test_detect_pure_regression_is_not_oscillation():
    # Growing failure set is a fresh defect, not a swap.
    assert detect(["a"], ["a", "b"]) is None


def test_detect_disjoint_families_not_oscillation():
    # Two wholly different failure sets: not the same failure family.
    assert detect(["a", "b"], ["c", "d"]) is None


def test_detect_oscillation_trips_at_second_failure():
    # The first failure is already recorded; the second (current, not yet
    # stored) must trip the signature -- no third failure required.
    events = [
        _gate_failure("T-042", [_node("a"), _node("b")]),
        {"type": "verdict.passed", "payload": {}},
    ]
    current = json.dumps({"failed_nodes": [_node("b"), _node("c")]})
    out = detect_oscillation(events, "T-042", current)
    assert out is not None
    assert out["healed"] == ["a"]
    assert out["newly_red"] == ["c"]


def test_detect_oscillation_first_failure_never_trips():
    events = [{"type": "verdict.passed", "payload": {}}]
    current = json.dumps({"failed_nodes": [_node("a"), _node("b")]})
    assert detect_oscillation(events, "T-042", current) is None


def test_detect_oscillation_unparseable_current_fail_closed():
    events = [_gate_failure("T-042", [_node("a"), _node("b")])]
    assert detect_oscillation(events, "T-042", "not json") is None


def test_last_failed_nodes_resets_on_task_success():
    # A verdict.passed for the task between failures resets the window:
    # oscillation is only defined across CONSECUTIVE failures.
    events = [
        _gate_failure("T-042", [_node("a"), _node("b")]),
        {"type": "verdict.passed", "payload": {"check": "green", "task_id": "T-042"}},
        _gate_failure("T-001", [_node("x")]),
    ]
    assert last_failed_nodes(events, "T-042") is None
    current = json.dumps({"failed_nodes": [_node("b"), _node("c")]})
    assert detect_oscillation(events, "T-042", current) is None


def test_last_failed_nodes_filters_other_tasks():
    events = [
        _gate_failure("T-001", [_node("a"), _node("b")]),
        _gate_failure("T-042", [_node("x")]),
        _gate_failure("T-001", [_node("b"), _node("c")]),
    ]
    assert last_failed_nodes(events, "T-001") == ["b", "c"]
    assert last_failed_nodes(events, "T-042") == ["x"]


def test_last_failed_nodes_skips_other_checks():
    # Non-impl_defect failures (lint, contract_error) are not anchor
    # outcomes and must not shadow the last impl_defect evidence.
    events = [
        _gate_failure("T-042", [_node("a")]),
        _ev(
            {
                "check": "lint",
                "task_id": "T-042",
                "evidence": "lint findings",
            }
        ),
    ]
    assert last_failed_nodes(events, "T-042") == ["a"]


def test_check_name_is_contract_conflict():
    # The routed classification: mutually exclusive anchors are a contract
    # conflict owned by the ruling authority, never a writer retry.
    assert OSCILLATION_CHECK == "contract_conflict"
