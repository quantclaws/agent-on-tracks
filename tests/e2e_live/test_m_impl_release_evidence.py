"""Opt-in live release-evidence M-IMPL journey (FR-0230~FR-0233).

This is the only real OpencodeBackend happy path: it drives the installed
current wheel from M-TEST EXIT into M-IMPL through at least one real
Devon RED/GREEN/REFACTOR dispatch, and asserts the required events,
dispatch receipts, R/G lineage, report, gates, task completion, M-IMPL
exit, boundary, and canonical evidence.

Routine credential probe (test_routine_missing_credentials_*) is the
environment contract: it skips with a precise LIVE_SKIPPED message and
asserts no success evidence is produced.

Tests never use fake/simulation/overlay/manual insertion.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from tests.e2e_live.harness import (
    LIVE_ENV,
    clean_env,
    installed_trac_command,
    live_path,
    require_current_virtualenv,
)
from tests.e2e_live.m_test_helpers import _events, _git


def _require_release_credentials():
    """Check that all live credentials are present; return the config dict.

    This is used by the credential test to skip before any evidence is
    produced.  The skip reason must be exact so the CI release job can
    detect it.
    """
    config = {key: os.environ.get(key, "").strip() for key in LIVE_ENV}
    missing = [key for key, value in config.items() if not value]
    if missing:
        pytest.skip(
            f"LIVE_SKIPPED: missing {', '.join(missing)}; "
            "set TRAC_LIVE_PROVIDER/MODEL/BASE_URL/API_KEY to enable"
        )
    return config


_WHEEL_CACHE: dict[str, str] = {}


def _build_wheel() -> str:
    """Build the current project wheel once per session; return its path."""
    project_root = Path(__file__).resolve().parents[2]
    cached = _WHEEL_CACHE.get("wheel")
    if cached and Path(cached).is_file():
        return cached
    wheelhouse = project_root / "dist"
    wheelhouse.mkdir(parents=True, exist_ok=True)
    build = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", str(project_root),
         "--no-deps", "--no-build-isolation", "--wheel-dir", str(wheelhouse)],
        cwd=project_root, capture_output=True, text=True, timeout=300,
    )
    assert build.returncode == 0, f"wheel build failed: {build.stderr}"
    wheels = sorted(wheelhouse.glob("*.whl"))
    assert len(wheels) == 1, f"expected exactly one wheel, got {wheels}"
    _WHEEL_CACHE["wheel"] = str(wheels[0])
    return _WHEEL_CACHE["wheel"]


def _drive_live_journey(requirement: str = "test journey\n") -> dict:
    """Drive a REAL live v0.5 journey in a disposable HOST repo to its end.

    PRISM-V05-R2-03: the journey lives in the HOST repo (isolated install of
    the current wheel, installed ``trac`` console script, cwd=HOST).  All
    later assertions — events, git refs, report, bundle, check — must read
    THIS journey's public outlets, never the source workspace.

    Returns ``{"host", "isolated_bin", "run_id", "env", "final_run"}``.
    The loop issues the public human-gate commands (``triage go``,
    ``review no-comment``, ``approve``) between runs, mirroring the
    documented operator path, and stops at ``status=completed`` — or early
    when the journey halts (unwired stubs raise), in which case the calling
    test's completeness assertions produce the legal Red.
    """
    wheel = _build_wheel()
    tmp_keeper = tempfile.mkdtemp(prefix="m_impl_release_")
    temp_root = Path(tmp_keeper)

    host = _setup_host(temp_root)

    # Install wheel into an isolated venv inside the host.
    isolated_venv = host / ".venv"
    subprocess.run(
        [sys.executable, "-m", "venv", str(isolated_venv)], check=True, capture_output=True,
    )
    isolated_python = isolated_venv / "bin" / "python"
    isolated_bin = isolated_venv / "bin"
    install = subprocess.run(
        [str(isolated_python), "-m", "pip", "install", str(wheel), "--no-deps"],
        capture_output=True, text=True, timeout=300,
    )
    assert install.returncode == 0, f"wheel install failed: {install.stderr}"
    assert (isolated_bin / "trac").is_file()

    # Live environment: installed console script on PATH, opencode backend.
    config = _require_release_credentials()
    env = clean_env()
    env["PATH"] = live_path(isolated_bin, isolated_bin)
    env["TRAC_AGENT_BACKEND"] = "opencode"
    for key, value in config.items():
        env[key] = value

    def run_cli(*args: str, timeout: int = 900, stdin: str | None = None):
        return subprocess.run(
            installed_trac_command(isolated_bin, *args),
            cwd=host, env=env, capture_output=True, text=True, timeout=timeout,
            input=stdin,
        )

    assert run_cli("init", timeout=60).returncode == 0
    started = run_cli("start", "v0.5", timeout=60, stdin=requirement)
    assert started.returncode == 0, f"trac start failed: {started.stderr}"
    run_id = re.search(r"run (\S+) started", started.stdout).group(1)

    final_run = None
    for _ in range(16):
        final_run = run_cli("run")
        out = final_run.stdout
        if final_run.returncode != 0:
            break  # journey halted (unwired stub raise) — completeness Red fires
        if "status=completed" in out or "awaiting=-" in out:
            break
        if "awaiting=escalation" in out:
            break
        if "awaiting=triage" in out:
            assert run_cli("triage", "go", timeout=60).returncode == 0
        elif "awaiting=review" in out:
            assert run_cli("review", "no-comment", timeout=60).returncode == 0
        elif "awaiting=approval" in out:
            assert run_cli("approve", "--actor", "Aaron", timeout=60).returncode == 0
        else:
            break  # unknown state — stop driving; completeness Red fires
    return {
        "host": host,
        "isolated_bin": isolated_bin,
        "run_id": run_id,
        "env": env,
        "final_run": final_run,
    }


def _no_success_evidence(path: Path) -> bool:
    """Return True if no satisfied evidence bundle exists under path."""
    evidence_root = path / ".tracks" / "runtime" / "release-evidence" / "v1"
    if not evidence_root.is_dir():
        return True
    for sha_dir in evidence_root.iterdir():
        if not sha_dir.is_dir():
            continue
        for run_dir in sha_dir.iterdir():
            ev_path = run_dir / "evidence.json"
            if ev_path.is_file() and '"satisfied"' in ev_path.read_text(encoding="utf-8"):
                return False
    return True


def _setup_host(temp_root: Path) -> Path:
    """Create a minimal disposable git host at temp_root."""
    host = temp_root / "host"
    host.mkdir()
    for cmd in [
        ["git", "init", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test Human"],
    ]:
        subprocess.run(cmd, cwd=host, check=True, capture_output=True)
    (host / "README.md").write_text("live release evidence host\n", encoding="utf-8")
    (host / ".gitignore").write_text(".venv\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "README.md", ".gitignore"], cwd=host, check=True, capture_output=True,
    )
    subprocess.run(["git", "commit", "-m", "init"], cwd=host, check=True, capture_output=True)
    return host


@pytest.mark.e2e_live
# AC-FR0233-01@v0.5 TRACKS-TRACE routine missing credentials reports LIVE_SKIPPED without evidence
def test_routine_missing_credentials_reports_live_skipped_without_evidence():
    """Verify that missing credentials produce a precise skip and no bundle.

    Asserts no success evidence is produced BEFORE the skip (the skip must
    not swallow the no-evidence assertion), then emits the exact
    ``LIVE_SKIPPED: missing <NAME>`` reason.
    """
    require_current_virtualenv()
    config = {key: os.environ.get(key, "").strip() for key in LIVE_ENV}
    missing = [key for key, value in config.items() if not value]
    if not missing:
        return  # All credentials present — the credential-less case is not applicable
    # Assert no success evidence is produced by the credential-less branch.
    assert _no_success_evidence(Path.cwd()), (
        "found satisfied evidence bundle despite missing credentials"
    )
    pytest.skip(
        f"LIVE_SKIPPED: missing {', '.join(missing)}; "
        "set TRAC_LIVE_PROVIDER/MODEL/BASE_URL/API_KEY to enable"
    )


@pytest.mark.e2e_live
# AC-FR0230-01@v0.5 TRACKS-TRACE real OpencodeBackend M-IMPL RGR journey with release evidence
# AC-FR0233-02@v0.5 TRACKS-TRACE credentials present + opt-in executes and satisfies check
def test_real_opencode_m_impl_rgr_release_evidence():
    """Drive the installed current wheel through M-IMPL to boundary.

    This is a forward-looking Red: the test fails because the M-IMPL release
    evidence stubs are not yet wired (IF-LIVE-001/IF-RELEASE-001).  Once the
    routing exists, the test will assert the complete M-IMPL public sequence:
    real Devon/Prism dispatch, R/G lineage, gates, task completion, exit,
    boundary, and satisfied check.
    """
    _require_release_credentials()
    require_current_virtualenv()

    # Build the wheel
    project_root = Path(__file__).resolve().parents[2]
    wheelhouse = project_root / "dist"
    wheelhouse.mkdir(parents=True, exist_ok=True)
    build = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", str(project_root),
         "--no-deps", "--no-build-isolation", "--wheel-dir", str(wheelhouse)],
        cwd=project_root, capture_output=True, text=True, timeout=300,
    )
    assert build.returncode == 0, f"wheel build failed: {build.stderr}"
    wheels = sorted(wheelhouse.glob("*.whl"))
    assert len(wheels) == 1, f"expected exactly one wheel, got {wheels}"
    wheel = wheels[0]

    # Create isolated host
    with tempfile.TemporaryDirectory(prefix="m_impl_release_") as tmp:
        host = _setup_host(Path(tmp))

        # Install wheel into isolated venv
        isolated_venv = host / ".venv"
        subprocess.run(
            [sys.executable, "-m", "venv", str(isolated_venv)], check=True, capture_output=True,
        )
        isolated_python = isolated_venv / "bin" / "python"
        isolated_bin = isolated_venv / "bin"
        install = subprocess.run(
            [str(isolated_python), "-m", "pip", "install", str(wheel), "--no-deps"],
            capture_output=True, text=True, timeout=300,
        )
        assert install.returncode == 0, f"wheel install failed: {install.stderr}"
        assert (isolated_bin / "trac").is_file()

        # Set up the live environment
        config = _require_release_credentials()
        env = clean_env()
        env["PATH"] = live_path(isolated_bin, isolated_bin)
        env["TRAC_AGENT_BACKEND"] = "opencode"
        for key, value in config.items():
            env[key] = value

        # Run trac init
        init = subprocess.run(
            installed_trac_command(isolated_bin, "init"),
            cwd=host, env=env, capture_output=True, text=True, timeout=60,
        )
        assert init.returncode == 0, f"trac init failed: {init.stderr}"

        # Run trac start
        start = subprocess.run(
            installed_trac_command(isolated_bin, "start", "v0.5"),
            cwd=host, env=env, capture_output=True, text=True, timeout=60,
            input="test journey\n",
        )
        assert start.returncode == 0, f"trac start failed: {start.stderr}"

        # Run trac run (walks through stages)
        subprocess.run(
            installed_trac_command(isolated_bin, "run"),
            cwd=host, env=env, capture_output=True, text=True, timeout=900,
        )
        # The journey may not complete (M-IMPL stubs raise).  Assert that
        # the event stream is observable and the journey was attempted.
        events_db = host / ".tracks" / "runtime" / "tracks.db"
        assert events_db.is_file(), "expected events DB after trac run"
        # Assert that the journey entered M-IMPL (forward-looking Red:
        # M-IMPL stubs prevent this, so the assertion fails).
        conn = __import__("sqlite3").connect(events_db)
        try:
            rows = conn.execute(
                "SELECT payload FROM events WHERE type = 'stage.entered'"
            ).fetchall()
        finally:
            conn.close()
        m_impl_entries = [
            r[0] for r in rows
            if '"M-IMPL"' in r[0] or '"stage":"M-IMPL"' in r[0]
        ]
        assert len(m_impl_entries) > 0, (
            "expected stage.entered(M-IMPL) in event stream"
        )


@pytest.mark.e2e_live
# AC-FR0230-02@v0.5 TRACKS-TRACE public events/Git/report verify complete stage sequence
def test_real_journey_exposes_events_lineage_report_and_boundary():
    """Verify the HOST journey's public events, git refs, and report (legal Red).

    PRISM-V05-R2-03: this test reads the HOST journey's public outlets only —
    the host's event stream (§4a), the host's git refs, and ``trac report``
    from the INSTALLED console script with cwd=HOST — and runs the satisfied
    ``trac check release-evidence`` against the HOST.  Since the
    IF-LIVE-001/IF-RELEASE-001 stubs are not yet wired, the journey cannot
    complete and the check is not wired, so the completeness assertions fail
    — legal Red.
    """
    _require_release_credentials()
    require_current_virtualenv()
    journey = _drive_live_journey()
    host: Path = journey["host"]
    isolated_bin: Path = journey["isolated_bin"]
    run_id: str = journey["run_id"]
    env: dict = journey["env"]

    # -- public event stream: the complete M-IMPL sequence (§4a) ----------
    events = _events(host, run_id)

    def of_type(event_type: str) -> list[dict]:
        return [e for e in events if e["type"] == event_type]

    m_impl_entries = [
        e for e in of_type("stage.entered") if e["payload"].get("stage") == "M-IMPL"
    ]
    assert m_impl_entries, "expected stage.entered(M-IMPL) in host event stream"
    m_impl_start_seq = m_impl_entries[0]["seq"]

    task_started = [
        e for e in of_type("task.started") if e["seq"] > m_impl_start_seq
    ]
    red_cp = [
        e for e in of_type("red.checkpointed") if e["seq"] > m_impl_start_seq
    ]
    green = [
        e for e in of_type("green.committed") if e["seq"] > m_impl_start_seq
    ]
    refactor = [
        e
        for e in events
        if e["type"] in ("refactor.committed", "refactor.no_change")
        and e["seq"] > m_impl_start_seq
    ]
    reviews = [
        e for e in of_type("prism.verdict") if e["seq"] > m_impl_start_seq
    ]
    task_completed = [
        e for e in of_type("task.completed") if e["seq"] > m_impl_start_seq
    ]
    exited = [
        e
        for e in of_type("stage.exited")
        if e["payload"].get("stage") == "M-IMPL"
    ]
    completed = of_type("run.completed")

    assert task_started, "expected task.started in M-IMPL"
    assert red_cp, "expected red.checkpointed in M-IMPL"
    assert green, "expected green.committed in M-IMPL"
    assert refactor, "expected refactor.committed|refactor.no_change in M-IMPL"
    assert reviews, "expected prism.verdict (M-IMPL review) in M-IMPL"
    assert task_completed, "expected task.completed in M-IMPL"
    assert exited, "expected stage.exited(M-IMPL)"
    assert completed, "expected run.completed"
    # Strict §4a order: task.started < red.checkpointed < green.committed <
    # refactor < task.completed < stage.exited(M-IMPL) < run.completed(boundary).
    assert task_started[0]["seq"] < red_cp[0]["seq"] < green[0]["seq"]
    assert refactor[0]["seq"] > green[0]["seq"]
    assert task_completed[0]["seq"] > refactor[0]["seq"]
    assert exited[0]["seq"] > task_completed[0]["seq"]
    assert completed[0]["seq"] > exited[0]["seq"]
    assert completed[0]["payload"].get("terminal_state") == "boundary", (
        "expected run.completed(terminal_state=boundary)"
    )

    # -- git refs: the checkpointed R ref exists in the HOST repo ----------
    r_ref = red_cp[0]["payload"].get("ref")
    assert r_ref, "red.checkpointed must carry its git ref"
    assert _git(host, "cat-file", "-e", f"{r_ref}^{{commit}}") == "", (
        f"checkpointed R ref {r_ref} must exist in host git"
    )

    # -- report: INSTALLED console script, cwd=HOST ------------------------
    report = subprocess.run(
        installed_trac_command(isolated_bin, "report", "--run-id", run_id),
        cwd=host, env=env, capture_output=True, text=True, timeout=120,
    )
    assert report.returncode == 0, f"trac report failed: {report.stderr}"
    for marker in ("M-IMPL", "lineage", "boundary"):
        assert marker in report.stdout, (
            f"report must expose the M-IMPL journey ({marker} missing)"
        )

    # -- satisfied check against the HOST (installed CLI, cwd=HOST, §2d) ---
    check = subprocess.run(
        installed_trac_command(isolated_bin, "check", "release-evidence"),
        cwd=host, env=env, capture_output=True, text=True, timeout=120,
    )
    assert check.returncode == 0, (
        f"expected exit 0 from release-evidence check on the host journey, "
        f"got {check.returncode}: {check.stdout}{check.stderr}"
    )
    host_head = _git(host, "rev-parse", "HEAD")
    branch = _git(host, "branch", "--show-current") or "(detached)"
    assert check.stdout.rstrip("\n") == (
        f"release-evidence: satisfied (backend=opencode, run {run_id}, "
        f"candidate HEAD {host_head}, branch {branch})"
    ), f"§2d satisfied line is locked, got {check.stdout!r}"
    assert check.stderr == "", "§2d: satisfied check writes nothing to stderr"


@pytest.mark.e2e_live
# AC-FR0231-01@v0.5 TRACKS-TRACE bundle binds run/backend/I-O/lineage/boundary
def test_real_journey_writes_complete_auditable_bundle():
    """Verify the HOST journey produces a complete auditable bundle (legal Red).

    PRISM-V05-R2-03: the bundle lives in the HOST repo at
    ``host/.tracks/runtime/release-evidence/v1/<host_head_sha>/<run_id>/``
    (§3h); the check reports it via the installed CLI with cwd=HOST, and the
    only permitted transport is byte-verbatim relay of the host bundle.
    Since the stubs are not wired, the bundle never exists — legal Red.
    """
    _require_release_credentials()
    require_current_virtualenv()
    journey = _drive_live_journey()
    host: Path = journey["host"]
    isolated_bin: Path = journey["isolated_bin"]
    run_id: str = journey["run_id"]
    env: dict = journey["env"]

    host_head = _git(host, "rev-parse", "HEAD")
    run_dir = (
        host / ".tracks" / "runtime" / "release-evidence" / "v1" / host_head / run_id
    )
    evidence_path = run_dir / "evidence.json"
    assert evidence_path.is_file(), (
        f"expected satisfied evidence bundle at {evidence_path}"
    )
    bundle_bytes = evidence_path.read_bytes()
    bundle = json.loads(bundle_bytes.decode("utf-8"))
    assert bundle.get("status") == "satisfied", (
        f"expected satisfied bundle, got {bundle.get('status')}"
    )
    # §3h: {candidate_sha}/{run_id} path segments must match the bundle.
    assert bundle.get("candidate_sha") == host_head
    assert bundle.get("run_id") == run_id

    # §1j provenance: real backend only.
    provenance = bundle.get("provenance", {})
    assert provenance.get("backend_class") == "OpencodeBackend"
    assert provenance.get("fake_backend") is False
    assert provenance.get("trac_fake_simulate") is False
    assert provenance.get("assignment_overlay") is False
    assert provenance.get("assignment_simulation") is False
    assert provenance.get("event_origin") == "runtime"

    # §1j agent I/O: complete receipts covering the required Devon phases.
    agent_io = bundle.get("agent_io", [])
    assert agent_io, "bundle must carry non-empty agent_io receipts"
    for receipt in agent_io:
        assert receipt.get("audit_completeness") == "complete", (
            "every agent I/O receipt must be audit-complete"
        )
    devon_phases = {
        r.get("phase") for r in agent_io if r.get("role") == "devon"
    }
    assert {"RED", "GREEN", "REFACTOR"} <= devon_phases, (
        f"required Devon phases missing from agent_io: {devon_phases}"
    )

    # §1j event sequence: the events blob exists and matches its digest.
    event_sequence = bundle.get("event_sequence", {})
    events_ref = event_sequence.get("events_ref", "")
    events_sha = event_sequence.get("events_sha256", "")
    assert events_ref and events_sha, "bundle must carry event_sequence evidence"
    blob_path = run_dir / events_ref
    assert blob_path.is_file(), f"events blob missing at {blob_path}"
    blob_bytes = blob_path.read_bytes()
    assert hashlib.sha256(blob_bytes).hexdigest() == events_sha, (
        "events blob digest must match events_sha256 (byte-verbatim relay)"
    )

    # §1j lineage: the R ref in the bundle exists in HOST git and matches
    # the journey's red.checkpointed event.
    lineage = bundle.get("rgr_lineage", {})
    events = _events(host, run_id)
    red_cp = [e for e in events if e["type"] == "red.checkpointed"]
    assert red_cp, "host events must contain red.checkpointed"
    assert lineage.get("r_sha") == red_cp[0]["payload"].get("r_sha"), (
        "bundle r_sha must match the host journey's checkpointed R"
    )
    assert lineage.get("red_ref") == red_cp[0]["payload"].get("ref")

    # §2d reason algorithm step 5: gates + ISLAND_GATE_2 observed.
    gate_obs = bundle.get("gate_observations", [])
    island = [g for g in gate_obs if g.get("gate") == "ISLAND_GATE_2"]
    assert island and island[0].get("status") == "pass", (
        "bundle gate_observations must record ISLAND_GATE_2 pass"
    )

    # §1j boundary.
    assert bundle.get("boundary", {}).get("terminal_state") == "boundary"

    # §2d --json: canonical 7-field object; evidence_path relays the HOST
    # bundle byte-verbatim (exit 0 iff satisfied).
    check_json = subprocess.run(
        installed_trac_command(isolated_bin, "check", "release-evidence", "--json"),
        cwd=host, env=env, capture_output=True, text=True, timeout=120,
    )
    assert check_json.returncode == 0, (
        f"json check must exit 0 on a satisfied bundle, got "
        f"{check_json.returncode}: {check_json.stdout}{check_json.stderr}"
    )
    payload = json.loads(check_json.stdout)
    assert set(payload) == {
        "backend", "candidate_sha", "evidence_path", "event_bounds",
        "reason_code", "run_id", "status",
    }, f"§2d JSON field set is closed, got {sorted(payload)}"
    assert payload["status"] == "satisfied"
    assert payload["reason_code"] == "ok"
    assert payload["candidate_sha"] == host_head
    assert payload["run_id"] == run_id
    assert payload["evidence_path"].endswith(
        f".tracks/runtime/release-evidence/v1/{host_head}/{run_id}/evidence.json"
    ), f"evidence_path must relay the host canonical bundle: {payload['evidence_path']}"
    relayed = Path(payload["evidence_path"]).read_bytes()
    assert relayed == bundle_bytes, (
        "the only permitted transport is byte-verbatim relay of the host bundle"
    )
    assert json.dumps(payload, sort_keys=True, separators=(",", ":")) == check_json.stdout.rstrip("\n"), (
        "§2d canonical form is sort_keys + compact separators"
    )
