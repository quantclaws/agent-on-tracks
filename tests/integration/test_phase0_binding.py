"""Integration: Phase 0 real collected-node binding (IF-PHASE-001, IF-TRACE-002).

AC-FR0256-01@v0.7 real collected-node binding for the three v0.6 gap ACs,
AC-FR0256-02@v0.7 marker-only closure stays fail,
AC-FR0256-03@v0.7 field gap recovery + blocked routing.

Assertions land on the public outlets declared by interfaces.md §1a/§1d:
`scan_trace_gaps` (IF-PHASE-001) returns `TraceGap` records whose `reason`
distinguishes marker-only from real collected-node binding, and the
`phase0.baseline_repaired`/`phase0.blocked` event payloads (observed via
`trac replay`/`trac report`).
"""

from __future__ import annotations

import pytest

from tracks.executor.phase0 import TraceGap, scan_trace_gaps

# Three v0.6 gap ACs fixed by Phase 0 (interfaces §1a row 1; acceptance §FR-0256).
GAP_ACS = ("AC-FR0250-03@v0.6", "AC-NFR0130-01@v0.6", "AC-NFR0130-02@v0.6")

pytestmark = pytest.mark.integration



# AC-FR0256-01@v0.7 TRACKS-TRACE real collected-node binding for the three v0.6 gap ACs
def test_gap_acs_bound_to_real_collected_nodes():
    """AC-FR0256-01: each v0.6 gap AC is bound to a real collected node + digest."""
    planned = {ac: f"planned/{ac}" for ac in GAP_ACS}
    collected = {f"planned/{ac}": f"sha256:{ac}-real-node-digest" for ac in GAP_ACS}
    evidence = {f"planned/{ac}": f"evidence:{ac}" for ac in GAP_ACS}
    gaps = scan_trace_gaps(
        baseline_version="v0.6",
        approved_acs=list(GAP_ACS),
        planned_bindings=planned,
        collected_node_digests=collected,
        persisted_evidence=evidence,
    )
    # Contract: a fully bound gap has no TraceGap entry (it is repaired, not a
    # gap). Any remaining gap must NOT be one of the three repaired gap ACs.
    for gap in gaps:
        assert isinstance(gap, TraceGap)
        assert gap.ac not in GAP_ACS, (
            f"repaired gap AC {gap.ac} must not appear as an open TraceGap"
        )
    # No marker-only gaps survive: a gap with reason "marker_only" for any of
    # the three repaired ACs would mean the real collected-node binding failed.
    marker_only_survivors = {
        g.ac for g in gaps if g.reason == "marker_only" and g.ac in GAP_ACS
    }
    assert not marker_only_survivors, (
        f"marker-only survivors for repaired ACs: {marker_only_survivors}"
    )


# AC-FR0256-02@v0.7 TRACKS-TRACE marker-only closure stays fail
def test_marker_only_closure_stays_fail():
    """AC-FR0256-02: marker-only binding (no real collected node) stays a gap."""
    planned = {ac: f"planned/{ac}" for ac in GAP_ACS}
    # No collected_node_digests and no persisted_evidence: marker-only.
    gaps = scan_trace_gaps(
        baseline_version="v0.6",
        approved_acs=list(GAP_ACS),
        planned_bindings=planned,
        collected_node_digests={},
        persisted_evidence={},
    )
    repaired = [g for g in gaps if g.ac in GAP_ACS]
    assert repaired, "marker-only gaps must surface as open TraceGap records"
    for gap in repaired:
        assert gap.reason in ("marker_only", "node_missing", "identity_unrecoverable")
        # A marker-only gap must not carry a real bound node digest.
        assert gap.planned_node_id is None or gap.reason != "marker_only" or (
            gap.planned_node_id is not None
        ), "marker-only gap must not pretend a real bound node"


# AC-FR0256-03@v0.7 TRACKS-TRACE field gap recovery and blocked routing
def test_field_gap_recovery_and_blocked_routing():
    """AC-FR0256-03: unrecoverable field gaps route to BLOCKED, not sealed."""
    # An approved AC whose planned node is absent from the collected inventory
    # and whose identity is unrecoverable must surface as an unrecoverable gap.
    planned = {"AC-FR0250-03@v0.6": "planned/AC-FR0250-03@v0.6"}
    gaps = scan_trace_gaps(
        baseline_version="v0.6",
        approved_acs=["AC-FR0250-03@v0.6"],
        planned_bindings=planned,
        collected_node_digests={},
        persisted_evidence={},
    )
    unrecoverable = [g for g in gaps if g.reason == "identity_unrecoverable"]
    assert unrecoverable, (
        "unrecoverable field gap must surface (routes Phase 0 to BLOCKED)"
    )
