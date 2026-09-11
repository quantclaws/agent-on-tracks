"""Runtime dispatch across M-PUBLISH operation kinds (AC-FR0275-01..04).

Real local bare git remotes carry tag/merge effects; a loopback GitHub REST
stand-in selected through ``TRAC_GITHUB_API_BASE`` carries release/artifact
effects. The Runtime handler is driven directly so each case asserts the
planned -> executed event chain, resume (reconciled_skip) without duplicate
effects, conflict/failure stop-on-first, and zero-side-effect preflight guards.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tests.integration.test_publish_runtime_direct import (
    _install_push_counter,
    _push_count,
)
from tests.unit.helpers import git_strip
from tests.unit.test_publish_effects_release_artifact import _GitHubStandIn
from tests.unit.test_publish_runtime_direct import _command, _host
from tracks.effects import publish as publish_effects
from tracks.effects.publish import read_remote_state
from tracks.executor.executor import Executor

pytestmark = pytest.mark.integration

_GITHUB_REMOTE = "https://github.com/acme/host.git"
_TAG = "v0.8.0"


class _AgentBackendStub:
    """Backend whose class name marks it as an Agent infrastructure."""


@pytest.fixture
def standin():
    server = _GitHubStandIn().start()
    try:
        yield server
    finally:
        server.close()


def _github_env(monkeypatch, standin):
    monkeypatch.setenv("GITHUB_TOKEN", "loopback-token")
    monkeypatch.setenv("TRAC_GITHUB_API_BASE", standin.base_url)


def _run(executor, store, preview_digest, *, command_id="CMD-OPS-1", reconcile=False):
    command = _command(preview_digest, command_id=command_id)
    executor._do_execute_publish(command, store.state("RUN"), None, reconcile)
    return command


def _events(store, run_id="RUN"):
    return list(store.events(run_id))


def _statuses(store):
    return [
        event.payload["status"]
        for event in _events(store)
        if event.type == "publish.executed"
    ]


def _set_github_origin(repo):
    git_strip(repo, "remote", "set-url", "origin", _GITHUB_REMOTE)


def _stub_tag_effect(monkeypatch, published, candidate, tag=_TAG):
    def read_remote(_remote_url, _kind, target, *, expected_object=None):
        exists = target in published
        return {
            "exists": exists,
            "matches": True if exists else None,
            "object_id": candidate if exists else None,
            "ref": f"refs/tags/{target}",
            "remote": _remote_url,
        }

    def push_tag(_remote_url, target, _ref):
        published.add(target)
        return {
            "status": "done",
            "object_id": candidate,
            "remote_check": {
                "exists": True,
                "matches": True,
                "object_id": candidate,
                "ref": f"refs/tags/{target}",
            },
        }

    monkeypatch.setattr(publish_effects, "read_remote_state", read_remote)
    monkeypatch.setattr(publish_effects, "push_tag", push_tag)


def _upload_posts(api) -> list:
    return [
        request
        for request in api.requests
        if request[0] == "POST" and request[1].startswith("/uploads/")
    ]


# AC-FR0275-01/02: tag planned -> executed(done), resume skips without a push.
def test_tag_planned_executed_done_then_resume_skips(tmp_path, monkeypatch):
    executor, store, candidate, preview, remote = _host(tmp_path)
    counter = tmp_path / "push-count"
    _install_push_counter(Path(remote), counter)
    monkeypatch.chdir(executor.repo)

    _run(executor, store, preview["preview_digest"])

    planned = [e for e in _events(store) if e.type == "publish.planned"]
    assert [e.payload["operation_kind"] for e in planned] == ["tag"]
    assert _statuses(store) == ["done"]
    executed = [e for e in _events(store) if e.type == "publish.executed"][-1]
    assert executed.payload["candidate_sha"] == candidate
    assert executed.payload["idempotency_key"] == planned[0].payload["idempotency_key"]
    assert executed.payload["remote_check"]["object_id"] == candidate
    state = read_remote_state(remote, "tag", _TAG, expected_object=candidate)
    assert state["matches"] is True
    assert _push_count(counter) == 1

    _run(executor, store, preview["preview_digest"], reconcile=True)

    assert _statuses(store) == ["done", "reconciled_skip"]
    assert len([e for e in _events(store) if e.type == "publish.planned"]) == 1
    assert _push_count(counter) == 1


# AC-FR0275-01/02: merge fast-forwards to the candidate; resume skips.
def test_merge_fast_forward_done_then_resume_skips(tmp_path, monkeypatch):
    executor, store, candidate, preview, remote = _host(
        tmp_path, operation_steps=["merge:main"]
    )
    counter = tmp_path / "push-count"
    _install_push_counter(Path(remote), counter)
    git_strip(executor.repo, "push", "-q", "origin", "HEAD~1:refs/heads/main")
    monkeypatch.chdir(executor.repo)
    assert _push_count(counter) == 1

    _run(executor, store, preview["preview_digest"])

    planned = [e for e in _events(store) if e.type == "publish.planned"]
    assert [e.payload["operation_kind"] for e in planned] == ["merge"]
    assert _statuses(store) == ["done"]
    state = read_remote_state(remote, "merge", "main", expected_object=candidate)
    assert state["matches"] is True
    assert _push_count(counter) == 2

    _run(executor, store, preview["preview_digest"], reconcile=True)

    assert _statuses(store) == ["done", "reconciled_skip"]
    assert _push_count(counter) == 2


# AC-FR0275-04: a diverged remote branch is a conflict and is never forced.
def test_merge_divergence_conflicts_without_force(tmp_path, monkeypatch):
    executor, store, candidate, preview, remote = _host(
        tmp_path, operation_steps=["merge:main"]
    )
    repo = executor.repo
    counter = tmp_path / "push-count"
    _install_push_counter(Path(remote), counter)
    branch = git_strip(repo, "branch", "--show-current")
    git_strip(repo, "switch", "-q", "-c", "divergent")
    (repo / "divergent.txt").write_text("remote winner\n", encoding="utf-8")
    git_strip(repo, "add", "divergent.txt")
    git_strip(repo, "commit", "-qm", "divergent remote tip")
    remote_sha = git_strip(repo, "rev-parse", "HEAD")
    git_strip(repo, "switch", "-q", branch)
    git_strip(repo, "push", "-q", "-f", "origin", f"{remote_sha}:refs/heads/main")
    monkeypatch.chdir(repo)
    before = _push_count(counter)
    assert remote_sha != candidate

    _run(executor, store, preview["preview_digest"])

    conflicts = [e for e in _events(store) if e.type == "reconcile_conflict"]
    assert conflicts and conflicts[-1].payload["remote"]["object_id"] == remote_sha
    failed = [e for e in _events(store) if e.type == "publish.failed"][-1]
    assert failed.payload["reason"] == "reconcile_conflict"
    assert _statuses(store) == []
    assert _push_count(counter) == before
    assert read_remote_state(remote, "merge", "main")["object_id"] == remote_sha


# AC-FR0275-01: a missing remote branch fails closed before any other tag.
def test_merge_missing_branch_stops_batch(tmp_path, monkeypatch):
    executor, store, _candidate, preview, remote = _host(
        tmp_path, operation_steps=["merge:main", "tag:v9.9"]
    )
    monkeypatch.chdir(executor.repo)

    _run(executor, store, preview["preview_digest"])

    failed = [e for e in _events(store) if e.type == "publish.failed"][-1]
    assert failed.payload["reason"] == "branch_missing"
    targets = [e.payload["target"] for e in _events(store) if e.type == "publish.planned"]
    assert targets == ["main"]
    assert read_remote_state(remote, "tag", "v9.9")["exists"] is False


# AC-FR0275-01/02: release binds the planned tag, then resume skips the POST.
def test_release_done_then_resume_skips(tmp_path, monkeypatch, standin):
    _github_env(monkeypatch, standin)
    executor, store, candidate, preview, _remote = _host(
        tmp_path, operation_steps=[f"tag:{_TAG}", f"release:{_TAG}"]
    )
    _set_github_origin(executor.repo)
    published: set = set()
    _stub_tag_effect(monkeypatch, published, candidate)

    _run(executor, store, preview["preview_digest"])

    assert _statuses(store) == ["done", "done"]
    assert len(standin.created_payloads) == 1
    created = standin.created_payloads[0]
    assert created["tag_name"] == _TAG
    assert created["name"] == _TAG
    assert created["prerelease"] is False
    assert created["target_commitish"] == candidate
    assert candidate in created["body"]
    assert preview["preview_digest"] in created["body"]
    assert "idempotency_key" in created["body"]

    _run(executor, store, preview["preview_digest"], reconcile=True)

    assert _statuses(store) == ["done", "done", "reconciled_skip", "reconciled_skip"]
    assert len(standin.created_payloads) == 1
    assert len([e for e in _events(store) if e.type == "publish.planned"]) == 2


# AC-FR0275-04: an existing divergent release is a conflict, never re-created.
def test_release_divergence_is_conflict_without_create(
    tmp_path, monkeypatch, standin
):
    _github_env(monkeypatch, standin)
    executor, store, candidate, preview, _remote = _host(
        tmp_path, operation_steps=[f"tag:{_TAG}", f"release:{_TAG}"]
    )
    _set_github_origin(executor.repo)
    _stub_tag_effect(monkeypatch, set(), candidate)
    standin.seed_release(_TAG, prerelease=True, target_commitish=candidate)

    _run(executor, store, preview["preview_digest"])

    assert standin.created_payloads == []
    conflicts = [e for e in _events(store) if e.type == "reconcile_conflict"]
    assert conflicts
    failed = [e for e in _events(store) if e.type == "publish.failed"][-1]
    assert failed.payload["reason"] == "reconcile_conflict"
    assert "reconciled_skip" not in _statuses(store)


# AC-FR0275-01/02: artifact identity is planned, uploaded once, resumed skip.
def test_artifact_upload_done_then_resume_skips(tmp_path, monkeypatch, standin):
    _github_env(monkeypatch, standin)
    executor, store, candidate, preview, _remote = _host(
        tmp_path, operation_steps=[f"tag:{_TAG}", "artifact:dist/*.whl"]
    )
    repo = executor.repo
    dist = repo / "dist"
    dist.mkdir()
    wheel = dist / "demo-0.1.0-py3-none-any.whl"
    wheel.write_bytes(b"wheel-bytes")
    standin.seed_release(_TAG, target_commitish=candidate)
    _set_github_origin(repo)
    _stub_tag_effect(monkeypatch, set(), candidate)

    _run(executor, store, preview["preview_digest"])

    planned = [
        e.payload
        for e in _events(store)
        if e.type == "publish.planned" and e.payload["operation_kind"] == "artifact"
    ]
    assert len(planned) == 1
    digest = hashlib.sha256(b"wheel-bytes").hexdigest()
    assert planned[0]["artifact_path"] == str(wheel.resolve())
    assert planned[0]["name"] == wheel.name
    assert planned[0]["size"] == len(b"wheel-bytes")
    assert planned[0]["sha256_local"] == digest

    executed = [e.payload for e in _events(store) if e.type == "publish.executed"]
    assert [(p["target"], p["status"]) for p in executed] == [
        (_TAG, "done"),
        ("dist/*.whl", "done"),
    ]
    artifact_done = executed[1]
    assert artifact_done["name"] == wheel.name
    assert artifact_done["size"] == len(b"wheel-bytes")
    assert artifact_done["sha256_local"] == digest
    assert len(_upload_posts(standin)) == 1

    _run(executor, store, preview["preview_digest"], reconcile=True)

    assert _statuses(store) == ["done", "done", "reconciled_skip", "reconciled_skip"]
    assert len(_upload_posts(standin)) == 1


# AC-FR0275-04: same-name divergent asset is a conflict with no upload.
def test_artifact_divergence_is_conflict_without_upload(
    tmp_path, monkeypatch, standin
):
    _github_env(monkeypatch, standin)
    executor, store, candidate, preview, _remote = _host(
        tmp_path, operation_steps=[f"tag:{_TAG}", "artifact:dist/*.whl"]
    )
    repo = executor.repo
    dist = repo / "dist"
    dist.mkdir()
    wheel = dist / "demo-0.1.0-py3-none-any.whl"
    wheel.write_bytes(b"wheel-bytes")
    standin.seed_release(_TAG, target_commitish=candidate)
    standin.seed_asset(_TAG, wheel.name, b"different-bytes")
    _set_github_origin(repo)
    _stub_tag_effect(monkeypatch, set(), candidate)

    _run(executor, store, preview["preview_digest"])

    assert _upload_posts(standin) == []
    conflicts = [e for e in _events(store) if e.type == "reconcile_conflict"]
    assert conflicts
    failed = [e for e in _events(store) if e.type == "publish.failed"][-1]
    assert failed.payload["reason"] == "reconcile_conflict"


# AC-FR0275-04: artifact TARGET must match exactly one file (planned=zero).
def test_artifact_multiple_matches_is_malformed_before_any_effect(
    tmp_path, monkeypatch
):
    executor, store, _candidate, preview, remote = _host(
        tmp_path, operation_steps=[f"tag:{_TAG}", "artifact:dist/*.whl"]
    )
    dist = executor.repo / "dist"
    dist.mkdir()
    (dist / "one.whl").write_bytes(b"one")
    (dist / "two.whl").write_bytes(b"two")
    monkeypatch.chdir(executor.repo)

    _run(executor, store, preview["preview_digest"])

    events = _events(store)
    assert not any(
        e.type in {"publish.planned", "publish.executed"} for e in events
    )
    failed = [e for e in events if e.type == "publish.failed"]
    assert failed[-1].payload["reason"] == "malformed"
    assert read_remote_state(remote, "tag", _TAG)["exists"] is False


# AC-FR0275-04: unknown/malformed plans fail before any WAL or remote effect.
@pytest.mark.parametrize(
    ("steps", "reason"),
    [
        ([f"tag:{_TAG}", "webhook:main"], "unknown_operation"),
        ([f"tag:{_TAG}", "merge:bad..branch"], "malformed"),
        ([f"tag:{_TAG}", "release:v9.9"], "malformed"),
    ],
)
def test_preflight_failures_are_zero_side_effect(
    tmp_path, monkeypatch, steps, reason
):
    executor, store, _candidate, preview, remote = _host(
        tmp_path, operation_steps=steps
    )
    monkeypatch.chdir(executor.repo)

    _run(executor, store, preview["preview_digest"])

    events = _events(store)
    assert not any(
        e.type in {"publish.planned", "publish.executed"} for e in events
    )
    failed = [e for e in events if e.type == "publish.failed"]
    assert failed and failed[-1].payload["reason"] == reason
    assert read_remote_state(remote, "tag", _TAG)["exists"] is False


# AC-FR0275-04: a non-GitHub remote fails release honestly; the batch stops.
def test_release_unsupported_remote_stops_batch(tmp_path, monkeypatch):
    executor, store, _candidate, preview, remote = _host(
        tmp_path, operation_steps=[f"tag:{_TAG}", f"release:{_TAG}"]
    )
    monkeypatch.chdir(executor.repo)

    _run(executor, store, preview["preview_digest"])

    assert _statuses(store) == ["done"]
    failed = [e for e in _events(store) if e.type == "publish.failed"][-1]
    assert failed.payload["reason"] == "unsupported_remote"
    assert read_remote_state(remote, "tag", _TAG)["exists"] is True


# AC-FR0275-03: an Agent backend yields publish.blocked with no side effects.
def test_agent_backend_blocks_publish_with_zero_side_effects(tmp_path, monkeypatch):
    executor, store, candidate, preview, remote = _host(tmp_path)
    executor.backend = _AgentBackendStub()
    monkeypatch.chdir(executor.repo)

    _run(executor, store, preview["preview_digest"])

    events = _events(store)
    blocked = [e for e in events if e.type == "publish.blocked"]
    assert blocked and blocked[-1].payload["reason"] == "agent_forbidden"
    assert blocked[-1].payload["preview_digest"] == preview["preview_digest"]
    assert blocked[-1].payload["candidate_sha"] == candidate
    assert not any(
        e.type in {"publish.planned", "publish.executed", "publish.failed"}
        for e in events
    )
    assert read_remote_state(remote, "tag", _TAG)["exists"] is False


# AC-FR0275-03: with nothing resolvable the blocked payload still only blocks.
def test_agent_backend_blocks_without_resolvable_facts(tmp_path):
    executor, store, _candidate, _preview, _remote = _host(tmp_path)
    empty = Executor(store, executor.repo, "EMPTY")
    empty.backend = _AgentBackendStub()

    empty._do_execute_publish(_command(), store.state("EMPTY"), None, False)

    blocked = [e for e in _events(store, "EMPTY") if e.type == "publish.blocked"]
    assert [e.payload for e in blocked] == [{"reason": "agent_forbidden"}]
