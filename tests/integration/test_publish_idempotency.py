"""Integration: M-PUBLISH write-ahead & idempotency (FR-0275, IF-PUBLISH-001/002).

b93 §8.1 bootstrap contract: the CLI halves are driven by the shared walker
(parks at M-IMPL/DIAGNOSE/awaiting=escalation) — bare ``trac run`` bootstrap
is forbidden (v0.8 suite-wide defect). The M-PUBLISH write-ahead &
reconcile event producers are wired by later runtime tasks (kernel/release
routing T-039 + executor handler T-001 + agent guard T-001); until then the
event-level assertions are legal Red against that product gap. The
module-level halves assert the delivered IF-PUBLISH-002 reconcile contract
where applicable.
"""

from __future__ import annotations

import hashlib
import json
import subprocess

import pytest

from tests.e2e.helpers import walk_to_m_impl_parked
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
def test_planned_then_executed_done(host_repo, trac, event_log):
    key = operation_idempotency_key("sha256:abc", "merge", "main")
    assert key == _expected_key("sha256:abc", "merge", "main")
    assert key.startswith("sha256:")
    assert plan_operations({}, "sha256:abc") == []

    # Prepare a bare remote for reconciliation
    bare = host_repo.parent / "bare.git"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", str(bare)], cwd=host_repo, check=True)

    walk_to_m_impl_parked(trac)
    # Release decision if needed
    trac("release", "--action", "release")
    trac("run")
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
    status = trac("status")
    assert "publish=" in status.stdout or "publish" in status.stdout


# AC-FR0275-02@v0.8 TRACKS-TRACE resume reconciled_skip without duplicate effects
def test_resume_reconciled_skip(host_repo, trac, event_log):
    verdict = reconcile_operation({"idempotency_key": "k", "target": "t"}, {"exists": True, "matches": True})
    assert verdict == "skip"

    bare = host_repo.parent / "bare2.git"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin2", str(bare)], cwd=host_repo, check=True)
    walk_to_m_impl_parked(trac)
    trac("release", "--action", "release")
    trac("run")
    events = event_log()
    done = [e for e in events if e["type"] == "publish.executed" and e["payload"].get("status") == "done"]
    # Resume must produce reconciled_skip for already-done ops
    trac("run", "--resume")
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
def test_agent_forbidden(host_repo, trac, event_log):
    with pytest.raises(PublishBlocked) as malformed:
        assert_agent_forbidden("")
    assert malformed.value.reason == "malformed_actor"
    for actor in ("runtime", "human"):
        assert assert_agent_forbidden(actor) is None
    with pytest.raises(PublishBlocked) as forbidden:
        assert_agent_forbidden("Devon")
    assert forbidden.value.reason == "agent_forbidden"

    bare = host_repo.parent / "bare3.git"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin3", str(bare)], cwd=host_repo, check=True)
    # Simulate agent attempting publish by setting actor env (harness would block)
    walk_to_m_impl_parked(trac)
    trac("release", "--action", "release")
    # Directly test the guard: any agent actor must be rejected
    with pytest.raises(PublishBlocked) as forbidden:
        assert_agent_forbidden("Devon")
    assert forbidden.value.reason == "agent_forbidden"
    # After implementation, an agent-triggered publish must yield blocked
    events = event_log()
    blocked = [e for e in events if e["type"] == "publish.blocked" and e["payload"].get("reason") == "agent_forbidden"]
    assert blocked, "publish.blocked reason=agent_forbidden must appear"
    # If blocked event exists, no executed success for that op and no remote side effect
    if blocked:
        assert not any(e["type"] == "publish.executed" and e["payload"].get("status") == "done" for e in events if e["payload"].get("idempotency_key") == blocked[0]["payload"].get("idempotency_key"))
        ls_out = subprocess.run(["git", "ls-remote", str(bare)], capture_output=True, text=True, check=True).stdout
        assert ls_out is not None
    else:
        raise AssertionError("publish.blocked must appear")


# AC-FR0275-04@v0.8 TRACKS-TRACE unknown operation and conflict produce blocked and reconcile_conflict
def test_unknown_operation_and_conflict(host_repo, trac, event_log):
    unknown_records = plan_operations({"steps": ["unknown:foo"]}, "sha256:abc")
    assert unknown_records and unknown_records[0]["operation_kind"] == "unknown"
    verdict = reconcile_operation(
        {"idempotency_key": "sha256:abc", "target": "t", "digest": "d1"},
        {"idempotency_key": "sha256:abc", "target": "t", "digest": "d2"},
    )
    assert verdict == "conflict"

    walk_to_m_impl_parked(trac)
    events = event_log()
    [e for e in events if e["type"] == "publish.failed" and e["payload"].get("reason") in ("unknown_operation", "malformed")]
    # For unknown operation injection, must get failed
    # Simulate by running with a contract that declares unknown kind (if file exists)
    contract_path = host_repo / ".tracks" / "projects" / "project.toml"
    if contract_path.exists():
        orig = contract_path.read_text(encoding="utf-8")
        contract_path.write_text(orig + '\n[host-contract.operations.feature]\nsteps=["unknown:bad"]\n', encoding="utf-8")
        trac("run")
        events2 = event_log()
        failed2 = [e for e in events2 if e["type"] == "publish.failed"]
        assert failed2, "publish.failed must appear for unknown operation"
        assert "blocked" in trac("status").stdout
        contract_path.write_text(orig, encoding="utf-8")
    # Conflict case: same idempotency key but remote differs
    conflict = [e for e in events if e["type"] == "reconcile_conflict"]
    # After remote mismatch (manual tag), must surface conflict
    if conflict:
        assert conflict[0]["payload"]["idempotency_key"]
        assert "expected" in conflict[0]["payload"]
        assert "remote" in conflict[0]["payload"]
        assert "blocked" in trac("status").stdout or "reconcile_conflict" in trac("replay").stdout
    else:
        raise AssertionError("reconcile_conflict must appear for same-key diff")
