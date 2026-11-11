"""Pilot control logic checks; these never invoke a model or claim journey success."""

from types import SimpleNamespace

import pytest

from tests.e2e_live import test_installed_web_workflow as workflow


# AC-FR0348-01@v0.12 TRACKS-TRACE repeated review rounds use real peer result identities
@pytest.mark.parametrize("peer_rounds,expected_reviews", [([65, 90], 2), ([65, 65], 1)])
def test_review_round_identity_does_not_require_a_complete_trio(
    tmp_path, monkeypatch, peer_rounds, expected_reviews
):
    page = SimpleNamespace(on=lambda *_: None, wait_for_timeout=lambda *_: None)
    install = SimpleNamespace(
        artifact_dir=tmp_path, isolated_bin=tmp_path / "bin", tools_bin=tmp_path / "tools",
    )
    pilot = workflow.InstalledWebPilot(tmp_path, install, page)
    pilot.run_id = "pilot"
    review = {
        "stage": "M-SPEC", "substate": "HUMAN_REVIEW", "status": "awaiting_human",
        "awaiting_gate": {"type": "review"},
    }
    states = iter([review, review, {
        "stage": "M-RELEASE", "substate": "AWAITING_RELEASE", "status": "active",
    }])
    rounds = iter(peer_rounds)
    calls = []

    def read(suffix=""):
        if suffix:
            # Before acceptance exists, this is the legitimate real API response.
            return {"content": "tinycalc spec", "revision": None}
        return next(states)

    pilot.read = read
    pilot.answer_review = lambda stage: calls.append(stage) or True
    monkeypatch.setattr(workflow, "_events", lambda *_: [{
        "seq": next(rounds), "type": "lex.verdict", "payload": {"verdict": "pass"},
    }])
    state = pilot.drive_to_candidate()
    assert state["stage"] == "M-RELEASE"
    assert calls == ["M-SPEC"] * expected_reviews
