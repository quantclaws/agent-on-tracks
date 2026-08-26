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
# AC-FR0120-02@v0.5 TRACKS-TRACE G binds the latest red.checkpointed anchor after Shield fix overwrite
def test_rgr_lineage_binds_latest_checkpoint_after_shield_fix_overwrite(host_repo):
    """B91 closed resolution order (interfaces §5 IF-IMPL-004 v0.7 extension),
    asserted only through public exits -- ``red.checkpointed`` /
    ``green.committed`` payloads, git R-family refs and G trailers,
    ``verify_lineage``'s joint ref+trailer+event proof:

    - step 1 (exact match authoritative): after SM-01.14 re-points
      ``state.r_tree_identity`` at a sha that exactly equals a checkpoint
      family member, that member is the authoritative anchor even when it is
      NOT the seq-latest one;
    - step 2 (mismatch fallback): after a further sanctioned overwrite onto a
      real Shield fix commit sha that matches no member, the anchor falls
      back to the family's seq-latest ``red.checkpointed.r_sha`` -- never the
      fix commit itself, or ``Tracks-R`` carries a non-checkpoint sha and
      verify_lineage dead-ends into awaiting=rollback (sealed run 01M0S0FQ
      T-013);
    - G commits are minted once per resolved anchor: a duplicate green claim
      on the same anchor must not mint a second G commit nor relabel the
      recorded one."""
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

    # Step 1 -- exact match authoritative, recency notwithstanding: the
    # sanctioned test_defect overwrite re-points r_tree_identity at a sha
    # equal to the OLDER family member's r_sha, so step 1 fires on a
    # non-latest member and must win over any seq-latest preference.
    store.append(
        "RUN", "v0.5", "test.committed",
        {"commit_sha": older.payload["r_sha"], "test_count": 1},
    )
    assert store.state("RUN").r_tree_identity == older.payload["r_sha"]

    # First GREEN is delivered over the matched member; commit through the
    # public dispatch.
    store.append(
        "RUN", "v0.5", "outcome.received",
        _devon_outcome("green", ["tracks/app.py"], older.payload["r_sha"],
                       diff_ref=RGR_GREEN_DIFF),
        task_id=task_id,
    )
    executor._do_commit_green(
        Command("commit_green", command_id="C-G"), store.state("RUN"), None, False
    )
    greens = [e for e in store.events("RUN") if e.type == "green.committed"]
    assert len(greens) == 1, (
        "the exact-match anchored green claim must mint exactly one G commit"
    )
    first = greens[0]

    # Anchor resolution honoured step 1: the matched member itself --
    # neither the newer sibling nor any non-family sha.
    assert first.seq > latest.seq
    assert first.payload["r_sha"] == older.payload["r_sha"], (
        "step 1: an exact family match is the authoritative lineage anchor "
        "even when it is not the seq-latest checkpoint"
    )
    assert first.payload["attempt"] == older.payload["attempt"]
    first_message = git_read(host_repo, "log", "--format=%B", "-1", first.payload["g_sha"])
    assert f"Tracks-R: {older.payload['r_sha']}" in first_message
    assert f"Tracks-R: {latest.payload['r_sha']}" not in first_message, (
        "recency must not override an exact step-1 match"
    )

    # The matched anchor's own slot validates as a joint proof.
    proof_first = verify_lineage(
        str(host_repo), "RUN", task_id, first.payload["attempt"],
        first.payload["g_sha"], _events_as_dicts(store),
    )
    assert proof_first.r_before_g, "exact-match anchored G must pass the joint proof"
    assert proof_first.r_ref_exists and proof_first.g_trailers_valid
    assert proof_first.event_order_valid

    # Step 2 -- identity mismatch fallback: a further sanctioned Shield fix
    # commit (real distinct-tree sha, matching no family member) re-points
    # r_tree_identity; SM-01.14 now leaves the identity off the family.
    shield_sha = _shield_fix_commit(host_repo)
    store.append(
        "RUN", "v0.5", "test.committed",
        {"commit_sha": shield_sha, "test_count": 1},
    )
    assert store.state("RUN").r_tree_identity == shield_sha

    # Second GREEN is delivered on top of the fix; commit through public dispatch.
    store.append(
        "RUN", "v0.5", "outcome.received",
        _devon_outcome("green", ["tracks/app.py"], latest.payload["r_sha"],
                       diff_ref=_GREEN_UPDATE_DIFF),
        task_id=task_id,
    )
    executor._do_commit_green(
        Command("commit_green", command_id="C-G2"), store.state("RUN"), None, False
    )
    greens = [e for e in store.events("RUN") if e.type == "green.committed"]
    assert len(greens) == 2, "the fallback-anchored green claim must mint its G commit"
    green = greens[-1]

    # Fallback resolution landed on the family's seq-latest checkpoint --
    # neither an older family member nor the repointed fix commit.
    assert latest.seq < green.seq
    assert green.payload["r_sha"] == latest.payload["r_sha"]
    assert green.payload["r_sha"] not in {older.payload["r_sha"], shield_sha}
    assert green.payload["attempt"] == latest.payload["attempt"]

    # R family immutability survives both overwrite rounds.
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

    # Cross-module lineage proof (the TASK_REVIEW consumer) validates the
    # fallback-anchored G under the slot it advertises.
    proof = verify_lineage(
        str(host_repo), "RUN", task_id, latest.payload["attempt"],
        green.payload["g_sha"], _events_as_dicts(store),
    )
    assert proof.r_before_g, "anchored G must pass the joint ref+trailer+event proof"
    assert proof.r_ref_exists and proof.g_trailers_valid and proof.event_order_valid

    # Deduplicated minting shares the resolved-anchor key: a duplicate green
    # claim over the same sanctioned anchor mints nothing further and never
    # relabels the recorded G (interfaces §5 IF-IMPL-004 v0.7 extension,
    # dedup keyed through the corrected r_sha).
    duplicated_claim = _devon_outcome(
        "green", ["tracks/app.py"], latest.payload["r_sha"], diff_ref=_GREEN_UPDATE_DIFF
    )
    store.append("RUN", "v0.5", "outcome.received", duplicated_claim, task_id=task_id)
    executor._do_commit_green(
        Command("commit_green", command_id="C-G3"), store.state("RUN"), None, False
    )
    greens_after_dupe = [e for e in store.events("RUN") if e.type == "green.committed"]
    assert len(greens_after_dupe) == 2, (
        "a duplicate green claim on the same resolved anchor must not mint "
        "another G commit"
    )
    assert greens_after_dupe[-1].payload == greens[-1].payload, (
        "the recorded fallback-anchored G stays byte-stable across replays"
    )


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
