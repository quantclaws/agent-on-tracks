"""E2E happy path: the missing-token attention halt and recovery.

The user journey (test-plan §9.1): the canonical ``GITHUB_TOKEN`` is
missing while a non-canonical ``GITHUB_ACCESS_TOKEN`` is present. The run
must:

* emit AT MOST ONE ``attention.required(area="issue_creation",
  reason="missing_token")`` whose hint names ``export GITHUB_TOKEN``;
* stop the run loop cleanly at ``needs_attention`` -- no tight loop, no
  ``CommandStallError``;
* recover once the operator restores the canonical credential: the run
  advances past the attention and the attention is resolved.

This is the HAPPY PATH of the attention journey. The boundary/error paths
(the classification variants, the dedup, the no-issue-created guarantee on
the real channel) live in the integration layer
(``tests/integration/test_hotfix262_attention.py``).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e


def _attention_events(query, run_id: str | None = None) -> list:
    return [
        event
        for event in query(run_id)
        if event["type"] == "attention.required"
    ]


def _missing_token_attentions(query, run_id: str | None = None) -> list:
    return [
        event
        for event in _attention_events(query, run_id)
        if event["payload"].get("area") == "issue_creation"
        and event["payload"].get("reason") == "missing_token"
    ]


# AC-FR0283-02@v0.8 TRACKS-TRACE missing token halts with one attention
# AC-FR0298-02@v0.9 TRACKS-TRACE the halted run does not hot loop
def test_missing_token_halts_and_recovers(trac, host_repo, event_log, monkeypatch):
    """AC-FR0283-02 / AC-FR0298-02: the missing-token path halts the run
    with exactly one actionable attention, does not hot-loop, and recovers
    once the operator restores the canonical credential.

    Contract: interfaces §1d items 1-4 (the canonical credential, the
    hint-only diagnostic, the ≤1 emission and the clean stop), §4a
    outlet 4.
    """
    # The canonical credential is absent; a NON-canonical one is present --
    # the contract forbids falling back to it.
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_ACCESS_TOKEN", "non-canonical-token")
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("TRAC_FAKE_SIMULATE", raising=False)

    assert trac("init").returncode == 0
    started = trac("start", "v0.8", stdin="hotfix-262 attention journey")
    assert started.returncode == 0, started.stderr

    # Walk the human gates up to the M-REQ-APPROVAL approval; approving it
    # materializes the ISSUES substate -- the issue-creation path whose
    # credential preflight the contract puts at that entry.
    assert trac("run", "--no-watch").returncode == 0
    assert trac("triage", "go").returncode == 0
    for _ in ("M-STORY", "M-SPEC", "M-ACC"):
        drive = trac("run", "--no-watch")
        assert drive.returncode == 0, drive.stderr
        assert trac("review", "no-comment").returncode == 0
    drive = trac("run", "--no-watch")
    assert drive.returncode == 0, drive.stderr
    assert "awaiting=approval" in drive.stdout, (
        f"the journey must reach the M-REQ-APPROVAL gate (stdout={drive.stdout!r})"
    )
    assert trac("approve", "--actor", "Aaron").returncode == 0

    # Drive the run until the attention appears (bounded drives; a missing
    # arrival is a failure, not a hang).
    for _ in range(4):
        drive = trac("run", "--no-watch")
        assert drive.returncode == 0, drive.stderr
        if _missing_token_attentions(event_log):
            break

    attentions = _missing_token_attentions(event_log)
    assert attentions, (
        "the missing canonical credential must land an "
        "attention.required(area=issue_creation, reason=missing_token) at the "
        "M-REQ-APPROVAL -> ISSUES entry"
    )
    assert len(attentions) == 1, (
        "the missing-token attention must be emitted AT MOST once (got "
        f"{len(attentions)})"
    )
    payload = attentions[0]["payload"]
    hint = str(payload.get("next") or "") + str(payload.get("detail") or "")
    assert "export GITHUB_TOKEN" in hint, (
        "the attention must carry the actionable hint naming the canonical "
        f"credential export (hint={hint!r})"
    )

    # The run stops at needs_attention -- no tight loop, no stall abort.
    status = trac("status")
    combined = status.stdout + status.stderr
    assert "needs_attention" in combined, (
        f"the run must report needs_attention (status={combined!r})"
    )
    assert "CommandStallError" not in combined, (
        "the halted run must not abort with a stall error (that is a hot loop)"
    )
    assert "missing_token" in combined, (
        f"the status must report the missing_token reason (status={combined!r})"
    )

    # The attention count is stable: the loop did not re-emit it.
    before = len(_missing_token_attentions(event_log))
    trac("run", "--no-watch")
    after = len(_missing_token_attentions(event_log))
    assert after == before, (
        "the halted run must not re-emit the attention (before "
        f"{before}, after {after})"
    )

    # Recovery: the operator restores the canonical credential and the run
    # advances past the attention.
    monkeypatch.setenv("GITHUB_TOKEN", "restored-token")
    monkeypatch.delenv("GITHUB_ACCESS_TOKEN", raising=False)
    for _ in range(4):
        drive = trac("run", "--no-watch")
        assert drive.returncode == 0, drive.stderr
        if "needs_attention" not in (drive.stdout + drive.stderr):
            break
    recovered = trac("status")
    recovered_text = recovered.stdout + recovered.stderr
    assert "missing_token" not in recovered_text, (
        "the restored credential must clear the missing_token attention "
        f"(status={recovered_text!r})"
    )
    assert len(_missing_token_attentions(event_log)) == before, (
        "the recovery must not re-emit the attention"
    )
