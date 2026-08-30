"""IF-PHASE-001 Phase 0 real collected-node binding (T-004 RED unit).

Pins the Phase 0 trace-gap scanner declared in interfaces.md §1d *before* the
GREEN implementation lands:

- ``scan_trace_gaps`` separates marker-only from real collected-node evidence
  (FR-0256 / AC-FR0256-01): a planned binding is only repaired when its planned
  node is ACTUALLY collected with an identity digest (AC-FR0256-02 marker-only
  closures stay fail), and unrecoverable bindings route to BLOCKED
  (AC-FR0256-03).
- ``TraceGap.reason`` is one of ``marker_only`` / ``node_missing`` /
  ``identity_unrecoverable`` (interfaces §1d).

Each assertion fails today because ``scan_trace_gaps`` is a
``NotImplementedError("IF-PHASE-001")`` stub -- the M-IMPL RED on the
contract.
"""

from __future__ import annotations

from tracks.executor.phase0 import scan_trace_gaps

# AC-FR0256-01@v0.7 TRACKS-TRACE real collected-node binding

_GAP_ACS = ("AC-FR0250-03@v0.6", "AC-NFR0130-01@v0.6", "AC-NFR0130-02@v0.6")


def test_scan_trace_gaps_no_gaps_when_planned_nodes_collected_with_evidence():
    planned = {
        "AC-FR0250-03@v0.6": "tests/integration/test_evidence_reuse.py::test_prism_consumes_runtime_evidence_without_suite_rerun",
        "AC-NFR0130-01@v0.6": "tests/integration/test_identity_fields.py::test_fields_append_only",
    }
    collected = dict.fromkeys(planned.values(), "d" * 64)
    evidence = dict.fromkeys(planned.values(), "evidence-1")

    gaps = scan_trace_gaps("v0.6", _GAP_ACS, planned, collected, evidence)
    assert isinstance(gaps, list)
    assert gaps == []


# AC-FR0256-02@v0.7 TRACKS-TRACE marker-only closure must stay fail
def test_scan_trace_gaps_planned_node_collected_without_evidence_is_marker_only():
    planned = {"AC-FR0250-03@v0.6": "tests/a.py::test_bounds_real_node"}
    collected = {"tests/a.py::test_bounds_real_node": "d" * 64}
    evidence = {}  # marker/entry only, no machine evidence

    gaps = scan_trace_gaps("v0.6", _GAP_ACS, planned, collected, evidence)
    assert len(gaps) == 1
    gap = gaps[0]
    assert gap.ac == "AC-FR0250-03@v0.6"
    assert gap.reason == "marker_only"
    assert gap.planned_node_id == "tests/a.py::test_bounds_real_node"


# AC-FR0256-03@v0.7 TRACKS-TRACE uncollected planned node -> node_missing
def test_scan_trace_gaps_planned_node_not_in_collect_inventory_is_node_missing():
    planned = {"AC-FR0250-03@v0.6": "tests/collect.py::test_not_collected"}
    collected = {"tests/other.py::test_collected": "d" * 64}
    evidence = {}

    gaps = scan_trace_gaps("v0.6", _GAP_ACS, planned, collected, evidence)
    assert len(gaps) == 1
    gap = gaps[0]
    assert gap.ac == "AC-FR0250-03@v0.6"
    assert gap.reason == "node_missing"
    assert gap.planned_node_id == "tests/collect.py::test_not_collected"


# AC-FR0256-03@v0.7 TRACKS-TRACE collected node without identity digest
def test_scan_trace_gaps_collected_node_without_digest_is_identity_unrecoverable():
    planned = {"AC-FR0250-03@v0.6": "tests/a.py::test_identity_lost"}
    # node appears in collect inventory but carries no identity digest
    collected = {"tests/a.py::test_identity_lost": None}
    evidence = {"tests/a.py::test_identity_lost": "evidence-1"}

    gaps = scan_trace_gaps("v0.6", _GAP_ACS, planned, collected, evidence)
    assert len(gaps) == 1
    gap = gaps[0]
    assert gap.reason == "identity_unrecoverable"
    assert gap.planned_node_id == "tests/a.py::test_identity_lost"


# AC-FR0256-01@v0.7 TRACKS-TRACE unrepaired planned bindings are NOT reported
def test_scan_trace_gaps_unapproved_ac_is_not_reported():
    planned = {"AC-OTHER@v0.6": "tests/a.py::test_c"}
    collected = {"tests/a.py::test_c": "d" * 64}
    evidence = {"tests/a.py::test_c": "e1"}

    gaps = scan_trace_gaps("v0.6", _GAP_ACS, planned, collected, evidence)
    assert gaps == []  # only approved ACs are scanned


# AC-FR0256-01@v0.7 TRACKS-TRACE per-ac gap shares the planned binding
def test_scan_trace_gaps_multiple_gaps_preserve_reason_and_ac(tmp_path):
    planned = {
        "AC-FR0250-03@v0.6": "tests/a.py::test_missing",
        "AC-NFR0130-01@v0.6": "tests/b.py::test_collected_no_evidence",
    }
    collected = {
        "tests/a.py::test_other": "d" * 64,
        "tests/b.py::test_collected_no_evidence": "d" * 64,
    }
    evidence = {}

    gaps = scan_trace_gaps("v0.6", _GAP_ACS, planned, collected, evidence)
    by_ac = {g.ac: g for g in gaps}
    assert by_ac["AC-FR0250-03@v0.6"].reason == "node_missing"
    assert by_ac["AC-NFR0130-01@v0.6"].reason == "marker_only"

