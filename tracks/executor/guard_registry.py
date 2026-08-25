"""Canonical quality-guard registry declarations."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import tomllib

GUARD_CATEGORIES = (
    "lint_format",
    "static_analysis",
    "cognitive_complexity",
    "file_length",
    "method_length_locals",
    "duplication",
    "coverage_threshold",
    "hooks_runner_ci_required_checks",
)


@dataclass(frozen=True)
class GuardEntry:
    guard_id: str
    category: str
    tool: str
    tool_version: str
    command: tuple[str, ...]
    config_paths: tuple[str, ...]
    config_sections: tuple[str, ...]
    config_digest: str
    scope: tuple[str, ...]
    threshold: str
    timeout_seconds: int
    failure_policy: Literal["fail_closed"]
    execution_points: tuple[str, ...]
    required_check: str


@dataclass(frozen=True)
class GuardRegistry:
    version: int
    host: str
    entries: tuple[GuardEntry, ...]
    digest: str


@dataclass(frozen=True)
class GuardMismatch:
    place: Literal["runtime", "pre_commit", "ci"]
    guard_id: str
    kind: str
    detail: str


@dataclass(frozen=True)
class ParityReport:
    registry_digest: str
    runtime_match: bool
    pre_commit_match: bool
    ci_match: bool
    mismatches: tuple[GuardMismatch, ...]


@dataclass(frozen=True)
class GuardDeployment:
    registry_digest: str
    pre_commit_path: Path
    ci_workflow_path: Path
    artifact_digests: Mapping[str, str]


def _canonicalize(value: object) -> object:
    """Normalize a parsed value for deterministic canonical JSON (dict keys
    sorted; arrays keep order)."""
    if isinstance(value, dict):
        return {str(k): _canonicalize(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_canonicalize(v) for v in value]
    return value


def _registry_digest(version: int, host: str, entries: Sequence[GuardEntry]) -> str:
    """``sha256:<hex>`` digest of the canonical JSON of the registry, EXCLUDING
    the registry's own digest field (interfaces §1k / §1e): stable and
    input-sensitive.  Same ``sha256:`` prefix convention as config digests."""
    payload = _canonicalize(
        {
            "version": version,
            "host": host,
            "entries": [
                {
                    "guard_id": e.guard_id,
                    "category": e.category,
                    "tool": e.tool,
                    "tool_version": e.tool_version,
                    "command": e.command,
                    "config_paths": list(e.config_paths),
                    "config_sections": list(e.config_sections),
                    "config_digest": e.config_digest,
                    "scope": list(e.scope),
                    "threshold": e.threshold,
                    "timeout_seconds": e.timeout_seconds,
                    "failure_policy": e.failure_policy,
                    "execution_points": list(e.execution_points),
                    "required_check": e.required_check,
                }
                for e in entries
            ],
        }
    )
    raw = json.dumps(
        payload, ensure_ascii=True, sort_keys=False, separators=(",", ":")
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


_REGISTRY_HEADING = re.compile(r"^#{2,3}\s+4\.2\s+[^\n]*(?:guard|registry)", re.IGNORECASE)
_TOML_FENCE = re.compile(r"^\s*```toml\s*$")
_FENCE_END = re.compile(r"^\s*```\s*$")


def _extract_registry_toml_block(text: str) -> str:
    """Extract the canonical ``toml`` fence block directly under the registry
    heading (interfaces §1k). The block must open a ``[quality_registry]``
    table. Zero/multiple blocks or a non-toml fence fail closed."""
    lines = text.splitlines()
    heading_idx = None
    for idx, line in enumerate(lines):
        if _REGISTRY_HEADING.match(line):
            heading_idx = idx
            break
    if heading_idx is None:
        raise ValueError("architecture.md has no quality-guard registry heading")

    open_idx = None
    for idx in range(heading_idx + 1, len(lines)):
        if _TOML_FENCE.match(lines[idx]):
            open_idx = idx
            break
    if open_idx is None:
        raise ValueError("registry block is not a TOML fence")

    end_idx = None
    for idx in range(open_idx + 1, len(lines)):
        if _FENCE_END.match(lines[idx]):
            end_idx = idx
            break
    if end_idx is None:
        raise ValueError("registry TOML fence is unclosed")

    return "\n".join(lines[open_idx + 1 : end_idx])


_GUARD_REQUIRED_FIELDS = (
    "id",
    "category",
    "tool",
    "tool_version",
    "command",
    "config_paths",
    "config_sections",
    "config_digest",
    "scope",
    "threshold",
    "timeout_seconds",
    "failure_policy",
    "execution_points",
    "required_check",
)


def _parse_guard_entry(table: object) -> GuardEntry:
    """Build a single ``GuardEntry`` from a parsed [[quality_guard]] table;
    fail closed on a missing field or a malformed config_digest."""
    if not isinstance(table, dict):
        raise ValueError("each [[quality_guard]] must be a table")
    for key in _GUARD_REQUIRED_FIELDS:
        if key not in table:
            raise ValueError(f"[[quality_guard]].{key} is required")
    config_digest = table["config_digest"]
    if not isinstance(config_digest, str) or not config_digest.strip():
        raise ValueError("[[quality_guard]].config_digest must be a non-empty string")
    return GuardEntry(
        guard_id=table["id"],
        category=table["category"],
        tool=table["tool"],
        tool_version=table["tool_version"],
        command=table["command"],
        config_paths=tuple(table["config_paths"]),
        config_sections=tuple(table["config_sections"]),
        config_digest=config_digest,
        scope=tuple(table["scope"]),
        threshold=table["threshold"],
        timeout_seconds=table["timeout_seconds"],
        failure_policy=table["failure_policy"],
        execution_points=tuple(table["execution_points"]),
        required_check=table["required_check"],
    )


def load_guard_registry(architecture_path: Path) -> GuardRegistry:
    """Load and validate the canonical ``[quality_registry]`` block.

    Parses the single ``toml`` fence under the §4.2 registry heading from the
    given architecture document.  The block's first table must be
    ``[quality_registry]`` with exactly the eight ``[[quality_guard]]``
    categories (AC-FR0258-01 single source of truth).  Zero/multiple blocks,
    a wrong first table, or an entry missing its ``config_digest`` fail closed
    with ``ValueError``.
    """
    text = Path(architecture_path).read_text(encoding="utf-8")
    block = _extract_registry_toml_block(text)
    data = tomllib.loads(block)
    registry_head = data.get("quality_registry")
    if not isinstance(registry_head, dict):
        raise ValueError("registry block must open with [quality_registry]")
    version = registry_head.get("version")
    host = registry_head.get("host")
    if not isinstance(version, int) or version < 1:
        raise ValueError("[quality_registry].version must be a positive integer")
    if not isinstance(host, str) or not host.strip():
        raise ValueError("[quality_registry].host must be a non-empty string")

    guards = data.get("quality_guard")
    if not isinstance(guards, list):
        raise ValueError("registry block is missing [[quality_guard]] entries")
    if len(guards) != len(GUARD_CATEGORIES):
        raise ValueError(
            f"registry must declare exactly {len(GUARD_CATEGORIES)} [[quality_guard]] "
            f"entries (found {len(guards)})"
        )

    entries = [_parse_guard_entry(g) for g in guards]
    return GuardRegistry(
        version=version,
        host=host,
        entries=tuple(entries),
        digest=_registry_digest(version, host, entries),
    )


def _config_file_digest_matches(repo: Path, path: str, digest: str) -> bool:
    """Resolve the declared digest against the raw config bytes."""
    if not digest.startswith("sha256:"):
        raise ValueError(f"config_digest must start with sha256: {digest}")
    raw = (repo / path).read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    return actual == digest[len("sha256:"):]


def _entry_errors(e: GuardEntry, repo: Path) -> list[str]:
    """Hard-error strings for a single guard entry."""
    errors: list[str] = []
    if not e.tool_version or not e.tool_version.strip():
        errors.append(f"unpinned tool: {e.guard_id} (empty tool_version)")
    if "--exit-zero" in e.command:
        errors.append(f"illegal --exit-zero command: {e.guard_id}")
    if e.failure_policy != "fail_closed":
        errors.append(f"failure_policy must be fail_closed: {e.guard_id}")
    if not e.required_check or not e.required_check.strip():
        errors.append(f"missing required_check: {e.guard_id}")
    for declared_path in e.config_paths:
        if not (repo / declared_path).exists():
            errors.append(f"config file missing: {declared_path}")
            continue
        try:
            if not _config_file_digest_matches(repo, declared_path, e.config_digest):
                errors.append(f"config digest drift: {declared_path} ({e.guard_id})")
        except (FileNotFoundError, ValueError, OSError) as exc:
            errors.append(f"config digest error for {declared_path}: {exc}")
    return errors


def validate_guard_registry(registry: GuardRegistry, repo: Path) -> tuple[str, ...]:
    """Return hard-error strings for an inconsistent registry (IF-GUARD-001).

    Catches: missing/duplicate category, unpinned tool, ``--exit-zero``,
    non-fail_closed policy, missing required check, absent config file, and
    config-digest drift against the on-disk config bytes.  Empty tuple when the
    registry is complete and consistent (AC-FR0258-01/04; FR-0259 completeness).
    """
    errors: list[str] = []

    categories = [e.category for e in registry.entries]
    missing = sorted(set(GUARD_CATEGORIES) - set(categories))
    errors.extend(f"missing guard category: {cat}" for cat in missing)
    seen: set[str] = set()
    for cat in categories:
        if cat in seen:
            errors.append(f"duplicate category: {cat}")
        seen.add(cat)

    for e in registry.entries:
        errors.extend(_entry_errors(e, repo))

    return tuple(errors)


def check_parity(
    registry: GuardRegistry,
    runtime_commands: Mapping[str, Sequence[str]],
    pre_commit_path: Path,
    ci_workflow_path: Path,
    cwd: Path,
) -> ParityReport:
    raise NotImplementedError("IF-GUARD-002")


def deploy_guard_configs(
    registry: GuardRegistry, target_repo: Path
) -> GuardDeployment:
    raise NotImplementedError("IF-GUARD-002")
