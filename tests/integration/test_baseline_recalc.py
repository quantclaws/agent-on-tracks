"""BASELINE recomputation and frozen-test authority contracts."""

import pytest

from tests.integration.v05_contract_helpers import events_of, run_m_impl_journey


@pytest.mark.integration
# AC-FR0020-01@v0.5 TRACKS-TRACE current baseline freezes and plans
# AC-FR0020-02@v0.5 TRACKS-TRACE stale baseline needs attention
# AC-FR0020-03@v0.5 TRACKS-TRACE layer-attributed test paths freeze
# AC-FR0020-04@v0.5 TRACKS-TRACE frozen paths feed authority worktrees
def test_baseline_events_expose_digest_paths_and_error_semantics(trac, event_log):
    _, _, events = run_m_impl_journey(trac, event_log)
    frozen = events_of(events, "baseline.frozen")
    assert len(frozen) == 1
    payload = frozen[0]["payload"]
    assert payload["status"] == "current"
    assert payload["digest"]
    assert {"integration", "e2e"} <= {path.split("/")[1] for path in payload["frozen_test_paths"]}
    assert not events_of(events, "stage.rolled_back")
