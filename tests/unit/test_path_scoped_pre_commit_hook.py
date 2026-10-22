"""Path-scoped host pre-commit hook contract (design ii).

The repo hook is lint-only and path-scoped: staged mode lints only ACMR
paths, explicit-path mode lints exactly those paths, and unrelated dirty
files never veto a commit. The runtime's scoped commit helper runs hooks for
product checkpoints and surfaces hook output on rejection.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from tracks.executor.helpers import _scoped_commit_if_staged
from tracks.executor.m_impl_dispatch import _run_host_commit_hook

REPO = Path(__file__).resolve().parents[2]
HOOK = REPO / ".githooks" / "pre-commit"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )


def _write(repo: Path, rel: str, text: str) -> Path:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _fake_toolchain(tmp_path: Path, *, fail_on: str | None = None) -> Path:
    """Fake ruff/flake8/pylint that log argv and optionally reject a path."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "tool.log"
    for tool in ("ruff", "flake8", "pylint"):
        reject = ""
        if fail_on:
            reject = (
                'for arg in "$@"; do\n'
                f'  case "$arg" in *{fail_on}*) '
                f'echo "{tool}: rejected $arg" >&2; exit 1 ;; esac\n'
                "done\n"
            )
        script = (
            "#!/bin/sh\n"
            f"{reject}"
            f"printf '%s %s\\n' \"{tool}\" \"$*\" >> \"{log}\"\n"
            "exit 0\n"
        )
        path = bin_dir / tool
        path.write_text(script, encoding="utf-8")
        path.chmod(0o755)
    marker = tmp_path / "pytest-called"
    pytest_stub = bin_dir / "pytest"
    pytest_stub.write_text(f'#!/bin/sh\ntouch "{marker}"\nexit 1\n', encoding="utf-8")
    pytest_stub.chmod(0o755)
    return bin_dir


def _repo(tmp_path: Path, *, hooks_path: str = ".githooks") -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    if hooks_path == ".githooks":
        hooks = repo / ".githooks"
        hooks.mkdir()
        shutil.copy2(HOOK, hooks / "pre-commit")
    _git(repo, "config", "core.hooksPath", hooks_path)
    return repo


def _run_hook(
    repo: Path, bin_dir: Path, *args: str
) -> subprocess.CompletedProcess:
    env = {**os.environ, "TRACKS_HOOK_BIN": str(bin_dir)}
    return subprocess.run(
        [str(repo / ".githooks" / "pre-commit"), *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _tool_lines(tmp_path: Path) -> dict[str, list[str]]:
    log = tmp_path / "tool.log"
    if not log.exists():
        return {}
    lines: dict[str, list[str]] = {}
    for line in log.read_text(encoding="utf-8").splitlines():
        tool, _, rest = line.partition(" ")
        lines.setdefault(tool, []).append(rest)
    return lines


def test_staged_mode_lints_only_staged_paths(tmp_path):
    repo = _repo(tmp_path)
    bin_dir = _fake_toolchain(tmp_path)
    _write(repo, "tracks/good.py", "VALUE = 1\n")
    _write(repo, "tests/test_good.py", "def test_good():\n    assert True\n")
    _write(repo, "tracks/dirty.py", "DIRTY = 1\n")
    _git(repo, "add", "tracks/good.py", "tests/test_good.py")

    proc = _run_hook(repo, bin_dir)

    assert proc.returncode == 0, proc.stderr
    calls = _tool_lines(tmp_path)
    assert calls["ruff"][0].startswith("check ")
    assert "tracks/good.py" in calls["ruff"][0]
    assert "tests/test_good.py" in calls["ruff"][0]
    assert "tracks/dirty.py" not in calls["ruff"][0]
    assert "tracks/good.py" in calls["flake8"][0]
    assert "tests/test_good.py" not in calls["flake8"][0]
    assert not (tmp_path / "pytest-called").exists()


def test_explicit_path_mode_ignores_staged_and_missing_paths(tmp_path):
    repo = _repo(tmp_path)
    bin_dir = _fake_toolchain(tmp_path)
    _write(repo, "tracks/good.py", "VALUE = 1\n")
    _write(repo, "tracks/other.py", "OTHER = 1\n")
    _git(repo, "add", "tracks/good.py", "tracks/other.py")

    proc = _run_hook(repo, bin_dir, "tracks/good.py", "tracks/deleted.py")

    assert proc.returncode == 0, proc.stderr
    calls = _tool_lines(tmp_path)
    assert "tracks/good.py" in calls["ruff"][0]
    assert "tracks/other.py" not in calls["ruff"][0]
    assert "deleted.py" not in calls["ruff"][0]
    assert not (tmp_path / "pytest-called").exists()


def test_lint_bad_explicit_path_fails_closed_without_pytest(tmp_path):
    repo = _repo(tmp_path)
    bin_dir = _fake_toolchain(tmp_path, fail_on="bad")
    _write(repo, "tracks/bad.py", "BAD = 1\n")

    proc = _run_hook(repo, bin_dir, "tracks/bad.py")

    assert proc.returncode != 0
    assert "ruff: rejected" in proc.stdout + proc.stderr
    assert not (tmp_path / "pytest-called").exists()


def test_staged_deletion_is_not_a_lint_surface(tmp_path):
    repo = _repo(tmp_path)
    bin_dir = _fake_toolchain(tmp_path)
    _write(repo, "tracks/gone.py", "GONE = 1\n")
    _git(repo, "add", "tracks/gone.py")
    _git(repo, "commit", "--no-verify", "-m", "baseline")
    (repo / "tracks/gone.py").unlink()
    _git(repo, "add", "-u")

    proc = _run_hook(repo, bin_dir)

    assert proc.returncode == 0, proc.stderr
    assert "gone.py" not in str(_tool_lines(tmp_path))


def _baseline(repo: Path) -> None:
    _write(repo, "README.md", "base\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "--no-verify", "-m", "baseline")


def test_scoped_product_checkpoint_passes_with_dirty_out_of_scope_file(
    tmp_path, monkeypatch
):
    repo = _repo(tmp_path)
    bin_dir = _fake_toolchain(tmp_path)
    _baseline(repo)
    monkeypatch.setenv("TRACKS_HOOK_BIN", str(bin_dir))
    _write(repo, "tracks/product.py", "PRODUCT = 1\n")
    _write(repo, "tracks/dirty.py", "DIRTY = 1\n")
    _git(repo, "add", "tracks/product.py")

    proc = _scoped_commit_if_staged(
        repo, "checkpoint", paths=["tracks/product.py"]
    )

    assert proc is not None and proc.returncode == 0, proc.stderr
    committed = _git(repo, "show", "--name-only", "--format=", "HEAD").stdout.split()
    assert committed == ["tracks/product.py"]
    calls = _tool_lines(tmp_path)
    assert "tracks/product.py" in calls["ruff"][0]
    assert "tracks/dirty.py" not in calls["ruff"][0]


def test_scoped_product_checkpoint_bad_file_fails_with_hook_output(
    tmp_path, monkeypatch
):
    repo = _repo(tmp_path)
    bin_dir = _fake_toolchain(tmp_path, fail_on="bad")
    _baseline(repo)
    monkeypatch.setenv("TRACKS_HOOK_BIN", str(bin_dir))
    _write(repo, "tracks/bad.py", "BAD = 1\n")
    _git(repo, "add", "tracks/bad.py")

    proc = _scoped_commit_if_staged(repo, "checkpoint", paths=["tracks/bad.py"])

    assert proc is not None and proc.returncode != 0
    assert "ruff: rejected" in proc.stdout + proc.stderr


def test_runtime_invokes_host_declared_custom_hook(tmp_path):
    main = tmp_path / "main"
    main.mkdir()
    gate = tmp_path / "gate"
    gate.mkdir()
    _git(gate, "init", "-q")
    hooks = gate / "host-hooks"
    hooks.mkdir()
    record = tmp_path / "hook.record"
    hook = hooks / "pre-commit"
    hook.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$@" > "{record}"\n'
        f'printf "%s|%s\\n" "$TRACKS_HOOK_ROOT" "$TRACKS_HOOK_BIN" >> "{record}"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    _git(gate, "config", "core.hooksPath", "host-hooks")
    assert not (gate / ".githooks").exists()

    ok, output = _run_host_commit_hook(gate, main, ["src/custom.py"])

    assert ok is True
    assert output == ""
    lines = record.read_text(encoding="utf-8").splitlines()
    assert lines == [
        "src/custom.py",
        f"{main}|{Path(sys.executable).parent}",
    ]


def test_runtime_missing_or_nonexecutable_host_hook_is_noop(tmp_path):
    main = tmp_path / "main"
    main.mkdir()
    gate = tmp_path / "gate"
    gate.mkdir()
    _git(gate, "init", "-q")
    _git(gate, "config", "core.hooksPath", "host-hooks")

    assert _run_host_commit_hook(gate, main, ["src/changed.py"]) == (True, "")

    hooks = gate / "host-hooks"
    hooks.mkdir()
    hook = hooks / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o644)

    assert _run_host_commit_hook(gate, main, ["src/changed.py"]) == (True, "")


def test_runtime_present_host_hook_rejects_with_changed_paths(tmp_path):
    main = tmp_path / "main"
    main.mkdir()
    gate = tmp_path / "gate"
    gate.mkdir()
    _git(gate, "init", "-q")
    hooks = gate / "host-hooks"
    hooks.mkdir()
    record = tmp_path / "hook.record"
    hook = hooks / "pre-commit"
    hook.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$@" > "{record}"\n'
        'echo "rejected by host hook" >&2\n'
        "exit 2\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    _git(gate, "config", "core.hooksPath", "host-hooks")

    ok, output = _run_host_commit_hook(gate, main, ["src/bad.py"])

    assert ok is False
    assert "rejected by host hook" in output
    assert record.read_text(encoding="utf-8").splitlines() == ["src/bad.py"]
