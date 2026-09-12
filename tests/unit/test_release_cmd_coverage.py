"""Behavior coverage for ``tracks.cli.release_cmd``: publish-plan indexing,
stale-evidence staling, release status/attention fragments, argument parsing
and the fail-closed ``cmd_release`` entry points.
"""

from __future__ import annotations

import contextlib
import sys
from types import SimpleNamespace

from tracks.cli import release_cmd as rc


def _ev(seq: int, etype: str, payload=None, command_id=None):
    return SimpleNamespace(
        seq=seq, type=etype, payload=payload or {}, command_id=command_id
    )


class _Store:
    def __init__(self, events=(), state=None):
        self.events_list = list(events)
        self.appended: list = []
        self._state = state or SimpleNamespace(version="v0.8")

    def events(self, _run_id):
        return list(self.events_list)

    def append(self, run_id, version, etype, payload):
        self.appended.append((etype, payload))
        self.events_list.append(_ev(len(self.events_list) + 1, etype, payload))

    def state(self, _run_id):
        return self._state


# -- plan index / staling ---------------------------------------------------


def test_publish_plan_index_keys_command_id_and_payload():
    events = [
        _ev(
            1,
            "publish.planned",
            {"operation_kind": "tag", "target": "v1"},
            command_id="CMD-1",
        ),
        _ev(
            2,
            "publish.planned",
            {"idempotency_key": "key-2", "kind": "merge", "target": "main"},
        ),
        _ev(3, "publish.planned", {}),
    ]
    plans = rc._publish_plan_index(_Store(events), "RUN")
    assert plans["CMD-1"]["operation_kind"] == "tag"
    assert plans["key-2"]["operation_kind"] == "merge"
    assert plans["key-2"]["target"] == "main"
    assert "" not in plans


def test_stale_downstream_evidence_appends_present_buckets():
    store = _Store(
        [
            _ev(1, "candidate.frozen"),
            _ev(2, "release.previewed"),
            _ev(3, "release.previewed"),
        ]
    )
    count = rc._stale_downstream_evidence(store, "RUN", "v0.8", "M-RELEASE")
    assert count == 2
    types = [payload["type"] for etype, payload in store.appended]
    assert types == ["candidate.frozen", "release.previewed"]
    assert store.appended[0][1]["source_seq"] == 1
    assert store.appended[1][1]["source_seq"] == 3


# -- argument parsing / target validation -----------------------------------


def test_parse_release_args_error_and_success_shapes():
    assert rc._parse_release_args(("preview",)) == ("preview", None, None)
    assert rc._parse_release_args(("--action",)) is None
    assert rc._parse_release_args(("--action", "release", "--bogus", "x")) is None
    assert rc._parse_release_args(("--action", "release", "--reason")) is None
    assert rc._parse_release_args(("--action", "delay")) == ("delay", None, None)
    assert rc._parse_release_args(("--action", "release", "--to", "M-TEST")) is None
    assert rc._parse_release_args(
        ("--action", "return", "--to", "M-DESIGN", "--reason", "r")
    ) == ("return", "r", "M-DESIGN")


def test_validate_return_target_and_request():
    assert rc._validate_return_target(None) is None
    assert isinstance(rc._validate_return_target("NOT-A-STAGE"), int)
    assert rc._validate_return_target("M-DESIGN") is None

    request = rc._release_request(("--action", "delay", "--reason", "r"))
    assert isinstance(request, rc._ReleaseRequest)
    assert (request.action, request.reason, request.target) == ("delay", "r", None)
    assert isinstance(rc._release_request(("--bogus",)), int)


# -- staleness / gate checks ------------------------------------------------


def test_release_preview_stale_reason_markers():
    preview = _ev(1, "release.previewed", {"candidate_sha": "a"})
    assert rc._release_preview_stale_reason([preview], preview) is None
    assert rc._release_preview_stale_reason(
        [preview, _ev(2, "candidate.stale")], preview
    ) == "candidate_drift"
    assert rc._release_preview_stale_reason(
        [preview, _ev(2, "evidence.staled")], preview
    ) == "evidence_staled"
    assert rc._release_preview_stale_reason(
        [preview, _ev(2, "candidate.frozen", {"candidate_sha": "b"})], preview
    ) == "candidate_drift"
    assert rc._release_preview_stale_reason(
        [preview, _ev(2, "candidate.frozen", {"candidate_sha": "a"})], preview
    ) is None


def test_release_gate_blocked_and_block_reason():
    assert rc._release_gate_blocked([]) is True
    failed_security = [_ev(1, "security.assessed", {"status": "failed"})]
    assert rc._release_gate_blocked(failed_security) is True
    bad_final = [
        _ev(1, "prism.verdict", {"scope": "verify_final", "verdict": "revise"}),
    ]
    assert rc._release_gate_blocked(bad_final) is True
    good = [_ev(1, "local_gate.passed")]
    assert rc._release_gate_blocked(good) is False

    assert rc._release_block_reason(failed_security) == "security_failed"
    assert rc._release_block_reason([_ev(1, "local_gate.failed")]) == (
        "local_gate_failed"
    )
    assert rc._release_block_reason([_ev(1, "host_contract.invalid")]) == (
        "contract_invalid"
    )
    attention = [_ev(1, "attention.required", {"stage": "M-VERIFY", "reason": "parked"})]
    assert rc._release_block_reason(attention) == "parked"
    assert rc._release_block_reason([]) == "gate_evidence_missing"


def test_known_issues_and_attention_fragments():
    assert rc._known_issues_fragment(None) == ""
    registered = [_ev(1, "known_issue.registered", {"title": "KI-1"})]
    assert rc._known_issues_fragment(registered) == " known_issues={KI-1}"

    assert rc._attention_fragment(None) == ""
    attention = [_ev(1, "attention.required", {"area": "ci", "reason": "down"})]
    assert rc._attention_fragment(attention) == " attention={ci:down}"


# -- rendering / status lines -----------------------------------------------


def test_render_preview_line_without_ci():
    line = rc._render_preview_line(
        {"candidate_sha": "a", "preview_digest": "d"}, None, events=[]
    )
    assert "ci_run={}" in line
    assert "status=awaiting_release" in line
    assert "ci_run={}" in rc._render_preview_line(
        {"candidate_sha": "a", "preview_digest": "d"}, "candidate_drift"
    )


def test_release_chain_fragments_ci_and_blocked():
    lines: list = []
    events = [
        _ev(1, "ci.run_observed", {"api_verified": False, "reason": "network"}),
        _ev(2, "candidate.frozen", {"candidate_sha": "a"}),
    ]
    rc._release_chain_fragments(events, lines)
    assert "ci=network" in lines
    assert any(line.startswith("blocked: ") for line in lines)

    verified: list = []
    rc._release_chain_fragments(
        [_ev(1, "ci.run_observed", {"api_verified": True})], verified
    )
    assert "ci=bound" in verified


def test_release_status_lines_review_and_decision():
    primary = SimpleNamespace(publish_status=None, status="running", terminal_state=None, version="v0.8")
    lines = rc._release_status_lines(
        [
            _ev(1, "review.failed", {"area": "failure_evidence", "outcome": "lost"}),
            _ev(2, "release.decided", {"action": "delay", "preview_digest": "d"}),
        ],
        primary,
        None,
    )
    assert any("blocked: evidence lost or mismatched (lost)" in line for line in lines)
    assert any(line.startswith("decision=delay") for line in lines)


def test_release_status_lines_stale_preview():
    preview = _ev(1, "release.previewed", {"preview_digest": "d", "candidate_sha": "a"})
    events = [preview, _ev(2, "candidate.stale")]
    primary = SimpleNamespace(publish_status=None, status="running", terminal_state=None, version="v0.8")
    lines = rc._release_status_lines(events, primary, None)
    assert any(line.startswith("status=stale") for line in lines)


def test_attention_area_resolved_unknown_area():
    attention = _ev(1, "attention.required", {"area": "mystery"})
    assert rc._attention_area_resolved([attention], attention, "mystery") is False


def test_release_publish_lines_cancelled():
    primary = SimpleNamespace(
        publish_status=None, status="completed", terminal_state="cancelled"
    )
    assert rc._release_publish_lines([], primary) == ["terminal=cancelled"]

    blocked = rc._release_publish_lines(
        [_ev(1, "publish.blocked", {"reason": "no-token"})], primary
    )
    assert blocked[0] == "publish=blocked reason=no-token"
    assert "terminal=cancelled" in blocked


def test_release_report_snippet_preview():
    preview = _ev(1, "release.previewed", {"preview_digest": "d", "candidate_sha": "a"})
    snippet = rc._release_report_snippet([preview])
    assert snippet.startswith("release preview_digest=d")
    assert rc._release_report_snippet([]) == ""


def test_release_report_snippet_exception(monkeypatch):
    monkeypatch.setattr(
        rc,
        "_latest_release_preview",
        lambda events: (_ for _ in ()).throw(RuntimeError("bad events")),
    )
    assert rc._release_report_snippet([_ev(1, "anything")]) == ""


def test_validate_return_target_import_failure(monkeypatch):
    monkeypatch.setitem(sys.modules, "tracks.kernel.machine", None)
    assert rc._validate_return_target("M-DESIGN") is None


# -- cmd_release entry ------------------------------------------------------


def test_release_gate_none_without_preview(tmp_path):
    store = _Store([])
    assert rc._release_gate(tmp_path, tmp_path, store, "RUN") is None


def test_cmd_release_no_active_run_and_missing_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(rc.paths, "tracks_home", lambda repo: tmp_path / "home")
    monkeypatch.setattr(
        rc, "Store", lambda home: SimpleNamespace(active_run=lambda: None)
    )
    assert rc.cmd_release(tmp_path, "--action", "delay", "--reason", "r") != 0

    monkeypatch.setattr(
        rc, "Store", lambda home: SimpleNamespace(active_run=lambda: "RUN")
    )
    monkeypatch.setattr(rc, "writer_lock", lambda home: contextlib.nullcontext())
    monkeypatch.setattr(rc, "_release_gate", lambda *a: None)
    assert rc.cmd_release(tmp_path, "--action", "delay", "--reason", "r") != 0
