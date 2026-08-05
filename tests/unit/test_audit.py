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
    # Leaf granularity (run001 fix): the rolled entry is the actual untracked
    # file, not a collapsed ``rogue_dir/`` directory entry. Empty parent dirs
    # are pruned so the whole tree is still removed.
    assert "rogue_dir/nested/x.json" in rolled
    assert not rogue.exists()  # the directory tree is gone (prune-empty)
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


# -- run001 regression: collapsed ``?? tests/`` directory entries ----------------
#
# A clean host has no tests/ directory; Shield writes only two allowed nested
# files. Git's default porcelain collapses these to ``?? tests/`` so the audit
# reported ``over-reach: tests/`` even though both leaf files are allowed. The
# fix enumerates actual untracked leaf paths and enforces the allowed-path
# policy per leaf. The whole tests/ parent is NOT allowed.


def test_clean_host_two_allowed_nested_files_accepted(tmp_path):
    """run001: repo initially lacks tests/; creating only two allowed nested
    files (tests/integration/test_code_stats_contract.py and
    tests/e2e/test_code_stats_happy.py) is accepted, NOT flagged as over-reach
    via a collapsed ``?? tests/`` directory entry."""
    repo = _repo(tmp_path)
    # Shield scope (FR-0120 RP-01): the four test-asset directories, no
    # repo-root trust, no ``tests/`` parent in the allowed set.
    allowed = [repo / d for d in
               ("tests/integration", "tests/e2e",
                "tests/assets", "tests/counterexamples")]
    auditor = Auditor(repo, allowed=allowed)
    baseline = auditor.baseline()
    assert not any(p.startswith("tests") for p in baseline)  # clean host

    # Shield writes exactly the two allowed nested files (no tests/ existed).
    integration = repo / "tests" / "integration" / "test_code_stats_contract.py"
    e2e = repo / "tests" / "e2e" / "test_code_stats_happy.py"
    integration.parent.mkdir(parents=True)
    e2e.parent.mkdir(parents=True)
    integration.write_text("def test_contract(): pass\n", encoding="utf-8")
    e2e.write_text("def test_happy(): pass\n", encoding="utf-8")

    assert auditor.audit(baseline) is None  # both leaves are allowed


def test_allowed_nested_file_plus_rogue_over_reach_rolled_back(tmp_path):
    """One allowed nested file plus ``tests/rogue.py`` is over-reach; rollback
    removes only the rogue file (and the now-empty dir it alone occupied), never
    the allowed sibling file or its directory."""
    repo = _repo(tmp_path)
    allowed = [repo / d for d in
               ("tests/integration", "tests/e2e",
                "tests/assets", "tests/counterexamples")]
    auditor = Auditor(repo, allowed=allowed)
    baseline = auditor.baseline()

    allowed_file = (repo / "tests" / "integration"
                    / "test_code_stats_contract.py")
    rogue = repo / "tests" / "rogue.py"
    allowed_file.parent.mkdir(parents=True)  # creates tests/ + tests/integration/
    rogue.parent.mkdir(parents=True, exist_ok=True)  # tests/ already exists
    allowed_file.write_text("def test_contract(): pass\n", encoding="utf-8")
    rogue.write_text("# over-reach\n", encoding="utf-8")

    evidence = auditor.audit(baseline)
    assert evidence is not None
    assert "tests/rogue.py" in evidence
    assert "test_code_stats_contract.py" not in evidence

    rolled = auditor.rollback_agent_changes(baseline)
    assert "tests/rogue.py" in rolled
    assert "tests/integration/test_code_stats_contract.py" not in rolled
    assert not rogue.exists()  # rogue removed
    assert allowed_file.exists()  # allowed sibling survives rollback
    assert allowed_file.parent.exists()  # tests/integration/ survives


def test_nul_parsing_handles_renames_spaces_and_untracked_leaves(tmp_path):
    """NUL-delimited (-z) porcelain parsing: renames keep the new path (old
    path skipped), paths with spaces are preserved (no C-quoting in -z mode),
    and untracked files under a new directory are enumerated as leaves (not
    collapsed to a ``dir/`` directory entry)."""
    repo = _repo(tmp_path)
    old = repo / "old_name.txt"
    tracked = repo / "tracked.md"
    for path in (old, tracked):
        path.write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=repo,
                   check=True, capture_output=True)
    # rename: old_name.txt -> renamed.txt
    subprocess.run(["git", "mv", "old_name.txt", "renamed.txt"], cwd=repo,
                   check=True, capture_output=True)
    # modify a tracked file
    tracked.write_text("changed\n", encoding="utf-8")
    # untracked file with spaces in the name
    spaced = repo / "with space.txt"
    spaced.write_text("sp\n", encoding="utf-8")
    # untracked file nested under a new directory
    nested = repo / "newdir" / "leaf.py"
    nested.parent.mkdir(parents=True)
    nested.write_text("# new\n", encoding="utf-8")

    auditor = Auditor(repo, allowed=[str(repo / "target.md")])
    files = auditor.modified_files()

    assert "renamed.txt" in files          # rename: new path kept
    assert "old_name.txt" not in files     # rename: old path skipped
    assert "tracked.md" in files           # tracked modification
    assert "with space.txt" in files       # spaces preserved (no C-quoting)
    assert "newdir/leaf.py" in files       # untracked dir expanded to leaf
    assert not any(p == "newdir/" or p == "newdir" for p in files)  # not collapsed
