"""IF-CLOSURE-001 RED: unique v0.7 composition root (``executor/v07_runtime``).

Architecture §1.0.9 / §1i: after the generic seam exists, a v0.7 extension is
assembled once -- registering the ``trace`` checker capability that delegates
to the same ``checks.trace`` candidate-bound pure join (CLI and ISLAND_GATE_2
MUST share it; re-computing or self-reporting pass is forbidden) and the
``island_gate_2`` callback that lazily dispatches to
``tracks.executor.failclosed.demonstrate_failclosed`` (delivered by T-016;
this task only owns the *calling* contract, so laziness is part of it: merely
importing the composition root must not require the fail-closed module).

RED note (T-013): ``tracks.executor.v07_runtime`` does not exist until GREEN,
so imports happen inside test bodies and their absence is normalized into an
``AssertionError`` carrying the contract token (IF-CLOSURE-001 / arch §1.0.9)
-- uniform legal assertion_failure reds, never collection/import errors.
"""

from __future__ import annotations

import importlib
import sys
import types

DIG = "sha256:" + "a" * 64


def _import_contract_module(name: str):
    """Import *name* lazily; absence fails on the contract token."""
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        raise AssertionError(
            f"IF-CLOSURE-001/architecture §1.0.9 composition contract not "
            f"delivered: {name} ({exc})"
        ) from exc


def _seam():
    return _import_contract_module("tracks.executor.version_extensions")


def _runtime():
    return _import_contract_module("tracks.executor.v07_runtime")


def _seam():
    return _import_contract_module("tracks.executor.version_extensions")


def _runtime():
    return _import_contract_module("tracks.executor.v07_runtime")


_LAZY_TOKEN = (
    "IF-CLOSURE-001/architecture §1.0.9 lazy island_gate_2 dispatch: "
    "assembling/resolving the v0.7 extension must not eagerly import "
    "tracks.executor.failclosed (the module is delivered by T-016; only the "
    "call-time dispatch contract belongs to this task)"
)


# Composition wiring: importing the v0.7 composition root registers its
# capabilities on the seam; v0.6-and-earlier versions select none.
def _resolved_v07(capability):
    _runtime()
    return _seam().resolve_capability("v0.7", capability)


def test_v07_runtime_import_does_not_require_failclosed_module(monkeypatch):
    """island_gate_2 dispatch must be call-time lazy (T-016 delivers failclosed).

    The laziness contract is about *eager import*, not file existence: whether
    or not ``tracks.executor.failclosed`` has been delivered yet, importing
    the composition root (and resolving the callback on the seam) must never
    load it into ``sys.modules`` -- only calling the island_gate_2 callback
    may do so. The member probes stay valid after T-016 lands.
    """
    name = "tracks.executor.failclosed"
    monkeypatch.delitem(sys.modules, name, raising=False)
    _runtime()
    assert name not in sys.modules, f"{_LAZY_TOKEN}: imported at composition time"
    callback = _resolved_v07("island_gate_2")
    assert callable(callback)
    assert name not in sys.modules, (
        f"{_LAZY_TOKEN}: imported at capability resolution time"
    )


def test_v07_trace_checker_shares_pure_candidate_join():
    """The resolved trace checker returns exactly what checks.trace computes."""
    checks_trace = importlib.import_module("tracks.checks.trace")
    evidence = {
        "outlet": "IF-CLOSURE-001",
        "nodes": ("opaque-node",),
        "node_statuses": ("passed",),
        "control_statuses": ("pass",),
        "baseline_evidence": "evidence-1",
        "baseline_candidate": DIG,
        "mutation_evidence": "manifest-1",
        "mutation_candidate": DIG,
        "full_pass_evidence": "evidence-full",
    }
    args = (["AC-FR0265-01"], DIG, {"AC-FR0265-01": evidence})
    resolved = _resolved_v07("trace")
    assert callable(resolved)
    assert resolved(*args) == checks_trace.check_closure_candidate(*args)


def test_v07_island_gate_2_forwards_to_failclosed_demonstrate(monkeypatch):
    """Calling contract: forward arguments verbatim and pass the result through.

    The downstream function belongs to T-016; stubbing only that dependency in
    ``sys.modules`` keeps this test's SUT strictly ``v07_runtime`` dispatch.
    """
    calls: list[tuple[tuple, dict]] = []
    stub = types.SimpleNamespace(
        demonstrate_failclosed=lambda *a, **k: calls.append((a, k)) or "demo-result"
    )
    monkeypatch.setitem(sys.modules, "tracks.executor.failclosed", stub)
    callback = _resolved_v07("island_gate_2")
    assert callable(callback)
    outcome = callback(("repo-a",), venv="venv-b")
    assert calls == [(("repo-a",), {"venv": "venv-b"})]
    assert outcome == "demo-result"
