"""Mutation evidence protocol declarations."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from tracks.adapters.base import TestRunResult

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


def build_manifest(
    ac: str,
    if_ref: str,
    candidate_digest: str,
    patch_digest: str,
    target_nodes: Sequence[str],
    control_nodes: Sequence[str],
    runner_identity: str,
    allowed_change_scope: Sequence[str],
) -> MutationManifest:
    raise NotImplementedError("IF-MUTATION-001")


def validate_manifest(
    manifest: Mapping, patch_paths: Sequence[str]
) -> MutationManifest:
    raise NotImplementedError("IF-MUTATION-001")


def run_mutation_experiment(
    manifest: MutationManifest,
    repo: Path,
    open_worktree: Callable[[str], Path],
    apply_patch: Callable[[Path, str], str],
    run_nodes: Callable[[Path, Sequence[str]], Mapping[str, TestRunResult]],
    close_worktree: Callable[[Path], None],
) -> MutationExperimentResult:
    raise NotImplementedError("IF-MUTATION-002")
