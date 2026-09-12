"""AC-FR0272-02: release authorization must read back the security review."""

from types import SimpleNamespace

import pytest

from tracks.executor.release_authorization import _security_status


def _event(kind, payload, seq, command_id=None):
    return SimpleNamespace(type=kind, payload=payload, seq=seq, command_id=command_id)


@pytest.mark.parametrize("change", ["missing", "candidate", "policy", "verdict", "scope", "command", "late"])
def test_passed_assessment_cannot_replace_its_bound_security_review(change):
    review = _event("prism.verdict", {
        "candidate_sha": "candidate", "policy_digest": "policy", "scope": "security", "verdict": "pass",
    }, 1, "review")
    assessment = _event("security.assessed", {
        "candidate_sha": "candidate", "policy_digest": "policy", "status": "passed",
        "review_command_id": "review",
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
    assert _security_status(events, "candidate", "policy")[1] is False


def test_bound_security_review_and_assessment_authorize():
    events = [
        _event("prism.verdict", {"candidate_sha": "candidate", "policy_digest": "policy",
                                "scope": "security", "verdict": "pass"}, 1, "review"),
        _event("security.assessed", {"candidate_sha": "candidate", "policy_digest": "policy",
                                    "status": "passed", "review_command_id": "review"}, 2),
    ]
    assert _security_status(events, "candidate", "policy") == ("passed", True)
