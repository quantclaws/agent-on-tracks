"""Executor reconcile (D-13, AC-29d): commit succeeded, result event lost —
recovery probes git by command_id, skips the commit, only backfills the event.
"""
import subprocess

from tracks import paths
from tracks.executor import Executor
from tracks.kernel.events import Command
from tracks.store import Store, new_ulid


def g(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


def make_repo(tmp_path):
    repo = tmp_path / "host"
    repo.mkdir()
    g(repo, "init", "-b", "main")
    g(repo, "config", "user.email", "t@example.com")
    g(repo, "config", "user.name", "T")
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    g(repo, "add", "README.md")
    g(repo, "commit", "-m", "initial")
    return repo


def test_reconcile_commit_document_skips_existing_commit(tmp_path):
    repo = make_repo(tmp_path)
    home = paths.tracks_home(repo)
    store = Store(home)
    run_id, cid = new_ulid(), new_ulid()

    vdir = paths.version_dir(home, "v0.1")
    vdir.mkdir(parents=True)
    story = vdir / "story.md"
    story.write_text("---\nsha:\n---\n\n# 目标\n", encoding="utf-8")

    # crash scenario: the git commit already carries the command_id marker,
    # but story.committed never reached the event log.
    g(repo, "add", str(story))
    g(repo, "commit", "-m", f"M-STORY: draft story.md\n\ncommand_id: {cid}")
    commit_sha = g(repo, "rev-parse", "HEAD").strip()

    store.append(run_id, "v0.1", "story.requested", {"raw_chars": 1})
    store.append(run_id, "v0.1", "stage.entered", {"stage": "M-STORY"})
    store.append(
        run_id, "v0.1", "command.issued",
        {"command": {"kind": "commit_document",
                     "params": {"doc": "story.md",
                                "message": "M-STORY: draft story.md"},
                     "command_id": cid}},
        command_id=cid,
    )

    commits_before = g(repo, "rev-list", "--count", "HEAD").strip()
    Executor(store, repo, run_id)._recover()

    # no duplicate / empty commit
    assert g(repo, "rev-list", "--count", "HEAD").strip() == commits_before
    committed = [e for e in store.events(run_id) if e.type == "story.committed"]
    assert len(committed) == 1
    assert committed[0].payload["commit_sha"] == commit_sha
    assert committed[0].command_id == cid


def test_reconcile_create_branch_skips_existing(tmp_path):
    """R3-03: branch already created & checked out, branch.created lost — recovery
    skips the git work and only backfills branch.created (no duplicate branch)."""
    repo = make_repo(tmp_path)
    home = paths.tracks_home(repo)
    store = Store(home)
    run_id, cid = new_ulid(), new_ulid()

    g(repo, "checkout", "-b", "releases/v0.1", "main")  # crash left the branch
    store.append(run_id, "v0.1", "story.requested", {"raw_chars": 1})
    store.append(run_id, "v0.1", "stage.entered", {"stage": "M-START"})
    store.append(
        run_id, "v0.1", "command.issued",
        {"command": {"kind": "create_branch",
                     "params": {"branch_name": "releases/v0.1", "base": "main"},
                     "command_id": cid}},
        command_id=cid,
    )

    branches_before = g(repo, "branch", "--list")
    Executor(store, repo, run_id)._recover()

    assert g(repo, "branch", "--list") == branches_before  # git untouched
    created = [e for e in store.events(run_id) if e.type == "branch.created"]
    assert len(created) == 1
    assert created[0].command_id == cid
    assert created[0].payload["branch_name"] == "releases/v0.1"


def test_delete_branch_tears_down_and_logs(tmp_path):
    """FR-09: delete_branch ends with HEAD==main, branch absent, branch.deleted."""
    repo = make_repo(tmp_path)
    home = paths.tracks_home(repo)
    store = Store(home)
    run_id = new_ulid()

    g(repo, "checkout", "-b", "releases/v0.1", "main")
    store.append(run_id, "v0.1", "story.requested", {"raw_chars": 1})
    Executor(store, repo, run_id).issue(
        Command(kind="delete_branch", params={"branch_name": "releases/v0.1"})
    )

    assert g(repo, "branch", "--list", "releases/v0.1") == ""
    assert g(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    deleted = [e for e in store.events(run_id) if e.type == "branch.deleted"]
    assert len(deleted) == 1 and deleted[0].payload["branch_name"] == "releases/v0.1"
