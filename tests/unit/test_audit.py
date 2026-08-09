"""Auditor — over-reach detection + rollback (ARCH-003 §6, FR-030).

Ground truth locked here so the live channel's directory-level over-reach fix
(directory rollback via shutil.rmtree) cannot silently regress.
"""
import os
import shutil
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


def test_force_rollback_restores_predirty_symlink_without_following(tmp_path):
    """A replaced pre-dirty symlink is recreated with its exact lexical target."""
    repo = _repo(tmp_path)
    link = repo / "linked.md"
    os.symlink("committed-target", link)
    subprocess.run(["git", "add", "linked.md"], cwd=repo, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-m", "link"], cwd=repo, check=True,
                   capture_output=True)

    link.unlink()
    os.symlink("../human-target", link)
    external = repo.parent / "human-target"
    external.write_bytes(b"human external bytes\n")
    auditor = Auditor(repo, allowed=[link])
    baseline = auditor.baseline()

    link.unlink()
    link.write_bytes(b"agent replacement\n")
    changed = auditor.agent_changed_paths(baseline)
    auditor.rollback_agent_changes(baseline, changed, force=True)

    assert link.is_symlink()
    assert os.readlink(link) == "../human-target"
    assert external.read_bytes() == b"human external bytes\n"


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


# -- BOOT-ROLLBACK-001: ancestor symlink escape protection -------------------
#
# ``_rollback_one("nested/victim", ...)`` only did no-follow on the FINAL
# component.  When ``nested`` is a symlink to a directory OUTSIDE the repo,
# every FS operation on ``nested/victim`` follows the ancestor symlink and
# mutates the external target (delete / restore-through / parent-cleanup).
# The rollback set comes from a ``set`` with unstable ordering, so the child
# may be processed before the ancestor.
#
# Fix: (a) sort rollback paths shallowest-first so ancestors are restored
# before descendants; (b) add ``_ancestor_escapes_repo`` safety check that
# fail-closes (skips) any path whose ancestor symlink resolves outside the
# repo (or is dangling), so the external target is never touched.


def _git_lsfiles(repo):
    out = subprocess.run(["git", "ls-files"], cwd=repo, capture_output=True,
                         text=True).stdout
    return set(out.splitlines())


def test_rollback_one_child_of_ancestor_symlink_preserves_external(tmp_path):
    """BOOT-ROLLBACK-001: ``_rollback_one`` on a child path whose ancestor is
    a symlink to an external directory must NOT follow the ancestor symlink
    and delete/modify the external target.

    Directly calls ``_rollback_one("nested/victim", ...)`` to reproduce the
    dangerous child-first order that unstable set iteration can produce: the
    ancestor symlink is still in place, so the child path resolves outside
    the repo.  The safety check must fail closed."""
    repo = _repo(tmp_path)
    nested = repo / "nested"
    nested.mkdir()
    victim = nested / "victim"
    victim.write_text("original\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-m", "nested"], cwd=repo,
                   check=True, capture_output=True)

    outside = tmp_path / "outside"
    outside.mkdir()
    outside_victim = outside / "victim"
    outside_victim.write_bytes(b"external bytes\n")

    # Agent replaces nested/ directory with a symlink to the external dir.
    shutil.rmtree(nested)
    os.symlink(outside, nested)

    auditor = Auditor(repo, allowed=[repo / "target.md"])
    tracked_set = _git_lsfiles(repo)

    # Dangerous order: child before ancestor.  Must fail closed.
    result = auditor._rollback_one("nested/victim", tracked_set)

    assert result is False  # skipped, external untouched
    assert outside_victim.exists()
    assert outside_victim.read_bytes() == b"external bytes\n"


def test_rollback_ancestor_symlink_full_rollback_preserves_external(tmp_path):
    """BOOT-ROLLBACK-001: ``rollback_agent_changes`` with both the ancestor
    symlink and its child in the set processes the ancestor first (shallowest
    sort), restoring it to a real directory before the child is touched.
    The external target remains byte-identical; the repo state is restored."""
    repo = _repo(tmp_path)
    nested = repo / "nested"
    nested.mkdir()
    victim = nested / "victim"
    victim.write_text("original\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-m", "nested"], cwd=repo,
                   check=True, capture_output=True)

    auditor = Auditor(repo, allowed=[repo / "target.md"])
    baseline = auditor.baseline()  # clean - before agent changes

    outside = tmp_path / "outside"
    outside.mkdir()
    outside_victim = outside / "victim"
    outside_victim.write_bytes(b"external bytes\n")

    # Agent replaces nested/ directory with a symlink to the external dir.
    shutil.rmtree(nested)
    os.symlink(outside, nested)

    # Explicitly include both paths to test the sort + safety check together.
    rolled = auditor.rollback_agent_changes(
        baseline, new_changes={"nested/victim", "nested"}, force=True)

    assert outside_victim.exists()
    assert outside_victim.read_bytes() == b"external bytes\n"
    assert not nested.is_symlink()
    assert nested.is_dir()
    assert victim.read_text(encoding="utf-8") == "original\n"
    assert "nested" in rolled
    assert "nested/victim" in rolled


def test_rollback_dangling_ancestor_symlink_fails_closed(tmp_path):
    """BOOT-ROLLBACK-001: a dangling ancestor symlink (target does not exist)
    is treated as an escape (fail closed).  Rollback of a child path is
    skipped; no crash."""
    repo = _repo(tmp_path)
    nested = repo / "nested"
    os.symlink("/nonexistent/dangling/target", nested)

    auditor = Auditor(repo, allowed=[repo / "target.md"])
    tracked_set = _git_lsfiles(repo)

    result = auditor._rollback_one("nested/victim", tracked_set)
    assert result is False


def test_rollback_ancestor_symlink_to_external_file_fails_closed(tmp_path):
    """BOOT-ROLLBACK-001: an ancestor symlink pointing to an external file
    (not a directory) is also treated as an escape.  Rollback of a child
    path is skipped; the external file is unchanged."""
    repo = _repo(tmp_path)
    external_file = tmp_path / "external.txt"
    external_file.write_bytes(b"external file\n")
    nested = repo / "nested"
    os.symlink(external_file, nested)

    auditor = Auditor(repo, allowed=[repo / "target.md"])
    tracked_set = _git_lsfiles(repo)

    result = auditor._rollback_one("nested/victim", tracked_set)
    assert result is False
    assert external_file.read_bytes() == b"external file\n"


def test_rollback_predirty_ancestor_symlink_escape_fails_closed(tmp_path):
    """BOOT-ROLLBACK-001: a pre-dirty ancestor symlink to an external
    directory is in baseline but NOT in the rollback set (the agent did not
    change the ancestor itself).  A child path behind it IS in the rollback
    set.  The child must be skipped (fail closed) because the ancestor
    still escapes after rollback.  The external target is unchanged."""
    repo = _repo(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_victim = outside / "victim"
    outside_victim.write_bytes(b"external bytes\n")

    nested = repo / "nested"
    os.symlink(outside, nested)  # pre-dirty ancestor symlink to external dir
    subprocess.run(["git", "add", "nested"], cwd=repo, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-m", "link"], cwd=repo,
                   check=True, capture_output=True)

    auditor = Auditor(repo, allowed=[repo / "target.md"])
    baseline = auditor.baseline()
    # Agent "changes" nested/victim (behind the symlink).  The ancestor
    # nested itself is NOT in new_changes (agent did not touch it).
    rolled = auditor.rollback_agent_changes(
        baseline, new_changes={"nested/victim"}, force=True)

    assert "nested/victim" not in rolled  # skipped (fail closed)
    assert outside_victim.exists()
    assert outside_victim.read_bytes() == b"external bytes\n"
