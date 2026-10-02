"""#220 (2026-10-02): an anchor-typed R (no tests/ files) must keep the GREEN
gate on the observed main-tree candidate.

Live fire (run 01M3E7SAANXKW1V73W8B8Q3G86, T-004): the anchor R carried
product-WIP residue (checkpoint_red has no product-file contamination
guard); the old `bool(rels) and ...` inverted the vacuous truth, forced
gate-worktree assembly, and its cherry-pick of the R conflicted with the
candidate diff on the same product files (attention.required
gate_worktree/creation_failed)."""

from __future__ import annotations

from types import SimpleNamespace

from tracks.executor import m_impl_testops as testops
from tracks.executor.m_impl_testops import MImplTestOpsMixin


class _FakeGit:
    def __init__(self, files):
        self.files = files
        self.returncode = 0
        self.stdout = "\n".join(files) + ("\n" if files else "")


def _make_exec(tmp_path, r_files, monkeypatch):
    """Patch the module-level git() helper at a fake diff-tree result and
    return a bare mixin instance (no namespace surgery: monkeypatch restores)."""
    proc = _FakeGit(r_files)
    monkeypatch.setattr(testops, "git", lambda *a, **k: proc)

    class _Exec(MImplTestOpsMixin):
        repo = str(tmp_path)

    return _Exec()


def test_anchor_r_without_tests_uses_main_tree(tmp_path, monkeypatch):
    """R with zero tests/ files: vacuously present -> True (no worktree)."""
    exec_ = _make_exec(
        tmp_path,
        ["tracks/server/api_command.py", "tracks/server/supervisor/service.py"],
        monkeypatch,
    )
    assert exec_._r_tests_in_working_tree("RSHA") is True


def test_r_with_tests_checks_presence(tmp_path, monkeypatch):
    exec_ = _make_exec(
        tmp_path, ["tests/unit/test_a.py", "tracks/server/x.py"], monkeypatch
    )
    unit_dir = tmp_path / "tests" / "unit"
    unit_dir.mkdir(parents=True, exist_ok=True)
    (unit_dir / "test_a.py").write_text("x")
    assert exec_._r_tests_in_working_tree("RSHA") is True
    (unit_dir / "test_a.py").unlink()
    assert exec_._r_tests_in_working_tree("RSHA") is False


def test_gate_inputs_none_for_anchor_r(tmp_path, monkeypatch):
    """The fix's pin (Prism B2): a resolvable base + a non-empty green diff
    with an anchor R must return None — the gate stays on the main tree.
    Under the OLD `bool(rels) and ...` this returned the (R, base, diff)
    triple and forced the conflicting worktree assembly."""
    exec_ = _make_exec(tmp_path, ["tracks/server/api_command.py"], monkeypatch)
    monkeypatch.setattr(
        MImplTestOpsMixin, "_usable_tree_identity", staticmethod(lambda _s: True)
    )
    monkeypatch.setattr(testops, "red_base_sha", lambda *a, **k: "BASE")
    exec_._latest_phase_diff_ref = lambda _phase: "diff-blob-ref"
    state = SimpleNamespace(r_tree_identity="RSHA")
    assert exec_._gate_candidate_inputs(state, "T-004", "green") is None
