"""IF-BLOCKREV-001 RED: review ingest refuses pass + blocking findings.

interfaces §1c.2 makes ``verdict == "pass"`` with any blocker/high/critical
finding a contradictory verdict = protocol violation. The ingest point
``verdict_face._emit_verdict`` must fail closed: record
``verdict.failed`` / ``attention.required``, never publish a passing
``prism.verdict``, never downgrade the review to ``revise``, and leave the
reviewer's verdict body untouched.

Expected events are enumerated from interfaces §1c/§4a, never derived from
the implementation under test.
"""

from __future__ import annotations

from types import SimpleNamespace

from tracks.executor.verdict_face import ExecVerdictMixin

_BLOCKING_FINDING = {
    "id": "PRISM-BLOCKREV-01",
    "severity": "blocker",
    "summary": "contradictory pass verdict",
}


class _Cmd:
    command_id = "CMD-BLOCKREV"


class _Host(ExecVerdictMixin):
    """Event sink: assertions read the emitted event stream, not internals."""

    def __init__(self):
        self.emitted = []

    def _emit(self, event, payload=None, **kwargs):
        self.emitted.append((event, payload or {}, kwargs))


# AC-FR0140-02@v0.5 TRACKS-TRACE IF-BLOCKREV-001 M-IMPL ingest refuses pass+blocking
# AC-FR0271-01@v0.8 TRACKS-TRACE IF-BLOCKREV-001 contradictory verdict fails closed
def test_emit_verdict_refuses_pass_with_blocking_findings():
    host = _Host()
    result = {
        "verdict": "pass",
        "findings": [dict(_BLOCKING_FINDING)],
        "review_summary": "blocker recorded by the reviewer",
    }

    ExecVerdictMixin._emit_verdict(
        host,
        "prism",
        "pass",
        result,
        SimpleNamespace(
            stage="M-IMPL",
            substate="PRISM_FINAL",
            review_round=0,
            current_attempt=0,
        ),
        {"stage": "M-IMPL"},
        _Cmd(),
        "T-006",
    )

    published = [
        payload for event, payload, _kwargs in host.emitted if event == "prism.verdict"
    ]
    assert [p for p in published if p.get("verdict") == "pass"] == [], (
        "pass + blocking findings must never publish a passing prism.verdict"
    )
    assert any(
        event in ("verdict.failed", "attention.required")
        for event, _payload, _kwargs in host.emitted
    ), "pass + blocking findings must fail closed with verdict.failed / attention.required"
    assert not any(
        event == "review.round_started" for event, _payload, _kwargs in host.emitted
    ), "a contradictory verdict must not be silently downgraded to revise"
    assert result["verdict"] == "pass"
    assert result["findings"] == [dict(_BLOCKING_FINDING)]
