"""IF-BLOCKREV-001 RED: M-TEST checkpoint refuses pass + blocking findings.

T006-R5-F1: M-TEST PRISM_REVIEW is checkpoint-driven. The checkpoint must
carry blocking findings on a pass (so the ingest point can judge them) and
``result_checkpoint._publish_prism_verdict`` must fail closed on the
contradictory pass instead of publishing a passing ``prism.verdict``
(interfaces §1c.2, AC-FR0040-03).

Expected events are enumerated from interfaces §1c/§4a, never derived from
the implementation under test.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from tracks.executor.result_checkpoint import ResultCheckpointMixin
from tracks.kernel.machine import _CRITERIA_PACK

_BLOCKING_FINDING = {
    "id": "PRISM-MTEST-01",
    "severity": "blocker",
    "summary": "contradictory pass verdict",
}


class _Cmd:
    command_id = "CMD-MTEST"
    params: dict = {}


class _Store:
    def write_audit_blob(self, _payload):
        return "blob-ref"


class _CheckpointHost(ResultCheckpointMixin):
    """Event sink over the real M-TEST checkpoint payload builder."""

    def __init__(self, repo: Path):
        self.repo = repo
        self.store = _Store()
        self.emitted = []

    def _doc_path(self, name: str) -> Path:
        return self.repo / name

    def _emit(self, event, payload=None, **kwargs):
        self.emitted.append((event, payload or {}, kwargs))


# AC-FR0040-03@v0.4 TRACKS-TRACE IF-BLOCKREV-001 M-TEST checkpoint refuses pass+blocking
# AC-FR0271-01@v0.8 TRACKS-TRACE IF-BLOCKREV-001 contradictory verdict never published
def test_mtest_checkpoint_publishes_no_pass_with_blocking_findings(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "test-plan.md").write_text("plan", encoding="utf-8")
    (repo / "interfaces.md").write_text("if", encoding="utf-8")
    host = _CheckpointHost(repo)

    result = {
        "verdict": "pass",
        "criteria_pack": dict(_CRITERIA_PACK),
        "findings": [dict(_BLOCKING_FINDING)],
        "review_summary": "blocker recorded by the reviewer",
    }
    checkpoint = host._m_test_prism_payload(
        result, base_sha="base", result_id="RESULT-1"
    )
    domain_payload = checkpoint["domain_event"]["payload"]

    assert any(
        finding.get("severity") == "blocker"
        for finding in domain_payload.get("findings") or []
    ), "the M-TEST checkpoint must preserve blocking findings on a pass"

    cmd = _Cmd()
    cmd.params = {"domain_event": checkpoint["domain_event"]}
    host._publish_prism_verdict(
        "pass",
        commit_sha="commit",
        created_commit=True,
        result_id="RESULT-1",
        state=SimpleNamespace(stage="M-TEST", review_round=0),
        cmd=cmd,
        task_id="T-006",
    )

    verdicts = [
        payload for event, payload, _kwargs in host.emitted if event == "prism.verdict"
    ]
    assert [p for p in verdicts if p.get("verdict") == "pass"] == [], (
        "pass + blocking findings must never publish a passing prism.verdict"
    )
    assert [p for p in verdicts if p.get("verdict") == "revise"] == [], (
        "the contradictory verdict must not be downgraded to revise"
    )
    assert any(
        event in ("verdict.failed", "attention.required")
        for event, _payload, _kwargs in host.emitted
    ), "pass + blocking findings must fail closed with verdict.failed / attention.required"
    assert result["verdict"] == "pass"
    assert result["findings"] == [dict(_BLOCKING_FINDING)]
