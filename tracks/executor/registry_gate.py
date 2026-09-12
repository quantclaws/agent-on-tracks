"""Execute the selected canonical quality guards for a host local gate."""

from __future__ import annotations

import shlex
from dataclasses import asdict, replace

from tracks.executor.guard_registry import load_guard_registry, validate_guard_registry
from tracks.executor.host_contract import NormalizedGateResult, execute_gate


def execute_registry_gate(gate, repo, architecture, scope) -> NormalizedGateResult:
    """Validate the registry before executing its declared categories in order."""
    registry = load_guard_registry(architecture)
    errors = validate_guard_registry(registry, repo)
    if errors:
        raise ValueError("guard_registry invalid: " + "; ".join(errors))
    categories = gate.categories
    by_category = {entry.category: entry for entry in registry.entries}
    if not categories or len(set(categories)) != len(categories):
        raise ValueError("guard_registry categories must be nonempty and unique")
    if set(categories) - by_category.keys():
        raise ValueError("guard_registry contains unknown requested categories")
    results = []
    for category in categories:
        entry = by_category[category]
        command = entry.command if isinstance(entry.command, str) else shlex.join(entry.command)
        declaration = replace(gate, source="inline", command=command,
                              result_channel="exit_code", categories=(),
                              timeout_seconds=min(gate.timeout_seconds, entry.timeout_seconds))
        result = execute_gate(declaration, repo, scope)
        results.append({"guard_id": entry.guard_id, "category": category, **asdict(result)})
    passed = all(result["status"] == "passed" for result in results)
    return NormalizedGateResult(
        gate_id=gate.kind, result_version=1, status="passed" if passed else "failed",
        exit_code=0 if passed else 1,
        summary={"registry_digest": registry.digest, "guards": results},
    )
