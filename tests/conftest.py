"""Shared fixtures: tmp host git repo + installed `trac` CLI + event-log query."""

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tests.steps import StepLog

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SUBCOV_DIR = str(Path(__file__).resolve().parent / "_subprocess_coverage")
# True when the test session runs under coverage (e.g. `coverage run -m pytest`):
# coverage imports itself into this process before pytest collects conftest.
_UNDER_COVERAGE = "coverage" in sys.modules


@pytest.fixture(autouse=True)
def _force_fake_backend(monkeypatch):
    """Deterministic suite forces the fake backend (SPEC test-plan §6 dual-channel).

    The live opencode E2E channel is a separate opt-in job that overrides
    TRAC_AGENT_BACKEND in its own fixture; everything else stays fake so the
    suite never shells out to a real model.
    """
    monkeypatch.setenv("TRAC_AGENT_BACKEND", "fake")


@pytest.fixture
def steps(request):
    """Per-test structured step log (NDJSON). See tests/steps.py.

    Usage::

        steps.step("trac init", returncode=0)
        ...
        steps.step("assert awaiting=review", status="fail" if cond else "ok")
    """
    log = StepLog(request.node.nodeid, truncate=True)
    yield log
    log.close()


@pytest.fixture
def host_repo(tmp_path):
    repo = tmp_path / "host"
    repo.mkdir()

    def g(*args):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

    g("init", "-b", "main")
    g("config", "user.email", "test@example.com")
    g("config", "user.name", "Test Human")
    (repo / "README.md").write_text("host project\n", encoding="utf-8")
    g("add", "README.md")
    g("commit", "-m", "initial")
    return repo


@pytest.fixture
def trac(host_repo, steps):
    def run(*args, stdin=None, simulate=None):
        env = {
            k: v for k, v in os.environ.items() if k not in ("TRACKS_HOME", "TRAC_FAKE_SIMULATE")
        }
        # Deterministic channel: fake backend unless the caller opts into live.
        env["TRAC_AGENT_BACKEND"] = os.environ.get("TRAC_AGENT_BACKEND", "fake")
        if simulate:
            env["TRAC_FAKE_SIMULATE"] = simulate
        if _UNDER_COVERAGE:
            # Merge E2E subprocess coverage into the report: the subprocess
            # auto-starts coverage (sitecustomize on PYTHONPATH), reads config
            # from COVERAGE_PROCESS_START, and writes a parallel data file under
            # the absolute COVERAGE_FILE base (its cwd is the tmp host repo, so
            # a relative path would be lost). `coverage combine` merges these.
            env["COVERAGE_PROCESS_START"] = str(_REPO_ROOT / "pyproject.toml")
            env["COVERAGE_FILE"] = str(_REPO_ROOT / ".coverage")
            env["PYTHONPATH"] = _SUBCOV_DIR + os.pathsep + env.get("PYTHONPATH", "")
        # Invoke the CLI as a module with the SAME interpreter running the
        # tests, so it works regardless of how pytest/coverage is launched and
        # does not depend on a `trac` console-script shim existing on disk.
        proc = subprocess.run(
            [sys.executable, "-m", "tracks.cli.main", *args],
            cwd=host_repo,
            env=env,
            input=stdin,
            capture_output=True,
            text=True,
        )
        steps.step(
            "trac " + " ".join(args),
            status="ok" if proc.returncode == 0 else "fail",
            returncode=proc.returncode,
        )
        return proc

    return run


@pytest.fixture
def event_log(host_repo):
    def query(run_id=None):
        db = host_repo / ".tracks" / "runtime" / "tracks.db"
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

    return query
