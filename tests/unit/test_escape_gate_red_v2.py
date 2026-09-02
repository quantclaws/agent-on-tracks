"""T-004 RED v2: deeper escape contract slices not yet covered.

Continues FR-0287 / IF-ESCAPE-001/002. Previous minimal GREEN satisfied the
6 coarse checks in test_escape_gate_red.py; these 3 tests pin finer contract
tokens that the minimal stub still misses and must fail with
assertion_failure (not stub_token) on the current baseline.

- AC-FR0287-02 barrier monotonic cutover
- AC-FR0287-02 quarantine respects seq boundary (post-barrier not quarantined)
- AC-FR0287-03 stale returns per-evidence-type entries (multiple, all human_return)
"""

from __future__ import annotations

from tracks.executor.escape import (
    establish_escape_barrier,
    quarantine_late_outcome,
    stale_downstream_evidence,
)


# AC-FR0287-02@v0.8 TRACKS-TRACE IF-ESCAPE-001 barrier monotonic
def test_barrier_cutover_seq_monotonic_increases():
    """AC-FR0287-02: successive barriers for the same run must strictly
    increase the cutover sequence so late outcomes are unambiguously
    identified. Minimal hash-based stub returns same seq and must fail."""
    b1 = establish_escape_barrier("run-monotonic")
    b2 = establish_escape_barrier("run-monotonic")
    assert isinstance(b1, dict) and isinstance(b2, dict)
    s1 = b1.get("cutover_seq")
    s2 = b2.get("cutover_seq")
    assert isinstance(s1, int) and isinstance(s2, int)
    assert s2 > s1, (
        f"assertion failure: second barrier cutover_seq must be > first "
        f"(got {s1} then {s2})"
    )


# AC-FR0287-02@v0.8 TRACKS-TRACE IF-ESCAPE-001 quarantine boundary
def test_quarantine_allows_post_barrier_outcome():
    """AC-FR0287-02: only outcomes dispatched *before* the barrier are
    quarantined. An outcome whose seq is at/after the cutover must be
    allowed (status != quarantined). Minimal stub quarantines everything."""
    barrier = establish_escape_barrier("run-boundary")
    seq = barrier.get("cutover_seq")
    assert isinstance(seq, int)
    # before -> quarantined (should pass)
    before = {"dispatch_id": "d-before", "seq": seq - 1}
    r_before = quarantine_late_outcome(barrier, before)
    assert r_before.get("status") == "quarantined"
    # at/after -> must NOT be quarantined (b93: seq >= cutover+1 is after)
    after = {"dispatch_id": "d-after", "seq": seq + 1}
    r_after = quarantine_late_outcome(barrier, after)
    assert r_after.get("status") != "quarantined", (
        f"assertion failure: post-barrier outcome must not be quarantined "
        f"(seq={seq+1}, got status={r_after.get('status')!r})"
    )
    at_plus = {"dispatch_id": "d-plus", "seq": seq + 5}
    r_plus = quarantine_late_outcome(barrier, at_plus)
    assert r_plus.get("status") != "quarantined", (
        f"assertion failure: future outcome must not be quarantined "
        f"(seq={seq+5}, got {r_plus.get('status')!r})"
    )


# AC-FR0287-03@v0.8 TRACKS-TRACE IF-ESCAPE-001 stale multiplicity
def test_stale_downstream_returns_per_type_entries(monkeypatch, tmp_path):
    """AC-FR0287-03: stale must emit one evidence.staled per downstream
    evidence type after the target (candidate/FULL_F/CI/security/preview
    etc.), not a single generic entry. Minimal stub returns 1.

    b93 control revision (red_defect re-pin, Prism ruling 2026-09-02):
    this pin targets the documented no-store coarse fallback branch --
    isolate TRACKS_HOME so no store backs the call. The store-backed
    existing-bucket-only semantics are pinned by
    test_escape_gate_red_v3.py::test_stale_emits_only_existing_buckets."""
    monkeypatch.setenv("TRACKS_HOME", str(tmp_path / "no-store"))
    staled = stale_downstream_evidence("run-stale-multi", "M-IMPL")
    assert isinstance(staled, list), "assertion failure: stale must return list"
    # at least the major downstream buckets
    assert len(staled) >= 4, (
        f"assertion failure: stale must return per-type entries "
        f"(expected >=4, got {len(staled)})"
    )
    for entry in staled:
        assert isinstance(entry, dict)
        payload = entry.get("payload", entry)
        reason = payload.get("reason") or entry.get("reason")
        assert reason == "human_return", (
            f"assertion failure: each staled entry must be human_return got {reason!r}"
        )
    # each entry should identify its evidence kind / target to allow
    # per-type replay (not all identical)
    kinds = set()
    for e in staled:
        k = e.get("type") or e.get("evidence_type") or (e.get("payload") or {}).get("type")
        if k:
            kinds.add(k)
    # if types are not surfaced, at least entries must not be byte-identical
    assert len(kinds) > 1 or len({str(x) for x in staled}) > 1, (
        "assertion failure: stale entries must be per-type distinct, not duplicated singletons"
    )
