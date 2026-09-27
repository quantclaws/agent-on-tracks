"""LEX_REVIEW legal park and thread reuse (IF-REVIEW-001).

Drives the M-SPEC Lex review through the real executor/dispatch seam over a
stub backend (the inherited ResultCheckpoint harness): a review that only
leaves Human threads pending must produce the legal
``pass-pending-human-threads`` verdict, park the run at
``awaiting=review_pending_threads``, render the pending-thread list through
``trac status``, and resume through ``trac review``; re-dispatches inject the
document's open threads so findings stay in the existing threads.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.integration.helpers import make_repo
from tests.integration.result_checkpoint_support import _StubBackend, _valid_spec_text
from tracks.kernel.events import Command
from tracks.store import new_ulid

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[2]

# A spec whose only open discussion thread asks @Human to adjudicate — the
# park premise of §1s.1 (every pending thread's owner is the Human).
_HUMAN_THREAD_SPEC = _valid_spec_text() + (
    "\n> **Lex:** 范围确认：§1n.4 六件套是否包含 test-plan？请 @Human 裁决。\n"
)


class _RecordingBackend(_StubBackend):
    """Stub backend that also records the dispatch assignment."""

    def __init__(self, outcome):
        super().__init__(outcome)
        self.outcome = dict(outcome)
        self.assignments: list[dict] = []

    def act(self, role, substate, doc, doc_path, assignment=None, worktree=None):
        self.assignments.append(dict(assignment or {}))
        return super().act(role, substate, doc, doc_path, assignment=assignment, worktree=worktree)


def _setup_spec_with_threads(tmp_path: Path):
    """A run at M-SPEC whose spec.md carries one Human-pending thread."""
    from tracks.executor.executor import Executor
    from tracks.paths import version_dir
    from tracks.store import Store

    repo = make_repo(tmp_path)
    home = repo / ".tracks"
    store = Store(home)
    run_id = new_ulid()
    vdir = version_dir(home, "v0.1")
    vdir.mkdir(parents=True)
    (vdir / "story.md").write_text(
        "# v0.1 story\n\nbaseline story.\n", encoding="utf-8"
    )
    (vdir / "spec.md").write_text(_HUMAN_THREAD_SPEC, encoding="utf-8")
    store.append(run_id, "v0.1", "story.requested", {"raw_chars": 1})
    store.append(run_id, "v0.1", "stage.entered", {"stage": "M-SPEC"})
    executor = Executor(store, repo, run_id)
    return repo, store, run_id, executor


def _draft_spec(executor, store, run_id):
    """Sage DRAFT commits spec.md -> LEX_REVIEW."""
    backend = _StubBackend(
        {"status": "done", "artifact_ref": "spec.md", "self_report": "draft"}
    )
    executor.backend = backend
    draft_cmd = Command(
        kind="dispatch_agent",
        params={
            "role": "sage",
            "substate": "DRAFT",
            "doc": "spec.md",
            "stage": "M-SPEC",
            "attempt": 1,
            "review_round": 1,
        },
        command_id=new_ulid(),
    )
    executor._do_dispatch_agent(draft_cmd, store.state(run_id), None, False)
    executor.run_pipeline()


def _lex_review(executor, store, run_id, outcome, backend=None):
    backend = backend or _StubBackend(outcome)
    executor.backend = backend
    review_cmd = Command(
        kind="dispatch_agent",
        params={
            "role": "lex",
            "substate": "LEX_REVIEW",
            "doc": "spec.md",
            "stage": "M-SPEC",
            "attempt": 1,
            "review_round": 1,
        },
        command_id=new_ulid(),
    )
    # through the write-ahead command surface so the dispatch materialization
    # (the assignment injection face) runs before the agent sees it
    executor.issue(review_cmd)
    executor.run_pipeline()
    return backend


def _cli(repo: Path, *args: str):
    env = {k: v for k, v in os.environ.items() if k != "TRACKS_HOME"}
    env["PYTHONPATH"] = str(_REPO_ROOT)
    return subprocess.run(
        [sys.executable, "-m", "tracks.cli.main", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
    )


# AC-FR0332-01@v0.10 TRACKS-TRACE pass-pending-human-threads parks and lists
def test_pass_pending_human_threads_parks_and_lists(tmp_path: Path):
    """AC-FR0332-01: a Lex review that only leaves Human threads pending
    produces the legal ``pass-pending-human-threads`` verdict with the
    pending-thread payload, parks at ``awaiting=review_pending_threads``
    (substate stays LEX_REVIEW, reviewer flag unset), renders the list through
    ``trac status``, and resumes through ``trac review``."""
    repo, store, run_id, executor = _setup_spec_with_threads(tmp_path)
    try:
        _draft_spec(executor, store, run_id)
        assert store.state(run_id).substate == "LEX_REVIEW"

        _lex_review(
            executor,
            store,
            run_id,
            {
                "status": "done",
                "artifact_ref": None,
                "self_report": "review",
                "verdict": "pass-pending-human-threads",
                "pending_threads": [
                    {"doc": "spec.md", "thread_id": "T-001", "summary": "六件套范围待 Human 裁决"}
                ],
            },
        )

        verdicts = [
            dict(e.payload or {})
            for e in store.events(run_id)
            if e.type == "lex.verdict"
        ]
        assert verdicts, "the review must publish its verdict"
        assert verdicts[-1]["verdict"] == "pass-pending-human-threads"
        assert verdicts[-1].get("pending_threads"), (
            "the park verdict carries the pending-thread payload (§1s.1)"
        )

        state = store.state(run_id)
        assert state.status == "awaiting_human", (
            f"the park must land status=awaiting_human: {state.status}"
        )
        assert state.awaiting == "review_pending_threads", (
            f"the park must set awaiting=review_pending_threads: {state.awaiting}"
        )
        assert state.substate == "LEX_REVIEW", (
            f"the park keeps the LEX_REVIEW substate: {state.substate}"
        )
        assert state.lex_passed_this_round is False, (
            "the reviewer pass flag must not be set by the park"
        )

        status = _cli(repo, "status")
        assert status.returncode == 0, status.stderr
        assert "review_pending_threads" in status.stdout, (
            f"trac status must render the park: {status.stdout!r}"
        )
        assert "T-001" in status.stdout, (
            f"trac status must list the pending Human threads: {status.stdout!r}"
        )
        assert "review" in status.stdout, (
            f"trac status must guide the resume: {status.stdout!r}"
        )

        review = _cli(repo, "review", "no-comment", "--actor", "Aaron")
        assert review.returncode == 0, review.stderr
        resumed = store.state(run_id)
        assert resumed.awaiting in (None, "review"), (
            f"the human review must clear the park awaiting: {resumed.awaiting}"
        )
        assert resumed.substate == "LEX_REVIEW", (
            f"the human review re-enters LEX_REVIEW: {resumed.substate}"
        )
    finally:
        store.close()


# AC-FR0332-02@v0.10 TRACKS-TRACE findings threads reused across attempts
def test_findings_threads_reused_across_attempts(tmp_path: Path):
    """AC-FR0332-02: LEX_REVIEW dispatches (including re-entry after a revise
    round) inject the document's open threads into the assignment, so Lex
    findings continue inside the existing threads — no new duplicate root
    threads across attempts."""
    from tracks.discuss.parser import parse_threads

    repo, store, run_id, executor = _setup_spec_with_threads(tmp_path)
    try:
        _draft_spec(executor, store, run_id)
        doc_path = repo / ".tracks" / "projects" / "v0.1" / "spec.md"
        before = {t.thread_id for t in parse_threads(doc_path.read_text(encoding="utf-8"))}
        assert before, "the fixture doc carries the discussion thread"

        backend = _RecordingBackend(
            {
                "status": "done",
                "artifact_ref": None,
                "self_report": "review",
                "verdict": "revise",
                "review_summary": "needs detail",
            }
        )
        _lex_review(executor, store, run_id, backend.outcome, backend=backend)
        _lex_review(executor, store, run_id, backend.outcome, backend=backend)

        assert len(backend.assignments) >= 2, "both dispatches must be recorded"
        open_threads = [a.get("open_threads") for a in backend.assignments]
        for injected in open_threads:
            assert isinstance(injected, list) and injected, (
                f"each LEX_REVIEW dispatch must inject open_threads: {injected!r}"
            )
            for thread in injected:
                assert set(thread) >= {"thread_id", "status"}, (
                    f"each open thread carries its identity: {thread!r}"
                )
                assert thread["status"] in ("open", "reopen")
        assert open_threads[0] == open_threads[1], (
            "re-entry re-injects the same open threads (stable across attempts)"
        )
        after = {t.thread_id for t in parse_threads(doc_path.read_text(encoding="utf-8"))}
        assert after == before, (
            "the dispatches must not open duplicate threads on the document"
        )
    finally:
        store.close()
