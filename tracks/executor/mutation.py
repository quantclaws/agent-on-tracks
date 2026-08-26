"""Mutation evidence protocol declarations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

MUTATION_PROTOCOL_VERSION = 1


@dataclass(frozen=True)
class MutationManifest:
    protocol_version: int
    ac: str
    if_ref: str
    candidate_digest: str
    patch_digest: str
    target_nodes: tuple[str, ...]
    control_nodes: tuple[str, ...]
    runner_identity: str
    allowed_change_scope: tuple[str, ...]
    expected_result: Mapping[str, str]


@dataclass(frozen=True)
class MutationExperimentResult:
    status: Literal["passed", "blocked"]
    baseline: str
    apply: str
    target_kill: str
    controls: str
    rollback: str
    blocked_reason: str | None
    node_results_ref: str | None


# Public facade: delegate to the T-010 helper (mutation_manifest) and the
# T-011 helper (mutation_experiment).  No cross-task facade wiring debt.
from .mutation_experiment import run_mutation_experiment  # noqa: E402,F401
from .mutation_manifest import build_manifest, validate_manifest  # noqa: E402,F401

