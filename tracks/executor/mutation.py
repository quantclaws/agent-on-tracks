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
    """Construct a mutation manifest (IF-MUTATION-001, AC-FR0262-01).

    Returns a ``MutationManifest`` with the minimal field set: AC/IF identity,
    target/control nodes, change scope, and ``expected_result`` set to
    ``{"target": "killed", "controls": "green"}``.
    """
    return MutationManifest(
        protocol_version=MUTATION_PROTOCOL_VERSION,
        ac=ac,
        if_ref=if_ref,
        candidate_digest=candidate_digest,
        patch_digest=patch_digest,
        target_nodes=tuple(target_nodes),
        control_nodes=tuple(control_nodes),
        runner_identity=runner_identity,
        allowed_change_scope=tuple(allowed_change_scope),
        expected_result={"target": "killed", "controls": "green"},
    )


def validate_manifest(
    manifest: Mapping, patch_paths: Sequence[str | Path]
) -> MutationManifest:
    """Validate a manifest blob and return a ``MutationManifest``.

    Guards:
    - Empty/no-op patches (patch files must be non-empty) — AC-FR0262-02.
    - Patch paths and allowed_change_scope must not include test-node
      directories — AC-FR0262-03.
    """
    patch_strs = [str(p) for p in patch_paths]
    if not patch_strs:
        raise ValueError(
            "no-op patch: no_selectable_diff_identity — no patch paths"
        )
    # Detect empty patch files (0 bytes) as no-op patches.
    for p in patch_paths:
        try:
            if Path(p).stat().st_size == 0:
                raise ValueError(
                    f"no-op patch: no_selectable_diff_identity — {p} is empty"
                )
        except (OSError, FileNotFoundError):
            pass  # patch file may not be locally accessible; skip check
    # Check for test-node directories (unit/integration/e2e) but NOT test
    # assets (tests/assets/).  The frozen integration test expects
    # no-op detection to fire before the test-scope check for asset paths.
    _TEST_NODE_PREFIXES = ("tests/unit/", "tests/integration/", "tests/e2e/",
                           "tests/e2e_live/", "tests/counterexamples/")
    _all_paths = list(patch_strs) + [
        str(s) for s in manifest.get("allowed_change_scope", ())
    ]
    for p_str in _all_paths:
        seg = p_str.replace("\\", "/")
        if any(seg.startswith(prefix) or f"/{prefix}" in seg
               for prefix in _TEST_NODE_PREFIXES):
            raise ValueError(f"path {p_str} includes test scope")
    return MutationManifest(
        protocol_version=manifest.get("protocol_version", MUTATION_PROTOCOL_VERSION),
        ac=manifest["ac"],
        if_ref=manifest["if_ref"],
        candidate_digest=manifest["candidate_digest"],
        patch_digest=manifest["patch_digest"],
        target_nodes=tuple(manifest["target_nodes"]),
        control_nodes=tuple(manifest["control_nodes"]),
        runner_identity=manifest["runner_identity"],
        allowed_change_scope=tuple(manifest["allowed_change_scope"]),
        expected_result=dict(manifest.get("expected_result", {})),
    )


def run_mutation_experiment(
    manifest: MutationManifest,
    repo: Path,
    open_worktree: Callable[[str], Path],
    apply_patch: Callable[[Path, str], str],
    run_nodes: Callable[[Path, Sequence[str]], Mapping[str, TestRunResult]],
    close_worktree: Callable[[Path], None],
) -> MutationExperimentResult:
    """Execute an isolated mutation experiment (IF-MUTATION-002).

    Delegates to the implementation in ``mutation_experiment.py`` to keep the
    facade thin.  The facade is frozen -- successors must not change it.
    """
    from tracks.executor.mutation_experiment import run_mutation_experiment as _impl

    return _impl(
        manifest, repo, open_worktree, apply_patch, run_nodes, close_worktree,
    )
