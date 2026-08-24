"""E2E: dual-host nine-scenario fail-closed acceptance.

AC-FR0266-01@v0.7 dual-host nine scenarios fail-closed,
AC-FR0266-03@v0.7 crash recovery replay ok,
AC-NFR0142-02@v0.7 demo host crash recovery rebuild.

Happy path only (test-plan §11.2): both hosts 9 scenarios blocked, summaries
all_fail_closed=true, crash_recovery=replay_ok.
"""

from __future__ import annotations

import pytest

from tests.hotfix_support import seed_v05_approved_baseline

pytestmark = pytest.mark.e2e



# AC-FR0266-01@v0.7 TRACKS-TRACE dual-host nine scenarios all fail-closed
# AC-FR0266-03@v0.7 TRACKS-TRACE dual-host nine scenarios all fail-closed
# AC-NFR0142-02@v0.7 TRACKS-TRACE dual-host nine scenarios all fail-closed
def test_dualhost_nine_scenarios_all_fail_closed(trac, host_repo, event_log):
    """Both tracks + demo-pytest hosts: 9 scenarios blocked, recovery replay_ok.

    Legal Red anchor: the dual-host fail-closed demonstration
    (IF-FAILCLOSED-001) is not wired into the run loop, so no
    `failclosed.summary` events are emitted; the assertion on the two-host
    summary set fails.
    """
    seed_v05_approved_baseline(host_repo, version="v0.7")
    trac("run")
    run_id = "latest"
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
