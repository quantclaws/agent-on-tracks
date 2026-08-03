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
    # Allowed = target only, NOT the repo root -> rogue dir is over-reach.
    auditor = Auditor(repo, allowed=[str(target)])
    baseline = auditor.baseline()

    # Simulate agent over-reach: a new untracked directory tree outside the
    # allowed target but still inside the repo (true over-reach because the
    # allowed set excludes the repo root).
    rogue = repo / "rogue_dir"
    rogue.mkdir(parents=True, exist_ok=True)
    (rogue / "nested").mkdir(parents=True, exist_ok=True)
    (rogue / "nested" / "x.json").write_text("{}", encoding="utf-8")

    assert auditor.audit(baseline) is not None  # over-reach detected
    rolled = auditor.rollback_agent_changes(baseline)
    assert any(r == "rogue_dir" or r == "rogue_dir/" for r in rolled)
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


def test_doc_set_allowed_accepts_trio_and_rejects_outside_writes(tmp_path):
    """Multi-doc contract (M-DESIGN DRAFT): allowed = the design trio. Writes to
    every doc of the set are legitimate; a write outside the set is over-reach,
    is detected with path-level evidence and rolled back — the trio survives."""
    repo = _repo(tmp_path)
    vdir = repo / ".tracks" / "projects" / "v0.3"
    vdir.mkdir(parents=True)
    trio = [vdir / doc for doc in
            ("architecture.md", "interfaces.md", "test-plan.md")]
    for path in trio:  # the trio already belongs to the version dir (like the
        path.write_text("# skeleton\n", encoding="utf-8")  # committed docs do)
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "trio"], cwd=repo, check=True, capture_output=True
    )

    auditor = Auditor(repo, allowed=[str(path) for path in trio])
    baseline = auditor.baseline()

    for path in trio:  # the legitimate multi-doc product
        path.write_text("# draft\n", encoding="utf-8")
    rogue = repo / "rogue.md"  # outside the doc set
    rogue.write_text("out-of-scope\n", encoding="utf-8")

    evidence = auditor.audit(baseline)
    assert evidence is not None and "rogue.md" in evidence
    assert all(path.name not in evidence for path in trio)
    rolled = auditor.rollback_agent_changes(baseline)
    assert "rogue.md" in rolled and not rogue.exists()
    assert all(path.exists() for path in trio)  # the trio is untouched


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
