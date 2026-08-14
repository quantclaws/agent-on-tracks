"""ResultCheckpoint pipeline: human triage + human review tests.

Split from ``test_result_checkpoint.py`` for module-size compliance (C0302).
"""

from tests.integration.result_checkpoint_support import (
    _setup,
    _submit_triage,
    _walk_to_human_review,
)

# -- Human triage pipeline -------------------------------------------------


def test_human_triage_no_artifacts_publishes(tmp_path):
    """Human triage with no doc diff: no artifacts, no-change checkpoint,
    publishes human.triage, transitions to DRAFT."""
    ex, store, run_id = _setup(tmp_path)
    _submit_triage(ex, store, run_id)

    events = [e.type for e in store.events(run_id)]
    assert "result.submitted" in events
    assert "result.validated" in events
    assert "result.checkpointed" in events
    assert "human.triage" in events
    state = store.state(run_id)
    assert state.active_result is None
    assert state.triage_decision == "go"
    assert state.substate == "DRAFT"


def test_human_triage_with_doc_diff_validates_template(tmp_path):
    """Human triage with a story doc diff: artifacts=[story.md],
    checks=[template], pipeline validates and commits."""
    ex, store, run_id = _setup(tmp_path)
    doc_path = ex._doc_path("story.md")
    text = doc_path.read_text(encoding="utf-8")
    doc_path.write_text(text + "\n\n额外内容\n", encoding="utf-8")
    _submit_triage(ex, store, run_id, artifacts=["story.md"], checks=["template"])

    events = [e.type for e in store.events(run_id)]
    assert "story.committed" not in events  # triage publishes human.triage, not committed
    assert "human.triage" in events
    checkpointed = [e for e in store.events(run_id) if e.type == "result.checkpointed"]
    assert checkpointed[-1].payload["created_commit"] is True
    state = store.state(run_id)
    assert state.triage_decision == "go"


# -- Human review no-comment (forbid_diff) ---------------------------------


def test_human_review_no_comment_clean_publishes(tmp_path):
    """Human review no-comment with no doc diff: forbid_diff passes,
    publishes human.review(no_comment)."""
    ex, store, run_id = _setup(tmp_path)
    _walk_to_human_review(ex, store, run_id)

    ex.submit_human_result(
        state=store.state(run_id),
        domain_event_type="human.review",
        payload={"actor": "T"},
        verdict="no_comment",
        artifacts=["story.md"],
        allowed_paths=["story.md"],
        checks=[],
        requires_diff=False,
        forbid_diff=True,
        discussion_only=False,
        commit_label="M-STORY: human no-comment",
    )
    ex.run_pipeline()

    reviews = [e for e in store.events(run_id) if e.type == "human.review"]
    assert reviews[-1].payload["action"] == "no_comment"


def test_human_review_no_comment_with_diff_fails(tmp_path):
    """Human review no-comment with a doc diff: forbid_diff rejects."""
    ex, store, run_id = _setup(tmp_path)
    _walk_to_human_review(ex, store, run_id)

    doc_path = ex._doc_path("story.md")
    text = doc_path.read_text(encoding="utf-8")
    doc_path.write_text(text + "\n\nextra content\n", encoding="utf-8")

    ex.submit_human_result(
        state=store.state(run_id),
        domain_event_type="human.review",
        payload={"actor": "T"},
        verdict="no_comment",
        artifacts=["story.md"],
        allowed_paths=["story.md"],
        checks=[],
        requires_diff=False,
        forbid_diff=True,
        discussion_only=False,
        commit_label="M-STORY: human no-comment",
    )
    ex.run_pipeline()

    failures = [e for e in store.events(run_id) if e.type == "verdict.failed"]
    assert failures
    assert "forbidden_diff" in failures[-1].payload["check"]


# -- Human review revise (discussion_only=False) --------------------------


def test_human_review_revise_allows_body_edits(tmp_path):
    """Human review revise with body edits (non-discussion):
    discussion_only=False allows it, requires_diff=True satisfied."""
    ex, store, run_id = _setup(tmp_path)
    _walk_to_human_review(ex, store, run_id)

    doc_path = ex._doc_path("story.md")
    text = doc_path.read_text(encoding="utf-8")
    doc_path.write_text(text + "\n\n额外正文内容\n", encoding="utf-8")

    ex.submit_human_result(
        state=store.state(run_id),
        domain_event_type="human.review",
        payload={"actor": "T"},
        verdict="comment",
        artifacts=["story.md"],
        allowed_paths=["story.md"],
        checks=["template"],
        requires_diff=True,
        forbid_diff=False,
        discussion_only=False,
        commit_label="M-STORY: human revise",
    )
    ex.run_pipeline()

    reviews = [e for e in store.events(run_id) if e.type == "human.review"]
    assert reviews[-1].payload["action"] == "comment"
    assert reviews[-1].payload["diff_ref"]  # commit_sha exists


# -- Triage invalid template (item 4) --------------------------------------


def test_triage_invalid_template_fails(tmp_path):
    """Human triage with a doc that fails template validation: verdict.failed."""
    ex, store, run_id = _setup(tmp_path)
    doc_path = ex._doc_path("story.md")
    doc_path.write_text("---\nsha:\n---\n\n# Bad\n", encoding="utf-8")
    _submit_triage(ex, store, run_id, artifacts=["story.md"], checks=["template"])
    failures = [e for e in store.events(run_id) if e.type == "verdict.failed"]
    assert failures
    assert "template" in failures[-1].payload["check"]
