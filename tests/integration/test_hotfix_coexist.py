"""Run coexistence: active/suspended selection, boundary restore, single
writer lock (IF-HOTFIX-006, FR-0242, NFR-0110).

Asserts on ``trac status`` rows (active + suspended + terminal),
``suspended:`` projection rows, and the writer-lock reject path with a
PID reported. The hotfix run + suspended feature run coexistence is
driven through the CLI; lock-reject is constructed by spawning a real
hanging ``trac run`` subprocess that holds the writer lock.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

from tests._support.hotfix_support import (
    assert_hotfix_drives_to_mtest,
    seed_host_issues,
    seed_inprogress_feature_run,
    seed_v05_approved_baseline,
)


# AC-FR0242-01@v0.6 TRACKS-TRACE active run selection and status discriminates runs
def test_active_run_selection_and_status_discriminates_runs(trac, host_repo, event_log):
    """AC-FR0242-01@v0.6: ``trac status`` outputs run_id + stage + branch +
    scenario, enough for the operator to tell which run ``trac run``
    continues. Both runs are visible during the hotfix run's lifetime.

    Failure mode (legal Red): ``trac hotfix`` is registered (IF-HOTFIX-001 landed) but the downstream sear is IF-HOTFIX-010; the hotfix
    run never starts. The status output carries only the pre-existing
    feature run, with no ``scenario=`` discriminator and no
    ``suspended:`` row.
    """
    seed_v05_approved_baseline(host_repo)
    seed_inprogress_feature_run(host_repo)
    seed_host_issues(host_repo)

    r = trac("hotfix", "42", "--scenario", "post-release")
    assert r.returncode == 0, r.stderr

    status = trac("status")
    # Active run row discriminates: run_id + stage + branch + scenario.
    assert "branch=fix/42" in status.stdout
    assert "scenario=post-release" in status.stdout
    # Suspended feature run is also observable.
    assert "suspended:" in status.stdout


# AC-FR0242-02@v0.6 TRACKS-TRACE boundary restores suspended feature run as active
# AC-NFR0110-02@v0.6 TRACKS-TRACE boundary restores suspended feature run as active
def test_boundary_restores_suspended_feature_run_as_active(trac, host_repo, event_log):
    """AC-FR0242-02 / AC-NFR0110-02@v0.6: at boundary the suspended feature
    run is restored as the active run (run_id switches back) and its gate
    state is recoverable via ``trac status`` / ``trac check``.

    Failure mode (legal Red): the hotfix journey never reaches boundary
    (the entry CLI (registered IF-HOTFIX-001) but the downstream IF-HOTFIX-010 seam -> IF-HOTFIX-010 seam (M-DESIGN awaiting=escalation), no events); the
    feature run stays the only visible run, the active-run switch-back
    cannot be observed, and the boundary terminal line never appears.
    """
    seed_v05_approved_baseline(host_repo)
    seed_inprogress_feature_run(host_repo)
    seed_host_issues(host_repo)

    r = trac("hotfix", "42", "--scenario", "post-release")
    assert r.returncode == 0, r.stderr

    # Drive to boundary (single dispatch covers the happy path in the
    # post-release scenario; the fake backend anchors and exits cleanly).
    cont = trac("run")
    assert cont.returncode == 0, cont.stderr

    # The restored feature is the active status line; boundary evidence stays
    # on the hotfix event stream rather than obscuring the continuation target.
    status = trac("status")
    assert "feature-run-1" in status.stdout  # restored_active_run
    run_id = r.stdout.split()[1] if "run " in r.stdout else "unknown"
    assert any(
        event["type"] == "run.completed"
        and event["payload"].get("terminal_state") == "boundary"
        for event in event_log(run_id)
    )


# AC-FR0242-03@v0.6 TRACKS-TRACE suspended run observable and gates recoverable
# AC-NFR0110-01@v0.6 TRACKS-TRACE suspended run observable and gates recoverable
def test_suspended_run_observable_and_gates_recoverable(trac, host_repo, event_log):
    """AC-FR0242-03 / AC-NFR0110-01@v0.6: while the hotfix run is active, the
    suspended feature run's state is observable via ``trac status`` /
    ``trac replay`` / ``trac report``; the gate is recoverable after the
    boundary restores the feature run as active.

    Failure mode (legal Red): the hotfix entry CLI is registered (IF-HOTFIX-001 landed); the downstream IF-HOTFIX-010 seam (resolve_inherited_baseline_docs not wired
    ``trac hotfix`` -> IF-HOTFIX-010 seam (M-DESIGN awaiting=escalation)); only the feature run is visible
    in ``trac status``, no ``suspended:`` row appears, and there is no
    boundary to restore from.
    """
    seed_v05_approved_baseline(host_repo)
    feature_run_id = seed_inprogress_feature_run(host_repo)
    seed_host_issues(host_repo)

    r = trac("hotfix", "42", "--scenario", "post-release")
    assert r.returncode == 0, r.stderr

    # Suspended feature run is observable.
    status = trac("status")
    assert "suspended:" in status.stdout

    replay = trac("replay", feature_run_id)
    assert replay.returncode == 0, replay.stderr
    assert "stage.entered" in replay.stdout

    # IF-HOTFIX-010 baseline-resolver seam (legal Red): the run must
    # continue from M-DESIGN into the M-TEST journey; the unimplemented
    # inherited-baseline resolver parks it at awaiting=escalation, so
    # the drive-to-M-TEST assertion fails until IF-HOTFIX-010 lands.
    assert_hotfix_drives_to_mtest(trac, host_repo)


# AC-FR0242-04@v0.6 TRACKS-TRACE second concurrent trac command rejected with holder PID
def test_second_concurrent_trac_command_rejected_with_holder_pid(trac, host_repo):
    """AC-FR0242-04@v0.6: a second concurrent ``trac`` command (feature run
    vs hotfix run) is rejected by the writer lock; stderr reports the
    holder PID and the exit is non-zero. No dispatch advances.

    Constructed by spawning a real hanging ``trac run`` subprocess (the
    canonical writer-lock path is already implemented) and then issuing
    ``trac hotfix`` against the held lock.

    Failure mode (legal Red): the hotfix CLI is registered (IF-HOTFIX-001 landed); the legal Red anchor is the IF-HOTFIX-010 baseline-resolver seam (run parks at M-DESIGN awaiting=escalation reason=[trace] test-plan validate requires acceptance.md in same dir).
    ``_COMMANDS`` (IF-HOTFIX-001), so ``main()`` short-circuits at
    command lookup with IF-HOTFIX-010 seam (M-DESIGN awaiting=escalation) before the writer lock is even
    consulted. The "runtime lock held by pid" assertion therefore fails
    - the symbol-missing form is the legal Red signal.
    """
    seed_v05_approved_baseline(host_repo)
    seed_host_issues(host_repo)

    # Bring the host repo to a state where the next `trac run` enters
    # the DRAFT substate so a hang token actually blocks inside the
    # writer_lock block (the TRIAGE substate returns "done" immediately
    # without consulting the token; only DRAFT/RESPOND reach the hang path).
    assert trac("init").returncode == 0
    assert trac("start", "v0.5", stdin="seed story for lock test").returncode == 0
    assert trac("run").returncode == 0  # M-STORY TRIAGE dispatch
    assert trac("triage", "go").returncode == 0

    # Spawn a hanging `trac run` (scribe DRAFT blocks via the fake backend)
    # so it acquires the writer lock and holds it for the duration.
    env = {k: v for k, v in os.environ.items() if k not in ("TRACKS_HOME", "TRAC_FAKE_SIMULATE")}
    env["TRAC_AGENT_BACKEND"] = "fake"
    env["TRAC_FAKE_SIMULATE"] = "scribe:DRAFT=hang"
    proc = subprocess.Popen(
        [sys.executable, "-m", "tracks.cli.main", "run"],
        cwd=host_repo,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        lock = host_repo / ".tracks" / "runtime" / "lock"
        for _ in range(100):
            if lock.exists() and lock.read_text(encoding="utf-8").strip():
                break
            time.sleep(0.05)
        else:
            raise AssertionError("hanging run never acquired the writer lock")
        holder = lock.read_text(encoding="utf-8").strip()

        # The second concurrent command - ``trac hotfix`` - must be rejected
        # with the holder PID (FR-0242-04 single-writer lock discipline).
        r = trac("hotfix", "42", "--scenario", "post-release")
        assert r.returncode != 0
        assert "runtime lock held by pid" in r.stderr
        assert holder in r.stderr
    finally:
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=10)

    # IF-HOTFIX-010 baseline-resolver seam (legal Red): after the lock is
    # released, a fresh hotfix run must continue from M-DESIGN into M-TEST;
    # the unimplemented inherited-baseline resolver parks it at
    # awaiting=escalation, so the drive-to-M-TEST assertion fails until
    # IF-HOTFIX-010 lands.
    assert_hotfix_drives_to_mtest(trac, host_repo, fresh_issue="58")
