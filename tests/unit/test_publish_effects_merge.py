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


def _remote_head(bare, branch="main") -> str | None:
    proc = subprocess.run(
        ["git", "--git-dir", str(bare), "rev-parse", "--verify", f"refs/heads/{branch}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 else None


_RELEASE_BRANCH = "releases/v0.8"


def _diverged_release_repo(tmp_path, *, conflicting: bool = False):
    """main candidate + a release branch with an independent commit.

    The release tip is diverged from the candidate: neither is an ancestor
    of the other, so a sync needs a real merge (FR-0277-02). ``conflicting``
    edits the same file as the candidate so the merge cannot be resolved.
    """
    seed, bare, first, candidate = _remote_repo(tmp_path)
    _run("git", "-C", str(seed), "branch", _RELEASE_BRANCH, first)
    _run(
        "git", "-C", str(seed), "push", "-q", str(bare),
        f"{_RELEASE_BRANCH}:refs/heads/{_RELEASE_BRANCH}",
    )
    _run("git", "-C", str(seed), "checkout", "-q", _RELEASE_BRANCH)
    if conflicting:
        (seed / "file.txt").write_text("release edit\n", encoding="utf-8")
        _run("git", "-C", str(seed), "commit", "-qam", "release edit")
    else:
        (seed / "release_only.txt").write_text("release\n", encoding="utf-8")
        _run("git", "-C", str(seed), "add", "release_only.txt")
        _run("git", "-C", str(seed), "commit", "-qm", "release only")
    release_tip = _run("git", "-C", str(seed), "rev-parse", "HEAD")
    _run(
        "git", "-C", str(seed), "push", "-q", str(bare),
        f"{_RELEASE_BRANCH}:refs/heads/{_RELEASE_BRANCH}",
    )
    _run("git", "-C", str(seed), "checkout", "-q", "main")
    return seed, bare, first, candidate, release_tip


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


# AC-FR0277-02@v0.8: a diverged release branch receives a true --no-ff merge
# commit whose readback is containment (not ref equality); the host working
# tree is never touched.
def test_push_merge_release_branch_true_merge(tmp_path, monkeypatch):
    seed, bare, _first, candidate, release_tip = _diverged_release_repo(tmp_path)
    monkeypatch.chdir(seed)

    result = push_merge(
        str(bare), candidate, _RELEASE_BRANCH, allow_merge=True
    )

    assert result["status"] == "done"
    assert result["merge_mode"] == "release_branch"
    merge_commit = _remote_head(bare, _RELEASE_BRANCH)
    assert merge_commit is not None
    assert merge_commit != candidate
    assert result["merge_commit"] == merge_commit
    assert result["remote_check"]["contains_fix"] is True
    assert result["remote_check"]["object_id"] == merge_commit
    # True merge product: two parents (release tip + the fix candidate),
    # candidate referenced in the message.
    body = _run("git", "--git-dir", str(bare), "cat-file", "-p", merge_commit)
    parents = [line.split()[1] for line in body.splitlines() if line.startswith("parent ")]
    assert set(parents) == {release_tip, candidate}
    message = _run("git", "--git-dir", str(bare), "log", "-1", "--format=%B", merge_commit)
    assert candidate in message
    # Content readback: both sides survive on the release branch.
    assert (
        _run("git", "--git-dir", str(bare), "show", f"{merge_commit}:release_only.txt")
        == "release"
    )
    assert (
        _run("git", "--git-dir", str(bare), "show", f"{merge_commit}:file.txt")
        == "two"
    )
    # The host checkout is untouched and no worktree residue remains.
    assert _run("git", "-C", str(seed), "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert _run("git", "-C", str(seed), "status", "--porcelain") == ""
    assert _run("git", "-C", str(seed), "worktree", "list", "--porcelain").count(
        "worktree "
    ) == 1

    # Idempotent re-run: containment readback skips without a new commit.
    repeat = push_merge(str(bare), candidate, _RELEASE_BRANCH, allow_merge=True)
    assert repeat["status"] == "reconciled_skip"
    assert repeat["remote_check"]["contains_fix"] is True
    assert _remote_head(bare, _RELEASE_BRANCH) == merge_commit


# AC-FR0277-02@v0.8 / NFR-0144-02: an unresolvable sync merge stays a
# conflict (reconcile_conflict at the runtime layer), never auto-resolved.
def test_push_merge_release_branch_conflict_no_force(tmp_path, monkeypatch):
    seed, bare, _first, candidate, release_tip = _diverged_release_repo(
        tmp_path, conflicting=True
    )
    monkeypatch.chdir(seed)

    result = push_merge(
        str(bare), candidate, _RELEASE_BRANCH, allow_merge=True
    )

    assert result["status"] == "conflict"
    assert result["merge_mode"] == "release_branch"
    assert result["remote_check"]["contains_fix"] is False
    assert _remote_head(bare, _RELEASE_BRANCH) == release_tip
    # No conflict resolution was staged into the host tree.
    assert _run("git", "-C", str(seed), "status", "--porcelain") == ""


# NFR-0144-01: a release branch that already contains the fix (whatever its
# merge history) skips on containment, not on ref equality.
def test_push_merge_release_branch_contained_skips(tmp_path, monkeypatch):
    seed, bare, _first, candidate, _release_tip = _diverged_release_repo(tmp_path)
    monkeypatch.chdir(seed)
    assert (
        push_merge(str(bare), candidate, _RELEASE_BRANCH, allow_merge=True)["status"]
        == "done"
    )
    # Later unrelated release work moves the head further away from both the
    # candidate and the earlier merge commit: containment still skips.
    _run(
        "git", "-C", str(seed), "fetch", "-q", str(bare),
        f"+{_RELEASE_BRANCH}:{_RELEASE_BRANCH}",
    )
    _run("git", "-C", str(seed), "checkout", "-q", _RELEASE_BRANCH)
    (seed / "post_merge.txt").write_text("later\n", encoding="utf-8")
    _run("git", "-C", str(seed), "add", "post_merge.txt")
    _run("git", "-C", str(seed), "commit", "-qm", "later release work")
    _run(
        "git", "-C", str(seed), "push", "-q", str(bare),
        f"{_RELEASE_BRANCH}:refs/heads/{_RELEASE_BRANCH}",
    )
    _run("git", "-C", str(seed), "checkout", "-q", "main")

    second = push_merge(str(bare), candidate, _RELEASE_BRANCH, allow_merge=True)

    assert second["status"] == "reconciled_skip"
    assert second["merge_mode"] == "release_branch"
    assert second["remote_check"]["contains_fix"] is True
