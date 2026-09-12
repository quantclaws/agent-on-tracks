"""Behavior coverage for the M-MILESTONE closing tail (``milestone_chain``):

itemized issue/project closer fail-closed branches, trace reuse, seal/refs
stall guards and the complete/closed idempotency witnesses.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from tracks.effects.github import GithubIssuesError
from tracks.executor import milestone_chain as mc
from tracks.executor.milestone_chain import ExecMilestoneMixin


class _Cmd:
    command_id = "CMD-CLOSE"
    params: dict = {}


def _ev(seq: int, etype: str, payload: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(seq=seq, type=etype, payload=payload or {})


class _Host(ExecMilestoneMixin):
    def __init__(self, tmp_path: Path, events=()):
        self.repo = tmp_path / "repo"
        self.repo.mkdir(parents=True, exist_ok=True)
        self.run_id = "RUN"
        self.events = list(events)
        self.emitted: list = []
        self.store = SimpleNamespace(events=lambda _run: list(self.events))

    def _emit(self, event, payload=None, **kwargs):
        self.emitted.append((event, payload, kwargs))
        self.events.append(_ev(len(self.events) + 1, event, payload))

    def _load_authoritative_issue_map(self):
        return {}


def test_do_close_milestone_returns_without_trace_and_on_stalls(tmp_path):
    host = _Host(tmp_path)
    host._milestone_trace = lambda *_a: {}
    host._do_close_milestone(_Cmd(), None, "T-1", False)
    assert host.emitted == []

    host = _Host(tmp_path)
    host._milestone_trace = lambda *_a: {"trace_digest": "td"}
    host._close_milestone_issues = lambda *_a: None
    host._close_milestone_project = lambda *_a: {"state": "closed"}
    host._emit_milestone_closed = lambda *_a: None
    host._seal_milestone = lambda *_a: False
    host._complete_milestone = lambda *_a: host.emitted.append(("completed", {}, {}))
    host._do_close_milestone(_Cmd(), None, "T-1", False)
    assert host.emitted == []

    host = _Host(tmp_path)
    host._milestone_trace = lambda *_a: {"trace_digest": "td"}
    host._close_milestone_issues = lambda *_a: None
    host._close_milestone_project = lambda *_a: {"state": "closed"}
    host._emit_milestone_closed = lambda *_a: None
    host._seal_milestone = lambda *_a: True
    host._clean_milestone_refs = lambda *_a: False
    host._complete_milestone = lambda *_a: host.emitted.append(("completed", {}, {}))
    host._do_close_milestone(_Cmd(), None, "T-1", False)
    assert host.emitted == []


def test_milestone_trace_reuses_landed_event(tmp_path):
    event = _ev(3, "milestone.trace_closed", {"trace_digest": "existing"})
    host = _Host(tmp_path, [event])
    assert host._milestone_trace(_Cmd(), "T-1", [event], "sha") == {
        "trace_digest": "existing"
    }


def test_close_milestone_issues_skips_done_and_emits_rest(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setattr(
        mc,
        "close_issues_with_comment",
        lambda *a, **k: [
            {"issue_number": 1, "state": "closed"},
            {"issue_number": 2, "state": "closed"},
        ],
    )
    monkeypatch.setattr(
        mc,
        "skipped_issue_entries",
        lambda *a, **k: [{"issue_number": 3, "state": "skipped"}],
    )
    host._close_milestone_issues(
        _Cmd(), "T-1", {"trace_digest": "td"}, {"i": {"issue_number": 9}},
        [], [1],
    )
    assert [payload["issue_number"] for _, payload, _ in host.emitted] == [2, 3]


def _closer(host, monkeypatch, result=None, error=None):
    if error is not None:

        def boom(*_a, **_k):
            raise error

        monkeypatch.setattr(mc, "close_issue", boom)
    else:
        monkeypatch.setattr(mc, "close_issue", lambda *a, **k: dict(result or {}))
    return host._real_issue_closer(_Cmd(), "T-1", {"trace_digest": "td", "candidate_sha": "c"})


def test_real_issue_closer_verified_close(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setenv("TRAC_GITHUB_REPO", "acme/host")
    closer = _closer(
        host,
        monkeypatch,
        result={"api_verified": True, "state": "closed", "comment_id": 7},
    )
    entry = closer({"repo": "", "issue_number": 5}, {})
    assert entry["state"] == "closed"
    assert entry["comment_ref"] == "comment:5:7"
    assert host.emitted == []


def test_real_issue_closer_github_error_and_malformed(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    closer = _closer(
        host, monkeypatch, error=GithubIssuesError("auth", "bad token")
    )
    entry = closer({"repo": "acme/host", "issue_number": 5}, {})
    assert entry["state"] == "skipped"
    assert entry["reason"] == "auth: bad token"
    assert host.emitted[-1][0] == "attention.required"

    malformed = closer({"repo": "acme/host", "issue_number": None}, {})
    assert malformed["state"] == "skipped"
    assert malformed["reason"].startswith("malformed_issue_number")


def test_close_milestone_project_existing_event(tmp_path):
    host = _Host(tmp_path)
    event = _ev(2, "project.closed", {"state": "closed", "milestone": "M1"})
    assert host._close_milestone_project(
        _Cmd(), "T-1", {"trace_digest": "td"}, {}, [event]
    ) == {"state": "closed", "milestone": "M1"}


def test_close_milestone_project_authoritative_success(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setattr(
        mc,
        "close_project_milestone",
        lambda repo, tracker, trace, closer=None: closer(tracker, {}),
    )
    monkeypatch.setattr(
        mc, "close_project_milestone_api", lambda *a, **k: {"api_verified": True, "state": "closed"}
    )
    entry = host._close_milestone_project(
        _Cmd(),
        "T-1",
        {"trace_digest": "td"},
        {"tracker": {"repo": "acme/host", "milestone": "M1", "project": "P"}},
        [],
    )
    assert entry == {"state": "closed", "remote_state": "closed", "api_verified": True}
    assert host.emitted[-1][0] == "project.closed"


def test_close_milestone_project_authoritative_error(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setattr(
        mc,
        "close_project_milestone",
        lambda repo, tracker, trace, closer=None: closer(tracker, {}),
    )
    monkeypatch.setattr(
        mc,
        "close_project_milestone_api",
        lambda *a, **k: (_ for _ in ()).throw(GithubIssuesError("network", "down")),
    )
    entry = host._close_milestone_project(
        _Cmd(),
        "T-1",
        {"trace_digest": "td"},
        {"tracker": {"repo": "acme/host", "milestone": "M1"}},
        [],
    )
    assert entry["state"] == "skipped"
    assert entry["reason"] == "network: down"
    assert any(e[0] == "attention.required" for e in host.emitted)


def test_close_milestone_project_not_authoritative(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setattr(mc, "close_project_milestone", lambda *a, **k: {"state": "closed"})
    entry = host._close_milestone_project(_Cmd(), "T-1", {"trace_digest": "td"}, {}, [])
    assert entry["state"] == "skipped"
    assert entry["reason"] == "not_authoritative"


def test_emit_milestone_closed_idempotent(tmp_path):
    host = _Host(tmp_path)
    host._emit_milestone_closed(
        _Cmd(), "T-1", {"trace_digest": "t"}, {}, [_ev(1, "milestone.closed")]
    )
    assert host.emitted == []


def test_seal_milestone_already_sealed_and_failed(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    sealed = _ev(1, "milestone.sealed", {"readonly": True})
    assert host._seal_milestone(_Cmd(), "T-1", {}, [sealed], "c") is True

    host = _Host(tmp_path)
    monkeypatch.setattr(
        mc, "seal_evidence_readonly", lambda *a, **k: {"readonly": False, "error": "ro"}
    )
    attention = _ev(2, "attention.required", {"area": "milestone_seal"})
    assert host._seal_milestone(_Cmd(), "T-1", {}, [attention], "c") is False
    assert host.emitted == []

    host = _Host(tmp_path)
    assert host._seal_milestone(_Cmd(), "T-1", {}, [], "c") is False
    assert host.emitted[-1][0] == "attention.required"


def test_clean_milestone_refs_paths(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    cleaned = _ev(1, "refs.cleaned", {"remaining": 0})
    assert host._clean_milestone_refs(_Cmd(), "T-1", [cleaned]) is True

    monkeypatch.setattr(
        mc,
        "clean_temp_refs",
        lambda *a, **k: {"remaining": 1, "remaining_refs": ["refs/trac/tmp/x"]},
    )
    host = _Host(tmp_path)
    stuck = _ev(1, "refs.cleaned", {"remaining_refs": ["refs/trac/tmp/x"]})
    assert host._clean_milestone_refs(_Cmd(), "T-1", [stuck]) is False
    assert host.emitted == []

    host = _Host(tmp_path)
    assert host._clean_milestone_refs(_Cmd(), "T-1", []) is False
    assert [e[0] for e in host.emitted] == ["refs.cleaned", "attention.required"]


def test_complete_milestone_idempotent(tmp_path):
    host = _Host(tmp_path)
    host._complete_milestone(
        _Cmd(), "T-1", {"release_tag": "v1"}, [_ev(1, "run.completed")]
    )
    assert host.emitted == []

    host = _Host(tmp_path)
    host._complete_milestone(_Cmd(), "T-1", {"release_tag": "v1"}, [])
    payload = host.emitted[-1][1]
    assert payload["terminal_state"] == "released"
    assert payload["release_tag"] == "v1"
