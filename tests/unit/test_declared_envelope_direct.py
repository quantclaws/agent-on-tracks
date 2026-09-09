"""Unit tests: Executor._format_error_shortcircuit declared-envelope slice.

Bounded envelope collection fix (T-024 collection face): when a dispatch
assignment declares the kernel envelope, every non-parsable reply shape is a
classified ``format_error`` handled at collection time — empty, missing and
non-string ``raw_output`` and free-form JSON included — and never a semantic
attempt (no attempt consumption, no success/business mutation). Undeclared
dispatches keep the legacy channels entirely.

Tests drive a real Executor over a real SQLite Store in a temp repo, with the
concrete assignment declaration built by ``build_assignment_envelope`` (the
exact structure the injection face ``_enrich_envelope_params`` materializes).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tracks.executor.executor import Executor
from tracks.kernel.envelope import (
    ENVELOPE_VERSION,
    EnvelopeFormatError,
    build_assignment_envelope,
    parse_agent_output,
)
from tracks.kernel.events import Command
from tracks.store import Store

RUN_ID = "run-envelope-unit-0001"
TASK_ID = "T-7"
CMD_ID = "cmd-envelope-0001"


def _declared_assignment(kind: str = "prism:review", task_id: str = TASK_ID) -> dict:
    """Concrete assignment declaration, exactly as the injection face builds it."""
    task = {"task_id": task_id}
    return {"task": task, "envelope": build_assignment_envelope(kind, task)}


def _envelope_block(payload: dict, kind: str = "prism:review") -> str:
    envelope = {
        "envelope": {"kind": kind, "version": ENVELOPE_VERSION},
        "payload": payload,
    }
    return (
        "```tracks-envelope\n"
        + json.dumps(envelope, sort_keys=True)
        + "\n```\n"
    )


@pytest.fixture()
def exec_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Real Store in a temp home + Executor over a temp (non-git) repo."""
    monkeypatch.setenv("TRAC_AGENT_BACKEND", "fake")
    store = Store(tmp_path / "store")
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    ex = Executor(store, repo, RUN_ID)
    try:
        yield ex, store
    finally:
        store.close()


def _cmd() -> Command:
    return Command(kind="dispatch_agent", params={}, command_id=CMD_ID)


def _run_events(store: Store) -> list:
    return list(store.events(RUN_ID))


def _assert_only_format_error(
    store: Store, expected_kind: str, task_id: str = TASK_ID
) -> None:
    """The call must be handled: exactly one classified format_error event,
    command/task identity preserved, and nothing else appended (no
    outcome.received, no verdict.*, no attempt/business mutation)."""
    events = _run_events(store)
    assert len(events) == 1, [e.type for e in events]
    ev = events[0]
    assert ev.type == "format_error"
    assert ev.command_id == CMD_ID
    assert ev.task_id == task_id
    assert ev.payload["kind"] == expected_kind
    assert ev.payload["task_id"] == task_id
    assert ev.payload["detail"]


# ---------------------------------------------------------------------------
# NEW declared-dispatch contract (RED → GREEN slice)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected_kind"),
    [
        ("", "no_envelope_block"),
        (None, "malformed_json"),
        (42, "malformed_json"),
        ({"verdict": "pass"}, "malformed_json"),
        ('{"verdict": "pass", "review_body": "x"}', "no_envelope_block"),
    ],
    ids=["empty", "missing", "non-string-int", "non-string-dict", "freeform-json"],
)
def test_declared_broken_raw_output_is_handled_format_error(
    exec_env, raw, expected_kind
):
    ex, store = exec_env
    assignment = _declared_assignment()
    result = {"raw_output": raw}
    handled = ex._format_error_shortcircuit(result, _cmd(), TASK_ID, assignment)
    assert handled is True
    _assert_only_format_error(store, expected_kind)


def test_declared_missing_raw_output_key_is_handled_format_error(exec_env):
    ex, store = exec_env
    assignment = _declared_assignment()
    handled = ex._format_error_shortcircuit(
        {"status": "done"}, _cmd(), TASK_ID, assignment
    )
    assert handled is True
    _assert_only_format_error(store, "malformed_json")


def test_declared_freeform_json_json_object_reply_is_format_error(exec_env):
    """A bare JSON object (no fenced block) is a compliance miss, classified
    by the kernel parser — not a guess passed to legacy channels."""
    ex, store = exec_env
    assignment = _declared_assignment()
    raw = json.dumps({"verdict": "pass", "review_summary": "fine"})
    handled = ex._format_error_shortcircuit(
        {"raw_output": raw}, _cmd(), TASK_ID, assignment
    )
    assert handled is True
    _assert_only_format_error(store, "no_envelope_block")


# ---------------------------------------------------------------------------
# Existing valid-payload handling unchanged
# ---------------------------------------------------------------------------


def test_declared_valid_payload_falls_through_with_enrichment(exec_env):
    ex, store = exec_env
    assignment = _declared_assignment()
    result = {"raw_output": _envelope_block({"verdict": "pass"})}
    handled = ex._format_error_shortcircuit(result, _cmd(), TASK_ID, assignment)
    assert handled is False
    assert result["envelope"]["envelope"]["kind"] == "prism:review"
    assert _run_events(store) == []  # no format_error for a valid reply


def test_declared_kind_mismatch_still_fails_closed(exec_env):
    ex, store = exec_env
    assignment = _declared_assignment(kind="prism:review")
    result = {"raw_output": _envelope_block({"verdict": "pass"}, kind="prism:diagnose")}
    handled = ex._format_error_shortcircuit(result, _cmd(), TASK_ID, assignment)
    assert handled is True
    _assert_only_format_error(store, "schema_violation")


def test_declared_multiple_blocks_still_fails_closed(exec_env):
    ex, store = exec_env
    assignment = _declared_assignment()
    raw = _envelope_block({"verdict": "pass"}) + _envelope_block(
        {"verdict": "pass"}
    )
    result = {"raw_output": raw}
    handled = ex._format_error_shortcircuit(result, _cmd(), TASK_ID, assignment)
    assert handled is True
    _assert_only_format_error(store, "multiple_envelope_blocks")


# ---------------------------------------------------------------------------
# Undeclared legacy behavior unchanged
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw",),
    [("",), (None,), (42,), ('{"verdict": "pass"}',)],
    ids=["empty", "missing-value-none", "non-string", "freeform-json"],
)
def test_undeclared_broken_raw_output_keeps_legacy_fallthrough(exec_env, raw):
    ex, store = exec_env
    result = {"raw_output": raw}
    handled = ex._format_error_shortcircuit(result, _cmd(), TASK_ID, None)
    assert handled is False
    assert _run_events(store) == []


def test_undeclared_freeform_json_falls_through_no_format_error(exec_env):
    ex, store = exec_env
    result = {
        "raw_output": json.dumps({"verdict": "pass"}),
        "status": "done",
    }
    handled = ex._format_error_shortcircuit(
        result, _cmd(), TASK_ID, assignment={"task": {"task_id": TASK_ID}}
    )
    assert handled is False
    assert _run_events(store) == []


def test_undeclared_envelope_none_is_not_declared(exec_env):
    ex, store = exec_env
    result = {"raw_output": ""}
    handled = ex._format_error_shortcircuit(
        result, _cmd(), TASK_ID, assignment={"envelope": None}
    )
    assert handled is False
    assert _run_events(store) == []


def test_undeclared_valid_string_reply_still_enriches(exec_env):
    """Legacy enrichment of a parseable reply without a declaration is kept."""
    ex, store = exec_env
    result = {"raw_output": _envelope_block({"verdict": "pass"})}
    handled = ex._format_error_shortcircuit(
        result, _cmd(), TASK_ID, assignment={"task": {"task_id": TASK_ID}}
    )
    assert handled is False
    assert result["envelope"]["envelope"]["kind"] == "prism:review"
    assert _run_events(store) == []


# ---------------------------------------------------------------------------
# Kernel parser classification backing the fixture expectations
# ---------------------------------------------------------------------------


def test_parser_classifies_non_string_and_empty_replies():
    with pytest.raises(EnvelopeFormatError) as non_str:
        parse_agent_output(None)
    assert non_str.value.kind == "malformed_json"
    with pytest.raises(EnvelopeFormatError) as empty:
        parse_agent_output("")
    assert empty.value.kind == "no_envelope_block"
