"""Integration: candidate freeze and binding (FR-0267, IF-VERIFY-001).

b93 §8.1 bootstrap contract: the CLI halves are driven by the shared walker
(``walk_to_m_impl_parked``, parks at M-IMPL/DIAGNOSE/awaiting=escalation) —
bare ``trac run`` bootstrap is forbidden (v0.8 suite-wide defect). The
module-level halves assert the delivered IF-VERIFY-001 contract faces
(interfaces §1d candidate freeze/binding). Event-level assertions on the
release chain stay legal Red: the batch-1 machine registers stages only
through M-IMPL, so the M-VERIFY producers (candidate.frozen wiring) are
later runtime tasks.
"""

from __future__ import annotations

import subprocess

import pytest

from tests.e2e.helpers import walk_to_m_impl_parked
from tracks.executor import m_verify
from tracks.executor.m_verify import collect_binding_violations, freeze_candidate

pytestmark = pytest.mark.integration

_CANDIDATE = "a" * 40
_FOREIGN = "b" * 40


def _git(repo, *args: str) -> str:
    out = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)
    return out.stdout.strip()


def _event(kind: str, sha) -> dict:
    event = {"kind": kind}
    if sha is not None:
        event["candidate_sha"] = sha
    return event


# AC-FR0267-01@v0.8 TRACKS-TRACE candidate frozen with full SHA and clean tree binding
def test_clean_tree_freezes_candidate(host_repo, trac, event_log):
    # Module half (IF-VERIFY-001 §1d): a clean tree freezes the bound
    # CandidateIdentity — full HEAD SHA, clean flag, current branch — and
    # freezing is idempotent for the same HEAD.
    identity = freeze_candidate(host_repo)
    assert isinstance(identity, m_verify.CandidateIdentity), (
        f"freeze_candidate must return CandidateIdentity, got {type(identity).__name__}"
    )
    head = _git(host_repo, "rev-parse", "HEAD")
    assert len(head) == 40
    assert identity.candidate_sha == head, (
        f"clean tree must freeze the full HEAD SHA {head!r}, got {identity.candidate_sha!r}"
    )
    assert identity.clean_tree is True
    assert identity.branch == _git(host_repo, "rev-parse", "--abbrev-ref", "HEAD")
    again = freeze_candidate(host_repo)
    assert again.candidate_sha == identity.candidate_sha, (
        "freeze_candidate must be idempotent for the same HEAD (不重冻)"
    )

    # Binding scan half (§1d 全链事件绑定校验): a fully bound chain yields no
    # violations; foreign/unbound evidence is flagged.
    bound_chain = [_event(k, _CANDIDATE) for k in ("candidate.frozen", "ci.run_observed")]
    assert collect_binding_violations(bound_chain, _CANDIDATE) == []
    unbound_chain = [_event("candidate.frozen", _CANDIDATE), _event("prism.verdict", None)]
    violations = collect_binding_violations(unbound_chain, _CANDIDATE)
    assert len(violations) == 1 and "prism.verdict" in str(violations[0])

    # CLI half: the walked run must bind candidate.frozen to the host HEAD.
    # Legal Red: the M-VERIFY freeze producer is not wired on this baseline,
    # so the event is absent (never a scaffold crash).
    walk_to_m_impl_parked(trac)
    # Anchor AFTER the walk: the walker journey itself commits (init
    # scaffold, stage seals, agent commits, phase0 seeds, shield
    # checkpoint), so the park-chain freeze correctly binds the
    # park-time HEAD (interfaces 1d) -- never the pre-walk HEAD.
    head = _git(host_repo, "rev-parse", "HEAD")
    events = event_log()
    frozen = [e for e in events if e["type"] == "candidate.frozen"]
    assert frozen, "candidate.frozen must appear after clean-tree freeze"
    payload = frozen[0]["payload"]
    assert payload["candidate_sha"] == head
    assert payload["clean_tree"] is True
    downstream = [e for e in events if "candidate_sha" in e["payload"]]
    for ev in downstream:
        assert ev["payload"]["candidate_sha"] == head


# AC-FR0267-02@v0.8 TRACKS-TRACE dirty tree does not freeze candidate and needs_attention
def test_dirty_tree_needs_attention(host_repo, trac, event_log):
    # Module half (§1d 不得返回身份): a dirty tree — modified, then staged —
    # must not yield an identity; any rejection failure is conforming, a
    # returned identity is not.
    (host_repo / "README.md").write_text("dirty\n", encoding="utf-8")
    try:
        decision = freeze_candidate(host_repo)
    except Exception:
        pass
    else:
        raise AssertionError(
            f"dirty tree must not return an identity, got {decision!r}"
        )
    subprocess.run(["git", "add", "README.md"], cwd=host_repo, check=True)
    try:
        decision = freeze_candidate(host_repo)
    except Exception:
        pass
    else:
        raise AssertionError(
            f"staged dirty tree must not return an identity, got {decision!r}"
        )

    # CLI half: the walked run parks without ever freezing a dirty candidate;
    # the fail-closed outlet is attention.required(reason=dirty_tree).
    walk_to_m_impl_parked(trac)
    events = event_log()
    frozen = [e for e in events if e["type"] == "candidate.frozen"]
    assert not any(e["payload"].get("clean_tree") is True for e in frozen), (
        "no successful clean-tree freeze may exist for a dirty tree"
    )
    attention = [e for e in events if e["type"] == "attention.required"]
    assert any(a["payload"].get("reason") == "dirty_tree" for a in attention), (
        "dirty tree must land attention.required(reason=dirty_tree)"
    )
    assert all(a["payload"].get("next") for a in attention)


# AC-FR0267-03@v0.8 TRACKS-TRACE drift marks stale and replay does not refreeze
def test_drift_marks_stale_no_refreeze(host_repo, trac, event_log):
    # Module half: the binding scan flags foreign-SHA drift in a release
    # chain (the identity drift the stale mark responds to).
    drifted = [
        _event("candidate.frozen", _CANDIDATE),
        _event("evidence.reused", _FOREIGN),
    ]
    violations = collect_binding_violations(drifted, _CANDIDATE)
    assert len(violations) == 1 and "evidence.reused" in str(violations[0])

    walk_to_m_impl_parked(trac)
    frozen_before = [e for e in event_log() if e["type"] == "candidate.frozen"]
    # Produce a drift commit after the freeze context
    (host_repo / "drift.txt").write_text("drift\n", encoding="utf-8")
    subprocess.run(["git", "add", "drift.txt"], cwd=host_repo, check=True)
    subprocess.run(["git", "commit", "-m", "drift"], cwd=host_repo, check=True)
    for _ in range(3):
        trac("run")
    events_after = event_log()
    # Drift must produce candidate.stale with the contracted reason
    stale = [e for e in events_after if e["type"] == "candidate.stale"]
    assert stale, "candidate.stale must appear after drift"
    assert any(s["payload"].get("reason") in ("candidate_drift", "head_moved") for s in stale)
    # Replay must rebuild identity without duplicating freeze
    frozen_after = [e for e in events_after if e["type"] == "candidate.frozen"]
    assert len(frozen_after) == len(frozen_before), (
        "replay must not re-freeze (幂等不重冻)"
    )
    replay = trac("replay")
    assert replay.returncode == 0 or "candidate.frozen" in replay.stdout
