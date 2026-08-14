"""E2E approval journeys (SM-05, FR-0180/0190): trac approve / trac return +
AWAIT_HUMAN sleep-replay + post-approval staleness blocking downstream.
"""

from tests.e2e.helpers import walk_to_await_human
from tests.e2e.test_happy_path import types


def test_await_human_sleep_replay(trac, event_log):
    # SM-05: every CLI call is a fresh process — the gate must survive sleep.
    run_id = walk_to_await_human(trac)
    before = len(event_log(run_id))
    for _ in range(2):  # repeated wake-ups at the gate are pure no-ops
        r = trac("run")
        assert r.returncode == 0 and "awaiting=approval" in r.stdout
    assert len(event_log(run_id)) == before
    r = trac("status")
    assert r.returncode == 0 and "awaiting=approval" in r.stdout
    r = trac("replay", run_id)
    assert r.returncode == 0
    assert "status=awaiting_human" in r.stdout and "awaiting=approval" in r.stdout


def test_approve_binds_preview_digest_and_default_actor(trac, event_log):
    # SM-05.3: human.approval carries the previewed digest; the actor defaults
    # to git user.name when --actor is omitted.
    run_id = walk_to_await_human(trac)
    r = trac("approve")
    assert r.returncode == 0, r.stderr
    evs = event_log(run_id)
    previews = [e for e in evs if e["type"] == "preview.generated"]
    approvals = [e for e in evs if e["type"] == "human.approval"]
    assert len(approvals) == 1
    assert approvals[0]["payload"]["digest"] == previews[-1]["payload"]["digest"]
    assert approvals[0]["payload"]["actor"] == "Test Human"
    assert approvals[0]["payload"]["ts"]


def test_return_journey_reworks_then_completes(trac, event_log):
    # SM-05.4/.7: return rolls back to M-SPEC, the review loop reruns from
    # there, a FRESH preview reopens the gate, and approval then completes
    # the run at the boundary. v0.3 (BS-02): the single post-approval run now
    # also drives M-DESIGN before completing — no assertion change needed,
    # terminal=boundary holds either way (Decision A keeps the terminal state).
    run_id = walk_to_await_human(trac)
    r = trac("return", "--to", "M-SPEC", "--reason", "范围要收")
    assert r.returncode == 0 and "returned to M-SPEC" in r.stdout
    for _ in ("M-SPEC", "M-ACC"):
        r = trac("run")
        assert r.returncode == 0, r.stderr
        assert "awaiting=review" in r.stdout
        assert trac("review", "no-comment").returncode == 0
    r = trac("run")
    assert r.returncode == 0 and "awaiting=approval" in r.stdout
    evs = event_log(run_id)
    returns = [e for e in evs if e["type"] == "human.return"]
    rollbacks = [e for e in evs if e["type"] == "stage.rolled_back"]
    assert len(returns) == 1 and returns[0]["payload"]["to_stage"] == "M-SPEC"
    assert len(rollbacks) == 1 and rollbacks[0]["payload"]["to_stage"] == "M-SPEC"
    assert returns[0]["payload"]["reason"] == "范围要收"
    assert types(evs).count("preview.generated") == 2  # fresh gate after rework
    assert trac("approve", "--actor", "Aaron").returncode == 0
    r = trac("run")
    assert r.returncode == 0, r.stderr
    r = trac("status")
    assert r.returncode == 0 and "terminal=boundary" in r.stdout


def test_stale_blocks_downstream(host_repo, trac, event_log):
    # FR-0190 (D-02/D-03): the trio changes AFTER approval — the executor's
    # entry gate recomputes the digest, blocks record_approval/create_issues,
    # and sends the run back to the human gate with a regenerated preview.
    run_id = walk_to_await_human(trac)
    assert trac("approve", "--actor", "Aaron").returncode == 0
    acc = host_repo / ".tracks" / "projects" / "v0.1" / "acceptance.md"
    acc.write_text(acc.read_text(encoding="utf-8") + "\n批准后偷改\n", encoding="utf-8")
    r = trac("run")
    assert r.returncode == 0, r.stderr
    assert "awaiting=approval" in r.stdout  # back at the gate, not downstream
    evs = event_log(run_id)
    assert types(evs).count("preview.generated") == 2
    assert "approval.recorded" not in types(evs)
    assert "issue.created" not in types(evs)
    # the fresh preview is approvable and the run then completes normally.
    # v0.3 (BS-02): completion now comes after M-DESIGN (single post-approval
    # run drives the design stage to its boundary exit) — the last-event
    # assertions below are unchanged because the terminal state is the same.
    assert trac("approve", "--actor", "Aaron").returncode == 0
    r = trac("run")
    assert r.returncode == 0, r.stderr
    evs = event_log(run_id)
    assert types(evs)[-1] == "run.completed"
    assert evs[-1]["payload"]["terminal_state"] == "boundary"
    recorded = [e for e in evs if e["type"] == "approval.recorded"]
    assert len(recorded) == 1
    assert (
        recorded[0]["payload"]["digest"]
        == [e for e in evs if e["type"] == "preview.generated"][-1]["payload"]["digest"]
    )
