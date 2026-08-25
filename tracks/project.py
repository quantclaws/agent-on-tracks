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

# D-41 atomic schema slice (interfaces §1m/§1n): ALL THREE flat layer
# sections plus [nightly] are required once the schema slice is active --
# the FULL chain and nightly CI run all three layers; an undeclared layer
# is a contract defect, not an optional convenience.
_TEST_LAYERS = ("unit", "integration", "e2e")
_NIGHTLY_REQUIRED_KEYS = ("schedule", "workflow", "job", "layers", "purpose")
_NIGHTLY_STRING_KEYS = ("schedule", "workflow", "job", "purpose")


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
    run_selected: str
    cwd: str


@dataclass(frozen=True, slots=True)
class NightlySection:
    """Required [nightly] section: scheduled FULL-suite regression (interfaces §1n)."""

    schedule: str
    workflow: str
    job: str
    layers: tuple[str, ...]
    purpose: str


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
class LintSection:
    """Archer-declared lint command (B4, issue #5): ``[lint] check``.

    The runtime is language-neutral and never hardcodes a linter; the
    project (Archer, M-DESIGN) decides which tool it uses. The declared
    command receives the changed deliverable paths as trailing args.
    """

    check: str


@dataclass(frozen=True, slots=True)
class AdapterDeclaration:
    """Declared host test adapter (interfaces §1h / project.toml ``[adapter]``).

    The loader surfaces a single versioned declaration to the Runtime: the
    ``id``/``protocol``/``version`` named by the host, so the Runtime consumes
    only the declared ``tracks-test-result`` protocol and never branches on a
    host framework (AC-FR0264-01).  Known-ness is enforced at load time: only
    the tracks-test-result v1 reference declaration is surfaced; any other
    combination fails closed (``None``) so the Runtime rejects an undeclared
    host contract instead of guessing (AC-FR0264-03).
    """

    id: str
    protocol: str
    version: int


@dataclass(frozen=True, slots=True)
class ProjectContract:
    integration: TestSection
    unit: TestSection
    e2e: TestSection
    nightly: NightlySection
    layout: LayoutConfig | None = None
    lint: LintSection | None = None
    adapter: AdapterDeclaration | None = None


def contract_path(repo: Path) -> Path:
    return paths.project_toml_path(paths.tracks_home(repo))


def load_contract(repo: Path) -> ProjectContract:
    """Load and validate the host project contract.

    Raises ``ContractError`` if the file is missing, unreadable, malformed,
    declares an unsupported framework, lacks any required layer section
    ([unit]/[integration]/[e2e]) or the required [nightly] section, or
    carries malformed run/run_selected placeholders.
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
    unit = _build_section(data, "unit")
    e2e = _build_section(data, "e2e")
    nightly = _build_nightly(data)
    layout = _build_layout(data)
    lint = _build_lint(data)
    adapter = _build_adapter(data)
    return ProjectContract(
        integration=integration,
        unit=unit,
        e2e=e2e,
        nightly=nightly,
        layout=layout,
        lint=lint,
        adapter=adapter,
    )


def _build_nightly(data: dict) -> NightlySection:
    """Parse the REQUIRED [nightly] section (interfaces §1n).

    Missing section/keys, a non-string or empty schedule/workflow/job/purpose
    value, or an invalid ``layers`` value is contract_error fail-closed at
    load time: nightly CI runs the declared FULL regression, and a malformed
    schedule contract must never degrade into a silently skipped job."""
    raw = data.get("nightly")
    if not isinstance(raw, dict):
        raise ContractError(
            "[nightly] section missing in project contract; the atomic schema "
            "requires the scheduled FULL-suite regression contract "
            f"({', '.join(_NIGHTLY_REQUIRED_KEYS)})",
            kind="contract",
        )
    missing = [key for key in _NIGHTLY_REQUIRED_KEYS if key not in raw]
    if missing:
        raise ContractError(
            f"[nightly].{missing[0]} is required in project contract "
            f"(required keys: {', '.join(_NIGHTLY_REQUIRED_KEYS)})",
            kind="contract",
        )
    for key in _NIGHTLY_STRING_KEYS:
        value = raw[key]
        if not isinstance(value, str) or not value.strip():
            raise ContractError(
                f"[nightly].{key} must be a non-empty string",
                kind="contract",
            )
    raw_layers = raw["layers"]
    if not isinstance(raw_layers, list) or not all(
        isinstance(layer, str) for layer in raw_layers
    ):
        raise ContractError(
            "[nightly].layers must be a list of strings drawn from the declared "
            f"test layers {list(_TEST_LAYERS)}",
            kind="contract",
        )
    unknown = [layer for layer in raw_layers if layer not in _TEST_LAYERS]
    if unknown:
        raise ContractError(
            f"[nightly].layers declares test layers outside the declared set: "
            f"{unknown} (declared test layers: {list(_TEST_LAYERS)})",
            kind="contract",
        )
    if len(set(raw_layers)) != len(raw_layers):
        raise ContractError(
            "[nightly].layers declares duplicate test layers "
            f"(each of {list(_TEST_LAYERS)} must appear exactly once)",
            kind="contract",
        )
    if set(raw_layers) != set(_TEST_LAYERS):
        raise ContractError(
            "[nightly].layers must declare exactly the three declared test "
            f"layers {list(_TEST_LAYERS)} once each "
            f"(missing: {sorted(set(_TEST_LAYERS) - set(raw_layers))})",
            kind="contract",
        )
    return NightlySection(
        schedule=raw["schedule"],
        workflow=raw["workflow"],
        job=raw["job"],
        layers=tuple(layer for layer in _TEST_LAYERS if layer in raw_layers),
        purpose=raw["purpose"],
    )


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
    _validate_placeholders(name, "run", run.strip(), with_nodes=False)
    raw_selected = section.get("run_selected")
    if not isinstance(raw_selected, str) or not raw_selected.strip():
        raise ContractError(
            f"[{name}].run_selected must be a non-empty string; a declared layer "
            "never falls back to appending nodeids to run",
            kind="contract",
        )
    _validate_placeholders(name, "run_selected", raw_selected.strip(), with_nodes=True)
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
        run_selected=raw_selected.strip(),
        cwd=cwd.strip(),
    )


def _validate_placeholders(name: str, key: str, template: str, *, with_nodes: bool) -> None:
    """Every run/run_selected template embeds {result} (and {nodes} when
    selected) exactly once; missing or duplicated placeholders are
    contract_error fail-closed at load time."""
    placeholders = {"{result}": template.count("{result}")}
    if with_nodes:
        placeholders["{nodes}"] = template.count("{nodes}")
    for placeholder, count in placeholders.items():
        if count != 1:
            raise ContractError(
                f"[{name}].{key} must embed {placeholder} exactly once (found {count}); "
                "Runtime substitutes only declared placeholders and never injects "
                "--junitxml or concurrency flags itself",
                kind="contract",
            )


def _build_lint(data: dict) -> LintSection | None:
    """Parse the optional [lint] section; None if absent or malformed.

    Degrades to None instead of raising: a broken [lint] must never make
    the whole test-execution contract unloadable (lint is hygiene; the
    test contract is load-bearing).
    """
    raw = data.get("lint")
    if not isinstance(raw, dict):
        return None
    check = raw.get("check")
    if not isinstance(check, str) or not check.strip():
        return None
    return LintSection(check=check.strip())


# Fixed, language-agnostic versioned adapter protocol the Runtime consumes
# exclusively (interfaces §1h / IF-ADAPTER-001 AC-FR0264-01): the loader
# surfaces ONLY the reference declaration; any other id/protocol/version is an
# undeclared host contract the Runtime must never guess at (AC-FR0264-03).
_ADAPTER_ID = "reference-pytest"
_ADAPTER_PROTOCOL = "tracks-test-result"
_ADAPTER_VERSION = 1


def _build_adapter(data: dict) -> AdapterDeclaration | None:
    """Parse the optional [adapter] declaration; None if absent/malformed/unknown.

    The loader surfaces the host's declared adapter as a single versioned
    declaration.  ``None`` when the section is absent, malformed, or declares
    an unknown id/protocol/version keeps pre-v0.7 contracts loadable and
    prevents the Runtime from guessing a host framework (AC-FR0264-03) —
    an undeclared host contract never degrades into a survivable
    ``AdapterDeclaration`` the Runtime could consume.
    """
    raw = data.get("adapter")
    if not isinstance(raw, dict):
        return None
    adapter_id = raw.get("id")
    protocol = raw.get("protocol")
    version = raw.get("version")
    if (
        not isinstance(adapter_id, str)
        or not adapter_id.strip()
        or not isinstance(protocol, str)
        or not protocol.strip()
        or not isinstance(version, int)
        or isinstance(version, bool)
        or adapter_id.strip() != _ADAPTER_ID
        or protocol.strip() != _ADAPTER_PROTOCOL
        or version != _ADAPTER_VERSION
    ):
        return None
    return AdapterDeclaration(
        id=adapter_id.strip(),
        protocol=protocol.strip(),
        version=version,
    )


def lint_check_command(repo: Path) -> str | None:
    """The declared lint command for this project, or None (skip lint).

    Same contract/loader as the write auditor (single truth). None on a
    missing/malformed contract too: gates fail-open (skip), never crash.
    """
    try:
        contract = load_contract(repo)
    except ContractError:
        return None
    return contract.lint.check if contract.lint is not None else None


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
