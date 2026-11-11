"""IF-BLOCKREV-001 RED: blocking review findings predicate (hotfix-262 T-004).

``has_blocking_findings(findings)`` is the single severity arbiter (interfaces
§1c, INV-3): any finding with severity in {"blocker", "high", "critical"} is
blocking.  The downstream release faces treat
``verdict != "pass" OR has_blocking_findings(findings)`` as failed:
``release_authorization._prism_status`` / ``_security_status`` and
``release_cmd._prism_final_status``.

Expected values are enumerated from interfaces §1c, never derived from the
implementation under test.
"""

from __future__ import annotations

import copy

import pytest

from tests.unit.full_f_authority_support import CANDIDATE_SHA
from tests.unit.hotfix262_release_support import ev as _ev
from tests.unit.test_release_authorization_direct import _host
from tracks.executor.host_contract import load_host_contract
from tracks.executor.release_authorization import _prism_status, _security_status
from tracks.executor.review_findings import has_blocking_findings

CANDIDATE = CANDIDATE_SHA


def _blocking(findings) -> bool:
    try:
        return has_blocking_findings(findings)
    except NotImplementedError:
        raise AssertionError(
            "IF-BLOCKREV-001 仍是桩：has_blocking_findings 必须已实现严重度判定"
        ) from None


def _copy_events(events):
    return [
        _ev(event.seq, event.type, copy.deepcopy(event.payload), event.command_id)
        for event in events
    ]


# AC-FR0271-01@v0.8 TRACKS-TRACE IF-BLOCKREV-001 blocking severity set
@pytest.mark.parametrize("severity", ["blocker", "high", "critical"])
def test_blocking_severities_are_recognized(severity):
    assert _blocking([{"severity": severity}]) is True


# AC-FR0271-01@v0.8 TRACKS-TRACE IF-BLOCKREV-001 non-blocking severity set
@pytest.mark.parametrize("severity", ["medium", "low", "info", "", "Blocker", None])
def test_non_blocking_or_unknown_severities_do_not_block(severity):
    assert _blocking([{"severity": severity}]) is False


# AC-FR0271-01@v0.8 TRACKS-TRACE IF-BLOCKREV-001 empty findings
def test_empty_findings_are_not_blocking():
    assert _blocking([]) is False
    assert _blocking(()) is False


# AC-FR0271-01@v0.8 TRACKS-TRACE IF-BLOCKREV-001 any blocking finding wins
def test_any_blocking_finding_in_the_set_blocks():
    findings = [
        {"severity": "low"},
        {"severity": "high"},
        {"severity": "info"},
    ]

    assert _blocking(findings) is True


# AC-FR0271-01@v0.8 TRACKS-TRACE IF-BLOCKREV-001 missing severity
def test_finding_without_severity_does_not_block():
    assert _blocking([{"message": "no severity recorded"}]) is False


# AC-FR0271-02@v0.8 TRACKS-TRACE IF-BLOCKREV-001 prism status fails pass+blocker
def test_prism_status_treats_pass_with_blocking_findings_as_failed():
    event = _ev(
        1,
        "prism.verdict",
        {
            "candidate_sha": CANDIDATE,
            "scope": "verify_final",
            "verdict": "pass",
            "findings": [{"severity": "blocker"}],
        },
        command_id="PRISM-FINAL",
    )

    status, ok = _prism_status([event], CANDIDATE)

    assert status == "failed"
    assert ok is False


# AC-FR0271-02@v0.8 TRACKS-TRACE IF-BLOCKREV-001 prism status keeps clean pass
def test_prism_status_keeps_pass_without_blocking_findings():
    event = _ev(
        1,
        "prism.verdict",
        {
            "candidate_sha": CANDIDATE,
            "scope": "verify_final",
            "verdict": "pass",
            "findings": [{"severity": "medium"}],
        },
        command_id="PRISM-FINAL",
    )

    assert _prism_status([event], CANDIDATE) == ("pass", True)


# AC-FR0272-02@v0.8 TRACKS-TRACE IF-BLOCKREV-001 security status fails blockers
def test_security_status_treats_pass_with_blocking_findings_as_failed(tmp_path):
    _repo, store, run_id, candidate, digest, contract_path, _preview = _host(
        tmp_path
    )
    events = _copy_events(store.events(run_id))
    for event in events:
        if event.type == "prism.verdict" and event.payload.get("scope") == "security":
            event.payload["findings"] = [{"severity": "critical"}]
    contract = load_host_contract(contract_path)

    status, ok = _security_status(events, candidate, digest, contract)

    assert status == "failed"
    assert ok is False


# AC-FR0272-02@v0.8 TRACKS-TRACE IF-BLOCKREV-001 security clean pass stays pass
def test_security_status_keeps_a_clean_pass(tmp_path):
    _repo, store, run_id, candidate, digest, contract_path, _preview = _host(
        tmp_path
    )
    events = _copy_events(store.events(run_id))
    for event in events:
        if event.type == "prism.verdict" and event.payload.get("scope") == "security":
            event.payload["findings"] = [{"severity": "low"}]
    contract = load_host_contract(contract_path)

    assert _security_status(events, candidate, digest, contract) == ("passed", True)
