"""Direct RED coverage for Runtime-owned tag publish orchestration."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from tests.unit.helpers import git_repo, git_strip
from tracks import paths
from tracks.effects import publish as publish_effects
from tracks.executor.executor import Executor
from tracks.executor.publish import operation_idempotency_key
from tracks.executor.release_gate import build_operation_plan, generate_preview
from tracks.kernel.events import Command
from tracks.store import Store

_TAG = "v0.8.0"
_PREVIEW_FACTS = {
    "artifact_digest": "",
    "evidence_digests": {"full_f": "sha256:" + "e1" * 32},
}


def _host(
    tmp_path: Path,
    *,
    operation: str = "tag",
    operation_steps: list[str] | None = None,
    build_artifact: str | None = None,
) -> tuple[Executor, Store, str, dict, str]:
    repo = git_repo(tmp_path, gitignore=True)
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    git_strip(repo, "remote", "add", "origin", str(remote))
    projects = repo / ".tracks" / "projects"
    projects.mkdir(parents=True, exist_ok=True)
    contract_text = (
        "[host-contract]\n"
        "version = 1\n"
        "language = 'python'\n"
        "toolchain = 'cpython'\n"
        "install = ''\n"
        "\n[host-contract.version_scheme]\n"
        "feature_tag = 'v{minor}.0'\n"
        "patch_line = 'v{minor}.{n}'\n"
        "prerelease_tag = 'v{minor}.{n}-pre.{ulid}'\n"
        "\n[host-contract.operations.feature]\n"
        f"steps = {json.dumps(operation_steps or [f'{operation}:{{feature_tag}}'])}\n"
    )
    if build_artifact is not None:
        contract_text += (
            "\n[host-contract.build]\n"
            f"artifact = {json.dumps(build_artifact)}\n"
        )
    contract_path = projects / "project.toml"
    contract_path.write_text(contract_text, encoding="utf-8")
    git_strip(repo, "add", "-f", ".tracks/projects/project.toml")
    git_strip(repo, "commit", "-m", "publish contract")
    candidate = git_strip(repo, "rev-parse", "HEAD")
    declared_steps = operation_steps or [f"{operation}:{{feature_tag}}"]
    contract_table: dict = {"operations": {"feature": {"steps": declared_steps}}}
    if build_artifact is not None:
        contract_table["build"] = {"artifact": build_artifact}
    plan = build_operation_plan(
        contract_table,
        "feature",
        {"feature_tag": _TAG},
    )
    operation_digest = "sha256:" + hashlib.sha256(
        json.dumps(plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    facts = {
        **_PREVIEW_FACTS,
        "operation_plan_digest": operation_digest,
        "contract_policy_digest": "sha256:"
        + hashlib.sha256(contract_path.read_bytes()).hexdigest(),
    }
    preview = generate_preview(candidate, facts, risks=[], plan=plan)
    seed_store = Store(repo / ".tracks")
    blob = seed_store.write_audit_blob(preview)
    assert blob is not None
    assert (paths.blobs_dir(repo / ".tracks") / blob).is_file()
    seed_store.close()
    preview = dict(preview)
    preview["blob_ref"] = f".tracks/runtime/blobs/{blob}"
    store = Store(repo / ".tracks")
    store.append("RUN", "v0.8", "candidate.frozen", {"candidate_sha": candidate})
    store.append("RUN", "v0.8", "release.previewed", preview)
    store.append(
        "RUN",
        "v0.8",
        "release.decided",
        {
            "candidate_sha": candidate,
            "preview_digest": preview["preview_digest"],
            "action": "release",
            "actor": "human",
        },
    )
    return Executor(store, repo, "RUN"), store, candidate, preview, str(remote)


def _command(preview_digest: str | None = None, *, command_id: str = "CMD-PUBLISH-1") -> Command:
    params = {} if preview_digest is None else {"preview_digest": preview_digest}
    return Command(
        "execute_publish",
        params=params,
        command_id=command_id,
    )


def test_runtime_plans_then_executes_tag_after_approval(tmp_path, monkeypatch):
    executor, store, candidate, preview, remote = _host(tmp_path)
    calls: list[tuple[str, str, str]] = []

    def fake_push(remote_url: str, tag: str, ref: str) -> dict:
        calls.append((remote_url, tag, ref))
        assert [event.type for event in store.events("RUN")][-1] == "publish.planned"
        return {
            "status": "done",
            "remote_check": {"exists": True, "matches": True, "object_id": candidate},
        }

    monkeypatch.setattr(publish_effects, "push_tag", fake_push)

    executor._do_execute_publish(
        _command(preview["preview_digest"]), store.state("RUN"), None, False
    )

    planned = [e for e in store.events("RUN") if e.type == "publish.planned"][-1]
    executed = [e for e in store.events("RUN") if e.type == "publish.executed"][-1]
    assert planned.payload["operation_kind"] == "tag"
    assert planned.payload["target"] == _TAG
    assert planned.payload["preview_digest"] == preview["preview_digest"]
    assert planned.payload["candidate_sha"] == candidate
    assert planned.payload["when"] is None
    assert planned.payload["idempotency_key"] == operation_idempotency_key(
        preview["preview_digest"], "tag", _TAG
    )
    assert executed.payload["status"] == "done"
    assert executed.payload["idempotency_key"] == planned.payload["idempotency_key"]
    assert calls == [(remote, _TAG, candidate)]


def test_agent_backend_blocks_publish_before_any_effect(tmp_path, monkeypatch):
    executor, store, candidate, preview, _remote = _host(tmp_path)

    class AgentBackendStub:
        pass

    executor.backend = AgentBackendStub()
    calls: list[str] = []
    monkeypatch.setattr(
        publish_effects, "push_tag", lambda *_args: calls.append("push")
    )

    executor._do_execute_publish(
        _command(preview["preview_digest"]), store.state("RUN"), None, False
    )

    assert calls == []
    blocked = [e for e in store.events("RUN") if e.type == "publish.blocked"]
    assert blocked and blocked[-1].payload["reason"] == "agent_forbidden"
    assert blocked[-1].payload["preview_digest"] == preview["preview_digest"]
    assert blocked[-1].payload["candidate_sha"] == candidate
    assert not any(
        e.type in {"publish.planned", "publish.executed"} for e in store.events("RUN")
    )


def test_kernel_empty_params_uses_current_preview_and_candidate(tmp_path, monkeypatch):
    executor, store, candidate, preview, _remote = _host(tmp_path)
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        publish_effects,
        "push_tag",
        lambda _remote_url, tag, ref: calls.append((tag, ref))
        or {
            "status": "done",
            "object_id": candidate,
            "remote_check": {"exists": True, "matches": True, "object_id": candidate},
        },
    )

    executor._do_execute_publish(_command(), store.state("RUN"), None, False)

    assert calls == [(_TAG, candidate)]
    assert any(e.type == "publish.executed" for e in store.events("RUN"))


def test_decision_candidate_mismatch_cannot_authorize_tag(tmp_path, monkeypatch):
    executor, store, _candidate, preview, _remote = _host(tmp_path)
    store.append(
        "RUN",
        "v0.8",
        "release.decided",
        {
            "candidate_sha": "f" * 40,
            "preview_digest": preview["preview_digest"],
            "action": "release",
            "actor": "human",
        },
    )
    calls: list[str] = []
    monkeypatch.setattr(publish_effects, "push_tag", lambda *_args: calls.append("push"))

    executor._do_execute_publish(
        _command(preview["preview_digest"]), store.state("RUN"), None, False
    )

    assert calls == []
    assert [e.payload["reason"] for e in store.events("RUN") if e.type == "publish.failed"][-1] == "candidate_mismatch"


def test_runtime_requires_authoritative_preview_and_approval(tmp_path, monkeypatch):
    executor, store, _candidate, _preview, _remote = _host(tmp_path)
    empty_executor = Executor(store, executor.repo, "EMPTY")
    calls: list[str] = []
    monkeypatch.setattr(
        publish_effects,
        "push_tag",
        lambda *_args: calls.append("called") or {"status": "done"},
    )

    empty_executor._do_execute_publish(
        _command("sha256:" + "x" * 64), store.state("EMPTY"), None, False
    )

    assert calls == []
    failed_events = [e for e in store.events("EMPTY") if e.type == "publish.failed"]
    assert failed_events, "missing authoritative approval must emit publish.failed"
    assert failed_events[-1].payload["reason"] in {
        "missing_candidate",
        "missing_preview",
        "not_approved",
    }


def test_unbound_release_is_rejected_without_fake_success(tmp_path, monkeypatch):
    executor, store, _candidate, preview, _remote = _host(tmp_path, operation="release")
    calls: list[str] = []
    monkeypatch.setattr(
        publish_effects,
        "create_release",
        lambda *_args: calls.append("called") or {"status": "done"},
    )

    executor._do_execute_publish(
        _command(preview["preview_digest"]), store.state("RUN"), None, False
    )

    assert calls == []
    failed_events = [e for e in store.events("RUN") if e.type == "publish.failed"]
    assert failed_events, "release without a planned tag must fail closed"
    assert failed_events[-1].payload["reason"] == "malformed"
    assert not any(e.type == "publish.planned" for e in store.events("RUN"))


def test_unsupported_later_operation_is_rejected_before_any_effect(tmp_path, monkeypatch):
    executor, store, _candidate, preview, _remote = _host(
        tmp_path, operation_steps=["tag:{feature_tag}", "webhook:main"]
    )
    calls: list[str] = []
    monkeypatch.setattr(publish_effects, "push_tag", lambda *_args: calls.append("push"))

    executor._do_execute_publish(
        _command(preview["preview_digest"]), store.state("RUN"), None, False
    )

    assert calls == []
    assert [e.payload["reason"] for e in store.events("RUN") if e.type == "publish.failed"][-1] == "unknown_operation"


def test_preview_blob_mismatch_blocks_publish(tmp_path, monkeypatch):
    executor, store, _candidate, preview, _remote = _host(tmp_path)
    blob_hash = Path(preview["blob_ref"]).name
    (paths.blobs_dir(executor.store.home) / blob_hash).write_text(
        json.dumps({"tampered": True}), encoding="utf-8"
    )
    calls: list[str] = []
    monkeypatch.setattr(publish_effects, "push_tag", lambda *_args: calls.append("push"))

    executor._do_execute_publish(
        _command(preview["preview_digest"]), store.state("RUN"), None, False
    )

    assert calls == []
    assert [e.payload["reason"] for e in store.events("RUN") if e.type == "publish.failed"][-1] == "preview_blob_mismatch"


def test_existing_planned_mismatch_cannot_suppress_new_command(tmp_path, monkeypatch):
    executor, store, candidate, preview, _remote = _host(tmp_path)
    key = operation_idempotency_key(preview["preview_digest"], "tag", _TAG)
    store.append(
        "RUN",
        "v0.8",
        "publish.planned",
        {
            "operation_kind": "tag",
            "target": _TAG,
            "preview_digest": preview["preview_digest"],
            "idempotency_key": key,
            "candidate_sha": "f" * 40,
            "when": "old",
        },
        command_id="OTHER-COMMAND",
    )
    calls: list[str] = []
    monkeypatch.setattr(publish_effects, "push_tag", lambda *_args: calls.append("push"))

    executor._do_execute_publish(
        _command(preview["preview_digest"]), store.state("RUN"), None, True
    )

    assert calls == []
    assert [e.payload["reason"] for e in store.events("RUN") if e.type == "publish.failed"][-1] == "planned_conflict"


def test_resume_reconciles_same_tag_and_reports_conflict(tmp_path, monkeypatch):
    executor, store, candidate, preview, remote = _host(tmp_path)
    remote_state = {"exists": False, "matches": None, "object_id": None}
    push_calls: list[str] = []
    monkeypatch.setattr(
        publish_effects,
        "read_remote_state",
        lambda *_args, **_kwargs: dict(remote_state),
    )
    monkeypatch.setattr(
        publish_effects,
        "push_tag",
        lambda *_args: push_calls.append("push")
        or {
            "status": "done",
            "remote_check": {"exists": True, "matches": True, "object_id": candidate},
        },
    )

    executor._do_execute_publish(
        _command(preview["preview_digest"]), store.state("RUN"), None, False
    )
    remote_state.update({"exists": True, "matches": True, "object_id": candidate})
    executor._do_execute_publish(
        _command(preview["preview_digest"]), store.state("RUN"), None, True
    )

    executed = [e for e in store.events("RUN") if e.type == "publish.executed"]
    assert executed[-1].payload["status"] == "reconciled_skip"
    assert push_calls == ["push"]

    remote_state.update({"matches": False, "object_id": "f" * 40})
    executor._do_execute_publish(
        _command(preview["preview_digest"]), store.state("RUN"), None, True
    )
    failed = [e for e in store.events("RUN") if e.type == "publish.failed"][-1]
    assert failed.payload["reason"] in {"reconcile_conflict", "conflict"}


def test_remote_object_must_match_even_when_matches_claims_true(tmp_path, monkeypatch):
    executor, store, candidate, preview, _remote = _host(tmp_path)
    monkeypatch.setattr(
        publish_effects,
        "read_remote_state",
        lambda *_args, **_kwargs: {
            "exists": True,
            "matches": True,
            "object_id": "f" * 40,
        },
    )
    calls: list[str] = []
    monkeypatch.setattr(publish_effects, "push_tag", lambda *_args: calls.append("push"))

    executor._do_execute_publish(
        _command(preview["preview_digest"]), store.state("RUN"), None, True
    )

    assert calls == []
    assert [e.payload["reason"] for e in store.events("RUN") if e.type == "publish.failed"][-1] == "reconcile_conflict"
    conflict = [e for e in store.events("RUN") if e.type == "reconcile_conflict"][-1]
    assert conflict.payload["expected"] == {
        "object_id": candidate,
        "candidate_sha": candidate,
        "target": _TAG,
    }
    assert conflict.payload["remote"]["object_id"] == "f" * 40


def test_push_reconciled_skip_requires_confirmed_remote_object(tmp_path, monkeypatch):
    executor, store, candidate, preview, _remote = _host(tmp_path)
    monkeypatch.setattr(
        publish_effects,
        "read_remote_state",
        lambda *_args, **_kwargs: {"exists": False, "matches": None},
    )
    monkeypatch.setattr(
        publish_effects,
        "push_tag",
        lambda *_args: {
            "status": "reconciled_skip",
            "object_id": candidate,
            "remote_check": {"exists": True, "matches": True, "object_id": candidate},
        },
    )

    executor._do_execute_publish(
        _command(preview["preview_digest"]), store.state("RUN"), None, False
    )

    executed = [e for e in store.events("RUN") if e.type == "publish.executed"]
    assert executed[-1].payload["status"] == "reconciled_skip"


def test_dirty_tracked_worktree_cannot_publish_frozen_head(tmp_path, monkeypatch):
    executor, store, _candidate, preview, _remote = _host(tmp_path)
    contract_path = executor.repo / ".tracks" / "projects" / "project.toml"
    contract_path.write_text(
        contract_path.read_text(encoding="utf-8") + "\n# dirty\n",
        encoding="utf-8",
    )
    calls: list[str] = []
    monkeypatch.setattr(publish_effects, "push_tag", lambda *_args: calls.append("push"))

    executor._do_execute_publish(
        _command(preview["preview_digest"]), store.state("RUN"), None, False
    )

    assert calls == []
    failed = [e for e in store.events("RUN") if e.type == "publish.failed"][-1]
    assert failed.payload["reason"] == "candidate_dirty"


def test_preview_blob_filename_hash_is_verified(tmp_path, monkeypatch):
    executor, store, _candidate, preview, _remote = _host(tmp_path)
    blob_name = preview["blob_ref"].rsplit("/", 1)[-1]
    blob_path = paths.blobs_dir(store.home) / blob_name
    blob_path.write_text(
        json.dumps(json.loads(blob_path.read_text(encoding="utf-8")), indent=2),
        encoding="utf-8",
    )
    calls: list[str] = []
    monkeypatch.setattr(publish_effects, "push_tag", lambda *_args: calls.append("push"))

    executor._do_execute_publish(
        _command(preview["preview_digest"]), store.state("RUN"), None, False
    )

    assert calls == []
    failed = [e for e in store.events("RUN") if e.type == "publish.failed"][-1]
    assert failed.payload["reason"] == "preview_blob_mismatch"


# AC-FR0277-02@v0.8: a release-branch merge target enables the true merge
# (allow_merge=True) and the containment confirmation lands the executed
# event with the merge product identity.
def test_release_branch_merge_dispatches_true_merge(tmp_path, monkeypatch):
    executor, store, candidate, preview, remote = _host(
        tmp_path, operation_steps=["merge:releases/v0.8"]
    )
    merge_commit = "f" * 40
    calls: list[tuple] = []

    def fake_merge(remote_url, source_ref, target, *, allow_merge=False):
        calls.append((remote_url, source_ref, target, allow_merge))
        return {
            "status": "done",
            "merge_mode": "release_branch",
            "merge_commit": merge_commit,
            "branch": target,
            "ref": source_ref,
            "remote_check": {
                "exists": True,
                "matches": False,
                "contains_fix": True,
                "object_id": merge_commit,
            },
        }

    monkeypatch.setattr(publish_effects, "push_merge", fake_merge)

    executor._do_execute_publish(
        _command(preview["preview_digest"]), store.state("RUN"), None, False
    )

    assert calls == [(remote, candidate, "releases/v0.8", True)]
    executed = [e for e in store.events("RUN") if e.type == "publish.executed"][-1]
    assert executed.payload["status"] == "done"
    assert executed.payload["merge_mode"] == "release_branch"
    assert executed.payload["merge_commit"] == merge_commit
    assert executed.payload["remote_check"]["contains_fix"] is True


# AC-FR0275-01@v0.8: the trunk merge target stays fast-forward-only
# (allow_merge=False), preserving the existing main semantics.
def test_trunk_merge_target_stays_fast_forward_only(tmp_path, monkeypatch):
    executor, store, candidate, preview, remote = _host(
        tmp_path, operation_steps=["merge:main"]
    )
    calls: list[tuple] = []

    def fake_merge(remote_url, source_ref, target, *, allow_merge=False):
        calls.append((remote_url, source_ref, target, allow_merge))
        return {
            "status": "done",
            "branch": target,
            "ref": source_ref,
            "remote_check": {
                "exists": True,
                "matches": True,
                "object_id": source_ref,
            },
        }

    monkeypatch.setattr(publish_effects, "push_merge", fake_merge)

    executor._do_execute_publish(
        _command(preview["preview_digest"]), store.state("RUN"), None, False
    )

    assert calls == [(remote, candidate, "main", False)]


# AC-FR0277-02@v0.8 / NFR-0144-02: an unresolvable sync merge is a
# reconcile_conflict (blocked), never auto-resolved.
def test_release_branch_merge_conflict_is_reconcile_conflict(tmp_path, monkeypatch):
    executor, store, _candidate, preview, _remote = _host(
        tmp_path, operation_steps=["merge:releases/v0.8"]
    )
    monkeypatch.setattr(
        publish_effects,
        "push_merge",
        lambda *_args, **_kwargs: {
            "status": "conflict",
            "merge_mode": "release_branch",
            "branch": "releases/v0.8",
            "remote_check": {
                "exists": True,
                "matches": False,
                "contains_fix": False,
                "object_id": "a" * 40,
            },
        },
    )

    executor._do_execute_publish(
        _command(preview["preview_digest"]), store.state("RUN"), None, False
    )

    conflict = [e for e in store.events("RUN") if e.type == "reconcile_conflict"]
    assert conflict and conflict[-1].payload["expected"]["target"] == "releases/v0.8"
    failed = [e for e in store.events("RUN") if e.type == "publish.failed"][-1]
    assert failed.payload["reason"] == "reconcile_conflict"
