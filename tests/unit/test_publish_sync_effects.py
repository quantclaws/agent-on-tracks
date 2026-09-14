"""FR-0277-02: the narrow sync-product effect with atomic expected-old update.

Real local bare remotes only. The effect consumes the approved prepared
identity: tip == P reconciles (exact object), tip == B executes the
``--force-with-lease`` pinned to B, any other tip (including a rollback to an
ancestor of B or a concurrent move between check and push) is a conflict and
never touches the remote.
"""

from __future__ import annotations

import subprocess

from tests.unit.helpers import git_repo, git_strip
from tracks.effects import publish as publish_effects
from tracks.effects.publish import push_sync_product
from tracks.executor.sync_product import build_product

_RELEASE = "releases/v0.8"


def _run(*args, cwd=None) -> str:
    proc = subprocess.run(list(args), cwd=cwd, check=True, capture_output=True, text=True)
    return proc.stdout.strip()


def _remote_head(bare, branch=_RELEASE):
    proc = subprocess.run(
        ["git", "--git-dir", str(bare), "rev-parse", "--verify", f"refs/heads/{branch}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 else None


def _diverged(tmp_path):
    repo = git_repo(tmp_path, gitignore=True)
    bare = tmp_path / "remote.git"
    _run("git", "init", "-q", "--bare", str(bare))
    git_strip(repo, "remote", "add", "origin", str(bare))
    base = git_strip(repo, "rev-parse", "HEAD")
    git_strip(repo, "checkout", "-q", "-b", _RELEASE, base)
    (repo / "release_only.txt").write_text("release\n", encoding="utf-8")
    git_strip(repo, "add", "release_only.txt")
    git_strip(repo, "commit", "-qm", "release only")
    baseline = git_strip(repo, "rev-parse", "HEAD")
    git_strip(repo, "push", "-q", str(bare), f"{_RELEASE}:refs/heads/{_RELEASE}")
    git_strip(repo, "checkout", "-q", "main")
    (repo / "fix.txt").write_text("fix\n", encoding="utf-8")
    git_strip(repo, "add", "fix.txt")
    git_strip(repo, "commit", "-qm", "fix")
    candidate = git_strip(repo, "rev-parse", "HEAD")
    record, error = build_product(repo, _RELEASE, baseline, candidate)
    assert error is None, error
    return repo, bare, baseline, candidate, record


def test_push_sync_product_done_then_exact_skip(tmp_path, monkeypatch):
    repo, bare, baseline, candidate, record = _diverged(tmp_path)
    monkeypatch.chdir(repo)

    result = push_sync_product(
        str(bare),
        _RELEASE,
        record["product_sha"],
        baseline_sha=baseline,
        candidate_sha=candidate,
    )

    assert result["status"] == "done"
    assert result["merge_mode"] == "sync_product"
    assert result["object_id"] == record["product_sha"]
    assert result["product_sha"] == record["product_sha"]
    assert result["baseline_sha"] == baseline
    assert result["source_candidate_sha"] == candidate
    assert _remote_head(bare) == record["product_sha"]
    body = _run("git", "--git-dir", str(bare), "cat-file", "-p", record["product_sha"])
    parents = [line.split()[1] for line in body.splitlines() if line.startswith("parent ")]
    assert parents == [baseline, candidate]

    repeat = push_sync_product(
        str(bare),
        _RELEASE,
        record["product_sha"],
        baseline_sha=baseline,
        candidate_sha=candidate,
    )

    assert repeat["status"] == "reconciled_skip"
    assert repeat["object_id"] == record["product_sha"]
    assert _remote_head(bare) == record["product_sha"]


def test_push_sync_product_baseline_moved_is_conflict(tmp_path, monkeypatch):
    repo, bare, baseline, candidate, record = _diverged(tmp_path)
    monkeypatch.chdir(repo)
    git_strip(repo, "checkout", "-q", _RELEASE)
    (repo / "newer.txt").write_text("newer\n", encoding="utf-8")
    git_strip(repo, "add", "newer.txt")
    git_strip(repo, "commit", "-qm", "newer release work")
    moved = git_strip(repo, "rev-parse", "HEAD")
    git_strip(repo, "push", "-q", str(bare), f"{_RELEASE}:refs/heads/{_RELEASE}")

    result = push_sync_product(
        str(bare),
        _RELEASE,
        record["product_sha"],
        baseline_sha=baseline,
        candidate_sha=candidate,
    )

    assert result["status"] == "conflict"
    assert result["reason"] == "remote_moved"
    assert _remote_head(bare) == moved


def test_push_sync_product_ancestor_rollback_is_conflict(tmp_path, monkeypatch):
    repo, bare, baseline, candidate, record = _diverged(tmp_path)
    monkeypatch.chdir(repo)
    ancestor = git_strip(repo, "rev-parse", f"{baseline}~1")
    # A plain non-force push from the rolled-back ancestor to P would be
    # accepted; the expected-old lease pinned to B must reject it.
    git_strip(
        repo, "push", "-q", "-f", str(bare), f"{ancestor}:refs/heads/{_RELEASE}"
    )
    assert _remote_head(bare) == ancestor

    result = push_sync_product(
        str(bare),
        _RELEASE,
        record["product_sha"],
        baseline_sha=baseline,
        candidate_sha=candidate,
    )

    assert result["status"] == "conflict"
    assert _remote_head(bare) == ancestor


def test_push_sync_product_race_between_check_and_push(tmp_path, monkeypatch):
    repo, bare, baseline, candidate, record = _diverged(tmp_path)
    monkeypatch.chdir(repo)
    # The remote moves after the client check: the effect's readback lies
    # about the tip while the real push still runs against the moved remote.
    monkeypatch.setattr(
        publish_effects,
        "read_remote_state",
        lambda *_args, **_kwargs: {
            "exists": True,
            "matches": False,
            "object_id": baseline,
            "ref": f"refs/heads/{_RELEASE}",
            "remote": str(bare),
        },
    )
    git_strip(repo, "checkout", "-q", _RELEASE)
    (repo / "concurrent.txt").write_text("concurrent\n", encoding="utf-8")
    git_strip(repo, "add", "concurrent.txt")
    git_strip(repo, "commit", "-qm", "concurrent release work")
    concurrent = git_strip(repo, "rev-parse", "HEAD")
    git_strip(repo, "push", "-q", str(bare), f"{_RELEASE}:refs/heads/{_RELEASE}")

    result = push_sync_product(
        str(bare),
        _RELEASE,
        record["product_sha"],
        baseline_sha=baseline,
        candidate_sha=candidate,
    )

    assert result["status"] == "conflict"
    assert _remote_head(bare) == concurrent


def test_push_sync_product_missing_branch_fails(tmp_path, monkeypatch):
    repo, bare, baseline, candidate, record = _diverged(tmp_path)
    monkeypatch.chdir(repo)
    _run("git", "--git-dir", str(bare), "update-ref", "-d", f"refs/heads/{_RELEASE}")

    result = push_sync_product(
        str(bare),
        _RELEASE,
        record["product_sha"],
        baseline_sha=baseline,
        candidate_sha=candidate,
    )

    assert result["status"] == "failed"
    assert result["reason"] == "branch_missing"
    assert _remote_head(bare) is None


def test_push_sync_product_invalid_identity_fails(tmp_path, monkeypatch):
    repo, bare, baseline, candidate, _record = _diverged(tmp_path)
    monkeypatch.chdir(repo)

    result = push_sync_product(
        str(bare), _RELEASE, "not-a-sha", baseline_sha=baseline, candidate_sha=candidate
    )

    assert result["status"] == "failed"
    assert result["reason"] == "invalid_ref"
    assert _remote_head(bare) is not None
