"""#224 (2026-10-02, Prism-adjudicated): deferred/carried refs are the
execution surface, not declared acceptance — they expand against the
inventory (fail-closed) but skip the §8 NODE cross-check (B50), which
belongs to declared anchors only. Live fire: T-005's FILE-level deferred
test_event_stream.py carries test_live_push_without_refresh, absent from
every §8 row; the flat merge convicted it at GREEN_GATE."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from tracks.executor.m_impl_testops import MImplTestOpsMixin

_INV = [
    "tests/integration/test_declared.py::test_declared_anchor_not_planned",
    "tests/integration/test_event_stream.py::test_live_push_without_refresh",
    "tests/integration/test_event_stream.py::test_reconnect_backfill_no_regression",
    "tests/integration/test_declared.py::test_declared_anchor",
]
_PLAN = (
    "## 8. AC Coverage\n\n"
    "| AC ID | Layer | Tests | IF |\n"
    "|---|---|---|---|\n"
    "| AC-FR9001-01 | integration | tests/integration/test_declared.py::test_declared_anchor | IF-X |\n"
)


class _Exec(MImplTestOpsMixin):
    repo = "/repo"
    _vdir_path = Path("/vdir")

    def _vdir(self):
        return self._vdir_path


def test_carried_file_ref_expands_without_section8(tmp_path, monkeypatch):
    (tmp_path / "test-plan.md").write_text(_PLAN, encoding="utf-8")
    task = SimpleNamespace(
        acceptance_refs=["tests/integration/test_declared.py::test_declared_anchor"],
        deferred_refs=["tests/integration/test_event_stream.py"],
        integration=False,
        schema=2,
    )
    exec_ = _Exec()
    exec_._vdir_path = tmp_path
    monkeypatch.setattr(exec_, "_effective_refs_for_gate", lambda t: [
        "tests/integration/test_declared.py::test_declared_anchor",
        "tests/integration/test_event_stream.py",
    ])
    nodes = exec_._gate_acceptance_nodes(task, {"integration": _INV})
    assert "tests/integration/test_event_stream.py::test_live_push_without_refresh" in nodes
    assert "tests/integration/test_event_stream.py::test_reconnect_backfill_no_regression" in nodes
    assert "tests/integration/test_declared.py::test_declared_anchor" in nodes


def test_declared_anchor_still_section8_checked(tmp_path, monkeypatch):
    (tmp_path / "test-plan.md").write_text(_PLAN, encoding="utf-8")
    task = SimpleNamespace(
        acceptance_refs=["tests/integration/test_declared.py::test_declared_anchor_not_planned"],
        deferred_refs=[],
        integration=False,
        schema=2,
    )
    exec_ = _Exec()
    exec_._vdir_path = tmp_path
    monkeypatch.setattr(exec_, "_effective_refs_for_gate", lambda t: [
        "tests/integration/test_declared.py::test_declared_anchor_not_planned",
    ])
    try:
        exec_._gate_acceptance_nodes(task, {"integration": _INV})
        raised = None
    except Exception as exc:  # TestSelectError expected
        raised = exc
    assert raised is not None and "not a test-plan" in str(raised)


def test_carried_missing_file_still_fails_closed(tmp_path, monkeypatch):
    (tmp_path / "test-plan.md").write_text(_PLAN, encoding="utf-8")
    task = SimpleNamespace(
        acceptance_refs=[],
        deferred_refs=["tests/integration/test_ghost.py"],
        integration=False,
        schema=2,
    )
    exec_ = _Exec()
    exec_._vdir_path = tmp_path
    monkeypatch.setattr(exec_, "_effective_refs_for_gate", lambda t: [
        "tests/integration/test_ghost.py",
    ])
    try:
        exec_._gate_acceptance_nodes(task, {"integration": _INV})
        raised = None
    except Exception as exc:
        raised = exc
    assert raised is not None and "absent from integration collect" in str(raised)
