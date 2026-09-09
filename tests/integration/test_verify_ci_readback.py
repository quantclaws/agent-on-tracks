"""Integration: required CI API readback (FR-0270, IF-VERIFY-004).

This file owns the smallest deterministic CI readback slice. The server is a
loopback GitHub REST stand-in: no real credentials or external network are
used. Each Runtime scenario starts at an explicit pre-CI M-VERIFY boundary,
executes the production command handler, then checks CLI status and replay.
This isolates CI acceptance; it does not replace the full release journey.
"""

from __future__ import annotations

import hashlib
import json
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from tracks import paths
from tracks.effects.github import judge_ci_binding, readback_ci_run
from tracks.executor.executor import Executor
from tracks.executor.m_verify import collect_binding_violations, freeze_candidate
from tracks.kernel.events import Command
from tracks.store import Store

pytestmark = pytest.mark.integration

_CANDIDATE = "a" * 40
_FOREIGN = "b" * 40
_WORKFLOW = "123"
_REPO = "acme/host"
_REQUIRED = ["required-ci"]


class _CiStandIn:
    """Minimal loopback subset of GitHub workflow-runs and jobs endpoints."""

    def __init__(self):
        self.runs: list[dict] = []
        self.jobs: dict[str, dict] = {}
        self.requests: list[str] = []
        self.expected_head_sha: str | None = None
        self.workflow_id = int(_WORKFLOW)
        self.workflow_name = _WORKFLOW
        self._lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):  # pragma: no cover - server noise
                return

            def _reply(self, status: int, body: dict):
                raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler protocol
                parsed = urlparse(self.path)
                with owner._lock:
                    owner.requests.append(self.path)
                    runs = list(owner.runs)
                    jobs = dict(owner.jobs)
                prefix = f"/repos/{_REPO}/actions/"
                if parsed.path == f"{prefix}workflows/{_WORKFLOW}/runs":
                    query = parse_qs(parsed.query)
                    if query.get("head_sha") != [owner.expected_head_sha]:
                        self._reply(200, {"total_count": 0, "workflow_runs": []})
                    else:
                        self._reply(200, {"total_count": len(runs), "workflow_runs": runs})
                    return
                if parsed.path == f"{prefix}runs":
                    # Either endpoint is legitimate when the returned
                    # workflow identity is actually checked.
                    self._reply(200, {"total_count": len(runs), "workflow_runs": runs})
                    return
                if parsed.path.startswith(f"{prefix}runs/") and parsed.path.endswith("/jobs"):
                    run_id = parsed.path.split("/")[-2]
                    self._reply(200, jobs.get(run_id, {"jobs": []}))
                    return
                self._reply(404, {"message": "not found"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def start(self):
        self._thread.start()
        return self

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self._thread.join(timeout=5)

    def configure(
        self,
        *,
        head_sha: str | None,
        checks: dict[str, str] | None = None,
        workflow_id: int | None = None,
        workflow_name: str | None = None,
        requested_sha: str | None = None,
    ):
        self.expected_head_sha = requested_sha or head_sha or _CANDIDATE
        self.workflow_id = workflow_id if workflow_id is not None else int(_WORKFLOW)
        self.workflow_name = workflow_name if workflow_name is not None else "Build and verify"
        self.runs = [] if head_sha is None else [
            {
                "id": 17,
                "name": self.workflow_name,
                "workflow_id": self.workflow_id,
                "path": ".github/workflows/ci.yml",
                "head_sha": head_sha,
                "status": "completed",
                "conclusion": "success",
            }
        ]
        self.jobs = {
            "17": {
                "total_count": len(checks or {}),
                "jobs": [
                    {"id": index, "run_id": 17, "name": name,
                     "head_sha": head_sha, "status": "completed", "conclusion": result}
                    for index, (name, result) in enumerate((checks or {}).items(), 1)
                ]
            }
        }


@pytest.fixture
def ci_standin():
    server = _CiStandIn().start()
    try:
        yield server
    finally:
        server.close()


def _set_ci_env(monkeypatch, standin: _CiStandIn) -> None:
    # Fake agents reduce cost; they must never fabricate CI API evidence.
    monkeypatch.setenv("TRAC_AGENT_BACKEND", "fake")
    monkeypatch.setenv("TRAC_GITHUB_API_BASE", standin.base_url)
    monkeypatch.setenv("TRAC_CI_REPO", _REPO)
    monkeypatch.setenv("GITHUB_TOKEN", "loopback-token")


def _clear_ci_env(monkeypatch) -> None:
    for key in ("TRAC_GITHUB_API_BASE", "TRAC_GITHUB_REPO", "TRAC_CI_REPO", "GITHUB_TOKEN"):
        monkeypatch.delenv(key, raising=False)


def _seed_verify_boundary(host_repo, trac) -> tuple[str, str]:
    """Create a contract before freezing, then append legal M-VERIFY facts."""
    assert trac("init").returncode == 0
    assert trac("start", "v0.8", stdin="CI readback boundary").returncode == 0
    contract = host_repo / ".tracks" / "projects" / "project.toml"
    contract.parent.mkdir(parents=True, exist_ok=True)
    contract.write_text(
        """[host-contract]\nversion = 1\nlanguage = \"runtime-default\"\ntoolchain = \"runtime-default\"\ninstall = \"true\"\n[host-contract.ci]\nrepo_env = \"TRAC_CI_REPO\"\nworkflow = \"123\"\nrequired_checks = [\"required-ci\"]\n""",
        encoding="utf-8",
    )
    for args in (
        ["add", ".gitignore"],
        ["add", "-f", ".tracks/projects/project.toml"],
        ["commit", "--only", "-m", "CI acceptance premises", "--",
         ".gitignore", ".tracks/projects/project.toml"],
    ):
        subprocess.run(["git", *args], cwd=host_repo, check=True, capture_output=True)
    identity = freeze_candidate(host_repo)
    candidate_sha = identity.candidate_sha
    store = Store(paths.tracks_home(host_repo))
    try:
        run_id = store.active_run()
        assert run_id
        version = store.state(run_id).version
        store.append(run_id, version, "stage.entered", {"stage": "M-VERIFY"})
        store.append(
            run_id,
            version,
            "candidate.frozen",
            {"candidate_sha": candidate_sha, "clean_tree": True,
             "branch": identity.branch, "frozen_at_seq": len(list(store.events(run_id))) + 1},
        )
        store.append(
            run_id,
            version,
            "local_gate.passed",
            {
                "kind": "quality",
                "candidate_sha": candidate_sha,
                "contract_digest": hashlib.sha256(contract.read_bytes()).hexdigest(),
                "normalized_result": {"schema": "tracks-gate-result", "version": 1, "status": "passed"},
            },
        )
        return run_id, candidate_sha
    finally:
        store.close()


def _observe_via_executor(host_repo, run_id: str, candidate_sha: str) -> None:
    """Run the production observe_ci_runs handler through its public command WAL."""
    store = Store(paths.tracks_home(host_repo))
    try:
        Executor(store, host_repo, run_id).issue(
            Command(kind="observe_ci_runs", params={"candidate_sha": candidate_sha})
        )
    finally:
        store.close()


def _reconcile_observe_via_executor(host_repo, run_id: str, candidate_sha: str) -> None:
    """Replay the exact issued command through a fresh Executor instance."""
    store = Store(paths.tracks_home(host_repo))
    try:
        issued = [
            event
            for event in store.events(run_id)
            if event.type == "command.issued"
            and (event.payload or {}).get("command", {}).get("kind") == "observe_ci_runs"
        ][-1]
        command = (issued.payload or {})["command"]
        Executor(store, host_repo, run_id)._execute(
            Command(
                kind=command["kind"],
                params=command.get("params", {}),
                command_id=command.get("command_id"),
            ),
            store.state(run_id),
            None,
            reconcile=True,
        )
    finally:
        store.close()


# AC-FR0270-01@v0.8 TRACKS-TRACE api readback binds candidate with four-tuple
def test_api_readback_binds_candidate(host_repo, trac, event_log, monkeypatch, ci_standin):
    run_id, candidate_sha = _seed_verify_boundary(host_repo, trac)
    assert len(candidate_sha) == 40
    ci_standin.configure(head_sha=candidate_sha, checks={"required-ci": "success"})
    _set_ci_env(monkeypatch, ci_standin)
    _observe_via_executor(host_repo, run_id, candidate_sha)
    events = event_log(run_id)
    observed_event = [e for e in events if e["type"] == "ci.run_observed"]
    assert observed_event, "observe_ci_runs must emit ci.run_observed"
    observed = observed_event[-1]["payload"]

    assert any(
        "/actions/" in path and "/runs?" in path and f"head_sha={candidate_sha}" in path
        for path in ci_standin.requests
    ), f"CI readback was not requested with the candidate head: {ci_standin.requests!r}"
    assert any(urlparse(path).path.endswith("/actions/runs/17/jobs") for path in ci_standin.requests)
    assert observed["repo"] == _REPO
    assert observed["workflow"] == _WORKFLOW
    assert observed["run_id"] == 17
    assert observed["head_sha"] == candidate_sha
    assert observed["candidate_sha"] == candidate_sha
    assert observed["api_verified"] is True
    assert observed["required_checks"] == _REQUIRED
    assert observed["status"] == "passed"
    assert observed["reason"] == "bound"
    final_dispatch = [e for e in events if e["type"] == "command.issued"
                      and e["payload"].get("command", {}).get("params", {}).get("substate") == "VERIFY_FINAL"]
    assert final_dispatch and final_dispatch[-1]["payload"]["command"]["params"]["candidate_sha"] == candidate_sha
    status = trac("status")
    replay = trac("replay")
    assert status.returncode == 0 and "ci=bound" in status.stdout
    assert replay.returncode == 0 and "ci.run_observed" in replay.stdout
    _clear_ci_env(monkeypatch)


# AC-FR0270-02@v0.8 TRACKS-TRACE mismatch missing stale blocks and not referenced
def test_mismatch_missing_stale_blocks(host_repo, trac, event_log, monkeypatch, ci_standin):
    chain = [
        {"kind": "candidate.frozen", "candidate_sha": _CANDIDATE},
        {"kind": "ci.run_observed", "candidate_sha": _FOREIGN},
    ]
    violations = collect_binding_violations(chain, _CANDIDATE)
    assert len(violations) == 1 and "ci.run_observed" in str(violations[0])

    _set_ci_env(monkeypatch, ci_standin)
    run_id, candidate_sha = _seed_verify_boundary(host_repo, trac)
    scenarios = (
        ("foreign_head", _FOREIGN, {"mismatch", "stale"}),
        ("missing_head", None, {"missing"}),
        ("no_run", None, {"missing"}),
        ("foreign_workflow", candidate_sha, {"mismatch", "missing"}),
    )
    for name, head_sha, expected_reason in scenarios:
        ci_standin.configure(head_sha=head_sha, requested_sha=candidate_sha,
                             checks={"required-ci": "success"})
        if name == "missing_head":
            # A run without head_sha must remain missing; the implementation
            # must never fill it with the requested candidate.
            ci_standin.runs = [{"id": 17, "name": "CI", "workflow_id": 123, "conclusion": "success"}]
        if name == "foreign_workflow":
            ci_standin.configure(
                head_sha=candidate_sha,
                checks={"required-ci": "success"},
                workflow_id=999,
                workflow_name="999",
                requested_sha=candidate_sha,
            )
        before = len([e for e in event_log(run_id) if e["type"] == "ci.run_observed"])
        request_count = len(ci_standin.requests)
        _observe_via_executor(host_repo, run_id, candidate_sha)
        observed_events = [e for e in event_log(run_id) if e["type"] == "ci.run_observed"]
        assert len(observed_events) == before + 1, f"{name}: missing fresh failure evidence"
        assert len(ci_standin.requests) > request_count, f"{name}: no API readback"
        observed = observed_events[-1]["payload"]
        assert observed["status"] == "failed", f"{name} unexpectedly passed: {observed!r}"
        assert observed["reason"] in expected_reason
        assert observed["api_verified"] is False
        assert observed.get("candidate_sha") == candidate_sha
        if name == "missing_head":
            assert observed.get("head_sha") in (None, ""), observed
        status = trac("status")
        assert status.returncode == 0 and f"ci={observed['reason']}" in status.stdout
        assert "blocked" in status.stdout.lower()
        assert not any(e["type"] in {"release.previewed", "publish.started"}
                       for e in event_log(run_id))
    status = trac("status")
    replay = trac("replay")
    assert replay.returncode == 0 and "ci.run_observed" in replay.stdout
    _clear_ci_env(monkeypatch)


# AC-FR0270-03@v0.8 TRACKS-TRACE missing credentials needs_attention with resume
def test_missing_credentials_needs_attention(host_repo, trac, event_log, monkeypatch, ci_standin):
    violations = collect_binding_violations([{"kind": "ci.run_observed"}], _CANDIDATE)
    assert len(violations) == 1 and "ci.run_observed" in str(violations[0])
    _clear_ci_env(monkeypatch)
    run_id, candidate_sha = _seed_verify_boundary(host_repo, trac)
    monkeypatch.setenv("TRAC_CI_REPO", _REPO)
    monkeypatch.setenv("TRAC_GITHUB_API_BASE", ci_standin.base_url)
    _observe_via_executor(host_repo, run_id, candidate_sha)
    with ci_standin._lock:
        assert ci_standin.requests == []
    events = event_log(run_id)
    attention = [e for e in events if e["type"] == "attention.required"]
    assert attention and attention[-1]["payload"].get("area") == "ci_readback"
    assert attention[-1]["payload"]["reason"] == "missing_token"
    assert attention[-1]["payload"].get("next")
    assert not any(e["type"] == "ci.run_observed" and e["payload"].get("api_verified") for e in events)
    status = trac("status")
    replay = trac("replay")
    assert status.returncode == 0 and "needs_attention" in status.stdout and "missing_token" in status.stdout
    assert replay.returncode == 0 and "attention.required" in replay.stdout

    # Restore the real target and issue the same production command again;
    # the attention path must be recoverable and may only become bound after
    # a genuine readback succeeds.
    ci_standin.configure(head_sha=candidate_sha, checks={"required-ci": "success"})
    _set_ci_env(monkeypatch, ci_standin)
    _observe_via_executor(host_repo, run_id, candidate_sha)
    assert ci_standin.requests, "restored credentials must cause a real API readback"
    recovered = [
        e for e in event_log(run_id)
        if e["type"] == "ci.run_observed" and e["payload"].get("api_verified") is True
    ]
    assert recovered and recovered[-1]["payload"].get("status") == "passed"
    recovered_status = trac("status")
    assert recovered_status.returncode == 0 and "ci=bound" in recovered_status.stdout
    assert "needs_attention=missing_token" not in recovered_status.stdout


# AC-FR0270-02@v0.8 TRACKS-TRACE declared required checks are sourced from jobs
def test_declared_required_check_missing_fails_closed(monkeypatch, ci_standin):
    _set_ci_env(monkeypatch, ci_standin)
    ci_standin.configure(head_sha=_CANDIDATE, checks={"optional": "success"})
    observed = readback_ci_run(_REPO, _WORKFLOW, _CANDIDATE)
    decision = judge_ci_binding(observed, _CANDIDATE, _REQUIRED)
    assert decision["status"] == "failed"
    assert decision["reason"] == "check_failed"
    assert decision["api_verified"] is False
    assert observed.get("checks", {}).get("required-ci") != "success"


# AC-FR0270-01@v0.8 TRACKS-TRACE replay preserves one bound observation
def test_api_readback_replay_does_not_repeat_api(host_repo, trac, event_log, monkeypatch, ci_standin):
    """A persisted bound result is replayed without a second API read."""
    run_id, candidate_sha = _seed_verify_boundary(host_repo, trac)
    ci_standin.configure(head_sha=candidate_sha, checks={"required-ci": "success"})
    _set_ci_env(monkeypatch, ci_standin)
    _observe_via_executor(host_repo, run_id, candidate_sha)
    events_before = event_log(run_id)
    bound = [e for e in events_before if e["type"] == "ci.run_observed" and e["payload"].get("api_verified")]
    assert bound, "the first observe must persist a bound CI result"
    requests_before = len(ci_standin.requests)
    assert requests_before >= 2, "initial binding must read runs and jobs over HTTP"
    _reconcile_observe_via_executor(host_repo, run_id, candidate_sha)
    assert len(ci_standin.requests) == requests_before
    events_after = event_log(run_id)
    assert len([e for e in events_after if e["type"] == "ci.run_observed"]) == len(bound)


# AC-FR0270-03@v0.8 TRACKS-TRACE unavailable CI transport fails with recovery instructions
def test_network_unavailable_needs_attention(host_repo, trac, event_log, monkeypatch, ci_standin):
    run_id, candidate_sha = _seed_verify_boundary(host_repo, trac)
    _set_ci_env(monkeypatch, ci_standin)
    # Reserve a local port without listening: connection refusal is deterministic
    # and cannot accidentally reach another process or an external service.
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        monkeypatch.setenv("TRAC_GITHUB_API_BASE", f"http://127.0.0.1:{reserved.getsockname()[1]}")
        _observe_via_executor(host_repo, run_id, candidate_sha)
    events = event_log(run_id)
    attention = [e for e in events if e["type"] == "attention.required"]
    assert attention and attention[-1]["payload"]["reason"] == "network_error"
    assert attention[-1]["payload"].get("next")
    assert not any(e["type"] == "ci.run_observed" and e["payload"].get("api_verified") for e in events)
    status = trac("status")
    assert status.returncode == 0
    assert "needs_attention" in status.stdout and "network_error" in status.stdout
