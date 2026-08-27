"""RGR contracts through public events and Git state."""

import subprocess

import pytest

from tests.integration.v05_contract_helpers import (
    events_of,
    git_read,
    run_m_impl_journey,
)
from tests.unit.helpers import (
    RGR_GREEN_DIFF,
)
from tests.unit.helpers import (
    m_impl_started_task as _started_task_store,
)
from tracks.executor.executor import Executor
from tracks.executor.rgr import verify_lineage
from tracks.kernel.events import Command


def _shield_fix_commit(repo) -> str:
    """Create a real Shield-fix style commit on the repo working tree."""
    tests_dir = repo / "tests" / "unit"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / "test_shield_fix.py").write_text(
        "def test_shield_fix():\n    assert True\n", encoding="utf-8"
    )
    subprocess.run(
        ["git", "add", "tests/unit/test_shield_fix.py"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "shield: repair defective R-frozen tests"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return git_read(repo, "rev-parse", "HEAD")


def _devon_outcome(phase, changed, r_identity=None, *, classification=None, diff_ref=None):
    """Structured M-IMPL Devon outcome as delivered over the public contract."""
    outcome = {
        "role": "devon",
        "status": "done",
        "phase": phase,
        "changed_paths": changed,
        "commands": [],
        "manifest_compliance": True,
        "pre_identity": "pre",
        "post_identity": "post",
        "implemented_if_ids": [],
        **({"r_identity": r_identity} if r_identity else {}),
        **({"diff_ref": diff_ref} if diff_ref is not None else {}),
    }
    if classification is not None:
        outcome["results"] = [{"classification": classification}]
    return outcome


def _checkpoint_red(executor, task_id, command_id):
    """Checkpoint one legal RED round through the public command layer."""
    executor.issue(
        Command("checkpoint_red", {"stage": "M-IMPL", "task_id": task_id},
                command_id=command_id)
    )


def _commit_green(executor, task_id, command_id):
    """Commit a claimed GREEN through the public command layer."""
    executor.issue(
        Command("commit_green", {"stage": "M-IMPL", "task_id": task_id},
                command_id=command_id)
    )


# A follow-up product delta over the already-committed tracks/app.py, used by
# the second anchored green claim so consecutive GREENs never share one
# new-file diff (the first new-file application is immutable history).
_GREEN_UPDATE_DIFF = (
    "diff --git a/tracks/app.py b/tracks/app.py\n"
    "--- a/tracks/app.py\n"
    "+++ b/tracks/app.py\n"
    "@@ -1 +1,2 @@\n"
    " IMPLEMENTED_IF = \"IF-IMPL-001\"\n"
    "+LINEAGE_ANCHOR = \"IF-IMPL-004\"\n"
)


def _events_as_dicts(store):
    return [
        {"type": e.type, "payload": dict(e.payload), "seq": e.seq}
        for e in store.events("RUN")
    ]


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
        event
        for event in events_of(events, "verdict.failed")
        if event["payload"].get("check") == "red_invalid"
    ]
    assert invalid
    assert invalid[0]["payload"].get("task_id")


@pytest.mark.integration
# AC-FR0120-02@v0.5 TRACKS-TRACE lineage resolution fails closed with empty checkpoint family
def test_rgr_lineage_no_checkpoint_fail_closed(host_repo):
    """B91 resolution order step 3 (interfaces §5 IF-IMPL-004, v0.7): a task
    with no ``red.checkpointed`` events has no lineage anchor. verify_lineage
    must fail closed -- no G may be minted from an anchorless green claim,
    no lineage declaration can validate against an empty checkpoint family,
    and the blocked claim must surface through the frozen B57 exit:
    ``verdict.failed(check=lineage)`` routed to ``awaiting=rollback``."""
    store, task = _started_task_store(host_repo)
    task_id = task["task_id"]
    executor = Executor(store, host_repo, "RUN")

    green_claim = _devon_outcome(
        "green", ["tracks/app.py"], "f" * 40, diff_ref=RGR_GREEN_DIFF
    )
    store.append("RUN", "v0.5", "outcome.received", green_claim, task_id=task_id)
    _commit_green(executor, task_id, "C-G")
    greens = [e for e in store.events("RUN") if e.type == "green.committed"]
    assert greens == [], (
        "an anchorless green claim must never mint a lineage-carrying G commit"
    )
    checkpoints = [e for e in store.events("RUN") if e.type == "red.checkpointed"]
    assert checkpoints == [], "fixture sanity: the family stays empty"

    # Step-3 fail-closed proof runs FIRST so its counterexample surface
    # (CE-2) stays observable independently of the routing leg below.
    unrelated_g = git_read(host_repo, "rev-parse", "HEAD")
    proof = verify_lineage(
        str(host_repo), "RUN", task_id, 1, unrelated_g, _events_as_dicts(store)
    )
    assert proof.r_before_g is False, "no checkpoint family -> lineage fails closed"
    assert proof.r_ref_exists is False
    assert proof.event_order_valid is False

    # Routing leg of resolution step 3 -- interfaces §5 IF-IMPL-004 v0.7
    # observable exits: "不匹配仍路由 TASK_REVIEW check=lineage ->
    # awaiting=rollback（B57 语义）" (repeated as this regression's asserted
    # exit in test-plan §8 row 6: "无 checkpoint fail-closed 路由
    # check=lineage → awaiting=rollback"). The public verdict that REFUSES
    # this anchorless claim at its dispatch must classify it as a LINEAGE
    # failure on the public event stream, and the frozen B57 router must park
    # the run for Human adjudication -- recorded lineage is untrustworthy
    # and not reworkable in place.
    lineage_failures = [
        e
        for e in store.events("RUN")
        if e.type == "verdict.failed" and e.payload.get("check") == "lineage"
    ]
    assert lineage_failures, (
        "the anchorless claim's blocking verdict must be classified "
        "check=lineage on the public event stream (test-plan §8 row 6 "
        "routing leg), not any diagnostic category"
    )
    assert lineage_failures[0]["payload"].get("task_id") == task_id
    projected = store.state("RUN")
    assert getattr(projected, "awaiting", None) == "rollback", (
        "the frozen B57 router must route check=lineage to awaiting=rollback "
        "(Human approves discarding M-IMPL progress; trac recover re-enters)"
    )
