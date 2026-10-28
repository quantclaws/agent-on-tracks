"""T-002 RED: IF-ATTN-001 credential preflight, attention dedupe, loop park.

Pins the four locked seams of the attention contract:

- ``supervisor/intake`` hotfix precheck keeps a classified issue-fetch
  failure as ``issue_fetch_failed`` instead of swallowing it into
  ``issue_not_found`` (AC-FR0329-02);
- ``supervisor/service_intake`` submit-intake issue fetch reports the
  missing-token classification with the canonical-credential hint
  (interfaces §1d.1/§1d.4);
- ``executor/release_tail`` issue creation emits exactly one
  ``attention.required(area="issue_creation", reason="missing_token")``
  while it is unresolved, and hints the canonical ``GITHUB_TOKEN`` when a
  legacy variable is present (no fallback) (AC-FR0283-02);
- ``executor/run_loop`` treats any unresolved non-auto-retryable
  attention as an operator stop instead of a re-drive (AC-FR0298-02).

RED discipline: every target node fails on the pre-fix baseline with a
real ``AssertionError`` (the seams re-emit, swallow, or ignore the
attention); no stub tokens, no assembly errors. Only unit tests are
added; no production code is modified.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.unit.helpers import git_repo
from tracks.effects.github import GithubIssuesError
from tracks.executor.breaker import RunBreaker
from tracks.executor.executor import Executor
from tracks.executor.stall import CommandStallTracker
from tracks.store import Store
from tracks.supervisor import intake
from tracks.supervisor.service_intake import IntakeCommands

_RUN = "RUN-ATTENTION"
_VERSION = "v0.12"
_ISSUE_ATTENTION = {
    "area": "issue_creation",
    "reason": "missing_token",
    "stage": "M-REQ-APPROVAL",
}


def _missing_token(_repo, _version):
    raise GithubIssuesError("missing_token", "GITHUB_TOKEN not set")


class _Denied(Exception):
    """Sentinel raised by the deny collector in the service-intake face."""


def _repo(tmp_path, monkeypatch):
    monkeypatch.delenv("TRACKS_HOME", raising=False)
    return git_repo(tmp_path)


def _executor(repo):
    store = Store(repo / ".tracks")
    store.append(_RUN, _VERSION, "story.requested", {"raw_chars": 1})
    store.append(_RUN, _VERSION, "stage.entered", {"stage": "M-REQ-APPROVAL"})
    executor = Executor.__new__(Executor)
    executor.store = store
    executor.repo = repo
    executor.run_id = _RUN
    executor.version = _VERSION
    executor._issue_backend = None
    executor._breaker = RunBreaker.from_events(store.events(_RUN))
    executor._stall = CommandStallTracker()
    return executor


def _attention_events(executor):
    return [
        event
        for event in executor.store.events(_RUN)
        if event.type == "attention.required"
    ]


def _noop():
    return None


def _noop_for_state(_state):
    return None


def _not_sm02(_state):
    return False


def _isolate_preflight(monkeypatch, executor):
    monkeypatch.setattr(executor, "_fail_fast_on_code_drift", _noop)
    monkeypatch.setattr(executor, "_observe_oob", _noop)
    monkeypatch.setattr(executor, "_check_breaker", _noop_for_state)
    monkeypatch.setattr(executor, "_sm02_pre_decide", _not_sm02)


def test_pending_operator_attention_covers_non_verify_manual_attention(tmp_path, monkeypatch):
    """AC-FR0298-02: an unresolved issue-creation attention is operator work
    even outside M-VERIFY; the re-drive gate must see it."""
    executor = _executor(_repo(tmp_path, monkeypatch))
    attention = executor.store.append(_RUN, _VERSION, "attention.required", dict(_ISSUE_ATTENTION))

    pending = executor._pending_operator_attention(executor.store.state(_RUN))

    assert pending is not None, (
        "assertion failure: an unresolved non-auto-retryable attention outside "
        "M-VERIFY must block the re-drive (IF-ATTN-001 §1d.2)"
    )
    assert pending.seq == attention.seq, (
        f"assertion failure: the pending attention must be the latest unresolved "
        f"one, got seq={getattr(pending, 'seq', None)!r} expected {attention.seq}"
    )


def test_run_loop_preflight_parks_on_unresolved_operator_attention(tmp_path, monkeypatch):
    """AC-FR0298-02: each loop iteration stops cleanly on a pending attention
    (bounded, no re-emission) instead of falling through to decide()."""
    executor = _executor(_repo(tmp_path, monkeypatch))
    executor.store.append(_RUN, _VERSION, "attention.required", dict(_ISSUE_ATTENTION))
    _isolate_preflight(monkeypatch, executor)
    before = len(_attention_events(executor))

    action, state = executor._run_loop_preflight()

    assert action == "return", (
        f"assertion failure: an unresolved operator attention must stop the loop "
        f"with action='return', got {action!r}"
    )
    assert state is not None, "assertion failure: the stop must carry the current state"
    assert len(_attention_events(executor)) == before, (
        "assertion failure: the preflight check must not emit a second attention "
        f"(IF-ATTN-001 §1d.2/§1d.5); got {len(_attention_events(executor))} events"
    )
    assert executor._run_loop_preflight()[0] == "return", (
        "assertion failure: every iteration must keep parking while the attention "
        "stays unresolved"
    )


def test_ensure_issue_backend_dedupes_unresolved_missing_token(tmp_path, monkeypatch):
    """AC-FR0283-02: repeated ISSUES entry points emit at most one unresolved
    missing-token attention (IF-ATTN-001 §1d.3/§1d.5)."""
    executor = _executor(_repo(tmp_path, monkeypatch))
    monkeypatch.setattr(
        "tracks.executor.release_tail.select_issue_backend", _missing_token
    )
    command = SimpleNamespace(command_id="CMD-ISSUES-1")

    assert executor._ensure_issue_backend(command, "T-002") is None
    assert len(_attention_events(executor)) == 1, (
        "assertion failure: the first missing-token failure must emit exactly one "
        f"attention, got {len(_attention_events(executor))}"
    )

    assert executor._ensure_issue_backend(command, "T-002") is None
    assert len(_attention_events(executor)) == 1, (
        "assertion failure: an already unresolved missing_token attention must not "
        f"be re-emitted (IF-ATTN-001 §1d.3); got {len(_attention_events(executor))}"
    )


@pytest.mark.parametrize("legacy", ["GITHUB_ACCESS_TOKEN", "GH_TOKEN"])
def test_ensure_issue_backend_hints_the_canonical_credential(
    tmp_path, monkeypatch, legacy
):
    """AC-FR0283-02: a legacy variable is hint-only — no fallback — and the
    hint names it together with the canonical export (IF-ATTN-001 §1d.1)."""
    executor = _executor(_repo(tmp_path, monkeypatch))
    for key in ("GITHUB_TOKEN", "GITHUB_ACCESS_TOKEN", "GH_TOKEN", "TRAC_FAKE_SIMULATE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("TRAC_AGENT_BACKEND", raising=False)
    monkeypatch.setenv(legacy, "legacy-value")

    assert executor._ensure_issue_backend(
        SimpleNamespace(command_id="CMD-ISSUES-2"), "T-002"
    ) is None, (
        f"assertion failure: {legacy} must never act as a GITHUB_TOKEN fallback "
        "(IF-ATTN-001 §1d.1)"
    )
    attentions = _attention_events(executor)
    assert len(attentions) == 1, (
        f"assertion failure: missing GITHUB_TOKEN must emit one attention, got {len(attentions)}"
    )
    payload = attentions[0].payload or {}
    detail = f"{payload.get('detail') or ''} {payload.get('next') or ''}"
    assert "export GITHUB_TOKEN" in detail, (
        f"assertion failure: the attention must carry the actionable export hint, got {detail!r}"
    )
    assert legacy in detail, (
        f"assertion failure: the preflight hint must name the legacy variable "
        f"{legacy!r} it found (IF-ATTN-001 §1d.1), got {detail!r}"
    )


def test_issue_fetch_deny_preserves_fetch_failure_classification(tmp_path, monkeypatch):
    """AC-FR0329-02/AC-FR0283-02: submit_intake(source="issue") maps a
    missing-token fetch failure to issue_fetch_failed with the canonical
    credential hint, never a bare issue_not_found swallow."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_ACCESS_TOKEN", "legacy-value")
    monkeypatch.setattr("tracks.effects.github.select_issue_backend", _missing_token)
    denied: dict = {}

    def _deny(reason, detail):
        denied.update({"reason": reason, "detail": detail})
        raise _Denied(detail)

    with pytest.raises(_Denied):
        IntakeCommands()._fetch_issue_or_deny(tmp_path, 7, _deny)

    assert denied.get("reason") == "issue_fetch_failed", (
        "assertion failure: a classified fetch failure must reject as "
        f"issue_fetch_failed, got {denied.get('reason')!r}"
    )
    detail = str(denied.get("detail") or "")
    assert "missing_token" in detail, (
        f"assertion failure: the rejection must carry the classification, got {detail!r}"
    )
    assert "GITHUB_ACCESS_TOKEN" in detail, (
        "assertion failure: the submit-intake preflight must hint the canonical "
        f"export when a legacy variable is present (IF-ATTN-001 §1d.1), got {detail!r}"
    )


def test_hotfix_precheck_keeps_missing_token_as_fetch_failed(tmp_path, monkeypatch):
    """AC-FR0329-02: create_run(hotfix_*) precheck preserves the classified
    issue_fetch_failed path instead of reporting issue_not_found."""
    repo = _repo(tmp_path, monkeypatch)
    monkeypatch.delenv("TRACKS_HOME", raising=False)
    monkeypatch.setattr("tracks.effects.github.select_issue_backend", _missing_token)

    report = intake._hotfix_precheck(repo, 7, "post-release")

    assert report.status == "rejected", (
        f"assertion failure: a missing-token hotfix precheck must reject, got {report.status!r}"
    )
    assert report.reason == "issue_fetch_failed", (
        "assertion failure: missing-token must stay issue_fetch_failed, not be "
        f"swallowed as {report.reason!r} (IF-ATTN-001 §1d.4)"
    )
    assert report.next and "GITHUB_TOKEN" in report.next, (
        f"assertion failure: the rejection must carry the GITHUB_TOKEN recovery "
        f"pointer, got {report.next!r}"
    )


def test_ensure_issue_backend_reemits_after_issue_created(tmp_path, monkeypatch):
    """Resolution guard: once issue.created proves the old attention cleared,
    a fresh missing-token failure must become visible again."""
    executor = _executor(_repo(tmp_path, monkeypatch))
    monkeypatch.setattr(
        "tracks.executor.release_tail.select_issue_backend", _missing_token
    )
    command = SimpleNamespace(command_id="CMD-ISSUES-3")

    assert executor._ensure_issue_backend(command, "T-002") is None
    assert len(_attention_events(executor)) == 1
    executor.store.append(
        _RUN, _VERSION, "issue.created", {"item_id": "FR-1", "issue_id": "1"}
    )

    assert executor._ensure_issue_backend(command, "T-002") is None
    assert len(_attention_events(executor)) == 2, (
        "assertion failure: a resolved attention must not suppress the next "
        f"missing-token emission, got {len(_attention_events(executor))}"
    )


def test_run_loop_preflight_ignores_auto_retryable_attention(tmp_path, monkeypatch):
    """AC-FR0298-02 boundary: arms a plain re-drive may clear (ci_readback)
    must not park the loop."""
    executor = _executor(_repo(tmp_path, monkeypatch))
    executor.store.append(
        _RUN,
        _VERSION,
        "attention.required",
        {"area": "ci_readback", "reason": "network_error", "stage": "M-REQ-APPROVAL"},
    )
    _isolate_preflight(monkeypatch, executor)

    action, _state = executor._run_loop_preflight()

    assert action == "proceed", (
        "assertion failure: an auto-retryable attention area must not park the "
        f"loop, got action={action!r}"
    )


def test_run_loop_preflight_ignores_resolved_attention(tmp_path, monkeypatch):
    """AC-FR0298-02 boundary: an attention cleared by its recovery event is
    not a pending operator block."""
    executor = _executor(_repo(tmp_path, monkeypatch))
    executor.store.append(_RUN, _VERSION, "attention.required", dict(_ISSUE_ATTENTION))
    executor.store.append(
        _RUN, _VERSION, "issue.created", {"item_id": "FR-1", "issue_id": "1"}
    )
    _isolate_preflight(monkeypatch, executor)

    action, _state = executor._run_loop_preflight()

    assert action == "proceed", (
        "assertion failure: a resolved attention must not park the loop, "
        f"got action={action!r}"
    )
