"""IF-PHASE-002 Phase 0 coverage/marks/environment (T-005 RED unit).

Pins the coverage/marks helpers declared in interfaces.md §1d *before* the
GREEN implementation lands in the new ``phase0_quality.py`` module:

- ``judge_real_coverage`` only passes when ratio >= threshold AND source
  exclusion is empty (AC-FR0257-01: real coverage by collected, exclude=none;
  AC-FR0257-03: marks registration as a prerequisite).
- ``validate_registered_marks`` checks that the declared marks cover all
  required marks; missing marks are reported as hard errors.

Each assertion fails today because both functions are
``NotImplementedError("IF-PHASE-002")`` stubs in
``tracks/executor/phase0.py`` -- the M-IMPL RED on the contract.
"""

from __future__ import annotations

from tracks.executor.phase0 import (
    CoverageJudgement,
    judge_real_coverage,
    validate_registered_marks,
)

# AC-FR0257-01@v0.7 TRACKS-TRACE coverage: ratio >= threshold, exclude none


def test_judge_real_coverage_passes_when_ratio_meets_threshold_no_exclusion():
    judgement = judge_real_coverage(0.97, 0.95, ())
    assert isinstance(judgement, CoverageJudgement)
    assert judgement.passed is True
    assert judgement.ratio == 0.97
    assert judgement.threshold == 0.95
    assert judgement.excluded_sources == ()
    assert judgement.reason is None


def test_judge_real_coverage_fails_when_ratio_below_threshold():
    judgement = judge_real_coverage(0.90, 0.95, ())
    assert judgement.passed is False
    assert judgement.reason is not None


def test_judge_real_coverage_blocks_excluded_sources():
    judgement = judge_real_coverage(0.97, 0.95, ("tests/excluded.py",))
    assert judgement.passed is False
    assert judgement.excluded_sources == ("tests/excluded.py",)
    assert judgement.reason is not None


# AC-FR0257-03@v0.7 TRACKS-TRACE marks registration

def test_validate_registered_marks_all_required_present_returns_empty():
    errors = validate_registered_marks(
        {"unit", "integration", "e2e", "performance"},
        {"unit", "integration", "e2e"},
    )
    assert isinstance(errors, tuple)
    assert errors == ()


def test_validate_registered_marks_reports_missing_marks():
    errors = validate_registered_marks(
        {"unit", "integration"},
        {"unit", "integration", "e2e", "performance"},
    )
    assert len(errors) >= 1
    assert any("e2e" in e for e in errors)
    assert any("performance" in e for e in errors)
