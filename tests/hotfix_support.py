"""Shared hotfix test scaffolding (v0.6, IF-HOTFIX-001~009).

Not pytest-collected: module name does not match ``test_*`` / ``*_test``
(see ``pyproject.toml`` ``testpaths``). Hosts the in-repo inline data and
host-repo baseline construction used across the hotfix integration/e2e
suites so tests stay deterministic and offline (test-plan §2.4).

Two responsibilities:

* :data:`HOST_ISSUES_SEED` — the canonical fake issue corpus (interfaces.md
  §3b schema). Bug #42 (anchored happy path), non-bug #99, no-anchor bug
  #77 (Sage NO_ANCHOR -> AWAIT_HUMAN), and a dev-scenario bug #50 whose
  body declares a target release branch.
* :func:`seed_host_issues` / :func:`seed_v05_approved_baseline` /
  :func:`seed_release_branch` — construct the observable host-repo state
  the hotfix entry reads (host-issues.json, an approved v0.5 trio +
  ``approval.recorded`` event projection, a ``releases/v0.6`` branch for
  the dev scenario).

These helpers construct *only* observable outlets (files + the events
table) the implementation is contracted to read; they never mock or
replace kernel/executor behavior (test-plan §6.2 boundary).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tracks import paths
from tracks.kernel.events import EventEnvelope
from tracks.store.store import SCHEMA_VERSION, Store, _now_iso  # type: ignore[attr-defined]

# Canonical fake issue corpus (interfaces.md §3b). Bug / non-bug / no-anchor
# bug / dev-scenario bug. The hotfix entry's precheck reads is_bug from the
# ``bug`` label; Sage's anchoring reads the body's optional FR/NFR hints.
HOST_ISSUES_SEED: dict[str, dict] = {
    "42": {
        "title": "crash on event replay after boundary",
        "body": (
            "### 版本\nv0.5\n### 对应 FR/NFR\nFR-0030\n"
            "### 症状\nReplaying a run after boundary raises KeyError."
        ),
        "labels": ["bug"],
    },
    "77": {
        "title": "regression: no AC matches the observed behavior",
        "body": "### 版本\nv0.5\n### 症状\nBehavior drifts from any documented AC.",
        "labels": ["bug"],
    },
    "99": {
        "title": "feature request: add hotfix live probe",
        "body": "### 版本\nv0.6\n### 对应 FR/NFR\nFR-0240",
        "labels": ["enhancement"],
    },
    "50": {
        "title": "dev regression on release branch",
        "body": "### 版本\nv0.6\n### 对应 FR/NFR\nFR-0030",
        "labels": ["bug"],
    },
    "58": {
        "title": "seam probe: hotfix must reach M-TEST",
        "body": "### 版本\nv0.5\n### 对应 FR/NFR\nFR-0030",
        "labels": ["bug"],
    },
}


def seed_host_issues(host_repo: Path, seed: dict[str, dict] | None = None) -> Path:
    """Write the fake issue corpus to ``.tracks/runtime/host-issues.json``.

    Matches interfaces.md §3b: top-level keys are decimal-string issue
    numbers; values carry ``title`` / ``body`` / ``labels``. The fake
    channel's ``FakeIssueBackend.fetch_issue`` reads this file; a missing
    file is the empty set (every fetch -> ``issue_not_found``).
    """
    home = paths.tracks_home(host_repo)
    runtime = paths.runtime_dir(home)
    runtime.mkdir(parents=True, exist_ok=True)
    path = runtime / "host-issues.json"
    path.write_text(
        json.dumps(seed if seed is not None else HOST_ISSUES_SEED, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def seed_project_contract(host_repo: Path) -> Path:
    """Write the host ``.tracks/projects/project.toml`` execution contract.

    Mirrors the Archer-produced contract (test-plan §2.3.1, FR-0190/FR-0120).
    The hotfix run's M-DESIGN EXIT layout gate (``validate_layout``,
    tracks/project.py) requires the contract to already exist with non-empty
    ``[layout.devon]`` / ``[layout.shield]`` writable lists; the M-TEST
    collect/RED_CHECK commands consume the ``[integration]`` / ``[e2e]``
    run contracts the same way the real host repo does.  The fixture data
    (not any implementation output) is the truth source (test-plan §2.3.1
    '合同文件已存在且内容不变'; §3.1 row 3).
    """
    home = paths.tracks_home(host_repo)
    toml = paths.project_toml_path(home)
    toml.parent.mkdir(parents=True, exist_ok=True)
    toml.write_text(
        "[integration]\n"
        'framework = "pytest"\n'
        'paths = ["tests/integration/"]\n'
        'collect = ".venv/bin/python -m pytest --collect-only -q tests/integration/"\n'
        'run = ".venv/bin/python -m pytest tests/integration/ --tb=short -q"\n'
        'cwd = "."\n\n'
        "[e2e]\n"
        'framework = "pytest"\n'
        'paths = ["tests/e2e/"]\n'
        'collect = ".venv/bin/python -m pytest --collect-only -q tests/e2e/"\n'
        'run = ".venv/bin/python -m pytest tests/e2e/ --tb=short -q"\n'
        'cwd = "."\n\n'
        "[layout]\n\n"
        "[layout.devon]\n"
        'writable = ["tracks/", "tests/unit/"]\n\n'
        "[layout.shield]\n"
        'writable = ["tests/integration/", "tests/e2e/", "tests/e2e_live/", "tests/assets/", "tests/counterexamples/"]\n',
        encoding="utf-8",
    )
    return toml


def seed_v05_approved_baseline(host_repo: Path, version: str = "v0.5") -> Path:
    """Construct an approved target-version baseline in the host repo.

    Uses the minimal ``r/trio`` toolkit: writes the project execution
    contract (``.tracks/projects/project.toml``, via
    :func:`seed_project_contract`) plus the minimal trio
    (story/spec/acceptance) under ``.tracks/projects/<version>/`` plus an
    ``approval.recorded`` event in the runtime events table, so
    ``approved_versions`` (derived from ``approval.recorded`` payloads,
    IF-HOTFIX-005) contains ``version``.

    The acceptance.md body carries a known AC heading so cross-version
    anchor validation (IF-HOTFIX-004) can resolve ``AC-FR0030-01@v0.5``.
    The fixture data (not any implementation output) is the truth source
    (test-plan §3.1 row 3: simple rule -> test data itself).
    """
    seed_project_contract(host_repo)
    home = paths.tracks_home(host_repo)
    vdir = paths.version_dir(home, version)
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / "story.md").write_text(f"# {version} story\n\nbaseline story.\n", encoding="utf-8")
    (vdir / "spec.md").write_text(f"# {version} spec\n\n### FR-0030\n\nbaseline spec.\n", encoding="utf-8")
    (vdir / "acceptance.md").write_text(
        f"# {version} acceptance\n\n### AC-FR0030-01\n\nbaseline AC.\n\n### AC-FR0160-03\n\nbaseline boundary AC.\n",
        encoding="utf-8",
    )
    # IF-HOTFIX-010: the canonical validator's sibling-lookup now resolves
    # the target version dir's interfaces.md read-only from a hotfix dir;
    # the baseline fixture must carry a §5 IF Registry so the resolver
    # path validates (non-empty registry). One baseline IF- identifier is
    # enough for the design-trace / test-tasks file-level validators.
    (vdir / "interfaces.md").write_text(
        f"# {version} interfaces\n\n## 5. IF Registry\n\n"
        "- IF-BASELINE-001: baseline interface contract\n"
        "- IF-HOTFIX-009: hotfix anchor verdict threading (inherited by delta)\n",
        encoding="utf-8",
    )

    # Record an approval.recorded event so approved_versions sees this version.
    # The reducer (kernel/machine.py _on_approval_recorded) projects ``digest``
    # and ``actor`` from the payload onto State; a synthetic digest keeps the
    # fixture self-contained (no run through cmd_start needed).
    store = Store(home)
    run_id = f"approval-{version}"
    try:
        store.append(
            run_id=run_id,
            version=version,
            type="approval.recorded",
            payload={
                "version": version,
                "actor": "Aaron",
                "digest": f"fixture-{version}-baseline-digest",
            },
        )
    finally:
        store.close()
    return vdir


def seed_release_branch(host_repo: Path, name: str = "releases/v0.6") -> str:
    """Create a release branch off main in the host repo.

    Used by the dev-scenario precheck (IF-HOTFIX-003 P-3): the active
    release branch is probed via ``git branch --list`` plus the active
    run's branch. Returns the branch name.
    """
    subprocess.run(
        ["git", "branch", name], cwd=host_repo, check=True, capture_output=True
    )
    return name


def seed_inprogress_feature_run(
    host_repo: Path, version: str = "v0.5", run_id: str = "feature-run-1"
) -> str:
    """Project an in-progress (non-terminal) feature run alongside the hotfix
    (FR-0242 / NFR-0110).

    Writes a ``stage.entered(M-DESIGN)`` event *without* ``run.completed``
    so the run is a contract-valid suspended object: interfaces §2b emits a
    ``suspended:`` row only for non-completed runs, and the boundary
    restore (AC-FR0242-02) can only be exercised against a run that is
    suspendable in the first place. Returns the run_id.
    """
    home = paths.tracks_home(host_repo)
    store = Store(home)
    try:
        store.append(
            run_id=run_id,
            version=version,
            type="stage.entered",
            payload={"stage": "M-DESIGN"},
        )
    finally:
        store.close()
    return run_id


def read_events(host_repo: Path, run_id: str | None = None) -> list[dict]:
    """Read the events table (IF-001 §6 / IF-HOTFIX-002 append-only outlet)."""
    import sqlite3

    db = paths.db_path(paths.tracks_home(host_repo))
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute(
            "SELECT run_id, seq, type, payload, command_id FROM events ORDER BY run_id, seq"
        ).fetchall()
    finally:
        conn.close()
    events = [
        {
            "run_id": r[0],
            "seq": r[1],
            "type": r[2],
            "payload": json.loads(r[3]),
            "command_id": r[4],
        }
        for r in rows
    ]
    if run_id:
        events = [e for e in events if e["run_id"] == run_id]
    return events


def git(host_repo: Path, *args: str) -> str:
    """Run a git command in the host repo, returning stdout."""
    return subprocess.run(
        ["git", *args], cwd=host_repo, capture_output=True, text=True, check=True
    ).stdout


def assert_hotfix_drives_to_mtest(
    trac, host_repo: Path, *, fresh_issue: str | None = None
) -> None:
    """Drive the hotfix run past the M-DESIGN delta into the M-TEST journey.

    IF-HOTFIX-010 (resolve_inherited_baseline_docs) is not yet wired into
    the run-loop validate step, so the hotfix delta run parks at
    ``stage=M-DESIGN awaiting=escalation reason=[trace] test-plan validate
    requires acceptance.md in same dir`` instead of progressing to M-TEST.
    Asserting ``stage=M-TEST`` in ``trac status`` is therefore a legal-Red
    assertion: it fails (at the unimplemented baseline-resolver seam) until
    IF-HOTFIX-010 lands, and passes once the hotfix journey can reach the
    M-TEST stage.

    ``fresh_issue``: for tests whose own entry terminated before leaving an
    active M-DESIGN run (REJECTED / FEATURE_ROUTE / lock-reject), enter a
    fresh post-release hotfix run on the given issue so the seam drive has a
    run to continue.
    """
    status = trac("status")
    if fresh_issue is not None and "stage=M-DESIGN" not in status.stdout:
        r = trac("hotfix", str(fresh_issue), "--scenario", "post-release")
        assert r.returncode == 0, r.stderr
    for _ in range(6):
        cont = trac("run")
        assert cont.returncode == 0, cont.stderr
    status = trac("status")
    assert "stage=M-TEST" in status.stdout, (
        "hotfix run must reach M-TEST; "
        f"blocked by IF-HOTFIX-010 baseline-resolver seam: {status.stdout.strip()}"
    )


__all__ = [
    "HOST_ISSUES_SEED",
    "seed_host_issues",
    "seed_v05_approved_baseline",
    "seed_project_contract",
    "seed_release_branch",
    "seed_inprogress_feature_run",
    "read_events",
    "git",
    "assert_hotfix_drives_to_mtest",
    "EventEnvelope",
    "SCHEMA_VERSION",
    "Store",
    "_now_iso",
]
