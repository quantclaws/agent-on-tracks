"""E2E: dual-host nine-scenario fail-closed acceptance.

AC-FR0266-01@v0.7 dual-host nine scenarios fail-closed,
AC-FR0266-03@v0.7 crash recovery replay ok,
AC-NFR0142-02@v0.7 demo host crash recovery rebuild.

Happy path only (test-plan §11.2): both hosts 9 scenarios blocked, summaries
all_fail_closed=true, crash_recovery=replay_ok.
"""

from __future__ import annotations

import pytest

from tests.integration.test_failclosed_scenarios import _start_v07_run

pytestmark = pytest.mark.e2e



# AC-FR0266-01@v0.7 TRACKS-TRACE dual-host nine scenarios all fail-closed
# AC-FR0266-03@v0.7 TRACKS-TRACE dual-host nine scenarios all fail-closed
# AC-NFR0142-02@v0.7 TRACKS-TRACE dual-host nine scenarios all fail-closed
def test_dualhost_nine_scenarios_all_fail_closed(trac, host_repo, event_log):
    """Both tracks + demo-pytest hosts: 9 scenarios blocked, recovery replay_ok.

    SHIELD_FIX (issue 100, dualhost fixture): the old fixture seeded the
    v0.5-baseline and issued a single bare ``trac run`` -- no active run and
    no §4.2 registry journey step, the same perpetual-Red defect class the
    integration anchors suffered (fixed by b085750). The repair reuses the
    proven ``_start_v07_run`` activation (walk_to_await_human -> approve ->
    M-DESIGN draft -> _seed_guard_registry -> bounded drives) and queries
    ``event_log`` with the REAL run_id (the conftest filters by literal
    run_id equality, so 'latest' matched nothing).
    """
    run_id = _start_v07_run(trac, host_repo)
    events = event_log(run_id)

    summaries = [e for e in events if e["type"] == "failclosed.summary"]
    assert len(summaries) == 2, (
        f"exactly two fail-closed summaries expected (tracks + demo); "
        f"got {len(summaries)}"
    )
    for summary in summaries:
        payload = summary["payload"]
        assert payload["scenarios_count"] == 9, (
            f"each host must cover 9 scenarios; got {payload.get('scenarios_count')}"
        )
        assert payload["all_fail_closed"] is True, (
            f"host {payload.get('host')} not all fail-closed"
        )
        assert payload["crash_recovery"] == "replay_ok", (
            f"host {payload.get('host')} crash recovery not replay_ok"
        )
        assert payload["status"] == "passed"

    hosts = {s["payload"]["host"] for s in summaries}
    assert hosts == {"tracks", "demo-pytest"}, (
        f"both hosts must be exercised; got {hosts}"
    )
