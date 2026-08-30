"""Run-level over-reach audit (ARCH-003 §6 / SPEC-003 FR-030).

v0.2 contract (Aaron): full Human/Agent serialization is deferred to the web UI;
until then Runtime *detects* over-reach via baseline + post-run git diff, it does
not prevent concurrent human edits.

Model:
- ``baseline()`` snapshots the set of already-modified paths (Human's pre-existing
  work) before the agent runs.
- ``audit(baseline)`` compares the post-run modified set; paths that became
  modified *during the run* (post - baseline) and lie outside the allowed set
  (target doc + command_id temp dir) are over-reach -> evidence string.
- ``rollback_agent_changes(...)`` reverts only run-produced over-reach paths,
  never Human's baseline modifications.
"""

from __future__ import annotations

import contextlib
import fnmatch
import os
import shutil
import subprocess
from pathlib import Path


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc


def _rel(repo: Path, path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(repo.resolve()))
    except ValueError:
        return str(path)


class Auditor:
    """Detects agent over-reach against a pre-run baseline."""

    def __init__(
        self,
        repo: Path,
        allowed: list[Path | str | None],
        forbidden: list[str] | None = None,
    ):
        self.repo = Path(repo)
        self.allowed: set[str] = set()
        for p in allowed:
            if p is not None:
                self.allowed.add(_rel(self.repo, Path(p)))
        # B64 (#82): ownership veto — repo-relative patterns (exact file,
        # directory prefix, or glob) that are NEVER writable regardless of
        # any allow grant. Injected from assignment data (e.g. Shield's
        # SHIELD_FIX domain excludes the run's red_test_paths — Devon's RED
        # unit artifacts, Archer-authored manifest data). Data-driven: the
        # runtime never hardcodes which paths are owned by whom.
        self.forbidden: list[str] = [
            p for p in (forbidden or []) if isinstance(p, str) and p.strip()
        ]
        # Pre-dispatch on-disk content of each baseline path, captured in
        # ``baseline()`` so a ``force`` rollback can restore Human's uncommitted
        # (pre-dirty) content instead of erasing it back to HEAD.
        self._baseline_content: dict[str, bytes] = {}
        # Pre-dispatch lexical type of each baseline path (``"file"``,
        # ``"dir"``, ``"symlink"``, ``"missing"``) so type changes (e.g. a
        # regular doc replaced by a symlink) are detected and rolled back
        # correctly without following links.
        self._baseline_types: dict[str, str] = {}
        # Exact lexical target text for pre-dirty symlinks. Reading and later
        # recreating the link never touches its target, including if dangling.
        self._baseline_symlinks: dict[str, str] = {}
        # Paths tracked in HEAD (lazy, from ``git ls-tree``). The rollback
        # restore phase identifies "tracked" against HEAD, not the index:
        # an agent that staged a deletion (``git rm``) has no index entry
        # left, and an index-based lookup would misclassify the path as an
        # agent-created untracked file and destroy it (run 01KZTHE7 T-017).
        self._head_tracked_cache: set[str] | None = None

    # -- snapshot -----------------------------------------------------------

    def _head_tracked(self) -> set[str]:
        """Lazily-cached set of paths tracked in HEAD (``git ls-tree``).
        Empty when HEAD does not exist yet (fresh repo)."""
        if self._head_tracked_cache is None:
            proc = _git(self.repo, "ls-tree", "-r", "--name-only", "HEAD")
            self._head_tracked_cache = {
                line for line in proc.stdout.splitlines() if line
            }
        return self._head_tracked_cache

    @staticmethod
    def _path_type(full: Path) -> str:
        """Lexical, no-follow type of a path: ``"symlink"``, ``"dir"``,
        ``"file"``, or ``"missing"``.  ``is_symlink`` is checked first (it
        never follows), so subsequent ``is_dir`` / ``exists`` calls only run
        on non-symlink paths and are safe."""
        if full.is_symlink():
            return "symlink"
        if full.is_dir():
            return "dir"
        if full.exists():
            return "file"
        return "missing"

    @staticmethod
    def _symlink_target(full: Path) -> str | None:
        """Return exact link text without following it, or None if unreadable."""
        try:
            return os.readlink(full)
        except OSError:
            return None

    # -- snapshot -----------------------------------------------------------

    def modified_files(self) -> set[str]:
        """Repo-relative paths currently modified/untracked vs HEAD.

        Untracked directories are expanded to leaf files
        (``--untracked-files=all``) so a clean host that gains only allowed
        nested files (e.g. ``tests/integration/foo.py`` + ``tests/e2e/bar.py``)
        is NOT mis-flagged as over-reach via a collapsed ``?? tests/`` directory
        entry (E2E run001 audit bug: the whole ``tests/`` parent was reported
        as over-reach even though every leaf was allowed). NUL-delimited
        (``-z``) parsing is robust to paths containing spaces, quotes, and
        other special characters (no C-quoting in -z mode); renames keep their
        new path (the old path is a separate NUL entry with no XY prefix and
        is skipped)."""
        out = _git(self.repo, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
        files: set[str] = set()
        entries = out.split("\0")
        i = 0
        while i < len(entries):
            entry = entries[i]
            i += 1
            if len(entry) < 4:
                continue
            xy, path = entry[:2], entry[3:]
            if (xy[0] == "R" or xy[1] == "R") and i < len(entries):
                # Rename: the next NUL entry is the old path (no XY prefix);
                # keep the new path, skip the old.
                i += 1
            files.add(path)
        return files

    def baseline(self) -> set[str]:
        """Pre-run snapshot of already-modified paths (Human's work).

        Also captures the on-disk content and lexical type of each baseline
        path so a later ``force`` rollback can restore the pre-dispatch
        (pre-dirty) content of a tracked file Human pre-modified - ``git
        checkout HEAD`` would erase that uncommitted work if a Shield
        force-rollback touches the path.  Type capture (file/dir/symlink/
        missing) lets ``agent_changed_paths`` detect type swaps (e.g. a
        regular doc replaced by a symlink) and lets ``_rollback_one`` restore
        the original without following replacement links."""
        paths = self.modified_files()
        self._baseline_content = {}
        self._baseline_types = {}
        self._baseline_symlinks = {}
        for p in paths:
            full = self.repo / p
            ptype = self._path_type(full)
            self._baseline_types[p] = ptype
            if ptype == "file":
                with contextlib.suppress(OSError):
                    self._baseline_content[p] = full.read_bytes()
            elif ptype == "symlink":
                target = self._symlink_target(full)
                if target is not None:
                    self._baseline_symlinks[p] = target
        return paths

    def baseline_content(self, rel: str) -> bytes | None:
        """Pre-dispatch on-disk content of a baseline path (Human's
        pre-existing work), or None if the path was clean/absent at dispatch
        (not in the baseline snapshot)."""
        return self._baseline_content.get(rel)

    def baseline_type(self, rel: str) -> str | None:
        """Pre-dispatch lexical type of a baseline path (``"file"``,
        ``"dir"``, ``"symlink"``, ``"missing"``), or None if the path was
        clean/absent at dispatch."""
        return self._baseline_types.get(rel)

    def agent_changed_paths(self, baseline: set[str]) -> set[str]:
        """Every path whose identity/content/type changed during the run,
        relative to the pre-dispatch snapshot.

        Covers three categories:
        - **clean -> changed**: paths in ``modified_files()`` that were NOT in
          baseline (the run produced them).
        - **pre-dirty -> agent-modified**: paths in baseline whose current
          lexical type differs from the snapshot type (e.g. file replaced by
          symlink/directory), or whose byte content drifted from the snapshot.
        - **pre-dirty -> deleted**: paths in baseline whose current type is
          ``"missing"`` (the agent deleted them).

        Used by the Shield WRITE guard for one atomic rollback decision: the
        force-rollback set must include modified pre-dirty test assets and
        allowed doc edits, not just clean->changed out-of-scope writes."""
        current = self.modified_files()
        changed: set[str] = set(current - baseline)
        for p in baseline:
            snap = self._baseline_content.get(p)
            snap_type = self._baseline_types.get(p, "missing")
            full = self.repo / p
            cur_type = self._path_type(full)
            if cur_type != snap_type:
                changed.add(p)
            elif cur_type == "symlink":
                if self._symlink_target(full) != self._baseline_symlinks.get(p):
                    changed.add(p)
            elif snap is not None and cur_type == "file":
                try:
                    if full.read_bytes() != snap:
                        changed.add(p)
                except OSError:
                    changed.add(p)
        return changed

    # -- product + audit ----------------------------------------------------

    def target_diff(self, doc_path: Path | None) -> str | None:
        """Controlled diff of the target doc = authoritative product (ARCH §4b).

        None when the target has no diff. Untracked new files are diffed against
        /dev/null so their full content is captured as additions.
        """
        if doc_path is None:
            return None
        rel = _rel(self.repo, Path(doc_path))
        diff = _git(self.repo, "diff", "--", rel).stdout
        if diff:
            return diff
        staged = _git(self.repo, "diff", "--cached", "--", rel).stdout
        if staged:
            return staged
        # Untracked NEW file the agent created: show full content as additions.
        # A tracked-but-unmodified file has no diff -> None (no product).
        tracked = _git(self.repo, "ls-files", "--", rel).stdout.strip()
        if not tracked and (self.repo / rel).exists():
            return (
                _git(self.repo, "diff", "--no-index", "--", "/dev/null", rel, check=False).stdout
                or None
            )
        return None

    def untracked_under(self, prefix: str) -> set[str]:
        """Untracked files under ``prefix``. A directory-level status entry
        (``dir/``, emitted when everything under it is untracked) hides
        per-file detail; audits that judge individual paths need the file
        granularity this provides."""
        out = _git(self.repo, "ls-files", "--others", "--exclude-standard", "--", prefix).stdout
        return {line.strip().strip('"') for line in out.splitlines() if line.strip()}

    def file_level(self, paths: set[str]) -> set[str]:
        """``paths`` at FILE granularity: every directory-level entry (``dir/``)
        is expanded into the untracked files it contains, so audits judge
        files, not collapsed directories (a dir that expands to nothing is
        kept as-is)."""
        files: set[str] = set()
        for path in paths:
            if path.endswith("/"):
                files |= self.untracked_under(path) or {path}
            else:
                files.add(path)
        return files

    def audit(self, baseline: set[str]) -> str | None:
        """Over-reach evidence (run-produced changes outside allowed) or None.

        When ``"."`` is in the allowed set (meaning the entire repo root is
        trusted), the agent may write anything inside the working tree — only
        writes that escape the repo are true over-reach (FR-030: coarse-grained
        frontmatter permission + post-run audit; the sandbox is the repo dir).
        """
        if "." in self.allowed:
            return None
        new_changes = self.modified_files() - baseline
        over = sorted(p for p in new_changes if not self._is_allowed(p))
        if over:
            return "over-reach: " + ", ".join(over)
        return None

    def _is_allowed(self, path: str) -> bool:
        """A path is allowed when it is an exact match or lives under an
        allowed directory (prefix match), so a directory entry in ``allowed``
        covers every file written beneath it. B64 (#82): the forbidden veto
        runs FIRST — a forbidden exact/prefix/glob match is over-reach no
        matter which allow entry would otherwise cover it."""
        for pat in self.forbidden:
            if path == pat:
                return False
            if path.startswith(pat.rstrip("/") + "/"):
                return False
            if fnmatch.fnmatch(path, pat):
                return False
        if path in self.allowed:
            return True
        return any(path.startswith(a + "/") for a in self.allowed)

    def _overreach_paths(self, new_changes: set[str], force: bool) -> list[str]:
        """Paths to roll back: every new_change when ``force``, else only
        those outside the allowed set."""
        if force:
            return list(new_changes)
        return [p for p in new_changes if not self._is_allowed(p)]

    def _ancestor_blocks(self, p: str) -> bool:
        """True if any *lexical* ancestor component of ``p`` (relative to
        repo) is currently a symlink.  The final component is NOT checked
        here (callers handle it via :meth:`_path_type`).

        No-follow, lexical type detection (BOOT-ROLLBACK-001): ANY symlink
        ancestor - internal, external, or dangling - blocks a descendant
        operation, because ``is_dir``/``exists``/``unlink``/``write_bytes``
        all follow the link and would mutate its target (e.g. a tracked
        ``other/victim`` the agent never touched, reachable through an
        internal symlink).  This deliberately does NOT resolve the link for
        repo containment: a symlink that resolves inside the repo is just as
        unsafe to follow as an escaping one.  A symlink ancestor that is
        itself in the rollback set is removed first by the shallowest-first
        symlink removal, so its original-path children become safe to restore
        afterwards."""
        parts = Path(p).parts
        if len(parts) <= 1:
            return False  # no ancestors
        current = self.repo.resolve()
        for part in parts[:-1]:
            current = current / part
            if current.is_symlink():
                return True
        return False

    def rollback_agent_changes(
        self, baseline: set[str], new_changes: set[str] | None = None, force: bool = False
    ) -> list[str]:
        """Revert only run-produced over-reach paths; never Human's baseline.

        Returns the paths rolled back. Tracked modifications are restored from
        HEAD; untracked new files are removed. ``new_changes`` - when given,
        the authoritative run-produced changed-file set (file granularity, as
        computed by the batch B scaffold audit); otherwise derived from the
        baseline difference. ``force`` - when True, roll back EVERY path in
        ``new_changes`` regardless of whether it is in the allowed set (used
        by the Shield M-TEST discussion-only guard: a non-discussion doc edit
        invalidates the entire run, including allowed test-asset writes).

        Rollback runs in TWO type-aware phases so an agent-swapped tree is
        unwound leaves-first without ever walking a child through a restored
        regular file:
        - **Removal** (deepest-first, :meth:`_remove_one`): every run-produced
          artifact is removed from the leaves up - the anomalous directory
          tree (e.g. a commentable regular doc replaced by a non-empty
          directory with payload/deep children) is taken apart before its
          parent is touched, so no child path is ever visited after its
          parent was restored to a regular file (which would raise
          NotADirectoryError and abort the whole rollback as a filesystem
          failure).
        - **Restore** (shallowest-first, :meth:`_restore_one`): the original
          content (pre-dispatch snapshot for Human-dirty paths, else HEAD)
          is written back after every descendant is gone, and now-empty
          parent dirs the run created are pruned.

        Both phases keep the BOOT-ROLLBACK-001 fail-closed contract: any
        path whose lexical ancestor is currently a symlink - internal,
        external, or dangling - is skipped via :meth:`_ancestor_blocks`
        before any FS operation, so a link target (whether in or out of the
        repo) is never deleted, restored through, or parent-cleaned.  The
        removal phase removes rollback-set symlinks shallowest-first so a
        descendant behind an in-set symlink ancestor is safe to restore
        afterwards; a symlink ancestor that is NOT in the rollback set keeps
        its descendants skipped per-path (fail closed)."""
        if new_changes is None:
            new_changes = self.modified_files() - baseline
        over = self._overreach_paths(new_changes, force)
        tracked_set = set(_git(self.repo, "ls-files").stdout.splitlines())
        tracked_set |= self._head_tracked()
        # Phase 1 - removal.  First remove every rollback-set path that is
        # currently a symlink, shallowest-first: unlinking a symlink never
        # follows its target (internal or external), so this is always safe
        # and it clears the way for the original-path descendants of an
        # in-set symlink ancestor to be handled lexically (restored per
        # baseline) in phase 2.
        removed: set[str] = set()
        over_sorted = sorted(over, key=lambda p: (p.count("/"), p))
        for p in over_sorted:
            if self._path_type(self.repo / p) == "symlink" and self._remove_one(p):
                removed.add(p)
        # Then deepest-first for everything else: children before their
        # ancestor, so a plain non-empty directory replacement is dismantled
        # from the leaves up and the ancestor (which may be restored to a
        # regular file) is only handled once nothing is left beneath it.
        for p in sorted(over_sorted, key=lambda p: (-p.count("/"), p)):
            if p in removed:
                continue
            if self._remove_one(p):
                removed.add(p)
        # Phase 2 - restore, shallowest-first: ancestors (BOOT-ROLLBACK-001)
        # before descendants, so a tracked child is checked out only after
        # its ancestor was restored to a real directory.
        rolled: list[str] = []
        for p in sorted(over, key=lambda p: (p.count("/"), p)):
            restored = self._restore_one(p, tracked_set)
            if p in removed or restored:
                rolled.append(p)
        return rolled

    def _rollback_one(self, p: str, tracked_set: set[str]) -> bool:
        """Restore one over-reach path to its pre-run state (single-path
        equivalent of the two-phase :meth:`rollback_agent_changes`).

        Returns True if the path was rolled back, False if it was skipped
        (fail-closed: a lexical ancestor is a symlink).

        Path handling is **lexical, no-follow, and type-aware**: a regular
        assignment doc replaced by a file/directory/dangling symlink is
        detected via ``_path_type``; the replacement (symlink or directory
        tree) is removed WITHOUT following links, and the original is restored
        from the pre-dispatch snapshot or HEAD.  This prevents writing through
        a replacement symlink to an external or internal target."""
        removed = self._remove_one(p)
        restored = self._restore_one(p, tracked_set)
        return removed or restored

    def _remove_one(self, p: str) -> bool:
        """Removal phase of one rollback path: take apart whatever the agent
        left at this lexical path WITHOUT following links - unlink a
        replacement symlink, remove a directory tree, or unlink a regular
        file.  Returns True if anything was removed, False if the path was
        skipped (fail-closed: an ancestor symlink escapes the repo) or was
        already gone.

        ``is_symlink`` is checked first (never follows) so a link is unlinked
        itself, never its target.  Type probes (``is_dir``/``exists``) return
        False without raising for a path whose parent was already restored to
        a regular file, so a child whose ancestor is being unwound by the
        deepest-first caller is a safe no-op here."""
        if self._ancestor_blocks(p):
            return False  # fail closed - never follow an ancestor symlink
        full = self.repo / p
        if full.is_symlink():
            full.unlink()
            return True
        if full.is_dir():
            shutil.rmtree(full, ignore_errors=True)
            return True
        if full.exists():
            full.unlink()
            return True
        return False

    def _restore_one(self, p: str, tracked_set: set[str]) -> bool:
        """Restore phase of one rollback path: write back the original
        (pre-dispatch / HEAD) content, then prune any now-empty parent dirs
        the run created.  Returns True unless the path was skipped
        (fail-closed: an ancestor symlink escapes the repo).

        Pre-dirty at dispatch: restore the pre-dispatch content (Human's
        uncommitted work), NOT HEAD - ``git checkout HEAD`` would erase it.
        Recreates the file (and parents) if the agent deleted it; a restored
        file keeps its parent non-empty so the prune below is a no-op for it.
        A tracked path whose parent was already restored to a regular file
        cannot be checked out (its ancestor's restoration already removed the
        whole replacement tree) and is skipped."""
        if self._ancestor_blocks(p):
            return False  # fail closed - never follow an ancestor symlink
        full = self.repo / p
        if p in self._baseline_symlinks:
            full.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(self._baseline_symlinks[p], full)
        elif p in self._baseline_content:
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_bytes(self._baseline_content[p])
        elif p in tracked_set:
            if full.parent.exists() and not full.parent.is_dir():
                return False  # parent is a restored regular file, not a dir
            # Restore from HEAD for HEAD-tracked paths, NOT the index: an
            # agent that staged a deletion (e.g. ``git rm``) leaves no index
            # entry, so ``git checkout -- p`` no-ops and the tracked file is
            # destroyed while the rollback reports success (run 01KZTHE7
            # T-017, 2026-08-17). ``checkout HEAD -- p`` overwrites both the
            # index entry and the worktree file. Index-only entries (staged
            # adds with no commit yet) still restore from the index.
            if p in self._head_tracked():
                _git(self.repo, "checkout", "HEAD", "--", p, check=False)
            else:
                _git(self.repo, "checkout", "--", p, check=False)
            return full.exists()
        # If none of the above: agent-created untracked file, already removed
        # in the removal phase - nothing to restore.
        self._prune_empty_parents(full, tracked_set)
        return True

    def _prune_empty_parents(self, full: Path, tracked_set: set[str]) -> None:
        """Prune now-empty parent dirs created by the run so a leaf-only
        rollback (file granularity, not ``dir/``) does not leave an empty
        ``tests/`` skeleton behind (the directory-tree-is-gone invariant of
        the directory-level rollback).  Stop at repo root; never remove
        tracked dirs or non-empty dirs.  ``parent.is_dir()`` (not
        ``exists()``) guards the walk: a parent that was restored to a
        regular file is not a directory, so iterating it would raise
        NotADirectoryError - the walk simply stops there."""
        parent = full.parent
        while parent != self.repo and parent.is_dir():
            if any(parent.iterdir()):
                break
            if str(parent.relative_to(self.repo)) in tracked_set:
                break  # don't delete a tracked (even if empty) dir
            parent.rmdir()
            parent = parent.parent
