"""Hotfix event log: append-only, replay, report (IF-HOTFIX-002 / IF-HOTFIX-006,
FR-0240-07, FR-0246-03, NFR-0100-04).

Covers the append-only / projection-rebuild discipline (NFR-0100-04) and
the replay/report audit surface for the full hotfix journey (FR-0246-03).
"""

from __future__ import annotations

import sqlite3

from tests._support.hotfix_support import (
    assert_hotfix_drives_to_mtest,
    seed_host_issues,
    seed_v05_approved_baseline,
)
from tracks import paths


# AC-FR0240-07@v0.6 TRACKS-TRACE hotfix events append-only, replay, report
def test_hotfix_events_append_only_replay_and_report(trac, host_repo, event_log):
    """AC-FR0240-07@v0.6: ``hotfix.requested`` / ``triage.prechecked`` /
    ``anchor.validated`` / ``human.anchor`` are append-only on the run's
    event stream (no row rewrites), and the journey is auditable via
    ``trac replay`` and ``trac report --run-id --format md``.

    Failure mode (legal Red): the hotfix event types (IF-HOTFIX-002 §1a)
    and the ``trac hotfix`` surface are implemented (IF-HOTFIX-001
    landed); the legal Red anchor is the IF-HOTFIX-010 baseline-resolver
    seam. The downstream journey parks at M-DESIGN awaiting=escalation
    reason=[trace] test-plan validate requires acceptance.md in same dir,
    so the replay/report probes find no full journey evidence.
    """
    seed_v05_approved_baseline(host_repo)
    seed_host_issues(host_repo)

    r = trac("hotfix", "42", "--scenario", "post-release")
    assert r.returncode == 0, r.stderr
    run_id = r.stdout.split()[1] if "run " in r.stdout else "unknown"

    evs = event_log(run_id)
    types_seq = [e["type"] for e in evs]
    assert "hotfix.requested" in types_seq
    # Append-only: no row rewrite (the seq column is strictly monotonic).
    seqs = [e["seq"] for e in evs]
    assert seqs == sorted(seqs)

    replay = trac("replay", run_id)
    assert "hotfix.requested" in replay.stdout
    assert "triage.prechecked" in replay.stdout

    report = trac(
        "report", "--run-id", run_id, "--output", ".tracks/runtime/report", "--format", "md"
    )
    assert report.returncode == 0, report.stderr

    # IF-HOTFIX-010 baseline-resolver seam (legal Red): the run must
    # continue from M-DESIGN into the M-TEST journey; the unimplemented
    # inherited-baseline resolver parks it at awaiting=escalation, so
    # the drive-to-M-TEST assertion fails until IF-HOTFIX-010 lands.
    assert_hotfix_drives_to_mtest(trac, host_repo)


# AC-NFR0100-04@v0.6 TRACKS-TRACE hotfix events append-only + projection rebuild
def test_hotfix_events_append_only_and_projection_rebuild(trac, host_repo, event_log):
    """AC-NFR0100-04@v0.6: HOTFIX-TRIAGE events are append-only and the
    projection table can be dropped and rebuilt from events alone
    (``trac status`` after rebuild reports the same entry state).

    Failure mode (legal Red): the hotfix run never starts (IF-HOTFIX-010 seam (M-DESIGN awaiting=escalation)
    from the the entry CLI (registered IF-HOTFIX-001) but the downstream IF-HOTFIX-010 seam command), so dropping and
    rebuilding the projection yields an empty projection rather than the
    hotfix run's state.
    """
    seed_v05_approved_baseline(host_repo)
    seed_host_issues(host_repo)

    r = trac("hotfix", "42", "--scenario", "post-release")
    assert r.returncode == 0, r.stderr

    # Snapshot the projected state before the rebuild.
    before = trac("status")
    # §2b: hotfix run row carries branch/scenario/issue contract fields.
    assert "branch=fix/42" in before.stdout
    assert "scenario=post-release" in before.stdout
    before_run_line = _run_line(before.stdout, "fix/42")

    # Drop the runs projection table and force a rebuild from events only.
    home = paths.tracks_home(host_repo)
    db = paths.db_path(home)
    conn = sqlite3.connect(db)
    try:
        conn.execute("DELETE FROM runs")
        conn.commit()
    finally:
        conn.close()

    after = trac("status")
    # NFR-04: projection rebuild must produce identical observable state.
    assert "branch=fix/42" in after.stdout
    assert "scenario=post-release" in after.stdout
    after_run_line = _run_line(after.stdout, "fix/42")
    assert after_run_line == before_run_line

    # IF-HOTFIX-010 baseline-resolver seam (legal Red): the run must
    # continue from M-DESIGN into the M-TEST journey; the unimplemented
    # inherited-baseline resolver parks it at awaiting=escalation, so
    # the drive-to-M-TEST assertion fails until IF-HOTFIX-010 lands.
    assert_hotfix_drives_to_mtest(trac, host_repo)


def _run_line(stdout: str, branch: str) -> str:
    """Extract the status line containing the named fix branch."""
    for line in stdout.splitlines():
        if branch in line:
            return line
    raise AssertionError(f"no status line contains {branch!r}")


# AC-FR0246-03@v0.6 TRACKS-TRACE replay and report show full hotfix journey
def test_replay_and_report_show_full_hotfix_journey(trac, host_repo, event_log):
    """AC-FR0246-03@v0.6: ``trac replay`` and ``trac report --format md``
    surface the full hotfix journey: ``hotfix.requested`` ->
    ``triage.prechecked`` -> ``anchor.validated`` -> ``stage.entered(M-
    DESIGN)`` -> . -> ``run.completed(terminal_state=boundary)``.

    Failure mode (legal Red): the hotfix CLI is registered (IF-HOTFIX-001 landed); the legal Red anchor is the IF-HOTFIX-010 baseline-resolver seam (run parks at M-DESIGN awaiting=escalation reason=[trace] test-plan validate requires acceptance.md in same dir).
    ``trac hotfix`` -> IF-HOTFIX-010 seam (M-DESIGN awaiting=escalation), no events), so neither ``replay``
    nor ``report`` can show the full sequence.
    """
    seed_v05_approved_baseline(host_repo)
    seed_host_issues(host_repo)

    r = trac("hotfix", "42", "--scenario", "post-release")
    assert r.returncode == 0, r.stderr
    run_id = r.stdout.split()[1] if "run " in r.stdout else "unknown"
    for _ in range(3):
        cont = trac("run")
        assert cont.returncode == 0, cont.stderr

    replay = trac("replay", run_id)
    expected = (
        "hotfix.requested",
        "triage.prechecked",
        "anchor.validated",
        "stage.entered",
        "run.completed",
    )
    for token in expected:
        assert token in replay.stdout, f"replay must surface {token}"
    assert replay.stdout.index('stage.entered\t{"stage": "M-DESIGN"}') < replay.stdout.index(
        'stage.entered\t{"stage": "M-TEST"}'
    ) < replay.stdout.index('stage.entered\t{"stage": "M-IMPL"}') < replay.stdout.index(
        'run.completed'
    )
    assert '"terminal_state": "boundary"' in replay.stdout

    r_report = trac(
        "report", "--run-id", run_id, "--output", ".tracks/runtime/report", "--format", "md"
    )
    assert r_report.returncode == 0, r_report.stderr
    report_path = host_repo / ".tracks" / "runtime" / "report"
    assert report_path.exists()
    body = "\n".join(p.read_text(encoding="utf-8") for p in report_path.rglob("*.md"))
    for token in expected:
        assert token in body, f"report must surface {token}"
