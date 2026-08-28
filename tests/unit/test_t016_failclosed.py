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
from pathlib import Path

from tests.unit.helpers import git_repo
from tracks import paths
from tracks.executor.demo_host import FAIL_CLOSED_SCENARIOS
from tracks.executor.executor import Executor
from tracks.kernel import decide
from tracks.project import load_contract
from tracks.store import Store

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


# --- ISLAND_GATE_2 capability call points (plan_defect replan #77) ----------
# T-016 now atomically owns the public-entry wiring: the v0.7 extension
# registers island_gate_2 (T-013, zero call points is the defect root), so
# the executor composition-root assembly must expose the ISLAND_GATE_2
# dispatch call point and m_impl_runtime must resolve the capability at the
# gate -- both currently missing.  These pins carry the IF contract token.

_EXECUTOR_ENTRY_TOKEN = (
    "IF-FAILCLOSED-001: executor assembly island_gate_2_dispatch call point "
    "missing (v0.7 registration exists, zero call points)"
)


def _executor_module():
    """Normalize a missing executor import to a contract-token assertion."""
    try:
        return importlib.import_module("tracks.executor.executor")
    except ModuleNotFoundError as exc:
        raise AssertionError(
            f"IF-FAILCLOSED-001: tracks.executor.executor not importable: {exc}"
        ) from exc


def test_executor_assembly_exposes_island_gate_2_dispatch():
    """architecture 1.0.9 / interfaces 1b kind 4: the executor composition
    root must expose an island_gate_2 capability CALL POINT -- the v0.7
    registration already exists, the dispatch entry is the missing half."""
    module = _executor_module()
    dispatch = getattr(module, "island_gate_2_dispatch", None)
    assert callable(dispatch), _EXECUTOR_ENTRY_TOKEN


def test_island_gate_2_dispatch_forwards_to_demonstrate_failclosed(monkeypatch):
    """The assembly dispatch, for the registered v0.7 extension, must resolve
    island_gate_2 and lazily forward its arguments onto demonstrate_failclosed
    (the failclosed module simulated in sys.modules -- call-time lazy dispatch
    exercised against the REAL v07_runtime callback)."""
    module = _executor_module()
    dispatch = getattr(module, "island_gate_2_dispatch", None)
    assert callable(dispatch), _EXECUTOR_ENTRY_TOKEN

    import types

    captured: dict[str, list] = {}

    def fake_demonstrate(*args, **kwargs):
        captured.setdefault("calls", []).append((args, kwargs))
        return "demo-outcome"

    fake = types.ModuleType("tracks.executor.failclosed")
    fake.demonstrate_failclosed = fake_demonstrate
    monkeypatch.setitem(sys.modules, "tracks.executor.failclosed", fake)

    outcome = dispatch("v0.7", ("tracks", "demo-pytest"), replay=True)
    assert outcome == "demo-outcome"
    calls = captured.get("calls") or []
    assert calls and calls[0] == (("tracks", "demo-pytest"), {"replay": True}), (
        f"IF-FAILCLOSED-001: island_gate_2 dispatch did not forward arguments: {calls}"
    )


def test_island_gate_2_dispatch_early_version_is_classic_none():
    """FR-0264-02 capability isolation: a version with no v0.7 extension
    selects no island_gate_2 callback, so the dispatch returns None and early
    versions keep their classic behaviour."""
    module = _executor_module()
    dispatch = getattr(module, "island_gate_2_dispatch", None)
    assert callable(dispatch), _EXECUTOR_ENTRY_TOKEN
    assert dispatch("v0.5", ("tracks", "demo-pytest")) is None


def test_m_impl_runtime_resolves_island_gate_2_for_gate_dispatch():
    """IF-FAILCLOSED-001 / architecture 1.0.9: m_impl_runtime's ISLAND_GATE_2
    handler must resolve the run version's island_gate_2 capability (mirroring
    machine._resolve_before_mtest) so the gate actually dispatches
    demonstrate_failclosed -- the zero-call-point defect means the resolver
    does not exist yet."""
    runtime = importlib.import_module("tracks.executor.m_impl_runtime")
    resolver = getattr(runtime, "_resolve_island_gate_2", None)
    assert callable(resolver), (
        "IF-FAILCLOSED-001: m_impl_runtime._resolve_island_gate_2 missing -- "
        "ISLAND_GATE_2 never dispatches demonstrate_failclosed"
    )


# --- Phase 0 BLOCKED resume preflight (plan_defect replan x2, r10) ----------
# SM-01.3 (interfaces section 1c / AC-FR0257-05) requires a repair -> revalidate
# cycle: after phase0.blocked parks the run, the KERNEL parks by design
# (decide_phase0 returns [] on BLOCKED -- no auto re-issue, section 1c). The
# r10 defect is that the tree had NO channel to issue a NEW phase0_validate
# once Human repairs the repo facts, so the resume half-loop never fires. The
# executor (effects layer, the only legal IO edge) must land a BLOCKED resume
# preflight at the run-loop entry: when a drive starts with phase0_status ==
# "BLOCKED" it appends command.issued and executes one fresh phase0_validate
# (at most once per drive, no tick-level hot loop, full hard checks -- a
# still-broken host must keep parking, never a watered-down SEALED pass).
# These pins carry the IF/§1c contract token and fail today (zero resume).


def _phase0_contract() -> str:
    """A loadable project contract for the phase0 resume host fixture."""
    return (
        "[unit]\nframework='pytest'\npaths=['tests/unit/']\n"
        "collect='.venv/bin/python -m pytest --collect-only -q tests/unit/'\n"
        "run='.venv/bin/python -m pytest tests/unit/ --tb=short -q -n 4 "
        "--dist loadscope --junitxml={result}'\n"
        "run_selected='.venv/bin/python -m pytest {nodes} --tb=short -q -n 4 "
        "--dist loadscope --junitxml={result}'\ncwd='.'\n\n"
        "[integration]\nframework='pytest'\npaths=['tests/integration/']\n"
        "collect='.venv/bin/python -m pytest --collect-only -q tests/integration/'\n"
        "run='.venv/bin/python -m pytest tests/integration/ --tb=short -q -n 8 "
        "--dist loadscope --junitxml={result}'\n"
        "run_selected='.venv/bin/python -m pytest {nodes} --tb=short -q -n 8 "
        "--dist loadscope --junitxml={result}'\ncwd='.'\n\n"
        "[e2e]\nframework='pytest'\npaths=['tests/e2e/']\n"
        "collect='.venv/bin/python -m pytest --collect-only -q tests/e2e/'\n"
        "run='.venv/bin/python -m pytest tests/e2e/ --tb=short -q -n 8 "
        "--dist loadscope --junitxml={result}'\n"
        "run_selected='.venv/bin/python -m pytest {nodes} --tb=short -q -n 8 "
        "--dist loadscope --junitxml={result}'\ncwd='.'\n\n"
        "[nightly]\nschedule='0 3 * * *'\nworkflow='.github/workflows/nightly.yml'\n"
        "job='nightly'\nlayers=['unit','integration','e2e']\npurpose='r'\n"
    )


def _phase0_host(tmp_path) -> Path:
    """Host repo with a loadable contract and a v0.7 run parked at
    phase0.blocked (BLOCKED) -- no §4.2 registry seeded, so any resumed
    re-validation must keep parking fail-closed (never fabricate SEALED)."""
    repo = git_repo(tmp_path, gitignore=True)
    contract = paths.project_toml_path(paths.tracks_home(repo))
    contract.parent.mkdir(parents=True, exist_ok=True)
    contract.write_text(_phase0_contract(), encoding="utf-8")
    return repo


def _phase0_executor(repo: Path) -> Executor:
    """Executor on a store seeded with a v0.7 M-TEST run whose Phase 0 parked
    at BLOCKED (phase0.blocked appended, SM-01.5)."""
    load_contract(repo)
    store = Store(paths.tracks_home(repo))
    store.append("RUN", "v0.7", "story.requested", {"raw_chars": 1})
    store.append("RUN", "v0.7", "stage.entered", {"stage": "M-TEST"})
    store.append(
        "RUN",
        "v0.7",
        "phase0.blocked",
        {"reason": "guard_registry_invalid", "detail": "no registry", "recoverable": True},
    )
    return Executor(store, repo, "RUN")


def _issued_kinds(store: Store) -> list[str]:
    """kinds of every command.issued event appended so far.

    The ``command.issued`` payload nests the command descriptor under
    ``command`` (``{"command": {"kind": ...}}``; executor.issue emits it, the
    kernel ``_on_command_issued`` and existing tests read the same nested
    shape). Re-pinned after red_defect (R-tree 3e8556e): the previous helper
    read the top-level ``payload["kind"]``, which is never set, so even a
    correct GREEN implementation could not turn the phase0 tests green.
    """
    return [
        (ev.payload.get("command") or {}).get("kind")
        for ev in store.events("RUN")
        if ev.type == "command.issued"
    ]


def test_run_loop_resumes_phase0_validate_after_blocked(tmp_path):
    """SM-01.3 resume half-loop (r10): when a drive starts with
    phase0_status == BLOCKED, the executor run-loop entry must append a NEW
    phase0_validate command -- the kernel parks (decide_phase0 -> []), so the
    resume channel lives in the executor effects layer, not the kernel. The
    resumed command is a fresh validation, never a reconcile replay."""
    repo = _phase0_host(tmp_path)
    executor = _phase0_executor(repo)
    assert executor.store.state("RUN").phase0_status == "BLOCKED"
    # Kernel pure boundary unchanged: decide still parks at BLOCKED.
    assert decide(executor.store.state("RUN")) is None
    executor.run_loop()
    kinds = _issued_kinds(executor.store)
    assert "phase0_validate" in kinds, (
        "IF-FAILCLOSED-001/§1c SM-01.3: BLOCKED resume half-loop missing -- "
        f"Human repair re-drive issued no new phase0_validate: {kinds}"
    )


def test_run_loop_phase0_resume_is_bounded_single_issue(tmp_path):
    """SM-01.3: the BLOCKED resume issues AT MOST ONE fresh phase0_validate
    per drive invocation -- no tick-level hot loop from the parked state."""
    repo = _phase0_host(tmp_path)
    executor = _phase0_executor(repo)
    before = _issued_kinds(executor.store)
    executor.run_loop()
    after = _issued_kinds(executor.store)
    new_kinds = after[len(before):]
    assert new_kinds.count("phase0_validate") == 1, (
        "IF-FAILCLOSED-001/§1c SM-01.3: BLOCKED resume must issue exactly one "
        f"phase0_validate per drive, got {new_kinds}"
    )


def test_run_loop_phase0_resume_keeps_fail_closed(tmp_path):
    """SM-01.3: the resumed phase0_validate re-runs the FULL hard checks -- a
    host whose §4.2 registry is still broken must keep parking at BLOCKED (no
    SEALED, no watered-down VALIDATING limp-along) on the resumed validation."""
    repo = _phase0_host(tmp_path)  # no §4.2 registry seeded
    executor = _phase0_executor(repo)
    executor.run_loop()
    events = list(executor.store.events("RUN"))
    sealed = [ev for ev in events if ev.type == "phase0.sealed"]
    assert not sealed, (
        "IF-FAILCLOSED-001/§1c SM-01.3: resumed validation must not fabricate "
        "a SEALED pass when the host registry is still broken"
    )
    assert "phase0_validate" in _issued_kinds(executor.store), (
        "IF-FAILCLOSED-001/§1c SM-01.3: BLOCKED resume issued no phase0_validate"
    )
    # The resumed validation parked the run back at BLOCKED: fail-closed, not
    # a silent VALIDATING limp-along.
    assert executor.store.state("RUN").phase0_status == "BLOCKED", (
        "IF-FAILCLOSED-001/§1c SM-01.3: resumed validation must re-park at "
        f"BLOCKED, got {executor.store.state('RUN').phase0_status}"
    )
