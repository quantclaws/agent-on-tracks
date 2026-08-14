"""Three-worktree temporal isolation through public M-IMPL outlets."""

import subprocess
from pathlib import Path

import pytest

from tests.integration.v05_contract_helpers import events_of, run_m_impl_journey


def _git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _worktree_paths(repo):
    output = _git(repo, "worktree", "list", "--porcelain")
    return {
        Path(line.removeprefix("worktree "))
        for line in output.splitlines()
        if line.startswith("worktree ")
    }


@pytest.mark.integration
# AC-FR0070-01@v0.5 TRACKS-TRACE Devon candidate predates Shield bundle
# AC-FR0070-05@v0.5 TRACKS-TRACE three worktrees isolate frozen tests
# AC-FR0100-02@v0.5 TRACKS-TRACE manifest rebase and cleanup
# AC-FR0110-02@v0.5 TRACKS-TRACE gates combine candidate and authority
def test_three_worktree_composition_and_cleanup(trac, event_log, host_repo):
    original = _worktree_paths(host_repo)
    _, _, events = run_m_impl_journey(trac, event_log)
    red = events_of(events, "red.checkpointed")
    green = events_of(events, "green.committed")
    outcomes = [
        event
        for event in events_of(events, "outcome.received")
        if event["payload"].get("role") == "devon"
    ]
    assert red and green
    assert red[0]["seq"] < green[0]["seq"]
    assert all(
        not path.startswith(("tests/integration/", "tests/e2e/"))
        for event in outcomes
        for path in event["payload"].get("audit_evidence", {}).get("changed_paths", [])
    )
    assert _worktree_paths(host_repo) == original
