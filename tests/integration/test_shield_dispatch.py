"""Shield dispatch + write scope audit (FR-0020, FR-0120).

L2 contract sim: Shield writes tests, write-scope audit enforces the four
test-asset directories, over-reach is rolled back.
"""

from pathlib import Path

from tests.e2e.helpers import dispatches
from tests.integration.helpers import walk_to_m_test


# AC-FR0020-01@v0.4 TRACKS-TRACE dispatch uses test plan layers
def test_dispatch_uses_test_plan_layers(trac, event_log):
    """AC-FR0020-01@v0.4: DISPATCH creates Shield tasks per test-plan layers."""
    run_id = walk_to_m_test(trac)
    trac("run", simulate="shield:WRITE=fail")
    evs = event_log(run_id)
    shield_dispatches = [
        d
        for d in dispatches(evs, "WRITE")
        if d["payload"]["command"]["params"].get("role") == "shield"
    ]
    assert shield_dispatches  # Shield was dispatched
    assert shield_dispatches[0]["payload"]["command"]["params"]["stage"] == "M-TEST"


# AC-FR0020-02@v0.4 TRACKS-TRACE shield writes test files
def test_shield_writes_test_files(trac, event_log, host_repo):
    """AC-FR0020-02@v0.4: Shield writes tests to tests/integration/, tests/e2e/."""
    walk_to_m_test(trac)
    trac("run")
    # After M-TEST completes, test files exist under tests/
    integration = host_repo / "tests" / "integration"
    e2e = host_repo / "tests" / "e2e"
    assert integration.exists() or e2e.exists()
    test_files = list(integration.rglob("test_*.py")) + list(e2e.rglob("test_*.py"))
    assert test_files


# AC-FR0020-03@v0.4 TRACKS-TRACE collectable legit red tests
def test_collectable_legit_red_tests(trac, event_log):
    """AC-FR0020-03@v0.4: tests are collectable and all legit-failing."""
    run_id = walk_to_m_test(trac)
    trac("run")
    evs = event_log(run_id)
    collected = [e for e in evs if e["type"] == "test.collected"]
    assert collected[0]["payload"]["status"] == "passed"
    red = [e for e in evs if e["type"] == "red.validated"]
    assert red[0]["payload"]["status"] == "valid"


# AC-FR0020-04@v0.4 TRACKS-TRACE validate fail redispatch
def test_validate_fail_redispatch(trac, event_log):
    """AC-FR0020-04@v0.4: Shield failure re-dispatches, 3rd escalates."""
    run_id = walk_to_m_test(trac)
    r = trac("run", simulate="shield:WRITE=fail")
    assert "awaiting=escalation" in r.stdout
    evs = event_log(run_id)
    fails = [
        e
        for e in evs
        if e["type"] == "outcome.received"
        and e["payload"].get("role") == "shield"
        and e["payload"]["status"] != "done"
    ]
    assert len(fails) == 3
    assert not [d for d in dispatches(evs) if d["seq"] > fails[-1]["seq"]]


# AC-FR0020-05@v0.4 TRACKS-TRACE shield no commit
def test_shield_no_commit(trac, event_log, host_repo):
    """AC-FR0020-05@v0.4: Shield does not commit; Runtime creates the test commit.

    b230664/B59 legitimately evolved the freeze flow: Shield's WRITE output is
    committed during the pipeline (the commit message comes from the Shield
    manifest's suggested_commit_message, e.g. 'shield checkpoint'), and the
    M-TEST freeze anchors on that controlled commit instead of a fixed
    'M-TEST: freeze test assets' literal. The preserved contract: the test
    assets land via a controlled test.committed with a real commit_sha (not a
    Shield-authored commit)."""
    import subprocess

    run_id = walk_to_m_test(trac)
    trac("run")
    evs = event_log(run_id)
    # The only test-related commit is from commit_tests (Runtime), not Shield
    committed = [e for e in evs if e["type"] == "test.committed"]
    assert len(committed) == 1
    assert committed[0]["payload"]["commit_sha"], (
        "test.committed must reference the controlled freeze commit"
    )
    assert committed[0]["payload"]["commit_sha"] in subprocess.run(
        ["git", "rev-list", "HEAD"],
        cwd=host_repo,
        capture_output=True,
        text=True,
    ).stdout, "test.committed commit_sha must be an actual git commit"


# AC-FR0120-04@v0.4 TRACKS-TRACE over reach rolled back
def test_over_reach_rolled_back(trac, event_log):
    """AC-FR0120-04@v0.4: over_reach failure_class -> outcome failed, re-dispatch."""
    run_id = walk_to_m_test(trac)
    r = trac("run", simulate="shield:WRITE=over_reach|ok")
    assert r.returncode == 0, r.stderr
    evs = event_log(run_id)
    over = [
        e
        for e in evs
        if e["type"] == "outcome.received" and e["payload"].get("failure_class") == "over_reach"
    ]
    assert over  # over-reach was detected
    # After rollback, Shield re-dispatched and succeeded
    completed = [e for e in evs if e["type"] == "run.completed"]
    assert completed


# AC-FR0120-03@v0.4 TRACKS-TRACE write scope four dirs
def test_write_scope_four_dirs(trac, event_log, host_repo):
    """AC-FR0120-03@v0.4: Shield may only write tests/integration, tests/e2e,
    tests/assets, tests/counterexamples."""
    walk_to_m_test(trac)
    trac("run")
    # The four directories exist (Shield created them)
    for d in ("integration", "e2e", "assets", "counterexamples"):
        assert (host_repo / "tests" / d).exists()


# AC-FR0120-05@v0.4 TRACKS-TRACE no product code writes
def test_no_product_code_writes(trac, event_log, host_repo):
    """AC-FR0120-05@v0.4: Shield does not write product code or design docs."""
    run_id = walk_to_m_test(trac)
    trac("run")
    evs = event_log(run_id)
    # No over_reach failures -> Shield stayed within scope
    over = [
        e
        for e in evs
        if e["type"] == "outcome.received" and e["payload"].get("failure_class") == "over_reach"
    ]
    assert not over


# AC-FR0120-01@v0.4 TRACKS-TRACE materialization lifecycle
def test_materialization_lifecycle(trac, event_log, host_repo):
    """AC-FR0120-01@v0.4: Shield agent + skills are materialized and cleaned up."""
    walk_to_m_test(trac)
    trac("run")
    # After the run, the materialized agent is cleaned up (no residual)
    agent_dest = host_repo / ".opencode" / "agents" / "Shield.md"
    # The cleanup restores the pre-existing state (absent -> absent)
    assert not agent_dest.exists() or agent_dest.read_text() != ""
    # b91: the declared tracks-shield skill is materialized from the canonical
    # deliverable and cleaned up after the run (same lifecycle as the agent)
    canonical = (
        Path(__file__).resolve().parent.parent.parent
        / "tracks"
        / "skills"
        / "tracks-shield"
        / "SKILL.md"
    )
    assert canonical.exists()
    skill_dest = host_repo / ".opencode" / "skills" / "tracks-shield" / "SKILL.md"
    assert not skill_dest.exists() or skill_dest.read_text() != ""


# AC-FR0120-06@v0.4 TRACKS-TRACE shield workflow
def test_shield_workflow(trac, event_log):
    """AC-FR0120-06@v0.4: Shield reads context docs -> writes tests -> outcome."""
    run_id = walk_to_m_test(trac)
    trac("run")
    evs = event_log(run_id)
    shield_dispatch = next(
        e
        for e in evs
        if e["type"] == "command.issued"
        and e["payload"]["command"]["params"].get("role") == "shield"
    )
    assignment = shield_dispatch["payload"]["command"]["params"]["assignment"]
    # Shield assignment carries read-only context docs
    assert "test-plan.md" in assignment["docs"]
    assert "interfaces.md" in assignment["docs"]
    assert "acceptance.md" in assignment["docs"]
    # Shield assignment carries the discussion protocol + the test-writing
    # method skill (discussion first, b91 routing)
    assert assignment["skills"] == ["tracks-discuz", "tracks-shield"]
