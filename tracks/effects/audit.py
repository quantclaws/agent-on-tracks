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

    # -- snapshot -----------------------------------------------------------

    def modified_files(self) -> set[str]:
        """Repo-relative paths currently modified/untracked vs HEAD."""
        out = _git(self.repo, "status", "--porcelain").stdout
        files: set[str] = set()
        for line in out.splitlines():
            if len(line) < 4:
                continue
            path = line[3:].strip()
            if " -> " in path:  # rename: keep the new path
                path = path.split(" -> ", 1)[1]
            files.add(path.strip('"'))
        return files

    def baseline(self) -> set[str]:
        """Pre-run snapshot of already-modified paths (Human's work)."""
        return self.modified_files()

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

    def rollback_agent_changes(self, baseline: set[str],
                               new_changes: set[str] | None = None) -> list[str]:
        """Revert only run-produced over-reach paths; never Human's baseline.

        Returns the paths rolled back. Tracked modifications are restored from
        HEAD; untracked new files are removed. ``new_changes`` — when given,
        the authoritative run-produced changed-file set (file granularity, as
        computed by the batch B scaffold audit); otherwise derived from the
        baseline difference.
        """
        if new_changes is None:
            new_changes = self.modified_files() - baseline
        over = [p for p in new_changes if not self._is_allowed(p)]
        rolled: list[str] = []
        tracked = _git(self.repo, "ls-files").stdout.splitlines()
        tracked_set = set(tracked)
        for p in over:
            full = self.repo / p
            if p in tracked_set:
                _git(self.repo, "checkout", "--", p, check=False)
            elif full.is_dir():
                shutil.rmtree(full, ignore_errors=True)
            elif full.exists():
                full.unlink()
            rolled.append(p)
        return rolled
