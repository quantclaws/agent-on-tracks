"""Store integration (AC-28b/c, AC-N04a): real SQLite + real filesystem."""
import json

from tracks import paths
from tracks.store import Store, new_ulid


def make_store(tmp_path):
    home = tmp_path / ".tracks"
    return Store(home), home


def test_large_payload_externalized_to_blob(tmp_path):
    """AC-28b: >8KB payload lands in runtime/blobs/{sha256}; the event row
    holds a $ref; reading back returns the original payload."""
    store, home = make_store(tmp_path)
    run_id = new_ulid()
    big = {"data": "x" * 10_000}
    store.append(run_id, "v0.1", "story.requested", big)

    row = store.conn.execute(
        "SELECT payload FROM events WHERE run_id = ?", (run_id,)
    ).fetchone()
    stored = json.loads(row[0])
    assert set(stored) == {"$ref"}
    blob = paths.blobs_dir(home) / stored["$ref"]
    assert blob.is_file()

    read_back = list(store.events(run_id))[0].payload
    assert read_back == big


def test_append_only_surface_and_byte_stability(tmp_path):
    """AC-28c: no public update/delete; existing rows byte-identical after
    further appends and a projection rebuild."""
    assert not any(
        name.startswith(("update", "delete", "remove"))
        for name in dir(Store)
        if not name.startswith("_")
    )

    store, home = make_store(tmp_path)
    run_id = new_ulid()
    store.append(run_id, "v0.1", "story.requested", {"raw_chars": 3})
    store.append(run_id, "v0.1", "stage.entered", {"stage": "M-START"})

    snapshot = store.conn.execute(
        "SELECT * FROM events ORDER BY run_id, seq"
    ).fetchall()

    store.append(run_id, "v0.1", "stage.exited", {"stage": "M-START"})
    store.rebuild_projections()

    after = store.conn.execute(
        "SELECT * FROM events ORDER BY run_id, seq"
    ).fetchall()
    assert after[: len(snapshot)] == snapshot
    assert len(after) == len(snapshot) + 1


def test_seq_strictly_increasing_per_run(tmp_path):
    """AC-28a: (run_id, seq) unique, seq monotonic per run."""
    store, _ = make_store(tmp_path)
    a, b = new_ulid(), new_ulid()
    for rid in (a, b, a, b, a):
        store.append(rid, "v0.1", "stage.entered", {"stage": "M-START"})
    for rid, want in ((a, [1, 2, 3]), (b, [1, 2])):
        seqs = [e.seq for e in store.events(rid)]
        assert seqs == want


def test_projections_rebuilt_from_events_alone(tmp_path):
    """AC-N04a: drop runs/backlog; rebuild reproduces both from events."""
    store, home = make_store(tmp_path)
    run_id = new_ulid()
    store.append(run_id, "v0.1", "story.requested", {"raw_chars": 1})
    store.append(run_id, "v0.1", "stage.entered", {"stage": "M-STORY"})
    store.append(
        run_id, "v0.1", "backlog.recorded",
        {"version": "v0.1", "decision": "park", "reason": "triage"},
    )

    store.conn.execute("DELETE FROM runs")
    store.conn.execute("DELETE FROM backlog")
    store.conn.commit()
    store.rebuild_projections()

    runs = store.conn.execute("SELECT run_id, version FROM runs").fetchall()
    assert runs == [(run_id, "v0.1")]
    backlog = store.conn.execute(
        "SELECT run_id, decision FROM backlog"
    ).fetchall()
    assert backlog == [(run_id, "park")]


def test_backlog_only_phantom_is_not_active(tmp_path):
    """SM-01.2 regression: `backlog.recorded` appended for a queue-while-active
    requirement (no prior `stage.entered`) must NOT make that run_id count as
    active. Previously the projection upserted a row with status='active' for
    any run_id passed to `Store.append`, so the phantom blocked every future
    `trac start`. The reducer now marks such placeholder runs status='backlog'
    and `active_run()` skips them."""
    store, _ = make_store(tmp_path)
    bid = new_ulid()
    store.append(
        bid, "v0.4", "backlog.recorded",
        {"version": "v0.4", "decision": "queued", "reason": "active_run"},
    )
    row = store.conn.execute(
        "SELECT status, stage FROM runs WHERE run_id = ?", (bid,)
    ).fetchone()
    assert row == ("backlog", None)
    assert store.state(bid).status == "backlog"
    assert store.active_run() is None


def test_backlog_phantom_does_not_shadow_real_active_run(tmp_path):
    """SM-01.2: with a real active run AND a backlog phantom present,
    `active_run()` returns the real run, not the most-recent phantom."""
    store, _ = make_store(tmp_path)
    real = new_ulid()
    store.append(real, "v0.1", "story.requested", {"raw_chars": 5})
    store.append(real, "v0.1", "stage.entered", {"stage": "M-START"})
    store.append(real, "v0.1", "stage.exited", {"stage": "M-START"})
    store.append(real, "v0.1", "stage.entered", {"stage": "M-STORY"})

    # Second start while active -> queue to backlog with a fresh run_id.
    bid = new_ulid()
    store.append(
        bid, "v0.2", "backlog.recorded",
        {"version": "v0.2", "decision": "queued", "reason": "active_run"},
    )
    # Phantom was appended LAST (newer updated_ts) but must not win.
    assert store.active_run() == real


def test_backlog_recorded_on_real_run_keeps_status_active(tmp_path):
    """FR-09 reject teardown: `backlog.recorded` appended to a run that has
    already entered a stage (M-STORY triage no_go/park) must NOT flip status to
    'backlog' - `decide()` still needs to issue delete_branch -> complete_run,
    which requires status='active'."""
    store, _ = make_store(tmp_path)
    rid = new_ulid()
    store.append(rid, "v0.1", "story.requested", {"raw_chars": 5})
    store.append(rid, "v0.1", "stage.entered", {"stage": "M-START"})
    store.append(rid, "v0.1", "stage.exited", {"stage": "M-START"})
    store.append(rid, "v0.1", "stage.entered", {"stage": "M-STORY"})
    store.append(rid, "v0.1", "outcome.received", {"role": "scribe", "status": "done"})
    store.append(rid, "v0.1", "human.triage", {"decision": "no_go", "actor": "H"})
    store.append(
        rid, "v0.1", "backlog.recorded",
        {"version": "v0.1", "decision": "no_go", "reason": "triage"},
    )
    s = store.state(rid)
    assert s.status == "active" and s.stage == "M-STORY" and s.backlog_recorded is True
    assert store.active_run() == rid


def test_projection_rebuild_preserves_backlog_distinction(tmp_path):
    """NFR-04: dropping the `runs` projection and rebuilding from events alone
    must reproduce the status='backlog' distinction for phantom rows, so the
    fix survives a projection reset."""
    store, _ = make_store(tmp_path)
    phantom = new_ulid()
    store.append(
        phantom, "v0.4", "backlog.recorded",
        {"version": "v0.4", "decision": "queued", "reason": "active_run"},
    )
    real = new_ulid()
    store.append(real, "v0.1", "story.requested", {"raw_chars": 5})
    store.append(real, "v0.1", "stage.entered", {"stage": "M-START"})

    store.conn.execute("DELETE FROM runs")
    store.conn.execute("DELETE FROM backlog")
    store.conn.commit()
    store.rebuild_projections()

    statuses = dict(store.conn.execute(
        "SELECT run_id, status FROM runs"
    ).fetchall())
    assert statuses == {phantom: "backlog", real: "active"}
    assert store.active_run() == real


def test_active_run_skips_stale_backlog_phantom_projection(tmp_path):
    """SM-01.2 stale-projection robustness: a phantom row left over from a
    pre-fix DB (status='active' in the `runs` table, but no stage.entered event
    in the event log) must NOT be returned by `active_run()` even before a
    `rebuild_projections()` is triggered. The `stage IS NULL` filter is robust
    to such staleness because `stage` derives from the absence of
    `stage.entered` events, not from the (possibly stale) status column.

    This mirrors the repo's own .tracks state at fix time: the Maestro appends
    `run.completed` to the stale v0.3 run (re-projecting only that row), leaving
    the phantom row's status='active' untouched - yet `active_run()` must still
    skip it."""
    store, _ = make_store(tmp_path)
    phantom = new_ulid()
    real = new_ulid()
    # Simulate a pre-fix DB: insert the phantom row directly with status='active'
    # (as the old buggy `_project_run` would have written it), and a real run.
    store.conn.execute(
        "INSERT INTO runs (run_id, version, status, stage, substate, awaiting, updated_ts) "
        "VALUES (?,?,?,?,?,?,?)",
        (phantom, "v0.4", "active", None, None, None, "2026-01-01T00:00:00+00:00"),
    )
    store.conn.execute(
        "INSERT INTO runs (run_id, version, status, stage, substate, awaiting, updated_ts) "
        "VALUES (?,?,?,?,?,?,?)",
        (real, "v0.1", "active", "M-STORY", "TRIAGE", None, "2026-01-01T00:00:00+00:00"),
    )
    store.conn.commit()
    # No rebuild - the stale phantom row has status='active' but stage=NULL.
    assert store.active_run() == real

    # And if the real run is completed (Maestro's bootstrap append), the phantom
    # is still skipped - active_run() returns None, unblocking future starts.
    store.conn.execute(
        "UPDATE runs SET status='completed' WHERE run_id=?", (real,)
    )
    store.conn.commit()
    assert store.active_run() is None
