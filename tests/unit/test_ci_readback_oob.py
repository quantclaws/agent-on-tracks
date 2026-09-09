"""OOB RED coverage for the CI readback takeover slice.

These tests exercise the public effects boundary and the Runtime readback
handler.  The HTTP/API and agent boundaries are replaced with small fakes;
the CI binding and reconcile behavior remain real.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.unit.helpers import git_repo
from tracks.effects import github
from tracks.executor.executor import Executor
from tracks.kernel.events import Command
from tracks.store import Store

_CANDIDATE = "a" * 40
_FOREIGN = "f" * 40


# AC-FR0270-01@v0.8 TRACKS-TRACE IF-VERIFY-004 missing API head is not candidate
def test_readback_missing_api_head_is_not_verified_from_requested_candidate(monkeypatch):
    """A response without head_sha must stay missing/failed; the requested
    candidate is not evidence that the API returned that head."""
    monkeypatch.setenv("GITHUB_TOKEN", "token")

    monkeypatch.setattr(
        github,
        "_get_any",
        lambda _request: {
            "workflow_runs": [
                {
                    "id": 17,
                    "name": "CI",
                    "path": ".github/workflows/ci.yml",
                    "conclusion": "success",
                }
            ]
        },
    )

    observed = github.readback_ci_run("acme/host", "ci.yml", _CANDIDATE)

    assert observed["head_sha"] != _CANDIDATE
    assert observed["status"] == "failed"
    assert observed["reason"] == "missing"
    assert observed["api_verified"] is False


# AC-FR0270-01@v0.8 TRACKS-TRACE IF-VERIFY-004 workflow identity binding
def test_readback_selects_the_requested_workflow_run(monkeypatch):
    """A candidate head may have several workflow runs; the requested
    workflow must determine which run is bound."""
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setattr(
        github,
        "_get_any",
        lambda _request: {
            "workflow_runs": [
                {
                    "id": 1,
                    "name": "CI",
                    "path": ".github/workflows/other.yml",
                    "workflow_id": 101,
                    "head_sha": _CANDIDATE,
                    "conclusion": "success",
                },
                {
                    "id": 2,
                    "name": "CI",
                    "path": ".github/workflows/ci.yml",
                    "workflow_id": 102,
                    "head_sha": _CANDIDATE,
                    "conclusion": "success",
                },
            ]
        },
    )

    observed = github.readback_ci_run("acme/host", "ci.yml", _CANDIDATE)

    assert observed["workflow"] == "ci.yml"
    assert observed["workflow_name"] == "CI"
    assert observed["run_id"] == 2


def test_readback_missing_or_malformed_api_payload_fails_closed(monkeypatch):
    """No run and a non-object API response both use closed paths."""
    monkeypatch.setenv("GITHUB_TOKEN", "token")

    monkeypatch.setattr(github, "_get_any", lambda _request: {"workflow_runs": []})
    missing = github.readback_ci_run("acme/host", "ci.yml", _CANDIDATE)
    assert missing["reason"] == "missing"
    assert missing["status"] == "failed"
    assert missing["api_verified"] is False

    monkeypatch.setattr(github, "_get_any", lambda _request: ["malformed"])
    malformed = github.readback_ci_run("acme/host", "ci.yml", _CANDIDATE)
    assert malformed["reason"] == "malformed"
    assert malformed["status"] == "failed"
    assert malformed["api_verified"] is False


# AC-FR0270-02@v0.8 TRACKS-TRACE IF-VERIFY-004 required checks from CI jobs
def test_readback_uses_real_required_check_results_and_rejects_missing_check(monkeypatch):
    """Required checks come from the provider response for the selected run;
    a missing declared check cannot be treated as success."""
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    requests: list[str] = []

    def _get_any(request):
        url = request.full_url
        requests.append(url)
        if "/jobs" in url:
            return {"jobs": []}
        return {
            "workflow_runs": [
                {
                    "id": 23,
                    "name": "CI",
                    "path": ".github/workflows/ci.yml",
                    "head_sha": _CANDIDATE,
                    "status": "completed",
                    "conclusion": "success",
                }
            ]
        }

    monkeypatch.setattr(github, "_get_any", _get_any)
    observed = github.readback_ci_run("acme/host", "ci.yml", _CANDIDATE)
    verdict = github.judge_ci_binding(observed, _CANDIDATE, ["lint", "test"])

    assert any("/jobs" in url for url in requests)
    assert observed["checks"] == {}
    assert verdict["status"] == "failed"
    assert verdict["reason"] == "check_failed"
    assert verdict["api_verified"] is False


def test_jobs_total_count_and_duplicate_names_fail_closed(monkeypatch):
    """A short first page is incomplete when total_count says more jobs
    exist; duplicate names cannot last-write into a false green."""
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    requests: list[str] = []

    def _get_any(request):
        url = request.full_url
        requests.append(url)
        if "/jobs" in url:
            if "page=1" in url:
                return {
                    "total_count": 2,
                    "jobs": [{"name": "lint", "conclusion": "failure"}],
                }
            return {
                "total_count": 2,
                "jobs": [{"name": "lint", "conclusion": "success"}],
            }
        return {
            "workflow_runs": [
                {
                    "id": 24,
                    "name": "CI",
                    "path": ".github/workflows/ci.yml",
                    "head_sha": _CANDIDATE,
                    "status": "completed",
                    "conclusion": "success",
                }
            ]
        }

    monkeypatch.setattr(github, "_get_any", _get_any)
    observed = github.readback_ci_run("acme/host", "ci.yml", _CANDIDATE)

    assert any("page=2" in url for url in requests)
    assert observed["status"] == "failed"
    assert observed["reason"] == "jobs_incomplete"
    assert observed["api_verified"] is False


# AC-FR0270-03@v0.8 TRACKS-TRACE IF-VERIFY-004 fake agent cannot fake CI
def test_fake_agent_channel_still_uses_ci_transport(monkeypatch):
    """Selecting a fake agent backend does not authorize a synthetic
    api_verified CI observation."""
    monkeypatch.setenv("TRAC_AGENT_BACKEND", "fake")
    monkeypatch.setenv("TRAC_GITHUB_REPO", "acme/host")
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    calls: list[tuple[str, str, str]] = []

    def _transport(repo_id, workflow, candidate_sha):
        calls.append((repo_id, workflow, candidate_sha))
        return {
            "repo": repo_id,
            "workflow": workflow,
            "run_id": 41,
            "head_sha": candidate_sha,
            "conclusion": "success",
            "checks": {"lint": "success"},
        }

    monkeypatch.setattr("tracks.executor.executor.readback_ci_run", _transport)

    class _Runtime:
        repo = Path(".")

        @staticmethod
        def _is_fake_channel():
            return True

        @staticmethod
        def _emit(*_args, **_kwargs):
            return None

    result = Executor._readback_ci_binding(
        _Runtime(),
        SimpleNamespace(command_id="C-CI"),
        _CANDIDATE,
        {
            "repo_env": "TRAC_GITHUB_REPO",
            "workflow": "ci.yml",
            "required_checks": ["lint"],
        },
    )

    assert calls == [("acme/host", "ci.yml", _CANDIDATE)]
    assert result["api_verified"] is True
    assert result["run_id"] == 41


# AC-FR0270-02@v0.8 TRACKS-TRACE IF-VERIFY-004 reconcile retries same candidate
@pytest.mark.parametrize(
    "prior_candidate,prior_status",
    [(_CANDIDATE, "failed"), (_FOREIGN, "passed")],
)
def test_reconcile_does_not_skip_retry_for_failed_or_foreign_ci_observation(
    tmp_path, monkeypatch, prior_candidate, prior_status
):
    """Persisted CI evidence is reusable only when it is a successful
    observation for the command's candidate; failed/foreign evidence cannot
    suppress the transport retry."""
    repo = git_repo(tmp_path, gitignore=True)
    store = Store(repo / ".tracks")
    store.append(
        "RUN",
        "v0.8",
        "ci.run_observed",
        {
            "candidate_sha": prior_candidate,
            "head_sha": prior_candidate,
            "status": prior_status,
            "api_verified": prior_status == "passed",
        },
    )
    executor = Executor(store, repo, "RUN")
    calls: list[str] = []
    monkeypatch.setattr(
        executor,
        "_load_or_default_contract",
        lambda _cmd, _candidate: (
            SimpleNamespace(ci={"repo_env": "TRAC_GITHUB_REPO", "workflow": "ci.yml"}),
            "digest",
            "source",
        ),
    )
    monkeypatch.setattr(
        executor,
        "_readback_ci_binding",
        lambda _cmd, candidate, _ci: calls.append(candidate)
        or {
            "candidate_sha": candidate,
            "repo": "acme/host",
            "workflow": "ci.yml",
            "run_id": 42,
            "head_sha": candidate,
            "conclusion": "success",
            "required_checks": [],
            "status": "passed",
            "api_verified": True,
        },
    )
    monkeypatch.setattr(executor, "issue", lambda _command: None)

    executor._do_observe_ci_runs(
        Command(
            "observe_ci_runs",
            params={"candidate_sha": _CANDIDATE},
            command_id="C-RETRY",
        ),
        store.state("RUN"),
        None,
        True,
    )

    assert calls == [_CANDIDATE]
    observed = [event for event in store.events("RUN") if event.type == "ci.run_observed"]
    assert observed[-1].payload["candidate_sha"] == _CANDIDATE
    assert observed[-1].payload["status"] == "passed"
    store.close()


def test_reconcile_reuses_same_command_and_workflow_id_binding(tmp_path, monkeypatch):
    """A successful workflow-id binding is reusable on a fresh Executor."""
    repo = git_repo(tmp_path, gitignore=True)
    store = Store(repo / ".tracks")
    store.append(
        "RUN",
        "v0.8",
        "ci.run_observed",
        {
            "candidate_sha": _CANDIDATE,
            "head_sha": _CANDIDATE,
            "repo": "acme/host",
            "workflow": "123",
            "workflow_path": ".github/workflows/ci.yml",
            "workflow_id": 123,
            "workflow_name": "Build and verify",
            "run_id": 77,
            "checks": {"lint": "success"},
            "required_checks": ["lint"],
            "status": "passed",
            "reason": "bound",
            "api_verified": True,
        },
        command_id="C-WF",
    )
    executor = Executor(store, repo, "RUN")
    calls: list[tuple[str, str, str]] = []
    monkeypatch.setenv("TRAC_CI_REPO", "acme/host")
    monkeypatch.setattr(
        executor,
        "_load_or_default_contract",
        lambda _cmd, _candidate: (
            SimpleNamespace(
                ci={
                    "repo_env": "TRAC_CI_REPO",
                    "workflow": "123",
                    "required_checks": ["lint"],
                }
            ),
            "digest",
            "source",
        ),
    )
    monkeypatch.setattr(
        "tracks.executor.executor.readback_ci_run",
        lambda *args: calls.append(args) or {},
    )

    executor._do_observe_ci_runs(
        Command(
            "observe_ci_runs",
            params={"candidate_sha": _CANDIDATE},
            command_id="C-WF",
        ),
        store.state("RUN"),
        None,
        True,
    )

    assert calls == []
    store.close()


def _ci_status_events(*events):
    from tracks.cli.main import _release_status_lines

    return _release_status_lines(
        [SimpleNamespace(seq=seq, type=kind, payload=payload) for seq, kind, payload in events],
        SimpleNamespace(status="active", terminal_state=None),
    )


def test_cli_ci_attention_clears_after_same_candidate_success():
    candidate = _CANDIDATE
    lines = _ci_status_events(
        (1, "attention.required", {"area": "ci_readback", "reason": "missing_token", "candidate_sha": candidate, "next": "set token"}),
        (2, "ci.run_observed", {"candidate_sha": candidate, "head_sha": candidate, "status": "passed", "api_verified": True, "reason": "bound"}),
    )

    assert not any(line.startswith("needs_attention=") for line in lines)


def test_cli_ci_attention_keeps_failure_after_earlier_success():
    candidate = _CANDIDATE
    lines = _ci_status_events(
        (1, "ci.run_observed", {"candidate_sha": candidate, "head_sha": candidate, "status": "passed", "api_verified": True, "reason": "bound"}),
        (2, "attention.required", {"area": "ci_readback", "reason": "network_error", "candidate_sha": candidate, "next": "retry CI"}),
    )

    assert any(line.startswith("needs_attention=network_error next=retry CI") for line in lines)


def test_cli_foreign_success_does_not_clear_current_ci_attention():
    candidate = _CANDIDATE
    lines = _ci_status_events(
        (1, "attention.required", {"area": "ci_readback", "reason": "missing_token", "candidate_sha": candidate, "next": "set token"}),
        (2, "ci.run_observed", {"candidate_sha": _FOREIGN, "head_sha": _FOREIGN, "status": "passed", "api_verified": True, "reason": "bound"}),
    )

    assert any(line.startswith("needs_attention=missing_token next=set token") for line in lines)
