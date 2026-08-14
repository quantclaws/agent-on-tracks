"""ResultCheckpoint pipeline: author DRAFT + reviewer pass/comment/discussion tests.

Split from ``test_result_checkpoint.py`` for module-size compliance (C0302).
"""

from tests.integration.result_checkpoint_support import (
    _dispatch_draft,
    _dispatch_sage_review,
    _setup,
    _setup_draft_committed,
    g,
)

# -- Author DRAFT pipeline ------------------------------------------------


def test_author_draft_validates_commits_publishes(tmp_path):
    """DRAFT dispatch -> pipeline validates template, commits story.md,
    publishes story.committed, transitions to SAGE_REVIEW."""
    ex, store, run_id = _setup(tmp_path)
    _dispatch_draft(ex, store, run_id)

    events = [e.type for e in store.events(run_id)]
    assert "outcome.received" in events
    assert "result.validated" in events
    assert "result.checkpointed" in events
    assert "story.committed" in events
    state = store.state(run_id)
    assert state.active_result is None
    assert state.substate == "SAGE_REVIEW"
    committed = [e for e in store.events(run_id) if e.type == "story.committed"]
    assert committed[0].payload["commit_sha"]
    assert committed[0].payload["final"] is False


def test_author_draft_checkpoint_creates_commit(tmp_path):
    """The checkpoint step creates a real git commit with the command_id marker."""
    ex, store, run_id = _setup(tmp_path)
    repo = ex.repo
    base_sha = g(repo, "rev-parse", "HEAD").strip()
    _dispatch_draft(ex, store, run_id)

    head = g(repo, "rev-parse", "HEAD").strip()
    assert head != base_sha
    log_msg = g(repo, "log", "-1", "--format=%B")
    assert "command_id:" in log_msg


# -- Reviewer pass pipeline ------------------------------------------------


def test_reviewer_pass_no_change_checkpoint(tmp_path):
    """Reviewer pass with no diff: checkpoint emits created_commit=False,
    publishes sage.verdict, transitions to HUMAN_REVIEW."""
    ex, store, run_id = _setup(tmp_path)
    _dispatch_draft(ex, store, run_id)
    assert store.state(run_id).substate == "SAGE_REVIEW"

    head_before, head_after = _dispatch_sage_review(ex, store, run_id, "pass")

    assert head_before == head_after  # no commit for pass with no diff
    checkpointed = [e for e in store.events(run_id) if e.type == "result.checkpointed"]
    assert checkpointed[-1].payload["created_commit"] is False
    verdicts = [e for e in store.events(run_id) if e.type == "sage.verdict"]
    assert verdicts[-1].payload["verdict"] == "pass"
    state = store.state(run_id)
    assert state.substate == "HUMAN_REVIEW"
    assert state.awaiting == "review"


# -- Reviewer comment pipeline ---------------------------------------------


def test_reviewer_comment_creates_discussion_checkpoint(tmp_path):
    """Reviewer comment with discussion diff: checkpoint commits the diff,
    publishes sage.verdict(comment), transitions to RESPOND."""
    ex, store, run_id = _setup(tmp_path)
    _dispatch_draft(ex, store, run_id)

    doc_path = ex._doc_path("story.md")
    text = doc_path.read_text(encoding="utf-8")
    doc_path.write_text(text + "\n\n> **Sage:** 需要补充用户路径。\n", encoding="utf-8")

    head_before, head_after = _dispatch_sage_review(ex, store, run_id, "comment")

    assert head_before != head_after  # commit created for discussion diff
    checkpointed = [e for e in store.events(run_id) if e.type == "result.checkpointed"]
    assert checkpointed[-1].payload["created_commit"] is True
    verdicts = [e for e in store.events(run_id) if e.type == "sage.verdict"]
    assert verdicts[-1].payload["verdict"] == "comment"
    state = store.state(run_id)
    assert state.substate == "RESPOND"


def test_reviewer_comment_no_diff_fails(tmp_path):
    """Reviewer comment with no diff: pipeline fails (requires_diff=True)."""
    ex, store, run_id = _setup(tmp_path)
    _dispatch_draft(ex, store, run_id)
    _dispatch_sage_review(ex, store, run_id, "comment")

    failures = [e for e in store.events(run_id) if e.type == "verdict.failed"]
    assert failures
    assert "no_diff" in failures[-1].payload["check"]


# -- Reviewer discussion_only regression (item 3) ---------------------------


def test_reviewer_pass_body_diff_rejects(tmp_path):
    """Sage pass with a body text diff (not discussion-only): discussion_diff
    failure, no sage.verdict, no commit."""
    ex, store, run_id = _setup_draft_committed(tmp_path)

    doc_path = ex._doc_path("story.md")
    text = doc_path.read_text(encoding="utf-8")
    doc_path.write_text(text + "\n\n额外正文内容\n", encoding="utf-8")

    head_before, head_after = _dispatch_sage_review(ex, store, run_id, "pass")

    assert head_before == head_after
    failures = [e for e in store.events(run_id) if e.type == "verdict.failed"]
    assert failures
    assert "discussion_diff" in failures[-1].payload["check"]
    verdicts = [e for e in store.events(run_id) if e.type == "sage.verdict"]
    assert not verdicts


def test_reviewer_pass_illegal_blockquote_rejects(tmp_path):
    """Sage pass with a raw (non-canonical) blockquote diff: discussion_diff
    failure, no sage.verdict, no commit."""
    ex, store, run_id = _setup_draft_committed(tmp_path)

    doc_path = ex._doc_path("story.md")
    text = doc_path.read_text(encoding="utf-8")
    doc_path.write_text(text + "\n\n> note: this is a raw quote\n", encoding="utf-8")

    head_before, head_after = _dispatch_sage_review(ex, store, run_id, "pass")

    assert head_before == head_after
    failures = [e for e in store.events(run_id) if e.type == "verdict.failed"]
    assert failures
    assert "discussion_diff" in failures[-1].payload["check"]
    verdicts = [e for e in store.events(run_id) if e.type == "sage.verdict"]
    assert not verdicts


def test_reviewer_pass_canonical_discussion_accepts(tmp_path):
    """Sage pass with only canonical discussion additions: accepted, commit
    created, sage.verdict(pass) published."""
    ex, store, run_id = _setup_draft_committed(tmp_path)

    doc_path = ex._doc_path("story.md")
    text = doc_path.read_text(encoding="utf-8")
    doc_path.write_text(text + "\n\n> **Sage:** 这是一个讨论注释。\n", encoding="utf-8")

    head_before, head_after = _dispatch_sage_review(ex, store, run_id, "pass")

    assert head_before != head_after
    failures = [e for e in store.events(run_id) if e.type == "verdict.failed"]
    assert not failures
    verdicts = [e for e in store.events(run_id) if e.type == "sage.verdict"]
    assert verdicts
    assert verdicts[-1].payload["verdict"] == "pass"
    state = store.state(run_id)
    assert state.substate == "HUMAN_REVIEW"
    assert state.awaiting == "review"
