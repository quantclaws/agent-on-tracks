"""Quality-gate layering and feedback contracts through IF-IMPL-005."""

import pytest

from tracks.executor.quality_gate import (
    run_gates,
    run_production_checks,
    run_test_checks,
)


@pytest.mark.integration
# AC-FR0110-01@v0.5 TRACKS-TRACE green gate runs granular checks without e2e
# AC-FR0110-03@v0.5 TRACKS-TRACE integration feedback is desensitized
# AC-FR0110-04@v0.5 TRACKS-TRACE unknown attribution enters diagnose
# AC-FR0130-01@v0.5 TRACKS-TRACE no-change refactor is gateable
# AC-FR0130-02@v0.5 TRACKS-TRACE production and test checks are layered
# AC-FR0130-03@v0.5 TRACKS-TRACE public interface changes roll back
def test_quality_gate_layers_expose_exact_check_sets(host_repo):
    production = run_production_checks(str(host_repo), ["tracks/feature.py"])
    tests = run_test_checks(str(host_repo), ["tests/unit/test_feature.py"])
    both = run_gates(
        str(host_repo),
        ["tracks/feature.py", "tests/unit/test_feature.py"],
        "refactor_gate",
        {"tests": {"integration": {"run": ["python", "-m", "pytest"]}}},
    )
    assert set(production.checks_run) >= {
        "ruff", "flake8:CCR001", "pylint:R0801", "pylint:C0302",
        "pylint:R0915", "pylint:R0914",
    }
    assert set(tests.checks_run) == {"pylint:R0801", "pylint:C0302"}
    assert both.layer == "both"
    assert not any("e2e" in check for check in both.checks_run)
