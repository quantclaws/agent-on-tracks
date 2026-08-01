"""Auditor — over-reach detection + rollback (ARCH-003 §6, FR-030).

Ground truth locked here so the live channel's directory-level over-reach fix
(directory rollback via shutil.rmtree) cannot silently regress.
"""
import subprocess

from tracks.effects.audit import Auditor


def _repo(tmp_path):
    repo = tmp_path / "host"
    repo.mkdir()
    for cmd in (
        ["init", "-b", "main"],
        ["config", "user.email", "t@t"],
        ["config", "user.name", "T"],
    ):
        subprocess.run(["git", *cmd], cwd=repo, check=True, capture_output=True)
    (repo / "target.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True
    )
    return repo


def test_directory_over_reach_rolled_back_removes_tree(tmp_path):
    """An agent that creates a directory outside the allowed set is reverted:
    the whole tree (not just a file) must be removed by rollback."""
    repo = _repo(tmp_path)
    target = repo / "target.md"
    auditor = Auditor(repo, allowed=[str(target)])
    baseline = auditor.baseline()

    # Simulate agent over-reach: a new untracked directory tree on disk.
    rogue = repo / ".tracks"
    rogue.mkdir()
    (rogue / "nested").mkdir()
    (rogue / "nested" / "x.json").write_text("{}", encoding="utf-8")

    assert auditor.audit(baseline) is not None  # over-reach detected
    rolled = auditor.rollback_agent_changes(baseline)
    assert any(r == ".tracks" or r == ".tracks/" for r in rolled)
    assert not rogue.exists()  # the directory tree is gone
    assert target.exists()  # the allowed target is untouched


def test_tracked_over_reach_restored_from_head(tmp_path):
    """A tracked file modified out-of-bounds is restored to HEAD, not deleted."""
    repo = _repo(tmp_path)
    target = repo / "target.md"
    # A second tracked file the agent is NOT allowed to touch.
    other = repo / "other.md"
    other.write_text("base2\n", encoding="utf-8")
    subprocess.run(["git", "add", "other.md"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "other"], cwd=repo, check=True, capture_output=True
    )

    auditor = Auditor(repo, allowed=[str(target)])
    baseline = auditor.baseline()
    other.write_text("agent wrote here\n", encoding="utf-8")

    rolled = auditor.rollback_agent_changes(baseline)
    assert "other.md" in rolled
    assert other.read_text(encoding="utf-8") == "base2\n"


def test_baseline_human_changes_never_rolled_back(tmp_path):
    """Human's pre-existing modifications (in baseline) are never reverted."""
    repo = _repo(tmp_path)
    target = repo / "target.md"
    human = repo / "human.md"
    human.write_text("human work\n", encoding="utf-8")

    auditor = Auditor(repo, allowed=[str(target)])
    baseline = auditor.baseline()  # human.md is already modified -> in baseline
    assert "human.md" in baseline

    rolled = auditor.rollback_agent_changes(baseline)
    assert "human.md" not in rolled
    assert human.exists()
