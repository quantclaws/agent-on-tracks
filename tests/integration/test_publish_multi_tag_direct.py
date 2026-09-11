"""AC-FR0275-01/02/03/04: ordered real-remote tag batch and recovery."""
from __future__ import annotations

import pytest

from tests.integration.test_publish_runtime_direct import (
    _events,
    _issue,
    _key,
    _prepare,
    _push_count,
    _tag_sha,
)
from tracks import paths
from tracks.executor.executor import Executor
from tracks.kernel.events import Command
from tracks.store import Store

pytestmark = pytest.mark.integration


def test_batch_publishes_both_tags_in_contract_order(host_repo, trac, tmp_path, monkeypatch):
    run, sha, _, remote, preview, counter = _prepare(
        host_repo, trac, tmp_path, operation_steps=["tag:first", "tag:second"]
    )
    monkeypatch.chdir(host_repo)
    _issue(host_repo, run, preview["preview_digest"])
    operations = [e for e in _events(host_repo, run)
                  if e.type in {"publish.planned", "publish.executed"}]
    assert [(e.type, e.payload["target"]) for e in operations] == [
        ("publish.planned", "first"), ("publish.executed", "first"),
        ("publish.planned", "second"), ("publish.executed", "second"),
    ]
    for event in operations:
        assert event.payload["idempotency_key"] == _key(
            preview["preview_digest"], "tag", event.payload["target"]
        )
    assert all(e.payload["status"] == "done" for e in operations
               if e.type == "publish.executed")
    assert _tag_sha(remote, "first", sha) == _tag_sha(remote, "second", sha) == sha
    assert _push_count(counter) == 3


def test_batch_remote_rejection_recovers_without_repeating_first_tag(
    host_repo, trac, tmp_path, monkeypatch
):
    run, sha, _, remote, preview, counter = _prepare(
        host_repo, trac, tmp_path, operation_steps=["tag:first", "tag:second", "tag:third"]
    )
    hook = remote / "hooks" / "update"
    hook.write_text('#!/bin/sh\n[ "$1" != "refs/tags/second" ]\n')
    hook.chmod(0o755)
    monkeypatch.chdir(host_repo)
    _issue(host_repo, run, preview["preview_digest"])
    before = _events(host_repo, run)
    assert _tag_sha(remote, "first", sha) == sha
    assert _tag_sha(remote, "second") is None
    assert _tag_sha(remote, "third") is None
    assert _push_count(counter) == 2
    assert any(e.type == "publish.failed" for e in before)
    assert not any(e.type == "publish.executed" and e.payload["target"] != "first"
                   for e in before)
    issued = next(e for e in before if e.type == "command.issued"
                  and e.payload["command"]["kind"] == "execute_publish")
    command = Command(**issued.payload["command"])
    hook.unlink()
    store = Store(paths.tracks_home(host_repo))
    try:
        Executor(store, host_repo, run)._execute(
            command, store.state(run), None, reconcile=True
        )
    finally:
        store.close()
    after = [e for e in _events(host_repo, run) if e.seq > before[-1].seq]
    outcomes = [e.payload for e in after if e.type == "publish.executed"]
    assert [(e["target"], e["status"]) for e in outcomes] == [
        ("first", "reconciled_skip"), ("second", "done"), ("third", "done")
    ]
    assert all(_tag_sha(remote, tag, sha) == sha for tag in ("first", "second", "third"))
    assert _push_count(counter) == 4


@pytest.mark.parametrize("later,reason", [("webhook:main", "unknown_operation"),
                                         ("tag:bad..tag", "malformed")])
def test_batch_preflights_unsupported_later_operation_before_first_push(
    host_repo, trac, tmp_path, monkeypatch, later, reason
):
    run, _, _, remote, preview, counter = _prepare(
        host_repo, trac, tmp_path, operation_steps=["tag:first", later]
    )
    monkeypatch.chdir(host_repo)
    _issue(host_repo, run, preview["preview_digest"])
    events = _events(host_repo, run)
    assert not any(e.type in {"publish.planned", "publish.executed"} for e in events)
    failed = [e for e in events if e.type == "publish.failed"]
    assert failed[-1].payload["reason"] == reason
    assert _tag_sha(remote, "first") is None
    assert _push_count(counter) == 1
