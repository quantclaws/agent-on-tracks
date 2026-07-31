"""E2E full journey (TP-003 §4a): M-STORY → M-SPEC → M-ACC → M-REQ-APPROVAL →
run.completed(terminal_state="boundary") on the fake channel (SM-05.6).

Covers the M-ACC seal (AC-FR0160), the preview/approve gate (FR-0180/0190) and
the fake-channel Issues creation (FR-0200, D-04/D-06) as external observables.
"""
import hashlib
import re
import sqlite3

from tests.e2e.helpers import walk_to_await_human
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

    # SM-05.5/.6: record_approval → ISSUES → fake Issues → boundary completion
    r = trac("run")
    assert r.returncode == 0, r.stderr
    evs = event_log(run_id)
    ts = types(evs)
    assert ts[-2:] == ["stage.exited", "run.completed"]
    completed = evs[-1]["payload"]
    assert completed["terminal_state"] == "boundary"

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
