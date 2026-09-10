"""Versioned host contract (FR-0281, IF-HOSTCONTRACT-001/002).

The ``[host-contract.*]`` namespaced sections of the host's
``.tracks/projects/project.toml`` are the machine truth Archer materializes
per host (architecture §1.0.3/§3.1): language/toolchain declaration,
dependency install, local gates for M-VERIFY, build/artifact, post-install
smoke, version scheme, M-SECURITY scan policy, required-CI binding and
M-PUBLISH operation plans.

The Runtime only executes declared commands and consumes versioned
``tracks-gate-result`` normalized results; it never interprets host language
semantics (NFR-0147). Unknown language/framework/tool or malformed result is
fail-closed with machine evidence for the M-DESIGN revision loop.
"""

from __future__ import annotations

import json
import posixpath
import re
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

import tomllib

GATE_RESULT_PROTOCOL = "tracks-gate-result"
GATE_RESULT_VERSION = 1

# Reference-contract default env interpreter (IF-HOSTCONTRACT-001): derived
# from the running environment (sys.prefix / sys.executable), never a
# hardcoded env spelling (NFR-0147 language neutrality). The Runtime process
# executes inside the project environment, so this resolves to the
# project-relative "<env-dir>/bin/<interpreter>" spelling the bundled
# project.toml ``[host-contract].install`` declares. Command-construction
# sites resolve interpreter paths through ``resolve_install_interpreter``
# and never spell one themselves; this constant is the boundary module's
# declared default for hosts without a loadable contract, not a consumer
# literal.
_ENV_DIRNAME = posixpath.basename(posixpath.normpath(sys.prefix))
DEFAULT_INSTALL_INTERPRETER = posixpath.join(
    _ENV_DIRNAME, "bin", posixpath.basename(sys.executable)
)

# Canonical host-contract location relative to a repo root (single truth
# for loaders that resolve the contract from a repo).
CANONICAL_CONTRACT_RELPATH = (".tracks", "projects", "project.toml")

LocalGateKind = Literal[
    "quality", "trace", "reach", "anti_slop", "version", "build", "smoke"
]
ResultChannel = Literal["exit_code", "file"]

CONTRACT_TABLES = frozenset(
    {
        "version",
        "language",
        "toolchain",
        "install",
        "local_gate",
        "version_scheme",
        "build",
        "smoke",
        "security_scan",
        "ci",
        "tracker",
        "operations",
    }
)
OPERATION_JOURNEYS = frozenset({"feature", "post_release", "dev"})
BASE_PLACEHOLDERS = frozenset(
    {
        "version",
        "major",
        "minor",
        "n",
        "ulid",
        "artifact",
        "prefix",
        "prefix_bin",
        "result",
    }
)
OPERATION_PLACEHOLDERS = BASE_PLACEHOLDERS | {
    "feature_tag",
    "patch_line",
    "prerelease_tag",
}
VERSION_FACT_KEYS = ("version", "major", "minor", "n", "ulid")

_PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")


@dataclass(frozen=True)
class LocalGateDecl:
    kind: LocalGateKind
    source: str
    command: str
    categories: tuple[str, ...]
    result_channel: ResultChannel
    timeout_seconds: int


@dataclass(frozen=True)
class VersionDecl:
    feature_tag: str
    patch_line: str
    prerelease_tag: str
    file: str | None = None
    key: str | None = None
    expect: str | None = None


@dataclass(frozen=True)
class SecurityScanDecl:
    scan_id: str
    tool: str
    tool_version: str
    install: str
    command: str
    result_channel: ResultChannel
    threshold: str
    timeout_seconds: int


@dataclass(frozen=True)
class OperationPlanDecl:
    journey: str
    steps: tuple[str, ...]
    requires: tuple[str, ...]


@dataclass(frozen=True)
class HostContract:
    contract_version: int
    language: str
    toolchain: str
    install: str
    local_gates: tuple[LocalGateDecl, ...]
    version: VersionDecl
    build_command: str
    build_artifact: str
    smoke: tuple[str, ...]
    security_scans: tuple[SecurityScanDecl, ...]
    ci: dict
    tracker: dict
    operations: dict[str, OperationPlanDecl] = field(default_factory=dict)


@dataclass(frozen=True)
class NormalizedGateResult:
    gate_id: str
    result_version: int
    status: Literal["passed", "failed", "malformed"]
    exit_code: int | None
    summary: dict
    command_echo: tuple[str, ...] = ()


def _fail_closed(message: str) -> None:
    raise ValueError(f"host-contract: {message}")


def _required(table: dict, key: str, label: str):
    if key not in table:
        _fail_closed(f"{label} missing required key {key!r}")
    return table[key]


def _closed(table: dict, key: str, closed: frozenset, label: str):
    value = _required(table, key, label)
    if value not in closed:
        _fail_closed(f"{label} has unknown {key} {value!r}")
    return value


def _load_local_gate(raw: dict) -> LocalGateDecl:
    label = "local_gate"
    return LocalGateDecl(
        kind=_closed(raw, "kind", set(LocalGateKind.__args__), label),
        source=raw.get("source", "command"),
        command=raw.get("command", ""),
        categories=tuple(raw.get("categories", ())),
        result_channel=_closed(raw, "result_channel", set(ResultChannel.__args__), label),
        timeout_seconds=int(raw.get("timeout_seconds", 60)),
    )


def _load_operations(raw: dict) -> dict[str, OperationPlanDecl]:
    plans: dict[str, OperationPlanDecl] = {}
    for journey, body in raw.items():
        if journey not in OPERATION_JOURNEYS:
            _fail_closed(f"unknown operations journey {journey!r}")
        plans[journey] = OperationPlanDecl(
            journey=journey,
            steps=tuple(body.get("steps", ())),
            requires=tuple(body.get("requires", ())),
        )
    return plans


def load_host_contract(path: Path) -> HostContract:
    """Parse the ``[host-contract.*]`` tables of a project.toml (fail-closed)."""
    with open(path, "rb") as handle:
        data = tomllib.load(handle)
    table = data.get("host-contract")
    if not isinstance(table, dict):
        _fail_closed("missing [host-contract] table")
    unknown = sorted(set(table) - CONTRACT_TABLES)
    if unknown:
        _fail_closed(f"unknown host-contract table(s): {', '.join(unknown)}")
    if table.get("version") != 1:
        _fail_closed("[host-contract].version must be 1")
    scheme = table.get("version_scheme", {})
    build = table.get("build", {})
    return HostContract(
        contract_version=table["version"],
        language=_required(table, "language", "[host-contract]"),
        toolchain=_required(table, "toolchain", "[host-contract]"),
        install=_required(table, "install", "[host-contract]"),
        local_gates=tuple(_load_local_gate(g) for g in table.get("local_gate", ())),
        version=VersionDecl(
            feature_tag=scheme.get("feature_tag", ""),
            patch_line=scheme.get("patch_line", ""),
            prerelease_tag=scheme.get("prerelease_tag", ""),
            file=scheme.get("file"),
            key=scheme.get("key"),
            expect=scheme.get("expect"),
        ),
        build_command=build.get("command", ""),
        build_artifact=build.get("artifact", ""),
        smoke=tuple(table.get("smoke", {}).get("steps", ())),
        security_scans=tuple(
            SecurityScanDecl(
                scan_id=_required(scan, "id", "security_scan"),
                tool=_required(scan, "tool", "security_scan"),
                tool_version=scan.get("tool_version", ""),
                install=scan.get("install", ""),
                command=_required(scan, "command", "security_scan"),
                result_channel=_closed(
                    scan, "result_channel", set(ResultChannel.__args__), "security_scan"
                ),
                threshold=scan.get("threshold", ""),
                timeout_seconds=int(scan.get("timeout_seconds", 600)),
            )
            for scan in table.get("security_scan", ())
        ),
        ci=dict(table.get("ci", {})),
        tracker=dict(table.get("tracker", {})),
        operations=_load_operations(table.get("operations", {})),
    )


def _scan_placeholders(errors: list[str], text: str, allowed: frozenset, label: str) -> None:
    for name in _PLACEHOLDER_RE.findall(text or ""):
        if name not in allowed:
            errors.append(f"{label}: unknown placeholder {{{name}}}")


def validate_host_contract(contract: HostContract, repo: Path) -> tuple[str, ...]:
    """Report unknown placeholders across every declared command-bearing field."""
    errors: list[str] = []
    _scan_placeholders(errors, contract.install, BASE_PLACEHOLDERS, "install")
    for gate in contract.local_gates:
        _scan_placeholders(errors, gate.command, BASE_PLACEHOLDERS, f"local_gate[{gate.kind}]")
    for template in (
        contract.version.feature_tag,
        contract.version.patch_line,
        contract.version.prerelease_tag,
    ):
        _scan_placeholders(errors, template, BASE_PLACEHOLDERS, "version_scheme")
    _scan_placeholders(errors, contract.build_command, BASE_PLACEHOLDERS, "build.command")
    _scan_placeholders(errors, contract.build_artifact, BASE_PLACEHOLDERS, "build.artifact")
    for step in contract.smoke:
        _scan_placeholders(errors, step, BASE_PLACEHOLDERS, "smoke")
    for scan in contract.security_scans:
        label = f"security_scan[{scan.scan_id}]"
        _scan_placeholders(errors, scan.command, BASE_PLACEHOLDERS, label)
    for journey in contract.operations.values():
        for step in journey.steps:
            _scan_placeholders(
                errors, step, OPERATION_PLACEHOLDERS, f"operations[{journey.journey}]"
            )
    return tuple(errors)


def _render_gate_command(decl: LocalGateDecl, placeholders: dict) -> tuple[str, Path | None]:
    scope = dict(placeholders)
    result_path = None
    if decl.result_channel == "file":
        result_path = Path(tempfile.mkdtemp(prefix="gate-result-")) / "result.json"
        scope["result"] = str(result_path)

    def substitute(match: re.Match) -> str:
        name = match.group(1)
        if name not in scope:
            raise ValueError(f"host-contract: unknown placeholder {{{name}}} in gate command")
        return str(scope[name])

    return _PLACEHOLDER_RE.sub(substitute, decl.command), result_path


def _run_declared(decl: LocalGateDecl, argv: list[str], repo: Path):
    try:
        return subprocess.run(
            argv,
            cwd=str(repo),
            shell=False,
            capture_output=True,
            timeout=decl.timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None


def _evidence(returncode: int | None, stdout: bytes, stderr: bytes) -> dict:
    return {
        "exit": returncode,
        "stdout": stdout.decode("utf-8", errors="replace"),
        "stderr": stderr.decode("utf-8", errors="replace"),
    }


def _timed_out(decl: LocalGateDecl) -> NormalizedGateResult:
    return NormalizedGateResult(
        gate_id=decl.kind,
        result_version=GATE_RESULT_VERSION,
        status="failed",
        exit_code=None,
        summary={
            "exit": None,
            "stdout": "",
            "stderr": f"gate timed out after {decl.timeout_seconds}s",
        },
    )


def execute_gate(
    decl: LocalGateDecl, repo: Path, placeholders: dict
) -> NormalizedGateResult:
    """Run one declared gate command (shlex, shell=False) under its timeout."""
    rendered, result_path = _render_gate_command(decl, placeholders)
    argv = shlex.split(rendered)
    try:
        completed = _run_declared(decl, argv, repo)
    except OSError as err:
        return NormalizedGateResult(
            gate_id=decl.kind,
            result_version=GATE_RESULT_VERSION,
            status="failed",
            exit_code=None,
            summary={"error": str(err)},
            command_echo=tuple(argv),
        )
    if completed is None:
        return replace(_timed_out(decl), command_echo=tuple(argv))
    evidence = _evidence(completed.returncode, completed.stdout, completed.stderr)
    if decl.result_channel == "exit_code":
        result = parse_gate_result(None, "exit_code", completed.returncode)
        return replace(result, gate_id=decl.kind, summary=evidence, command_echo=tuple(argv))
    raw = result_path.read_bytes() if result_path is not None and result_path.exists() else None
    result = parse_gate_result(raw, "file", completed.returncode)
    if completed.returncode != 0:
        result = replace(result, status="failed", exit_code=completed.returncode)
    return replace(
        result,
        gate_id=decl.kind,
        summary={**result.summary, **evidence},
        command_echo=tuple(argv),
    )


def _parse_result_blob(raw: bytes | None) -> tuple[dict | None, str]:
    if raw is None:
        return None, "gate result file missing"
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"gate result is not valid JSON: {exc}"
    if not isinstance(payload, dict):
        return None, "gate result is not a JSON object"
    for key in ("schema", "version", "status"):
        if key not in payload:
            return None, f"gate result missing {key!r}"
    if payload["schema"] != GATE_RESULT_PROTOCOL:
        return None, f"gate result schema {payload['schema']!r} not recognized"
    if payload["version"] != GATE_RESULT_VERSION:
        return None, f"gate result version {payload['version']!r} not supported"
    if payload["status"] not in ("passed", "failed"):
        return None, f"gate result status {payload['status']!r} not recognized"
    return payload, ""


def parse_gate_result(
    raw: bytes | None, channel: ResultChannel, exit_code: int | None
) -> NormalizedGateResult:
    """Normalize a gate outcome into ``tracks-gate-result`` v1 (fail-closed)."""
    if channel == "exit_code":
        status = "passed" if exit_code == 0 else "failed"
        return NormalizedGateResult(
            gate_id="",
            result_version=GATE_RESULT_VERSION,
            status=status,
            exit_code=exit_code,
            summary={"exit": exit_code, "stdout": "", "stderr": ""},
        )
    payload, reason = _parse_result_blob(raw)
    if payload is None:
        return NormalizedGateResult(
            gate_id="",
            result_version=GATE_RESULT_VERSION,
            status="malformed",
            exit_code=None,
            summary={"error": reason},
        )
    return NormalizedGateResult(
        gate_id="",
        result_version=GATE_RESULT_VERSION,
        status=payload["status"],
        exit_code=payload.get("exit_code"),
        summary=payload.get("summary", {}),
    )


def resolve_install_interpreter(contract: HostContract | None = None) -> str:
    """The contract-declared env interpreter for command construction.

    First token of the declared ``install`` command; the reference-contract
    default when no contract is loaded (fail-open construction parity, same
    contract/loader family as ``lint_check_command``). Command-construction
    sites never spell an interpreter themselves (NFR-0147 language
    neutrality): the spelling lives here and in host declarations only.
    """
    raw = contract.install if contract is not None else DEFAULT_INSTALL_INTERPRETER
    if not isinstance(raw, str) or not raw.strip():
        return DEFAULT_INSTALL_INTERPRETER
    return shlex.split(raw)[0]


def declared_install_interpreter(repo: Path | str) -> str:
    """The repo's contract-declared env interpreter (IF-HOSTCONTRACT-001).

    Repo-level resolution for command-construction sites: loads the
    canonical ``[host-contract]`` table and returns its declared install
    interpreter; the reference-contract default on a missing/malformed
    contract, so construction never hardcodes the spelling (NFR-0147).
    Lives on this leaf module (no internal imports) so every executor
    consumer can import it without import-order hazards.
    """
    try:
        contract = load_host_contract(
            Path(repo).joinpath(*CANONICAL_CONTRACT_RELPATH)
        )
    except (OSError, ValueError):
        return resolve_install_interpreter(None)
    return resolve_install_interpreter(contract)


def resolve_placeholders(version_facts: dict, artifact: str, prefix: str) -> dict:
    """Resolve the base closed placeholder set from run version facts."""
    missing = [key for key in VERSION_FACT_KEYS if key not in version_facts]
    if missing:
        raise ValueError(f"host-contract: version facts missing {', '.join(missing)}")
    resolved = {key: str(version_facts[key]) for key in VERSION_FACT_KEYS}
    resolved["artifact"] = artifact
    resolved["prefix"] = prefix
    resolved["prefix_bin"] = str(Path(prefix) / "bin")
    return resolved
