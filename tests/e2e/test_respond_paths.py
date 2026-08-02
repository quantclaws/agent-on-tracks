"""RESPOND loops + human revise + wrong-state command rejection
(FR-08, FR-14, FR-16): AC-08b, AC-14b, AC-16a, AC-16b.
"""
import subprocess

from tests.e2e.helpers import dispatches


def git_out(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


def test_wrong_state_commands_rejected(trac, event_log):
    assert trac("init").returncode == 0
    assert trac("start", "v0.1", stdin="需求").returncode == 0
    # not awaiting triage yet (no run executed) — AC-08b
    before = len(event_log())
    r = trac("triage", "go")
    assert r.returncode == 1 and r.stderr.strip()
    r = trac("review", "no-comment")
    assert r.returncode == 1 and r.stderr.strip()
    assert len(event_log()) == before  # no events appended


def test_sage_comment_and_human_revise_loops(host_repo, trac, event_log):
    assert trac("init").returncode == 0
    assert trac("start", "v0.1", stdin="需要评审往返的需求").returncode == 0
    assert trac("run").returncode == 0
    assert trac("triage", "go").returncode == 0

    # AC-14b: sage comments once, then passes — RESPOND cycle in between
    r = trac("run", simulate="sage:SAGE_REVIEW=comment|pass")
    assert r.returncode == 0, r.stderr
    evs = event_log()
    comments = [
        e for e in evs
        if e["type"] == "sage.verdict" and e["payload"]["verdict"] == "comment"
    ]
    assert len(comments) == 1
    responds = [
        d for d in dispatches(evs, "RESPOND") if d["seq"] > comments[0]["seq"]
    ]
    assert responds and responds[0]["payload"]["command"]["params"]["role"] == "scribe"
    assert "awaiting=review" in r.stdout  # forward progress to the human gate

    # AC-16b: dirty file outside the whitelist blocks revise, appends nothing
    story = host_repo / ".tracks" / "projects" / "v0.1" / "story.md"
    readme = host_repo / "README.md"
    readme.write_text(readme.read_text() + "drift\n", encoding="utf-8")
    before = len(event_log())
    r = trac("review", "revise")
    assert r.returncode == 1 and "README.md" in r.stderr
    assert len(event_log()) == before
    subprocess.run(["git", "checkout", "--", "README.md"], cwd=host_repo, check=True)

    # AC-16a: human edits story.md, revise commits it and carries diff_ref
    story.write_text(story.read_text(encoding="utf-8") + "\n人类补充意见。\n",
                     encoding="utf-8")
    r = trac("review", "revise")
    assert r.returncode == 0, r.stderr
    revise_sha = git_out(host_repo, "rev-parse", "HEAD").strip()
    assert "human revise" in git_out(host_repo, "log", "-1", "--format=%s")
    reviews = [e for e in event_log() if e["type"] == "human.review"]
    review_payload = reviews[-1]["payload"]
    assert review_payload["action"] == "comment"
    assert review_payload["diff_ref"] == revise_sha
    assert review_payload["actor"] == git_out(
        host_repo, "config", "user.name"
    ).strip()

    # next run dispatches Scribe into RESPOND with the diff_ref in the assignment
    r = trac("run")
    assert r.returncode == 0, r.stderr
    evs = event_log()
    responds = [
        d for d in dispatches(evs, "RESPOND")
        if d["seq"] > reviews[-1]["seq"]
    ]
    assert responds
    params = responds[0]["payload"]["command"]["params"]
    assert params["role"] == "scribe" and params["diff_ref"] == revise_sha
    assert "awaiting=review" in r.stdout  # cycled back to the human gate
