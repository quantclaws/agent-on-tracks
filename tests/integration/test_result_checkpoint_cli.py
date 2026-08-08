"""ResultCheckpoint CLI pipeline integration tests (v0.5 review C.2 item 2).

Tests the real ``trac triage``/``trac review`` CLI commands through the
subprocess entry (conftest ``trac`` fixture), covering:

- Triage valid story diff: rc=0, commit+1, event order, human.triage last.
- Triage invalid story: rc!=0, no commit, no human.triage, still awaiting triage.
- Triage outside dirty: rc!=0, no result.submitted.
- Triage pre-staged: rc!=0, no result.submitted.
- Review no-comment dirty: rc!=0, no commit, no human.review, still awaiting review.
- Review revise valid body edit: rc=0, commit, human.review(comment).
"""
import re
import subprocess


def git_out(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


def _walk_to_triage(trac):
    """init -> start -> run (TRIAGE dispatch) -> awaiting=triage."""
    assert trac("init").returncode == 0
    r = trac("start", "v0.1", stdin="做一个X")
    assert r.returncode == 0, r.stderr
    run_id = re.search(r"run (\S+) started", r.stdout).group(1)
    assert trac("run").returncode == 0
    return run_id


def _walk_to_human_review(trac):
    """init -> start -> run -> triage go -> run (DRAFT+SAGE) -> awaiting=review."""
    run_id = _walk_to_triage(trac)
    assert trac("triage", "go").returncode == 0
    assert trac("run").returncode == 0
    return run_id


# -- Triage CLI ---------------------------------------------------------------


def test_triage_valid_story_diff(host_repo, trac, event_log):
    """Triage go with a valid story.md diff: rc=0, commit+1, event order,
    human.triage is the last event."""
    run_id = _walk_to_triage(trac)
    story = host_repo / ".tracks" / "projects" / "v0.1" / "story.md"
    text = story.read_text(encoding="utf-8")
    story.write_text(text + "\n\n额外补充内容\n", encoding="utf-8")

    commits_before = int(git_out(host_repo, "rev-list", "--count", "HEAD"))

    r = trac("triage", "go")
    assert r.returncode == 0, r.stderr

    commits_after = int(git_out(host_repo, "rev-list", "--count", "HEAD"))
    assert commits_after == commits_before + 1

    evs = event_log(run_id)
    types = [e["type"] for e in evs]
    assert "result.submitted" in types
    assert "result.validated" in types
    assert "result.checkpointed" in types
    assert "human.triage" in types

    seqs = {}
    for e in evs:
        if e["type"] not in seqs:
            seqs[e["type"]] = e["seq"]
    assert seqs["result.submitted"] < seqs["result.validated"]
    assert seqs["result.validated"] < seqs["result.checkpointed"]
    assert seqs["result.checkpointed"] < seqs["human.triage"]

    assert evs[-1]["type"] == "human.triage"


def test_triage_invalid_story_rejects(host_repo, trac, event_log):
    """Triage go with invalid story.md (broken template): rc!=0, no commit,
    no human.triage, still awaiting triage."""
    run_id = _walk_to_triage(trac)
    story = host_repo / ".tracks" / "projects" / "v0.1" / "story.md"
    story.write_text("---\nsha:\n---\n\n# Bad\n", encoding="utf-8")

    commits_before = int(git_out(host_repo, "rev-list", "--count", "HEAD"))

    r = trac("triage", "go")
    assert r.returncode != 0

    commits_after = int(git_out(host_repo, "rev-list", "--count", "HEAD"))
    assert commits_after == commits_before

    evs = event_log(run_id)
    types = [e["type"] for e in evs]
    assert "human.triage" not in types
    assert "verdict.failed" in types

    r = trac("status")
    assert "awaiting=triage" in r.stdout


def test_triage_outside_dirty_rejects(host_repo, trac, event_log):
    """Triage go with dirty file outside version dir: rc!=0, no result.submitted."""
    run_id = _walk_to_triage(trac)
    (host_repo / "outside.txt").write_text("dirty\n", encoding="utf-8")

    r = trac("triage", "go")
    assert r.returncode != 0

    evs = event_log(run_id)
    types = [e["type"] for e in evs]
    assert "result.submitted" not in types
    assert "human.triage" not in types


def test_triage_pre_staged_rejects(host_repo, trac, event_log):
    """Triage go with pre-staged content in version dir: rc!=0,
    no result.submitted."""
    run_id = _walk_to_triage(trac)
    story = host_repo / ".tracks" / "projects" / "v0.1" / "story.md"
    text = story.read_text(encoding="utf-8")
    story.write_text(text + "\n\nstaged content\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", ".tracks/projects/v0.1/story.md"],
        cwd=host_repo, capture_output=True, check=True,
    )

    r = trac("triage", "go")
    assert r.returncode != 0

    evs = event_log(run_id)
    types = [e["type"] for e in evs]
    assert "result.submitted" not in types
    assert "human.triage" not in types


# -- Review CLI ---------------------------------------------------------------


def test_review_no_comment_dirty_rejects(host_repo, trac, event_log):
    """Review no-comment with dirty story.md: rc!=0 (forbid_diff), no commit,
    no human.review, still awaiting review."""
    run_id = _walk_to_human_review(trac)
    story = host_repo / ".tracks" / "projects" / "v0.1" / "story.md"
    text = story.read_text(encoding="utf-8")
    story.write_text(text + "\n\nextra content\n", encoding="utf-8")

    commits_before = int(git_out(host_repo, "rev-list", "--count", "HEAD"))

    r = trac("review", "no-comment")
    assert r.returncode != 0

    commits_after = int(git_out(host_repo, "rev-list", "--count", "HEAD"))
    assert commits_after == commits_before

    evs = event_log(run_id)
    types = [e["type"] for e in evs]
    assert "human.review" not in types
    assert "verdict.failed" in types

    r = trac("status")
    assert "awaiting=review" in r.stdout


def test_review_revise_valid_body_edit(host_repo, trac, event_log):
    """Review revise with valid body edit: rc=0, commit created,
    human.review(comment) published."""
    run_id = _walk_to_human_review(trac)
    story = host_repo / ".tracks" / "projects" / "v0.1" / "story.md"
    text = story.read_text(encoding="utf-8")
    story.write_text(text + "\n\n额外修订内容\n", encoding="utf-8")

    commits_before = int(git_out(host_repo, "rev-list", "--count", "HEAD"))

    r = trac("review", "revise")
    assert r.returncode == 0, r.stderr

    commits_after = int(git_out(host_repo, "rev-list", "--count", "HEAD"))
    assert commits_after == commits_before + 1

    evs = event_log(run_id)
    reviews = [e for e in evs if e["type"] == "human.review"]
    assert reviews
    assert reviews[-1]["payload"]["action"] == "comment"
