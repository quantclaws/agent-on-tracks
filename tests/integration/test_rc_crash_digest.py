"""ResultCheckpoint pipeline: crash recovery, digest drift, template failure,
result_id propagation, digests, event ordering, publish recovery.

Split from ``test_result_checkpoint.py`` for module-size compliance (C0302).
"""

from tests.integration.result_checkpoint_support import (
    _dispatch_draft,
    _setup,
    _step_to_publish_and_crash,
    _step_validate,
    _submit_triage,
    g,
)

# -- Crash recovery --------------------------------------------------------


def test_crash_recovery_checkpoint_commit_exists(tmp_path):
    """Crash after checkpoint commit but before result.checkpointed: calling
    _recover() re-executes the pending checkpoint command, detects the commit
    by command_id marker, and backfills the event without a duplicate commit."""
    ex, store, run_id = _setup(tmp_path)
    repo = ex.repo
    _dispatch_draft(ex, store, run_id, run_pipeline=False)

    from tracks.kernel.machine import decide

    state = _step_validate(ex, store, run_id)
    assert state.active_result and not state.active_result.get("checkpointed")
    checkpoint_cmd = decide(state)
    assert checkpoint_cmd.kind == "checkpoint_result"

    original_execute = ex._execute
    committed_cids = []

    def crash_after_commit(executor_self, cmd, state, task_id, reconcile=False):
        if cmd.kind == "checkpoint_result":
            from tracks.executor.helpers import _scoped_commit_if_staged, git

            allowed_paths = cmd.params.get("allowed_paths", [])
            commit_label = cmd.params.get("commit_label") or "checkpoint"
            marker = f"command_id: {cmd.command_id}"
            stage_paths = [executor_self._doc_path(doc) for doc in allowed_paths]
            git(executor_self.repo, "add", *(str(p) for p in stage_paths))
            _scoped_commit_if_staged(
                executor_self.repo, f"{commit_label}\n\n{marker}", paths=stage_paths
            )
            committed_cids.append(cmd.command_id)
            return  # CRASH: no event emitted
        original_execute(cmd, state, task_id, reconcile)

    ex._execute = lambda cmd, state, task_id=None, reconcile=False: crash_after_commit(
        ex, cmd, state, task_id, reconcile
    )

    state = store.state(run_id)
    checkpoint_cmd = decide(state)
    ex._progress(checkpoint_cmd, state)
    ex.issue(checkpoint_cmd)
    assert committed_cids  # commit was made

    commits_before = g(repo, "rev-list", "--count", "HEAD").strip()

    ex._execute = original_execute

    ex._recover()

    assert g(repo, "rev-list", "--count", "HEAD").strip() == commits_before
    checkpointed = [e for e in store.events(run_id) if e.type == "result.checkpointed"]
    assert checkpointed
    assert checkpointed[-1].payload["created_commit"] is True


# -- Digest drift ----------------------------------------------------------


def test_digest_drift_detected_at_checkpoint(tmp_path):
    """If a file changes between capture (outcome.received) and checkpoint,
    the digest verification fails with verdict.failed."""
    ex, store, run_id = _setup(tmp_path)
    _dispatch_draft(ex, store, run_id, run_pipeline=False)
    _step_validate(ex, store, run_id)

    doc_path = ex._doc_path("story.md")
    text = doc_path.read_text(encoding="utf-8")
    doc_path.write_text(text + "\n\n<!-- tampered -->\n", encoding="utf-8")

    ex.run_pipeline()

    failures = [e for e in store.events(run_id) if e.type == "verdict.failed"]
    assert failures
    assert "digest_drift" in failures[-1].payload["check"]


# -- Template failure ------------------------------------------------------


def test_template_failure_produces_verdict_failed(tmp_path):
    """If the doc fails template validation, the pipeline emits verdict.failed
    and clears active_result."""
    ex, store, run_id = _setup(tmp_path)
    doc_path = ex._doc_path("story.md")
    doc_path.write_text("---\nsha:\n---\n\n# Bad\n", encoding="utf-8")

    _dispatch_draft(ex, store, run_id)

    failures = [e for e in store.events(run_id) if e.type == "verdict.failed"]
    assert failures
    assert "template" in failures[-1].payload["check"]
    state = store.state(run_id)
    assert state.active_result is None


# -- result_id propagation -------------------------------------------------


def test_result_id_propagated_through_pipeline(tmp_path):
    """result_id appears in outcome.received (result_checkpoint),
    result.validated, result.checkpointed, and the published domain event."""
    ex, store, run_id = _setup(tmp_path)
    _dispatch_draft(ex, store, run_id)

    outcomes = [e for e in store.events(run_id) if e.type == "outcome.received"]
    validated = [e for e in store.events(run_id) if e.type == "result.validated"]
    checkpointed = [e for e in store.events(run_id) if e.type == "result.checkpointed"]
    committed = [e for e in store.events(run_id) if e.type == "story.committed"]

    rid = outcomes[0].payload["result_checkpoint"]["result_id"]
    assert rid
    assert validated[0].payload["result_id"] == rid
    assert checkpointed[0].payload["result_id"] == rid
    assert committed[0].payload["result_id"] == rid


# -- Digests captured ------------------------------------------------------


def test_digests_captured_at_submission(tmp_path):
    """outcome.received result_checkpoint carries digests (sha256) per artifact."""
    ex, store, run_id = _setup(tmp_path)
    _dispatch_draft(ex, store, run_id, run_pipeline=False)

    outcomes = [e for e in store.events(run_id) if e.type == "outcome.received"]
    digests = outcomes[0].payload["result_checkpoint"].get("digests", {})
    assert "story.md" in digests
    assert len(digests["story.md"]) == 64  # sha256 hex


# -- Strict event ordering (item 4) ----------------------------------------


def test_strict_event_ordering_agent_draft(tmp_path):
    """Agent DRAFT: outcome.received < result.validated < result.checkpointed
    < domain event (story.committed) < transition (SAGE_REVIEW)."""
    ex, store, run_id = _setup(tmp_path)
    _dispatch_draft(ex, store, run_id)

    events = store.events(run_id)
    seqs = {}
    for e in events:
        if e.type not in seqs:
            seqs[e.type] = e.seq
    assert seqs["outcome.received"] < seqs["result.validated"]
    assert seqs["result.validated"] < seqs["result.checkpointed"]
    assert seqs["result.checkpointed"] < seqs["story.committed"]
    state = store.state(run_id)
    assert state.substate == "SAGE_REVIEW"


def test_strict_event_ordering_human_triage(tmp_path):
    """Human triage: result.submitted < result.validated < result.checkpointed
    < domain event (human.triage) < transition (DRAFT)."""
    ex, store, run_id = _setup(tmp_path)
    _submit_triage(ex, store, run_id)

    events = store.events(run_id)
    seqs = {}
    for e in events:
        if e.type not in seqs:
            seqs[e.type] = e.seq
    assert seqs["result.submitted"] < seqs["result.validated"]
    assert seqs["result.validated"] < seqs["result.checkpointed"]
    assert seqs["result.checkpointed"] < seqs["human.triage"]
    state = store.state(run_id)
    assert state.substate == "DRAFT"


# -- publish_result recovery (exactly-once) (item 4) -----------------------


def test_publish_result_recovery_exactly_once(tmp_path):
    """Crash after publish_result command is issued but before domain event:
    _recover() re-executes publish_result, emitting the domain event exactly
    once (no duplicate)."""
    ex, store, run_id = _setup(tmp_path)
    _dispatch_draft(ex, store, run_id, run_pipeline=False)
    _step_to_publish_and_crash(ex, store, run_id)

    assert not [e for e in store.events(run_id) if e.type == "story.committed"]

    ex._recover()

    committed = [e for e in store.events(run_id) if e.type == "story.committed"]
    assert len(committed) == 1
    state = store.state(run_id)
    assert state.active_result is None
    assert state.substate == "SAGE_REVIEW"
