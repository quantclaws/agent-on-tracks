"""M-DESIGN architecture commits own the declared host scaffold."""
import stat
import subprocess

import pytest

from tests.e2e.helpers import walk_to_await_human
from tests.e2e_live.harness import SCENARIO_DIR
from tests.e2e_live.m_test_helpers import _assert_design_exit_adjacency
from tests.integration.test_executor_reconcile import _setup as setup_story
from tests.integration.test_executor_reconcile import make_repo
from tracks import paths
from tracks.executor import Executor
from tracks.kernel.events import Command
from tracks.store import Store, new_ulid


def _git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


def _setup_design(tmp_path):
    repo = make_repo(tmp_path)
    home = paths.tracks_home(repo)
    vdir = paths.version_dir(home, "v0.4")
    store = Store(home)
    run_id = new_ulid()
    vdir.mkdir(parents=True)
    store.append(run_id, "v0.4", "story.requested", {"raw_chars": 1})
    store.append(run_id, "v0.4", "stage.entered", {"stage": "M-DESIGN"})
    return Executor(store, repo, run_id), store, run_id, vdir


def _architecture(*declared):
    bullets = "\n".join(
        f"- {path} \u2014 declared scaffold" for path in declared
    )
    return (
        "---\nsha:\n---\n\n"
        "## 2. Scaffold \u5ba3\u8a00\n\n"
        f"{bullets}\n\n"
        "## 3. Next section\n"
    )


def _commit_command(command_id):
    return Command(
        kind="commit_document",
        params={"doc": "architecture.md", "message": "M-DESIGN: draft architecture.md"},
        command_id=command_id,
    )


def test_architecture_commit_stages_declared_scaffold(tmp_path):
    ex, store, run_id, vdir = _setup_design(tmp_path)
    architecture = vdir / "architecture.md"
    scaffold = ex.repo / "scaffold.cfg"
    architecture.write_text(_architecture("scaffold.cfg"), encoding="utf-8")
    scaffold.write_text("config\n", encoding="utf-8")

    ex.issue(_commit_command(new_ulid()))

    committed = [e for e in store.events(run_id) if e.type == "design.committed"]
    assert len(committed) == 1
    names = _git(ex.repo, "show", "--format=", "--name-only", "HEAD").splitlines()
    assert sorted(names) == [".tracks/projects/v0.4/architecture.md", "scaffold.cfg"]


@pytest.mark.parametrize(
    ("declared", "setup", "evidence"),
    [
        ("missing.cfg", lambda repo: None, "missing"),
        ("../outside.cfg", lambda repo: None, "escapes repository"),
        ("scaffold-dir", lambda repo: (repo / "scaffold-dir").mkdir(), "directory"),
        (
            ".opencode/generated.md",
            lambda repo: (
                (repo / ".opencode").mkdir(),
                (repo / ".opencode" / "generated.md").write_text("x\n"),
            ),
            "not allowed",
        ),
    ],
)
def test_invalid_declared_scaffold_fails_closed(
    tmp_path, declared, setup, evidence
):
    ex, store, run_id, vdir = _setup_design(tmp_path)
    architecture = vdir / "architecture.md"
    architecture.write_text(_architecture(declared), encoding="utf-8")
    setup(ex.repo)
    before = _git(ex.repo, "rev-list", "--count", "HEAD")

    ex.issue(_commit_command(new_ulid()))

    failures = [e for e in store.events(run_id) if e.type == "verdict.failed"]
    assert len(failures) == 1
    assert failures[0].payload["check"] == "commit"
    assert failures[0].payload["reason"] == "declared scaffold path rejected"
    assert evidence in failures[0].payload["evidence"]
    assert not [e for e in store.events(run_id) if e.type == "design.committed"]
    assert _git(ex.repo, "rev-list", "--count", "HEAD") == before
    assert _git(ex.repo, "diff", "--cached", "--name-only") == ""


def test_non_design_commit_does_not_stage_manifest_files(tmp_path):
    ex, store, run_id = setup_story(tmp_path)
    repo = ex.repo
    scaffold = repo / "scaffold.cfg"
    scaffold.write_text("config\n", encoding="utf-8")

    ex.issue(
        Command(
            kind="commit_document",
            params={"doc": "story.md", "message": "M-STORY: draft story.md"},
            command_id=new_ulid(),
        )
    )

    assert _git(repo, "show", "--format=", "--name-only", "HEAD").splitlines() == [
        ".tracks/projects/v0.1/story.md"
    ]
    assert not _git(repo, "status", "--short").startswith("A  scaffold.cfg")
    assert (repo / "scaffold.cfg").exists()


def test_reconcile_design_commit_only_backfills_event(tmp_path):
    ex, store, run_id, vdir = _setup_design(tmp_path)
    architecture = vdir / "architecture.md"
    scaffold = ex.repo / "scaffold.cfg"
    architecture.write_text(_architecture("scaffold.cfg"), encoding="utf-8")
    scaffold.write_text("config\n", encoding="utf-8")
    command_id = new_ulid()
    command = _commit_command(command_id)
    _git(ex.repo, "add", str(architecture), str(scaffold))
    _git(ex.repo, "commit", "-m", f"M-DESIGN: draft architecture.md\n\ncommand_id: {command_id}")
    before = _git(ex.repo, "rev-list", "--count", "HEAD")
    store.append(
        run_id,
        "v0.4",
        "command.issued",
        {"command": {"kind": command.kind, "params": command.params,
                     "command_id": command_id}},
        command_id=command_id,
    )

    Executor(store, ex.repo, run_id)._recover()

    assert _git(ex.repo, "rev-list", "--count", "HEAD") == before
    committed = [e for e in store.events(run_id) if e.type == "design.committed"]
    assert len(committed) == 1
    assert committed[0].payload["commit_sha"] == _git(ex.repo, "rev-parse", "HEAD").strip()


def test_architecture_scaffold_hook_rejection_keeps_commit_evidence(tmp_path):
    ex, store, run_id, vdir = _setup_design(tmp_path)
    architecture = vdir / "architecture.md"
    scaffold = ex.repo / "scaffold.cfg"
    architecture.write_text(_architecture("scaffold.cfg"), encoding="utf-8")
    scaffold.write_text("config\n", encoding="utf-8")
    hook = ex.repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'ruff: scaffold error' >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)

    ex.issue(_commit_command(new_ulid()))

    failures = [e for e in store.events(run_id) if e.type == "verdict.failed"]
    assert len(failures) == 1
    assert failures[0].payload["check"] == "commit"
    assert failures[0].payload["reason"] == "pre-commit hook rejected the commit"
    assert "ruff: scaffold error" in failures[0].payload["evidence"]
    assert _git(ex.repo, "diff", "--cached", "--name-only").splitlines() == [
        ".tracks/projects/v0.4/architecture.md",
        "scaffold.cfg",
    ]
    assert not [e for e in store.events(run_id) if e.type == "design.committed"]


def test_fake_design_tail_materializes_declared_cli_before_m_test_dispatch(
    trac, event_log, host_repo
):
    run_id = walk_to_await_human(trac)
    stub = host_repo / "code-stats"
    assert not stub.exists()
    assert _git(host_repo, "status", "--porcelain", "--", "code-stats") == ""

    assert trac("approve", "--actor", "Aaron").returncode == 0
    design = trac(
        "run",
        "--assignment-overlay",
        str(SCENARIO_DIR / "archer-design-draft.json"),
        "--max-dispatches",
        "2",
    )

    assert design.returncode == 0, design.stderr
    assert "stage=M-DESIGN" in design.stdout
    assert "substate=PRISM_REVIEW" in design.stdout
    review = trac("run", "--max-dispatches", "1")
    assert review.returncode == 0, review.stderr
    assert "stage=M-TEST" in review.stdout
    assert "substate=DISPATCH" in review.stdout
    assert "status=active" in review.stdout
    version_dir = host_repo / ".tracks" / "projects" / "v0.1"
    for name in ("architecture.md", "interfaces.md", "test-plan.md"):
        assert (version_dir / name).is_file(), name
    assert stub.read_text(encoding="utf-8") == (
        "#!/usr/bin/env python3\n"
        "raise NotImplementedError(\"IF-MTEST-001 code-stats CLI\")\n"
    )
    assert stub.stat().st_mode & stat.S_IXUSR
    assert _git(host_repo, "ls-files", "--error-unmatch", "code-stats").strip() == (
        "code-stats"
    )
    assert _git(host_repo, "status", "--porcelain", "--", "code-stats") == ""

    events = event_log(run_id)
    _assert_design_exit_adjacency(events)
    scaffold_commit = _git(
        host_repo, "log", "-1", "--format=%H", "--", "code-stats"
    ).strip()
    committed_paths = _git(
        host_repo, "show", "--format=", "--name-only", scaffold_commit
    ).splitlines()
    assert sorted(committed_paths) == [
        ".tracks/project/project.toml",
        ".tracks/projects/v0.1/architecture.md",
        "code-stats",
    ]
