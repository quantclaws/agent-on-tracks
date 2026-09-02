"""T-004 RED (b93 re-dispatch): persistent cutover & no-empty-bucket stale.

Pins the b93 delivery-face deltas (architecture §1.0.14.1) against the
current escape.py, which still carries the deprecated fallbacks:

- AC-FR0287-02 (1): cutover_seq MUST be the run's persistent max seq from
  the event store (barrier event seq = cutover+1, cross-process monotonic);
  the run_id hash / in-process counter fallback is DEPRECATED — a
  store-backed run with an empty log must report cutover_seq == 0, never a
  hash-derived constant.
- AC-FR0287-03 (2): evidence.staled is emitted ONLY for buckets that
  actually exist in the run's event log; a store-backed run with an empty
  log has no buckets and must emit NOTHING (never a blanket all-buckets
  payload).

Both tests fail on the current baseline with assertion_failure on the
contract behaviour (no stub_token, no assembly errors). Only unit tests are
added (RED discipline, manifest red_test_paths = tests/unit; TRACKS_HOME is
monkeypatched to an isolated store so the repo runtime DB is untouched).

IF-ESCAPE-001: establish_escape_barrier, stale_downstream_evidence
"""

from __future__ import annotations

from tracks.executor.escape import (
    establish_escape_barrier,
    stale_downstream_evidence,
)


def _isolated_store(tmp_path):
    """Create an isolated TRACKS_HOME store; returns (Store, cleanup)."""
    home = tmp_path / ".tracks"
    from tracks.store import Store

    store = Store(home)
    return store, home


# AC-FR0287-02@v0.8 TRACKS-TRACE IF-ESCAPE-001 persistent cutover seq (b93-1)
def test_barrier_cutover_is_persistent_max_seq(tmp_path, monkeypatch):
    """b93 (1): cutover_seq = persistent max seq of the run's event log
    (barrier event lands at cutover+1, cross-process monotonic). The
    run_id-hash / in-process fallback is deprecated: a store-backed run with
    an empty log must report cutover_seq == 0, not a hash constant."""
    store, home = _isolated_store(tmp_path)
    store.close()
    monkeypatch.setenv("TRACKS_HOME", str(home))
    try:
        barrier = establish_escape_barrier("RUN-b93-cutover")
    except NotImplementedError as err:
        raise AssertionError(
            "assertion failure: establish_escape_barrier persistent path not implemented"
        ) from err
    assert isinstance(barrier, dict)
    seq = barrier.get("cutover_seq")
    assert isinstance(seq, int)
    assert seq == 0, (
        "assertion failure: cutover_seq must equal the run's persistent max seq "
        f"(0 for an empty log), got hash/counter fallback {seq}"
    )


# AC-FR0287-03@v0.8 TRACKS-TRACE IF-ESCAPE-001 no-empty-bucket stale (b93-2)
def test_stale_emits_only_existing_buckets(tmp_path, monkeypatch):
    """b93 (2): evidence.staled is emitted per ACTUALLY EXISTING bucket only;
    a store-backed run with an empty log has no buckets and must emit
    nothing — never a blanket all-buckets payload."""
    store, home = _isolated_store(tmp_path)
    store.close()
    monkeypatch.setenv("TRACKS_HOME", str(home))
    try:
        staled = stale_downstream_evidence("RUN-b93-stale", "M-IMPL")
    except NotImplementedError as err:
        raise AssertionError(
            "assertion failure: stale_downstream_evidence existing-bucket path "
            "not implemented"
        ) from err
    assert isinstance(staled, list)
    assert staled == [], (
        "assertion failure: a run with no candidate/CI/security/preview/decision "
        f"buckets must emit no evidence.staled, got {len(staled)} entries"
    )


# AC-FR0287-03@v0.8 TRACKS-TRACE IF-ESCAPE-001 stale buckets follow the log
def test_stale_follows_existing_buckets_only(tmp_path, monkeypatch):
    """b93 (2) positive slice: after a candidate freeze event exists, the
    candidate bucket is emitted; buckets absent from the log stay absent."""
    home = tmp_path / ".tracks"
    from tracks.store import Store

    store = Store(home)
    store.append(
        "RUN-b93-partial",
        "v0.8",
        "candidate.frozen",
        {"candidate_sha": "c" * 40, "clean_tree": True, "branch": "main"},
    )
    store.close()
    monkeypatch.setenv("TRACKS_HOME", str(home))
    try:
        staled = stale_downstream_evidence("RUN-b93-partial", "M-IMPL")
    except NotImplementedError as err:
        raise AssertionError(
            "assertion failure: stale_downstream_evidence existing-bucket path "
            "not implemented (partial log)"
        ) from err
    assert isinstance(staled, list)
    kinds = {
        (e.get("payload", {}).get("type") or e.get("evidence_type") or e.get("bucket"))
        for e in staled
        if isinstance(e, dict)
    }
    assert "candidate.frozen" in kinds, (
        f"assertion failure: existing candidate bucket must be staled, got {kinds!r}"
    )
    assert "release.decided" not in kinds, (
        f"assertion failure: absent release.decided bucket must not be emitted, got {kinds!r}"
    )
    assert "ci.run_observed" not in kinds, (
        f"assertion failure: absent ci bucket must not be emitted, got {kinds!r}"
    )
