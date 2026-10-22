"""Auto-push + bounded CI-run wait for the M-VERIFY readback (IF-VERIFY-004).

Every git remote is a local bare directory; the CI HTTP transport is
monkeypatched. No network, no real GitHub, no wall-clock wait.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from tests.unit.helpers import git_repo
from tracks.executor import verify_ci
from tracks.executor.verify_ci import ExecVerifyCiMixin
from tracks.kernel.events import Command

_REPO_ID = "acme/host"
_WORKFLOW = "ci.yml"
_REQUIRED = ["lint"]


class _FakeStore:
    def __init__(self, events=()):
        self._events = list(events)

    def events(self, run_id):
        return list(self._events)


class _Host(ExecVerifyCiMixin):
    def __init__(self, repo, events=()):
        self.repo = repo
        self.run_id = "RUN"
        self.store = _FakeStore(events)
        self.emitted: list[tuple] = []
        self.issued: list[tuple] = []
        self.contract = SimpleNamespace(
            ci={"repo_env": "TRAC_CI_REPO", "workflow": _WORKFLOW,
                "required_checks": _REQUIRED}
        )

    def _emit(self, event, payload=None, **kwargs):
        self.emitted.append((event, payload, kwargs))

    def issue(self, cmd, command_id=None):
        self.issued.append((cmd, command_id))

    def _latest_event(self, event_type):
        hits = [e for e in self.store.events(self.run_id) if e.type == event_type]
        return hits[-1] if hits else None

    def _load_or_default_contract(self, cmd, sha):
        return self.contract, "DIGEST", "source"

    def _park_preview(self, cmd, sha, contract, digest=None):
        return None


def _ev(seq, type, payload=None, command_id=None):
    return SimpleNamespace(seq=seq, type=type, payload=payload or {}, command_id=command_id)


def _cmd(candidate):
    return Command(
        kind="observe_ci_runs",
        params={"candidate_sha": candidate},
        command_id="C-CI",
    )


def _git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _prepare(tmp_path, monkeypatch):
    repo = git_repo(tmp_path)
    bare = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", str(bare)], check=True, capture_output=True
    )
    _git(repo, "remote", "add", "origin", str(bare))
    sha = _git(repo, "rev-parse", "HEAD")
    monkeypatch.setenv("TRAC_CI_REPO", _REPO_ID)
    host = _Host(repo, [_ev(1, "candidate.frozen", {"candidate_sha": sha, "branch": "main"})])
    return host, repo, bare, sha


def _green(sha):
    return {
        "repo": _REPO_ID,
        "workflow": _WORKFLOW,
        "run_id": 17,
        "head_sha": sha,
        "conclusion": "success",
        "checks": {"lint": "success"},
    }


def _pending(sha, reason):
    return {
        "repo": _REPO_ID,
        "workflow": _WORKFLOW,
        "run_id": None if reason == "missing" else 17,
        "head_sha": None if reason == "missing" else sha,
        "conclusion": None,
        "checks": {},
        "reason": reason,
    }


def _types(host):
    return [event[0] for event in host.emitted]


def _payload(host, event_type):
    return [event[1] for event in host.emitted if event[0] == event_type]


def _observe(host, sha):
    host._do_observe_ci_runs(_cmd(sha), SimpleNamespace(), None, False)


# AC-FR0270-01@v0.8 TRACKS-TRACE run registration/status drives the poll gate
def test_verify_ci_pending_detection_uses_readback_fields():
    assert verify_ci._ci_run_pending({"reason": "missing", "run_id": None})
    assert not verify_ci._ci_run_pending({"reason": "missing", "run_id": 7})
    assert verify_ci._ci_run_pending({"reason": "run_incomplete", "run_id": 7})
    assert verify_ci._ci_run_pending({"run_status": "in_progress", "run_id": 7})
    assert not verify_ci._ci_run_pending({"run_status": "completed", "run_id": 7})


# AC-FR0270-01@v0.8 TRACKS-TRACE push candidate then bind readback
def test_verify_ci_pushes_frozen_candidate_to_origin(tmp_path, monkeypatch):
    host, repo, _bare, sha = _prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(verify_ci, "readback_ci_run", lambda *args: _green(sha))

    _observe(host, sha)

    assert _git(repo, "ls-remote", "--heads", "origin", "main").startswith(sha)
    assert _payload(host, "ci.run_observed")[-1]["status"] == "passed"
    assert host.issued and host.issued[-1][0].params["substate"] == "VERIFY_FINAL"


# AC-FR0270-01@v0.8 TRACKS-TRACE already-pushed candidate is an idempotent skip
def test_verify_ci_push_is_idempotent_for_the_same_frozen_sha(tmp_path, monkeypatch):
    host, repo, _bare, sha = _prepare(tmp_path, monkeypatch)
    _git(repo, "push", "origin", f"{sha}:refs/heads/main")
    monkeypatch.setattr(verify_ci, "readback_ci_run", lambda *args: _green(sha))
    calls: list[tuple] = []
    real_git = verify_ci.git

    def _spy(repo_path, *args, **kwargs):
        calls.append(args)
        return real_git(repo_path, *args, **kwargs)

    monkeypatch.setattr(verify_ci, "git", _spy)

    _observe(host, sha)

    assert not any(args[0] == "push" for args in calls)
    assert _payload(host, "ci.run_observed")[-1]["status"] == "passed"


# AC-FR0270-03@v0.8 TRACKS-TRACE failed push blocks before any CI readback
def test_verify_ci_push_failure_never_reads_back(tmp_path, monkeypatch):
    host, repo, _bare, sha = _prepare(tmp_path, monkeypatch)
    _git(repo, "remote", "set-url", "origin", str(tmp_path / "missing.git"))
    monkeypatch.setattr(
        verify_ci,
        "readback_ci_run",
        lambda *args: pytest.fail("readback must not run after a failed push"),
    )

    _observe(host, sha)

    assert _types(host) == ["attention.required"]
    attention = _payload(host, "attention.required")[-1]
    assert attention["area"] == "ci_readback"
    assert attention["reason"] == "push_failed"
    assert attention["detail"]
    assert attention["next"]


@pytest.mark.parametrize("pending_reason", ["missing", "run_incomplete"])
# AC-FR0270-01@v0.8 TRACKS-TRACE pending run polls with a visible heartbeat
def test_verify_ci_polls_pending_run_then_binds_green(
    tmp_path, monkeypatch, pending_reason
):
    host, _repo, _bare, sha = _prepare(tmp_path, monkeypatch)
    responses = iter([_pending(sha, pending_reason), _green(sha)])
    monkeypatch.setattr(verify_ci, "readback_ci_run", lambda *args: next(responses))
    monkeypatch.setattr(verify_ci, "_CI_POLL_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(verify_ci, "_CI_POLL_TIMEOUT_SECONDS", 5)

    _observe(host, sha)

    heartbeat = _payload(host, "advisory.recorded")
    assert len(heartbeat) == 1
    assert heartbeat[0]["area"] == "ci_readback"
    assert heartbeat[0]["status"] == "pending"
    assert heartbeat[0]["reason"] == pending_reason
    observed = _payload(host, "ci.run_observed")[-1]
    assert observed["status"] == "passed" and observed["api_verified"] is True
    assert host.issued and host.issued[-1][0].params["substate"] == "VERIFY_FINAL"


# AC-FR0270-01@v0.8 TRACKS-TRACE missing run past the bound parks on attention
def test_verify_ci_pending_timeout_lands_attention(tmp_path, monkeypatch):
    host, _repo, _bare, sha = _prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(
        verify_ci, "readback_ci_run", lambda *args: _pending(sha, "missing")
    )
    monkeypatch.setattr(verify_ci, "_CI_POLL_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(verify_ci, "_CI_POLL_TIMEOUT_SECONDS", 0)

    _observe(host, sha)

    attention = _payload(host, "attention.required")[-1]
    assert attention["area"] == "ci_readback"
    assert attention["reason"] == "ci_pending_timeout"
    assert attention["next"]
    assert "advisory.recorded" in _types(host)
    assert "ci.run_observed" not in _types(host)
    assert host.issued == []


@pytest.mark.parametrize(
    "conclusion,checks,reason",
    [
        ("failure", {"lint": "failure"}, "failed_conclusion"),
        ("success", {"lint": "failure"}, "check_failed"),
    ],
)
# AC-FR0270-02@v0.8 TRACKS-TRACE completed red run routes ci_failed with checks
def test_verify_ci_red_run_routes_ci_failed(
    tmp_path, monkeypatch, conclusion, checks, reason
):
    host, _repo, _bare, sha = _prepare(tmp_path, monkeypatch)
    red = {
        "repo": _REPO_ID,
        "workflow": _WORKFLOW,
        "run_id": 17,
        "head_sha": sha,
        "conclusion": conclusion,
        "checks": checks,
    }
    monkeypatch.setattr(verify_ci, "readback_ci_run", lambda *args: red)

    _observe(host, sha)

    observed = _payload(host, "ci.run_observed")[-1]
    assert observed["status"] == "failed"
    assert observed["reason"] == reason
    attention = _payload(host, "attention.required")[-1]
    assert attention["reason"] == "ci_failed"
    assert "lint=failure" in attention["detail"]
    assert attention["next"] == "fix the CI failure and trac run retries"
    assert host.issued == []
