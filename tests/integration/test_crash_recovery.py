"""Event replay resumes M-IMPL without repeating completed work."""

import pytest

from tests.integration.v05_contract_helpers import events_of, run_m_impl_journey


@pytest.mark.integration
# AC-FR0200-01@v0.5 TRACKS-TRACE replay resumes exact phase without rerun
def test_replay_does_not_duplicate_completed_task_evidence(trac, event_log):
    run_id, _, before = run_m_impl_journey(trac, event_log)
    assert events_of(before, "task.completed")
    resumed = trac("run")
    assert resumed.returncode == 0, resumed.stderr
    after = event_log(run_id)
    for event_type in ("red.checkpointed", "green.committed", "task.completed"):
        identities = [event["payload"] for event in events_of(after, event_type)]
        assert len(identities) == len({repr(sorted(item.items())) for item in identities})
