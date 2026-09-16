"""Shared E2E helpers (kept out of individual test modules to avoid R0801 duplication)."""

import os
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


def walk_to_m_impl_parked(
    trac,
    stdin="构建一个事件溯源运行时",
    version="v0.8",
    *,
    pre_seed_hook=None,
    post_approve_hook=None,
):
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
    run seals phase0 and reaches the M-IMPL park.

    ``pre_seed_hook(repo)`` runs after the phase0-blocked first run and before
    the seeding commit, so repo facts it writes into the canonical contract
    land in the SAME commit as the seeded premises (an extra post-park commit
    would drift the already-frozen candidate and stall the park chain).

    ``post_approve_hook(trac, run_id)`` runs right after the human approval is
    recorded and before the first post-approval drive, so production handlers
    the CLI cannot express deterministically (e.g. the real GitHub issue
    channel while the agent channel stays the deterministic fake) can land
    their events before the M-DESIGN drive consumes the M-REQ-APPROVAL
    boundary."""
    run_id = walk_to_await_human(trac, stdin=stdin, version=version)
    assert trac("approve", "--actor", "Aaron").returncode == 0
    if post_approve_hook is not None:
        post_approve_hook(trac, run_id)
    r = trac("run", simulate=M_IMPL_PARK_SIMULATE)
    repo = getattr(trac, "repo", None)
    if repo is not None:
        if pre_seed_hook is not None:
            pre_seed_hook(repo)
        _seed_phase0_premises(repo, version)
        r = trac("run", simulate=M_IMPL_PARK_SIMULATE)
    assert r.returncode == 0, r.stderr
    assert "stage=M-IMPL" in r.stdout
    assert "substate=DIAGNOSE" in r.stdout
    assert "awaiting=escalation" in r.stdout
    return run_id


def walk_to_awaiting_release(
    trac,
    stdin="构建一个事件溯源运行时",
    version="v0.8",
    *,
    pre_seed_hook=None,
    post_approve_hook=None,
    max_drives=10,
    host_repo=None,
):
    """Drive the proven M-IMPL park to M-RELEASE/AWAITING_RELEASE.

    v0.8 reality (verified on HEAD): after the park, the first injected
    ``trac run`` replays the failure review and registers the waived known
    issues, the next one (or two) walks M-IMPL EXIT -> M-VERIFY
    (evidence.reused -> persisted CI readback -> prism verify_final ->
    security) and lands the release preview at M-RELEASE/AWAITING_RELEASE.
    The loop is bounded; a missing arrival is a harness bug, not a park
    expectation.

    The release preview needs the version facts' remote tag census, so a
    host_repo passed here gets a bare origin first (the security advance
    made the chain genuinely reach the preview on fixture hosts too --
    without a remote the facts attention fail-closes the walk)."""
    if host_repo is not None:
        import subprocess as _sp

        has_origin = (
            _sp.run(
                ["git", "remote", "get-url", "origin"],
                cwd=host_repo, capture_output=True,
            ).returncode
            == 0
        )
        if not has_origin:
            init_bare_remote(host_repo, "walk-origin.git")
    run_id = walk_to_m_impl_parked(
        trac,
        stdin=stdin,
        version=version,
        pre_seed_hook=pre_seed_hook,
        post_approve_hook=post_approve_hook,
    )
    for _ in range(max_drives):
        r = trac("run", simulate=M_IMPL_PARK_SIMULATE)
        assert r.returncode == 0, (
            f"trac run failed rc={r.returncode}\nstderr:\n{r.stderr[-800:]}\nstdout:\n{r.stdout[-1500:]}"
        )
        status = trac("status")
        combined = status.stdout + status.stderr
        if "AWAITING_RELEASE" in combined or "awaiting_release" in combined:
            return run_id
    raise AssertionError(
        f"M-RELEASE/AWAITING_RELEASE not reached within {max_drives} drives"
    )


def real_issue_hook(host_repo, version="v0.8"):
    """``post_approve_hook`` that creates the run's issues on the real channel.

    The deterministic fake agent channel makes ``select_issue_backend`` treat
    the run as an explicit simulation and select the fake issue channel, so
    the real create + API-readback mapping is driven through the production
    handler with a stand-in-backed ``GithubBackend`` injected test-side (the
    ``TRAC_GITHUB_API_BASE`` stand-in, token and repo come from the caller's
    environment)."""
    from tracks.effects.github import GithubBackend

    def hook(_trac, run_id):
        invoke_create_issues(
            host_repo, run_id, backend=GithubBackend(host_repo, version)
        )

    return hook


def generate_report_md(trac, host_repo, name="report_out"):
    """Render the run report and return the generated Markdown body.

    ``trac report`` only prints the artifact path; content assertions must
    read the generated ``report.md`` (the §2b Issue map / Release trace
    sections included)."""
    output = host_repo / name
    result = trac("report", "--format", "md", "--output", str(output))
    assert result.returncode == 0, result.stderr
    return (output / "report.md").read_text(encoding="utf-8")


def init_bare_remote(host_repo, name, *, push_main=True):
    """Create a sibling bare remote, bind it as origin and return it.

    ``push_main`` publishes the host's current main first so every later
    commit (the frozen candidate included) is its descendant and a merge
    operation can fast-forward. Without it the remote stays empty and the
    first publish fails closed with ``branch_missing`` — the interrupted
    write-ahead premise. Also returns the pre-push HEAD sha for later
    ancestor pushes."""
    import subprocess

    bare = host_repo.parent / name
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    subprocess.run(
        ["git", "remote", "add", "origin", str(bare)], cwd=host_repo, check=True
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=host_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if push_main:
        subprocess.run(
            ["git", "push", "-q", "-u", "origin", "main"],
            cwd=host_repo,
            check=True,
            capture_output=True,
        )
    return bare, head


def skipped_issue_closes(events):
    """``issue.closed state=skipped`` payloads (non-authoritative audits)."""
    return [
        e["payload"]
        for e in events
        if e["type"] == "issue.closed" and e["payload"].get("state") == "skipped"
    ]


def assert_temp_refs_empty(host_repo):
    """``git for-each-ref refs/trac/tmp`` must list nothing (measured cleanup)."""
    import subprocess

    listed = subprocess.run(
        ["git", "for-each-ref", "refs/trac/tmp"],
        cwd=host_repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert listed.strip() == ""


def walk_and_release(trac, host_repo, *, post_approve_hook=None, name="bare.git"):
    """One full release-chain setup: bare origin -> walk to AWAITING_RELEASE
    -> human ``release`` decision; returns the run id."""
    init_bare_remote(host_repo, name)
    run_id = walk_to_awaiting_release(trac, post_approve_hook=post_approve_hook)
    assert run_id
    assert trac("release", "--action", "release").returncode == 0
    return run_id


def push_ancestor_main(host_repo, sha):
    """Publish an ancestor commit to origin/main (repair after branch_missing)."""
    import subprocess

    subprocess.run(
        ["git", "push", "-q", "origin", f"{sha}:refs/heads/main"],
        cwd=host_repo,
        check=True,
        capture_output=True,
    )


def force_diverged_main(host_repo):
    """Force origin/main to a non-ancestor commit (manual remote overwrite)."""
    import subprocess

    def run(*args):
        return subprocess.run(
            ["git", *args],
            cwd=host_repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    head = run("rev-parse", "HEAD")
    divergent = run(
        "commit-tree", f"{head}^{{tree}}", "-p", head, "-m", "divergent remote main"
    )
    run("push", "-q", "-f", "origin", f"{divergent}:refs/heads/main")
    return divergent


def _publish_store_and_executor(host_repo, run_id):
    """Open the journey store and a production Executor over it (test-side)."""
    from tracks import paths
    from tracks.executor.executor import Executor
    from tracks.store import Store

    store = Store(paths.tracks_home(host_repo))
    return store, Executor(store, host_repo, run_id)


def replay_execute_publish(host_repo, run_id):
    """Replay the WAL's last execute_publish through the production handler.

    Mirrors the runtime's own D-13 recovery: the SAME issued command (same
    command_id, so the write-ahead record is reused, never re-planned) is
    re-executed with ``reconcile=True`` — the remote decides
    done/reconciled_skip/conflict. ``push_merge``/``push_tag`` resolve refs
    against the process cwd, so the replay runs from the host repo root."""
    from tracks.kernel.events import Command

    store, executor = _publish_store_and_executor(host_repo, run_id)
    try:
        issued = [
            event
            for event in store.events(run_id)
            if event.type == "command.issued"
            and (event.payload or {}).get("command", {}).get("kind")
            == "execute_publish"
        ]
        assert issued, "no execute_publish command in the WAL to replay"
        command = issued[-1].payload["command"]
        previous = os.getcwd()
        os.chdir(host_repo)
        try:
            executor._execute(
                Command(
                    kind=command["kind"],
                    params=command.get("params", {}),
                    command_id=command.get("command_id"),
                ),
                store.state(run_id),
                None,
                reconcile=True,
            )
        finally:
            os.chdir(previous)
    finally:
        store.close()


def invoke_create_issues(host_repo, run_id, *, backend=None):
    """Invoke the production create_issues handler on the journey store.

    Used where the CLI cannot express the scenario deterministically: with the
    deterministic fake agent channel selected, backend selection treats the
    explicit simulation mode as the fake issue channel too, so a real
    (stand-in-backed) creation/readback must be driven through the production
    handler with the real backend injected test-side. ``backend=None`` leaves
    the handler's own ``select_issue_backend`` in charge, so a missing-credential
    attempt lands the audited attention.required path."""
    from tracks.kernel.events import Command

    store, executor = _publish_store_and_executor(host_repo, run_id)
    try:
        if backend is not None:
            executor._issue_backend = backend
        state = store.state(run_id)
        executor._execute(
            Command(
                kind="create_issues",
                params={"digest": state.approval_digest},
            ),
            state,
            None,
            False,
        )
    finally:
        store.close()


def invoke_execute_publish(host_repo, run_id, *, backend=None, params=None):
    """Invoke the production execute_publish handler on the journey store.

    Used where the CLI cannot express the scenario deterministically: the
    Agent-backend guard blocks before any effect, and a zero-effect plan
    preflight failure has no write-ahead event to flip the stage, so the CLI
    loop would re-issue the command unbounded (reported harness finding)."""
    from tracks.kernel.events import Command

    store, executor = _publish_store_and_executor(host_repo, run_id)
    try:
        if backend is not None:
            executor.backend = backend
        previous = os.getcwd()
        os.chdir(host_repo)
        try:
            executor._execute(
                Command(kind="execute_publish", params=dict(params or {})),
                store.state(run_id),
                None,
                False,
            )
        finally:
            os.chdir(previous)
    finally:
        store.close()


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
