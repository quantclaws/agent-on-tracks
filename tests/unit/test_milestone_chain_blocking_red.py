"""IF-BLOCKREV-001 RED: the M-MILESTONE close refuses a blocking review
(hotfix-262 T-005).

interfaces §1c.3 makes ``milestone_chain._do_close_milestone`` share the
downstream hard invariant of INV-3: ``verdict != "pass" OR
has_blocking_findings(findings)`` is failed.  A contradictory pass+blocking
verdict -- or any non-pass verdict -- bound to the closing candidate must stop
the closing tail (no ``milestone.trace_closed`` / ``run.completed(released)``)
and leave an actionable ``attention.required`` audit witness, exactly like the
other fail-closed milestone sub-steps.

The current tree has no findings/blocking check in ``_do_close_milestone``, so
the blocking scenarios fail with assertion_failure (no stub_token, no assembly
errors).  Expected values are enumerated from INV-3 / interfaces §1c, never
derived from the implementation under test.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from tracks.executor.milestone_chain import ExecMilestoneMixin
from tracks.executor.publish import operation_idempotency_key

CANDIDATE = "44c14de" + "0" * 33
FOREIGN_CANDIDATE = "ee781bf" + "0" * 33
PREVIEW_DIGEST = "sha256:" + "b" * 64


class _Cmd:
    command_id = "CMD-CLOSE"
    params: dict = {}


def _ev(seq: int, etype: str, payload: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(seq=seq, type=etype, payload=payload or {})


class _Host(ExecMilestoneMixin):
    """Real close-tail logic; only external/irreversible seams are stubbed.

    Issue/project closes, evidence sealing and refs cleanup touch git/GitHub or
    the runtime blob store; the closing transitions themselves (trace closure,
    completion) stay real so their presence/absence is the assertion surface.
    """

    def __init__(self, tmp_path: Path):
        self.repo = Path(tmp_path) / "repo"
        self.repo.mkdir(parents=True, exist_ok=True)
        self.run_id = "RUN"
        self.events: list = []
        self.emitted: list[tuple[str, dict, dict]] = []
        self.store = SimpleNamespace(events=lambda _run: list(self.events))

    def _emit(self, event, payload=None, **kwargs):
        self.emitted.append((event, payload or {}, kwargs))
        self.events.append(_ev(len(self.events) + 1, event, payload))

    def _load_authoritative_issue_map(self):
        return {}

    def _close_milestone_issues(self, *_args, **_kwargs):
        return None

    def _close_milestone_project(self, *_args, **_kwargs):
        return {"state": "skipped", "reason": "not_authoritative"}

    def _seal_milestone(self, *_args, **_kwargs):
        return True

    def _clean_milestone_refs(self, *_args, **_kwargs):
        return True


def _terminal_publish_events(start_seq: int) -> list:
    """A candidate-bound preview whose whole operation plan is already done."""
    steps = ["merge:main", "tag:v0.12.1"]
    events = [
        _ev(
            start_seq,
            "release.previewed",
            {
                "candidate_sha": CANDIDATE,
                "preview_digest": PREVIEW_DIGEST,
                "operation_plan": {"steps": steps},
            },
        )
    ]
    for offset, step in enumerate(steps, start=1):
        kind, _, target = step.partition(":")
        events.append(
            _ev(
                start_seq + offset,
                "publish.executed",
                {
                    "idempotency_key": operation_idempotency_key(
                        PREVIEW_DIGEST, kind, target
                    ),
                    "status": "done",
                },
            )
        )
    return events


def _close_with_verdicts(tmp_path: Path, verdicts: list[dict]) -> _Host:
    host = _Host(tmp_path)
    host.events = [_ev(1, "candidate.frozen", {"candidate_sha": CANDIDATE})]
    host.events += _terminal_publish_events(2)
    host.events += [
        _ev(index + 5, "prism.verdict", payload)
        for index, payload in enumerate(verdicts)
    ]
    host._do_close_milestone(_Cmd(), SimpleNamespace(), "T-1", False)
    return host


def _event_names(host: _Host) -> list[str]:
    return [event for event, _payload, _kwargs in host.emitted]


def _assert_close_blocked(host: _Host) -> None:
    names = _event_names(host)
    assert "attention.required" in names, (
        "a blocking review verdict must leave an actionable attention.required"
    )
    assert "milestone.trace_closed" not in names, (
        "a blocking review must never trace-close the release"
    )
    assert "run.completed" not in names, (
        "a blocking review must never complete the run as released"
    )
    attention = next(
        payload for event, payload, _kwargs in host.emitted
        if event == "attention.required"
    )
    assert attention.get("candidate_sha") == CANDIDATE
    assert attention.get("reason"), "the attention must name the failed judgement"
    assert attention.get("next"), "the attention must carry actionable next guidance"


def _assert_close_completed(host: _Host) -> None:
    names = _event_names(host)
    assert "attention.required" not in names, (
        "a clean pass without blocking findings must not park the closing tail"
    )
    completed = [
        payload for event, payload, _kwargs in host.emitted
        if event == "run.completed"
    ]
    assert len(completed) == 1
    assert completed[0]["terminal_state"] == "released"


# AC-FR0271-02@v0.8 TRACKS-TRACE IF-BLOCKREV-001 milestone close fails on blocking
def test_close_milestone_fails_on_blocking_findings(tmp_path):
    blocking_verdicts = [
        {
            "scope": "verify_final",
            "verdict": "pass",
            "candidate_sha": CANDIDATE,
            "findings": [{"severity": "blocker", "summary": "contradictory pass"}],
        },
        {
            "scope": "verify_final",
            "verdict": "revise",
            "candidate_sha": CANDIDATE,
            "findings": [],
        },
        {
            "scope": "security",
            "verdict": "pass",
            "candidate_sha": CANDIDATE,
            "findings": [{"severity": "critical", "summary": "unfixed CVE"}],
        },
    ]
    for verdict in blocking_verdicts:
        _assert_close_blocked(_close_with_verdicts(tmp_path, [verdict]))

    clean = {
        "scope": "verify_final",
        "verdict": "pass",
        "candidate_sha": CANDIDATE,
        "findings": [{"severity": "low", "summary": "naming nit"}],
    }
    # A blocker bound to another candidate is stale for this closure: it must
    # not block the current candidate's release (NFR-0143 candidate binding).
    stale_blocker = {
        "scope": "verify_final",
        "verdict": "pass",
        "candidate_sha": FOREIGN_CANDIDATE,
        "findings": [{"severity": "blocker", "summary": "superseded candidate"}],
    }
    _assert_close_completed(_close_with_verdicts(tmp_path, [clean, stale_blocker]))
