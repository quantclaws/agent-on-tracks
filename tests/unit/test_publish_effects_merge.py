"""Direct coverage for the fast-forward merge effects boundary (FR-0275).

Local bare Git remotes only: no executor, no provider. The contract is the
coordinator ruling #1 -- a merge is a non-force fast-forward of the approved
candidate (no new merge commit), reconciled against the real remote
(IF-PUBLISH-001/002).
"""

from __future__ import annotations

import subprocess

from tracks.effects.publish import push_merge


def _run(*args: str, cwd=None) -> str:
    proc = subprocess.run(list(args), cwd=cwd, check=True, capture_output=True, text=True)
    return proc.stdout.strip()


def _remote_repo(tmp_path):
    seed = tmp_path / "seed"
    bare = tmp_path / "remote.git"
    _run("git", "init", "-q", "-b", "main", str(seed))
    _run("git", "-C", str(seed), "config", "user.email", "test@example.com")
    _run("git", "-C", str(seed), "config", "user.name", "Test")
    (seed / "file.txt").write_text("one\n", encoding="utf-8")
    _run("git", "-C", str(seed), "add", "file.txt")
    _run("git", "-C", str(seed), "commit", "-qm", "one")
    first = _run("git", "-C", str(seed), "rev-parse", "HEAD")
    (seed / "file.txt").write_text("two\n", encoding="utf-8")
    _run("git", "-C", str(seed), "commit", "-qam", "two")
    candidate = _run("git", "-C", str(seed), "rev-parse", "HEAD")
    _run("git", "init", "-q", "--bare", str(bare))
    _run("git", "-C", str(seed), "push", "-q", str(bare), f"{first}:refs/heads/main")
    return seed, bare, first, candidate


def _remote_head(bare) -> str | None:
    proc = subprocess.run(
        ["git", "--git-dir", str(bare), "rev-parse", "--verify", "refs/heads/main"],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 else None


# AC-FR0275-01@v0.8: FF push reaches the candidate, no new merge commit, and a
# repeat is reconciled against the remote (IF-PUBLISH-001/002).
def test_push_merge_fast_forwards_and_repeat_skips(tmp_path, monkeypatch):
    seed, bare, _first, candidate = _remote_repo(tmp_path)
    monkeypatch.chdir(seed)

    result = push_merge(str(bare), candidate, "main")

    assert result["status"] == "done"
    assert result["branch"] == "main"
    assert result["ref"] == candidate
    assert result["object_id"] == candidate
    assert result["remote_check"]["exists"] is True
    assert result["remote_check"]["matches"] is True
    assert _remote_head(bare) == candidate
    parents = _run("git", "-C", str(seed), "cat-file", "-p", candidate).splitlines()
    assert sum(1 for line in parents if line.startswith("parent ")) == 1
    leftovers = _run("git", "-C", str(seed), "for-each-ref", "refs/trac/publish-fetch")
    assert leftovers == ""

    repeat = push_merge(str(bare), candidate, "main")

    assert repeat["status"] == "reconciled_skip"
    assert _remote_head(bare) == candidate


# AC-NFR0144-02@v0.8: a diverged remote is a conflict; nothing is forced.
def test_push_merge_divergence_is_conflict_without_force(tmp_path, monkeypatch):
    seed, bare, first, candidate = _remote_repo(tmp_path)
    monkeypatch.chdir(seed)
    _run("git", "-C", str(seed), "checkout", "-q", "-b", "sibling", first)
    (seed / "file.txt").write_text("sibling\n", encoding="utf-8")
    _run("git", "-C", str(seed), "commit", "-qam", "sibling")
    sibling = _run("git", "-C", str(seed), "rev-parse", "HEAD")
    _run("git", "-C", str(seed), "push", "-q", str(bare), "sibling:refs/heads/main")
    _run("git", "-C", str(seed), "checkout", "-q", "main")

    result = push_merge(str(bare), candidate, "main")

    assert result["status"] == "conflict"
    assert result["remote_check"]["object_id"] == sibling
    assert _remote_head(bare) == sibling


# AC-NFR0144-02@v0.8: a remote ahead of the candidate is divergence too.
def test_push_merge_remote_ahead_is_conflict_not_force(tmp_path, monkeypatch):
    seed, bare, first, candidate = _remote_repo(tmp_path)
    monkeypatch.chdir(seed)
    _run("git", "-C", str(seed), "checkout", "-q", "-b", "ahead", candidate)
    (seed / "file.txt").write_text("ahead\n", encoding="utf-8")
    _run("git", "-C", str(seed), "commit", "-qam", "ahead")
    ahead = _run("git", "-C", str(seed), "rev-parse", "HEAD")
    _run("git", "-C", str(seed), "push", "-q", str(bare), "ahead:refs/heads/main")
    _run("git", "-C", str(seed), "checkout", "-q", "main")

    result = push_merge(str(bare), first, "main")

    assert result["status"] == "conflict"
    assert _remote_head(bare) == ahead


# AC-FR0275-04@v0.8: an absent target branch fails closed (branch_missing).
def test_push_merge_missing_branch_fails(tmp_path, monkeypatch):
    seed, bare, _first, candidate = _remote_repo(tmp_path)
    subprocess.run(
        ["git", "--git-dir", str(bare), "update-ref", "-d", "refs/heads/main"],
        check=True,
        capture_output=True,
        text=True,
    )
    monkeypatch.chdir(seed)

    result = push_merge(str(bare), candidate, "main")

    assert result["status"] == "failed"
    assert result["reason"] == "branch_missing"
    assert result["remote_check"]["exists"] is False


# AC-FR0275-04@v0.8: an unresolvable source ref fails before any remote write.
def test_push_merge_invalid_ref_fails(tmp_path, monkeypatch):
    seed, bare, _first, _candidate = _remote_repo(tmp_path)
    monkeypatch.chdir(seed)

    result = push_merge(str(bare), "refs/heads/no-such-branch", "main")

    assert result["status"] == "failed"
    assert result["reason"] == "invalid_ref"
    assert _remote_head(bare) is not None
