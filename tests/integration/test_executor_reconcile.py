"""Executor reconcile (D-13, AC-29d): commit succeeded, result event lost —
recovery probes git by command_id, skips the commit, only backfills the event.
"""
import subprocess

from tracks import paths
from tracks.executor import Executor
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
