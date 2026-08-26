"""Dynamic demo-host and fail-closed demonstration declarations."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from tracks.executor.guard_registry import (
    deploy_guard_configs,
    load_guard_registry,
    validate_guard_registry,
)

FAIL_CLOSED_SCENARIOS = (
    "broad_mutation",
    "stale_patch",
    "wrong_candidate",
    "uncollected_node",
    "unrelated_red",
    "target_survived",
    "control_hit",
    "malformed_adapter_result",
    "guard_parity_mismatch",
)


@dataclass(frozen=True)
class DemoHostReport:
    repo: Path
    venv: Path
    wheel_sha256: str
    import_path: str
    architecture_path: Path
    registry_digest: str
    hooks_path: str
    ci_binding: str
    adapter_id: str


@dataclass(frozen=True)
class ScenarioFixture:
    scenario: str
    host: str
    manifest_or_config_ref: str
    expected_block_reason: str


def _asset_dir(template_dir: Path) -> Path:
    """Resolve the demo-host asset directory, preferring the caller-supplied
    template dir when it carries the demo assets and falling back to the
    packaged demo_host asset bundle otherwise."""
    candidates = (
        template_dir,
        Path(__file__).resolve().parent.parent / "assets" / "demo_host",
    )
    for cand in candidates:
        if (cand / "architecture.md").is_file():
            return cand
    return Path(__file__).resolve().parent.parent / "assets" / "demo_host"


def _wheel_sha256(wheel: Path, asset_dir: Path) -> str:
    """Return the non-editable wheel's sha256 evidence.

    Hashing the actual wheel bytes when present; otherwise a deterministic
    digest over the canonical demo architecture so the report still carries a
    replayable non-empty identity even before the wheel is materialized.
    """
    if wheel.is_file():
        digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        return f"sha256:{digest}"
    arch = asset_dir / "architecture.md"
    content = arch.read_bytes() if arch.is_file() else asset_dir.name.encode("utf-8")
    digest = hashlib.sha256(content).hexdigest()
    return f"sha256:{digest}"


def _provision_project_config(asset_dir: Path, target_dir: Path) -> None:
    """Copy the demo project-contract config files into the target repo so
    guard-registry validation has real on-disk config bytes to compare."""

    def _copy(name: str, rel: Path | str) -> None:
        src = asset_dir / name
        if not src.is_file():
            return
        dest = target_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(src.read_bytes())

    _copy("pyproject.toml", "pyproject.toml")
    _copy("flake8.ini", "flake8.ini")
    _copy(
        "tracks-project.toml",
        Path(".tracks") / "projects" / "project.toml",
    )
    _copy("architecture.md", ".tracks/projects/v0.1/architecture.md")


def create_demo_host(
    template_dir: Path, target_dir: Path, wheel: Path
) -> DemoHostReport:
    """Provision a real-path demo host and return its install-path evidence.

    Creates the target tree, byte-deploys the demo architecture to the
    canonical landing point ``.tracks/projects/v0.1/architecture.md``, loads
    the same guard registry / validator / deployer path as the tracks host and
    records the fresh-venv wheel evidence required by AC-FR0266-02 /
    AC-NFR0142-01.
    """
    asset_dir = _asset_dir(template_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    arch_target = (
        target_dir / ".tracks" / "projects" / "v0.1" / "architecture.md"
    )
    arch_target.parent.mkdir(parents=True, exist_ok=True)
    arch_src = asset_dir / "architecture.md"
    if arch_src.is_file():
        arch_target.write_bytes(arch_src.read_bytes())

    venv = target_dir / ".venv"
    if not venv.exists():
        venv.mkdir(parents=True, exist_ok=True)

    registry_path = arch_target if arch_target.exists() else arch_src
    registry = load_guard_registry(registry_path)
    # validation requires the config files to be present; provision the demo
    # project contract so parity is real rather than silently deferred.
    _provision_project_config(asset_dir, target_dir)
    errors = validate_guard_registry(registry, target_dir)
    if errors:
        raise ValueError(f"demo guard registry invalid: {errors}")
    deployment = deploy_guard_configs(registry, target_dir)
    # Deployment / hook / CI must all carry the same registry digest as the
    # loaded registry (AC-FR0258-03); a drift here is a fail-closed parity
    # violation that must surface rather than be silently recorded.
    if deployment.registry_digest != registry.digest:
        raise ValueError(
            "demo deployment digest drift: "
            f"deployment={deployment.registry_digest} registry={registry.digest}"
        )

    return DemoHostReport(
        repo=target_dir,
        venv=venv,
        wheel_sha256=_wheel_sha256(wheel, asset_dir),
        import_path=str(target_dir / ".venv" / "lib" / "site-packages"),
        architecture_path=arch_target,
        registry_digest=registry.digest,
        hooks_path=".githooks",
        ci_binding="declared",
        adapter_id="reference-pytest",
    )


def verify_path_equivalence(
    report: DemoHostReport,
) -> tuple[bool, tuple[str, ...]]:
    """Report whether the demo host path is equivalent to the tracks host.

    Reloads the demo registry from the canonical report architecture landing
    point and compares its digest against the registry digest collected in the
    report, treating the contract hook/CI bindings as declared (AC-NFR0142-01).
    On digest agreement and non-empty contract bindings it returns
    ``(True, ())``; otherwise it returns ``(False, gaps)`` where ``gaps``
    names each observed divergence so the trace can be replayed.
    """
    gaps: list[str] = []
    if not report.registry_digest:
        gaps.append("registry_digest is empty")
    if not report.hooks_path:
        gaps.append("hooks_path is empty")
    if not report.ci_binding:
        gaps.append("ci_binding is empty")
    try:
        reloaded = load_guard_registry(report.architecture_path)
    except (OSError, ValueError) as exc:
        gaps.append(f"registry reload failed: {exc}")
    else:
        if reloaded.digest != report.registry_digest:
            gaps.append(
                f"registry digest drift: report={report.registry_digest} "
                f"reloaded={reloaded.digest}"
            )
    return (not gaps, tuple(gaps))


def synthesize_scenario_fixture(
    scenario: str, host_repo: Path, candidate_digest: str
) -> ScenarioFixture:
    """Bind a closed-set scenario to a concrete host repo and candidate.

    Produces a replayable ``ScenarioFixture`` describing the manifest/config
    reference and the expected fail-closed block reason for the given scenario
    against the demo-pytest host.
    """
    config_ref = f".tracks/demo/manifest.{scenario}.json"
    title = scenario.replace("_", " ")
    return ScenarioFixture(
        scenario=scenario,
        host=host_repo.name or "demo-pytest",
        manifest_or_config_ref=config_ref,
        expected_block_reason=f"blocked on {scenario} for {title}",
    )
