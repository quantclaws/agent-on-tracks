"""RGR contracts through public events and Git state."""

import pytest

from tests.integration.v05_contract_helpers import events_of, git_read, run_m_impl_journey


@pytest.mark.integration
# AC-FR0070-03@v0.5 TRACKS-TRACE immutable private R ref exists
# AC-FR0070-04@v0.5 TRACKS-TRACE compare-and-set preserves R
# AC-FR0120-01@v0.5 TRACKS-TRACE G parent and five trailers
# AC-FR0120-02@v0.5 TRACKS-TRACE ref trailer event lineage proof
# AC-FR0200-02@v0.5 TRACKS-TRACE git lineage survives replay
# AC-FR0220-03@v0.5 TRACKS-TRACE G commit trailers contain Tracks-Issue and Tracks-AC
def test_rgr_git_contract_happy_and_immutable(trac, event_log, host_repo):
    _, _, events = run_m_impl_journey(trac, event_log)
    reds = events_of(events, "red.checkpointed")
    greens = events_of(events, "green.committed")
    assert reds and greens
    red = reds[0]
    green = greens[0]
    ref = red["payload"]["ref"]
    r_sha = red["payload"]["r_sha"]
    g_sha = green["payload"]["g_sha"]
    assert git_read(host_repo, "rev-parse", ref) == r_sha
    assert git_read(host_repo, "rev-parse", f"{g_sha}^") == git_read(
        host_repo, "rev-parse", f"{r_sha}^"
    )
    message = git_read(host_repo, "log", "--format=%B", "-1", g_sha)
    assert f"Tracks-Task: {green['payload']['task_id']}" in message
    assert f"Tracks-Attempt: {green['payload']['attempt']}" in message
    assert f"Tracks-R: {r_sha}" in message
    assert "Tracks-Issue:" in message
    assert "Tracks-AC:" in message
    assert red["seq"] < green["seq"]


@pytest.mark.integration
# AC-FR0080-01@v0.5 TRACKS-TRACE stub-token Red is rejected in M-IMPL
def test_m_impl_red_classification_excludes_stub_tokens(trac, event_log):
    _, _, events = run_m_impl_journey(
        trac,
        event_log,
        simulate="devon:RED=stub_token_failure|ok",
    )
    invalid = [
        event for event in events_of(events, "verdict.failed")
        if event["payload"].get("check") == "red_invalid"
    ]
    assert invalid
    assert invalid[0]["payload"].get("task_id")
