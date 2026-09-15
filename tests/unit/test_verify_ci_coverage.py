"""Behavior coverage for the M-VERIFY CI observation chain.

Drives ``ci.run_observed`` reuse/reconcile guards, the candidate-bound
readback attention/binding failure faces, evidence digests, the
M-VERIFY->M-SECURITY->M-RELEASE advance and the release-preview reload
refusal (IF-VERIFY-004/005, AC-FR0270-01..03).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from tracks.effects.github import GithubIssuesError
from tracks.executor import verify_ci
from tracks.executor.verify_ci import ExecVerifyCiMixin
from tracks.kernel.events import Command


class _FakeStore:
    def __init__(self, events=()):
        self._events = list(events)

    def events(self, run_id):
        return list(self._events)


class _Host(ExecVerifyCiMixin):
    def __init__(self, tmp_path: Path, *, events=()):
        self.repo = tmp_path / "repo"
        self.repo.mkdir(parents=True, exist_ok=True)
        self.run_id = "RUN"
        self.store = _FakeStore(events)
        self.emitted: list[tuple] = []
        self.issued: list[tuple] = []
        self.calls: dict = {}

    def _emit(self, event, payload=None, **kwargs):
        self.emitted.append((event, payload, kwargs))

    def issue(self, cmd, command_id=None):
        self.issued.append((cmd, command_id))

    def _latest_event(self, event_type):
        hits = [e for e in self.store.events(self.run_id) if e.type == event_type]
        return hits[-1] if hits else None

    def _load_or_default_contract(self, cmd, sha):
        return self.calls.get(
            "contract", (SimpleNamespace(ci={}), "DIGEST", "source")
        )

    def _park_preview(self, cmd, sha, contract, digest=None):
        self.calls.setdefault("preview", []).append((sha, contract, digest))


def _ev(seq, type, payload=None, command_id=None):
    return SimpleNamespace(seq=seq, type=type, payload=payload or {}, command_id=command_id)


def _cmd(command_id="C-1", **params):
    return Command(kind="observe_ci_runs", params=dict(params), command_id=command_id)


# ---------------------------------------------------------------------------
# _do_observe_ci_runs
# ---------------------------------------------------------------------------


def test_observe_ci_runs_contract_none_returns(tmp_path):
    host = _Host(tmp_path)
    host.calls["contract"] = (None, None, None)
    host._do_observe_ci_runs(_cmd(), SimpleNamespace(), "T", reconcile=False)
    assert host.emitted == []


def test_observe_ci_runs_reconcile_reuse_returns(tmp_path):
    host = _Host(tmp_path)
    host._ci_observation_reusable = lambda *a: True
    host._do_observe_ci_runs(_cmd(), SimpleNamespace(), "T", reconcile=True)
    assert host.emitted == []


def test_observe_ci_runs_readback_none_returns(tmp_path):
    host = _Host(tmp_path)
    host.calls["contract"] = (SimpleNamespace(ci={}), "D", "src")
    host._readback_ci_binding = lambda *a: None
    host._do_observe_ci_runs(_cmd(), SimpleNamespace(), "T", reconcile=False)
    assert host.emitted == []


def test_observe_ci_runs_success_emits_and_issues_final(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    host.calls["contract"] = (SimpleNamespace(ci={}), "D", "src")
    host._readback_ci_binding = lambda *a: {"status": "passed"}
    monkeypatch.setattr(
        verify_ci.m_verify,
        "build_prism_final_review_assignment",
        lambda sha, digests: {"kind": "VERIFY_FINAL", "candidate_sha": sha},
    )
    host._do_observe_ci_runs(
        _cmd(candidate_sha="S"), SimpleNamespace(ci={}), "T", reconcile=False
    )
    assert host.emitted[-1][0] == "ci.run_observed"
    issued, _ = host.issued[0]
    assert issued.kind == "dispatch_agent"
    assert issued.params["assignment"]["kind"] == "VERIFY_FINAL"


# ---------------------------------------------------------------------------
# _ci_observation_reusable
# ---------------------------------------------------------------------------


def test_ci_observation_reusable_no_previous(tmp_path):
    host = _Host(tmp_path)
    assert host._ci_observation_reusable(_cmd(), "S", {}) is False


def test_ci_observation_reusable_full_match(tmp_path, monkeypatch):
    monkeypatch.setenv("TRAC_CI_REPO", "o/r")
    payload = {
        "candidate_sha": "S",
        "head_sha": "S",
        "status": "passed",
        "api_verified": True,
        "repo": "o/r",
        "run_id": 5,
        "workflow": "ci.yml",
        "required_checks": ["unit"],
        "checks": {"unit": "success"},
    }
    events = [_ev(1, "ci.run_observed", payload, command_id="C-1")]
    host = _Host(tmp_path, events=events)
    ci = {"repo_env": "TRAC_CI_REPO", "workflow": "ci.yml", "required_checks": ["unit"]}
    assert host._ci_observation_reusable(_cmd(), "S", ci) is True

    payload["checks"] = {"unit": "failure"}
    assert host._ci_observation_reusable(_cmd(), "S", ci) is False


# ---------------------------------------------------------------------------
# _readback_ci_binding
# ---------------------------------------------------------------------------


def test_readback_binding_missing_target(tmp_path):
    host = _Host(tmp_path)
    assert host._readback_ci_binding(_cmd(), "S", {}) is None
    assert host.emitted[-1][0] == "attention.required"
    assert host.emitted[-1][1]["reason"] == "missing_token"


def test_readback_binding_api_error_and_transport_error(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setenv("TRAC_CI_REPO", "o/r")
    monkeypatch.setattr(
        verify_ci,
        "readback_ci_run",
        lambda *a: (_ for _ in ()).throw(GithubIssuesError("network", "down")),
    )
    assert host._readback_ci_binding(
        _cmd(), "S", {"repo_env": "TRAC_CI_REPO", "workflow": "ci.yml"}
    ) is None
    assert host.emitted[-1][1]["reason"] == "network"
    assert "resolve the CI API error" in host.emitted[-1][1]["next"]

    monkeypatch.setattr(
        verify_ci,
        "readback_ci_run",
        lambda *a: (_ for _ in ()).throw(OSError("boom")),
    )
    assert host._readback_ci_binding(
        _cmd(), "S", {"repo_env": "TRAC_CI_REPO", "workflow": "ci.yml"}
    ) is None
    assert host.emitted[-1][1]["reason"] == "network_error"


def test_readback_binding_missing_token_next_hint(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setenv("TRAC_CI_REPO", "o/r")
    monkeypatch.setattr(
        verify_ci,
        "readback_ci_run",
        lambda *a: (_ for _ in ()).throw(GithubIssuesError("missing_token", "no token")),
    )
    host._readback_ci_binding(_cmd(), "S", {"repo_env": "TRAC_CI_REPO", "workflow": "ci.yml"})
    assert "set the CI token" in host.emitted[-1][1]["next"]


def test_readback_binding_failed_judgment(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setenv("TRAC_CI_REPO", "o/r")
    observed = {"head_sha": "OTHER", "checks": {}, "run_id": 3, "workflow_name": "CI"}
    monkeypatch.setattr(verify_ci, "readback_ci_run", lambda *a: observed)
    monkeypatch.setattr(
        verify_ci,
        "judge_ci_binding",
        lambda *a: {"status": "failed", "reason": "mismatch", "api_verified": False},
    )
    assert host._readback_ci_binding(
        _cmd(), "S", {"repo_env": "TRAC_CI_REPO", "workflow": "ci.yml", "required_checks": ["unit"]}
    ) is None
    types = [e[0] for e in host.emitted]
    assert types == ["ci.run_observed", "attention.required"]
    assert host.emitted[0][1]["reason"] == "mismatch"
    assert host.emitted[0][1]["status"] == "failed"
    assert host.emitted[1][1]["reason"] == "ci_binding_blocked"


def test_readback_binding_passed_payload(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setenv("TRAC_CI_REPO", "o/r")
    observed = {"head_sha": "S", "checks": {"unit": "success"}, "run_id": 4}
    monkeypatch.setattr(verify_ci, "readback_ci_run", lambda *a: observed)
    monkeypatch.setattr(
        verify_ci,
        "judge_ci_binding",
        lambda *a: {"status": "passed", "api_verified": True},
    )
    payload = host._readback_ci_binding(
        _cmd(), "S", {"repo_env": "TRAC_CI_REPO", "workflow": "ci.yml", "required_checks": ["unit"]}
    )
    assert payload["status"] == "passed"
    assert payload["api_verified"] is True
    assert payload["required_checks"] == ["unit"]


# ---------------------------------------------------------------------------
# digests / advance / preview
# ---------------------------------------------------------------------------


def test_verify_evidence_digests(tmp_path):
    events = [
        _ev(1, "local_gate.passed", {"kind": "unit", "contract_digest": "d1"}),
        _ev(2, "other", {}),
        _ev(3, "local_gate.passed", {"kind": "lint"}),
    ]
    host = _Host(tmp_path, events=events)
    assert host._verify_evidence_digests() == {"unit": "d1", "lint": None}


def test_advance_verify_chain_blocked_without_security(tmp_path):
    host = _Host(tmp_path)
    host._advance_verify_chain(_cmd(), "S")
    assert [e[0] for e in host.emitted] == ["stage.exited", "stage.entered"]
    assert host.emitted[1][1]["stage"] == "M-SECURITY"
    assert host.issued[0][0].kind == "assess_security"
    assert "preview" not in host.calls


def test_advance_verify_chain_assessed_but_failed(tmp_path):
    events = [_ev(1, "security.assessed", {"status": "failed"})]
    host = _Host(tmp_path, events=events)
    host._advance_verify_chain(_cmd(), "S")
    assert len(host.emitted) == 2


def test_advance_verify_chain_green_reaches_release(tmp_path):
    events = [_ev(1, "security.assessed", {"status": "passed", "candidate_sha": "S"})]
    host = _Host(tmp_path, events=events)
    host._advance_verify_chain(_cmd(), "S")
    assert [e[1]["stage"] for e in host.emitted if e[0] == "stage.entered"] == [
        "M-SECURITY",
        "M-RELEASE",
    ]
    preview = host.calls["preview"][0]
    assert preview[0] == "S" and preview[2] == "DIGEST"


def test_advance_after_security_ignores_stale_candidate(tmp_path):
    """A passing assessment bound to an OLDER candidate never advances the
    chain -- the re-driven assessment owns the current candidate."""
    events = [_ev(1, "security.assessed", {"status": "passed", "candidate_sha": "OLD"})]
    host = _Host(tmp_path, events=events)
    host._advance_after_security(_cmd(), "NEW")
    assert [e[1]["stage"] for e in host.emitted if e[0] == "stage.entered"] == []
    assert "preview" not in host.calls


def test_release_preview_contract_refusal(tmp_path):
    host = _Host(tmp_path)
    host.calls["contract"] = (None, None, None)
    host._release_preview(_cmd(), "S")
    assert "preview" not in host.calls
