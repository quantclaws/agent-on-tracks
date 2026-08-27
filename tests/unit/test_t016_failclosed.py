"""T-016 RED: IF-FAILCLOSED-001 dual-host nine-scenario demonstration.

The module ``tracks.executor.failclosed`` (with ``demonstrate_failclosed``)
is the T-016 GREEN obligation. These tests pin the public contract from
interfaces §1a (rows 4/11/12) and IF-FAILCLOSED-001 plus the T-013 coupling
(``v07_runtime.island_gate_2`` star-unpacks its ``arguments`` tuple onto
``demonstrate_failclosed(*arguments, **kwargs)``):

- the nine scenario names form a closed set identical to
  ``demo_host.FAIL_CLOSED_SCENARIOS``;
- ``demonstrate_failclosed(hosts=[...])`` synthesizes exactly one
  ``failclosed.demonstrated`` per scenario per host and one
  ``failclosed.summary`` per host (9 * host_count details + host_count
  summaries), each carrying the §1a payload fields;
- EVERY demonstrated event must be traceable to the module invoking one of
  the REAL gate entry points (authenticity judgement, guard parity,
  adapter selection, mutation experiment, demo-host synthesis) whose
  verdict drives the outcome -- no canned generator may fabricate
  evidence;
- a gate leaking (reporting a pass where the scenario must fail closed)
  yields ``outcome="leaked"`` and forces ``all_fail_closed=False`` and a
  non-pass summary; a gate whose verdict cannot be produced (blocked)
  keeps fail-closed;
- the clean run reports ``crash_recovery="replay_ok"`` per host.

GREEN implements this by importing and invoking the real gate modules at
the canonical paths (the ones patched below) and emitting the events through
an injectable sink; a faithful implementation turns all these green.
"""

from __future__ import annotations

import importlib
import sys

from tracks.executor.demo_host import FAIL_CLOSED_SCENARIOS

_LEGAL_SCENARIOS = (
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
_CANONICAL_HOSTS = ("tracks", "demo-pytest")


def _import_failclosed():
    """The module is the T-016 deliverable; a missing module is normalized to
    a contract-token AssertionError so every RED is a clean assertion."""
    try:
        return importlib.import_module("tracks.executor.failclosed")
    except ModuleNotFoundError as exc:
        raise AssertionError(
            "IF-FAILCLOSED-001/§1a: tracks.executor.failclosed is not "
            f"delivered: {exc}"
        ) from exc


class _Sink:
    """Recording event sink: ``demonstrate_failclosed(hosts, flush=sink)``
    delivers ``(host, type, payload)`` tuples for details + summaries."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    def __call__(self, host: str, event_type: str, payload: dict) -> None:
        self.events.append((host, event_type, dict(payload)))

    def details(self, host: str) -> list[dict]:
        return [
            e[2] for e in self.events if e[1] == "failclosed.demonstrated" and e[0] == host
        ]

    def summaries(self) -> list[dict]:
        return [e[2] for e in self.events if e[1] == "failclosed.summary"]


def _gate_stubs(monkeypatch, *, leak_gates):
    """Stub the REAL gate modules the failclosed orchestrator must invoke, and
    record every call. Gates named in *leak_gates* wrongly return a pass for
    any scenario they judge (the module must translate that pass into a
    leaked demonstrated outcome); every other gate returns a fail-closed
    verdict. A canned generator that never calls these gates is therefore
    detectable: the call log stays empty."""
    calls: list[tuple[str, tuple]] = []

    def _record(name):
        def _stub(*args, **kwargs):
            calls.append((name, args))
            return "passed" if name in leak_gates else "blocked"

        return _stub

    monkeypatch.setattr(
        "tracks.executor.authenticity.judge_authenticity", _record("authenticity")
    )
    monkeypatch.setattr(
        "tracks.executor.guard_parity.check_parity", _record("parity")
    )
    monkeypatch.setattr(
        "tracks.adapters.base.resolve_adapter", _record("adapter")
    )
    monkeypatch.setattr(
        "tracks.executor.mutation.run_mutation_experiment", _record("mutation")
    )
    monkeypatch.setattr(
        "tracks.executor.guard_registry.deploy_guard_configs",
        _record("deploy"),
    )
    monkeypatch.setattr(
        "tracks.executor.demo_host.synthesize_scenario_fixture",
        _record("fixture"),
    )
    return calls


# --- closed set ------------------------------------------------------------

def test_failclosed_scenario_closed_set_matches_contract():
    """§1a: the nine FailClosedScenario literals are exactly
    demo_host.FAIL_CLOSED_SCENARIOS -- no drift between the two surfaces."""
    assert tuple(FAIL_CLOSED_SCENARIOS) == _LEGAL_SCENARIOS, FAIL_CLOSED_SCENARIOS
    assert len(FAIL_CLOSED_SCENARIOS) == 9


# --- module surface --------------------------------------------------------

def test_module_exposes_demonstrate_failclosed_callable():
    """§1a row 4: the module must expose demonstrate_failclosed."""
    module = _import_failclosed()
    assert callable(getattr(module, "demonstrate_failclosed", None)), (
        "IF-FAILCLOSED-001: failclosed module must expose demonstrate_failclosed"
    )


# --- island_gate_2 coupling (caller side, delivered) -----------------------

def test_island_gate_2_star_unpacks_onto_demonstrate_failclosed(monkeypatch):
    """T-013 contract: v07_runtime.island_gate_2(arguments, **kwargs) must
    forward ``demonstrate_failclosed(*arguments, **kwargs)``. The failclosed
    module is simulated in sys.modules so the (delivered) T-013 callback is
    exercised against the real coupling shape."""
    import types

    captured = {}

    def fake_demonstrate(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return "outcome"

    fake = types.ModuleType("tracks.executor.failclosed")
    fake.demonstrate_failclosed = fake_demonstrate
    monkeypatch.setitem(sys.modules, "tracks.executor.failclosed", fake)

    from tracks.executor.v07_runtime import V07Extension

    result = V07Extension.island_gate_2(("tracks", "demo-pytest"), replay=True)
    assert result == "outcome"
    assert captured["args"] == ("tracks", "demo-pytest")
    assert captured["kwargs"] == {"replay": True}


# --- scenario detail + summary recording -----------------------------------

def test_demonstrate_emits_nine_details_and_summary_per_host_with_evidence(
    monkeypatch,
):
    """§1a rows 11/12 + IF-FAILCLOSED-001: canonical two hosts -> exactly 18
    demonstrated + 2 summary events; every detail carries evidence_ref and the
    summary carries the contract fields with crash_recovery=replay_ok on the
    clean run. GREEN cannot satisfy this without invoking the real gates:
    every detail must be attributable to a gate call logged by _gate_stubs."""
    module = _import_failclosed()
    sink = _Sink()
    gate_calls = _gate_stubs(monkeypatch, leak_gates=())
    module.demonstrate_failclosed(list(_CANONICAL_HOSTS), flush=sink)
    details = [
        e for e in sink.events if e[1] == "failclosed.demonstrated"
    ]
    summaries = sink.summaries()
    assert len(details) == 9 * len(_CANONICAL_HOSTS), sink.events
    assert len(summaries) == len(_CANONICAL_HOSTS), sink.events
    assert gate_calls, (
        "no real gate was invoked: a canned generator must not satisfy "
        "IF-FAILCLOSED-001"
    )
    per_host = {host: set() for host in _CANONICAL_HOSTS}
    for host, _etype, payload in details:
        assert payload.get("scenario") in FAIL_CLOSED_SCENARIOS, payload
        assert payload.get("outcome") == "blocked", payload
        assert isinstance(payload.get("evidence_ref"), str) and payload["evidence_ref"], (
            f"evidence_ref must be bound to a real gate verdict: {payload}"
        )
        per_host[host].add(payload["scenario"])
    for host in _CANONICAL_HOSTS:
        assert per_host[host] == set(FAIL_CLOSED_SCENARIOS), per_host
    for payload in summaries:
        assert payload.get("scenarios_count") == 9, payload
        assert payload.get("all_fail_closed") is True, payload
        assert payload.get("crash_recovery") == "replay_ok", payload
        assert payload.get("status") == "passed", payload


# --- a gate leaking forces the summary to block ----------------------------

def test_a_gate_leak_blocks_the_summary(monkeypatch):
    """§1a/IF-FAILCLOSED-001: if a gate wrongly passes a scenario that must
    fail closed, the demonstrated outcome is leaked and the summary blocks --
    the leak must be attributable to the leaking scenario's gate. The
    authenticity gate is the one that judges broad_mutation; forcing it to
    pass must leave that scenario leaked."""
    module = _import_failclosed()
    sink = _Sink()
    _gate_stubs(monkeypatch, leak_gates=("authenticity",))
    module.demonstrate_failclosed(["tracks"], flush=sink)
    summaries = sink.summaries()
    assert summaries, sink.events
    summary = summaries[0]
    assert summary.get("all_fail_closed") is False, summary
    assert summary.get("status") != "passed", summary
    details = sink.details("tracks")
    leaked = [d for d in details if d.get("outcome") == "leaked"]
    assert leaked and any(d.get("scenario") == "broad_mutation" for d in leaked), (
        "leaked outcome must be attributable to broad_mutation"
    )


# --- crash recovery label per host -----------------------------------------

def test_crash_recovery_replay_ok_per_host():
    """§1a row 12: on a clean replay the summary for EVERY host reports
    crash_recovery=replay_ok and passes -- no phantom failed-with-pass."""
    module = _import_failclosed()
    sink = _Sink()
    module.demonstrate_failclosed(list(_CANONICAL_HOSTS), flush=sink)
    summaries = sink.summaries()
    assert len(summaries) == len(_CANONICAL_HOSTS), sink.events
    for payload in summaries:
        assert payload.get("crash_recovery") == "replay_ok", payload
        assert payload.get("status") == "passed", payload
