"""Host project test execution contract (``.tracks/projects/project.toml``).

Reads the host project's test execution contract committed at
``.tracks/projects/project.toml`` during M-TEST.  The contract declares the
framework, paths, and shell commands for collecting and running tests, plus
the working directory to execute them from.

v0.4 supports only ``framework = "pytest"``.  An unsupported framework or a
missing/malformed contract fails closed as an infrastructure finding — the
runtime never falls back to ``sys.executable -m pytest``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import tomllib

from tracks import paths

_SUPPORTED_FRAMEWORKS = frozenset({"pytest"})


class ContractError(Exception):
    """Raised when the host project contract is missing, malformed, or
    declares an unsupported framework."""

    def __init__(self, reason: str, *, kind: str = "contract"):
        super().__init__(reason)
        self.reason = reason
        self.kind = kind


@dataclass(frozen=True, slots=True)
class TestSection:
    framework: str
    paths: list[str]
    collect: str
    run: str
    cwd: str


@dataclass(frozen=True, slots=True)
class AgentLayout:
    """Writable directories for a single agent role (FR-0120 layout)."""

    writable: list[str]


@dataclass(frozen=True, slots=True)
class LayoutConfig:
    """Project directory layout designed by Archer in M-DESIGN."""

    devon: AgentLayout | None = None
    shield: AgentLayout | None = None


@dataclass(frozen=True, slots=True)
class ProjectContract:
    integration: TestSection
    e2e: TestSection | None = None
    layout: LayoutConfig | None = None


def contract_path(repo: Path) -> Path:
    return paths.project_toml_path(paths.tracks_home(repo))


def load_contract(repo: Path) -> ProjectContract:
    """Load and validate the host project contract.

    Raises ``ContractError`` if the file is missing, unreadable, malformed,
    or declares an unsupported framework.
    """
    toml_path = contract_path(repo)
    if not toml_path.exists():
        raise ContractError(
            f"project contract not found: {toml_path}",
            kind="contract",
        )
    try:
        raw = toml_path.read_bytes()
        data = tomllib.loads(raw.decode("utf-8"))
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ContractError(
            f"project contract unreadable: {exc}",
            kind="contract",
        ) from exc
    return _build_contract(data)


def _build_contract(data: dict) -> ProjectContract:
    integration = _build_section(data, "integration")
    e2e_raw = data.get("e2e")
    e2e: TestSection | None = None
    if e2e_raw is not None:
        e2e = _build_section(data, "e2e")
    layout = _build_layout(data)
    return ProjectContract(integration=integration, e2e=e2e, layout=layout)


def _build_layout(data: dict) -> LayoutConfig | None:
    """Parse [layout] section; None if absent (backward compatible)."""
    layout_raw = data.get("layout")
    if not isinstance(layout_raw, dict):
        return None
    devon = _build_agent_layout(layout_raw, "devon")
    shield = _build_agent_layout(layout_raw, "shield")
    if devon is None and shield is None:
        return None
    return LayoutConfig(devon=devon, shield=shield)


def _build_agent_layout(layout_data: dict, role: str) -> AgentLayout | None:
    section = layout_data.get(role)
    if not isinstance(section, dict):
        return None
    raw_writable = section.get("writable")
    if not isinstance(raw_writable, list) or not raw_writable:
        return None
    return AgentLayout(writable=[str(p) for p in raw_writable])


def _build_section(data: dict, name: str) -> TestSection:
    section = data.get(name)
    if not isinstance(section, dict):
        raise ContractError(
            f"[{name}] section missing in project contract",
            kind="contract",
        )
    framework = section.get("framework")
    if not isinstance(framework, str) or not framework.strip():
        raise ContractError(
            f"[{name}].framework must be a non-empty string",
            kind="contract",
        )
    if framework not in _SUPPORTED_FRAMEWORKS:
        raise ContractError(
            f"[{name}].framework {framework!r} is not supported "
            f"(supported: {sorted(_SUPPORTED_FRAMEWORKS)})",
            kind="framework",
        )
    raw_paths = section.get("paths")
    if not isinstance(raw_paths, list) or not raw_paths:
        raise ContractError(
            f"[{name}].paths must be a non-empty list",
            kind="contract",
        )
    section_paths = [str(p) for p in raw_paths]
    collect = section.get("collect")
    if not isinstance(collect, str) or not collect.strip():
        raise ContractError(
            f"[{name}].collect must be a non-empty string",
            kind="contract",
        )
    run = section.get("run")
    if not isinstance(run, str) or not run.strip():
        raise ContractError(
            f"[{name}].run must be a non-empty string",
            kind="contract",
        )
    cwd = section.get("cwd", ".")
    if not isinstance(cwd, str) or not cwd.strip():
        raise ContractError(
            f"[{name}].cwd must be a non-empty string",
            kind="contract",
        )
    return TestSection(
        framework=framework,
        paths=section_paths,
        collect=collect.strip(),
        run=run.strip(),
        cwd=cwd.strip(),
    )


def layout_paths(repo: Path, role: str) -> list[str]:
    """Return writable dir paths for a role from project.toml [layout].

    Returns an empty list if the contract or [layout] section is absent,
    so the auditor fails closed (all writes flagged as over-reach).
    """
    try:
        contract = load_contract(repo)
    except ContractError:
        return []
    if contract.layout is None:
        return []
    agent = getattr(contract.layout, role, None)
    if agent is None:
        return []
    return agent.writable


def validate_layout(repo: Path) -> str | None:
    """Validate that project.toml declares non-empty [layout.devon] and
    [layout.shield] sections (Archer M-DESIGN contract, FR-0120).

    Returns an English reason string on failure, None on success.  Called
    during M-DESIGN EXIT validation when architecture.md is gated.
    """
    try:
        contract = load_contract(repo)
    except ContractError as exc:
        return f"project contract unreadable: {exc.reason}"
    if contract.layout is None:
        return (
            "project.toml [layout] section is missing; Archer must declare "
            "[layout.devon] and [layout.shield] with non-empty writable lists"
        )
    if contract.layout.devon is None:
        return (
            "project.toml [layout.devon] is missing or has an empty writable "
            "list; Devon needs at least one writable directory"
        )
    if contract.layout.shield is None:
        return (
            "project.toml [layout.shield] is missing or has an empty writable "
            "list; Shield needs at least one writable directory"
        )
    return None


# Fixed, language-agnostic design docs each role may comment on (tracks-decided,
# not project.toml [layout] which only governs code/data dirs).
COMMENTABLE_DOCS: dict[str, tuple[str, ...]] = {
    "shield": ("test-plan.md", "interfaces.md"),
    "devon": ("architecture.md", "interfaces.md"),
}
