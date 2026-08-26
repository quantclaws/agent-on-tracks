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
    RGR_RED_DIFF,
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


def _checkpoint_red(executor, store, command_id):
    executor._do_checkpoint_red(
        Command("checkpoint_red", command_id=command_id), store.state("RUN"), None, False
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
# AC-FR0120-02@v0.5 TRACKS-TRACE G binds the latest red.checkpointed anchor after Shield fix overwrite
def test_rgr_lineage_binds_latest_checkpoint_after_shield_fix_overwrite(host_repo):
    """B91 (interfaces §5 IF-IMPL-004, v0.7 extension): in the test_defect
    overwrite scenario ``_on_test_committed`` re-points
    ``state.r_tree_identity`` at the sanctioned Shield fix commit, so the
    identity no longer exactly matches any ``red.checkpointed`` r_sha. The G
    lineage anchor must then be resolved through the task's checkpoint event
    family -- exact match authoritative, mismatch falls back to the family's
    seq-latest ``red.checkpointed`` r_sha -- never the fix commit itself, or
    ``Tracks-R`` carries a non-checkpoint sha and verify_lineage dead-ends
    into awaiting=rollback (sealed run 01M0S0FQ T-013). Asserted only through
    public exits: store command dispatch, ``red.checkpointed`` /
    ``green.committed`` payloads, git refs and G trailers."""
    store, task = _started_task_store(host_repo)
    task_id = task["task_id"]
    executor = Executor(store, host_repo, "RUN")

    # Round 1: legal failing red checkpoints into the R family.
    store.append(
        "RUN", "v0.5", "outcome.received",
        _devon_outcome("red", ["tests/unit/test_app.py"],
                       classification="assertion_failure", diff_ref=RGR_RED_DIFF),
        task_id=task_id,
    )
    _checkpoint_red(executor, store, "C-R1")
    # PRISM revise round: a second checkpoint joins the family on a fresh slot.
    store.append(
        "RUN", "v0.5", "prism.verdict",
        {"verdict": "revise", "defect_classification": "red_defect"},
        task_id=task_id,
    )
    store.append(
        "RUN", "v0.5", "outcome.received",
        _devon_outcome("red", ["tests/unit/test_app.py"],
                       classification="assertion_failure", diff_ref=RGR_RED_DIFF),
        task_id=task_id,
    )
    _checkpoint_red(executor, store, "C-R2")

    family = [e for e in store.events("RUN") if e.type == "red.checkpointed"]
    assert len(family) == 2, "two RED rounds must join two checkpoints to the family"
    older, latest = family[0], family[-1]
    assert older.payload["attempt"] != latest.payload["attempt"], (
        "each checkpoint occupies its own immutable slot"
    )

    # test_defect round: a real Shield fix commit is sanctioned via
    # test.committed; SM-01.14 re-points r_tree_identity at it.
    shield_sha = _shield_fix_commit(host_repo)
    store.append(
        "RUN", "v0.5", "test.committed",
        {"commit_sha": shield_sha, "test_count": 1},
    )
    assert store.state("RUN").r_tree_identity == shield_sha

    # GREEN is delivered on top of the fix; commit through public dispatch.
    store.append(
        "RUN", "v0.5", "outcome.received",
        _devon_outcome("green", ["tracks/app.py"], latest.payload["r_sha"],
                       diff_ref=RGR_GREEN_DIFF),
        task_id=task_id,
    )
    executor._do_commit_green(
        Command("commit_green", command_id="C-G"), store.state("RUN"), None, False
    )
    greens = [e for e in store.events("RUN") if e.type == "green.committed"]
    assert greens, "the anchored green claim must mint its G commit"
    green = greens[-1]

    # Anchor resolution fell back to the family's seq-latest checkpoint --
    # neither an older family member nor the repointed fix commit.
    assert latest.seq < green.seq
    assert green.payload["r_sha"] == latest.payload["r_sha"]
    assert green.payload["r_sha"] not in {older.payload["r_sha"], shield_sha}
    assert green.payload["attempt"] == latest.payload["attempt"]

    # R family immutability survives the overwrite round.
    for member in family:
        resolved = git_read(host_repo, "rev-parse", member.payload["ref"])
        assert resolved == member.payload["r_sha"], "R family refs must stay immutable"

    message = git_read(host_repo, "log", "--format=%B", "-1", green.payload["g_sha"])
    assert f"Tracks-Task: {task_id}" in message
    assert f"Tracks-Attempt: {latest.payload['attempt']}" in message
    assert f"Tracks-R: {latest.payload['r_sha']}" in message, (
        "G trailer must bind the seq-latest red.checkpointed r_sha, not the "
        "Shield fix commit that r_tree_identity was re-pointed to"
    )
    assert f"Tracks-R: {shield_sha}" not in message
    assert f"Tracks-R: {older.payload['r_sha']}" not in message

    # Cross-module lineage proof (the TASK_REVIEW consumer) validates the G.
    proof = verify_lineage(
        str(host_repo), "RUN", task_id, latest.payload["attempt"],
        green.payload["g_sha"], _events_as_dicts(store),
    )
    assert proof.r_before_g, "anchored G must pass the joint ref+trailer+event proof"
    assert proof.r_ref_exists and proof.g_trailers_valid and proof.event_order_valid


@pytest.mark.integration
# AC-FR0120-02@v0.5 TRACKS-TRACE lineage resolution fails closed with empty checkpoint family
def test_rgr_lineage_no_checkpoint_fail_closed(host_repo):
    """B91 resolution order step 3 (interfaces §5 IF-IMPL-004, v0.7): a task
    with no ``red.checkpointed`` events has no lineage anchor. verify_lineage
    must fail closed -- no G may be minted from an anchorless green claim, and
    no lineage declaration can validate against an empty checkpoint family."""
    store, task = _started_task_store(host_repo)
    task_id = task["task_id"]
    executor = Executor(store, host_repo, "RUN")

    green_claim = _devon_outcome(
        "green", ["tracks/app.py"], "f" * 40, diff_ref=RGR_GREEN_DIFF
    )
    store.append("RUN", "v0.5", "outcome.received", green_claim, task_id=task_id)
    executor._do_commit_green(
        Command("commit_green", command_id="C-G"), store.state("RUN"), None, False
    )
    greens = [e for e in store.events("RUN") if e.type == "green.committed"]
    assert greens == [], (
        "an anchorless green claim must never mint a lineage-carrying G commit"
    )
    checkpoints = [e for e in store.events("RUN") if e.type == "red.checkpointed"]
    assert checkpoints == [], "fixture sanity: the family stays empty"

    unrelated_g = git_read(host_repo, "rev-parse", "HEAD")
    proof = verify_lineage(
        str(host_repo), "RUN", task_id, 1, unrelated_g, _events_as_dicts(store)
    )
    assert proof.r_before_g is False, "no checkpoint family -> lineage fails closed"
    assert proof.r_ref_exists is False
    assert proof.event_order_valid is False
