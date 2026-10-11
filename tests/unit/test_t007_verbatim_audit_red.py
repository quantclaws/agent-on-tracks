"""T-007 RED round 5: flow.md §1.0.12 verbatim colons + reviewer audit gate
alignment (IF-SPLIT-001 / IF-REVIEW-002; AC-FR0339-03 / AC-FR0347-02).

Devon-owned unit RED for the round-5 re-composed T-007 slice (DIAGNOSE
plan_defect seq 11047 — two GREEN failure nodes; scope additions
``tracks/effects/opencode_audit.py`` + ``tracks/effects/opencode_act.py``):

- ``.tracks/wiki/flow.md`` §3.1 must land the ARCH-011 §1.0.12 pinned text
  verbatim: the pinned state clauses separate the state name from its action
  with a FULL-WIDTH colon — ``CREATE_BRANCH：从 main 创建 releases/v{N+1}``，
  ``FAILED：报告原因, 拒绝 start``; the delivered round-1 machine rendered
  them with ASCII `` : `` mermaid separators (the frozen anchor's order[2]
  miss, tests/integration/test_flow_contracts.py:89-90);
- the reviewer discussion audit gate (``opencode_audit.py
  ``_discussion_audit_result`` reviewer branch) must align with FR-0347 and
  the narrowed ``_lex_review_verdict`` (``opencode_review.py``): when content
  validation passed and every pending thread is non-own (initiated by another
  reviewer, or Human-adjudicated), the LEX_REVIEW face legally answers
  pass / pass-pending-human-threads — the gate must NOT classify that shape
  as ``revise_without_findings``; the doc-anchored M-DESIGN channel keeps
  the bare-revise protection (a Prism revise with no anchored thread stays a
  classified failure).

RED discipline: every node below fails as a real ``AssertionError`` on the
current tree — the full-width-colon clause forms are absent from §3.1, and
the audit gate returns the ``revise_without_findings`` failure for both
legal non-own LEX_REVIEW shapes. Fixtures are real tracks/discuss documents
parsed by the product parser and a real OpencodeBackend over a temp repo; no
stub tokens, no assembly errors, no skips. The frozen integration anchors
are Shield-owned and deliberately not pinned here.

AC: FR-0339-03, FR-0347-02 — TRACKS-TRACE IF-SPLIT-001, IF-REVIEW-002.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from tracks.effects.opencode import OpencodeBackend

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FLOW_MD = _REPO_ROOT / ".tracks" / "wiki" / "flow.md"

# Pending threads NOT initiated by the LEX reviewer — the two legal shapes
# of the narrowed exit: the pass leg (another reviewer's finding, no single
# Human adjudication owner) and the park leg (another reviewer's thread
# adjudicated to @Human -> pass-pending-human-threads under
# _lex_review_verdict).
NON_OWN_OPEN_DOC = (
    "# spec\n"
    "\n"
    "> **Sage:** this section needs rework before GO.\n"
)
NON_OWN_HUMAN_PENDING_DOC = (
    "# spec\n"
    "\n"
    "> **Sage [open]:** the rollout stop condition must be decided by @Human.\n"
)


def _section_of(text: str, heading: str) -> str:
    """Slice one markdown section (heading line startswith ``heading``) up to
    the next heading of the same or higher level; ``""`` when absent (the
    RED shape for a not-yet-landed section)."""
    match = re.search(rf"^({re.escape(heading)}.*)$", text, re.MULTILINE)
    if match is None:
        return ""
    level = len(heading) - len(heading.lstrip("#"))
    after = text[match.end():]
    stop = re.search(rf"^#{{1,{level}}} ", after, re.MULTILINE)
    return match.group(1) + ("\n" + after[: stop.start()] if stop else "\n" + after)


def _proc() -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")


def _review_audit(tmp_path: Path, body: str, role: str, substate: str) -> dict | None:
    """Run the reviewer discussion audit gate over a one-doc doc-set."""
    backend = OpencodeBackend(tmp_path, "v0.11")
    doc = tmp_path / "spec.md"
    doc.write_text(body, encoding="utf-8")
    return backend._discussion_audit_result(
        role, substate, [doc], None, _proc(), "", None, True
    )


# -- flow.md §3.1: pinned clauses land verbatim (full-width colons) --------


# AC-FR0339-03@v0.11 TRACKS-TRACE IF-MSTART-001 §3.1 pinned state clauses use the §1.0.12 full-width colon verbatim
def test_flowmd_substate_clauses_use_pinned_full_width_colons():
    machine = _section_of(_FLOW_MD.read_text(encoding="utf-8"), "### 3.1.")

    # §1.0.12 item 1 verbatim: ``CREATE_BRANCH：从 main 创建
    # releases/v{N+1}；Scribe 定标题写入 frontmatter title`` and
    # ``FAILED：报告原因, 拒绝 start`` separate state from action with a
    # FULL-WIDTH colon; the delivered machine used ASCII " : " separators.
    assert "CREATE_BRANCH：" in machine
    assert "FAILED：" in machine
    assert "从 main 创建 releases/v{N+1}" in machine


# -- reviewer discussion audit gate aligns with FR-0347 --------------------


# AC-FR0347-02@v0.11 TRACKS-TRACE IF-REVIEW-002 audit gate passes the legal non-own pass shape
def test_discussion_audit_passes_legal_lex_pass_shape(tmp_path):
    # Content validation passed, the only pending thread was initiated by
    # another reviewer: the narrowed exit answers pass — the audit must not
    # classify this legal shape as revise_without_findings.
    assert _review_audit(tmp_path, NON_OWN_OPEN_DOC, "lex", "LEX_REVIEW") is None


# AC-FR0347-02@v0.11 TRACKS-TRACE IF-REVIEW-002 audit gate passes the legal Human-pending park shape
def test_discussion_audit_passes_legal_lex_park_shape(tmp_path):
    # The only pending thread is non-own and Human-adjudicated: the exit is
    # the pass-pending-human-threads park (Human park branch unchanged) —
    # again a legal shape the audit must let through.
    assert _review_audit(tmp_path, NON_OWN_HUMAN_PENDING_DOC, "lex", "LEX_REVIEW") is None


# AC-FR0347-02@v0.11 TRACKS-TRACE IF-REVIEW-002 M-DESIGN bare-revise protection preserved
def test_discussion_audit_keeps_mdesign_bare_revise_protection(tmp_path):
    # The doc-anchored M-DESIGN channel keeps the run042 protection: a
    # Prism reviewer face with unresolved threads it did NOT initiate still
    # classifies as revise_without_findings (a bare revise gives the
    # author's RESPOND nothing anchored to reply to).
    audit = _review_audit(tmp_path, NON_OWN_OPEN_DOC, "prism", "PRISM_REVIEW")

    assert audit is not None
    assert audit["status"] == "failed"
    assert audit["failure_class"] == "revise_without_findings"
