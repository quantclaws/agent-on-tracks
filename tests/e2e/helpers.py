"""Shared E2E helpers (kept out of individual test modules to avoid R0801 duplication)."""

import re


def walk_to_await_human(trac, stdin="构建一个事件溯源运行时", version="v0.1"):
    """init -> start -> triage go -> three review rounds -> AWAIT_HUMAN (FR-0180)."""
    assert trac("init").returncode == 0
    r = trac("start", version, stdin=stdin)
    assert r.returncode == 0, r.stderr
    run_id = re.search(r"run (\S+) started", r.stdout).group(1)
    assert trac("run").returncode == 0
    assert trac("triage", "go").returncode == 0
    for _ in ("M-STORY", "M-SPEC", "M-ACC"):
        r = trac("run")
        assert r.returncode == 0, r.stderr
        assert "awaiting=review" in r.stdout
        assert trac("review", "no-comment").returncode == 0
    r = trac("run")
    assert r.returncode == 0, r.stderr
    assert "awaiting=approval" in r.stdout  # FR-0180: hard human gate
    return run_id


def walk_to_m_test_complete(trac, stdin="构建一个事件溯源运行时", version="v0.1"):
    """approval -> M-DESIGN (Archer drafts the trio, Prism passes) ->
    M-TEST (Shield writes tests, Prism reviews, Runtime verifies Red + trace)
    -> run.completed(terminal_state="boundary") - no human gate in M-DESIGN
    or M-TEST (BS-05), so a single `trac run` after approval reaches the
    terminal state (v0.4: boundary moved from M-DESIGN to M-TEST->M-IMPL)."""
    run_id = walk_to_await_human(trac, stdin=stdin, version=version)
    assert trac("approve", "--actor", "Aaron").returncode == 0
    r = trac("run")
    assert r.returncode == 0, r.stderr
    assert "status=completed" in r.stdout
    assert "awaiting=-" in r.stdout
    return run_id


# Backward-compatible alias (pre-v0.4 name; the walk now reaches the M-TEST
# boundary, not the M-DESIGN one).
walk_to_design_complete = walk_to_m_test_complete


# The b93 §8.1 M-IMPL park injection: devon:RED fails the shared <=3 attempt
# budget (diagnose routes impl_defect back to RED each round) until the run
# parks at M-IMPL/DIAGNOSE/awaiting=escalation — the proven parking logic.
M_IMPL_PARK_SIMULATE = "devon:RED=fail;diagnose:classification=impl_defect"


def _seed_phase0_premises(repo, version):
    """Seed the phase0 baseline-repair premises into the host repo (v0.7+).

    The phase0 M-TEST pre-gate (v0.7+ architecture §1.0.2/§1.1) blocks a bare
    host repo before M-IMPL is ever reachable: `scan_trace_gaps` finds no
    v0.6 baseline, no `[adapter]` declaration, no §4.2 quality registry. The
    premises must land AFTER the fake M-DESIGN trio overwrite (seeding before
    the walk is wiped; every run parks at phase0.blocked) — exactly the
    blocked -> Human repairs the repo fact -> validate again channel the
    design prescribes (tests/integration/v07_journey_seed.py seed order).
    """
    from tests.integration.v07_journey_seed import (
        add_adapter_declaration,
        seed_v06_baseline,
    )

    seed_v06_baseline(repo)
    add_adapter_declaration(repo)
    from pathlib import Path

    from tests.integration.test_failclosed_scenarios import (
        _HEADING,
        _eight_guard_blocks,
        _registry_body,
    )

    arch = Path(repo) / ".tracks" / "projects" / version / "architecture.md"
    arch.parent.mkdir(parents=True, exist_ok=True)
    text = arch.read_text(encoding="utf-8") if arch.exists() else f"# {version} architecture\n"
    if "[quality_registry]" in text:
        return
    digest = "sha256:" + "0" * 64
    arch.write_text(
        text.rstrip("\n")
        + "\n\n"
        + _HEADING
        + "\n\n```toml\n"
        + _registry_body(_eight_guard_blocks(digest), host="tracks")
        + "```\n",
        encoding="utf-8",
    )

    # The parked run's evidence chain fail-closes at freeze_candidate on a
    # dirty tracked tree (interfaces section 1d), so the seeded premises
    # must land as a commit -- same pattern as seed_v06_baseline (B59
    # clean-tree expectation). check=False: no-op when the premises are
    # already committed (idempotent re-walk of the same host repo).
    import subprocess

    subprocess.run(
        ["git", "add", ".tracks/projects/project.toml",
         f".tracks/projects/{version}/architecture.md"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    # Path-scoped commit: never swallow a test's own staged dirt (the
    # dirty-tree anchors stage README.md before the walk -- a bare commit
    # would clean it and flip the freeze outcome).
    subprocess.run(
        ["git", "commit", "-m", "seed phase0 premises (adapter + quality registry)",
         "--", ".tracks/projects/project.toml",
         f".tracks/projects/{version}/architecture.md"],
        cwd=repo,
        check=False,
        capture_output=True,
    )


def walk_to_m_impl_parked(trac, stdin="构建一个事件溯源运行时", version="v0.8"):
    """init -> start -> triage go -> doc trio -> approval -> M-IMPL RED failure
    injection -> parks at M-IMPL/DIAGNOSE/awaiting=escalation.

    b93 §8.1 escape-gate walker contract: the batch-1 machine registers stages
    only through M-IMPL, so the sole reachable *legal escape source* is the
    M-IMPL failure-injection park (DIAGNOSE awaiting=escalation; NEEDS_ATTENTION
    is the sibling source). ``walk_to_m_test_complete`` runs the whole journey
    to the completed boundary, so parking here REQUIRES the failure injection
    (M_IMPL_PARK_SIMULATE burns the shared attempt budget). Bare ``trac run``
    bootstrap is forbidden for escape anchors (v0.8 suite-wide bootstrap
    defect, no init/start -> rc=1). The escape anchors are v0.8 ACs, so the
    walker default binds them to version v0.8.

    v0.7+ phase0 pre-gate: the first post-approval ``trac run`` drafts the
    M-DESIGN trio and fails the phase0 validate on a bare repo; when the trac
    fixture exposes its backing host repo (``trac.repo``), the phase0
    premises are seeded on the baseline-repair channel so the next injected
    run seals phase0 and reaches the M-IMPL park."""
    run_id = walk_to_await_human(trac, stdin=stdin, version=version)
    assert trac("approve", "--actor", "Aaron").returncode == 0
    r = trac("run", simulate=M_IMPL_PARK_SIMULATE)
    repo = getattr(trac, "repo", None)
    if repo is not None:
        _seed_phase0_premises(repo, version)
        r = trac("run", simulate=M_IMPL_PARK_SIMULATE)
    assert r.returncode == 0, r.stderr
    assert "stage=M-IMPL" in r.stdout
    assert "substate=DIAGNOSE" in r.stdout
    assert "awaiting=escalation" in r.stdout
    return run_id


def dispatches(evs, substate=None):
    """Filter event-log rows to `dispatch_agent` command.issued events,
    optionally narrowed to a substate."""
    out = []
    for e in evs:
        if e["type"] != "command.issued":
            continue
        cmd = e["payload"]["command"]
        if cmd["kind"] != "dispatch_agent":
            continue
        if substate and cmd["params"].get("substate") != substate:
            continue
        out.append(e)
    return out


def assert_escalation_after_three_failures(trac, evs, run_result, fails, substate="DRAFT"):
    """Shared escalation assertion (NFR-06a de-dup): exactly 3 failed attempts,
    no dispatch after the last failure, awaiting=escalation in run + status."""
    assert len(fails) == 3  # exactly 3 attempts, then stop
    assert len(dispatches(evs, substate)) == 3
    last_fail_seq = fails[-1]["seq"]
    assert not [d for d in dispatches(evs) if d["seq"] > last_fail_seq]
    assert "awaiting=escalation" in run_result.stdout
    r = trac("status")
    assert r.returncode == 0 and "escalation" in r.stdout
