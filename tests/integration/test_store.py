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
