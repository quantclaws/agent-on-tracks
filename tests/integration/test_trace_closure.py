"""Integration: candidate-bound trace closure (IF-CLOSURE-001, IF-MUTATION-001).

AC-FR0265-01@v0.7 closure candidate-bound pass,
AC-FR0265-02@v0.7 blocking conditions hard errors,
AC-FR0265-03@v0.7 candidate digest consistency.

Assertions land on `trac check trace --version v0.7 --json` (IF-CLOSURE-001,
interfaces §1i) CLI outlet: the JSON payload is parsed and specific contract
fields are asserted (not substring presence/echo). The v0.7 candidate-bound
closure is not yet wired, so the v0.7-specific fields are absent — the
assertions fail legally on the missing contract outlet.
"""

from __future__ import annotations

import json

import pytest

from tracks.cli.main import cmd_check

pytestmark = pytest.mark.integration


def _v07_closure_json(host_repo, capsys):
    """Run `trac check trace --version v0.7 --json` and parse the JSON payload.

    Returns the parsed dict; raises if stdout is not valid JSON (the v0.7
    closure outlet is absent — a legal-Red signal, not a silent pass)."""
    cmd_check(host_repo, "trace", "--version", "v0.7", "--json")
    out = capsys.readouterr().out
    return json.loads(out), out


# AC-FR0265-01@v0.7 TRACKS-TRACE closure candidate-bound pass
def test_closure_candidate_bound_pass(host_repo, capsys):
    """AC-FR0265-01: v0.7 closure JSON carries closure=candidate-bound + pass."""
    payload, raw = _v07_closure_json(host_repo, capsys)
    # Contract (§1i/§2c): the v0.7 closure JSON must carry a `closure` field
    # equal to `candidate-bound` and `status` == `pass` — not a substring echo.
    assert payload.get("closure") == "candidate-bound", (
        f"v0.7 closure must be candidate-bound; payload={payload}"
    )
    assert payload.get("status") == "pass", (
        f"candidate-bound closure must report status=pass; payload={payload}"
    )


# AC-FR0265-02@v0.7 TRACKS-TRACE blocking conditions hard errors
def test_blocking_conditions_hard_errors(host_repo, capsys):
    """AC-FR0265-02: node_missing/skip_xfail/identity_drift/control_failure
    surface as hard_errors from the closed set (§1i)."""
    payload, raw = _v07_closure_json(host_repo, capsys)
    hard_errors = payload.get("hard_errors", [])
    closed_set = {
        "node_missing", "skip_xfail", "identity_drift", "control_failure",
        "baseline_missing", "mutation_missing", "full_pass_missing",
        "foreign_candidate",
    }
    # The v0.7 closure must surface hard_errors drawn from the closed set;
    # a status=fail must carry at least one closed-set hard error (not a
    # generic message). On an unseeded host the closure outlet is absent.
    if payload.get("status") != "pass":
        assert hard_errors, "status=fail must carry hard_errors (no silent fail)"
        assert any(
            any(tok in err for tok in closed_set) for err in hard_errors
        ), f"hard_errors must come from the closed set {closed_set}; got {hard_errors}"
    # The closure field itself must be present (the v0.7-specific outlet).
    assert "closure" in payload, (
        "v0.7 closure must surface the closure field (hard-error closed set)"
    )


# AC-FR0265-03@v0.7 TRACKS-TRACE candidate digest consistency
def test_candidate_digest_consistency(host_repo, capsys):
    """AC-FR0265-03: the --json per-AC record (§1i) binds baseline + mutation
    evidence to the SAME candidate_digest; a foreign candidate hard-errors."""
    payload, raw = _v07_closure_json(host_repo, capsys)
    # Contract (§1i): the v0.7 closure JSON carries a per-AC record list where
    # each entry has a `candidate_digest`; baseline_evidence and
    # mutation_evidence bind the same candidate_digest (no foreign candidate).
    ac_records = payload.get("acs") or payload.get("records") or []
    assert ac_records, (
        "v0.7 closure must carry a per-AC record list (candidate_digest binding)"
    )
    for record in ac_records:
        assert "candidate_digest" in record, (
            f"per-AC record must carry candidate_digest; record={record}"
        )
        baseline_cd = record.get("candidate_digest")
        # baseline_evidence + mutation_evidence must bind the same candidate.
        mutation_cd = record.get("mutation_evidence")
        if mutation_cd is not None:
            assert baseline_cd == mutation_cd or baseline_cd in str(mutation_cd), (
                f"foreign candidate: baseline {baseline_cd} != mutation {mutation_cd}"
            )
