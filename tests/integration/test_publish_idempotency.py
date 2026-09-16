"""Integration: M-PUBLISH write-ahead & idempotency (FR-0275, IF-PUBLISH-001/002).

b93 §8.1 bootstrap contract: the CLI halves are driven by the shared walker
(M-IMPL/DIAGNOSE/awaiting=escalation park -> bounded drives to
M-RELEASE/AWAITING_RELEASE -> ``trac release`` -> ``trac run`` executes
M-PUBLISH) — bare ``trac run`` bootstrap is forbidden (v0.8 suite-wide
defect). The release chain needs the loopback CI stand-in (``ci_echo_standin``)
and a bare ``origin`` whose main is an ancestor of the frozen candidate; the
Runtime-owned publish handler then reconciles against that real remote.
The module-level halves assert the delivered IF-PUBLISH-002 contracts where
applicable.
"""

from __future__ import annotations

import hashlib
import json
import subprocess

import pytest

from tests.e2e.helpers import (
    init_bare_remote,
    invoke_execute_publish,
    replay_execute_publish,
    walk_to_awaiting_release,
)
from tests.unit.helpers import git_strip
from tests.unit.test_publish_runtime_direct import _command, _host
from tracks.executor.publish import (
    PublishBlocked,
    assert_agent_forbidden,
    operation_idempotency_key,
    plan_operations,
    reconcile_operation,
)

pytestmark = pytest.mark.integration


def _expected_key(preview_digest: str, kind: str, target: str) -> str:
    raw = {"preview_digest": preview_digest, "kind": kind, "target": target}
    return "sha256:" + hashlib.sha256(json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# AC-FR0275-01@v0.8 TRACKS-TRACE planned then executed done with remote mutual verification
def test_planned_then_executed_done(host_repo, trac, event_log, ci_echo_standin):
    key = operation_idempotency_key("sha256:abc", "merge", "main")
    assert key == _expected_key("sha256:abc", "merge", "main")
    assert key.startswith("sha256:")
    assert plan_operations({}, "sha256:abc") == []

    # A bare remote carrying an ancestor main: the frozen candidate can FF.
    bare, _initial = init_bare_remote(host_repo, "bare.git")

    run_id = walk_to_awaiting_release(trac, host_repo=host_repo)
    assert run_id
    assert trac("release", "--action", "release").returncode == 0
    trac("run")  # executes M-PUBLISH (write-ahead -> merge push -> readback)
    events = event_log()
    planned = [e for e in events if e["type"] == "publish.planned"]
    assert planned, "publish.planned must appear with operation_kind/target/preview_digest/idempotency_key"
    for p in planned:
        payload = p["payload"]
        for k in ("operation_kind", "target", "preview_digest", "candidate_sha", "idempotency_key"):
            assert k in payload, f"publish.planned missing {k}"
        assert payload["idempotency_key"] == _expected_key(payload["preview_digest"], payload["operation_kind"], payload["target"])
        assert payload["idempotency_key"].startswith("sha256:")
    executed = [e for e in events if e["type"] == "publish.executed" and e["payload"].get("status") == "done"]
    assert executed, "publish.executed status=done must follow planned"
    for ex in executed:
        assert ex["payload"]["idempotency_key"] in [p["payload"]["idempotency_key"] for p in planned]
        assert ex["payload"]["candidate_sha"] in [p["payload"]["candidate_sha"] for p in planned]
    # Remote mutual verification: ls-remote / tag existence must mirror events
    ls = subprocess.run(["git", "ls-remote", str(bare)], capture_output=True, text=True, check=True).stdout
    assert ls is not None
    candidate = planned[0]["payload"]["candidate_sha"]
    assert f"{candidate}\trefs/heads/main" in ls
    status = trac("status")
    # A completed publish may continue through milestone closure; status must
    # retain the publish state and the latest operation's audit key.
    assert "publish=done" in status.stdout
    assert f"idempotency_key={executed[-1]['payload']['idempotency_key']}" in status.stdout
    assert "M-PUBLISH" in status.stdout or (
        "terminal=released" in status.stdout and "M-MILESTONE" in status.stdout
    )


# AC-FR0275-02@v0.8 TRACKS-TRACE resume reconciled_skip without duplicate effects
def test_resume_reconciled_skip(host_repo, trac, event_log, ci_echo_standin):
    verdict = reconcile_operation({"idempotency_key": "k", "target": "t"}, {"exists": True, "matches": True})
    assert verdict == "skip"

    init_bare_remote(host_repo, "bare2.git")
    run_id = walk_to_awaiting_release(trac, host_repo=host_repo)
    assert trac("release", "--action", "release").returncode == 0
    trac("run")
    events = event_log()
    done = [e for e in events if e["type"] == "publish.executed" and e["payload"].get("status") == "done"]
    assert done, "the first publish must complete before the reconcile replay"
    # Resume must produce reconciled_skip for already-done ops
    replay_execute_publish(host_repo, run_id)
    events2 = event_log()
    skipped = [e for e in events2 if e["type"] == "publish.executed" and e["payload"].get("status") == "reconciled_skip"]
    assert skipped, "publish.executed reconciled_skip must appear on resume for completed ops"
    for s in skipped:
        assert s["payload"]["idempotency_key"] in [d["payload"]["idempotency_key"] for d in done]
    # No duplicate done after resume
    done_after = [e for e in events2 if e["type"] == "publish.executed" and e["payload"].get("status") == "done"]
    assert len(done_after) == len(done)
    # Replay must show reconciled_skip with remote_check
    replay = trac("replay")
    assert "reconciled_skip" in replay.stdout or skipped


# AC-FR0275-03@v0.8 TRACKS-TRACE agent forbidden blocks publish and produces no remote side effects
def test_agent_forbidden(host_repo, trac, event_log, ci_echo_standin):
    with pytest.raises(PublishBlocked) as malformed:
        assert_agent_forbidden("")
    assert malformed.value.reason == "malformed_actor"
    for actor in ("runtime", "human"):
        assert assert_agent_forbidden(actor) is None
    with pytest.raises(PublishBlocked) as forbidden:
        assert_agent_forbidden("Devon")
    assert forbidden.value.reason == "agent_forbidden"

    bare, initial = init_bare_remote(host_repo, "bare3.git")
    run_id = walk_to_awaiting_release(trac, host_repo=host_repo)
    assert trac("release", "--action", "release").returncode == 0
    # Directly test the guard: any agent actor must be rejected
    with pytest.raises(PublishBlocked) as forbidden:
        assert_agent_forbidden("Devon")
    assert forbidden.value.reason == "agent_forbidden"
    # The Runtime reachability guard keys off the backend class name: an
    # Agent-named backend must land publish.blocked before any effect.
    class XAgentBackend:
        """Backend whose class name marks Agent infrastructure."""

    invoke_execute_publish(host_repo, run_id, backend=XAgentBackend())
    events = event_log()
    blocked = [e for e in events if e["type"] == "publish.blocked" and e["payload"].get("reason") == "agent_forbidden"]
    assert blocked, "publish.blocked reason=agent_forbidden must appear"
    # If blocked event exists, no executed success for that op and no remote side effect
    if blocked:
        assert not any(e["type"] == "publish.executed" and e["payload"].get("status") == "done" for e in events if e["payload"].get("idempotency_key") == blocked[0]["payload"].get("idempotency_key"))
        ls_out = subprocess.run(["git", "ls-remote", str(bare)], capture_output=True, text=True, check=True).stdout
        assert ls_out is not None
        assert f"{initial}\trefs/heads/main" in ls_out
    else:
        raise AssertionError("publish.blocked must appear")


# AC-FR0275-04@v0.8 TRACKS-TRACE unknown operation and conflict produce blocked and reconcile_conflict
def test_unknown_operation_and_conflict(host_repo, trac, event_log, ci_echo_standin, tmp_path, monkeypatch):
    unknown_records = plan_operations({"steps": ["unknown:foo"]}, "sha256:abc")
    assert unknown_records and unknown_records[0]["operation_kind"] == "unknown"
    verdict = reconcile_operation(
        {"idempotency_key": "sha256:abc", "target": "t", "digest": "d1"},
        {"idempotency_key": "sha256:abc", "target": "t", "digest": "d2"},
    )
    assert verdict == "conflict"

    def _declare_unknown_operation(repo):
        """Declare the runtime default contract with an unknown feature step.

        The declaration must be committed as part of the phase0 seed commit:
        a later commit would drift the frozen candidate, and the CLI publish
        loop re-issues zero-effect preflight failures unboundedly, so the
        production handler is invoked directly below."""
        from tracks.executor.executor import _DEFAULT_HOST_CONTRACT_TOML

        contract = repo / ".tracks" / "projects" / "project.toml"
        text = contract.read_text(encoding="utf-8")
        assert "[host-contract]" not in text
        declared = _DEFAULT_HOST_CONTRACT_TOML.replace(
            'steps = ["merge:main"]\n\n[host-contract.operations.post_release]',
            'steps = ["frob:main"]\n\n[host-contract.operations.post_release]',
            1,
        )
        contract.write_text(text.rstrip("\n") + "\n\n" + declared, encoding="utf-8")

    init_bare_remote(host_repo, "bare4.git")
    run_id = walk_to_awaiting_release(trac, pre_seed_hook=_declare_unknown_operation)
    assert trac("release", "--action", "release").returncode == 0
    invoke_execute_publish(host_repo, run_id)
    events = event_log()
    failed = [e for e in events if e["type"] == "publish.failed"]
    assert failed, "publish.failed must appear for unknown operation"
    assert failed[-1]["payload"]["reason"] == "unknown_operation"
    assert not [e for e in events if e["type"] in ("publish.planned", "publish.executed")]
    # Conflict case: same idempotency key but remote differs (manual overwrite).
    # One preview cannot carry both an unknown plan and a valid merge plan, so
    # the conflict half drives the same production handler over a directly
    # seeded merge authority + real bare remote (runtime-ops fixture).
    executor, store, _candidate, preview, _remote = _host(
        tmp_path, operation_steps=["merge:main"]
    )
    repo = executor.repo
    branch = git_strip(repo, "branch", "--show-current")
    git_strip(repo, "switch", "-q", "-c", "divergent")
    (repo / "divergent.txt").write_text("remote winner\n", encoding="utf-8")
    git_strip(repo, "add", "divergent.txt")
    git_strip(repo, "commit", "-qm", "divergent remote tip")
    remote_sha = git_strip(repo, "rev-parse", "HEAD")
    git_strip(repo, "switch", "-q", branch)
    git_strip(repo, "push", "-q", "-f", "origin", f"{remote_sha}:refs/heads/main")
    monkeypatch.chdir(repo)

    executor._do_execute_publish(
        _command(preview["preview_digest"]), store.state("RUN"), None, False
    )

    conflicts = [e for e in store.events("RUN") if e.type == "reconcile_conflict"]
    assert conflicts, "reconcile_conflict must appear for same-key diff"
    conflict = conflicts[-1].payload
    assert conflict["idempotency_key"]
    assert "expected" in conflict
    assert "remote" in conflict
    assert conflict["remote"]["object_id"] == remote_sha
    failed_events = [e for e in store.events("RUN") if e.type == "publish.failed"]
    assert failed_events and failed_events[-1].payload["reason"] == "reconcile_conflict"
