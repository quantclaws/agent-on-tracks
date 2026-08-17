"""RED phase tests for T-018 (atomic over-reach rejection audit contract).

AC-FR0237-01: an over-reach write triggers an atomic whole-round rollback
            (no partial writes survive), visible in the dispatch failed result.
AC-FR0237-02: the rejection event (rollback="atomic" + the concrete rejected
            document paths) is rendered in the Audit section of status /
            replay / report.

Two gaps exist today:

1. ``OpencodeBackend._overreach_result`` produces a ``status=failed`` result
   with ``failure_class="over_reach"`` but WITHOUT the atomic rollback
   evidence keys (``rollback="atomic"`` and a structured ``rejected_paths``
   list). Without those keys the audit trail cannot render *which* document
   paths were rejected and *that* the rollback was atomic.

2. ``tracks.report._audit_event_lines`` has no ``outcome.rejected`` branch,
   so an atomic over-reach rejection event renders NO audit lines — the
   rejected paths are invisible in status/replay/report.

This file is intentionally **untracked** (same discipline as the T-017
test_audit_allowed_paths.py precedent): the T-018 RGR cycles gate against
``git diff --name-only r_sha -- tests/``, so plain singular-purpose unit
tests stay out of tracked modules to avoid polluting that diff.
"""

import subprocess
from types import SimpleNamespace

from tracks.effects.opencode import OpencodeBackend
from tracks.report import _audit_event_lines


def _make_backend(tmp_path) -> OpencodeBackend:
    return OpencodeBackend(tmp_path, "v0.1")


def test_overreach_result_carries_atomic_rollback_evidence(tmp_path):
    """AC-FR0237-01/02: the post-write audit failed-result for an over-reach
    must declare ``rollback="atomic"`` (whole-round rollback, no partial
    writes) and carry a structured ``rejected_paths`` list — the report and
    the audit trail consume these keys to render the rejected document paths."""
    backend = _make_backend(tmp_path)
    proc = SimpleNamespace(stdout="", stderr="", returncode=1)
    result = backend._overreach_result(
        "ref-1",
        proc,
        "prompt",
        None,
        evidence="non-discussion edit to architecture.md",
    )

    assert result.get("rollback") == "atomic", (
        "AC-FR0237-01: over-reach result must declare "
        f"rollback='atomic' (found rollback={result.get('rollback')!r})"
    )
    rejected_paths = result.get("rejected_paths")
    assert rejected_paths is not None, (
        "AC-FR0237-02: over-reach result must carry rejected_paths so the "
        "audit trail can render the rejected document paths"
    )
    assert isinstance(rejected_paths, list), (
        f"rejected_paths must be a list (found {type(rejected_paths).__name__})"
    )
    assert "architecture.md" in rejected_paths, (
        "AC-FR0237-02: evidence-recognized offending path must appear in "
        f"rejected_paths (found {rejected_paths!r})"
    )


def test_overreach_result_drives_report_rejection_render(tmp_path):
    """End-to-end contract through report: when the over-reach failed result
    is forwarded as a received outcome, the Audit section must render the
    rejection — the mandatory ``rollback='atomic'`` key (used for the event
    classification) and the rejected paths. This test builds the exact
    ``_overreach_result`` output as the received payload, so fixing the
    backend result automatically fixes this rendering contract."""
    backend = _make_backend(tmp_path)
    proc = SimpleNamespace(stdout="", stderr="", returncode=1)
    result = backend._overreach_result(
        "ref-1",
        proc,
        "prompt",
        None,
        evidence="non-discussion edit to architecture.md",
    )

    event = SimpleNamespace(
        type="outcome.received",
        seq=7,
        command_id="cmd-1",
        payload=result,
    )
    lines = _audit_event_lines(event, [event])

    assert len(lines) >= 1, (
        "AC-FR0237-02: over-reach rejection must render audit lines"
    )
    assert "over_reach" in "\n".join(lines), (
        "render must surface the over-reach failure class"
    )
    assert "atomic" in "\n".join(lines), (
        "AC-FR0237-01: render must surface the atomic rollback"
    )


def test_report_renders_outcome_rejected_events(tmp_path):
    """AC-FR0237-02: an ``outcome.rejected`` event (the executor-side atomic
    over-reach rejection carrying ``rollback='atomic'`` and concrete
    ``rejected_paths``) must render audit lines exposing the rejected paths;
    today there is no such branch in ``_audit_event_lines``."""
    event = SimpleNamespace(
        type="outcome.rejected",
        seq=9,
        command_id="cmd-2",
        payload={
            "role": "devon",
            "failure_class": "over_reach",
            "rollback": "atomic",
            "rejected_paths": ["doc/architecture.md", "tracks/effects/opencode.py"],
            "reason": "non-discussion edit to architecture.md",
        },
    )
    lines = _audit_event_lines(event, [event])

    assert len(lines) >= 1, (
        "AC-FR0237-02: outcome.rejected events must render audit lines"
    )
    rendered = "\n".join(lines)
    assert "atomic" in rendered, "rendered rejection must surface rollback='atomic'"
    assert "doc/architecture.md" in rendered, (
        "rendered rejection must surface the rejected document paths"
    )
    assert "over_reach" in rendered, (
        "rendered rejection must surface the failure class"
    )


def test_overreach_result_keeps_agent_io_capture(tmp_path):
    """Guard against the fix regressing the existing over-reach contract: the
    failed result must keep the agent IO capture (bytewise _capture_io dict)
    alongside the atomic rollback evidence."""
    backend = _make_backend(tmp_path)
    proc = subprocess.CompletedProcess(
        args=["opencode", "run"],
        returncode=1,
        stdout="plain raw stdout",
        stderr="",
    )
    result = backend._overreach_result(
        "ref-1", proc, "prompt", None, evidence="over-reach evidence"
    )
    io = result.get("agent_io")
    assert isinstance(io, dict), (
        "over-reach result must keep the agent IO capture" f" (found {type(io).__name__})"
    )
    assert io.get("stdout") == "plain raw stdout", (
        "agent_io.stdout must carry the raw capture"
    )
    assert io.get("prompt") == "prompt", (
        "agent_io.prompt must carry the dispatched prompt"
    )


def test_overreach_result_keeps_failure_class_and_self_report(tmp_path):
    """Existing contract must survive the fix: failure_class='over_reach' and
    self_report carrying the offending path (D-37 lesson) and the rollback
    notice."""
    backend = _make_backend(tmp_path)
    proc = SimpleNamespace(stdout="", stderr="", returncode=1)
    result = backend._overreach_result(
        "ref-1", proc, "prompt", None, evidence="non-discussion edit to architecture.md"
    )

    assert result["failure_class"] == "over_reach"
    assert "architecture.md" in result["self_report"], (
        "self_report must carry the offending path (D-37 lesson)"
    )
    assert "rolled back" in result["self_report"], (
        "self_report must state the agent changes rolled back"
    )
