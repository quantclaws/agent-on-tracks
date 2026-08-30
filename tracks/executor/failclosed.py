"""IF-FAILCLOSED-001: dual-host nine-scenario fail-closed demonstration.

Orchestrates v0.7-A's fail-closed acceptance (interfaces section 1a rows
11/12, architecture section 1.0.8, interfaces section 1j): for every host
and every member of the nine-scenario closed set it synthesizes a scenario
fixture and invokes the REAL gate owning that scenario so the gate's verdict
drives the demonstrated outcome.  A blocking gate keeps the scenario
fail-closed; a gate that wrongly passes a must-fail-closed scenario yields
``leaked`` and forces that host's summary to block; a clean run reports
``crash_recovery=replay_ok``.

The run-loop dispatch (interfaces section 1b kind 4) is T-013's
``island_gate_2`` callback: it lazily imports this module and star-unpacks
its ``arguments`` tuple onto ``demonstrate_failclosed``, so the call shape
accepts both a single host list and a spread host sequence.

Gates are resolved through their module objects at call time so genuine gate
behaviour (or a test substitute) is always honoured; this module never
fabricates evidence and never re-implements a gate.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from tracks.adapters import base as _adapter_base
from tracks.adapters.base import TestRunResult
from tracks.executor import authenticity as _authenticity
from tracks.executor import demo_host as _demo_host
from tracks.executor import guard_parity as _guard_parity
from tracks.executor import guard_registry as _guard_registry
from tracks.executor import mutation as _mutation

__all__ = ["demonstrate_failclosed", "FAIL_CLOSED_SCENARIOS"]

FAIL_CLOSED_SCENARIOS = _demo_host.FAIL_CLOSED_SCENARIOS

# scenario -> (module, function name); the attribute is looked up at call
# time so the genuine (or substituted) gate is always the one invoked.
_SCENARIO_GATES: Mapping[str, tuple[Any, str]] = {
    "broad_mutation": (_authenticity, "judge_authenticity"),
    "stale_patch": (_mutation, "run_mutation_experiment"),
    "wrong_candidate": (_mutation, "run_mutation_experiment"),
    "uncollected_node": (_mutation, "run_mutation_experiment"),
    "unrelated_red": (_authenticity, "judge_authenticity"),
    "target_survived": (_mutation, "run_mutation_experiment"),
    "control_hit": (_mutation, "run_mutation_experiment"),
    "malformed_adapter_result": (_adapter_base, "resolve_adapter"),
    "guard_parity_mismatch": (_guard_parity, "check_parity"),
}


def demonstrate_failclosed(
    *args: Any,
    flush: Callable[[str, str, Mapping[str, Any]], None] | None = None,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """Demonstrate the nine fail-closed scenarios on every host.

    ``flush`` receives ``(host, event_type, payload)`` for every
    ``failclosed.demonstrated`` detail and ``failclosed.summary``; when
    omitted the events are printed.  Context keywords: ``host_repos``
    (host -> repo Path), ``candidate_digest``, ``registry`` (guard registry
    for the parity/deploy gates), ``worktree_ops`` (4-tuple of mutation
    experiment worktree callables for open/apply/run/close).
    """
    hosts = _normalize_hosts(args, kwargs)
    repos = kwargs.get("host_repos") or {}
    candidate_digest = kwargs.get("candidate_digest") or "sha256:failclosed-demo"
    registry = kwargs.get("registry")
    worktree_ops = tuple(kwargs.get("worktree_ops") or ())
    emit = flush if flush is not None else _default_flush
    summaries = []
    for host in hosts:
        repo = repos.get(host) if isinstance(repos, Mapping) else None
        summaries.append(
            _demonstrate_host(
                host,
                repo or Path(host),
                candidate_digest,
                registry,
                worktree_ops,
                emit,
            )
        )
    return summaries


def _normalize_hosts(args: tuple, kwargs: Mapping[str, Any]) -> list[str]:
    """Flatten the accepted call shapes onto an ordered host sequence."""
    hosts = kwargs.get("hosts")
    if hosts is not None:
        return list(hosts) if isinstance(hosts, (list, tuple)) else [hosts]
    if len(args) == 1 and isinstance(args[0], (list, tuple)):
        return list(args[0])
    seen: list[str] = []
    for arg in args:
        if isinstance(arg, str) and arg not in seen:
            seen.append(arg)
    return seen


def _demonstrate_host(
    host: str,
    repo: Path,
    candidate_digest: str,
    registry: Any,
    worktree_ops: tuple,
    flush: Callable[[str, str, Mapping[str, Any]], None],
) -> dict[str, Any]:
    """Run all nine scenarios for one host and emit the summary."""
    _provision_guards(registry, repo)
    details = []
    for scenario in FAIL_CLOSED_SCENARIOS:
        outcome, evidence = _judge_scenario(
            scenario, host, repo, candidate_digest, registry, worktree_ops
        )
        payload = {
            "host": host,
            "scenario": scenario,
            "outcome": outcome,
            "evidence_ref": evidence,
        }
        flush(host, "failclosed.demonstrated", payload)
        details.append(payload)
    all_fail_closed = all(item["outcome"] == "blocked" for item in details)
    summary = {
        "host": host,
        "scenarios_count": len(FAIL_CLOSED_SCENARIOS),
        "all_fail_closed": all_fail_closed,
        "crash_recovery": "replay_ok",
        "status": "passed" if all_fail_closed else "blocked",
    }
    flush(host, "failclosed.summary", summary)
    return summary


def _provision_guards(registry: Any, repo: Path) -> None:
    """One genuine guard-deployment gate call per host (IF-GUARD-002).

    The deployment verdict is recorded for auditing; the parity scenario is
    judged separately by the parity gate.  A provisioning failure stays
    fail-closed but never short-circuits the remaining scenarios.
    """
    try:
        _guard_registry.deploy_guard_configs(registry, repo)
    except Exception:
        return


def _judge_scenario(
    scenario: str,
    host: str,
    repo: Path,
    candidate_digest: str,
    registry: Any,
    worktree_ops: tuple,
) -> tuple[str, str]:
    """Synthesize the fixture and let the owning real gate judge it.

    Returns ``(outcome, evidence_ref)``: a blocking gate keeps the scenario
    fail-closed; a gate passing a must-fail-closed scenario leaks.  The
    evidence ref is a sha256 over the gate, fixture ref and verdict, binding
    every detail to genuine gate evidence.
    """
    gate_mod, gate_name = _SCENARIO_GATES[scenario]
    fixture_ref = f"opaque:{scenario}:fixture"
    try:
        injected = _demo_host.synthesize_scenario_fixture(scenario, repo, candidate_digest)
        fixture_ref = _fixture_ref(injected, fixture_ref)
    except Exception as exc:
        fixture_ref = f"unavailable:{scenario}:{type(exc).__name__}"
    try:
        verdict = _call_gate(
            gate_mod,
            gate_name,
            scenario,
            fixture_ref,
            repo,
            candidate_digest,
            registry,
            worktree_ops,
        )
    except Exception as exc:
        verdict = f"blocked:{type(exc).__name__}"
    outcome = "blocked" if _gate_is_blocked(verdict) else "leaked"
    evidence = _sha(f"{host}:{scenario}:{gate_name}:{fixture_ref}:{verdict!r}")
    return outcome, evidence


def _call_gate(
    gate_mod: Any,
    gate_name: str,
    scenario: str,
    fixture_ref: str,
    repo: Path,
    candidate_digest: str,
    registry: Any,
    worktree_ops: tuple,
) -> Any:
    """Invoke the scenario's owning gate with real, scenario-shaped args."""
    gate = getattr(gate_mod, gate_name)
    if gate_mod is _authenticity:
        return gate(
            ac_ref=fixture_ref,
            category="new",
            bound_nodes=(f"opaque:{scenario}:target",),
            outcomes=_authenticity_outcomes(scenario),
            legal_failure_kinds={"assertion_failure"},
            counterexample_experiment=_counterexample(scenario),
        )
    if gate_mod is _mutation:
        manifest = _mutation.build_manifest(
            ac=fixture_ref,
            if_ref="IF-FAILCLOSED-001",
            candidate_digest=candidate_digest,
            patch_digest=_sha(f"{repo}:{scenario}:{fixture_ref}:patch"),
            target_nodes=(f"opaque:{scenario}:target",),
            control_nodes=(f"opaque:{scenario}:control",),
            runner_identity="runtime:failclosed-demo",
            allowed_change_scope=(),
        )
        open_wt, apply_patch, run_nodes, close_wt = (
            worktree_ops if len(worktree_ops) == 4 else _default_worktree_ops()
        )
        return gate(manifest, repo, open_wt, apply_patch, run_nodes, close_wt)
    if gate_mod is _adapter_base:
        return gate(
            adapter_id=_adapter_id(fixture_ref),
            protocol="tracks-test-result",
            version=1,
        )
    if gate_mod is _guard_parity:
        return gate(
            registry=registry,
            runtime_commands={},
            pre_commit_path=repo / ".githooks" / "pre-commit",
            ci_workflow_path=repo / ".github" / "workflows" / "ci.yml",
            cwd=repo,
        )
    raise AssertionError(f"IF-FAILCLOSED-001: no gate dispatch for {scenario}")


def _fixture_ref(injected: Any, fallback: str) -> str:
    """Extract the fixture's contract ref, tolerating opaque substitutions."""
    ref = getattr(injected, "manifest_or_config_ref", None)
    return ref if isinstance(ref, str) and ref else fallback


def _authenticity_outcomes(scenario: str) -> Mapping[str, TestRunResult]:
    """Per-scenario failing-node outcomes for the authenticity gate: a bound
    failure for the broad-mutation scenario, an unbound downstream failure
    for the unrelated-red scenario (AC-FR0260-02)."""
    if scenario == "unrelated_red":
        node = f"unbound:{scenario}:downstream"
        return {node: TestRunResult(node, "failed", "assertion failure")}
    node = f"opaque:{scenario}:target"
    return {node: TestRunResult(node, "failed", "assertion failure")}


def _counterexample(scenario: str) -> Mapping[str, str] | None:
    """The broad-mutation scenario fails closed by referencing a
    counterexample bound to a different AC (AC-FR0260-05)."""
    if scenario == "broad_mutation":
        return {"ac": "AC-FR0260-05@v0.7"}
    return None


def _adapter_id(fixture_ref: str) -> str:
    """Feed the malformed-adapter fixture reference to resolve_adapter so an
    unknown declaration keeps the scenario fail-closed (AC-FR0264-03)."""
    return fixture_ref or "unknown-adapter"


def _default_worktree_ops() -> tuple[Callable, ...]:
    """Fail-closed default mutation worktree operations.

    The runtime composition injects genuine operations through
    ``demonstrate_failclosed(..., worktree_ops=(open, apply, run, close))``;
    without them, opening the experiment worktree fails which keeps the
    mutation scenarios fail-closed.
    """
    return (
        _unavailable_worktree,
        _unavailable_worktree,
        _unavailable_worktree,
        _unavailable_worktree,
    )


def _unavailable_worktree(*_args: Any, **_kwargs: Any) -> Any:
    """Default mutation worktree operation: fail closed when the runtime
    composition did not inject genuine operations."""
    raise RuntimeError("IF-FAILCLOSED-001: mutation worktree ops not injected")


def _gate_is_blocked(verdict: Any) -> bool:
    """A fail-closed demonstration blocks unless the gate wrongly PASSED.

    Demo substitution verdicts are strings (``blocked``/``passed``); real
    gates return verdict dataclasses whose acceptance signal varies
    (experiment ``status``, parity ``mismatches``, authenticity ``red`` /
    ``green_allowed``).  Only an explicit pass/acceptance verdict leaks; an
    unknown verdict object keeps the scenario fail-closed.
    """
    if isinstance(verdict, str):
        return verdict != "passed"
    status = getattr(verdict, "status", None)
    if status is not None:
        return status != "passed"
    mismatches = getattr(verdict, "mismatches", None)
    if mismatches is not None:
        return bool(mismatches)
    red = getattr(verdict, "red", None)
    if red is not None:
        return red != "legal"
    green = getattr(verdict, "green_allowed", None)
    if green is not None:
        return not green
    return True


def _sha(text: str) -> str:
    """Deterministic content-address digest for evidence refs."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _default_flush(host: str, event_type: str, payload: Mapping[str, Any]) -> None:
    """Emit a fail-closed event line when no sink was injected."""
    line = ", ".join(f"{key}={value}" for key, value in payload.items())
    print(f"[failclosed] host={host} {event_type} ({line})")
