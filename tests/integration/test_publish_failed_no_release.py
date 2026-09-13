"""D3: a failed/incomplete publish batch must never close as released.

Real walk over a bare remote (no stand-in API needed for merge/tag plans):
the publish batch stops on the first failure, the run parks at M-PUBLISH with
the failure reason rendered, and no trace-close/seal/run.completed(released)
lands. Repairing the remote and driving ``trac run`` replays the SAME
execute_publish command (D-13 recovery): finished operations reconcile as
``reconciled_skip`` (no duplicate effect) and the unfinished ones continue to
a normal ``released`` terminal (SM-01.15 -> SM-01.16).
"""

from __future__ import annotations

import subprocess

import pytest

from tests.e2e.helpers import (
    generate_report_md,
    init_bare_remote,
    push_ancestor_main,
    walk_to_awaiting_release,
)
from tests.integration.test_journey_versioning import _declare_journey_contract

pytestmark = pytest.mark.integration


def _publish_events(events) -> list[dict]:
    return [
        e
        for e in events
        if e["type"] in ("publish.planned", "publish.executed", "publish.failed")
    ]


def _completed(events) -> list[dict]:
    return [e for e in events if e["type"] == "run.completed"]


# AC-FR0275-01/AC-FR0276-01@v0.8: a branch_missing batch parks fail-closed;
# the repaired retry completes the batch and only then releases.
def test_branch_missing_publish_parks_without_release(
    host_repo, trac, event_log, ci_echo_standin
):
    bare, initial = init_bare_remote(host_repo, "d3_bare.git", push_main=False)
    run_id = walk_to_awaiting_release(
        trac,
        pre_seed_hook=lambda repo: _declare_journey_contract(
            repo, ["merge:main", "tag:{feature_tag}"]
        ),
    )
    assert run_id
    assert trac("release", "--action", "release").returncode == 0
    assert trac("run").returncode == 0

    events = event_log(run_id)
    failed = [e for e in events if e["type"] == "publish.failed"]
    assert failed, "the missing remote branch must fail closed"
    assert failed[-1]["payload"]["reason"] == "branch_missing"
    assert not _completed(events), "a failed publish must not complete the run"
    assert not [
        e for e in events if e["type"] == "milestone.trace_closed"
    ], "a failed publish must not trace-close"
    assert not [
        e for e in events if e["type"] == "milestone.sealed"
    ], "a failed publish must not seal"
    planned_targets = [
        e["payload"]["target"] for e in events if e["type"] == "publish.planned"
    ]
    assert planned_targets == ["main"], "stop-on-first-failure: tag stays unplanned"

    status = trac("status")
    assert "publish=blocked" in status.stdout
    assert "branch_missing" in status.stdout
    assert "retry_tail" in status.stdout
    report = generate_report_md(trac, host_repo)
    assert "publish: blocked reason=branch_missing" in report

    # Repair the remote: publish an ancestor of the frozen candidate as main.
    push_ancestor_main(host_repo, initial)
    assert trac("run").returncode == 0, "the repaired retry must resume publish"

    events = event_log(run_id)
    executed = [
        (e["payload"]["target"], e["payload"]["status"])
        for e in events
        if e["type"] == "publish.executed"
    ]
    assert ("main", "done") in executed
    assert ("v0.8.0", "done") in executed
    completed = _completed(events)
    assert len(completed) == 1
    assert completed[0]["payload"]["terminal_state"] == "released"
    assert "terminal=released" in trac("status").stdout

    ls = subprocess.run(
        ["git", "ls-remote", str(bare)], capture_output=True, text=True, check=True
    ).stdout
    candidate = next(
        e["payload"]["candidate_sha"]
        for e in events
        if e["type"] == "candidate.frozen"
    )
    assert f"{candidate}\trefs/heads/main" in ls
    assert f"{candidate}\trefs/tags/v0.8.0" in ls


# AC-FR0275-02/AC-FR0276-02@v0.8: a partially executed batch resumes by
# reconciling the finished operation (no duplicate effect) and completing the
# missing one; the run only then releases.
def test_partial_publish_retry_reconciles_and_completes(
    host_repo, trac, event_log, ci_echo_standin
):
    bare, initial = init_bare_remote(host_repo, "d3_partial.git")
    run_id = walk_to_awaiting_release(
        trac,
        pre_seed_hook=lambda repo: _declare_journey_contract(
            repo, ["merge:main", "merge:topic"]
        ),
    )
    assert run_id
    assert trac("release", "--action", "release").returncode == 0
    assert trac("run").returncode == 0

    events = event_log(run_id)
    failed = [e for e in events if e["type"] == "publish.failed"]
    assert failed and failed[-1]["payload"]["reason"] == "branch_missing"
    executed = [
        (e["payload"]["target"], e["payload"]["status"])
        for e in events
        if e["type"] == "publish.executed"
    ]
    assert executed == [("main", "done")], "the first merge finished before the stop"
    assert not _completed(events)
    failure_seq = failed[-1]["seq"]

    # Repair the missing branch, then retry: no second main push/effect.
    subprocess.run(
        ["git", "push", "-q", "origin", f"{initial}:refs/heads/topic"],
        cwd=host_repo,
        check=True,
        capture_output=True,
    )
    assert trac("run").returncode == 0

    events = event_log(run_id)
    resumed = [
        (e["payload"]["target"], e["payload"]["status"])
        for e in events
        if e["type"] == "publish.executed" and e["seq"] > failure_seq
    ]
    assert resumed == [("main", "reconciled_skip"), ("topic", "done")]
    assert len([e for e in events if e["type"] == "publish.executed"]) == 3
    completed = _completed(events)
    assert len(completed) == 1
    assert completed[0]["payload"]["terminal_state"] == "released"
    ls = subprocess.run(
        ["git", "ls-remote", str(bare)], capture_output=True, text=True, check=True
    ).stdout
    candidate = next(
        e["payload"]["candidate_sha"]
        for e in events
        if e["type"] == "candidate.frozen"
    )
    assert f"{candidate}\trefs/heads/main" in ls
    assert f"{candidate}\trefs/heads/topic" in ls
    assert "terminal=released" in trac("status").stdout
