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
        over = sorted(p for p in new_changes if p not in self.allowed)
        if over:
            return "over-reach: " + ", ".join(over)
        return None

    def rollback_agent_changes(self, baseline: set[str]) -> list[str]:
        """Revert only run-produced over-reach paths; never Human's baseline.

        Returns the paths rolled back. Tracked modifications are restored from
        HEAD; untracked new files are removed.
        """
        new_changes = self.modified_files() - baseline
        over = [p for p in new_changes if p not in self.allowed]
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
