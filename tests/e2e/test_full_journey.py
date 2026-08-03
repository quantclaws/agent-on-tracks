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

from tests.e2e.helpers import walk_to_await_human, walk_to_design_complete
from tests.e2e.test_happy_path import git_out, parse_frontmatter, types


def test_full_journey_to_boundary(host_repo, trac, event_log):
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
    # Archer commits the trio, Prism passes, EXIT gate re-validates, and the
    # run completes at the M-IMPL boundary with no human.* event after
    # approval (BS-05).
    approval_seq = next(e["seq"] for e in evs if e["type"] == "human.approval")
    entered = [e for e in evs if e["type"] == "stage.entered"]
    assert entered[-1]["payload"]["stage"] == "M-DESIGN"
    assert entered[-1]["seq"] > approval_seq
    committed = [e for e in evs if e["type"] == "design.committed"]
    assert sorted(e["payload"]["doc"] for e in committed) == [
        "architecture.md", "interfaces.md", "test-plan.md"]
    vdir = host_repo / ".tracks" / "projects" / "v0.1"
    for doc in ("architecture.md", "interfaces.md", "test-plan.md"):
        assert (vdir / doc).exists()  # BS-03: the trio on disk, validate-clean
    verdicts = [e for e in evs if e["type"] == "prism.verdict"]
    assert [e["payload"]["verdict"] for e in verdicts] == ["pass"]
    assert not [e for e in evs if e["type"].startswith("human.")
                and e["seq"] > approval_seq]  # BS-05: no gate in M-DESIGN

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
    # flow.md §8.1: verdict(revise) → RESPOND (Archer revises the trio) →
    # validate → commit → a NEW PRISM_REVIEW round (review.round_started),
    # then pass exits and completes the run — still no human gate (BS-05).
    run_id = walk_to_await_human(trac)
    assert trac("approve", "--actor", "Aaron").returncode == 0
    r = trac("run", simulate="prism:PRISM_REVIEW=revise|pass")
    assert r.returncode == 0, r.stderr
    assert "status=completed" in r.stdout
    evs = event_log(run_id)
    ts = types(evs)
    assert [e["payload"]["verdict"] for e in evs
            if e["type"] == "prism.verdict"] == ["revise", "pass"]
    assert ts.count("review.round_started") == 1
    assert ts.count("design.committed") == 6  # 3 draft + 3 respond re-commits
    assert ts[-2:] == ["stage.exited", "run.completed"]
    assert evs[-1]["payload"]["terminal_state"] == "boundary"


def test_walk_to_design_complete(trac, event_log):
    # The shared helper drives approval → M-DESIGN → run.completed in a single
    # `trac run` after approval (no human gate in M-DESIGN, BS-05).
    run_id = walk_to_design_complete(trac)
    evs = event_log(run_id)
    assert types(evs)[-2:] == ["stage.exited", "run.completed"]
    assert evs[-1]["payload"]["terminal_state"] == "boundary"
    assert [e["payload"]["stage"] for e in evs
            if e["type"] == "stage.exited"][-1] == "M-DESIGN"
