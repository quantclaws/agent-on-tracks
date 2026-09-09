"""Direct Runtime-owned tag publish acceptance on a local bare remote.

The upstream candidate, preview, and human decision are explicit Arrange
facts. The tests begin at ``execute_publish`` and observe the real effects
boundary; they do not seed publish outcomes or call effects in place of the
Runtime handler.
"""

from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
from pathlib import Path

import pytest
import tomllib

from tracks import paths
from tracks.effects.publish import read_remote_state
from tracks.executor.executor import Executor
from tracks.executor.host_contract import load_host_contract, validate_host_contract
from tracks.executor.m_verify import freeze_candidate
from tracks.executor.release_gate import build_operation_plan, generate_preview
from tracks.kernel.events import Command
from tracks.store import Store

pytestmark = pytest.mark.integration


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _install_push_counter(remote: Path, counter: Path) -> None:
    hook = remote / "hooks" / "post-receive"
    hook.write_text(
        "#!/bin/sh\n"
        "cat >/dev/null\n"
        f"printf x >> {shlex.quote(str(counter))}\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)


def _push_count(counter: Path) -> int:
    return len(counter.read_text(encoding="utf-8")) if counter.exists() else 0


def _write_contract(repo: Path, steps: list[str]) -> Path:
    contract = repo / ".tracks" / "projects" / "project.toml"
    contract.parent.mkdir(parents=True, exist_ok=True)
    encoded_steps = ",\n  ".join(json.dumps(step) for step in steps)
    contract.write_text(
        "\n".join(
            [
                "[host-contract]",
                "version = 1",
                'language = "python"',
                'toolchain = "cpython"',
                'install = "python"',
                "",
                "[host-contract.version_scheme]",
                'feature_tag = "v{minor}.0"',
                "",
                "[host-contract.operations.feature]",
                f"steps = [\n  {encoded_steps}\n]",
                "",
            ]
        ),
        encoding="utf-8",
    )
    loaded = load_host_contract(contract)
    assert loaded.contract_version == 1
    assert validate_host_contract(loaded, repo) == ()
    return contract


def _preview_for(
    store: Store,
    contract: Path,
    candidate: str,
    operation_steps: list[str],
) -> dict:
    raw_contract = tomllib.loads(contract.read_text(encoding="utf-8"))
    facts = {
        "version": "v0.8",
        "minor": "8",
        "n": "0",
        "ulid": "01HLOCALTAG",
        "artifact": "dist/package.whl",
        "feature_tag": "v8.0",
    }
    plan = build_operation_plan(
        raw_contract["host-contract"], "feature", facts
    )
    plan_digest = "sha256:" + hashlib.sha256(
        json.dumps(plan, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    contract_digest = "sha256:" + hashlib.sha256(contract.read_bytes()).hexdigest()
    preview = generate_preview(
        candidate,
        {
            "artifact_digest": "sha256:" + "a" * 64,
            "evidence_digests": {"full_f": "sha256:" + "b" * 64},
            "operation_plan_digest": plan_digest,
            "contract_policy_digest": contract_digest,
        },
        [],
        plan,
    )
    blob_hash = store.write_audit_blob(preview)
    assert blob_hash
    preview["blob_ref"] = f".tracks/runtime/blobs/{blob_hash}"
    assert store.load_payload({"$ref": blob_hash}) == {
        key: value for key, value in preview.items() if key != "blob_ref"
    }
    return preview


def _prepare(
    host_repo: Path,
    trac,
    tmp_path: Path,
    *,
    operation_steps: list[str] | None = None,
    approved: bool = True,
) -> tuple[str, str, str, Path, dict, Path]:
    assert trac("init").returncode == 0
    assert trac("start", "v0.8", stdin="Runtime tag publish boundary").returncode == 0
    steps = operation_steps or ["tag:{feature_tag}"]
    contract = _write_contract(host_repo, steps)
    subprocess.run(
        ["git", "add", "-f", ".tracks/projects/project.toml"],
        cwd=host_repo,
        check=True,
        capture_output=True,
    )
    _git(host_repo, "commit", "-qm", "materialize publish operations contract")
    candidate = _git(host_repo, "rev-parse", "HEAD")
    identity = freeze_candidate(host_repo)
    assert identity.candidate_sha == candidate
    assert identity.clean_tree is True and identity.branch

    remote = tmp_path / "remote.git"
    counter = tmp_path / "push-count"
    subprocess.run(
        ["git", "init", "-q", "--bare", str(remote)],
        check=True,
        capture_output=True,
    )
    _install_push_counter(remote, counter)
    _git(host_repo, "remote", "add", "origin", str(remote))
    _git(host_repo, "push", "-q", "origin", "HEAD:refs/heads/main")

    store = Store(paths.tracks_home(host_repo))
    try:
        run_id = store.active_run()
        assert run_id
        version = store.state(run_id).version or "v0.8"
        preview = _preview_for(store, contract, candidate, steps)
        store.append(run_id, version, "stage.entered", {"stage": "M-VERIFY"})
        store.append(
            run_id,
            version,
            "candidate.frozen",
            {
                "candidate_sha": candidate,
                "clean_tree": True,
                "branch": identity.branch,
                "frozen_at_seq": len(list(store.events(run_id))) + 1,
            },
        )
        store.append(run_id, version, "stage.entered", {"stage": "M-RELEASE"})
        store.append(run_id, version, "release.previewed", dict(preview))
        if approved:
            store.append(
                run_id,
                version,
                "release.decided",
                {
                    "action": "release",
                    "actor": "human",
                    "candidate_sha": candidate,
                    "preview_digest": preview["preview_digest"],
                    "reason": None,
                    "target": None,
                },
            )
        store.append(run_id, version, "stage.entered", {"stage": "M-PUBLISH"})
        return run_id, candidate, steps[0].split(":", 1)[-1].replace("{feature_tag}", "v8.0"), remote, preview, counter
    finally:
        store.close()


def _events(repo: Path, run_id: str) -> list:
    store = Store(paths.tracks_home(repo))
    try:
        return list(store.events(run_id))
    finally:
        store.close()


def _issue(repo: Path, run_id: str, preview_digest: str) -> None:
    store = Store(paths.tracks_home(repo))
    try:
        Executor(store, repo, run_id).issue(
            Command(kind="execute_publish", params={"preview_digest": preview_digest})
        )
    finally:
        store.close()


def _reconcile_existing(
    repo: Path, run_id: str, preview_digest: str, planned: dict
) -> None:
    command_id = "CMD-PUBLISH-RECOVER"
    store = Store(paths.tracks_home(repo))
    try:
        store.append(
            run_id,
            store.state(run_id).version or "v0.8",
            "command.issued",
            {
                "command": {
                    "kind": "execute_publish",
                    "params": {"preview_digest": preview_digest},
                    "command_id": command_id,
                }
            },
            command_id=command_id,
        )
        store.append(
            run_id,
            store.state(run_id).version or "v0.8",
            "publish.planned",
            planned,
            command_id=command_id,
        )
        command = Command(
            kind="execute_publish",
            params={"preview_digest": preview_digest},
            command_id=command_id,
        )
        Executor(store, repo, run_id)._execute(
            command, store.state(run_id), None, reconcile=True
        )
    finally:
        store.close()


def _tag_sha(remote: Path, tag: str, expected: str | None = None) -> str | None:
    state = read_remote_state(str(remote), "tag", tag, expected_object=expected)
    return state.get("object_id")


def _key(preview_digest: str, kind: str, target: str) -> str:
    raw = {"preview_digest": preview_digest, "kind": kind, "target": target}
    return "sha256:" + hashlib.sha256(
        json.dumps(raw, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


# AC-FR0275-01: approved Runtime command plans and performs a real tag effect.
def test_runtime_publish_tag_wal_then_real_remote_tag(host_repo, trac, tmp_path, monkeypatch):
    run_id, candidate, tag, remote, preview, counter = _prepare(host_repo, trac, tmp_path)
    monkeypatch.chdir(host_repo)
    assert _tag_sha(remote, tag) is None

    _issue(host_repo, run_id, preview["preview_digest"])

    events = _events(host_repo, run_id)
    issued = [event for event in events if event.type == "command.issued"][-1]
    planned = [event for event in events if event.type == "publish.planned"][-1]
    executed = [event for event in events if event.type == "publish.executed"][-1]
    assert issued.payload["command"]["params"] == {"preview_digest": preview["preview_digest"]}
    assert issued.seq < planned.seq < executed.seq
    assert planned.payload == {
        "operation_kind": "tag",
        "target": tag,
        "preview_digest": preview["preview_digest"],
        "candidate_sha": candidate,
        "idempotency_key": _key(preview["preview_digest"], "tag", tag),
        "when": None,
    }
    assert executed.payload["status"] == "done"
    assert executed.payload["candidate_sha"] == candidate
    assert executed.payload["idempotency_key"] == planned.payload["idempotency_key"]
    assert executed.payload["remote_check"]["object_id"] == candidate
    assert _tag_sha(remote, tag, expected=candidate) == candidate
    assert _push_count(counter) == 2


# AC-FR0275-02: existing planned + completed remote effect reconciles without a second push.
def test_runtime_publish_reconcile_existing_tag_skips_effect(host_repo, trac, tmp_path, monkeypatch):
    run_id, candidate, tag, remote, preview, counter = _prepare(host_repo, trac, tmp_path)
    monkeypatch.chdir(host_repo)
    _git(host_repo, "tag", tag, candidate)
    _git(host_repo, "push", "-q", "origin", f"refs/tags/{tag}:refs/tags/{tag}")
    before_pushes = _push_count(counter)
    assert _tag_sha(remote, tag, expected=candidate) == candidate

    planned = {
        "operation_kind": "tag",
        "target": tag,
        "preview_digest": preview["preview_digest"],
        "candidate_sha": candidate,
        "idempotency_key": _key(preview["preview_digest"], "tag", tag),
        "when": None,
    }
    _reconcile_existing(host_repo, run_id, preview["preview_digest"], planned)

    events = _events(host_repo, run_id)
    assert len([event for event in events if event.type == "publish.planned"]) == 1
    skipped = [event for event in events if event.type == "publish.executed"]
    assert len(skipped) == 1 and skipped[0].payload["status"] == "reconciled_skip"
    assert skipped[0].payload["idempotency_key"] == _key(preview["preview_digest"], "tag", tag)
    assert _push_count(counter) == before_pushes
    assert _tag_sha(remote, tag, expected=candidate) == candidate


# AC-FR0275-04: a remote tag pointing elsewhere is a conflict and is preserved.
def test_runtime_publish_remote_conflict_does_not_overwrite(host_repo, trac, tmp_path, monkeypatch):
    run_id, candidate, tag, remote, preview, counter = _prepare(host_repo, trac, tmp_path)
    monkeypatch.chdir(host_repo)
    branch = _git(host_repo, "branch", "--show-current")
    _git(host_repo, "switch", "-q", "-c", "divergent")
    (host_repo / "divergent.txt").write_text("remote winner\n", encoding="utf-8")
    _git(host_repo, "add", "divergent.txt")
    _git(host_repo, "commit", "-qm", "divergent remote tag")
    remote_sha = _git(host_repo, "rev-parse", "HEAD")
    _git(host_repo, "switch", "-q", branch)
    _git(host_repo, "push", "-q", "origin", f"{remote_sha}:refs/tags/{tag}")
    before_pushes = _push_count(counter)

    _issue(host_repo, run_id, preview["preview_digest"])

    events = _events(host_repo, run_id)
    conflicts = [event for event in events if event.type == "reconcile_conflict"]
    assert conflicts
    assert conflicts[-1].payload["idempotency_key"] == _key(preview["preview_digest"], "tag", tag)
    assert conflicts[-1].payload["remote"]["object_id"] == remote_sha
    assert not any(
        event.type == "publish.executed" and event.payload.get("status") == "done"
        for event in events
    )
    assert _push_count(counter) == before_pushes
    assert _tag_sha(remote, tag) == remote_sha
    assert candidate != remote_sha


@pytest.mark.parametrize(
    "case,approved,declared_steps",
    [
        ("missing-approval", False, ["tag:{feature_tag}"]),
        ("unknown-operation", True, ["webhook:foo"]),
    ],
)
# AC-FR0275-03/04: unapproved or undeclared operations cannot affect the remote.
def test_runtime_publish_guards_have_no_remote_side_effect(
    host_repo, trac, tmp_path, case, approved, declared_steps, monkeypatch
):
    run_id, _candidate, tag, remote, preview, counter = _prepare(
        host_repo,
        trac,
        tmp_path,
        operation_steps=declared_steps,
        approved=approved,
    )
    monkeypatch.chdir(host_repo)
    _issue(host_repo, run_id, preview["preview_digest"])

    events = _events(host_repo, run_id)
    if case == "missing-approval":
        assert not any(
            event.type in {"publish.planned", "publish.executed"}
            for event in events
        ), "an unapproved command must not enter the publish effect boundary"
    else:
        failed = [event for event in events if event.type == "publish.failed"]
        assert failed, f"{case} must fail closed"
        assert failed[-1].payload["reason"] == "unknown_operation"
    assert not any(event.type == "publish.executed" for event in events)
    assert _tag_sha(remote, tag) is None
    assert _push_count(counter) == 1
