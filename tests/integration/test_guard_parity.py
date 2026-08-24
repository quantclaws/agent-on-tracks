"""Integration: guard parity and deployment (IF-GUARD-002).

AC-FR0258-02@v0.7 parity mismatch blocks fail-closed,
AC-FR0258-03@v0.7 deploy mechanism generates host configs,
AC-FR0259-02@v0.7 registry real execution evidence,
AC-NFR0141-01@v0.7 parity event programmatic, no agent self-report.

Assertions land on `check_parity`/`deploy_guard_configs` (IF-GUARD-002,
interfaces §1e) public outlets.
"""

from __future__ import annotations

from pathlib import Path

from tracks.executor.guard_registry import (
    GuardDeployment,
    ParityReport,
    check_parity,
    deploy_guard_configs,
    load_guard_registry,
    validate_guard_registry,
)

ARCH = Path(__file__).resolve().parents[2] / ".tracks" / "projects" / "v0.7" / "architecture.md"
REPO = Path(__file__).resolve().parents[2]


# AC-FR0258-02@v0.7 TRACKS-TRACE parity mismatch blocks fail-closed
def test_parity_mismatch_blocks_fail_closed():
    """AC-FR0258-02: any --exit-zero / scope / command drift blocks parity."""
    registry = load_guard_registry(ARCH)
    # A runtime command set that injects --exit-zero must produce a blocked
    # parity report with a mismatch on the offending place/guard.
    runtime_commands = {
        e.required_check: (*e.command, "--exit-zero") for e in registry.entries
    }
    report = check_parity(
        registry=registry,
        runtime_commands=runtime_commands,
        pre_commit_path=REPO / ".githooks" / "pre-commit",
        ci_workflow_path=REPO / ".github" / "workflows" / "ci.yml",
        cwd=REPO,
    )
    assert isinstance(report, ParityReport)
    assert report.runtime_match is False or report.pre_commit_match is False
    assert report.mismatches, (
        "--exit-zero injection must surface GuardMismatch entries"
    )
    # Mismatch kind must include exit_zero for the offending guard(s).
    kinds = {m.kind for m in report.mismatches}
    assert "exit_zero" in kinds, f"exit_zero mismatch kind missing: {kinds}"


# AC-FR0258-03@v0.7 TRACKS-TRACE deploy mechanism generates host configs
def test_deploy_mechanism_generates_host_configs():
    """AC-FR0258-03: deploy_guard_configs writes hook + CI bound to registry."""
    registry = load_guard_registry(ARCH)
    # Deploy must only consume a validated registry (§1e).
    errors = validate_guard_registry(registry, REPO)
    assert errors == (), f"registry must validate before deploy: {errors}"
    deployment = deploy_guard_configs(registry, REPO)
    assert isinstance(deployment, GuardDeployment)
    # The deployment registry digest must equal the source registry digest
    # (single-host four-way: deployment/Runtime/hook/CI, §1k).
    assert deployment.registry_digest == registry.digest, (
        "deployment digest must match source registry digest"
    )
    # Hook and CI paths must be the fixed deployment targets (§1e).
    assert deployment.pre_commit_path == REPO / ".githooks" / "pre-commit"
    assert deployment.ci_workflow_path == REPO / ".github" / "workflows" / "ci.yml"
    # artifact_digests audit the generated raw bytes (not the config_digest).
    assert set(deployment.artifact_digests) >= {".githooks/pre-commit", ".github/workflows/ci.yml"}


# AC-FR0259-02@v0.7 TRACKS-TRACE registry real execution evidence
def test_registry_real_execution_evidence():
    """AC-FR0259-02: every registry guard has a real required_check evidence source.

    A guard entry without a required_check is a declaration-only guard (REVISE
    trigger). We assert the canonical registry entries each carry a non-empty
    required_check and execution_points that include 'ci' (real execution)."""
    registry = load_guard_registry(ARCH)
    for entry in registry.entries:
        assert entry.required_check, (
            f"guard {entry.guard_id} missing required_check (no real evidence)"
        )
        assert "ci" in entry.execution_points, (
            f"guard {entry.guard_id} lacks ci execution point (no real evidence)"
        )
        assert entry.failure_policy == "fail_closed", (
            f"guard {entry.guard_id} not fail_closed"
        )


# AC-NFR0141-01@v0.7 TRACKS-TRACE parity event programmatic, no agent self-report
def test_parity_event_programmatic_no_agent_selfreport():
    """AC-NFR0141-01: parity is a programmatic ParityReport, not agent self-report.

    check_parity returns a frozen ParityReport whose match booleans are derived
    from the three execution places, not from any agent verdict. We assert the
    report shape carries the registry digest and three-place match fields."""
    registry = load_guard_registry(ARCH)
    runtime_commands = {e.required_check: e.command for e in registry.entries}
    report = check_parity(
        registry=registry,
        runtime_commands=runtime_commands,
        pre_commit_path=REPO / ".githooks" / "pre-commit",
        ci_workflow_path=REPO / ".github" / "workflows" / "ci.yml",
        cwd=REPO,
    )
    assert report.registry_digest == registry.digest
    # Three-place match fields are present and boolean (programmatic, not text).
    assert isinstance(report.runtime_match, bool)
    assert isinstance(report.pre_commit_match, bool)
    assert isinstance(report.ci_match, bool)
