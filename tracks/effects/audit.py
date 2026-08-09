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

    def __init__(self, repo: Path, allowed: list[Path | str | None]):
        self.repo = Path(repo)
        self.allowed: set[str] = set()
        for p in allowed:
            if p is not None:
                self.allowed.add(_rel(self.repo, Path(p)))
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

    # -- snapshot -----------------------------------------------------------

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
        out = _git(self.repo, "status", "--porcelain=v1", "-z",
                   "--untracked-files=all").stdout
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
            return _git(self.repo, "diff", "--no-index", "--", "/dev/null", rel,
                        check=False).stdout or None
        return None

    def untracked_under(self, prefix: str) -> set[str]:
        """Untracked files under ``prefix``. A directory-level status entry
        (``dir/``, emitted when everything under it is untracked) hides
        per-file detail; audits that judge individual paths need the file
        granularity this provides."""
        out = _git(self.repo, "ls-files", "--others", "--exclude-standard",
                   "--", prefix).stdout
        return {line.strip().strip('"')
                for line in out.splitlines() if line.strip()}

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
        covers every file written beneath it."""
        if path in self.allowed:
            return True
        return any(path.startswith(a + "/") for a in self.allowed)

    def _overreach_paths(self, new_changes: set[str],
                         force: bool) -> list[str]:
        """Paths to roll back: every new_change when ``force``, else only
        those outside the allowed set."""
        if force:
            return list(new_changes)
        return [p for p in new_changes if not self._is_allowed(p)]

    def _ancestor_escapes_repo(self, p: str) -> bool:
        """True if any *ancestor* component of ``p`` (relative to repo) is a
        symlink that resolves outside the repo, or is dangling/unreadable.
        The final component is NOT checked here (callers handle it via
        :meth:`_path_type`).

        Fail-closed (BOOT-ROLLBACK-001): a dangling or unreadable ancestor
        symlink is treated as an escape so rollback never touches a path it
        cannot prove is inside the repo.  Both lexical and resolved
        containment are checked: ``resolve()`` follows the entire symlink
        chain, and ``relative_to`` fails if the fully-resolved target is
        not under the repo root."""
        parts = Path(p).parts
        if len(parts) <= 1:
            return False  # no ancestors
        repo_resolved = self.repo.resolve()
        current = repo_resolved
        for part in parts[:-1]:
            current = current / part
            if current.is_symlink():
                resolved = self._resolve_ancestor(current, repo_resolved)
                if resolved is None:
                    return True  # fail closed
                current = resolved
        return False

    @staticmethod
    def _resolve_ancestor(link: Path, repo_resolved: Path) -> Path | None:
        """Resolve one ancestor symlink, or return None when unsafe."""
        try:
            target = os.readlink(link)
        except OSError:
            return None  # unreadable -> fail closed
        link_target = Path(target)
        if not link_target.is_absolute():
            link_target = link.parent / target
        try:
            resolved = link_target.resolve()
        except OSError:
            return None  # dangling -> fail closed
        try:
            resolved.relative_to(repo_resolved)
        except ValueError:
            return None  # escapes repo -> fail closed
        return resolved

    def rollback_agent_changes(self, baseline: set[str],
                               new_changes: set[str] | None = None,
                               force: bool = False) -> list[str]:
        """Revert only run-produced over-reach paths; never Human's baseline.

        Returns the paths rolled back. Tracked modifications are restored from
        HEAD; untracked new files are removed. ``new_changes`` - when given,
        the authoritative run-produced changed-file set (file granularity, as
        computed by the batch B scaffold audit); otherwise derived from the
        baseline difference. ``force`` - when True, roll back EVERY path in
        ``new_changes`` regardless of whether it is in the allowed set (used
        by the Shield M-TEST discussion-only guard: a non-discussion doc edit
        invalidates the entire run, including allowed test-asset writes).

        Paths are sorted shallowest-first (BOOT-ROLLBACK-001) so an ancestor
        symlink is restored/removed before any descendant is touched.  This
        ensures that after the ancestor is restored to a real directory, the
        descendant path is safe to process.  Paths whose ancestor still
        escapes the repo (pre-dirty symlink to outside, or ancestor not in
        the rollback set) are skipped via :meth:`_ancestor_escapes_repo`
        (fail closed)."""
        if new_changes is None:
            new_changes = self.modified_files() - baseline
        over = self._overreach_paths(new_changes, force)
        # Shallowest-first: ancestors before descendants so a symlink ancestor
        # is restored before its children are accessed.
        over.sort(key=lambda p: (p.count("/"), p))
        tracked_set = set(_git(self.repo, "ls-files").stdout.splitlines())
        rolled: list[str] = []
        for p in over:
            if self._rollback_one(p, tracked_set):
                rolled.append(p)
        return rolled

    def _rollback_one(self, p: str, tracked_set: set[str]) -> bool:
        """Restore one over-reach path to its pre-run state, then prune any
        now-empty parent dirs the run created.

        Returns True if the path was rolled back, False if it was skipped
        (fail-closed: an ancestor symlink escapes the repo).

        Path handling is **lexical, no-follow, and type-aware**: a regular
        assignment doc replaced by a file/directory/dangling symlink is
        detected via ``_path_type``; the replacement (symlink or directory
        tree) is removed WITHOUT following links, and the original is restored
        from the pre-dispatch snapshot or HEAD.  This prevents writing through
        a replacement symlink to an external target.

        Ancestor symlink safety (BOOT-ROLLBACK-001): before any FS operation,
        :meth:`_ancestor_escapes_repo` checks whether any ancestor component
        is a symlink resolving outside the repo.  If so, the path is skipped
        (returns False) so the external target is never deleted, restored
        through, or parent-cleaned.  Callers that sort shallowest-first
        ensure that after an ancestor is restored, descendants become safe."""
        if self._ancestor_escapes_repo(p):
            return False  # fail closed - external target untouched
        full = self.repo / p
        # Phase 1: remove whatever the agent left at this lexical path.
        # is_symlink is checked first (never follows) so we unlink the link
        # itself, not its target.  A directory (real, not a symlink-to-dir)
        # is removed as a tree.  A regular file is unlinked.
        if full.is_symlink():
            full.unlink()
        elif full.is_dir():
            shutil.rmtree(full, ignore_errors=True)
        elif full.exists():
            full.unlink()
        # Phase 2: restore the original (pre-dispatch / HEAD) content.
        if p in self._baseline_symlinks:
            full.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(self._baseline_symlinks[p], full)
        elif p in self._baseline_content:
            # Pre-dirty at dispatch: restore the pre-dispatch content
            # (Human's uncommitted work), NOT HEAD - ``git checkout HEAD``
            # would erase it. Recreate the file (and parents) if the agent
            # deleted it; a restored file keeps its parent non-empty so the
            # prune-empty loop below is a no-op for it.
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_bytes(self._baseline_content[p])
        elif p in tracked_set:
            _git(self.repo, "checkout", "--", p, check=False)
        # If neither: agent-created untracked file, already removed in phase 1.
        # Prune now-empty parent dirs created by the run so a leaf-only
        # rollback (file granularity, not ``dir/``) does not leave an
        # empty ``tests/`` skeleton behind (the directory-tree-is-gone
        # invariant of the directory-level rollback). Stop at repo root
        # and never remove tracked dirs or non-empty dirs.
        parent = full.parent
        while parent != self.repo and parent.exists():
            if any(parent.iterdir()):
                break
            if str(parent.relative_to(self.repo)) in tracked_set:
                break  # don't delete a tracked (even if empty) dir
            parent.rmdir()
            parent = parent.parent
        return True
