"""Baseline residency scoping + NEEDS_ATTENTION reconcile exit.

Operator findings (2026-08-24, run 01M0S0FQ, v0.7 self-hosting):

1. ``_last_baseline_digest`` must scope to the CURRENT M-IMPL residency: a
   rollback (stub_gap) through M-DESIGN re-approval legitimately moves the
   tree between M-IMPL residencies; freezes from the abandoned cycle must
   not poison the stale-guard reference (the guard exists for a branch
   advancing WHILE the run is resident in M-IMPL).
2. ``human.retry`` at M-IMPL/NEEDS_ATTENTION re-enters BASELINE so decide()
   re-freezes after the operator reconcile confirmation.
"""

from __future__ import annotations

from pathlib import Path

from tests.unit.helpers import seq
from tracks.executor.m_impl_runtime import MImplRuntimeMixin as _GateMixin
from tracks.kernel import project
from tracks.store import Store


class _Gate(_GateMixin):
    """Bare instance carrying only what _last_baseline_digest touches."""

    def __init__(self, store, run_id):
        self.store = store
        self.run_id = run_id
        self.repo = Path(".")


_RUN = "RUN"


def _store_with(events, tmp_path):
    store = Store(tmp_path / ".tracks")
    store.append(_RUN, "v0.7", "stage.entered", {"stage": "M-IMPL"})
    for ev_type, payload in events:
        store.append(_RUN, "v0.7", ev_type, payload)
    return store


def _gate(store):
    return _Gate(store, _RUN)


def test_baseline_digest_scoped_to_current_residency(tmp_path):
    """A freeze from an abandoned M-IMPL cycle (before the latest
    stage.entered M-IMPL) is NOT the reconcile reference; a stale park
    freeze inside this residency is a conflict observation, also not a
    reference -- after the operator reconcile the re-freeze anchors to
    reality with no prior reference in the residency."""
    store = _store_with(
        [
            ("baseline.frozen", {"status": "current", "digest": "D-abandoned"}),
            ("stage.rolled_back", {"from_stage": "M-IMPL", "to_stage": "M-DESIGN"}),
            ("stage.entered", {"stage": "M-DESIGN"}),
            ("stage.exited", {"stage": "M-DESIGN"}),
            ("stage.entered", {"stage": "M-TEST"}),
            ("stage.exited", {"stage": "M-TEST"}),
            ("stage.entered", {"stage": "M-IMPL"}),
            ("baseline.frozen", {"status": "stale", "digest": "D-park"}),
            ("human.retry", {"actor": "Maestro", "clear_evidence": True}),
        ],
        tmp_path,
    )
    assert _gate(store)._last_baseline_digest() == ""


def test_baseline_digest_current_freeze_in_residency_is_reference(tmp_path):
    """A CURRENT freeze within this residency stays the reference (the
    scenario-B mid-residency branch-advance guard keeps its teeth)."""
    store = _store_with(
        [
            ("baseline.frozen", {"status": "current", "digest": "D-live"}),
            ("baseline.frozen", {"status": "stale", "digest": "D-park"}),
        ],
        tmp_path,
    )
    assert _gate(store)._last_baseline_digest() == "D-live"


def test_baseline_digest_fresh_residency_has_no_reference(tmp_path):
    """A fresh M-IMPL residency with no freeze inside it references '' (not
    the abandoned cycle's digest)."""
    store = _store_with(
        [
            ("baseline.frozen", {"status": "current", "digest": "D-abandoned"}),
            ("stage.rolled_back", {"from_stage": "M-IMPL", "to_stage": "M-DESIGN"}),
            ("stage.entered", {"stage": "M-DESIGN"}),
            ("stage.exited", {"stage": "M-DESIGN"}),
            ("stage.entered", {"stage": "M-IMPL"}),
        ],
        tmp_path,
    )
    assert _gate(store)._last_baseline_digest() == ""


def test_human_retry_exits_needs_attention_to_baseline():
    """retry at M-IMPL/NEEDS_ATTENTION re-enters BASELINE (reconcile
    confirmation); decide() then issues freeze_baseline again."""
    s = project(seq(
            ("story.requested", {"raw_chars": 5}),
            ("stage.entered", {"stage": "M-IMPL"}),
            ("command.issued", {"command": {"kind": "freeze_baseline", "params": {}, "command_id": "C0"}}),
            ("baseline.frozen", {"status": "stale"}),
            ("human.retry", {"actor": "Maestro"}),
        ))
    assert s.substate == "BASELINE"
    from tracks.kernel import decide

    cmd = decide(s)
    assert cmd is not None and cmd.kind == "freeze_baseline"


def test_human_retry_outside_needs_attention_keeps_substate():
    """Ordinary retry elsewhere does not touch the substate (regression
    guard for the targeted exit)."""
    s = project(seq(
            ("story.requested", {"raw_chars": 5}),
            ("stage.entered", {"stage": "M-IMPL"}),
            ("command.issued", {"command": {"kind": "freeze_baseline", "params": {}, "command_id": "C0"}}),
            ("baseline.frozen", {"status": "current"}),
            ("command.issued", {"command": {"kind": "commit_taskgraph", "params": {}, "command_id": "C1"}}),
            ("taskgraph.committed", {"task_count": 1, "digest": "g", "tasks": []}),
            ("human.retry", {"actor": "Maestro"}),
        ))
    assert s.substate == "ISLAND_GATE_1"
