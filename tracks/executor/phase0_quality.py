"""Phase 0 coverage / marks / environment helpers (IF-PHASE-002).

Pure functions implementing the Phase 0 quality gates:
- ``judge_real_coverage``: passes only when ratio >= threshold AND source
  exclusion is empty (AC-FR0257-01, interfaces §1d).
- ``validate_registered_marks``: reports missing required marks as hard
  errors (AC-FR0257-03 marks registration).
"""

from __future__ import annotations

from collections.abc import Sequence, Set

from tracks.executor.phase0 import CoverageJudgement


def judge_real_coverage(
    ratio: float, threshold: float, excluded_sources: Sequence[str]
) -> CoverageJudgement:
    """Pass only when the collected ratio meets the threshold AND no source
    is excluded (interfaces §1d: ratio >= threshold and exclude=none)."""
    passed = ratio >= threshold and not excluded_sources
    if passed:
        return CoverageJudgement(
            passed=True,
            ratio=ratio,
            threshold=threshold,
            excluded_sources=(),
            reason=None,
        )
    if ratio < threshold:
        return CoverageJudgement(
            passed=False,
            ratio=ratio,
            threshold=threshold,
            excluded_sources=tuple(excluded_sources),
            reason=f"ratio {ratio:.4f} below threshold {threshold:.4f}",
        )
    return CoverageJudgement(
        passed=False,
        ratio=ratio,
        threshold=threshold,
        excluded_sources=tuple(excluded_sources),
        reason=f"excluded sources must be empty: {list(excluded_sources)}",
    )


def validate_registered_marks(
    declared_marks: Set[str], required_marks: Set[str]
) -> tuple[str, ...]:
    """Return hard-error strings for each required mark absent from the
    declared set (AC-FR0257-03 marks registration).  Empty tuple when all
    required marks are present."""
    missing = sorted(required_marks - declared_marks)
    return tuple(f"missing required mark: {m}" for m in missing)
