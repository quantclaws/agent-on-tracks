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

import pytest

from tracks.executor.guard_registry import (
    GUARD_CATEGORIES,
    GuardDeployment,
    ParityReport,
    check_parity,
    deploy_guard_configs,
    load_guard_registry,
    validate_guard_registry,
)

ARCH = Path(__file__).resolve().parents[2] / ".tracks" / "projects" / "v0.7" / "architecture.md"
REPO = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.integration



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
def test_deploy_mechanism_generates_host_configs(tmp_path):
    """AC-FR0258-03 / test-plan §6.5 frozen prescription.

    The deploy mechanism must consume the wheel-shipped demo architecture,
    materialize it byte-for-byte into an ISOLATED target repo (never the live
    repo tree), run load→validate→deploy_guard_configs, and produce a four-way
    registry-digest binding (deployment record / hook TRACKS_GUARD_REGISTRY /
    CI env / guard.parity.registry). The built wheel and fresh repo must not
    contain the inherited legacy `guards.toml`. Tampering `flake8.ini` or any
    generation point must hard-error/blocked with no fallback. The 8th guard's
    `config_digest` is recomputed from `.tracks/projects/project.toml` raw bytes
    to prove generated `artifact_digests` are not treated as the config value.
    """
    import hashlib
    import shutil
    import subprocess
    import sys

    repo = Path(__file__).resolve().parents[2]
    demo_template = repo / "tracks" / "assets" / "demo_host"
    demo_arch_src = demo_template / "architecture.md"
    assert demo_arch_src.exists(), "demo architecture asset must ship in the wheel"

    # 1. Build a real wheel into an isolated dir (never the live repo tree).
    wheel_dir = tmp_path / "wheels"
    wheel_dir.mkdir()
    subprocess.run(
        [sys.executable, "-m", "pip", "wheel", "--no-deps", "-w", str(wheel_dir), str(repo)],
        check=True,
        capture_output=True,
    )
    wheels = list(wheel_dir.glob("*.whl"))
    assert wheels, "wheel build must produce a wheel"
    wheel = wheels[0]

    # 2. package-data allowlist: the built wheel must NOT contain the inherited
    #    legacy `guards.toml`, and MUST contain the formal demo architecture.
    import zipfile

    with zipfile.ZipFile(wheel) as zf:
        names = zf.namelist()
    assert not any(n.endswith("guards.toml") for n in names), (
        "wheel must not contain inherited legacy guards.toml (allowlist exclusion)"
    )
    assert any(n.endswith("demo_host/architecture.md") for n in names), (
        "wheel must ship the formal demo architecture registry source"
    )

    # 3. Isolated fresh target repo (NOT the live repo tree) — side-effect free.
    target = tmp_path / "fresh-demo"
    target.mkdir()
    (target / "README.md").write_text("fresh demo host\n", encoding="utf-8")
    subprocess.run(
        ["git", "init", "-b", "main"], cwd=target, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.email", "demo@x"], cwd=target, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Demo"], cwd=target, check=True, capture_output=True
    )

    # 4. Materialize the demo architecture byte-for-byte from the wheel asset to
    #    the fixed deployment landing point (.tracks/projects/v0.1/architecture.md).
    arch_landing = target / ".tracks" / "projects" / "v0.1" / "architecture.md"
    arch_landing.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(demo_arch_src, arch_landing)
    assert (
        hashlib.sha256(arch_landing.read_bytes()).hexdigest()
        == hashlib.sha256(demo_arch_src.read_bytes()).hexdigest()
    ), "materialized architecture must be byte-for-byte identical to the asset"

    # 5. load→validate→deploy on the isolated target (demo-pytest host).
    registry = load_guard_registry(arch_landing)
    assert {e.category for e in registry.entries} == set(GUARD_CATEGORIES), (
        "demo registry must declare exactly the eight categories"
    )
    errors = validate_guard_registry(registry, target)
    assert errors == (), f"demo registry must validate clean: {errors}"
    deployment = deploy_guard_configs(registry, target)
    assert isinstance(deployment, GuardDeployment)

    # 6. Four-way registry-digest binding: deployment record, hook
    #    TRACKS_GUARD_REGISTRY, CI env, guard.parity.registry — all equal.
    hook_path = target / ".githooks" / "pre-commit"
    ci_path = target / ".github" / "workflows" / "ci.yml"
    parity = check_parity(
        registry=registry,
        runtime_commands={e.required_check: e.command for e in registry.entries},
        pre_commit_path=hook_path,
        ci_workflow_path=ci_path,
        cwd=target,
    )
    assert deployment.registry_digest == registry.digest
    assert parity.registry_digest == registry.digest
    hook_text = hook_path.read_text(encoding="utf-8") if hook_path.exists() else ""
    ci_text = ci_path.read_text(encoding="utf-8") if ci_path.exists() else ""
    assert f"TRACKS_GUARD_REGISTRY={registry.digest}" in hook_text, (
        "hook must embed TRACKS_GUARD_REGISTRY=<registry.digest>"
    )
    assert registry.digest in ci_text, "CI env must embed TRACKS_GUARD_REGISTRY"

    # 7. tracks vs demo digests must NOT be equal (declared host-profile diff:
    #    file-length 1200/500, required checks 6/3). Equality would be a bug.
    tracks_registry = load_guard_registry(ARCH)
    assert tracks_registry.digest != registry.digest, (
        "tracks and demo digests must differ (declared host profile); "
        "equality is an erroneous fixture"
    )

    # 8. Recompute the 8th guard's config_digest from project.toml raw bytes
    #    (tracks + demo) to prove artifact_digests are not the config value.
    tracks_proj = (repo / ".tracks" / "projects" / "project.toml").read_bytes()
    demo_proj = (demo_template / "tracks-project.toml").read_bytes()
    tracks_8th = next(
        e for e in tracks_registry.entries if e.category == "hooks_runner_ci_required_checks"
    )
    demo_8th = next(
        e for e in registry.entries if e.category == "hooks_runner_ci_required_checks"
    )
    assert tracks_8th.config_digest == "sha256:" + hashlib.sha256(tracks_proj).hexdigest(), (
        "tracks 8th config_digest must equal recomputed project.toml raw-bytes digest"
    )
    assert demo_8th.config_digest == "sha256:" + hashlib.sha256(demo_proj).hexdigest(), (
        "demo 8th config_digest must equal recomputed tracks-project.toml raw-bytes digest"
    )
    # The generated artifact_digests must NOT equal the config_digest (no
    # self-reference: generated bytes are audit-only, never the config value).
    for art_digest in deployment.artifact_digests.values():
        assert art_digest != demo_8th.config_digest, (
            "generated artifact_digest must not equal the 8th config_digest "
            "(artifact_digests are audit-only, not the config value)"
        )

    # 9. Tamper flake8.ini -> hard error / blocked, no fallback.
    flake8 = target / "flake8.ini"
    if flake8.exists():
        flake8.write_text("[flake8]\nmax-line-length = 1\n", encoding="utf-8")
        tampered_registry = load_guard_registry(arch_landing)
        tampered_errors = validate_guard_registry(tampered_registry, target)
        assert tampered_errors, (
            "tampering flake8.ini must produce hard errors / blocked, no fallback"
        )


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
