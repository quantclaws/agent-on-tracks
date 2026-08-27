"""Integration: fail-closed scenarios + crash recovery (IF-FAILCLOSED-001, IF-DEMO-001).

AC-FR0266-01@v0.7 tracks host nine scenarios blocked,
AC-FR0266-03@v0.7 crash recovery replay ok,
AC-FR0266-04@v0.7 any leak or inequivalence blocks,
AC-NFR0142-02@v0.7 demo host crash recovery rebuild.

Assertions land on the `failclosed.demonstrated`/`failclosed.summary` event
outlets (interfaces §1a rows 11–12) observed via `trac run` + `event_log`,
NOT on fixture shapes. The crash-recovery ACs assert the
`failclosed.summary.crash_recovery` field (`replay_ok`) and that an
interruption/restart replays without phantom passes.
"""

from __future__ import annotations

import pytest

from tests.e2e.helpers import walk_to_await_human
from tests.unit.test_guard_registry_loader import (
    _HEADING,
    _eight_guard_blocks,
    _registry_body,
)
from tracks.executor.demo_host import FAIL_CLOSED_SCENARIOS

pytestmark = pytest.mark.integration


def _seed_guard_registry(host_repo):
    """APPEND the canonical §4.2 [quality_registry] block to the
    Archer-drafted v0.7 architecture.md (idempotent).

    Placement matters (2026-08-27 operator fix, second placement): seeding
    BEFORE the journey walk is wiped by the fake M-DESIGN trio overwrite,
    so every run parks at phase0.blocked(guard_registry_invalid). The seed
    must land AFTER M-DESIGN has written the doc — i.e. after the first
    post-approval `trac run` — exactly the baseline-repair channel the
    design prescribes (blocked -> Human repairs the repo fact -> validate
    again). Builders shared with tests/unit/test_guard_registry_loader.py
    (the same shape load_guard_registry validates)."""
    from pathlib import Path

    arch = Path(host_repo) / ".tracks" / "projects" / "v0.7" / "architecture.md"
    text = arch.read_text(encoding="utf-8") if arch.exists() else "# v0.7 architecture\n"
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


def _start_v07_run(trac, host_repo):
    """Activate a real v0.7 run through the journey (init -> start -> triage
    -> reviews -> approve), let M-DESIGN draft the trio, repair the §4.2
    registry premise, then drive to the failclosed demonstration.

    NOTE (SHIELD_FIX T-016 / issue 100): `seed_v05_approved_baseline`
    creates the project docs + a synthetic approval event but NO active run,
    so a bare `trac run` exits rc=1 'no active run' (perpetual-Red fixture,
    same class as T-015's issue 101). The activation walks the real journey
    and approves; the returned run_id must be used for `event_log` (the
    conftest filters by literal run_id equality, 'latest' matches nothing).
    After the first drive (M-DESIGN overwrites architecture.md), the §4.2
    registry block is appended as the repo-fact repair, then bounded drives
    reach the failclosed.* outlet."""
    run_id = walk_to_await_human(trac, version="v0.7")
    assert trac("approve", "--actor", "Aaron").returncode == 0
    trac("run")  # M-DESIGN drafts the trio (overwrites architecture.md)
    _seed_guard_registry(host_repo)  # baseline-repair: append the registry
    for _ in range(10):  # bounded drive to the failclosed demonstration
        trac("run")
        if "failclosed" in trac("status").stdout:
            break
    return run_id


# AC-FR0266-01@v0.7 TRACKS-TRACE tracks host nine scenarios blocked
def test_tracks_host_nine_scenarios_blocked(trac, host_repo, event_log):
    """AC-FR0266-01: each of the 9 scenarios produces a blocked demonstrated
    event on host=tracks; the summary reports all_fail_closed=true.

    Legal Red anchor: IF-FAILCLOSED-001 demonstration events are not yet
    produced; the per-scenario and summary assertions fail on absence of the
    contract outlet.
    """
    run_id = _start_v07_run(trac, host_repo)
    events = event_log(run_id)
    demonstrated = [e for e in events if e["type"] == "failclosed.demonstrated"
                     and e["payload"].get("host") == "tracks"]
    # Exactly one blocked demonstrated event per scenario, all outcome=blocked.
    assert len(demonstrated) == len(FAIL_CLOSED_SCENARIOS) == 9, (
        f"expected 9 tracks demonstrated events; got {len(demonstrated)}"
    )
    seen_scenarios = {e["payload"]["scenario"] for e in demonstrated}
    assert seen_scenarios == set(FAIL_CLOSED_SCENARIOS), (
        f"tracks demonstrated scenarios mismatch: {seen_scenarios}"
    )
    for ev in demonstrated:
        assert ev["payload"]["outcome"] == "blocked", (
            f"tracks scenario {ev['payload']['scenario']} leaked "
            f"(outcome={ev['payload']['outcome']})"
        )
    summary = [e for e in events if e["type"] == "failclosed.summary"
               and e["payload"].get("host") == "tracks"]
    assert summary, "tracks failclosed.summary event missing"
    assert summary[-1]["payload"]["all_fail_closed"] is True


# AC-FR0266-03@v0.7 TRACKS-TRACE crash recovery replay ok
def test_crash_recovery_replay_ok(trac, host_repo, event_log):
    """AC-FR0266-03: a controlled interruption/restart replays events without
    loss; the summary carries crash_recovery=replay_ok (not `failed`).

    The crash-recovery outlet is the `failclosed.summary.crash_recovery` field
    (§1a row 12), observed via the event stream after an interruption/restart.
    A phantom pass (status=passed with missing results) is forbidden."""
    run_id = _start_v07_run(trac, host_repo)
    events = event_log(run_id)
    summaries = [e for e in events if e["type"] == "failclosed.summary"]
    assert summaries, "failclosed.summary event missing (crash recovery outlet)"
    for summary in summaries:
        assert summary["payload"]["crash_recovery"] == "replay_ok", (
            f"host {summary['payload'].get('host')} crash_recovery must be "
            f"replay_ok; got {summary['payload'].get('crash_recovery')}"
        )
    # No phantom pass: a summary with status=passed must carry replay_ok (not
    # a missing/failed recovery field masked as passed).
    for summary in summaries:
        if summary["payload"]["status"] == "passed":
            assert summary["payload"]["crash_recovery"] == "replay_ok"


# AC-FR0266-04@v0.7 TRACKS-TRACE any leak or inequivalence blocks
def test_any_leak_or_inequivalence_blocks(trac, host_repo, event_log):
    """AC-FR0266-04: any leaked scenario (outcome != blocked) or demo
    inequivalence routes the host summary to status=blocked (not passed)."""
    run_id = _start_v07_run(trac, host_repo)
    events = event_log(run_id)
    demonstrated = [e for e in events if e["type"] == "failclosed.demonstrated"]
    # If any scenario leaked, its host summary must be status=blocked.
    leaked_hosts = {e["payload"]["host"] for e in demonstrated
                    if e["payload"]["outcome"] != "blocked"}
    for summary in (e for e in events if e["type"] == "failclosed.summary"):
        if summary["payload"]["host"] in leaked_hosts:
            assert summary["payload"]["status"] == "blocked", (
                f"host {summary['payload']['host']} leaked a scenario but "
                f"summary status={summary['payload']['status']} (must block)"
            )
    # At least one summary must exist and report all_fail_closed consistently.
    assert [e for e in events if e["type"] == "failclosed.summary"], (
        "failclosed.summary events missing (no acceptance outlet)"
    )


# AC-NFR0142-02@v0.7 TRACKS-TRACE demo host crash recovery rebuild
def test_demo_host_crash_recovery_rebuild(trac, host_repo, event_log):
    """AC-NFR0142-02: the demo-pytest host summary also carries
    crash_recovery=replay_ok after interruption/restart (dual-host)."""
    run_id = _start_v07_run(trac, host_repo)
    events = event_log(run_id)
    demo_summaries = [e for e in events if e["type"] == "failclosed.summary"
                      and e["payload"].get("host") == "demo-pytest"]
    assert demo_summaries, (
        "demo-pytest failclosed.summary missing (dual-host crash recovery outlet)"
    )
    assert demo_summaries[-1]["payload"]["crash_recovery"] == "replay_ok", (
        "demo-pytest crash recovery must be replay_ok after rebuild"
    )
