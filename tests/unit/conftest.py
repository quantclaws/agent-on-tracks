"""Devon-owned unit fixtures.

Unit tests deliberately do not import Shield's tests/_support package. This
keeps unit infrastructure separate from integration/e2e infrastructure.
"""

import contextlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tracks import paths

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SUBCOV_DIR = str(Path(__file__).resolve().parent.parent / "_subprocess_coverage")
_UNDER_COVERAGE = "coverage" in sys.modules


def _steps_dir() -> Path:
    override = os.environ.get("TRACKS_STEPS_DIR", "").strip()
    if override:
        target = Path(override)
        target.mkdir(parents=True, exist_ok=True)
        return target
    target = Path(os.environ.get("TMPDIR", "/tmp")) / "tracks-steps"
    target.mkdir(parents=True, exist_ok=True)
    return target


class _StepLog:
    def __init__(self, nodeid: str):
        name = nodeid.replace("::", "__").replace("/", "_").replace(".py", "")
        self.path = _steps_dir() / f"{name}.steps.ndjson"
        self.path.unlink(missing_ok=True)
        self._file = self.path.open("a", encoding="utf-8")
        self._sequence = 0

    def step(self, name: str, status: str = "ok", **detail) -> None:
        self._sequence += 1
        record = {"seq": self._sequence, "ts_ms": int(time.time() * 1000), "step": name, "status": status}
        if detail:
            record["detail"] = detail
        self._file.write(json.dumps(record, default=str) + "\n")
        self._file.flush()

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self._file.close()


@pytest.fixture(autouse=True)
def _force_fake_backend(monkeypatch):
    monkeypatch.setenv("TRAC_AGENT_BACKEND", "fake")


@pytest.fixture
def steps(request):
    log = _StepLog(request.node.nodeid)
    yield log
    log.close()


@pytest.fixture
def host_repo(tmp_path):
    repo = tmp_path / "host"
    repo.mkdir()

    def git(*args):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

    git("init", "-b", "main")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test Human")
    (repo / "README.md").write_text("host project\n", encoding="utf-8")
    (repo / ".venv").symlink_to(Path(sys.prefix).resolve(), target_is_directory=True)
    (repo / ".gitignore").write_text(".venv\n", encoding="utf-8")
    git("add", "README.md", ".gitignore")
    git("commit", "-m", "initial")
    return repo


@pytest.fixture
def trac(host_repo, steps):
    def run(*args, stdin=None, simulate=None):
        env = {key: value for key, value in os.environ.items() if key not in ("TRACKS_HOME", "TRAC_FAKE_SIMULATE")}
        env["TRAC_AGENT_BACKEND"] = os.environ.get("TRAC_AGENT_BACKEND", "fake")
        if simulate:
            env["TRAC_FAKE_SIMULATE"] = simulate
        if _UNDER_COVERAGE:
            env["COVERAGE_PROCESS_START"] = str(_REPO_ROOT / "pyproject.toml")
            env["COVERAGE_FILE"] = str(_REPO_ROOT / ".coverage")
            env["PYTHONPATH"] = _SUBCOV_DIR + os.pathsep + env.get("PYTHONPATH", "")
        result = subprocess.run([sys.executable, "-m", "tracks.cli.main", *args], cwd=host_repo, env=env, input=stdin, capture_output=True, text=True)
        steps.step("trac " + " ".join(args), status="ok" if result.returncode == 0 else "fail", returncode=result.returncode)
        return result

    return run


@pytest.fixture
def event_log(host_repo):
    def query(run_id=None):
        db = host_repo / ".tracks" / "runtime" / "tracks.db"
        connection = sqlite3.connect(db)
        try:
            rows = connection.execute("SELECT run_id, seq, type, payload, command_id FROM events ORDER BY run_id, seq").fetchall()
        finally:
            connection.close()
        home = paths.tracks_home(host_repo)
        result = [{"run_id": row[0], "seq": row[1], "type": row[2], "payload": _load_payload(home, json.loads(row[3])), "command_id": row[4]} for row in rows]
        return [event for event in result if event["run_id"] == run_id] if run_id else result

    return query


def _load_payload(home: Path, payload: dict) -> dict:
    if set(payload) == {"$ref"}:
        return json.loads((paths.blobs_dir(home) / payload["$ref"]).read_bytes())
    return payload
