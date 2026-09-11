"""Integration: escape gate (FR-0287, IF-ESCAPE-001/002).

b93 §8.1 anchor contract (authoritative design: b93-prism-design-r3 PASS):
- bootstrap via the shared walker only — parks at M-IMPL/DIAGNOSE/awaiting=
  escalation, the single batch-1 reachable legal escape source (no bare
  ``trac run``);
- return targets are reachable upstream stages (``--to M-TEST``); any
  ``--to M-VERIFY`` anchor is forward under the batch-1 machine and must be
  rejected fail-closed;
- A3/A4 premises are seeded through tests/integration/escape_gate_seed.py
  (b93 B2: batch-1 has no M-VERIFY..M-MILESTONE producers);
- A4 is split per b93: already_executed report + ``--confirm`` gate stay in
  this anchor; the reconciled_skip half moved to the T-021/T-039-era anchor;
- pointer-migration completion and real quiesce interception are registered
  batch-2 anchors (b93 B4 / §1.0.14.1) at the bottom of this file.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import time

import pytest

from tests.e2e.helpers import (
    M_IMPL_PARK_SIMULATE,
    _seed_phase0_premises,
    walk_to_await_human,
    walk_to_m_impl_parked,
)
from tests.integration.escape_gate_seed import (
    seed_escape_downstream_evidence,
    seed_escape_publish_done,
)
from tracks.executor.escape import quarantine_late_outcome

pytestmark = pytest.mark.integration


def _barrier(events: list[dict]) -> dict:
    """The escape.barrier_established event (fails closed when absent)."""
    found = [e for e in events if e["type"] == "escape.barrier_established"]
    assert found, "escape.barrier_established must appear"
    return found[-1]


def _cutover(events: list[dict]) -> int:
    return _barrier(events)["payload"]["cutover_seq"]


# AC-FR0287-01@v0.8 TRACKS-TRACE universal return moves pointer with advisory separation
def test_universal_return_moves_pointer(host_repo, trac, event_log):
    run_id = walk_to_m_impl_parked(trac)
    before = event_log(run_id)
    # Fail-closed gate: a forward/unregistered target is rejected with zero
    # events and an unmoved pointer (SM-05.7a/C-03; M-VERIFY is downstream of
    # the M-IMPL source under the batch-1 machine).
    r_fwd = trac("return", "--to", "M-VERIFY", "--reason", "forward probe")
    assert r_fwd.returncode != 0
    assert event_log(run_id) == before, "rejected return must append zero events"
    # Universal escape: M-IMPL/DIAGNOSE escalation -> upstream M-TEST accepted.
    r = trac("return", "--to", "M-TEST", "--reason", "human escape")
    assert r.returncode == 0, r.stdout + r.stderr
    events = event_log(run_id)
    barrier = _barrier(events)
    human = [e for e in events if e["type"] == "human.return"]
    assert human, "human.return must appear"
    payload = human[-1]["payload"]
    # b93 dual-track payload: actor/from/to serve E-01 rendering, to_stage is
    # the kernel projection key; both tracks must agree on the target.
    assert payload["actor"] == "Human"
    assert payload["from"] == "M-IMPL"
    assert payload["to"] == "M-TEST"
    assert payload["to_stage"] == "M-TEST"
    assert payload["reason"] == "human escape"
    # Contract order: the barrier is the first escape event, before human.return.
    assert barrier["seq"] < human[-1]["seq"]
    # b93 Q3.3: staling is bucket-existence-bound -- ONLY evidence buckets
    # that actually exist get evidence.staled. Since the v0.8 repair-round
    # chain an M-IMPL park legitimately carries post-M-TEST buckets (the
    # walk went through M-VERIFY: candidate.frozen, release.previewed), the
    # invariant is "every staled bucket exists on the stream", not "no
    # buckets exist" (the pre-v0.8-chain environment assumption).
    staled = [e for e in events if e["type"] == "evidence.staled"]
    existing_types = {e["type"] for e in events}
    for entry in staled:
        assert entry["payload"]["evidence_type"] in existing_types, (
            "evidence.staled must reference an actually-existing bucket "
            f"(got {entry['payload']['evidence_type']})"
        )
        # Escape status five fragments (interfaces §2b; spec E-01 rendering).
        status = trac("status").stdout
        assert "human_return=Human→M-TEST" in status
        assert f"evidence.staled={len(staled)}" in status
    assert "barrier=established" in status
    assert "late_outcome=quarantined" in status
    assert "frozen_tests=unfrozen" in status


# AC-FR0287-02@v0.8 TRACKS-TRACE escape barrier quarantines late outcomes
def test_escape_barrier_quarantines_late_outcomes(host_repo, trac, event_log):
    run_id = walk_to_m_impl_parked(trac)
    assert trac("return", "--to", "M-TEST", "--reason", "barrier quarantine").returncode == 0
    events = event_log(run_id)
    barrier = _barrier(events)
    cutover = barrier["payload"]["cutover_seq"]
    # b93 B3: cutover_seq is drawn from the persistent store seq space — it
    # equals the max pre-barrier seq and the barrier itself occupies cutover+1.
    assert isinstance(cutover, int)
    pre_seqs = [e["seq"] for e in events if e["seq"] < barrier["seq"]]
    assert pre_seqs, "the parked run must have pre-barrier events"
    assert cutover == max(pre_seqs)
    assert barrier["seq"] == cutover + 1
    # Batch-1 payload contract: quiesced_dispatches is a list (real quiesce
    # consumption is the batch-2 anchor below).
    assert isinstance(barrier["payload"].get("quiesced_dispatches"), list)
    # Late-outcome discrimination boundary sample (§8.1 b93 R2-B2): an outcome
    # with seq == cutover_seq - 1 is a pre-barrier dispatch and must quarantine.
    verdict = quarantine_late_outcome(
        {"cutover_seq": cutover},
        {"dispatch_id": "d-late-01", "seq": cutover - 1, "payload": {}},
    )
    assert verdict["status"] == "quarantined"
    assert verdict["dispatch_id"] == "d-late-01"
    assert "outcome_ref" in verdict
    # Quarantine is the barrier policy state: rendered immediately after the
    # barrier even with no late outcome yet (b93 Q3.5).
    status = trac("status").stdout
    assert "barrier=established" in status
    assert "late_outcome=quarantined" in status


# AC-FR0287-03@v0.8 TRACKS-TRACE return stales downstream evidence
def test_return_stales_downstream_evidence(host_repo, trac, event_log):
    run_id = walk_to_m_impl_parked(trac)
    seeded = seed_escape_downstream_evidence(host_repo)
    assert len(seeded) == 6, "the §8.1 A3 minimal bucket set must be seeded"
    assert trac("return", "--to", "M-TEST", "--reason", "stale evidence").returncode == 0
    events = event_log(run_id)
    staled = [e for e in events if e["type"] == "evidence.staled"]
    # Only the actually-existing post-target buckets are staled: exactly the
    # seeded set, each entry carrying the human_return reason, the target and
    # a reference to the staled bucket's store seq (b93 Q3.3).
    assert len(staled) == len(seeded)
    staled_types: set[str] = set()
    for entry in staled:
        payload = entry["payload"]
        assert payload["reason"] == "human_return"
        assert payload["target_stage"] == "M-TEST"
        assert payload["evidence_type"] in seeded
        assert seeded[payload["evidence_type"]] in payload.values()
        staled_types.add(payload["evidence_type"])
    assert staled_types == set(seeded)
    status = trac("status").stdout
    assert f"evidence.staled={len(seeded)}" in status
    assert "frozen_tests=unfrozen" in status
    # Non-reuse on re-entry (旧证据不复用) is NOT asserted here: the pointer
    # migration and the M-VERIFY freeze machinery are batch-2 / T-039
    # deliverables (b93 B4); that half belongs to the batch-2 anchor layer,
    # not skipped.


# AC-FR0287-04@v0.8 TRACKS-TRACE irreversible confirm then reconcile skip
def test_irreversible_confirm_then_reconcile_skip(host_repo, trac, event_log):
    """b93 A4 拆分: the irreversible sample lives on a source where the return
    gate demonstrably ACCEPTS the target (M-REQ-APPROVAL awaiting=approval ->
    M-STORY), so a red here is attributable to the missing already_executed
    report / --confirm gate — not to the batch-1 no-targets rejection."""
    run_id = walk_to_await_human(trac, version="v0.8")
    seeded = seed_escape_publish_done(host_repo)
    assert "publish.executed" in seeded, "the executed merge must be seeded"
    before = event_log(run_id)
    # Crossing executed irreversible operations without explicit confirmation:
    # the already_executed manifest is reported, the return exits non-zero,
    # appends zero events and leaves the pointer unmoved (§2d / b93 Q3.1).
    r = trac("return", "--to", "M-STORY", "--reason", "cross irreversible")
    assert r.returncode != 0
    out = r.stdout + r.stderr
    assert "already_executed" in out
    assert "merge" in out, "the seeded irreversible operation kind must be reported"
    assert event_log(run_id) == before, "unconfirmed return must append zero events"
    status = trac("status").stdout
    assert "stage=M-REQ-APPROVAL" in status and "awaiting=approval" in status
    # With explicit --confirm the escape sequence runs (the approval source is
    # in the §2d fixed source set, so the barrier semantics apply).
    r2 = trac("return", "--to", "M-STORY", "--reason", "cross irreversible", "--confirm")
    assert r2.returncode == 0, r2.stdout + r2.stderr
    events = event_log(run_id)
    barrier = _barrier(events)
    human = [e for e in events if e["type"] == "human.return"]
    assert human and barrier["seq"] < human[-1]["seq"]
    payload = human[-1]["payload"]
    assert payload["actor"] == "Human"
    assert payload["from"] == "M-REQ-APPROVAL"
    assert payload["to"] == "M-STORY"
    assert payload["to_stage"] == "M-STORY"
    assert "human_return=Human→M-STORY" in trac("status").stdout
    # b93 A4 拆分: the reconciled_skip assertion (IF-PUBLISH-002 reconcile on
    # re-entering M-PUBLISH) moved to the T-021/T-039-era anchor — batch 1 has
    # no M-PUBLISH route and seeding cannot reach it; not asserted here.


# AC-FR0287-05@v0.8 TRACKS-TRACE abandon terminal zero side effects
def test_abandon_terminal_zero_side_effects(host_repo, trac, event_log):
    run_id = walk_to_m_impl_parked(trac)
    tag_before = subprocess.run(
        ["git", "tag", "--list"], cwd=host_repo, capture_output=True, text=True, check=True
    ).stdout
    branch_before = subprocess.run(
        ["git", "branch", "--list"], cwd=host_repo, capture_output=True, text=True, check=True
    ).stdout
    r = trac("abandon", "--reason", "human termination")
    assert r.returncode == 0, r.stdout + r.stderr
    events = event_log(run_id)
    completed = [
        e
        for e in events
        if e["type"] == "run.completed" and e["payload"].get("terminal_state") == "cancelled"
    ]
    assert completed, "run.completed cancelled must appear"
    assert completed[-1]["payload"].get("reason") == "human termination"
    assert "terminal=cancelled" in trac("status").stdout
    assert (
        subprocess.run(
            ["git", "tag", "--list"], cwd=host_repo, capture_output=True, text=True, check=True
        ).stdout
        == tag_before
    )
    assert (
        subprocess.run(
            ["git", "branch", "--list"], cwd=host_repo, capture_output=True, text=True, check=True
        ).stdout
        == branch_before
    )
    r2 = trac("run")
    assert r2.returncode != 0
    # _err convention writes to stderr; the anchor checks stdout+stderr (b93 A5).
    assert "run is cancelled" in (r2.stdout + r2.stderr)


# AC-FR0287-01@v0.8 TRACKS-TRACE pointer rollback lands target initial substate (batch 2 §1.0.14.1)
def test_pointer_rollback_lands_target_stage(trac, event_log):
    """b93 B4 batch-2 anchor: real pointer migration. The run following the
    escape consumes RETURNED, appends stage.rolled_back to the target stage
    and routes re-entry via its StageDef.initial_substate (M-TEST=DISPATCH —
    no fixed-DRAFT dead state); the run stays re-enterable and a further
    escape is accepted with a strictly increasing cutover. A second migration
    lands M-TEST again inside the run-breaker budget of two, parking legally
    executable; --to M-IMPL has no legal batch-1 source (universal targets
    are strictly upstream — no self-target — and release stages land with
    T-039), so the M-IMPL=BASELINE routing stays pinned at unit level until
    the T-039 era."""
    run_id = walk_to_m_impl_parked(trac)
    assert trac("return", "--to", "M-TEST", "--reason", "pointer rollback").returncode == 0
    c1 = _cutover(event_log(run_id))
    # Strangled run: consumes RETURNED, migrates the pointer to M-TEST and
    # stops after one dispatch. The landing initial_substate (DISPATCH) is a
    # pre-dispatch transient, not the readable stable stop: the first Shield
    # dispatch advances DISPATCH→WRITE (SM-01.2) and inline non-dispatch
    # commands reach the PRISM_REVIEW stop (quota counts dispatch_agent
    # only). The not-bricked contract is therefore observed via the
    # stage.rolled_back payload plus a non-DRAFT stop in the M-TEST domain
    # (M-TEST has no DRAFT route; DRAFT would be the fossilized dead state).
    r_land = trac("run", "--max-dispatches", "1")
    assert r_land.returncode == 0, r_land.stderr
    rolled = [
        e
        for e in event_log(run_id)
        if e["type"] == "stage.rolled_back" and e["payload"].get("to_stage") == "M-TEST"
    ]
    assert rolled, "pointer migration must append stage.rolled_back to M-TEST"
    assert rolled[-1]["payload"].get("reason") == "human_return"
    status = trac("status").stdout
    assert "stage=M-TEST" in status
    assert "substate=DRAFT" not in status, "landing must not fossilize into DRAFT"
    # The run is not bricked: continuing it re-parks at M-IMPL escalation.
    r = trac("run", simulate=M_IMPL_PARK_SIMULATE)
    assert r.returncode == 0, r.stderr
    assert "stage=M-IMPL" in r.stdout and "awaiting=escalation" in r.stdout
    # Reentrant escape: a second barrier with a strictly increasing cutover
    # (store seq space is cross-process monotone, b93 B3).
    assert trac("return", "--to", "M-TEST", "--reason", "pointer rollback 2").returncode == 0
    assert _cutover(event_log(run_id)) > c1
    # Second migration: r3run consumes the second RETURNED and migrates to
    # M-TEST again (rollback #2 of the run-breaker budget of two), then parks
    # legally executable instead of dispatching further.
    r3run = trac("run", simulate=M_IMPL_PARK_SIMULATE)
    assert r3run.returncode == 0, r3run.stderr
    rolled_again = [e for e in event_log(run_id) if e["type"] == "stage.rolled_back"]
    assert len(rolled_again) == 2, "exactly the two accepted escapes may migrate"
    assert rolled_again[-1]["payload"].get("to_stage") == "M-TEST"
    assert rolled_again[-1]["payload"].get("reason") == "human_return"
    status3 = trac("status").stdout
    assert "stage=M-TEST" in status3 and "awaiting=escalation" in status3


# AC-FR0287-02@v0.8 TRACKS-TRACE real quiesce freezes in-flight dispatch (batch 2 §1.0.14.1)
def test_escape_barrier_quiesces_inflight_dispatch(host_repo, trac, event_log):
    """b93 batch-2 anchor: a genuinely in-flight dispatch (issued row with no
    outcome — created by killing a hanging M-STORY scribe DRAFT, the backend's
    real block token) is frozen/cancelled by the barrier and named in
    quiesced_dispatches. The parking reuses the walker's proven M-IMPL
    injection (M_IMPL_PARK_SIMULATE); the outstanding dispatch stays in the
    ledger across the recovery (recovery issues a NEW command)."""
    assert trac("init").returncode == 0
    r = trac("start", "v0.8", stdin="构建一个事件溯源运行时")
    assert r.returncode == 0, r.stderr
    run_id = re.search(r"run (\S+) started", r.stdout).group(1)
    assert trac("run").returncode == 0
    assert trac("triage", "go").returncode == 0
    # Real in-flight dispatch: hang the M-STORY scribe DRAFT and kill it.
    env = {
        k: v for k, v in os.environ.items() if k not in ("TRACKS_HOME", "TRAC_FAKE_SIMULATE")
    }
    env["TRAC_AGENT_BACKEND"] = "fake"
    env["TRAC_FAKE_SIMULATE"] = "scribe:DRAFT=hang"
    proc = subprocess.Popen(
        [sys.executable, "-m", "tracks.cli.main", "run"],
        cwd=host_repo,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        inflight_id: str | None = None
        for _ in range(200):
            issued = [
                e
                for e in event_log(run_id)
                if e["type"] == "command.issued"
                and e["payload"].get("command", {}).get("kind") == "dispatch_agent"
                and e["payload"]["command"]["params"].get("substate") == "DRAFT"
            ]
            if issued:
                inflight_id = issued[-1]["payload"]["command"]["command_id"]
                break
            time.sleep(0.05)
        else:
            raise AssertionError("hanging run never issued a DRAFT dispatch")
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=10)
    except Exception:
        proc.kill()
        proc.wait(timeout=10)
        raise
    assert inflight_id is not None
    events_killed = event_log(run_id)
    outcomes_killed = [
        e for e in events_killed if e["type"] == "outcome.received" and e["command_id"] == inflight_id
    ]
    assert not outcomes_killed, "the killed dispatch must remain in flight (issued, no outcome)"
    # Recovery replays the pending dispatch under its own command id; the walk
    # continues exactly as the proven walker drives it.
    r = trac("run")
    assert r.returncode == 0, r.stderr
    assert "awaiting=review" in r.stdout
    assert trac("review", "no-comment").returncode == 0
    for _ in ("M-SPEC", "M-ACC"):
        r = trac("run")
        assert r.returncode == 0, r.stderr
        assert "awaiting=review" in r.stdout
        assert trac("review", "no-comment").returncode == 0
    r = trac("run")
    assert r.returncode == 0, r.stderr
    assert "awaiting=approval" in r.stdout
    assert trac("approve", "--actor", "Aaron").returncode == 0
    # Park at the legal escape source with the walker's proven injection.
    # v0.7+ phase0 pre-gate: the first post-approval run drafts the M-DESIGN
    # trio and parks at phase0 on a bare repo; seed the premises (same
    # channel as walk_to_m_impl_parked) and drive one more injected run to
    # the M-IMPL park.
    r = trac("run", simulate=M_IMPL_PARK_SIMULATE)
    assert r.returncode == 0, r.stderr
    _seed_phase0_premises(getattr(trac, "repo", None) or host_repo, "v0.8")
    r = trac("run", simulate=M_IMPL_PARK_SIMULATE)
    assert r.returncode == 0, r.stderr
    assert "stage=M-IMPL" in r.stdout and "awaiting=escalation" in r.stdout
    # Barrier establishment quiesces in-flight dispatches (batch 2). Under the
    # current runtime a legal return source is always an awaiting state whose
    # pending dispatches were already replayed, so the payload contract plus a
    # soundness invariant are the anchor's executable core; the named-quiesce
    # / late-outcome interception scenario materializes with the batch-2
    # dispatch-loop cutover consumption (§1.0.14.1) and stays pinned at the
    # module boundary by the A2 discrimination sample until then.
    res = trac("return", "--to", "M-TEST", "--reason", "quiesce in-flight")
    assert res.returncode == 0, res.stdout + res.stderr
    events = event_log(run_id)
    barrier = _barrier(events)
    cutover = barrier["payload"]["cutover_seq"]
    quiesced = barrier["payload"]["quiesced_dispatches"]
    assert isinstance(quiesced, list)
    assert all(isinstance(q, str) for q in quiesced)
    for q in quiesced:
        rows = [
            e
            for e in events
            if e["type"] == "command.issued"
            and e["payload"].get("command", {}).get("command_id") == q
        ]
        assert rows, f"quiesced entry {q} must reference a real dispatch"
        assert rows[0]["seq"] <= cutover, "quiesced dispatches must be pre-barrier"
