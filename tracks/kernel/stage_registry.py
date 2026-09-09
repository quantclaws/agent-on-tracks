"""Declarative stage registry (FR-0160 / BS-01): the StageDef fact table,
its derived lookup indexes, the canonical stage order, and the rollback
re-entry routing helpers.

Extracted from ``machine.py`` for module-size compliance (C0302).
``machine.py`` re-exports every name here (frozen import surface:
``release.py`` imports ``StageDef``/``State`` through ``machine``).

No circular import: nothing is imported from the kernel at runtime (the
``release`` seam is resolved lazily inside functions because ``release``
imports ``machine`` at module scope); ``State`` is imported under
``TYPE_CHECKING`` only (duck-typed at runtime).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .machine import State


# FR-0160 / BS-01: declarative stage registry. Every per-stage fact lives here
# exactly once; decide() and the reducers consult it (directly or through the
# derived indexes below) instead of per-stage dicts/branches. Adding a stage is
# one more StageDef. Genuinely stage-specific *control flow* (triage reject
# teardown, scope_overflow rollback, the M-REQ-APPROVAL approval sequence,
# rollback_stage handling) stays explicit. M-START is not registered: it has
# no agent dispatch/review structure.


@dataclass(frozen=True)
class StageDef:
    stage: str
    initial_substate: str  # substate on stage.entered
    drafting_role: str | None = None  # agent drafting the stage's doc
    doc: str | None = None  # target doc (draft / validate / seal)
    docs: tuple = ()  # multi-doc target set (doc is None): one dispatch covers
    # the whole set (M-DESIGN trio, flow.md §8 / Decision A); single-doc
    # stages keep () and name their one doc in `doc`.
    committed_event: str | None = None  # event recording the doc commit
    committed_flag: str | None = None  # State field tracking that commit
    review_substate: str | None = None  # substate after a non-final commit
    reviewer: str | None = None  # agent dispatched in the review substate
    verdict_event: str | None = None  # the reviewer's verdict event
    reviewer_passed_flag: str | None = None  # State field: reviewer passed


# M-DESIGN deliverables (flow.md §8): architecture, interfaces, test-plan.
DESIGN_DOCS = ("architecture.md", "interfaces.md", "test-plan.md")

_STAGES = {
    sd.stage: sd
    for sd in (
        StageDef(
            stage="M-STORY",
            initial_substate="TRIAGE",
            drafting_role="scribe",
            doc="story.md",
            committed_event="story.committed",
            committed_flag="story_committed",
            review_substate="SAGE_REVIEW",
            reviewer="sage",
            verdict_event="sage.verdict",
            reviewer_passed_flag="sage_passed_this_round",
        ),
        # M-ACC mirrors M-SPEC's review loop (Sage drafts, Lex reviews),
        # on acceptance.md.
        StageDef(
            stage="M-SPEC",
            initial_substate="DRAFT",
            drafting_role="sage",
            doc="spec.md",
            committed_event="spec.committed",
            committed_flag="spec_committed",
            review_substate="LEX_REVIEW",
            reviewer="lex",
            verdict_event="lex.verdict",
            reviewer_passed_flag="lex_passed_this_round",
        ),
        StageDef(
            stage="M-ACC",
            initial_substate="DRAFT",
            drafting_role="sage",
            doc="acceptance.md",
            committed_event="acceptance.committed",
            committed_flag="acceptance_committed",
            review_substate="LEX_REVIEW",
            reviewer="lex",
            verdict_event="lex.verdict",
            reviewer_passed_flag="lex_passed_this_round",
        ),
        StageDef(stage="M-REQ-APPROVAL", initial_substate="PREVIEW"),
        # M-DESIGN (flow.md §8): pure technical stage — no human.review/approval
        # gates (BS-05). Multi-doc: one Archer dispatch drafts all DESIGN_DOCS;
        # the per-doc control flow is explicit (_decide_design_draft/_exit), like
        # _decide_approval. doc is None: no single target doc.
        StageDef(
            stage="M-DESIGN",
            initial_substate="DRAFT",
            drafting_role="archer",
            doc=None,
            docs=DESIGN_DOCS,
            committed_event="design.committed",
            review_substate="PRISM_REVIEW",
            reviewer="prism",
            verdict_event="prism.verdict",
            reviewer_passed_flag="prism_passed_this_round",
        ),
        # M-TEST (flow.md §9 / SM-01): no drafting_role/doc/reviewer -- the Shield
        # and Prism dispatches are driven by the explicit `_decide_m_test` control
        # flow (like `_decide_approval`), not by the StageDef table. initial_substate
        # is DISPATCH (SM-01.1).
        StageDef(stage="M-TEST", initial_substate="DISPATCH"),
        # M-IMPL (flow.md §10): explicit control flow via `_decide_m_impl`, like
        # M-TEST. No drafting_role/doc/reviewer -- Archer/Devon/Prism/Shield
        # dispatches are driven by the explicit control flow.
        StageDef(stage="M-IMPL", initial_substate="BASELINE"),
    )
}

# Derived lookups, keyed like the facts they replace.
# committed_flag None (M-DESIGN's multi-doc design.committed) is routed through
# its own reducer (_on_design_committed), not _on_doc_committed.
_COMMITTED_EVENT = {
    sd.committed_event: sd for sd in _STAGES.values() if sd.committed_event and sd.committed_flag
}
# review substate -> an owning StageDef (read for its reviewer role).
_REVIEW_SUBSTATE = {sd.review_substate: sd for sd in _STAGES.values() if sd.review_substate}
# verdict event -> owning stages; a verdict event may be shared by several
# stages (lex.verdict: M-SPEC/M-ACC). See _on_reviewer_verdict.
_VERDICT_OWNERS: dict[str, list[StageDef]] = {}
for sd in _STAGES.values():
    if sd.verdict_event:
        _VERDICT_OWNERS.setdefault(sd.verdict_event, []).append(sd)


def canonical_stage_order() -> tuple[str, ...]:
    """IF-RELEASE-003 / FR-0274/FR-0287: the canonical stage order single
    source — ``tuple(_STAGES)`` minus M-REQ-APPROVAL plus the five release
    stages (``release.RELEASE_STAGES``). Upstream = a smaller ordinal. The
    release import is lazy (release imports machine.State at module level).
    """
    from .release import RELEASE_STAGES

    return tuple(s for s in _STAGES if s != "M-REQ-APPROVAL") + tuple(RELEASE_STAGES)


# IF-RELEASE-003 (face C): rollback re-entry routes via the target StageDef
# .initial_substate — M-TEST=DISPATCH, M-IMPL=BASELINE — never a fossilized
# DRAFT literal. The author stages keep the SM-01.12/.13 re-author semantics
# (re-entry DRAFT): M-STORY's TRIAGE is a fresh-entry substate only, so a
# return re-authors from DRAFT; M-SPEC/M-ACC/M-DESIGN re-enter DRAFT.
_AUTHOR_REENTRY_DRAFT = frozenset({"M-STORY", "M-SPEC", "M-ACC", "M-DESIGN"})


def _rolled_back_entry_substate(target: str) -> str:
    """Re-entry substate for a rolled-back target (IF-RELEASE-003 face C).
    Release-stage targets read the release stage-table seam
    (``release_stage_defs``; DRAFT fallback until the T-039 body lands)."""
    if target in _AUTHOR_REENTRY_DRAFT:
        return "DRAFT"
    sd = _STAGES.get(target)
    if sd is not None:
        return sd.initial_substate
    try:
        from .release import release_stage_defs

        for rel in release_stage_defs():
            if getattr(rel, "stage", None) == target:
                return rel.initial_substate
    except NotImplementedError:
        pass
    return "DRAFT"


def _uncommit(s: State) -> None:
    """RESPOND must re-commit the current stage's doc to re-enter review."""
    sd = _STAGES.get(s.stage)
    setattr(s, (sd.committed_flag if sd else None) or "story_committed", False)
