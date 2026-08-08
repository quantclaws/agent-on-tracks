"""ResultCheckpoint pipeline: M-SPEC/M-ACC Lex review, M-TEST Shield WRITE,
M-DESIGN Prism, and _reject_dirty_staged tests.

Split from ``test_result_checkpoint.py`` for module-size compliance (C0302).
"""
import pytest

from tests.integration.result_checkpoint_support import (
    _setup_design,
    _setup_m_test,
    _setup_spec_stage,
    _ShieldBackend,
    _step_to_publish_and_crash,
    _StubBackend,
    g,
    make_repo,
)
from tracks.kernel.events import Command
from tracks.store import new_ulid

# -- M-SPEC / M-ACC Lex review smoke (parameterized) ----------------------


@pytest.mark.parametrize("stage,doc,committed_event", [
    ("M-SPEC", "spec.md", "spec.committed"),
    ("M-ACC", "acceptance.md", "acceptance.committed"),
])
@pytest.mark.parametrize("verdict", ["pass", "comment"])
def test_lex_review_smoke(tmp_path, stage, doc, committed_event, verdict):
    """M-SPEC/M-ACC Lex reviewer pass/comment smoke: DRAFT pipeline commits
    the doc, Lex review with pass/comment produces the correct verdict and
    transition."""
    ex, store, run_id = _setup_spec_stage(tmp_path, stage)

    ex.backend = _StubBackend({
        "status": "done", "artifact_ref": doc, "self_report": "draft",
    })
    draft_cmd = Command(
        kind="dispatch_agent",
        params={"role": "sage", "substate": "DRAFT", "doc": doc,
                "stage": stage, "attempt": 1, "review_round": 1},
        command_id=new_ulid(),
    )
    ex._do_dispatch_agent(draft_cmd, store.state(run_id), None, False)
    ex.run_pipeline()

    events = [e.type for e in store.events(run_id)]
    assert committed_event in events
    assert store.state(run_id).substate == "LEX_REVIEW"

    if verdict == "comment":
        doc_path = ex._doc_path(doc)
        text = doc_path.read_text(encoding="utf-8")
        doc_path.write_text(
            text + "\n\n> **Lex:** 需要补充细节。\n", encoding="utf-8")

    ex.backend = _StubBackend({
        "status": "done", "artifact_ref": None, "self_report": "review",
        "verdict": verdict,
    })
    review_cmd = Command(
        kind="dispatch_agent",
        params={"role": "lex", "substate": "LEX_REVIEW", "doc": doc,
                "stage": stage, "attempt": 1, "review_round": 1},
        command_id=new_ulid(),
    )
    ex._do_dispatch_agent(review_cmd, store.state(run_id), None, False)
    ex.run_pipeline()

    verdicts = [e for e in store.events(run_id) if e.type == "lex.verdict"]
    assert verdicts[-1].payload["verdict"] == verdict
    state = store.state(run_id)
    if verdict == "pass":
        assert state.substate == "HUMAN_REVIEW"
        assert state.awaiting == "review"
    else:
        assert state.substate == "RESPOND"


# -- _reject_dirty_staged unit tests (item 4: outside dirty, pre-staged) ---


def test_reject_dirty_staged_outside_allowlist(tmp_path):
    """_reject_dirty_staged rejects dirty files outside the version dir."""
    from tracks.cli.main import _reject_dirty_staged
    repo = make_repo(tmp_path)
    (repo / "outside.txt").write_text("dirty\n", encoding="utf-8")
    rc = _reject_dirty_staged(repo, "v0.1", "triage")
    assert rc != 0


def test_reject_dirty_staged_pre_staged(tmp_path):
    """_reject_dirty_staged rejects pre-staged content."""
    from tracks.cli.main import _reject_dirty_staged
    repo = make_repo(tmp_path)
    vdir = repo / ".tracks" / "projects" / "v0.1"
    vdir.mkdir(parents=True)
    (vdir / "story.md").write_text("x\n", encoding="utf-8")
    g(repo, "add", str(vdir / "story.md"))
    rc = _reject_dirty_staged(repo, "v0.1", "review")
    assert rc != 0


def test_reject_dirty_staged_clean(tmp_path):
    """_reject_dirty_staged returns 0 when clean."""
    from tracks.cli.main import _reject_dirty_staged
    repo = make_repo(tmp_path)
    rc = _reject_dirty_staged(repo, "v0.1", "triage")
    assert rc == 0


def test_reject_dirty_staged_allows_version_dir_dirty(tmp_path):
    """_reject_dirty_staged allows dirty files inside the version dir."""
    from tracks.cli.main import _reject_dirty_staged
    repo = make_repo(tmp_path)
    vdir = repo / ".tracks" / "projects" / "v0.1"
    vdir.mkdir(parents=True)
    (vdir / ".gitkeep").write_text("", encoding="utf-8")
    g(repo, "add", ".tracks")
    g(repo, "commit", "-m", "init tracks")
    (vdir / "story.md").write_text("x\n", encoding="utf-8")
    rc = _reject_dirty_staged(repo, "v0.1", "triage")
    assert rc == 0


# -- M-TEST Shield WRITE pipeline -------------------------------------------


def test_m_test_shield_write_pipeline_publishes_test_written(tmp_path):
    """M-TEST Shield WRITE: pipeline validates (write_scope+collection),
    checkpoints test files, publishes test.written, transitions WRITE -> COLLECT."""
    ex, store, run_id = _setup_m_test(tmp_path)
    repo = ex.repo

    ex.backend = _ShieldBackend(repo, {
        "tests/integration/test_a.py": "def test_a():\n    assert True\n",
    })
    cmd = Command(
        kind="dispatch_agent",
        params={"role": "shield", "substate": "WRITE", "stage": "M-TEST",
                "attempt": 1, "review_round": 1,
                "assignment": {"kind": "WRITE", "skills": ["tracks-discuz"],
                               "docs": ["test-plan.md", "interfaces.md",
                                        "acceptance.md"]}},
        command_id=new_ulid(),
    )
    ex.issue(cmd)
    ex.run_pipeline()

    events = [e.type for e in store.events(run_id)]
    assert "outcome.received" in events
    assert "result.validated" in events
    assert "result.checkpointed" in events
    assert "test.written" in events
    state = store.state(run_id)
    assert state.active_result is None
    assert state.substate == "COLLECT"
    written = [e for e in store.events(run_id) if e.type == "test.written"]
    assert written[0].payload["commit_sha"]
    names = g(repo, "show", "--format=", "--name-only", "HEAD").splitlines()
    assert "tests/integration/test_a.py" in names


def test_m_test_shield_write_checkpoint_creates_commit(tmp_path):
    """The checkpoint step creates a real git commit for test files with the
    command_id marker."""
    ex, store, run_id = _setup_m_test(tmp_path)
    repo = ex.repo
    base_sha = g(repo, "rev-parse", "HEAD").strip()

    ex.backend = _ShieldBackend(repo, {
        "tests/integration/test_a.py": "def test_a():\n    pass\n",
    })
    cmd = Command(
        kind="dispatch_agent",
        params={"role": "shield", "substate": "WRITE", "stage": "M-TEST",
                "attempt": 1, "review_round": 1,
                "assignment": {"kind": "WRITE", "skills": ["tracks-discuz"],
                               "docs": ["test-plan.md", "interfaces.md",
                                        "acceptance.md"]}},
        command_id=new_ulid(),
    )
    ex.issue(cmd)
    ex.run_pipeline()

    checkpointed = [e for e in store.events(run_id)
                    if e.type == "result.checkpointed"]
    assert checkpointed[0].payload["created_commit"] is True
    assert checkpointed[0].payload["commit_sha"] != base_sha
    log = g(repo, "log", "-1", "--format=%B")
    assert "command_id:" in log


def test_m_test_shield_write_crash_recovery(tmp_path):
    """Crash after checkpoint commit but before test.written: _recover()
    re-executes publish_result, emitting test.written exactly once."""
    ex, store, run_id = _setup_m_test(tmp_path)
    repo = ex.repo

    ex.backend = _ShieldBackend(repo, {
        "tests/integration/test_a.py": "def test_a():\n    pass\n",
    })
    cmd = Command(
        kind="dispatch_agent",
        params={"role": "shield", "substate": "WRITE", "stage": "M-TEST",
                "attempt": 1, "review_round": 1,
                "assignment": {"kind": "WRITE", "skills": ["tracks-discuz"],
                               "docs": ["test-plan.md", "interfaces.md",
                                        "acceptance.md"]}},
        command_id=new_ulid(),
    )
    ex.issue(cmd)

    _step_to_publish_and_crash(ex, store, run_id)

    assert not [e for e in store.events(run_id) if e.type == "test.written"]

    ex._recover()

    written = [e for e in store.events(run_id) if e.type == "test.written"]
    assert len(written) == 1
    state = store.state(run_id)
    assert state.active_result is None
    assert state.substate == "COLLECT"


# -- M-DESIGN Prism pipeline -------------------------------------------------


def test_m_design_prism_pass_pipeline(tmp_path):
    """M-DESIGN Prism pass: pipeline validates 3 docs (template), no-change
    checkpoint, publishes prism.verdict(pass), transitions to EXIT."""
    ex, store, run_id = _setup_design(tmp_path)

    ex.backend = _StubBackend({
        "status": "done", "artifact_ref": None, "self_report": "review",
        "verdict": "pass",
    })
    cmd = Command(
        kind="dispatch_agent",
        params={"role": "prism", "substate": "PRISM_REVIEW",
                "stage": "M-DESIGN", "attempt": 1, "review_round": 1,
                "assignment": {"kind": "PRISM_REVIEW",
                               "docs": ["architecture.md", "interfaces.md",
                                        "test-plan.md"]}},
        command_id=new_ulid(),
    )
    ex.issue(cmd)
    ex.run_pipeline()

    events = [e.type for e in store.events(run_id)]
    assert "result.validated" in events
    assert "result.checkpointed" in events
    assert "prism.verdict" in events
    state = store.state(run_id)
    assert state.active_result is None
    assert state.substate == "EXIT"
    verdicts = [e for e in store.events(run_id) if e.type == "prism.verdict"]
    assert verdicts[-1].payload["verdict"] == "pass"
    checkpointed = [e for e in store.events(run_id)
                    if e.type == "result.checkpointed"]
    assert checkpointed[0].payload["created_commit"] is False


def test_m_design_prism_revise_pipeline(tmp_path):
    """M-DESIGN Prism revise: pipeline validates, checkpoints (discussion diff),
    publishes prism.verdict(revise) + review.round_started, transitions to RESPOND."""
    ex, store, run_id = _setup_design(tmp_path)

    arch_path = ex._doc_path("architecture.md")
    text = arch_path.read_text(encoding="utf-8")
    arch_path.write_text(
        text + "\n\n> **Prism:** 需要补充安全设计。\n", encoding="utf-8")

    ex.backend = _StubBackend({
        "status": "done", "artifact_ref": None, "self_report": "review",
        "verdict": "revise",
    })
    cmd = Command(
        kind="dispatch_agent",
        params={"role": "prism", "substate": "PRISM_REVIEW",
                "stage": "M-DESIGN", "attempt": 1, "review_round": 1,
                "assignment": {"kind": "PRISM_REVIEW",
                               "docs": ["architecture.md", "interfaces.md",
                                        "test-plan.md"]}},
        command_id=new_ulid(),
    )
    ex.issue(cmd)
    ex.run_pipeline()

    events = [e.type for e in store.events(run_id)]
    assert "prism.verdict" in events
    assert "review.round_started" in events
    state = store.state(run_id)
    assert state.active_result is None
    assert state.substate == "RESPOND"
    verdicts = [e for e in store.events(run_id) if e.type == "prism.verdict"]
    assert verdicts[-1].payload["verdict"] == "revise"
    checkpointed = [e for e in store.events(run_id)
                    if e.type == "result.checkpointed"]
    assert checkpointed[0].payload["created_commit"] is True
    rounds = [e for e in store.events(run_id)
              if e.type == "review.round_started"]
    assert rounds[-1].payload["round"] == 2
