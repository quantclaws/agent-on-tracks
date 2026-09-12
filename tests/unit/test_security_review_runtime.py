"""AC-FR0272-01/02: scan success needs a bound Prism security review."""

from types import SimpleNamespace

import pytest

from tests.unit.test_security_red import _contract, _scan
from tracks.executor.executor import Executor
from tracks.kernel.events import Command
from tracks.store import Store


@pytest.mark.parametrize("status", ["done", "failed"])
def test_security_outcome_scope_survives_all_result_paths(status):
    from tracks.executor.helpers import _dispatch_payload

    payload = _dispatch_payload(None, {
        "role": "prism", "assignment": {"scope": "security"},
    }, {"status": status, "self_report": "security review"})
    assert payload["scope"] == "security"


@pytest.mark.parametrize("park", [False, True])
@pytest.mark.parametrize("reply, expected", [
    ({"verdict": "pass"}, "passed"),
    ({"verdict": "revise"}, "failed"),
    ({"verdict": "pass", "candidate_sha": "foreign"}, "unknown"),
    ({"verdict": "pass", "policy_digest": "foreign"}, "unknown"),
    (None, "unknown"),
])
def test_security_assessment_requires_bound_review(tmp_path, monkeypatch, park, reply, expected):
    store = Store(tmp_path / ".tracks")
    candidate = "a" * 40
    store.append("RUN", "v0.8", "candidate.frozen", {"candidate_sha": candidate})
    executor = object.__new__(Executor)
    executor.repo, executor.store = tmp_path, store
    executor.run_id, executor.version = "RUN", "v0.8"
    monkeypatch.setattr(executor, "_emit", lambda kind, payload, **kw: store.append(
        "RUN", "v0.8", kind, payload, command_id=kw.get("command_id")
    ))
    contract = _contract(_scan())
    monkeypatch.setattr(executor, "_load_or_default_contract", lambda *_: (contract, "policy", "test"))
    dispatched = []

    def issue(cmd, command_id=None):
        # Mirror the production issue() contract: the id is assigned on a NEW
        # Command (the caller's object is frozen); the outcome's verdict
        # binds that assigned id so the assessor can look it up.
        issued = Command(kind=cmd.kind, params=cmd.params, command_id=command_id)
        dispatched.append(issued)
        assert cmd.kind == "dispatch_agent"
        assert cmd.params["role"] == "prism"
        assert cmd.params["assignment"]["scope"] == "security"
        assert cmd.params["assignment"]["candidate_sha"] == candidate
        if reply is not None:
            executor._emit_dispatch_verdict(
                {"status": "done", **reply}, "prism",
                SimpleNamespace(stage="M-IMPL", substate="DIAGNOSE"),
                issued.params, issued, None,
            )

    monkeypatch.setattr(executor, "issue", issue)
    cmd = Command(kind="assess_security", params={"candidate_sha": candidate})
    if park:
        result = executor._park_assess_security(cmd, candidate, contract, "policy")
        assert result == (expected == "passed")
    else:
        executor._do_assess_security(cmd, SimpleNamespace(candidate_sha=candidate), None, False)
    assert len(dispatched) == 1, "scans alone must not complete the security assessment"
    assessed = [e.payload for e in store.events("RUN") if e.type == "security.assessed"]
    assert assessed[-1]["status"] == expected
    if expected == "passed":
        verdicts = [e for e in store.events("RUN") if e.type == "prism.verdict"]
        assert verdicts[-1].payload["scope"] == "security"
        assert verdicts[-1].payload["candidate_sha"] == candidate
        assert verdicts[-1].payload["policy_digest"] == "policy"
