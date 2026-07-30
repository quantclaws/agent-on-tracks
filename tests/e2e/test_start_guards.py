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
