"""Behavior coverage for the escape-barrier helpers (``executor.escape``).

Direct unit coverage for the store-backed and no-store branches the CLI/run
journeys exercise only through full runs: store open/close failure tolerance,
persistent max-seq reads, the pure-unit barrier fallback, irreversible
operation reporting and staled-bucket filtering.
"""

from __future__ import annotations

import json

from tracks.executor import escape


class _Cursor:
    def __init__(self, *, one=None, many=()):
        self._one = one
        self._many = list(many)

    def fetchone(self):
        return self._one

    def fetchall(self):
        return list(self._many)


class _Store:
    def __init__(self, *, cursor=None, error=None, payloads=None, close_error=None):
        self._cursor = cursor
        self._error = error
        self._payloads = payloads or {}
        self._close_error = close_error
        self.closed = False

    @property
    def conn(self):
        return self

    def execute(self, *_args, **_kwargs):
        if self._error is not None:
            raise self._error
        return self._cursor

    def load_payload(self, payload):
        return self._payloads.get(payload.get("$ref"))

    def close(self):
        self.closed = True
        if self._close_error is not None:
            raise self._close_error


def test_open_store_failure_returns_none(monkeypatch):
    import tracks.paths

    def boom(*_args, **_kwargs):
        raise RuntimeError("no tracks home")

    monkeypatch.setattr(tracks.paths, "tracks_home", boom)
    assert escape._open_store() is None


def test_close_store_none_and_exception_suppressed():
    escape._close_store(None)
    store = _Store(close_error=RuntimeError("already gone"))
    escape._close_store(store)
    assert store.closed


def test_persistent_max_seq_store_paths(monkeypatch):
    monkeypatch.setattr(escape, "_open_store", lambda: None)
    assert escape._persistent_max_seq("RUN") is None

    store = _Store(cursor=_Cursor(one=(7,)))
    monkeypatch.setattr(escape, "_open_store", lambda: store)
    assert escape._persistent_max_seq("RUN") == 7
    assert store.closed

    empty = _Store(cursor=_Cursor(one=None))
    monkeypatch.setattr(escape, "_open_store", lambda: empty)
    assert escape._persistent_max_seq("RUN") == 0

    broken = _Store(error=RuntimeError("db gone"))
    monkeypatch.setattr(escape, "_open_store", lambda: broken)
    assert escape._persistent_max_seq("RUN") is None
    assert broken.closed


def test_existing_event_types_store_paths(monkeypatch):
    monkeypatch.setattr(escape, "_open_store", lambda: None)
    assert escape._existing_event_types("RUN") is None

    store = _Store(cursor=_Cursor(many=[("candidate.frozen",), ("ci.run_observed",)]))
    monkeypatch.setattr(escape, "_open_store", lambda: store)
    assert escape._existing_event_types("RUN") == {
        "candidate.frozen",
        "ci.run_observed",
    }

    broken = _Store(error=RuntimeError("db gone"))
    monkeypatch.setattr(escape, "_open_store", lambda: broken)
    assert escape._existing_event_types("RUN") is None


def test_establish_barrier_store_backed_monotonic(monkeypatch):
    store = _Store(cursor=_Cursor(one=(5,)))
    monkeypatch.setattr(escape, "_open_store", lambda: store)
    monkeypatch.delitem(escape._BARRIER_LAST, "RUN-BARRIER", raising=False)
    first = escape.establish_escape_barrier("RUN-BARRIER")
    assert first == {"cutover_seq": 5, "quiesced_dispatches": []}
    monkeypatch.setitem(escape._BARRIER_LAST, "RUN-BARRIER", 99)
    second = escape.establish_escape_barrier("RUN-BARRIER")
    assert second["cutover_seq"] == 100


def test_establish_barrier_no_store_hash_fallback(monkeypatch):
    monkeypatch.setattr(escape, "_persistent_max_seq", lambda _run: None)
    monkeypatch.delitem(escape._BARRIER_LAST, "RUN-PURE", raising=False)
    first = escape.establish_escape_barrier("RUN-PURE")
    second = escape.establish_escape_barrier("RUN-PURE")
    assert second["cutover_seq"] > first["cutover_seq"]
    monkeypatch.setitem(escape._BARRIER_LAST, "RUN-PURE", 10**9)
    forced = escape.establish_escape_barrier("RUN-PURE")
    assert forced["cutover_seq"] == 10**9 + 1


def test_is_already_executed_only_done_publish():
    assert escape._is_already_executed("publish.executed", {"status": "done"}) is True
    assert escape._is_already_executed("publish.executed", {"status": "skipped"}) is False
    assert escape._is_already_executed("publish.planned", {"status": "done"}) is False


def test_load_event_payload_json_ref_and_error():
    store = _Store(payloads={"blob": {"status": "done", "target": "v1"}})
    ref = json.dumps({"$ref": "blob"})
    assert escape._load_event_payload(store, ref) == {"status": "done", "target": "v1"}
    assert escape._load_event_payload(store, "[]") == {}
    assert escape._load_event_payload(store, "{not json") == {}


def test_report_irreversible_no_store(monkeypatch):
    monkeypatch.setattr(escape, "_open_store", lambda: None)
    assert escape.report_irreversible_operations("RUN") == []


def test_report_irreversible_reads_done_operations(monkeypatch):
    done = json.dumps({"status": "done", "operation_kind": "tag", "target": "v1"})
    ref = json.dumps({"$ref": "blob"})
    store = _Store(
        cursor=_Cursor(
            many=[
                ("publish.executed", done),
                ("publish.planned", done),
                ("publish.executed", json.dumps({"status": "skipped"})),
                ("publish.executed", ref),
            ]
        ),
        payloads={"blob": {"status": "done", "kind": "artifact", "target": "pkg"}},
    )
    monkeypatch.setattr(escape, "_open_store", lambda: store)
    ops = escape.report_irreversible_operations("RUN")
    assert [op["operation_kind"] for op in ops] == ["tag", "artifact"]
    assert ops[0]["target"] == "v1"
    assert ops[1]["target"] == "pkg"


def test_report_irreversible_failure_returns_empty(monkeypatch):
    store = _Store(error=RuntimeError("db gone"))
    monkeypatch.setattr(escape, "_open_store", lambda: store)
    assert escape.report_irreversible_operations("RUN") == []
    assert store.closed
