"""RED coverage for the direct Git tag effects boundary.

These tests use a local bare Git remote and never exercise the publish
executor or an external provider.  They pin the FR-0275/IF-PUBLISH direct
effects contract: real tag creation, same-object idempotency, divergent-tag
conflict, and remote object identity readback.
"""

from __future__ import annotations

import subprocess

from tracks.effects.publish import push_tag, read_remote_state


def _run(*args: str, cwd=None) -> str:
    proc = subprocess.run(
        list(args), cwd=cwd, check=True, capture_output=True, text=True
    )
    return proc.stdout.strip()


def _remote_repo(tmp_path):
    seed = tmp_path / "seed"
    bare = tmp_path / "remote.git"
    _run("git", "init", "-q", str(seed))
    _run("git", "-C", str(seed), "config", "user.email", "test@example.com")
    _run("git", "-C", str(seed), "config", "user.name", "Test")
    (seed / "file.txt").write_text("one\n", encoding="utf-8")
    _run("git", "-C", str(seed), "add", "file.txt")
    _run("git", "-C", str(seed), "commit", "-qm", "one")
    first = _run("git", "-C", str(seed), "rev-parse", "HEAD")
    (seed / "file.txt").write_text("two\n", encoding="utf-8")
    _run("git", "-C", str(seed), "commit", "-qam", "two")
    second = _run("git", "-C", str(seed), "rev-parse", "HEAD")
    branch = _run("git", "-C", str(seed), "symbolic-ref", "--short", "HEAD")
    _run("git", "init", "-q", "--bare", str(bare))
    _run("git", "-C", str(seed), "push", "-q", str(bare), "HEAD:refs/heads/main")
    return seed, bare, first, second, branch


def _remote_tag(remote, tag: str) -> str:
    return _run("git", "ls-remote", str(remote), f"refs/tags/{tag}").split()[0]


def test_push_tag_creates_real_tag_and_repeats_same_object(tmp_path, monkeypatch):
    seed, remote, _first, second, branch = _remote_repo(tmp_path)
    monkeypatch.chdir(seed)

    first_result = push_tag(str(remote), "v1.0.0", branch)
    second_result = push_tag(str(remote), "v1.0.0", branch)

    assert isinstance(first_result, dict)
    assert isinstance(second_result, dict)
    assert _remote_tag(remote, "v1.0.0") == second


def test_push_tag_does_not_overwrite_different_remote_object(tmp_path, monkeypatch):
    seed, remote, first, second, _branch = _remote_repo(tmp_path)
    monkeypatch.chdir(seed)
    push_tag(str(remote), "v1.0.0", first)

    try:
        result = push_tag(str(remote), "v1.0.0", second)
    except Exception as exc:  # pragma: no cover - baseline stub / bad effect
        raise AssertionError("divergent tag must return a conflict result") from exc

    assert result.get("status") in {"conflict", "failed", "blocked"}
    assert _remote_tag(remote, "v1.0.0") == first


def test_read_remote_state_reports_object_identity_without_fake_match(
    tmp_path, monkeypatch
):
    seed, remote, first, _second, _branch = _remote_repo(tmp_path)
    monkeypatch.chdir(seed)
    push_tag(str(remote), "v1.0.0", first)

    state = read_remote_state(str(remote), "tag", "v1.0.0")

    assert state["exists"] is True
    assert state["ref"] == "refs/tags/v1.0.0"
    assert state.get("object_id") == first
    assert state.get("matches") is not True
