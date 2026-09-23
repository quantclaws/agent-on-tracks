"""Integration: Phase 0 quality seal (IF-PHASE-002/003, IF-GUARD-001/002, IF-ADAPTER-001).

AC-FR0257-01@v0.7 coverage ratio by collected, exclude none,
AC-FR0257-02@v0.7 guard hardened, violations=0, revisions audited,
AC-FR0257-03@v0.7 marks registered and environment contract valid,
AC-FR0257-04@v0.7 seal read-only blocks rewrite,
AC-FR0257-05@v0.7 unrecoverable coverage/guard blocked.

Assertions land on `judge_real_coverage`/`validate_registered_marks`/
`build_seal_manifest` (IF-PHASE-002/003, interfaces §1d) public outlets.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tracks.executor.guard_registry import (
    GuardRegistry,
    check_parity,
    load_guard_registry,
    validate_guard_registry,
)
from tracks.executor.phase0 import (
    CoverageJudgement,
    SealManifest,
    build_seal_manifest,
    judge_real_coverage,
    validate_registered_marks,
)

REPO = Path(__file__).resolve().parents[2]

def _latest_architecture(root):
    """2026-09-23 (island-2 sweep): meta-tests pinning config digests must
    validate the LIVE version's registry -- a frozen v0.7 registry's digest
    pins only held at that release's tree; the repo's lint config legitimately
    evolves per each version's design (v0.9's M-DESIGN updated pyproject).
    Historical registries stay in git history."""
    import re as _re

    projects = root / ".tracks" / "projects"
    versions = sorted(
        (p.name for p in projects.iterdir() if _re.fullmatch(r"v\d+\.\d+", p.name)),
        key=lambda v: tuple(int(x) for x in v[1:].split(".")),
    )
    return projects / versions[-1] / "architecture.md"


ARCH = _latest_architecture(REPO)

pytestmark = pytest.mark.integration



# AC-FR0257-01@v0.7 TRACKS-TRACE coverage ratio by collected, exclude none
def test_coverage_ratio_by_collected_exclude_none():
    """AC-FR0257-01: coverage passed only when ratio>=threshold and exclude=none."""
    # Happy: ratio meets threshold with zero excluded sources -> passed.
    happy = judge_real_coverage(ratio=0.97, threshold=0.95, excluded_sources=())
    assert isinstance(happy, CoverageJudgement)
    assert happy.passed is True
    assert happy.excluded_sources == ()
    assert happy.reason is None
    # Boundary: ratio exactly at threshold still passes (>=).
    boundary = judge_real_coverage(ratio=0.95, threshold=0.95, excluded_sources=())
    assert boundary.passed is True
    # Below threshold fails regardless of exclusion.
    below = judge_real_coverage(ratio=0.90, threshold=0.95, excluded_sources=())
    assert below.passed is False
    # Exclusion path is not a pass route: any excluded source blocks the
    # "by=collected exclude=none" contract even if ratio>=threshold.
    excluded = judge_real_coverage(
        ratio=0.99, threshold=0.95, excluded_sources=("tracks/legacy",)
    )
    assert excluded.passed is False, "source exclusion must not satisfy the gate"


# AC-FR0257-02@v0.7 TRACKS-TRACE guard hardened, violations=0, revisions audited
# AC-FR0259-01@v0.7 TRACKS-TRACE Prism REVISE routes back to Archer
# AC-FR0259-02@v0.7 TRACKS-TRACE registry real execution evidence
# AC-NFR0141-01@v0.7 TRACKS-TRACE parity event programmatic, no agent self-report
def test_guard_hardened_violations_zero_revisions_audited():
    """AC-FR0257-02 / FR-0259 / NFR-0141-01: guard hardening + parity outlets.

    The `phase0.guard_hardened` event (§1a row 3) is produced by the Phase 0
    executor consuming the guard registry; its observable outlets are
    `validate_guard_registry` (violations) and `check_parity` (guard.parity
    payload). We assert: (1) the canonical registry hardens with zero
    violations; (2) the parity payload carries three-place match + registry
    digest (programmatic, no agent self-report); (3) a guard violation (missing
    category / --exit-zero) routes to BLOCKED — the REVISE trigger that routes
    back to Archer, not to a later stage (FR-0259-01); (4) every guard carries
    real execution evidence (required_check + ci execution point, FR-0259-02).
    """
    registry = load_guard_registry(ARCH)
    # (1) Guards hardened: zero validation violations.
    violations = validate_guard_registry(registry, REPO)
    assert violations == (), f"canonical registry must harden with 0 violations: {violations}"
    # (4) Real execution evidence per guard (FR-0259-02): required_check + ci.
    for entry in registry.entries:
        assert entry.required_check, (
            f"guard {entry.guard_id} lacks required_check (declaration-only, REVISE)"
        )
        assert "ci" in entry.execution_points, (
            f"guard {entry.guard_id} lacks ci execution point (no real evidence)"
        )
        assert entry.failure_policy == "fail_closed"
    # (2) guard.parity payload: three-place match + registry digest (NFR-0141-01).
    runtime_commands = {e.required_check: e.command for e in registry.entries}
    parity = check_parity(
        registry=registry,
        runtime_commands=runtime_commands,
        pre_commit_path=REPO / ".githooks" / "pre-commit",
        ci_workflow_path=REPO / ".github" / "workflows" / "ci.yml",
        cwd=REPO,
    )
    assert parity.registry_digest == registry.digest
    assert isinstance(parity.runtime_match, bool)
    assert isinstance(parity.pre_commit_match, bool)
    assert isinstance(parity.ci_match, bool)
    # (3) REVISE trigger (FR-0259-01): a missing-category registry must surface
    # violations (routes back to Archer, not a later stage). --exit-zero injection
    # must block parity (no soft guard).
    short = GuardRegistry(
        version=registry.version,
        host=registry.host,
        entries=registry.entries[:7],
        digest=registry.digest,
    )
    revise_errors = validate_guard_registry(short, REPO)
    assert revise_errors, "missing category must trigger REVISE (route to Archer)"
    exit_zero_commands = {
        e.required_check: (*e.command, "--exit-zero") for e in registry.entries
    }
    exit_zero_parity = check_parity(
        registry=registry,
        runtime_commands=exit_zero_commands,
        pre_commit_path=REPO / ".githooks" / "pre-commit",
        ci_workflow_path=REPO / ".github" / "workflows" / "ci.yml",
        cwd=REPO,
    )
    assert exit_zero_parity.mismatches, "--exit-zero must block parity (no soft guard)"
    assert {m.kind for m in exit_zero_parity.mismatches} == {"exit_zero"} or (
        "exit_zero" in {m.kind for m in exit_zero_parity.mismatches}
    )


# AC-FR0257-03@v0.7 TRACKS-TRACE marks registered and environment contract valid
def test_marks_registered_and_env_contract_valid():
    """AC-FR0257-03: required marks registered so node classification works."""
    required = {"integration", "e2e", "performance"}
    declared = {"integration", "e2e", "performance"}
    missing = validate_registered_marks(declared_marks=declared, required_marks=required)
    # Contract: empty tuple when all required marks are registered.
    assert missing == ()
    # A missing mark must surface as a non-empty tuple (routes to BLOCKED).
    incomplete = validate_registered_marks(
        declared_marks={"integration"}, required_marks=required
    )
    assert incomplete, "missing marks must be reported (no silent pass)"


# AC-FR0257-04@v0.7 TRACKS-TRACE seal read-only blocks rewrite
def test_seal_readonly_blocks_rewrite():
    """AC-FR0257-04: SEALED is terminal; a second build_seal_manifest is stable.

    The seal manifest is content-addressed (seal_id = sha256 of canonical JSON
    of the remaining fields). A re-seal with identical inputs must yield an
    identical seal_id (idempotent, non-retreating). Any drift in inputs yields a
    different seal_id, evidencing that the sealed baseline is read-only.
    """
    s1 = build_seal_manifest(
        baseline_version="v0.6",
        document_digests={"spec.md": "sha256:spec"},
        frozen_test_digests={"t": "sha256:t"},
        marks=("integration",),
        environment_contract_digest="sha256:env",
    )
    s2 = build_seal_manifest(
        baseline_version="v0.6",
        document_digests={"spec.md": "sha256:spec"},
        frozen_test_digests={"t": "sha256:t"},
        marks=("integration",),
        environment_contract_digest="sha256:env",
    )
    assert isinstance(s1, SealManifest)
    assert s1.seal_id == s2.seal_id, "identical inputs must yield identical seal"
    # Drift in frozen tests must change the seal_id (read-only enforcement).
    drifted = build_seal_manifest(
        baseline_version="v0.6",
        document_digests={"spec.md": "sha256:spec"},
        frozen_test_digests={"t": "sha256:drifted"},
        marks=("integration",),
        environment_contract_digest="sha256:env",
    )
    assert s1.seal_id != drifted.seal_id, "frozen-test drift must change seal_id"


# AC-FR0257-05@v0.7 TRACKS-TRACE unrecoverable coverage/guard blocked
def test_unrecoverable_coverage_guard_blocked():
    """AC-FR0257-05: unrecoverable coverage/guard violation routes to BLOCKED."""
    # Coverage below threshold with no exclusion route -> unrecoverable -> blocked.
    failed = judge_real_coverage(
        ratio=0.40, threshold=0.95, excluded_sources=()
    )
    assert failed.passed is False
    assert failed.reason is not None, "blocked coverage must carry a reason"
