"""Unit tests for three-worktree scheme (IF-IMPL-006).

Tests path determinism, cleanup idempotency, and that the main worktree
is never removed.
"""

from __future__ import annotations

from pathlib import Path

from tests.unit.helpers import git as _git
from tests.unit.helpers import init_repo as _init_repo
from tracks.executor.worktree import (
    cleanup_worktree,
    create_devon_worktree,
    create_gate_worktree,
    create_test_authority_worktree,
    ensure_runtime_assets,
    sweep_worktrees,
)


def _worktree_list(repo: Path) -> set[str]:
    out = _git(repo, "worktree", "list", "--porcelain").stdout
    return {
        line.removeprefix("worktree ") for line in out.splitlines() if line.startswith("worktree ")
    }


def test_devon_worktree_path(tmp_path):
    repo, base = _init_repo(tmp_path)
    handle = create_devon_worktree(str(repo), base, "run-1", "T-001")
    assert "run-1" in handle.path
    assert "devon" in handle.path
    assert Path(handle.path).exists()
    cleanup_worktree(handle)


def test_devon_worktree_deterministic(tmp_path):
    repo, base = _init_repo(tmp_path)
    h1 = create_devon_worktree(str(repo), base, "run-1", "T-001")
    p1 = h1.path
    cleanup_worktree(h1)
    h2 = create_devon_worktree(str(repo), base, "run-1", "T-001")
    assert h2.path == p1
    cleanup_worktree(h2)


def test_test_authority_worktree(tmp_path):
    repo, base = _init_repo(tmp_path)
    handle = create_test_authority_worktree(str(repo), base, "run-1")
    assert Path(handle.path).exists()
    cleanup_worktree(handle)


def test_gate_worktree(tmp_path):
    repo, base = _init_repo(tmp_path)
    devon = create_devon_worktree(str(repo), base, "run-1", "T-001")
    authority = create_test_authority_worktree(str(repo), base, "run-1")
    gate = create_gate_worktree(
        str(repo),
        base,
        "",
        "",
        "run-1",
        "T-001",
    )
    assert Path(gate.path).exists()
    cleanup_worktree(gate)
    cleanup_worktree(devon)
    cleanup_worktree(authority)


def test_cleanup_idempotent(tmp_path):
    repo, base = _init_repo(tmp_path)
    handle = create_devon_worktree(str(repo), base, "run-1", "T-001")
    cleanup_worktree(handle)
    cleanup_worktree(handle)
    assert not Path(handle.path).exists()


def test_cleanup_never_deletes_main(tmp_path):
    repo, base = _init_repo(tmp_path)
    original = _worktree_list(repo)
    handle = create_devon_worktree(str(repo), base, "run-1", "T-001")
    cleanup_worktree(handle)
    assert _worktree_list(repo) == original


def _repo_with_opencode(tmp_path: Path) -> tuple[Path, str]:
    """Repo whose main tree has a gitignored .opencode deployment."""
    repo, base = _init_repo(tmp_path)
    (repo / ".gitignore").write_text(".opencode\n", encoding="utf-8")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-m", "gitignore")
    base = _git(repo, "rev-parse", "HEAD").stdout.strip()
    deployed = repo / ".opencode" / "skills" / "tracks-devon-rgr"
    deployed.mkdir(parents=True)
    (deployed / "SKILL.md").write_text("canonical body\n", encoding="utf-8")
    return repo, base


def test_ensure_runtime_assets_links_opencode(tmp_path):
    repo, _ = _repo_with_opencode(tmp_path)
    wt = tmp_path / "wt"
    wt.mkdir()
    ensure_runtime_assets(str(repo), str(wt))
    link = wt / ".opencode"
    assert link.is_symlink()
    body = (link / "skills" / "tracks-devon-rgr" / "SKILL.md").read_text()
    assert body == "canonical body\n"


def test_ensure_runtime_assets_links_venv(tmp_path):
    """B59 (#75): .venv joins the runtime asset set — agent-side guard/unit
    commands use the relative interpreter path ``.venv/bin/python``, which
    without the symlink only resolves in the main repo (run 01M0S0FQ T-001:
    Devon verified against the contaminated main tree instead of its clean
    worktree)."""
    repo, _ = _repo_with_opencode(tmp_path)
    venv_bin = repo / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").write_text("#!/bin/sh\n", encoding="utf-8")
    wt = tmp_path / "wt"
    wt.mkdir()
    ensure_runtime_assets(str(repo), str(wt))
    link = wt / ".venv"
    assert link.is_symlink()
    assert (link / "bin" / "python").exists()


def test_ensure_runtime_assets_idempotent(tmp_path):
    repo, _ = _repo_with_opencode(tmp_path)
    wt = tmp_path / "wt"
    wt.mkdir()
    ensure_runtime_assets(str(repo), str(wt))
    ensure_runtime_assets(str(repo), str(wt))  # second call must not raise
    assert (wt / ".opencode").is_symlink()


def test_ensure_runtime_assets_noop_without_source(tmp_path):
    repo, _ = _init_repo(tmp_path)  # no .opencode in main tree
    wt = tmp_path / "wt"
    wt.mkdir()
    ensure_runtime_assets(str(repo), str(wt))  # no source -> no link, no raise
    assert not (wt / ".opencode").exists()


def test_create_gate_worktree_links_opencode_and_ignored(tmp_path):
    repo, base = _repo_with_opencode(tmp_path)
    gate = create_gate_worktree(str(repo), base, "", "", "run-1", "T-001")
    try:
        link = Path(gate.path) / ".opencode"
        assert link.is_symlink()
        body = (link / "skills" / "tracks-devon-rgr" / "SKILL.md").read_text()
        assert body == "canonical body\n"
        # The symlink is gitignored (not untracked noise in the worktree).
        status = _git(repo, "-C", gate.path, "status", "--porcelain").stdout
        assert ".opencode" not in status
    finally:
        cleanup_worktree(gate)


def test_preexisting_gate_worktree_returns_cleanup_handle(tmp_path):
    """B2a (run 01KZTHE7): a pre-existing gate worktree must come back with a
    real handle so the gate runner's finally-cleanup removes it. The old
    ``return gate_path, None`` made every pre-existing worktree a permanent
    leak (five accumulated across T-013..T-018)."""
    from types import SimpleNamespace

    from tracks.executor.m_impl_runtime import MImplRuntimeMixin

    repo, _base = _init_repo(tmp_path)
    gate = repo / ".tracks" / "worktrees" / "run-1" / "T-001" / "gate"
    gate.mkdir(parents=True)
    runtime = SimpleNamespace(repo=repo, run_id="run-1")
    state = SimpleNamespace(current_task_id="T-001")
    cwd, handle = MImplRuntimeMixin._ensure_gate_worktree(runtime, state)
    assert cwd == str(gate)
    assert handle is not None
    assert handle.kind == "gate"
    cleanup_worktree(handle)
    assert not gate.exists()


def test_sweep_worktrees_removes_all_runs_registered_and_shells(tmp_path):
    """B2b: the sweep (trac start only) reclaims registered worktrees of every
    run plus unregistered directory shells, and prunes git's registry."""
    repo, base = _init_repo(tmp_path)
    mine = create_devon_worktree(str(repo), base, "run-1", "T-001")
    other = create_gate_worktree(str(repo), base, "", "", "run-2", "T-002")
    shell = repo / ".tracks" / "worktrees" / "run-3" / "T-003" / "gate"
    shell.mkdir(parents=True)
    (shell / "junk.py").write_text("x = 1\n", encoding="utf-8")
    removed = sweep_worktrees(str(repo))
    assert mine.path in removed and other.path in removed
    assert not Path(mine.path).exists()
    assert not Path(other.path).exists()
    assert not shell.exists()
    assert not (repo / ".tracks" / "worktrees").exists()
    survivors = {w.removeprefix("worktree ") for w in _worktree_list(repo)}
    assert str(repo) in survivors
    assert len(survivors) == 1


def test_sweep_worktrees_noop_when_absent(tmp_path):
    repo, _base = _init_repo(tmp_path)
    assert sweep_worktrees(str(repo)) == []


def test_seed_worktree_with_cycle_wip_carries_in_scope_dirty(tmp_path):
    """OOB 2026-09-06: a fresh writer worktree must see the cycle's
    accumulated WIP (run 01M19FJVES7G113RD8QXXY3PQZ: work built from clean
    HEAD every round -> 15 anchors never shrank). In-scope tracked edits AND
    new files seed; out-of-scope dirty content never crosses."""
    repo, base = _init_repo(tmp_path)
    # in-scope tracked file committed, then dirtied
    (repo / "tracks").mkdir()
    (repo / "tracks" / "a.py").write_text("v1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "a")
    (repo / "tracks" / "a.py").write_text("v2-wip\n")            # tracked dirty
    (repo / "tracks" / "b.py").write_text("new-wip\n")          # untracked in-scope
    (repo / "notes.md").write_text("operator-only\n")            # out-of-scope dirty
    wt = create_devon_worktree(str(repo), base, "run-1", "T-001")
    from tracks.executor.worktree import seed_worktree_with_cycle_wip
    n = seed_worktree_with_cycle_wip(str(repo), wt.path, ["tracks"])
    assert n == 2  # a.py + b.py; notes.md stays out
    assert (Path(wt.path) / "tracks" / "a.py").read_text() == "v2-wip\n"
    assert (Path(wt.path) / "tracks" / "b.py").read_text() == "new-wip\n"
    assert not (Path(wt.path) / "notes.md").exists()
    cleanup_worktree(wt)


def test_seed_worktree_with_cycle_wip_empty_scope_noop(tmp_path):
    """No scope -> nothing seeded (fail-closed, never bleeds operator state)."""
    repo, base = _init_repo(tmp_path)
    (repo / "README.md").write_text("dirty\n")
    wt = create_devon_worktree(str(repo), base, "run-1", "T-001")
    from tracks.executor.worktree import seed_worktree_with_cycle_wip
    assert seed_worktree_with_cycle_wip(str(repo), wt.path, []) == 0
    assert (Path(wt.path) / "README.md").read_text() == "hello\n"
    cleanup_worktree(wt)
