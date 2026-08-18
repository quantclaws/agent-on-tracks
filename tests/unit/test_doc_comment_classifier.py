"""Unit tests for the B26a/#28 classifier fix: reply-only discussion deltas.

Live evidence (run 01M0AMKV, 2026-08-18): Shield's replies to Prism's
pre-existing test-plan.md threads were classified ``legal_discussion`` with
empty thread_ids, pausing the outcome in SM-02 DETECTED forever (the
adjudication dispatch is not wired — #28). Per flow.md §10.4 replies to old
threads must not trigger interception at all.
"""

from __future__ import annotations

from tracks.executor.doc_comment import classify_design_document_deltas

# Canonical writer shapes (discuss/writer.py format_root): an OPEN root
# carries NO status tag; resolving adds " [RESOLVED]" to the same root.
_BASELINE = (
    "# Test Plan\n\n## 1\n\n"
    "> **Prism:** finding one\n"
    ">> **Shield:** reply to finding one\n"
)

_REPLY_ONLY = _BASELINE + ">> **Shield:** second reply, same thread\n"

_NEW_THREAD = _BASELINE + (
    "\n> **Shield:** brand new root thread\n"
)

# The same thread, legally resolved by its initiator (FR-090 path).
_RESOLVED = _BASELINE.replace(
    "> **Prism:** finding one", "> **Prism [RESOLVED]:** finding one"
)


def _deltas(baseline: str, current: str):
    return classify_design_document_deltas(
        role="shield",
        baseline_documents={"test-plan.md": baseline.encode()},
        current_documents={"test-plan.md": current.encode()},
    )


def test_reply_only_delta_classifies_discussion_reply():
    deltas = _deltas(_BASELINE, _REPLY_ONLY)
    (delta,) = deltas
    assert delta.classification == "discussion_reply"
    assert delta.new_thread_ids == ()


def test_new_root_thread_classifies_legal_discussion():
    deltas = _deltas(_BASELINE, _NEW_THREAD)
    (delta,) = deltas
    assert delta.classification == "legal_discussion"
    assert delta.new_thread_ids  # the new root is identified


def test_unchanged_and_body_edit_classifications_unchanged():
    assert _deltas(_BASELINE, _BASELINE)[0].classification == "none"
    deltas = classify_design_document_deltas(
        role="shield",
        baseline_documents={"test-plan.md": b"# body\n"},
        current_documents={"test-plan.md": b"# changed body\n"},
    )
    assert deltas[0].classification == "illegal_body_edit"


# -- PRISM-B26A-R1 boundary counterexamples ------------------------------


def test_deleting_a_baseline_thread_is_illegal():
    """Whole-thread deletion must not pass as discussion_reply (fail-open
    regression PRISM-B26A-R1-01)."""
    deltas = _deltas(_REPLY_ONLY, _BASELINE)  # current drops the second reply
    assert deltas[0].classification == "illegal_body_edit"


def test_rewriting_an_existing_comment_line_is_illegal():
    current = _RESOLVED.replace("finding one", "TAMPERED TEXT")
    deltas = _deltas(_BASELINE, current)  # status flip + body rewrite
    assert deltas[0].classification == "illegal_body_edit"


def test_status_flip_cannot_smuggle_body_rewrite():
    """PRISM-B26A-R2-01 probe E: a flip that ALSO rewrites the body is
    illegal even though the flip alone is legal."""
    deltas = _deltas(_BASELINE, _RESOLVED.replace("finding one", "other text"))
    assert deltas[0].classification == "illegal_body_edit"


def test_canonical_resolve_is_discussion_reply():
    """PRISM-B26A-R2-01: the canonical writer's legal resolve (open root
    gains " [RESOLVED]") is the SAME thread — must classify
    discussion_reply, never illegal_body_edit (text-level tag stripping
    leaves whitespace drift and used to misjudge this)."""
    deltas = _deltas(_BASELINE, _RESOLVED)
    assert deltas[0].classification == "discussion_reply"
    assert deltas[0].new_thread_ids == ()
