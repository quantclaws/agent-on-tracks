"""Direct M-PUBLISH effects checks against a local bare Git remote.

These tests deliberately stop at the public ``effects.publish`` boundary.
They verify real remote state and therefore do not claim the complete
``trac run``/write-ahead/reconcile journey covered by AC-FR0275.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tracks.effects.publish import push_tag, read_remote_state

pytestmark = pytest.mark.integration


def _git(repo: Path, *args: str, check: bool = True) -> str:
    """Run Git locally and return stdout without involving a shell."""
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=check,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


def _new_remote(tmp_path: Path) -> tuple[Path, Path, str]:
    """Create a committed local repository and an empty local bare remote."""
    seed = tmp_path / "seed"
    bare = tmp_path / "remote.git"
    seed.mkdir()
    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(seed)], check=True, capture_output=True, text=True
    )
    _git(seed, "config", "user.email", "test@example.com")
    _git(seed, "config", "user.name", "publish-effects-test")
    (seed / "payload.txt").write_text("first\n", encoding="utf-8")
    _git(seed, "add", "payload.txt")
    _git(seed, "commit", "-qm", "initial")
    head = _git(seed, "rev-parse", "HEAD")
    subprocess.run(
        ["git", "init", "-q", "--bare", str(bare)],
        check=True,
        capture_output=True,
        text=True,
    )
    return seed, bare, head


def _remote_tag_sha(bare: Path, tag: str) -> str | None:
    """Read one tag ref directly from the bare repository."""
    proc = subprocess.run(
        ["git", "--git-dir", str(bare), "rev-parse", f"refs/tags/{tag}"],
        check=False,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip() if proc.returncode == 0 else None


def _push_tag(remote: Path, tag: str, ref: str, expected_status: str) -> dict:
    """Call the public effect and require its documented status result."""
    try:
        result = push_tag(str(remote), tag, ref)
    except NotImplementedError as exc:
        raise AssertionError(
            "push_tag must perform the local Git tag effect at the public boundary"
        ) from exc
    assert isinstance(result, dict), f"push_tag must return an object, got {result!r}"
    assert result.get("status") == expected_status, (
        f"push_tag status must be {expected_status!r}, got {result!r}"
    )
    return result


def _seed_remote_tag(seed: Path, remote: Path, tag: str, head: str) -> None:
    """Seed only the independent read-back scenario through Git itself."""
    _git(seed, "tag", tag, head)
    _git(
        seed,
        "push",
        "-q",
        str(remote),
        "refs/heads/main:refs/heads/main",
        f"refs/tags/{tag}:refs/tags/{tag}",
    )


# IF-PUBLISH-002 / AC-FR0275-01: remote state is read from the actual bare remote.
def test_read_remote_state_reports_real_tag_presence(tmp_path: Path) -> None:
    seed, remote, head = _new_remote(tmp_path)
    _seed_remote_tag(seed, remote, "v1.0.0", head)

    present = read_remote_state(str(remote), "tag", "v1.0.0")
    missing = read_remote_state(str(remote), "tag", "v9.9.9")

    assert present["exists"] is True
    assert present["object_id"] == head
    assert present["ref"] == "refs/tags/v1.0.0"
    assert missing["exists"] is False
    assert missing["ref"] == "refs/tags/v9.9.9"


# IF-PUBLISH-001 / AC-FR0275-01: a tag write must be visible in remote read-back.
def test_push_tag_creates_real_remote_tag(tmp_path: Path, monkeypatch) -> None:
    seed, remote, head = _new_remote(tmp_path)
    monkeypatch.chdir(seed)

    _push_tag(remote, "v1.0.0", head, "done")

    assert _remote_tag_sha(remote, "v1.0.0") == head
    state = read_remote_state(str(remote), "tag", "v1.0.0")
    assert state["exists"] is True


# IF-PUBLISH-001/002 / AC-FR0275-02: repeating the same target is idempotent.
def test_push_tag_same_target_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    seed, remote, head = _new_remote(tmp_path)
    monkeypatch.chdir(seed)

    _push_tag(remote, "v1.0.0", head, "done")
    before = _remote_tag_sha(remote, "v1.0.0")
    _push_tag(remote, "v1.0.0", head, "reconciled_skip")
    after = _remote_tag_sha(remote, "v1.0.0")

    assert before == head
    assert after == before
    refs = _git(remote, "show-ref", "--tags", "v1.0.0").splitlines()
    assert len(refs) == 1


# IF-PUBLISH-001/002 / AC-FR0275-04: divergent content is rejected and preserved.
def test_push_tag_different_sha_rejects_without_overwrite(tmp_path: Path, monkeypatch) -> None:
    seed, remote, first_head = _new_remote(tmp_path)
    monkeypatch.chdir(seed)
    _push_tag(remote, "v1.0.0", first_head, "done")

    (seed / "payload.txt").write_text("second\n", encoding="utf-8")
    _git(seed, "add", "payload.txt")
    _git(seed, "commit", "-qm", "second")
    second_head = _git(seed, "rev-parse", "HEAD")

    try:
        result = push_tag(str(remote), "v1.0.0", second_head)
    except NotImplementedError as exc:
        raise AssertionError(
            "push_tag must implement divergent-tag rejection before reconcile"
        ) from exc
    assert isinstance(result, dict), f"push_tag must return an object, got {result!r}"
    assert result.get("status") == "conflict", (
        f"same tag with a different SHA must return conflict, got {result!r}"
    )
    assert _remote_tag_sha(remote, "v1.0.0") == first_head
    assert _remote_tag_sha(remote, "v1.0.0") != second_head
