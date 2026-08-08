"""Quality gate layered execution (FR-0110/FR-0130, IF-IMPL-005).

GREEN_GATE: targeted unit tests + historical unit tests + int subset +
lint/format/type/static + contract. REFACTOR_GATE: rerun all GREEN_GATE
checks + quality gate layering (production: full checks; test: R0801+C0302
only). Feedback desensitization: int/e2e failures return classification
diagnosis only, not assertion originals.

This module is a contract stub: signatures are frozen, bodies raise
NotImplementedError with the IF- token. Devon fills the implementation during
M-IMPL; the declaration contract must not change.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class GateResult:
    status: Literal["pass", "fail"]
    checks_run: tuple[str, ...]      # executed check names (e.g. ruff, flake8-CCR001, pylint-R0801)
    failures: tuple[str, ...]        # failed checks + details
    layer: Literal["production", "test", "both"]  # executed layer


def run_gates(
    repo: str,
    changed_paths: list[str],    # files changed in this task
    gate_scope: Literal["green_gate", "refactor_gate"],
    project_toml: dict,          # parsed .tracks/project/project.toml contract
) -> GateResult:
    """FR-0110/FR-0130 quality gate layered execution.

    GREEN_GATE: targeted unit tests + historical unit tests + int subset +
    lint/format/type/static + contract.
    REFACTOR_GATE: rerun all GREEN_GATE checks + quality gate layering
    (production: full checks; test: R0801+C0302 only).
    Classify files by changed_paths as production code (tracks/ non-tests/) or
    test code (tests/), execute different check sets (BS-11).
    """
    raise NotImplementedError("IF-IMPL-005")


def run_production_checks(
    repo: str,
    changed_paths: list[str],
) -> GateResult:
    """FR-0130 production code full four segments: ruff + flake8 CCR001 +
    pylint R0801 + C0302 + R0915 + R0914.

    Any failure -> status=fail.
    """
    raise NotImplementedError("IF-IMPL-005")


def run_test_checks(
    repo: str,
    changed_paths: list[str],
) -> GateResult:
    """FR-0130 test code only R0801 + C0302 (BS-11).

    Does NOT apply CCR001 / R0915 / R0914.
    """
    raise NotImplementedError("IF-IMPL-005")
