"""Shared fixtures: tmp host git repo + installed `trac` CLI + event-log query."""
import json
import os
import sqlite3
import subprocess
import sys

import pytest


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
def trac(host_repo):
    def run(*args, stdin=None, simulate=None):
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("TRACKS_HOME", "TRAC_FAKE_SIMULATE")
        }
        if simulate:
            env["TRAC_FAKE_SIMULATE"] = simulate
        # Invoke the CLI as a module with the SAME interpreter running the
        # tests, so it works regardless of how pytest/coverage is launched and
        # does not depend on a `trac` console-script shim existing on disk.
        return subprocess.run(
            [sys.executable, "-m", "tracks.cli.main", *args],
            cwd=host_repo,
            env=env,
            input=stdin,
            capture_output=True,
            text=True,
        )

    return run


@pytest.fixture
def event_log(host_repo):
    def query(run_id=None):
        db = host_repo / ".tracks" / "runtime" / "tracks.db"
        conn = sqlite3.connect(db)
        try:
            rows = conn.execute(
                "SELECT run_id, seq, type, payload, command_id FROM events "
                "ORDER BY run_id, seq"
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
