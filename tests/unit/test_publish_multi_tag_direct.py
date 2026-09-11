"""RED coverage for ordered multi-tag Runtime publication."""

from __future__ import annotations

from tests.unit.test_publish_runtime_direct import _command, _host
from tracks.effects import publish as publish_effects
from tracks.executor.publish import operation_idempotency_key


def _remote_counter(candidate: str):
    published: set[str] = set()

    def read_remote(_remote, _kind, tag, *, expected_object):
        exists = tag in published
        return {
            "exists": exists,
            "matches": True if exists else None,
            "object_id": candidate if exists else None,
            "expected_object": expected_object,
        }

    return published, read_remote


def test_declared_tags_run_in_order_with_one_wal_record_each(tmp_path, monkeypatch):
    steps = ["tag:{feature_tag}", "tag:v9.9"]
    executor, store, candidate, preview, remote = _host(
        tmp_path, operation_steps=steps
    )
    tags = ["v0.8.0", "v9.9"]
    published, read_remote = _remote_counter(candidate)
    push_calls: list[str] = []

    def push(_remote, tag, _ref):
        push_calls.append(tag)
        published.add(tag)
        return {
            "status": "done",
            "object_id": candidate,
            "remote_check": {
                "exists": True,
                "matches": True,
                "object_id": candidate,
            },
        }

    monkeypatch.setattr(publish_effects, "read_remote_state", read_remote)
    monkeypatch.setattr(publish_effects, "push_tag", push)

    executor._do_execute_publish(
        _command(preview["preview_digest"]), store.state("RUN"), None, False
    )

    planned = [e for e in store.events("RUN") if e.type == "publish.planned"]
    executed = [e for e in store.events("RUN") if e.type == "publish.executed"]
    assert push_calls == tags
    assert [e.payload["target"] for e in planned] == tags
    assert [e.payload["target"] for e in executed] == tags
    assert [e.payload["status"] for e in executed] == ["done", "done"]
    assert all(e.payload["candidate_sha"] == candidate for e in planned + executed)
    assert remote


def test_failed_second_tag_recovers_without_repeating_first(tmp_path, monkeypatch):
    steps = ["tag:{feature_tag}", "tag:v9.9"]
    executor, store, candidate, preview, _remote = _host(
        tmp_path, operation_steps=steps
    )
    first, second = "v0.8.0", "v9.9"
    published, read_remote = _remote_counter(candidate)
    push_calls: list[str] = []
    fail_second = True

    def push(_remote, tag, _ref):
        nonlocal fail_second
        push_calls.append(tag)
        if tag == second and fail_second:
            fail_second = False
            return {
                "status": "failed",
                "object_id": None,
                "remote_check": {"exists": False, "matches": None, "object_id": None},
            }
        published.add(tag)
        return {
            "status": "done",
            "object_id": candidate,
            "remote_check": {
                "exists": True,
                "matches": True,
                "object_id": candidate,
            },
        }

    monkeypatch.setattr(publish_effects, "read_remote_state", read_remote)
    monkeypatch.setattr(publish_effects, "push_tag", push)
    command = _command(preview["preview_digest"])

    executor._do_execute_publish(command, store.state("RUN"), None, False)
    first_run_failed = [e for e in store.events("RUN") if e.type == "publish.failed"]
    assert first_run_failed[-1].payload["idempotency_key"] == operation_idempotency_key(
        preview["preview_digest"], "tag", second
    )
    assert push_calls == [first, second]

    executor._do_execute_publish(command, store.state("RUN"), None, True)
    planned = [e for e in store.events("RUN") if e.type == "publish.planned"]
    executed = [e for e in store.events("RUN") if e.type == "publish.executed"]
    assert push_calls == [first, second, second]
    assert [e.payload["target"] for e in planned].count(first) == 1
    assert [e.payload["target"] for e in planned].count(second) == 1
    assert [e.payload["status"] for e in executed] == [
        "done",
        "reconciled_skip",
        "done",
    ]


def test_unsupported_operation_is_rejected_before_any_tag_effect(tmp_path, monkeypatch):
    executor, store, _candidate, preview, _remote = _host(
        tmp_path, operation_steps=["tag:{feature_tag}", "webhook:main"]
    )
    calls: list[str] = []
    monkeypatch.setattr(publish_effects, "push_tag", lambda *_args: calls.append("push"))

    executor._do_execute_publish(
        _command(preview["preview_digest"]), store.state("RUN"), None, False
    )

    assert calls == []
    failed = [e for e in store.events("RUN") if e.type == "publish.failed"][-1]
    assert failed.payload["reason"] == "unknown_operation"


def test_malformed_target_rejects_batch_before_any_tag_effect(tmp_path, monkeypatch):
    executor, store, _candidate, preview, _remote = _host(
        tmp_path, operation_steps=["tag:{feature_tag}", "tag:"]
    )
    calls: list[str] = []
    monkeypatch.setattr(publish_effects, "push_tag", lambda *_args: calls.append("push"))

    executor._do_execute_publish(
        _command(preview["preview_digest"]), store.state("RUN"), None, False
    )

    assert calls == []
    assert [e for e in store.events("RUN") if e.type == "publish.planned"] == []
    failed = [e for e in store.events("RUN") if e.type == "publish.failed"][-1]
    assert failed.payload["reason"] == "malformed"


def test_failure_stops_remaining_tags_and_recovery_completes_them(
    tmp_path, monkeypatch
):
    steps = ["tag:{feature_tag}", "tag:v9.9", "tag:v9.10"]
    executor, store, candidate, preview, _remote = _host(
        tmp_path, operation_steps=steps
    )
    first, second, third = "v0.8.0", "v9.9", "v9.10"
    published, read_remote = _remote_counter(candidate)
    push_calls: list[str] = []
    fail_second = True

    def push(_remote, tag, _ref):
        nonlocal fail_second
        push_calls.append(tag)
        if tag == second and fail_second:
            fail_second = False
            return {
                "status": "failed",
                "object_id": None,
                "remote_check": {"exists": False, "matches": None, "object_id": None},
            }
        published.add(tag)
        return {
            "status": "done",
            "object_id": candidate,
            "remote_check": {
                "exists": True,
                "matches": True,
                "object_id": candidate,
            },
        }

    monkeypatch.setattr(publish_effects, "read_remote_state", read_remote)
    monkeypatch.setattr(publish_effects, "push_tag", push)
    command = _command(preview["preview_digest"])

    executor._do_execute_publish(command, store.state("RUN"), None, False)

    # Stop on the first failed effect: the third tag is never attempted.
    assert push_calls == [first, second]
    failed = [e for e in store.events("RUN") if e.type == "publish.failed"][-1]
    assert failed.payload["idempotency_key"] == operation_idempotency_key(
        preview["preview_digest"], "tag", second
    )
    executed = [e for e in store.events("RUN") if e.type == "publish.executed"]
    assert [e.payload["status"] for e in executed] == ["done"]

    executor._do_execute_publish(command, store.state("RUN"), None, True)

    planned = [e for e in store.events("RUN") if e.type == "publish.planned"]
    executed = [e for e in store.events("RUN") if e.type == "publish.executed"]
    assert push_calls == [first, second, second, third]
    assert [e.payload["target"] for e in planned] == [first, second, third]
    assert [e.payload["status"] for e in executed] == [
        "done",
        "reconciled_skip",
        "done",
        "done",
    ]
    assert all(e.payload["candidate_sha"] == candidate for e in planned + executed)


def test_invalid_later_tag_target_rejects_batch_before_any_effect(
    tmp_path, monkeypatch
):
    """Preflight: every tag target is ref-format validated before any
    WAL/effect. A later invalid target must not allow an earlier valid tag
    to be pushed (no planned records, no remote read, no push)."""
    executor, store, _candidate, preview, _remote = _host(
        tmp_path, operation_steps=["tag:{feature_tag}", "tag:bad..tag"]
    )
    pushes: list[str] = []
    reads: list[str] = []
    monkeypatch.setattr(
        publish_effects,
        "read_remote_state",
        lambda _remote_url, _kind, tag, **kwargs: reads.append(tag)
        or {"exists": False, "matches": None, "object_id": None},
    )
    monkeypatch.setattr(
        publish_effects, "push_tag", lambda *_args: pushes.append("push")
    )

    executor._do_execute_publish(
        _command(preview["preview_digest"]), store.state("RUN"), None, False
    )

    assert reads == []
    assert pushes == []
    assert [e for e in store.events("RUN") if e.type == "publish.planned"] == []
    failed = [e for e in store.events("RUN") if e.type == "publish.failed"][-1]
    assert failed.payload["reason"] == "malformed"


def test_valid_multi_segment_tag_names_still_publish(tmp_path, monkeypatch):
    """Ref-format preflight must not over-reject valid tag names."""
    steps = ["tag:v2/candidate-1", "tag:{feature_tag}"]
    executor, store, candidate, preview, _remote = _host(
        tmp_path, operation_steps=steps
    )
    tags = ["v2/candidate-1", "v0.8.0"]
    published, read_remote = _remote_counter(candidate)
    push_calls: list[str] = []

    def push(_remote, tag, _ref):
        push_calls.append(tag)
        published.add(tag)
        return {
            "status": "done",
            "object_id": candidate,
            "remote_check": {
                "exists": True,
                "matches": True,
                "object_id": candidate,
            },
        }

    monkeypatch.setattr(publish_effects, "read_remote_state", read_remote)
    monkeypatch.setattr(publish_effects, "push_tag", push)

    executor._do_execute_publish(
        _command(preview["preview_digest"]), store.state("RUN"), None, False
    )

    planned = [e for e in store.events("RUN") if e.type == "publish.planned"]
    executed = [e for e in store.events("RUN") if e.type == "publish.executed"]
    assert push_calls == tags
    assert [e.payload["target"] for e in planned] == tags
    assert [e.payload["target"] for e in executed] == tags
    assert [e.payload["status"] for e in executed] == ["done", "done"]
