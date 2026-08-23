"""Shared helpers for ResultCheckpoint pipeline integration tests.

Extracted from ``test_result_checkpoint.py`` for module-size compliance (C0302).
"""

import sys

from tests.integration.helpers import g, make_repo
from tracks import paths, templating
from tracks.effects.fake import FakeBackend
from tracks.executor import Executor
from tracks.store import Store, new_ulid


class _StubBackend:
    """Test double returning a fixed outcome."""

    def __init__(self, outcome):
        self._outcome = outcome

    def act(self, role, substate, doc, doc_path, assignment=None, worktree=None):
        return self._outcome


class _ShieldBackend:
    """Backend that writes test files during act(), then returns done."""

    def __init__(self, repo, files, outcome=None):
        self._repo = repo
        self._files = files
        self._outcome = outcome

    def act(self, role, substate, doc, doc_path, assignment=None, worktree=None):
        for rel, content in self._files.items():
            path = self._repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        if self._outcome is not None:
            return self._outcome
        return {
            "status": "done",
            "artifact_ref": "tests",
            "self_report": "wrote",
            "artifact_manifest": {
                "include": [
                    {"path": rel, "kind": "test_asset", "role": "integration"}
                    for rel in self._files
                ]
            },
            "suggested_commit_message": "M-TEST: shield suggested commit",
        }


def _init_workspace(tmp_path, version="v0.1"):
    """Create repo, store, run_id, and version dir — shared setup boilerplate."""
    repo = make_repo(tmp_path)
    home = paths.tracks_home(repo)
    store = Store(home)
    run_id = new_ulid()
    vdir = paths.version_dir(home, version)
    vdir.mkdir(parents=True)
    return repo, home, store, run_id, vdir


def _setup(tmp_path, version="v0.1"):
    repo, home, store, run_id, vdir = _init_workspace(tmp_path, version)
    (vdir / "story.md").write_text(
        templating.render_story_skeleton("做一个X", "2026-07-31"), encoding="utf-8"
    )
    store.append(run_id, version, "story.requested", {"raw_chars": 1})
    store.append(run_id, version, "stage.entered", {"stage": "M-STORY"})
    return Executor(store, repo, run_id), store, run_id


def _valid_spec_text():
    return (
        "---\n"
        "spec_id: SPEC-001\n"
        "created: 2026-01-01\n"
        "status: draft\n"
        "sha:\n"
        "---\n\n"
        "# Test — 需求规格\n\n"
        "## 功能需求\n\n"
        "### FR-0001 测试需求\n\n"
        "- **来源**：story §目标\n"
        "- **交付入口**：无独立入口\n\n"
        "测试描述。\n\n"
        "## 非功能需求\n\n"
        "### NFR-0001 确定性\n\n"
        "- **来源**：story §目标\n\n"
        "确定性输出。\n"
    )


def _valid_acceptance_text():
    return (
        "---\n"
        "acc_id: ACC-001\n"
        "created: 2026-01-01\n"
        "status: draft\n"
        "sha:\n"
        "---\n\n"
        "# Test — 验收标准\n\n"
        "## FR-0001 测试需求\n\n"
        "### AC-FR0001-01\n\n"
        "  - 条件：测试需求可在系统外断言\n\n"
        "## NFR-0001 确定性\n\n"
        "### AC-NFR0001-01\n\n"
        "  - 条件：确定性输出可断言\n"
    )


def _setup_spec_stage(tmp_path, stage):
    """Set up M-SPEC or M-ACC with valid docs on disk."""
    repo, home, store, run_id, vdir = _init_workspace(tmp_path)
    (vdir / "spec.md").write_text(_valid_spec_text(), encoding="utf-8")
    if stage == "M-ACC":
        (vdir / "acceptance.md").write_text(_valid_acceptance_text(), encoding="utf-8")
    store.append(run_id, "v0.1", "story.requested", {"raw_chars": 1})
    store.append(run_id, "v0.1", "stage.entered", {"stage": stage})
    return Executor(store, repo, run_id), store, run_id


def _setup_m_test(tmp_path):
    """Set up M-TEST stage with test-plan.md and acceptance.md on disk."""
    repo, home, store, run_id, vdir = _init_workspace(tmp_path, "v0.4")
    contract_path = paths.project_toml_path(home)
    contract_path.parent.mkdir(parents=True, exist_ok=True)

    def section(name):
        return (
            f"[{name}]\n"
            'framework = "pytest"\n'
            f'paths = ["tests/{name}/"]\n'
            f'collect = "{sys.executable} -m pytest --collect-only -q tests/{name}/"\n'
            f'run = "{sys.executable} -m pytest -q tests/{name}/ --junitxml={{result}}"\n'
            f'run_selected = "{sys.executable} -m pytest {{nodes}} -q '
            '--junitxml={result}"\n'
            'cwd = "."\n\n'
        )

    contract_path.write_text(
        section("unit")
        + section("integration")
        + section("e2e")
        + '[nightly]\n'
        'schedule = "0 3 * * *"\n'
        'workflow = ".github/workflows/nightly.yml"\n'
        'job = "nightly-regression"\n'
        'layers = ["unit", "integration", "e2e"]\n'
        'purpose = "current FULL suite; not a local gate"\n\n'
        '[layout.shield]\n'
        'writable = ["tests/"]\n',
        encoding="utf-8",
    )
    # The legacy support-only collection gate executes integration + e2e.
    # Keep e2e a legal empty declared layer (pytest rc=5), while leaving the
    # integration path absent so tests can still exercise its fail-closed case.
    (repo / "tests" / "e2e").mkdir(parents=True, exist_ok=True)
    (vdir / "test-plan.md").write_text(
        "# Test Plan\n\n## 8. AC Coverage\n\n"
        "| AC id | layer | test | IF |\n|---|---|---|---|\n"
        "| AC-FR0010-01 | integration | test_a | IF-MTEST-001 |\n",
        encoding="utf-8",
    )
    (vdir / "acceptance.md").write_text(
        "# Acceptance\n\n## FR-0010\n\n### AC-FR0010-01\n\n  - c\n",
        encoding="utf-8",
    )
    store.append(run_id, "v0.4", "story.requested", {"raw_chars": 1})
    store.append(run_id, "v0.4", "stage.entered", {"stage": "M-TEST"})
    # D-41 v3 entry order: the pre-WRITE R1 snapshot capture precedes the
    # first Shield WRITE dispatch.
    store.append(
        run_id,
        "v0.4",
        "command.issued",
        {"command": {"kind": "capture_baseline", "params": {"stage": "M-TEST"}}},
    )
    store.append(
        run_id,
        "v0.4",
        "test.baseline_captured",
        {
            "status": "passed",
            "baseline_id": "b0",
            "baseline_tree": "t0",
            "layers": ["unit", "integration", "e2e"],
            "nodes_count": 0,
            "empty_baseline": True,
            "node_digest_blob": None,
            "errors": [],
        },
    )
    return Executor(store, repo, run_id), store, run_id


def _recover_and_artifacts(ex, store, run_id, original_execute):
    """Restore _execute, run crash recovery, return result_checkpoint
    artifacts. Shared by crash-attribution test suites."""
    ex._execute = original_execute
    ex._recover()
    outcomes = [e for e in store.events(run_id) if e.type == "outcome.received"]
    assert outcomes, "outcome.received must be emitted after recovery"
    rc = outcomes[-1].payload.get("result_checkpoint", {})
    return rc.get("artifacts", [])


def _setup_design(tmp_path):
    """Set up M-DESIGN stage with 3 template-compliant design docs on disk,
    DRAFT committed, state in PRISM_REVIEW (ready for Prism dispatch)."""
    repo, home, store, run_id, vdir = _init_workspace(tmp_path, "v0.4")
    fake = FakeBackend.__new__(FakeBackend)
    for doc, kind in [
        ("architecture.md", "architecture"),
        ("interfaces.md", "interfaces"),
        ("test-plan.md", "test-plan"),
    ]:
        (vdir / doc).write_text(fake._design_doc(kind), encoding="utf-8")
    store.append(run_id, "v0.4", "story.requested", {"raw_chars": 1})
    store.append(run_id, "v0.4", "stage.entered", {"stage": "M-DESIGN"})
    for doc in ("architecture.md", "interfaces.md", "test-plan.md"):
        g(repo, "add", str(vdir / doc))
    g(repo, "commit", "-m", "M-DESIGN: draft trio")
    commit_sha = g(repo, "rev-parse", "HEAD").strip()
    for doc in ("architecture.md", "interfaces.md", "test-plan.md"):
        store.append(
            run_id,
            "v0.4",
            "design.committed",
            {"doc": doc, "commit_sha": commit_sha, "final": False},
        )
    return Executor(store, repo, run_id), store, run_id


def _setup_draft_committed(tmp_path):
    """Run DRAFT pipeline to commit story.md and reach SAGE_REVIEW."""
    ex, store, run_id = _setup(tmp_path)
    _dispatch_draft(ex, store, run_id)
    assert store.state(run_id).substate == "SAGE_REVIEW"
    return ex, store, run_id


def _dispatch_sage_review(ex, store, run_id, verdict):
    """Dispatch a sage review with the given verdict and run the pipeline."""
    from tracks.kernel.events import Command

    ex.backend = _StubBackend(
        {
            "status": "done",
            "artifact_ref": None,
            "self_report": "review",
            "verdict": verdict,
        }
    )
    review_cmd = Command(
        kind="dispatch_agent",
        params={
            "role": "sage",
            "substate": "SAGE_REVIEW",
            "doc": "story.md",
            "stage": "M-STORY",
            "attempt": 1,
            "review_round": 1,
        },
        command_id=new_ulid(),
    )
    repo = ex.repo
    head_before = g(repo, "rev-parse", "HEAD").strip()
    ex._do_dispatch_agent(review_cmd, store.state(run_id), None, False)
    ex.run_pipeline()
    head_after = g(repo, "rev-parse", "HEAD").strip()
    return head_before, head_after


def _dispatch_draft(ex, store, run_id, run_pipeline=True):
    """Dispatch a scribe DRAFT for M-STORY and optionally run the pipeline."""
    from tracks.kernel.events import Command

    ex.backend = _StubBackend(
        {
            "status": "done",
            "artifact_ref": "story.md",
            "self_report": "draft",
        }
    )
    cmd = Command(
        kind="dispatch_agent",
        params={
            "role": "scribe",
            "substate": "DRAFT",
            "doc": "story.md",
            "stage": "M-STORY",
            "attempt": 1,
            "review_round": 1,
        },
        command_id=new_ulid(),
    )
    ex._do_dispatch_agent(cmd, store.state(run_id), None, False)
    if run_pipeline:
        ex.run_pipeline()


def _submit_triage(ex, store, run_id, artifacts=None, checks=None):
    """Submit a human triage result and run the pipeline."""
    ex.submit_human_result(
        state=store.state(run_id),
        domain_event_type="human.triage",
        payload={"actor": "T"},
        verdict="go",
        artifacts=artifacts or [],
        allowed_paths=["story.md"],
        checks=checks or [],
        requires_diff=False,
        commit_label="M-STORY: human triage (go)",
    )
    ex.run_pipeline()


def _walk_to_human_review(ex, store, run_id):
    """Run DRAFT + sage review pass to reach HUMAN_REVIEW."""
    _dispatch_draft(ex, store, run_id)
    _dispatch_sage_review(ex, store, run_id, "pass")
    assert store.state(run_id).awaiting == "review"


def _step_validate(ex, store, run_id):
    """Step the validate_result command: decide, progress, issue. Returns
    the refreshed state."""
    from tracks.kernel.machine import decide

    state = store.state(run_id)
    validate_cmd = decide(state)
    assert validate_cmd.kind == "validate_result"
    ex._progress(validate_cmd, state)
    ex.issue(validate_cmd)
    return store.state(run_id)


def _step_to_publish_and_crash(ex, store, run_id, crash_event="publish_result"):
    """Manually step validate_result + checkpoint_result, then crash at the
    given command kind (default publish_result). Returns the original _execute
    so the caller can restore it for _recover()."""
    from tracks.kernel.machine import decide

    state = _step_validate(ex, store, run_id)
    checkpoint_cmd = decide(state)
    assert checkpoint_cmd.kind == "checkpoint_result"
    ex._progress(checkpoint_cmd, state)
    ex.issue(checkpoint_cmd)

    original_execute = ex._execute

    def _crash(executor_self, cmd, state, task_id, reconcile=False):
        if cmd.kind == crash_event:
            return
        original_execute(cmd, state, task_id, reconcile)

    ex._execute = lambda cmd, state, task_id=None, reconcile=False: _crash(
        ex, cmd, state, task_id, reconcile
    )

    state = store.state(run_id)
    crash_cmd = decide(state)
    assert crash_cmd.kind == crash_event
    ex._progress(crash_cmd, state)
    ex.issue(crash_cmd)

    ex._execute = original_execute
    return original_execute
