"""T-007 RED: milestone closing-tail terminal-state guard and tracker ensure
wiring (FR-0327 / FR-0331; IF-MILESTONE-002 + IF-TRACKER-001 close face).

Pins the still-undelivered slices of tracks/executor/milestone_chain.py:

- ``_complete_milestone`` must accept only ``run.completed(
  terminal_state="released")`` as its idempotent completion witness. The
  M-IMPL closure boundary pseudo-completion (``terminal_state="boundary"``)
  no longer blocks the released terminal event, which lands exactly once.
- ``_close_milestone_project`` must first ensure the declared milestone
  exists on the remote (IF-TRACKER-001 first-contact semantics) and only
  then close it. An unavailable ensure lands ``attention.required(
  area=project_close, reason=milestone_not_found)`` with an actionable
  ``next`` and keeps the audited skip -- close is never attempted blind.

All nodes fail on the pre-fix baseline with assertion_failure on the contract
behaviour (no stub_token, no assembly errors). Only unit tests are added
(RED discipline, manifest red_test_paths = tests/unit).

AC: FR-0327-01, FR-0331-02 -- TRACKS-TRACE IF-MILESTONE-002, IF-TRACKER-001.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from tracks.executor import milestone_chain as mc
from tracks.executor.milestone_chain import ExecMilestoneMixin

_TRACE = {"release_tag": "v0.10", "trace_digest": "sha256:trace"}
_TRACKER = {
    "repo": "acme/host",
    "project": "release-board",
    "milestone": "release v0.10",
}


class _Cmd:
    command_id = "CMD-CLOSE"
    params: dict = {}


def _ev(seq: int, etype: str, payload: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(seq=seq, type=etype, payload=payload or {})


class _Host(ExecMilestoneMixin):
    """Minimal host: fake event store + emit capture (no filesystem writes)."""

    def __init__(self, tmp_path: Path, events=()):
        self.repo = Path(tmp_path) / "repo"
        self.repo.mkdir(parents=True, exist_ok=True)
        self.run_id = "RUN"
        self.events = list(events)
        self.emitted: list[tuple[str, dict, dict]] = []
        self.store = SimpleNamespace(events=lambda _run: list(self.events))

    def _emit(self, event, payload=None, **kwargs):
        self.emitted.append((event, payload or {}, kwargs))
        self.events.append(_ev(len(self.events) + 1, event, payload or {}))


def _completions(host: _Host) -> list[dict]:
    return [payload for event, payload, _ in host.emitted if event == "run.completed"]


def _attentions(host: _Host) -> list[dict]:
    return [payload for event, payload, _ in host.emitted if event == "attention.required"]


def _params() -> dict:
    return {"tracker": dict(_TRACKER)}


def _ensure_result(*, created=False, api_verified=True, error=None) -> dict:
    return {
        "project": _TRACKER["project"],
        "milestone": _TRACKER["milestone"],
        "number": 7,
        "state": "open",
        "created": created,
        "api_verified": api_verified,
        "error": error,
    }


# AC-FR0327-01@v0.10 TRACKS-TRACE IF-MILESTONE-002 boundary is no witness
def test_complete_milestone_boundary_pseudo_completion_is_not_a_witness(tmp_path):
    """IF-MILESTONE-002 (interfaces 1r.1): a run.completed(
    terminal_state="boundary") is the M-IMPL closure pseudo-completion -- the
    closing tail must still land run.completed(terminal_state=released,
    release_tag) instead of silently skipping."""
    host = _Host(
        tmp_path, [_ev(1, "run.completed", {"terminal_state": "boundary"})]
    )

    host._complete_milestone(_Cmd(), "T-1", dict(_TRACE), list(host.events))

    completions = _completions(host)
    assert len(completions) == 1, (
        "assertion failure: boundary pseudo-completion must not swallow the "
        f"released terminal event, got {host.emitted!r}"
    )
    payload = completions[0]
    assert payload.get("terminal_state") == "released", (
        f"assertion failure: the tail completes as released, got {payload!r}"
    )
    assert payload.get("release_tag") == "v0.10", (
        f"assertion failure: released completion carries the run release tag, got {payload!r}"
    )


# AC-FR0327-01@v0.10 TRACKS-TRACE IF-MILESTONE-002 released lands once
def test_complete_milestone_released_lands_exactly_once_across_reruns(tmp_path):
    """IF-MILESTONE-002 (interfaces 1r.1): a re-entered tail (the released
    completion already in the log) is a true no-op, so a landed released
    completion is the idempotency witness and close_milestone cannot loop on
    repeated completions."""
    host = _Host(
        tmp_path, [_ev(1, "run.completed", {"terminal_state": "boundary"})]
    )
    host._complete_milestone(_Cmd(), "T-1", dict(_TRACE), list(host.events))
    assert len(_completions(host)) == 1, (
        "assertion failure: released must land on the first tail pass"
    )

    host._complete_milestone(_Cmd(), "T-1", dict(_TRACE), list(host.events))
    assert len(_completions(host)) == 1, (
        "assertion failure: a landed released completion is the idempotency "
        f"witness and must not be re-emitted, got {host.emitted!r}"
    )


# AC-FR0327-01@v0.10 TRACKS-TRACE IF-MILESTONE-002 only released counts
def test_complete_milestone_only_released_counts_as_done(tmp_path):
    """IF-MILESTONE-002 (interfaces 1r.1): only terminal_state == "released"
    is the completion witness; a legacy run.completed without the released
    terminal state is not the closing-tail completion either."""
    for payload in ({}, {"terminal_state": ""}):
        host = _Host(tmp_path, [_ev(1, "run.completed", dict(payload))])
        host._complete_milestone(_Cmd(), "T-1", dict(_TRACE), list(host.events))
        assert len(_completions(host)) == 1, (
            f"assertion failure: run.completed payload {payload!r} is not a "
            "released witness; the tail must land the released completion"
        )


# AC-FR0331-01@v0.10 TRACKS-TRACE IF-TRACKER-001 ensure before close
def test_close_project_ensures_milestone_before_close(tmp_path, monkeypatch):
    """IF-TRACKER-001 (interfaces 1r.5.2): the close step runs
    ensure-then-close -- the declared milestone is ensured first (reuse and
    create paths) and closed only after the ensure verified; the rendered
    title reaches the ensure call."""
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    for created in (False, True):
        calls: list[tuple] = []
        host = _Host(tmp_path)

        def _ensure(repo_id, project, title, _created=created, _calls=calls):
            _calls.append(("ensure", repo_id, project, title))
            return _ensure_result(created=_created)

        def _close(repo_id, project, milestone, _calls=calls):
            _calls.append(("close", repo_id, project, milestone))
            return {"api_verified": True, "state": "closed"}

        monkeypatch.setattr(mc, "ensure_project_milestone", _ensure, raising=False)
        monkeypatch.setattr(mc, "close_project_milestone_api", _close)

        entry = host._close_milestone_project(
            _Cmd(), "T-1", dict(_TRACE), _params(), []
        )

        assert [call[0] for call in calls] == ["ensure", "close"], (
            f"assertion failure: ensure must run before close (created={created}), "
            f"got {calls!r}"
        )
        assert calls[0][1:] == ("acme/host", "release-board", "release v0.10"), (
            f"assertion failure: ensure receives the declared repo/project/title, got {calls[0]!r}"
        )
        assert entry.get("state") == "closed", (
            f"assertion failure: an ensured milestone closes normally, got {entry!r}"
        )
        assert entry.get("api_verified") is True, (
            f"assertion failure: verified close keeps api_verified, got {entry!r}"
        )
        assert host.emitted[-1][0] == "project.closed", (
            f"assertion failure: the project step lands project.closed, got {host.emitted!r}"
        )


# AC-FR0331-02@v0.10 TRACKS-TRACE IF-TRACKER-001 no credentials guidance
def test_close_project_without_credentials_reports_milestone_not_found(
    tmp_path, monkeypatch
):
    """IF-TRACKER-001 (interfaces 1r.5.2) + FR-0331: without credentials the
    declared milestone cannot be ensured -- the tail reports
    milestone_not_found with the manual-create + resume guidance and keeps
    the audited skip; close is never attempted."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    close_calls: list[tuple] = []

    def _close(repo_id, project, milestone):
        close_calls.append((repo_id, project, milestone))
        return {"api_verified": True, "state": "closed"}

    monkeypatch.setattr(mc, "close_project_milestone_api", _close)
    host = _Host(tmp_path)

    entry = host._close_milestone_project(
        _Cmd(), "T-1", dict(_TRACE), _params(), []
    )

    attentions = _attentions(host)
    assert attentions, (
        "assertion failure: an unavailable ensure must land attention.required, "
        f"got {host.emitted!r}"
    )
    attention = attentions[-1]
    assert attention.get("area") == "project_close", (
        f"assertion failure: attention area is project_close, got {attention!r}"
    )
    assert attention.get("reason") == "milestone_not_found", (
        f"assertion failure: attention reason is milestone_not_found, got {attention!r}"
    )
    next_text = str(attention.get("next") or "")
    assert "release v0.10" in next_text, (
        f"assertion failure: next names the rendered milestone title, got {next_text!r}"
    )
    assert "trac run --resume" in next_text, (
        f"assertion failure: next carries the resume command, got {next_text!r}"
    )
    assert entry.get("state") == "skipped", (
        f"assertion failure: an unbuildable milestone keeps the audited skip, got {entry!r}"
    )
    assert close_calls == [], (
        f"assertion failure: close must not be attempted without an ensured milestone, "
        f"got {close_calls!r}"
    )
    assert host.emitted[-1][0] == "project.closed", (
        f"assertion failure: the audited skip is the project.closed witness, got {host.emitted!r}"
    )
    assert host.emitted[-1][1].get("state") == "skipped", (
        f"assertion failure: the skip witness records state=skipped, got {host.emitted!r}"
    )


# AC-FR0331-02@v0.10 TRACKS-TRACE IF-TRACKER-001 failed ensure stays skip
def test_close_project_ensure_failure_reports_not_found_and_skips_close(
    tmp_path, monkeypatch
):
    """IF-TRACKER-001 (interfaces 1r.5.2): a failed ensure (create issued but
    unverified) is never compensated by a blind close -- milestone_not_found
    attention, actionable next, audited skip and close not attempted."""
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    close_calls: list[tuple] = []

    def _ensure(repo_id, project, title):
        return _ensure_result(
            created=True, api_verified=False, error="readback_unconfirmed"
        )

    def _close(repo_id, project, milestone):
        close_calls.append((repo_id, project, milestone))
        return {"api_verified": True, "state": "closed"}

    monkeypatch.setattr(mc, "ensure_project_milestone", _ensure, raising=False)
    monkeypatch.setattr(mc, "close_project_milestone_api", _close)
    host = _Host(tmp_path)

    entry = host._close_milestone_project(
        _Cmd(), "T-1", dict(_TRACE), _params(), []
    )

    attentions = _attentions(host)
    assert attentions and attentions[-1].get("reason") == "milestone_not_found", (
        f"assertion failure: a failed ensure reports milestone_not_found, got {host.emitted!r}"
    )
    attention = attentions[-1]
    assert attention.get("area") == "project_close", (
        f"assertion failure: attention area is project_close, got {attention!r}"
    )
    next_text = str(attention.get("next") or "")
    assert "release v0.10" in next_text and "trac run --resume" in next_text, (
        f"assertion failure: next must be actionable (title + resume), got {next_text!r}"
    )
    assert entry.get("state") == "skipped" and entry.get("api_verified") is not True, (
        f"assertion failure: a failed ensure keeps the audited skip, got {entry!r}"
    )
    assert close_calls == [], (
        f"assertion failure: close must not run without an ensured milestone, got {close_calls!r}"
    )
    assert host.emitted[-1][0] == "project.closed", (
        f"assertion failure: the skip still lands the project.closed witness, got {host.emitted!r}"
    )
