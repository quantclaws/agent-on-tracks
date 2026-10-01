"""T-010 RED: LEX_REVIEW legal park exit and resume (IF-REVIEW-001,
AC-FR0332-01 / issue #183).

Devon-owned unit RED for the round-4 re-composed T-010 slice (scope:
``tracks/effects/opencode_review.py``, ``tracks/kernel/machine_verdicts.py``,
``tracks/kernel/machine.py``, ``tracks/cli/status_cmd.py``,
``tracks/executor/result_payload.py``, ``tracks/executor/result_audit.py``,
``tracks/cli/run_cmd.py``). The injection half (dispatch.py ``open_threads``
context + Lex.md discipline) is owned by the sibling task T-010b and is
deliberately not pinned here.

Contracts pinned (interfaces §1s.1/.2/.3/.4):

- the review exit computes its verdict server-side from the tracks/discuss
  parse, never from an agent self-report: every thread resolved -> ``pass``;
  every unresolved thread Human-adjudicated -> ``pass-pending-human-threads``
  with a non-empty §1s.1 ``pending_threads`` payload
  (``{doc, thread_id, summary}``); any other unresolved owner -> ``revise``;
- the kernel reduces the park to ``status=awaiting_human``,
  ``awaiting=review_pending_threads``, substate stays ``LEX_REVIEW``, the
  reviewer pass flag is not set, and the pending list is projected into State
  (event-replayable); ``human.review`` clears the awaiting and re-enters
  LEX_REVIEW (`trac review` resume);
- the ResultCheckpoint pipeline treats the park as a legal no-diff verdict:
  ``_requirement_review_payload`` keeps ``requires_diff`` off and carries the
  pending list on the park ``lex.verdict`` payload, and
  ``_validate_diff_policy`` lets a park with no diff through instead of
  emitting the reviewer ``no_diff`` hard failure (a plain ``revise`` keeps
  requiring the diff);
- ``trac status`` renders the pending Human thread list (doc + thread_id +
  summary) plus the ``trac review`` recovery pointer, and ``trac review``
  admits ``awaiting=review_pending_threads`` (HUMAN_REVIEW semantics
  unchanged);
- the park no-diff exemption is what lets the park publish at all (the
  pre-defect anchor failed at "no lex.verdict published").

RED discipline: this is a fresh RED on a clean baseline (the earlier candidate
was quarantined and never entered main history), so every node below fails as
a real ``AssertionError`` on the missing park contract — no stub tokens, no
assembly errors. Fixtures are real tracks/discuss documents parsed by the
product parser; the pipeline nodes drive the real Executor payload/diff-policy
faces.

AC: FR-0332 — TRACKS-TRACE IF-REVIEW-001.
"""

from __future__ import annotations

import subprocess
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from tests.unit.helpers import git_repo, seq
from tracks import paths
from tracks.cli import run_cmd
from tracks.cli.run_cmd import cmd_review
from tracks.cli.status_cmd import cmd_status
from tracks.effects.opencode_review import OpencodeReviewMixin
from tracks.executor.executor import Executor
from tracks.kernel import decide, project
from tracks.kernel.events import Command
from tracks.kernel.machine import State
from tracks.store import Store

PARK_VERDICT_NAME = "pass-pending-human-threads"

# One open thread plus one reopen thread, each root @mentioning Human as the
# single adjudication owner (FR-0314.4); the review exit must park on these.
HUMAN_PENDING_DOC = (
    "# spec\n"
    "\n"
    "> **Lex [open]:** The rollout stop condition must be decided by @Human.\n"
    "\n"
    "> **Lex [reopen]:** The coverage claim needs @Human to re-confirm.\n"
)

PARK_THREADS = [
    {"doc": "spec.md", "thread_id": "T-001", "summary": "stop condition needs a Human call"},
    {
        "doc": "spec.md",
        "thread_id": "T-002",
        "summary": "coverage claim needs Human re-confirmation",
    },
]
PARK_VERDICT = {"verdict": PARK_VERDICT_NAME, "pending_threads": PARK_THREADS}


def _write_doc(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _versioned_doc(store: Store, version: str, text: str) -> Path:
    return _write_doc(paths.version_dir(store.home, version) / "spec.md", text)


def _spec_review_events(*extra):
    """M-SPEC review pipeline up to the Lex park verdict, plus ``extra``."""
    return seq(
        ("story.requested", {"raw_chars": 5}),
        ("stage.entered", {"stage": "M-SPEC"}),
        ("spec.committed", {"commit_sha": "c1", "spec_sha": "s1", "final": False}),
        (
            "command.issued",
            {
                "command": {
                    "kind": "dispatch_agent",
                    "params": {"role": "lex", "substate": "LEX_REVIEW"},
                    "command_id": "C9",
                }
            },
        ),
        ("lex.verdict", dict(PARK_VERDICT)),
        *extra,
    )


def _park_result() -> dict:
    return {"verdict": PARK_VERDICT_NAME, "pending_threads": list(PARK_THREADS)}


# -- review-exit three-branch discrimination ---------------------------------


# AC-FR0332-01@v0.10 TRACKS-TRACE IF-REVIEW-001 three-branch verdict discrimination
def test_review_exit_three_branches_discriminate(tmp_path):
    resolved_only = _write_doc(
        tmp_path / "resolved" / "spec.md", "> **Lex [resolved]:** closed.\n"
    )
    result = OpencodeReviewMixin._enrich_discussion({}, [resolved_only], "LEX_REVIEW", True)
    assert result["verdict"] == "pass"
    assert not result.get("pending_threads")

    human_only = _write_doc(tmp_path / "human" / "spec.md", HUMAN_PENDING_DOC)
    result = OpencodeReviewMixin._enrich_discussion({}, [human_only], "LEX_REVIEW", True)
    assert result["verdict"] == PARK_VERDICT_NAME
    pending = result["pending_threads"]
    assert {entry["thread_id"] for entry in pending} == {"T-001", "T-002"}
    for entry in pending:
        assert set(entry) == {"doc", "thread_id", "summary"}
        assert entry["doc"] == "spec.md"
        assert isinstance(entry["summary"], str) and entry["summary"]

    other_owner = _write_doc(
        tmp_path / "other" / "spec.md",
        "> **Lex:** @Sage must rework this section.\n"
        "\n"
        "> **Lex [reopen]:** still waiting on the revision.\n",
    )
    result = OpencodeReviewMixin._enrich_discussion({}, [other_owner], "LEX_REVIEW", True)
    assert result["verdict"] == "revise"
    assert not result.get("pending_threads")


# AC-FR0332-01@v0.10 TRACKS-TRACE IF-REVIEW-001 verdict is server-computed
def test_review_exit_ignores_agent_self_reported_pass(tmp_path):
    doc = _write_doc(tmp_path / "spec.md", HUMAN_PENDING_DOC)

    result = OpencodeReviewMixin._enrich_discussion(
        {"verdict": "pass", "pending_threads": []}, [doc], "LEX_REVIEW", True
    )

    assert result["verdict"] == PARK_VERDICT_NAME
    assert result.get("pending_threads")


# -- kernel park reduction + State projection (machine_verdicts/machine) -----


# AC-FR0332-01@v0.10 TRACKS-TRACE IF-REVIEW-001 park reduction and projection
def test_park_verdict_reduces_to_awaiting_review_pending_threads():
    s = project(_spec_review_events())

    assert s.status == "awaiting_human"
    assert s.awaiting == "review_pending_threads"
    assert s.substate == "LEX_REVIEW"
    assert s.spec_committed is True
    assert s.lex_passed_this_round is False
    assert getattr(s, "pending_threads", None) == PARK_THREADS
    assert decide(s) is None  # parked: no further command until Human resumes


# AC-FR0332-01@v0.10 TRACKS-TRACE IF-REVIEW-001 trac review resumes re-review
def test_trac_review_resumes_the_lex_review():
    s = project(_spec_review_events(("human.review", {"action": "no_comment"})))

    assert s.status == "active"
    assert s.awaiting is None
    assert s.substate == "LEX_REVIEW"

    cmd = decide(s)
    assert cmd is not None and cmd.kind == "dispatch_agent"
    assert cmd.params.get("role") == "lex"
    assert cmd.params.get("substate") == "LEX_REVIEW"


# -- ResultCheckpoint diff policy: park is a legal no-diff verdict ------------


# AC-FR0332-01@v0.10 TRACKS-TRACE IF-REVIEW-001 park is a legal no-diff verdict
def test_requirement_review_payload_exempts_park_from_diff(tmp_path):
    repo = git_repo(tmp_path)
    store = Store(paths.tracks_home(repo))
    executor = Executor(store, repo, "RUN")
    executor.version = "v0.10"
    _versioned_doc(store, "v0.10", "# spec\n")

    state = State()
    state.stage = "M-SPEC"

    park = executor._requirement_review_payload(
        state, "LEX_REVIEW", "lex", _park_result(), "base", "r1"
    )
    domain_event = park["domain_event"]
    assert domain_event["type"] == "lex.verdict"
    assert domain_event["payload"]["verdict"] == PARK_VERDICT_NAME
    # §1s.1: the park payload carries the pending Human thread list so the
    # kernel State projection and the trac status listing replay it.
    assert domain_event["payload"].get("pending_threads") == _park_result()["pending_threads"]
    assert park["requires_diff"] is False

    revise = executor._requirement_review_payload(
        state, "LEX_REVIEW", "lex", {"verdict": "revise"}, "base", "r2"
    )
    assert revise["requires_diff"] is True
    store.close()


# AC-FR0332-01@v0.10 TRACKS-TRACE IF-REVIEW-001 park no-diff passes the audit
def test_validate_diff_policy_admits_park_without_diff(tmp_path):
    repo = git_repo(tmp_path)
    store = Store(paths.tracks_home(repo))
    executor = Executor(store, repo, "RUN")
    executor.version = "v0.10"
    doc = _versioned_doc(store, "v0.10", "# spec\n")
    rel_doc = doc.resolve().relative_to(Path(repo).resolve()).as_posix()
    subprocess.run(["git", "add", rel_doc], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "spec"], cwd=repo, check=True, capture_output=True)
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()

    park = Command(
        "validate_result",
        {
            "verdict": PARK_VERDICT_NAME,
            "requires_diff": True,
            "discussion_only": True,
            "result_id": "r1",
        },
        command_id="C1",
    )
    assert executor._validate_diff_policy(park, ["spec.md"], base_sha, 1) is False
    emitted = [event.type for event in store.events("RUN")]
    assert "verdict.failed" not in emitted
    assert "no_diff.detected" not in emitted

    revise = Command(
        "validate_result",
        {
            "verdict": "revise",
            "requires_diff": True,
            "discussion_only": True,
            "result_id": "r2",
        },
        command_id="C2",
    )
    assert executor._validate_diff_policy(revise, ["spec.md"], base_sha, 1) is True
    assert list(store.events("RUN"))[-1].type == "verdict.failed"
    store.close()


# -- status rendering (status_cmd.py) ----------------------------------------


# AC-FR0332-01@v0.10 TRACKS-TRACE IF-REVIEW-001 status pending list + recovery
def test_status_lists_pending_human_threads_with_recovery_pointer(tmp_path, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    store = Store(repo / ".tracks")
    store.append("RUN-LEX", "v0.10", "story.requested", {"raw_chars": 1})
    store.append("RUN-LEX", "v0.10", "stage.entered", {"stage": "M-SPEC"})
    store.append(
        "RUN-LEX",
        "v0.10",
        "spec.committed",
        {"commit_sha": "c1", "spec_sha": "s1", "final": False},
    )
    store.append("RUN-LEX", "v0.10", "lex.verdict", dict(PARK_VERDICT))

    rc = cmd_status(repo)
    out = capsys.readouterr().out
    store.close()

    assert rc == 0
    assert "review_pending_threads" in out
    assert "spec.md" in out
    assert "T-001" in out and "T-002" in out
    assert "stop condition" in out
    assert "coverage claim" in out
    assert "trac review" in out


# -- trac review park admission (tracks/cli/run_cmd.py) -----------------------


class _FakeStore:
    """Minimal store seam for cmd_review (the pipeline is faked below)."""

    def __init__(self, state):
        self._state = state

    def state(self, run_id):
        return self._state


@contextmanager
def _active_run(state):
    yield ("home", _FakeStore(state), "RUN")


# AC-FR0332-01@v0.10 TRACKS-TRACE IF-REVIEW-001 trac review admits the park resume
def test_cmd_review_admits_review_pending_threads(tmp_path, capsys, monkeypatch):
    repo = tmp_path / "host"
    repo.mkdir()
    state = SimpleNamespace(awaiting="review_pending_threads", stage="M-SPEC", version="v0.10")
    monkeypatch.setattr(run_cmd, "active_run_context", lambda repo_arg: _active_run(state))
    seen = {}

    def _fake_pipeline(repo_arg, state_arg, store, run_id, checkpoint):
        seen["checkpoint"] = checkpoint
        return 0, (SimpleNamespace(awaiting=None, last_failure=None), "review_pending_threads")

    monkeypatch.setattr(run_cmd, "_do_human_pipeline", _fake_pipeline)

    # §1s.3: the park awaiting is admissible — the Human handled the pending
    # threads and resumes the review through trac review (human.review).
    assert cmd_review(repo, "no-comment") == 0
    assert "review recorded: no_comment" in capsys.readouterr().out
    assert seen["checkpoint"].event_type == "human.review"

    # HUMAN_REVIEW semantics unchanged: awaiting=review stays admissible ...
    state.awaiting = "review"
    assert cmd_review(repo, "no-comment") == 0
    capsys.readouterr()

    # ... and every other awaiting keeps the existing rejection.
    state.awaiting = "escalation"
    assert cmd_review(repo, "no-comment") == 1
    assert "run not awaiting review" in capsys.readouterr().err
