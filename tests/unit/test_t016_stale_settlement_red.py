"""T-016 RED: IF-LEDGER-001/IF-FULLCHAIN-001 stale-identity settlement (r17).

Pins the settlement emitter contract for IF-LEDGER-001/IF-FULLCHAIN-001
(r17 plan_defect replan, PRISM-PLAN15/16-R01, #77): ISLAND_GATE_2
accumulated 200 stale OPEN ledger identities whose nodes are all green, and
per-identity DIAGNOSE dispatch cannot converge them.  Per architecture
FR-0265-01 (m_impl_runtime row) and interfaces IF-LEDGER-001/IF-FULLCHAIN-001,
the island_2 OPEN branch must first run a real FULL round, then settle every
non-PROVEN identity that re-verified green — ``(node, signature)`` not in this
round's failure set AND node in this round's executed set — by emitting
OPEN→CLASSIFIED→FIXED with an explicit settlement reason.  Only legal
transitions; never STALE; the clean criterion stays all-PROVEN (settlement
hands FIXED entries to the existing fallback proof ring); identities still
failing this round keep the per-item diagnosis loop and must not be swallowed.
Settlement is Runtime program logic (no agent free text), a pure function of
the ledger (replayed events) plus this round's FULL outcomes, idempotent and
replayable, and fail-closed on WAL inconsistency.

The RED pins the pure capability on ``tracks.executor.test_select`` (R02 gave
this task explicit test_select.py ownership; the ledger state machine already
lives there).  GREEN wires the real call into ``_do_check_island_2`` (same
file's atomic boundary).

red 判定（r18 re-pin, PRISM-016-R17-01）：attempt-1 的 6/11 用例崩溃于
本文件 ``_replayed`` helper 的装配缺陷（把 ledger 状态 dict 当作 WAL 事件
展开，且无 seq 的结算事件会排序到 ``ledger.opened`` 之前），而非实现缺陷；
PRISM 已确认 GREEN 结算实现正确且守卫清洁。本次 re-pin 修复 helper（fixture
WAL 作重放 base + store 式 seq 追加），断言合同不变：
- 对 pre-fix 基线（结算能力缺位）各断言失败于 IF-LEDGER-001 合同 token
  （``_settle`` 的能力缺位断言），即合法 RED；
- 对已交付实现（GREEN 于 3242986）全部断言通过，证明合同被真实满足且
  不依赖任何装配捷径——每个 fixture 都经公共 ``rebuild_ledger`` WAL 机构构建。
"""

from __future__ import annotations

import json

import pytest

from tracks.executor.test_select import (
    LedgerCorruptionError,
    ledger_is_clean,
    rebuild_ledger,
)

# IF-LEDGER-001@v0.7 TRACKS-TRACE IF-FULLCHAIN-001 stale-identity settlement

_SETTLEMENT_REASON = "stale_identity_settlement"
_PATH_TO = {
    "OPEN": (),
    "CLASSIFIED": ("CLASSIFIED",),
    "FIXED": ("CLASSIFIED", "FIXED"),
    "PROVEN": ("CLASSIFIED", "FIXED", "PROVEN"),
}


def _settle():
    """Resolve the IF-LEDGER-001 settlement emitter (capability contract).

    Fails on the contract token while the capability is absent (T-016 RED);
    after GREEN this returns the real pure function under test.
    """
    from tracks.executor import test_select

    settle = getattr(test_select, "settle_stale_identities", None)
    if settle is None:
        raise AssertionError(
            "IF-LEDGER-001 settlement emitter missing: "
            "tracks.executor.test_select.settle_stale_identities "
            "(stale-identity settlement capability absent, T-016 RED)"
        )
    return settle


def _identity_events(node: str, signature: str, target: str, seq: int) -> list[dict]:
    """WAL events opening ``(node, signature)`` and walking it to ``target``."""
    events = [
        {
            "seq": seq,
            "type": "ledger.opened",
            "payload": {
                "node": node,
                "failure_signature": signature,
                "state": "OPEN",
            },
        }
    ]
    before = "OPEN"
    for after in _PATH_TO[target]:
        seq += 1
        events.append(
            {
                "seq": seq,
                "type": "ledger.transitioned",
                "payload": {
                    "node": node,
                    "failure_signature": signature,
                    "from": before,
                    "to": after,
                },
            }
        )
        before = after
    return events


def _fixture_wal(*specs: tuple[str, str, str]) -> list[dict]:
    """The WAL events opening each (node, signature) and walking it to its
    target state — what the fixture's ledger was rebuilt from."""
    events: list[dict] = []
    seq = 0
    for node, signature, target in specs:
        seq += 1
        events.extend(_identity_events(node, signature, target, seq))
        seq += len(_PATH_TO[target])
    return events


def _ledger_fixture(*specs: tuple[str, str, str]) -> tuple[dict[str, str], list[dict]]:
    """(ledger, wal) built through the public rebuild_ledger WAL machinery.

    Settlement replay needs the fixture's WAL events, not the derived state
    dict: ``rebuild_ledger`` only accepts ledger WAL event mappings, so a
    state dict (or its identity-key strings) can never be replayed (PRISM-
    016-R17-01 re-pin).
    """
    events = _fixture_wal(*specs)
    return rebuild_ledger(events), events


def _build_ledger(*specs: tuple[str, str, str]) -> dict[str, str]:
    """Rebuild a ledger through the public WAL: (node, signature, state) specs."""
    return _ledger_fixture(*specs)[0]


def _state_of(ledger: dict[str, str], node: str, signature: str) -> str:
    """Read one identity's state, treating ledger keys as opaque WAL JSON."""
    for key, state in ledger.items():
        identity = json.loads(key)
        if tuple(identity) == (node, signature):
            return state
    raise AssertionError(f"identity absent from ledger: {(node, signature)!r}")


def _transitions_for(events: list[dict], node: str, signature: str) -> list[tuple[str, str]]:
    """The (from, to) transition pairs the settlement emitted for one identity."""
    pairs = []
    for event in events:
        payload = event["payload"]
        if (payload["node"], payload["failure_signature"]) == (node, signature):
            pairs.append((payload["from"], payload["to"]))
    return pairs


def _replayed(base_wal: list[dict], events: list[dict]) -> dict[str, str]:
    """Replay settlement events after the fixture's own WAL events.

    Settlement events carry no ``seq``: the store appends them with monotonic
    sequence numbers, and ``rebuild_ledger`` replays events in ``seq`` order —
    a seq-less settlement event would sort before the fixture's ``ledger.opened``
    and corrupt the replay (PRISM-016-R17-01 re-pin: seq is assigned at append
    time, exactly what the production store does).
    """
    out = list(base_wal)
    seq = max((event["seq"] for event in out), default=0)
    for event in events:
        seq += 1
        out.append({**event, "seq": seq})
    return rebuild_ledger(out)


def test_stale_open_identity_reverified_green_settles_open_classified_fixed():
    """IF-LEDGER-001: an OPEN identity whose node ran this round and whose
    (node, signature) is absent from this round's failure set is settled
    through the legal OPEN→CLASSIFIED→FIXED path with the explicit settlement
    reason — the stale identity must not require a DIAGNOSE dispatch."""
    ledger, wal = _ledger_fixture(("tests/unit/test_stale.py::test_old", "sig-stale-1", "OPEN"))
    events = _settle()(ledger, {"tests/unit/test_stale.py::test_old"}, [])
    transitions = _transitions_for(
        events, "tests/unit/test_stale.py::test_old", "sig-stale-1"
    )
    assert transitions == [("OPEN", "CLASSIFIED"), ("CLASSIFIED", "FIXED")], (
        "IF-LEDGER-001: settlement must emit the legal OPEN→CLASSIFIED→FIXED "
        f"path, got {transitions!r}"
    )
    for event in events:
        assert event["payload"]["reason"] == _SETTLEMENT_REASON, (
            "IF-LEDGER-001: every settlement transition carries the explicit "
            f"program settlement reason, got {event['payload'].get('reason')!r}"
        )
    assert _state_of(_replayed(wal, events), "tests/unit/test_stale.py::test_old", "sig-stale-1") == "FIXED"


def test_identity_failed_this_round_is_not_settled():
    """IF-FULLCHAIN-001: an identity reobserved in this round's failure set
    keeps the per-item diagnosis loop — settlement must not swallow it."""
    node = "tests/unit/test_stale.py::test_red"
    ledger, wal = _ledger_fixture((node, "sig-live", "OPEN"))
    events = _settle()(ledger, {node}, [(node, "sig-live")])
    assert events == [], (
        "IF-FULLCHAIN-001: an identity failing this round must not be settled"
    )
    assert _state_of(_replayed(wal, events), node, "sig-live") == "OPEN"


def test_node_not_executed_this_round_is_not_settled():
    """IF-LEDGER-001: settlement requires node∈本轮执行集 — a stale identity
    whose node did not run in this FULL round has no re-verification evidence
    and must stay OPEN (fail-closed, no evidence-free settle)."""
    node = "tests/unit/test_stale.py::test_unexecuted"
    ledger, wal = _ledger_fixture((node, "sig-stale-2", "OPEN"))
    events = _settle()(ledger, {"tests/unit/test_other.py::test_ran"}, [])
    assert events == [], (
        "IF-LEDGER-001: a node absent from this round's executed set must "
        "not be settled without execution evidence"
    )
    assert _state_of(_replayed(wal, events), node, "sig-stale-2") == "OPEN"


def test_settlement_never_emits_stale():
    """IF-LEDGER-001: 不发射 STALE — settlement drives re-verified identities
    to FIXED only; no STALE transition may appear even though the legacy
    state machine knows OPEN→STALE."""
    ledger, wal = _ledger_fixture(
        ("tests/unit/test_a.py::test_one", "sig-1", "OPEN"),
        ("tests/unit/test_b.py::test_two", "sig-2", "OPEN"),
    )
    executed = {"tests/unit/test_a.py::test_one", "tests/unit/test_b.py::test_two"}
    events = _settle()(ledger, executed, [])
    for event in events:
        assert event["payload"]["to"] != "STALE", (
            "IF-LEDGER-001: settlement must never emit a STALE transition"
        )
    replayed = _replayed(wal, events)
    assert "STALE" not in set(replayed.values()), (
        "IF-LEDGER-001: replayed ledger must contain no STALE identity"
    )


def test_settlement_is_per_signature_for_same_node():
    """IF-LEDGER-001: the candidate predicate keys on the (node, signature)
    identity — for one node with a live failure and a stale green signature,
    only the re-verified signature settles."""
    node = "tests/unit/test_stale.py::test_mixed"
    ledger = _build_ledger(
        (node, "sig-green", "OPEN"),
        (node, "sig-red", "OPEN"),
    )
    events = _settle()(ledger, {node}, [(node, "sig-red")])
    assert _transitions_for(events, node, "sig-green") == [
        ("OPEN", "CLASSIFIED"),
        ("CLASSIFIED", "FIXED"),
    ], "the re-verified signature must settle"
    assert _transitions_for(events, node, "sig-red") == [], (
        "the signature failing this round must keep its diagnosis loop"
    )


def test_classified_identity_reverified_green_settles_to_fixed():
    """IF-LEDGER-001: settlement candidates are every non-PROVEN identity —
    a CLASSIFIED stale identity re-verified green advances CLASSIFIED→FIXED
    through its legal remaining path."""
    node = "tests/unit/test_stale.py::test_classified"
    ledger, wal = _ledger_fixture((node, "sig-classified", "CLASSIFIED"))
    events = _settle()(ledger, {node}, [])
    assert _transitions_for(events, node, "sig-classified") == [
        ("CLASSIFIED", "FIXED")
    ], "a re-verified CLASSIFIED identity must settle CLASSIFIED→FIXED"
    assert _state_of(_replayed(wal, events), node, "sig-classified") == "FIXED"


def test_proven_identities_are_never_touched():
    """IF-LEDGER-001: PROVEN identities are outside the settlement scope —
    no event may target them."""
    node = "tests/unit/test_stale.py::test_proven"
    ledger = _build_ledger((node, "sig-proven", "PROVEN"))
    events = _settle()(ledger, {node}, [])
    assert events == [], "settlement must never emit events for PROVEN identities"


def test_settlement_is_pure_and_idempotent():
    """IF-LEDGER-001: settlement is Runtime program logic — a pure function
    of the ledger plus this round's outcomes: it must not mutate its input
    and must return identical decisions for identical inputs (replayable)."""
    node = "tests/unit/test_stale.py::test_replay"
    ledger = _build_ledger((node, "sig-idem", "OPEN"))
    snapshot = dict(ledger)
    settle = _settle()
    first = settle(ledger, {node}, [])
    assert ledger == snapshot, "settlement must not mutate its input ledger"
    second = settle(ledger, {node}, [])
    assert first == second, (
        "IF-LEDGER-001: settlement must be idempotent — identical inputs "
        "produce identical transition decisions"
    )


def test_wal_inconsistency_fails_closed():
    """IF-LEDGER-001: WAL 不一致 fail-closed — a ledger carrying an unknown
    state or an unparsable identity key must raise LedgerCorruptionError
    instead of guessing which identities to settle."""
    node = "tests/unit/test_stale.py::test_corrupt"
    ledger = _build_ledger((node, "sig-corrupt", "OPEN"))
    corrupted = dict(ledger)
    corrupted[next(iter(corrupted))] = "DESTROYED"
    with pytest.raises(LedgerCorruptionError):
        _settle()(corrupted, {node}, [])
    with pytest.raises(LedgerCorruptionError):
        _settle()({"not-a-ledger-key": "OPEN"}, {node}, [])


def test_no_settlement_candidates_yields_no_events():
    """IF-LEDGER-001: a ledger with nothing to settle emits nothing — the
    OPEN branch must stay silent rather than fabricate transitions."""
    node = "tests/unit/test_stale.py::test_clean"
    ledger = _build_ledger((node, "sig-proven-2", "PROVEN"))
    events = _settle()(ledger, {node}, [])
    assert events == []


def test_settled_ledger_stays_unclean_until_proven_ring():
    """IF-FULLCHAIN-001: settlement hands FIXED entries to the existing
    fallback proof ring — after replaying settlement events the ledger is
    all-FIXED and therefore NOT clean (clean 判据不变：全 PROVEN)."""
    ledger, wal = _ledger_fixture(
        ("tests/unit/test_a.py::test_one", "sig-3", "OPEN"),
        ("tests/unit/test_b.py::test_two", "sig-4", "OPEN"),
    )
    executed = {"tests/unit/test_a.py::test_one", "tests/unit/test_b.py::test_two"}
    events = _settle()(ledger, executed, [])
    replayed = _replayed(wal, events)
    assert set(replayed.values()) == {"FIXED"}, (
        "settlement must stop at FIXED and leave PROVEN to the proof ring"
    )
    assert ledger_is_clean(replayed) is False, (
        "IF-FULLCHAIN-001: settlement alone must not flip the ledger clean — "
        "the clean criterion stays all-PROVEN"
    )
