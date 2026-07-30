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


class _StubBackend:
    """Test double standing in for an AgentBackend (returns a fixed outcome)."""

    def __init__(self, outcome):
        self._outcome = outcome

    def act(self, role, substate, doc, doc_path):
        return self._outcome


def _dispatch(ex, store, run_id, outcome):
    ex.backend = _StubBackend(outcome)
    cmd = Command(kind="dispatch_agent",
                  params={"role": "scribe", "substate": "DRAFT", "doc": "story.md"},
                  command_id=new_ulid())
    ex._do_dispatch_agent(cmd, store.state(run_id), None, False)
    outcomes = [e.payload for e in store.events(run_id)
                if e.type == "outcome.received"]
    assert len(outcomes) == 1
    return outcomes[0]


def _setup(tmp_path):
    repo = make_repo(tmp_path)
    home = paths.tracks_home(repo)
    store = Store(home)
    run_id = new_ulid()
    vdir = paths.version_dir(home, "v0.1")
    vdir.mkdir(parents=True)
    (vdir / "story.md").write_text("---\nsha:\n---\n\n# 目标\n", encoding="utf-8")
    store.append(run_id, "v0.1", "story.requested", {"raw_chars": 1})
    store.append(run_id, "v0.1", "stage.entered", {"stage": "M-STORY"})
    return Executor(store, repo, run_id), store, run_id


def test_dispatch_agent_carries_opencode_outcome_fields(tmp_path):
    """IF-003 §1: diff_ref / audit_evidence / failure_class flow into the
    outcome.received event so over-reach & failures are observable."""
    ex, store, run_id = _setup(tmp_path)
    payload = _dispatch(ex, store, run_id, {
        "status": "failed", "artifact_ref": None, "self_report": "over-reach",
        "diff_ref": "diff --git a/story.md", "audit_evidence": "over-reach: EXTRA.md",
        "failure_class": "over_reach",
    })
    assert payload["failure_class"] == "over_reach"
    assert payload["audit_evidence"] == "over-reach: EXTRA.md"
    assert payload["diff_ref"] == "diff --git a/story.md"


def test_dispatch_agent_fake_outcome_omits_additive_fields(tmp_path):
    """FakeBackend outcomes carry no diff/audit/failure fields (absent, not null)."""
    ex, store, run_id = _setup(tmp_path)
    payload = _dispatch(ex, store, run_id, {
        "status": "done", "artifact_ref": "story.md", "self_report": "wrote story",
    })
    assert "failure_class" not in payload
    assert "audit_evidence" not in payload
    assert "diff_ref" not in payload
