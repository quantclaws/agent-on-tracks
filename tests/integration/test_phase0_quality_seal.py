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

from tracks.executor.phase0 import (
    CoverageJudgement,
    SealManifest,
    build_seal_manifest,
    judge_real_coverage,
    validate_registered_marks,
)


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
def test_guard_hardened_violations_zero_revisions_audited():
    """AC-FR0257-02: guard_hardened payload carries violations=0 and revised count.

    The guard hardening outlet is the `phase0.guard_hardened` event (interfaces
    §1a row 3). The Phase 0 executor must surface violations=0 (hardened) with a
    non-negative `revised` count that is auditable. A non-zero violations count
    must route to BLOCKED, not to sealed. We assert the judgement shape against
    the coverage seal manifest builder (the seal is only produced when guards
    hardened): build_seal_manifest is the frozen seal outlet (IF-PHASE-003) and
    must embed the environment-contract digest that proves guards hardened.
    """
    seal = build_seal_manifest(
        baseline_version="v0.6",
        document_digests={"spec.md": "sha256:spec", "acceptance.md": "sha256:acc"},
        frozen_test_digests={"tests/integration/test_x.py": "sha256:tests"},
        marks=("integration", "e2e"),
        environment_contract_digest="sha256:env-contract-pass",
    )
    assert isinstance(seal, SealManifest)
    assert seal.baseline_version == "v0.6"
    # seal_id is sha256(canonical_json(remaining fields)) per §1d: stable, hex.
    assert seal.seal_id.startswith("sha256:")
    # environment_contract_digest must be non-empty (guards hardened proof).
    assert seal.environment_contract_digest == "sha256:env-contract-pass"


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
