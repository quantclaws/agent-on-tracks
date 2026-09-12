"""AC-FR0272-02: release authorization must read back the security review."""

from types import SimpleNamespace

import pytest

from tests.unit.test_security_red import _contract, _scan
from tracks.executor.release_authorization import _security_status
from tracks.executor.security import security_policy_digest

_CONTRACT = _contract(_scan("scan"))
_POLICY = security_policy_digest(_CONTRACT)
_SCANS = [{"id": "scan", "status": "passed", "result_version": 1, "summary": {}, "exit_code": 0}]


def _event(kind, payload, seq, command_id=None):
    return SimpleNamespace(type=kind, payload=payload, seq=seq, command_id=command_id)


@pytest.mark.parametrize("change", ["missing", "candidate", "policy", "verdict", "scope", "command", "late"])
def test_passed_assessment_cannot_replace_its_bound_security_review(change):
    review = _event("prism.verdict", {
        "candidate_sha": "candidate", "policy_digest": _POLICY, "scope": "security", "verdict": "pass",
    }, 1, "review")
    assessment = _event("security.assessed", {
        "candidate_sha": "candidate", "policy_digest": _POLICY, "status": "passed",
        "review_command_id": "review", "contract_digest": "contract", "scans": _SCANS,
    }, 2)
    if change in ("candidate", "policy"):
        review.payload[change + ("_sha" if change == "candidate" else "_digest")] = "foreign"
    elif change == "verdict":
        review.payload["verdict"] = "revise"
    elif change == "scope":
        review.payload["scope"] = "verify_final"
    elif change == "command":
        review.command_id = "other"
    elif change == "late":
        review.seq = 3
    events = [assessment] if change == "missing" else [review, assessment]
    assert _security_status(events, "candidate", "contract", _CONTRACT)[1] is False


def test_bound_security_review_and_assessment_authorize():
    events = [
        _event("prism.verdict", {"candidate_sha": "candidate", "policy_digest": _POLICY,
                                "scope": "security", "verdict": "pass"}, 1, "review"),
        _event("security.assessed", {"candidate_sha": "candidate", "policy_digest": _POLICY,
                                    "status": "passed", "review_command_id": "review", "contract_digest": "contract", "scans": _SCANS}, 2),
    ]
    assert _security_status(events, "candidate", "contract", _CONTRACT) == ("passed", True)


@pytest.mark.parametrize("scans", [None, [], _SCANS * 2, [{**_SCANS[0], "id": "foreign"}],
                                  [{**_SCANS[0], "result_version": 2}]])
def test_review_pass_cannot_authorize_incomplete_scan_evidence(scans):
    events = [
        _event("prism.verdict", {"candidate_sha": "candidate", "policy_digest": _POLICY,
                                "scope": "security", "verdict": "pass"}, 1, "review"),
        _event("security.assessed", {"candidate_sha": "candidate", "policy_digest": _POLICY,
                                    "status": "passed", "review_command_id": "review",
                                    "contract_digest": "contract", "scans": scans}, 2),
    ]
    assert _security_status(events, "candidate", "contract", _CONTRACT) == ("failed", False)
