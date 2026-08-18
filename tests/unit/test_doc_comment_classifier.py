"""Unit tests for the B26a/#28 classifier fix: reply-only discussion deltas.

Live evidence (run 01M0AMKV, 2026-08-18): Shield's replies to Prism's
pre-existing test-plan.md threads were classified ``legal_discussion`` with
empty thread_ids, pausing the outcome in SM-02 DETECTED forever (the
adjudication dispatch is not wired — #28). Per flow.md §10.4 replies to old
threads must not trigger interception at all.
"""

from __future__ import annotations

from tracks.executor.doc_comment import classify_design_document_deltas

# A discussion thread as the canonical writer materializes it: a root
# blockquote line plus the trac discuss blockquote body.
_BASELINE = (
    "# Test Plan\n\n## 1\n\n"
    "> **Prism [RESOLVED]:** finding one\n"
    ">> **Shield:** reply to finding one\n"
)

_REPLY_ONLY = _BASELINE + ">> **Shield:** second reply, same thread\n"

_NEW_THREAD = _BASELINE + (
    "\n> **Shield:** brand new root thread\n"
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
