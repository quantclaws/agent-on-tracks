"""T-026 RED: three-journey plan & preview binding (FR-0277/FR-0273, IF-JOURNEY-001).

Pins the still-unimplemented slices of tracks/executor/release_gate.py
(registered vocabulary IF-JOURNEY-001; scope = release_gate.py, the journey
planner / preview digest owner per §1m):

- AC-FR0277-01/02/03: journey resolution — feature / post-release / dev
  version facts, patch/prerelease derivation, active-release-branch
  participation and dev precheck fail-closed semantics.
- AC-FR0273-01/02 (preview family adopted): compute_preview_digest binds all
  five components (candidate + artifact + evidence + operation plan +
  contract/policy) with the canonical JSON + sha256 formula.

All target tests fail on the pre-fix baseline with assertion_failure on the
contract behaviour (no stub_token, no assembly errors). Only unit tests are
added (RED discipline, manifest red_test_paths = tests/unit).

IF-JOURNEY-001: build_operation_plan, resolve_version_facts; IF-RELEASE-002
digest formula is adopted via compute_preview_digest.
"""

from __future__ import annotations

import hashlib
import json

from tracks.executor.release_gate import compute_preview_digest


def _canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


# AC-FR0277-01@v0.8 TRACKS-TRACE IF-JOURNEY-001 feature journey plan
def test_feature_journey_plan_public_output():
    """AC-FR0277-01: feature journey produces a full public plan (merge main,
    public tag, artifact, release) with `{minor}/{n}/{artifact}` resolved from
    version facts."""
    try:
        from tracks.executor.release_gate import build_operation_plan
    except ImportError as err:
        raise AssertionError(
            "assertion failure: build_operation_plan not implemented in release_gate"
        ) from err
    try:
        plan = build_operation_plan(
            {
                "operations": {
                    "feature": {
                        "steps": [
                            "merge:main",
                            "tag:v{minor}.{n}",
                            "artifact:{artifact}",
                            "release:v{minor}.{n}",
                        ]
                    }
                }
            },
            "feature",
            {"minor": "0", "n": "1", "artifact": "dist/x-1.whl", "ulid": "RUN"},
        )
    except NotImplementedError as err:
        raise AssertionError(
            "assertion failure: build_operation_plan not implemented for feature journey"
        ) from err
    assert isinstance(plan, dict), "assertion failure: plan must be dict"
    steps = plan.get("steps") if isinstance(plan, dict) else None
    if steps is None:
        steps = list(plan.get("operations", {}).get("feature", {}).get("steps", []))
    assert any("merge" in s and "main" in s for s in steps), (
        f"assertion failure: feature plan must merge main, got {steps!r}"
    )
    assert any("tag:v0.1" in s or "v0.1" in s for s in steps), (
        f"assertion failure: feature plan must tag v0.1 from version facts, got {steps!r}"
    )


# D6/FR-0281: `artifact:{artifact}` resolves from [host-contract.build]
def test_plan_resolves_declared_artifact_placeholder():
    """The declared build artifact is the ``{artifact}`` source (single truth):
    ``artifact:{artifact}`` must render to the contract's declared glob."""
    from tracks.executor.release_gate import build_operation_plan

    plan = build_operation_plan(
        {
            "build": {"artifact": "dist/*.whl"},
            "operations": {
                "feature": {
                    "steps": ["artifact:{artifact}", "release:{feature_tag}"]
                }
            },
        },
        "feature",
        {"feature_tag": "v0.8.0"},
    )
    assert plan["steps"] == ["release:v0.8.0", "artifact:dist/*.whl"]


def test_plan_resolves_artifact_declared_with_version_facts():
    """A declared artifact template renders with the same facts as the plan."""
    from tracks.executor.release_gate import build_operation_plan

    plan = build_operation_plan(
        {
            "build": {"artifact": "dist/{version}/*.whl"},
            "operations": {"feature": {"steps": ["artifact:{artifact}"]}},
        },
        "feature",
        {"version": "v0.8.0"},
    )
    assert plan["steps"] == ["artifact:dist/v0.8.0/*.whl"]


def test_plan_leaves_undeclared_artifact_literal():
    """No declared artifact -> the literal survives for the fail-closed
    publish preflight (never a guessed path)."""
    from tracks.executor.release_gate import build_operation_plan

    plan = build_operation_plan(
        {
            "operations": {
                "feature": {"steps": ["tag:{feature_tag}", "artifact:{artifact}"]}
            }
        },
        "feature",
        {"feature_tag": "v0.8.0"},
    )
    assert plan["steps"] == ["tag:v0.8.0", "artifact:{artifact}"]


# AC-FR0277-03@v0.8 TRACKS-TRACE IF-JOURNEY-001 dev journey precheck fail-closed
def test_dev_journey_requires_active_release_branch():
    """AC-FR0277-03/04: dev precheck fails closed when no active release
    branch exists — must surface 'no active release branch' and produce no
    plan steps (no fake public release)."""
    try:
        from tracks.executor.release_gate import dev_precheck
    except ImportError as err:
        raise AssertionError(
            "assertion failure: dev_precheck not implemented in release_gate"
        ) from err
    try:
        ok, reason = dev_precheck(
            {"operations": {"dev": {"steps": ["merge:release/{minor}"]}}},
            {"minor": "0"},
            active_release_branch=None,
        )
    except NotImplementedError as err:
        raise AssertionError(
            "assertion failure: dev_precheck fail-closed path not implemented"
        ) from err
    assert ok is False, (
        f"assertion failure: dev precheck must fail closed without active branch, got {ok!r}"
    )
    assert "no active release branch" in reason, (
        f"assertion failure: must report 'no active release branch', got {reason!r}"
    )


# AC-FR0273-01@v0.8 TRACKS-TRACE IF-JOURNEY-001 preview digest binds all
def test_compute_preview_digest_canonical_formula():
    """AC-FR0273-01: preview_digest = sha256(canonical_json(all five
    components)) — locally recomputed, no component omitted."""
    candidate = "a" * 40
    artifact = "sha256:" + "b" * 64
    evidence = {"full_f": "sha256:" + "e" * 32}
    op_plan = "sha256:" + "c" * 64
    contract = "sha256:" + "d" * 64
    raw = {
        "candidate_sha": candidate,
        "artifact_digest": artifact,
        "evidence_digests": evidence,
        "operation_plan_digest": op_plan,
        "contract_policy_digest": contract,
    }
    expected = "sha256:" + hashlib.sha256(_canonical(raw)).hexdigest()
    try:
        digest = compute_preview_digest(candidate, artifact, evidence, op_plan, contract)
    except NotImplementedError as err:
        raise AssertionError(
            "assertion failure: compute_preview_digest not implemented"
        ) from err
    assert digest == expected, (
        f"assertion failure: preview digest must bind all five components, "
        f"got {digest!r} expected {expected!r}"
    )
    assert digest.startswith("sha256:")


# AC-FR0273-01@v0.8 TRACKS-TRACE IF-JOURNEY-001 digest changes on any component
def test_preview_digest_tracks_any_component_change():
    """Changing any one component changes the digest (bind-sensitivity)."""
    candidate = "a" * 40
    artifact = "sha256:" + "b" * 64
    evidence = {}
    op_plan = "sha256:" + "c" * 64
    contract = "sha256:" + "d" * 64
    try:
        d1 = compute_preview_digest(candidate, artifact, evidence, op_plan, contract)
        d2 = compute_preview_digest("f" * 40, artifact, evidence, op_plan, contract)
        d3 = compute_preview_digest(candidate, artifact, evidence, op_plan, "sha256:" + "9" * 64)
    except NotImplementedError as err:
        raise AssertionError(
            "assertion failure: compute_preview_digest sensitivity check not implemented"
        ) from err
    assert d1 != d2, "assertion failure: candidate change must change digest"
    assert d1 != d3, "assertion failure: contract change must change digest"


# AC-FR0277-02@v0.8 TRACKS-TRACE IF-JOURNEY-001 when=active_release_branch skip
def test_post_release_when_active_branch_step_skipped_without_branch():
    """IF-JOURNEY-001 §1m: a post-release `merge:release/{minor}:when=
    active_release_branch` step is silently SKIPPED when no active release
    branch exists — the plan digest is computed over the actually-resolved
    step set (no phantom sync-merge step)."""
    try:
        from tracks.executor.release_gate import build_operation_plan
    except ImportError as err:
        raise AssertionError(
            "assertion failure: build_operation_plan not implemented in release_gate"
        ) from err
    try:
        plan = build_operation_plan(
            {
                "operations": {
                    "post_release": {
                        "steps": [
                            "merge:main",
                            "merge:release/{minor}:when=active_release_branch",
                            "tag:v{minor}.{n}",
                        ]
                    }
                }
            },
            "post-release",
            {"minor": "0", "n": "1"},
            active_release_branch=None,
        )
    except TypeError as err:
        raise AssertionError(
            "assertion failure: build_operation_plan must accept an "
            "active_release_branch input to honour the when=active_release_branch "
            "step semantics (§1m)"
        ) from err
    except NotImplementedError as err:
        raise AssertionError(
            "assertion failure: build_operation_plan not implemented for post-release"
        ) from err
    assert isinstance(plan, dict), "assertion failure: plan must be dict"
    steps = plan.get("steps")
    if steps is None:
        steps = list(
            plan.get("operations", {}).get("post_release", {}).get("steps", [])
        )
    assert "merge:main" in steps, (
        f"assertion failure: unconditional merge:main must be in the resolved plan, "
        f"got {steps!r}"
    )
    assert not any("when=active_release_branch" in s for s in steps), (
        f"assertion failure: the when=active_release_branch step must be silently "
        f"skipped when no active release branch exists, got {steps!r}"
    )
    assert not any("merge:release/" in s for s in steps), (
        f"assertion failure: no sync-merge step may remain without an active "
        f"release branch, got {steps!r}"
    )


# AC-FR0277-02@v0.8 TRACKS-TRACE IF-JOURNEY-001 when-step kept with branch
def test_post_release_when_active_branch_kept_with_branch():
    """IF-JOURNEY-001 §1m: with an active release branch present, the
    `when=active_release_branch` step IS kept and its `{minor}` placeholder
    resolved."""
    try:
        from tracks.executor.release_gate import build_operation_plan
    except ImportError as err:
        raise AssertionError(
            "assertion failure: build_operation_plan not implemented in release_gate"
        ) from err
    try:
        plan = build_operation_plan(
            {
                "operations": {
                    "post_release": {
                        "steps": [
                            "merge:release/{minor}:when=active_release_branch",
                        ]
                    }
                }
            },
            "post-release",
            {"minor": "0", "n": "1"},
            active_release_branch="release/0",
        )
    except TypeError as err:
        raise AssertionError(
            "assertion failure: build_operation_plan must accept an "
            "active_release_branch input to honour the when=active_release_branch "
            "step semantics (§1m)"
        ) from err
    except NotImplementedError as err:
        raise AssertionError(
            "assertion failure: build_operation_plan not implemented for post-release"
        ) from err
    assert isinstance(plan, dict), "assertion failure: plan must be dict"
    steps = plan.get("steps")
    if steps is None:
        steps = list(
            plan.get("operations", {}).get("post_release", {}).get("steps", [])
        )
    assert any("merge:release/" in s for s in steps), (
        f"assertion failure: with an active release branch the sync-merge step "
        f"must be kept, got {steps!r}"
    )
    assert any("release/0" in s for s in steps), (
        f"assertion failure: the {{minor}} placeholder of the kept step must be "
        f"resolved, got {steps!r}"
    )
