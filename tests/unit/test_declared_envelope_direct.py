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

from tracks.effects.dispatch_parity import (
    envelope_declaration_error,
    is_declared,
    static_parity_check,
)
from tracks.effects.fake import FakeBackend
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


def _assert_format_error_routed(
    store: Store, expected_kind: str, task_id: str = TASK_ID
) -> None:
    """The call must be handled: the classified format_error event plus the
    routable verdict.failed (reply_format_error) — every declared kind
    (devon/shield/archer/prism, non-DIAGNOSE) routes, so the stage's dispatch
    flag can never strand (T-042 writer stall + 2026-09-19 PRISM_RED: plain
    prism review kinds fell through both branches and parked the run)."""
    events = _run_events(store)
    kinds = [e.type for e in events]
    assert kinds == ["format_error", "verdict.failed"], kinds
    fe, vf = events
    assert fe.command_id == CMD_ID
    assert fe.task_id == task_id
    assert fe.payload["kind"] == expected_kind
    assert fe.payload["task_id"] == task_id
    assert fe.payload["detail"]
    assert vf.command_id == CMD_ID
    assert vf.task_id == task_id
    assert vf.payload["check"] == "reply_format_error"
    assert vf.payload["task_id"] == task_id


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
    _assert_format_error_routed(store, expected_kind)


def test_declared_missing_raw_output_key_is_handled_format_error(exec_env):
    ex, store = exec_env
    assignment = _declared_assignment()
    handled = ex._format_error_shortcircuit(
        {"status": "done"}, _cmd(), TASK_ID, assignment
    )
    assert handled is True
    _assert_format_error_routed(store, "malformed_json")


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
    _assert_format_error_routed(store, "no_envelope_block")


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
    _assert_format_error_routed(store, "schema_violation")


def test_declared_prism_red_kind_routes_verdict_to_unstrand_reviewer(exec_env):
    """2026-09-19 (run 01M2QTJB PRISM_RED): plain prism review kinds must emit
    the routable verdict.failed — format_error alone left reviewer_dispatched
    set and decide() parked the run forever after the review reply failed
    classification (the exact B62 #80 stall class). The bare-payload-in-fence
    reply below is the live failure shape (missing_kind)."""
    ex, store = exec_env
    assignment = _declared_assignment(kind="prism:red")
    handled = ex._format_error_shortcircuit(
        {"raw_output": "```tracks-envelope\n{\"verdict\": \"pass\"}\n```"},
        _cmd(),
        TASK_ID,
        assignment,
    )
    assert handled is True
    _assert_format_error_routed(store, "missing_kind")


def test_declared_multiple_blocks_still_fails_closed(exec_env):
    ex, store = exec_env
    assignment = _declared_assignment()
    raw = _envelope_block({"verdict": "pass"}) + _envelope_block(
        {"verdict": "pass"}
    )
    result = {"raw_output": raw}
    handled = ex._format_error_shortcircuit(result, _cmd(), TASK_ID, assignment)
    assert handled is True
    _assert_format_error_routed(store, "multiple_envelope_blocks")


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


# ---------------------------------------------------------------------------
# Wrong-typed envelope declarations fail closed (truthy malformed)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "envelope",
    ["junk", [], 2, True],
    ids=["string", "empty-list", "int", "bool"],
)
def test_wrong_typed_envelope_declaration_is_malformed(envelope):
    assignment = {"envelope": envelope, "envelope_version": ENVELOPE_VERSION}
    error = envelope_declaration_error(assignment)
    assert error is not None
    assert error["face"] == "assignment"
    assert error["reason"] == "malformed"
    assert "object" in error["detail"]

    verdict = static_parity_check(assignment)
    assert verdict["consistent"] is False
    assert any(m["reason"] == "malformed" for m in verdict["mismatches"])


def test_truthy_malformed_envelope_never_passes_via_legacy_copy():
    """The original defect: a truthy wrong-typed declaration fell back to a
    matching legacy envelope_version and passed the static gate."""
    assignment = {"envelope": "junk", "envelope_version": ENVELOPE_VERSION}
    verdict = static_parity_check(assignment)
    assert verdict["consistent"] is False
    assert all(m["face"] == "assignment" for m in verdict["mismatches"])


@pytest.mark.parametrize(
    "assignment",
    [
        None,
        {},
        {"task": {"task_id": TASK_ID}},
        {"envelope": None},
        {"envelope": None, "envelope_version": ENVELOPE_VERSION},
    ],
    ids=["none", "empty", "task-only", "envelope-none", "envelope-none-legacy"],
)
def test_missing_or_none_envelope_keeps_staged_semantics(assignment):
    assert envelope_declaration_error(assignment) is None
    assert static_parity_check(assignment)["consistent"] is True


def test_env_declare_0_reverts_to_undeclared_legacy_dispatch(exec_env, monkeypatch):
    """``TRAC_ENVELOPE_DECLARE=0`` is the explicit rollout opt-out: the
    injection face declares nothing, the static gate keeps the staged
    (PARITY_STAGED) legacy semantics and only the minimal legacy
    ``dispatch.parity`` record lands — no full declared-dispatch audit and no
    parity evidence on the result."""
    ex, store = exec_env
    monkeypatch.setenv("TRAC_ENVELOPE_DECLARE", "0")
    params = {
        "role": "prism",
        "substate": "PRISM_REVIEW",
        "assignment": {"task": {"task_id": TASK_ID}},
    }
    ex._enrich_envelope_params(params, "cmd-test-declare-off")
    assignment = params["assignment"]
    assert "envelope" not in assignment
    assert assignment.get("envelope_version") is None
    assert is_declared(assignment) is False
    assert static_parity_check(assignment)["consistent"] is True

    # The fake backend keeps the legacy result shape: no encoded reply and no
    # parity evidence ("absent_not_invented") on an undeclared dispatch.
    result = FakeBackend(ex.repo, "v0.5.0").act(
        "prism", "PRISM_REVIEW", None, None, assignment=assignment
    )
    assert "raw_output" not in result
    assert "parity" not in result

    # Static gate: undeclared -> the legacy minimal record is preserved ...
    assert ex._dispatch_parity_ok(_cmd(), TASK_ID, assignment) is True
    parity = [ev for ev in _run_events(store) if ev.type == "dispatch.parity"]
    assert len(parity) == 1
    assert parity[0].payload["task_id"] == TASK_ID
    assert "referenced" in parity[0].payload
    assert "manifest" not in parity[0].payload
    # ... and the declared-dispatch full-gate success emitter stays a no-op
    # (no second, manifest-carrying event).
    ex._emit_dispatch_parity_success(result, _cmd(), TASK_ID, assignment)
    parity = [ev for ev in _run_events(store) if ev.type == "dispatch.parity"]
    assert len(parity) == 1
    assert "manifest" not in parity[0].payload
