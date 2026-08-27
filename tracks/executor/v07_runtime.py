"""Unique v0.7 extension assembly wired onto the capability seam.

Architecture §1.0.9 / interfaces §1i / §1h: importing this module registers
the v0.7 extension exactly once on ``tracks.executor.version_extensions``.

- ``trace`` delegates to the same candidate-bound pure join in
  ``tracks.checks.trace`` that backs ISLAND_GATE_2 -- CLI and gate share one
  checker; re-computing or self-reporting a pass is forbidden.
- ``island_gate_2`` forwards verbatim to
  ``tracks.executor.failclosed.demonstrate_failclosed`` (delivered by its own
  task). The import happens at call time only: assembling this composition
  root or resolving the callback must never eagerly load the fail-closed
  module.
- ``render_closure`` turns a stored gate payload (keys ``gate`` /
  ``closure`` / ``records``, mirroring ClosureReport) into operator-auditable
  report sections without recomputing anything.

No other version selects these callbacks: earlier versions resolve nothing on
this seam and keep their classic behaviour (FR-0264-02 schema isolation).
"""

from __future__ import annotations

from typing import Any

from tracks.executor import version_extensions

EXTENSION_VERSION = "v0.7"


def _trace(*args: Any) -> Any:
    """Forward to the shared candidate-bound join (interfaces §1i contract)."""
    from tracks.checks.trace import check_closure_candidate

    return check_closure_candidate(*args)


def _island_gate_2(arguments: tuple, **kwargs: Any) -> Any:
    """ISLAND_GATE_2 exit-gate callback; lazy dispatch is part of the contract.

    ``arguments`` is the gate's argument sequence: dispatch star-unpacks it
    onto ``tracks.executor.failclosed.demonstrate_failclosed`` together with
    the keywords, forwarding the target's outcome unchanged.
    """
    from tracks.executor.failclosed import demonstrate_failclosed

    return demonstrate_failclosed(*arguments, **kwargs)


def _render_closure(payload: dict[str, Any]) -> list[str]:
    """Render one stored gate payload as markdown closure-section lines.

    Every AC keeps its human-auditable chain elements (bound nodes, baseline,
    mutation, FULL evidence, candidate digest, status); blocking hard errors
    stay visible so an operator can audit why a gate did not pass.
    """
    lines = [
        "",
        "# Candidate-bound closure",
        "",
        f"- gate: `{payload.get('gate', '-')}`",
        f"- closure: `{payload.get('closure', '-')}`",
        f"- status: `{payload.get('status', '-')}`",
        "",
    ]
    for record in payload.get("records") or ():
        nodes = ", ".join(record.get("nodes") or ()) or "-"
        lines.append(
            f"- `{record.get('ac')}` outlet=`{record.get('outlet', '-')}` "
            f"baseline=`{record.get('baseline_evidence')}` "
            f"mutation=`{record.get('mutation_evidence')}` "
            f"full_pass=`{record.get('full_pass_evidence')}` "
            f"candidate=`{record.get('candidate_digest')}` "
            f"nodes=[{nodes}] status=`{record.get('status')}`"
        )
    hard_errors = payload.get("hard_errors") or ()
    if hard_errors:
        lines.append(f"- hard errors: {', '.join(hard_errors)}")
    lines.append("")
    return lines


class V07Extension:
    """Capability namespace registered for the v0.7 project version."""

    trace = staticmethod(_trace)
    island_gate_2 = staticmethod(_island_gate_2)
    render_closure = staticmethod(_render_closure)


version_extensions.register_extension(EXTENSION_VERSION, V07Extension())
