"""M-START guard rails (test-plan §3): AC-01b, AC-02b, AC-03a."""
import subprocess


def git_out(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


def test_reinit_is_idempotent(host_repo, trac):
    assert trac("init").returncode == 0
    head = git_out(host_repo, "rev-parse", "HEAD")
    r = trac("init")
    assert r.returncode == 0 and r.stderr == ""
    assert git_out(host_repo, "rev-parse", "HEAD") == head  # no empty commit
    for sub in ("projects", "runtime", "wiki"):
        assert (host_repo / ".tracks" / sub).is_dir()


def test_empty_stdin_rejected(host_repo, trac):
    assert trac("init").returncode == 0
    r = trac("start", "v0.1", stdin="")
    assert r.returncode == 1
    assert "stdin" in r.stderr or "空" in r.stderr
    assert "releases/v0.1" not in git_out(host_repo, "branch", "--list")


def test_dirty_worktree_rejected(host_repo, trac):
    assert trac("init").returncode == 0
    (host_repo / "WIP.txt").write_text("uncommitted\n", encoding="utf-8")
    r = trac("start", "v0.1", stdin="需求")
    assert r.returncode == 1
    assert "dirty" in r.stderr or "uncommitted" in r.stderr
    assert git_out(host_repo, "branch", "--list", "releases/v0.1") == ""  # AC-03a


def test_active_run_queues_to_backlog(host_repo, trac):
    """SM-01.2: active run → requirement recorded into backlog, no branch."""
    assert trac("init").returncode == 0
    assert trac("start", "v0.1", stdin="第一个需求").returncode == 0
    assert "releases/v0.1" in git_out(host_repo, "branch", "--list", "releases/v0.1")

    # second start while a run is active → queued, not started
    r = trac("start", "v0.2", stdin="排队的需求")
    assert r.returncode == 0, r.stderr
    assert "backlog" in r.stdout
    assert "releases/v0.2" not in git_out(host_repo, "branch", "--list", "releases/v0.2")
    assert not (host_repo / ".tracks" / "projects" / "v0.2" / "story.md").exists()

    # backlog row recorded (query the DB)
    import sqlite3
    db = host_repo / ".tracks" / "runtime" / "tracks.db"
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute(
            "SELECT version, decision, reason FROM backlog ORDER BY ts"
        ).fetchall()
    finally:
        conn.close()
    assert any(v == "v0.2" and d == "queued" and rsn == "active_run"
               for v, d, rsn in rows)


def test_backlog_phantom_does_not_block_later_start(host_repo, trac):
    """SM-01.2 regression: a previously-queued backlog phantom must not keep
    `active_run()` returning the phantom forever. After the real run is
    completed (FR-09 reject teardown), a fresh `trac start` must proceed and
    create a new branch - not be blocked by the lingering phantom row."""
    assert trac("init").returncode == 0
    assert trac("start", "v0.1", stdin="第一个需求").returncode == 0
    # queue a second requirement while v0.1 is active -> creates phantom
    assert trac("start", "v0.2", stdin="排队的需求").returncode == 0

    # `trac status` must report the real v0.1 run, not the v0.2 phantom
    r = trac("status")
    assert r.returncode == 0
    assert "stage=M-STORY" in r.stdout
    assert "substate=None" not in r.stdout  # phantom has substate=None

    # complete v0.1 via the reject path (triage no_go -> run -> teardown)
    assert trac("run").returncode == 0
    assert trac("triage", "no-go").returncode == 0
    assert trac("run").returncode == 0
    assert "completed" in trac("status").stdout

    # Now no real run is active. The v0.2 phantom still exists in `runs` but
    # must NOT block a new start: `trac start v0.3` proceeds, creates branch.
    r = trac("start", "v0.3", stdin="第三个需求")
    assert r.returncode == 0, r.stderr
    assert "started" in r.stdout
    assert "releases/v0.3" in git_out(host_repo, "branch", "--list", "releases/v0.3")


def test_unmerged_branch_requires_confirm(host_repo, trac):
    """SM-01.6/.8/.9: unmerged release branch → AWAIT_CONFIRM (--confirm/--cancel)."""
    assert trac("init").returncode == 0
    # create an unmerged release branch directly in git (simulating leftovers)
    git_out(host_repo, "checkout", "-b", "releases/v0.9")
    (host_repo / "leak.txt").write_text("leftover\n", encoding="utf-8")
    git_out(host_repo, "add", "leak.txt")
    git_out(host_repo, "commit", "-m", "leftover release work")
    git_out(host_repo, "checkout", "main")

    # no flag → prompt (exit 2), no branch created
    r = trac("start", "v0.1", stdin="需求")
    assert r.returncode == 2
    assert "--confirm" in r.stderr
    assert "releases/v0.1" not in git_out(host_repo, "branch", "--list", "releases/v0.1")

    # --cancel → abort, no branch
    r = trac("start", "v0.1", "--cancel", stdin="需求")
    assert r.returncode == 0
    assert "cancelled" in r.stdout
    assert "releases/v0.1" not in git_out(host_repo, "branch", "--list", "releases/v0.1")

    # --confirm → proceed
    r = trac("start", "v0.1", "--confirm", stdin="需求")
    assert r.returncode == 0, r.stderr
    assert "releases/v0.1" in git_out(host_repo, "branch", "--list", "releases/v0.1")
