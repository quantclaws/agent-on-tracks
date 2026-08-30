"""Integration tests for pre-commit hook rejection at commit time (D-30/F-1).

Executor-level: each commit site (_do_commit_document, _do_write_frontmatter,
_do_commit_tests) emits verdict.failed(check=commit) with the hook's combined
output as evidence, and returns cleanly instead of crashing.

Pipeline-level: a failing hook that passes on the second attempt lets the full
M-DESIGN walk recover via the normal WRITE redispatch path.
"""

from tests.integration.test_executor_reconcile import make_repo as _make_repo
from tracks import paths
from tracks.executor import Executor
from tracks.kernel.events import Command
from tracks.store import Store, new_ulid


def _install_failing_hook(repo, message="ruff: lint error"):
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(f"#!/bin/sh\necho '{message}' >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)


def _setup_story_draft(tmp_path):
    """Reuse _setup from test_executor_reconcile (repo+store+M-STORY DRAFT
    with doc_validated=True), returning (ex, store, run_id)."""
    from tests.integration.test_executor_reconcile import _setup

    return _setup(tmp_path)


def test_commit_document_hook_rejection_emits_verdict_failed(tmp_path):
    """_do_commit_document: hook rejects -> verdict.failed(check=commit) with
    hook stderr as evidence, no crash, no committed event."""
    ex, store, run_id = _setup_story_draft(tmp_path)
    _install_failing_hook(ex.repo, "ruff: 6 errors in story.md")
    ex.issue(
        Command(
            kind="commit_document", params={"doc": "story.md", "message": "M-STORY: draft story.md"}
        )
    )
    fails = [e for e in store.events(run_id) if e.type == "verdict.failed"]
    assert len(fails) == 1
    p = fails[0].payload
    assert p["check"] == "commit"
    assert p["reason"] == "pre-commit hook rejected the commit"
    assert "ruff" in p["evidence"]
    assert p["attempt"] == 1
    assert not [e for e in store.events(run_id) if e.type == "story.committed"]


def test_write_frontmatter_hook_rejection_emits_verdict_failed(tmp_path):
    """_do_write_frontmatter (EXIT seal): hook rejects -> verdict.failed
    (check=commit), no stage.exited event, no crash."""
    ex, store, run_id = _setup_story_draft(tmp_path)
    repo = ex.repo
    # advance to EXIT: commit (non-final), lex pass, human no_comment, exit gate
    store.append(
        run_id, "v0.1", "story.committed", {"commit_sha": "c1", "story_sha": "s1", "final": False}
    )
    store.append(
        run_id,
        "v0.1",
        "command.issued",
        {
            "command": {
                "kind": "dispatch_agent",
                "params": {"role": "sage", "substate": "SAGE_REVIEW"},
                "command_id": "C2",
            }
        },
        command_id="C2",
    )
    store.append(run_id, "v0.1", "outcome.received", {"role": "sage", "status": "done"})
    store.append(run_id, "v0.1", "sage.verdict", {"verdict": "pass"})
    store.append(run_id, "v0.1", "human.review", {"action": "no_comment"})
    store.append(
        run_id, "v0.1", "verdict.passed", {"check": "template,discussion_ready", "detail": "ok"}
    )
    _install_failing_hook(repo, "ruff: seal commit rejected")
    ex.issue(
        Command(
            kind="write_frontmatter", params={"doc": "story.md", "stage": "M-STORY", "field": "sha"}
        )
    )
    fails = [e for e in store.events(run_id) if e.type == "verdict.failed"]
    assert len(fails) == 1
    assert fails[0].payload["check"] == "commit"
    assert "ruff" in fails[0].payload["evidence"]
    assert not [e for e in store.events(run_id) if e.type == "stage.exited"]


def test_commit_tests_hook_rejection_emits_verdict_failed(tmp_path):
    """_do_commit_tests (M-TEST): freeze refuses a contaminated tests/ tree ->
    verdict.failed(check=test_freeze_contamination), no test.committed /
    stage.exited / run.completed, no crash.

    B59 (commit 944c0c0) legitimately evolved the M-TEST freeze rejection:
    the designed flow commits Shield's WRITE output during the pipeline
    itself, so at freeze time an UNCOMMITTED tests/ modification is residue
    the gate refuses (check=test_freeze_contamination) BEFORE any hook runs.
    The v0.4-era assertion (check=commit, hook-driven) described the
    pre-B59 path that a clean-tree synthetic freeze can no longer reach
    (no-op freeze commit short-circuits). The preserved contract semantics:
    the contaminated freeze is REJECTED fail-closed with a verdict.failed
    naming the offending tests/ paths, and no spurious freeze events fire.
    """
    repo = _make_repo(tmp_path)
    home = paths.tracks_home(repo)
    store = Store(home)
    run_id = new_ulid()
    tests_dir = repo / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_smoke.py").write_text(
        '"""AC-FR0010-01@v0.1"""\ndef test_x():\n    raise NotImplementedError("IF-X")\n',
        encoding="utf-8",
    )
    store.append(run_id, "v0.1", "story.requested", {"raw_chars": 1})
    store.append(run_id, "v0.1", "stage.entered", {"stage": "M-TEST"})
    store.append(
        run_id,
        "v0.1",
        "command.issued",
        {
            "command": {
                "kind": "dispatch_agent",
                "params": {"role": "shield", "substate": "WRITE"},
                "command_id": "C1",
            }
        },
        command_id="C1",
    )
    store.append(run_id, "v0.1", "outcome.received", {"role": "shield", "status": "done"})
    store.append(run_id, "v0.1", "verdict.passed", {"check": "trace", "detail": "closure verified"})
    _install_failing_hook(repo, "ruff: test files have lint errors")
    ex = Executor(store, repo, run_id)
    ex.issue(Command(kind="commit_tests", params={"stage": "M-TEST"}))
    fails = [e for e in store.events(run_id) if e.type == "verdict.failed"]
    assert len(fails) == 1
    p = fails[0].payload
    assert p["check"] == "test_freeze_contamination"
    # The residue evidence names the offending tests/ paths (the porcelain
    # entry for the untracked test tree -- the directory marker 'tests/').
    assert "tests/" in p["evidence"]
    assert not [
        e
        for e in store.events(run_id)
        if e.type in ("test.committed", "stage.exited", "run.completed")
    ]


def test_hook_rejection_recovery_full_pipeline(host_repo, trac, event_log):
    """D-30/F-1 regression: Archer deliverable (architecture.md) rejected by
    pre-commit hook -> verdict.failed with hook stderr -> Archer redispatched
    -> hook passes on second attempt -> commit succeeds -> run completes."""
    from tests.e2e.helpers import walk_to_await_human

    run_id = walk_to_await_human(trac)
    # Install a hook that rejects the first commit involving architecture.md,
    # then self-destructs so subsequent commits pass (the agent "fixed" the
    # deliverable on the redispatch).
    hook = host_repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(
        "#!/bin/sh\n"
        'if git diff --cached --name-only | grep -q "architecture.md"; then\n'
        '  rm -f "$0"\n'
        '  echo "ruff check: 6 errors in architecture.md" >&2\n'
        "  exit 1\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    assert trac("approve", "--actor", "Aaron").returncode == 0
    r = trac("run")
    assert r.returncode == 0, r.stderr
    assert "status=completed" in r.stdout
    evs = event_log(run_id)
    # The hook rejection emitted verdict.failed(check=commit) with hook output
    commit_fails = [
        e for e in evs if e["type"] == "verdict.failed" and e["payload"].get("check") == "commit"
    ]
    assert len(commit_fails) == 1
    assert "ruff" in commit_fails[0]["payload"]["evidence"]
    # The run still completed: architecture.md was committed on the second try
    arch_commits = [
        e
        for e in evs
        if e["type"] == "design.committed" and e["payload"].get("doc") == "architecture.md"
    ]
    assert len(arch_commits) >= 1
    assert any(e["type"] == "run.completed" for e in evs)
