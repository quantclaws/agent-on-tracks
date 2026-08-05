"""E2E full journey (TP-003 §4a): M-STORY → M-SPEC → M-ACC → M-REQ-APPROVAL →
M-DESIGN → run.completed(terminal_state="boundary") on the fake channel.

Covers the M-ACC seal (AC-FR0160), the preview/approve gate (FR-0180/0190),
the fake-channel Issues creation (FR-0200, D-04/D-06) and the v0.3 M-DESIGN
stage (flow.md §8: Archer drafts the trio, Prism passes, no human gate) as
external observables.

v0.3 change (BS-02): the run no longer completes right after M-REQ-APPROVAL —
it continues into M-DESIGN and completes there at the M-IMPL boundary
(Decision A). The pre-M-DESIGN event prefix is asserted byte-stable below.
"""
import hashlib
import re
import sqlite3

from tests.e2e.helpers import dispatches, walk_to_await_human, walk_to_m_test_complete
from tests.e2e.test_happy_path import git_out, parse_frontmatter, types


# AC-FR0010-04@v0.4 TRACKS-TRACE full journey to boundary
def test_full_journey_to_boundary(host_repo, trac, event_log):
    """AC-FR0010-04@v0.4: pre-M-DESIGN event prefix byte-stable; M-TEST
    integration does not change transitions, events, or boundary terminal."""
    run_id = walk_to_await_human(trac)
    evs = event_log(run_id)

    # M-ACC sealed on exit (AC-FR0160): frontmatter sha == final event == body
    acceptance = host_repo / ".tracks" / "projects" / "v0.1" / "acceptance.md"
    fm, body = parse_frontmatter(acceptance)
    body_sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    finals = [
        e for e in evs
        if e["type"] == "acceptance.committed" and e["payload"].get("final")
    ]
    assert len(finals) == 1
    assert re.fullmatch(r"[0-9a-f]{64}", fm["sha"])
    assert fm["sha"] == finals[0]["payload"]["acceptance_sha"] == body_sha
    assert "seal acceptance.md sha" in git_out(host_repo, "log", "-5", "--format=%s")

    # SM-05.1/.2: preview generated with the D-01 digest + human summary
    previews = [e for e in evs if e["type"] == "preview.generated"]
    assert len(previews) == 1
    digest = previews[0]["payload"]["digest"]
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert "spec.md" in previews[0]["payload"]["summary"]

    # Human gate holds: another run without approval stays put (FR-0180)
    r = trac("run")
    assert r.returncode == 0 and "awaiting=approval" in r.stdout
    assert types(event_log(run_id)).count("preview.generated") == 1

    # SM-05.3: approve binds the digest
    r = trac("approve", "--actor", "Aaron")
    assert r.returncode == 0, r.stderr
    assert f"approved {digest}" in r.stdout

    # SM-05.5/.6: record_approval → ISSUES → fake Issues → stage exit.
    # v0.3 (BS-02): the run now CONTINUES into M-DESIGN instead of completing
    # here; completion moves to after M-DESIGN EXIT (still terminal=boundary).
    r = trac("run")
    assert r.returncode == 0, r.stderr
    evs = event_log(run_id)
    ts = types(evs)
    assert ts[-2:] == ["stage.exited", "run.completed"]
    completed = evs[-1]["payload"]
    assert completed["terminal_state"] == "boundary"

    # v0.3 M-DESIGN (flow.md §8): entered right after M-REQ-APPROVAL exits,
    # Archer commits the trio, Prism passes. v0.4: M-DESIGN EXIT enters M-TEST
    # (not run.completed); M-TEST runs to completion (Shield writes tests,
    # Prism reviews, Runtime verifies Red + trace, commits test assets).
    approval_seq = next(e["seq"] for e in evs if e["type"] == "human.approval")
    entered = [e for e in evs if e["type"] == "stage.entered"]
    assert entered[-1]["payload"]["stage"] == "M-TEST"
    assert entered[-1]["seq"] > approval_seq
    assert [e["payload"]["stage"] for e in evs
            if e["type"] == "stage.exited"][-1] == "M-TEST"
    committed = [e for e in evs if e["type"] == "design.committed"]
    assert sorted(e["payload"]["doc"] for e in committed) == [
        "architecture.md", "interfaces.md", "test-plan.md"]
    vdir = host_repo / ".tracks" / "projects" / "v0.1"
    for doc in ("architecture.md", "interfaces.md", "test-plan.md"):
        assert (vdir / doc).exists()  # BS-03: the trio on disk, validate-clean
    verdicts = [e for e in evs if e["type"] == "prism.verdict"]
    # v0.4: two prism.verdict(pass) -- M-DESIGN review + M-TEST PRISM_REVIEW
    assert [e["payload"]["verdict"] for e in verdicts] == ["pass", "pass"]
    # M-TEST events: test.collected, red.validated, test.committed
    assert [e["payload"]["status"] for e in evs
            if e["type"] == "test.collected"] == ["passed"]
    assert [e["payload"]["status"] for e in evs
            if e["type"] == "red.validated"] == ["valid"]
    assert any(e["type"] == "test.committed" for e in evs)
    assert not [e for e in evs if e["type"].startswith("human.")
                and e["seq"] > approval_seq]  # BS-05: no gate in M-DESIGN/M-TEST

    recorded = [e for e in evs if e["type"] == "approval.recorded"]
    assert len(recorded) == 1
    assert recorded[0]["payload"]["actor"] == "Aaron"
    assert recorded[0]["payload"]["digest"] == digest
    assert recorded[0]["payload"]["readonly"] is True

    # D-04/D-06: one Issue per FR/NFR item; summary mapping matches the items
    spec_body = parse_frontmatter(
        host_repo / ".tracks" / "projects" / "v0.1" / "spec.md")[1]
    items = re.findall(r"^### (N?FR-\d{4})", spec_body, re.M)
    created = [e for e in evs if e["type"] == "issue.created"]
    assert sorted(e["payload"]["item_id"] for e in created) == sorted(items)
    summary = [e for e in evs if e["type"] == "issues.created"]
    assert len(summary) == 1
    assert sorted(summary[0]["payload"]["mapping"]) == sorted(items)
    assert summary[0]["payload"]["digest"] == digest

    # AC-30a: every command.issued precedes its result event
    for e in evs:
        if e["type"] != "command.issued" and e["command_id"]:
            issue_seqs = [
                x["seq"] for x in evs
                if x["type"] == "command.issued" and x["command_id"] == e["command_id"]
            ]
            assert issue_seqs and min(issue_seqs) < e["seq"]

    # status / replay report the boundary terminal state (AC-24a/25a)
    r = trac("status")
    assert r.returncode == 0 and "terminal=boundary" in r.stdout
    r = trac("replay", run_id)
    assert r.returncode == 0 and "status=completed" in r.stdout

    # NFR-04: drop projections — the boundary state still folds from events
    db = host_repo / ".tracks" / "runtime" / "tracks.db"
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM runs")
    conn.commit()
    conn.close()
    r = trac("status")
    assert r.returncode == 0 and "terminal=boundary" in r.stdout


def test_prism_revise_drives_a_second_review_round(trac, event_log):
    # flow.md §8.1: verdict(revise) -> RESPOND (Archer revises the trio) ->
    # validate -> commit -> a NEW PRISM_REVIEW round (review.round_started),
    # then pass exits M-DESIGN and enters M-TEST (v0.4: no longer run.completed
    # at M-DESIGN; M-TEST runs to the boundary) - still no human gate (BS-05).
    run_id = walk_to_await_human(trac)
    assert trac("approve", "--actor", "Aaron").returncode == 0
    r = trac("run", simulate="prism:PRISM_REVIEW=revise|pass")
    assert r.returncode == 0, r.stderr
    assert "status=completed" in r.stdout
    evs = event_log(run_id)
    ts = types(evs)
    # v0.4: three prism.verdict events - M-DESIGN revise, M-DESIGN re-review
    # pass, M-TEST PRISM_REVIEW pass (simulate `revise|pass` applies to both
    # stages' PRISM_REVIEW; the last token sticks for the M-TEST call).
    assert [e["payload"]["verdict"] for e in evs
            if e["type"] == "prism.verdict"] == ["revise", "pass", "pass"]
    assert ts.count("review.round_started") == 1
    assert ts.count("design.committed") == 6  # 3 draft + 3 respond re-commits
    assert ts[-2:] == ["stage.exited", "run.completed"]
    assert evs[-1]["payload"]["terminal_state"] == "boundary"
    # v0.4: the final stage.exited is M-TEST (not M-DESIGN)
    assert [e["payload"]["stage"] for e in evs
            if e["type"] == "stage.exited"][-1] == "M-TEST"


def test_walk_to_design_complete(trac, event_log):
    # The shared helper drives approval -> M-DESIGN -> M-TEST -> run.completed
    # in a single `trac run` after approval (no human gate in M-DESIGN or
    # M-TEST, BS-05). v0.4: the boundary moved from M-DESIGN to M-TEST.
    run_id = walk_to_m_test_complete(trac)
    evs = event_log(run_id)
    assert types(evs)[-2:] == ["stage.exited", "run.completed"]
    assert evs[-1]["payload"]["terminal_state"] == "boundary"
    assert [e["payload"]["stage"] for e in evs
            if e["type"] == "stage.exited"][-1] == "M-TEST"


def test_bounded_walk_matches_harness_step_boundaries(trac, event_log):
    """The live-harness journey on the deterministic fake channel: each step
    runs with its own dispatch budget (3 for author and reviewer steps, 1
    otherwise) and must end exactly at the next substate boundary — no step
    over-runs and dispatches the following substate's agent under a stale
    overlay."""
    assert trac("init").returncode == 0
    r = trac("start", "v0.1", stdin="构建一个事件溯源运行时")
    assert r.returncode == 0, r.stderr
    run_id = re.search(r"run (\S+) started", r.stdout).group(1)
    budget = {
        "author": ("--max-dispatches", "3"),
        "reviewer": ("--max-dispatches", "3"),
        "other": ("--max-dispatches", "1"),
    }
    steps = 0

    def step(expect_substates, expect_stdout, kind="other", simulate=None):
        nonlocal steps
        before = len(dispatches(event_log(run_id)))
        r = trac("run", *budget[kind], simulate=simulate)
        assert r.returncode == 0, r.stderr
        new = dispatches(event_log(run_id))[before:]
        assert [d["payload"]["command"]["params"]["substate"] for d in new
                ] == expect_substates, f"step {steps} over-ran"
        assert expect_stdout in r.stdout
        steps += 1
        return r

    step(["TRIAGE"], "awaiting=triage")
    assert trac("triage", "go").returncode == 0

    step(["DRAFT"], "substate=SAGE_REVIEW", kind="author")  # scribe-story-draft
    step(["SAGE_REVIEW"], "substate=RESPOND", kind="reviewer",
         simulate="sage:SAGE_REVIEW=revise")               # sage-story-finding
    step(["RESPOND"], "substate=SAGE_REVIEW", kind="author")  # scribe-story-respond
    step(["SAGE_REVIEW"], "awaiting=review", kind="reviewer")  # sage-story-resolve
    assert trac("review", "no-comment").returncode == 0

    step(["DRAFT"], "substate=LEX_REVIEW", kind="author")  # sage-spec-draft
    step(["LEX_REVIEW"], "substate=RESPOND", kind="reviewer",
         simulate="lex:LEX_REVIEW=revise")                 # lex-spec-finding
    step(["RESPOND"], "substate=LEX_REVIEW", kind="author")  # sage-spec-respond
    step(["LEX_REVIEW"], "awaiting=review", kind="reviewer")  # lex-spec-resolve
    assert trac("review", "no-comment").returncode == 0

    step(["DRAFT"], "substate=LEX_REVIEW", kind="author")  # sage-acceptance-draft
    step(["LEX_REVIEW"], "awaiting=review", kind="reviewer")  # lex-acceptance-review
    assert trac("review", "no-comment").returncode == 0

    step([], "awaiting=approval")                          # approval-final: preview
    assert trac("approve", "--actor", "LiveE2E-Human").returncode == 0

    step(["DRAFT"], "substate=PRISM_REVIEW", kind="author")  # archer-design-draft
    # v0.4: M-DESIGN Prism pass -> EXIT -> stage.entered(M-TEST); the Shield
    # dispatch (WRITE) is a different substate -> gated, run stops at DISPATCH.
    step(["PRISM_REVIEW"], "stage=M-TEST", kind="reviewer")  # prism-design-review

    # v0.4 M-TEST: Shield dispatch (WRITE) -> collect_tests (not a dispatch) ->
    # stops at PRISM_REVIEW (Prism M-TEST dispatch is a different substate).
    step(["WRITE"], "substate=PRISM_REVIEW", kind="author")  # shield writes tests
    # Prism M-TEST dispatch -> run_tests/check_trace/commit_tests (not dispatches)
    # all execute in this step -> run.completed at the M-TEST->M-IMPL boundary.
    step(["PRISM_REVIEW"], "status=completed", kind="reviewer")  # prism M-TEST review

    evs = event_log(run_id)
    assert types(evs)[-2:] == ["stage.exited", "run.completed"]
    assert evs[-1]["payload"]["terminal_state"] == "boundary"
    assert [e["payload"]["stage"] for e in evs
            if e["type"] == "stage.exited"][-1] == "M-TEST"
    assert steps == 16
