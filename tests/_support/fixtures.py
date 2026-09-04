"""Shared test fixtures: available to integration/e2e via their conftest imports."""

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


def steps_dir() -> Path:
    override = os.environ.get("TRACKS_STEPS_DIR", "").strip()
    if override:
        p = Path(override)
        p.mkdir(parents=True, exist_ok=True)
        return p
    base = Path(os.environ.get("TMPDIR", "/tmp")) / "tracks-steps"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _node_id(node: str) -> str:
    return node.replace("::", "__").replace("/", "_").replace(".py", "")


class StepLog:
    def __init__(self, nodeid: str, truncate: bool = False):
        self.path = steps_dir() / f"{_node_id(nodeid)}.steps.ndjson"
        self._seq = 0
        if truncate and self.path.exists():
            self.path.unlink()
        self._fh = self.path.open("a", encoding="utf-8")

    def step(self, step: str, status: str = "ok", **detail) -> None:
        self._seq += 1
        rec = {"seq": self._seq, "ts_ms": int(time.time() * 1000), "step": step, "status": status}
        if detail:
            rec["detail"] = _safe(detail)
        try:
            self._fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self._fh.flush()
        except OSError:
            pass

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self._fh.close()


def _safe(obj):
    if isinstance(obj, dict):
        return {k: _safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_safe(v) for v in obj]
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        return str(obj)


@pytest.fixture(autouse=True)
def _force_fake_backend(monkeypatch):
    monkeypatch.setenv("TRAC_AGENT_BACKEND", "fake")


@pytest.fixture
def steps(request):
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
    venv_target = Path(sys.prefix).resolve()
    (repo / ".venv").symlink_to(venv_target, target_is_directory=True)
    (repo / ".gitignore").write_text(".venv\n", encoding="utf-8")
    g("add", "README.md", ".gitignore")
    g("commit", "-m", "initial")
    return repo


@pytest.fixture
def trac(host_repo, steps):
    def run(*args, stdin=None, simulate=None):
        env = {
            k: v for k, v in os.environ.items() if k not in ("TRACKS_HOME", "TRAC_FAKE_SIMULATE")
        }
        env["TRAC_AGENT_BACKEND"] = os.environ.get("TRAC_AGENT_BACKEND", "fake")
        if simulate:
            env["TRAC_FAKE_SIMULATE"] = simulate
        if _UNDER_COVERAGE:
            env["COVERAGE_PROCESS_START"] = str(_REPO_ROOT / "pyproject.toml")
            env["COVERAGE_FILE"] = str(_REPO_ROOT / ".coverage")
            env["PYTHONPATH"] = _SUBCOV_DIR + os.pathsep + env.get("PYTHONPATH", "")
        # M7 (convergence plan 2026-09-05): the runtime loop executes the
        # installed wheel, but the TESTS exercise the tree under
        # development -- pin the repo root ahead of site-packages so
        # `python -m tracks.cli.main` resolves the tree's package even
        # when the venv carries a non-editable install.
        env["PYTHONPATH"] = str(_REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
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

    # Expose the backing host repo so shared walkers (tests/e2e/helpers.py)
    # can seed repo facts (phase0 premises) without changing their signature.
    run.repo = host_repo
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
        home = paths.tracks_home(host_repo)
        events = [
            {
                "run_id": r[0],
                "seq": r[1],
                "type": r[2],
                "payload": _load_event_payload(home, json.loads(r[3])),
                "command_id": r[4],
            }
            for r in rows
        ]
        if run_id:
            events = [e for e in events if e["run_id"] == run_id]
        return events

    return query


def _load_event_payload(home: Path, payload: dict) -> dict:
    if set(payload) == {"$ref"}:
        return json.loads((paths.blobs_dir(home) / payload["$ref"]).read_bytes())
    return payload
