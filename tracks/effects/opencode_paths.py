"""Audit-path vocabulary mixin for OpencodeBackend.

Extracted from ``opencode.py`` for module-size compliance (C0302).
"""

from __future__ import annotations

from pathlib import Path

from tracks import paths
from tracks.project import COMMENTABLE_DOCS, layout_paths


class OpencodePathsMixin:
    """Target-doc / allowed-path / commentable-doc resolution."""

    @staticmethod
    def _assignment_forbidden_paths(assignment: dict | None) -> list[str]:
        """B64 (#82): repo-relative forbidden patterns from the assignment
        (executor-injected; see Executor._red_test_scope). Returns [] when
        the dispatch carries no ownership veto."""
        value = (assignment or {}).get("forbidden_paths")
        if not isinstance(value, list):
            return []
        return [p for p in value if isinstance(p, str) and p.strip()]

    def _dispatch_doc_paths(
        self,
        role: str,
        doc_path: Path | None,
        assignment: dict | None,
        worktree: Path | None,
        root: Path,
    ) -> list[Path]:
        """Target doc paths for a dispatch, shifted into the writer's view.

        Devon always works on its manifest paths; other writer dispatches
        (Shield WRITE) get the executor's main-tree absolutes shifted into
        the isolated worktree so the Auditor matches what it observes there
        (B1 Prism blocker fix)."""
        doc_paths = self._target_paths(doc_path, assignment)
        if role == "devon":
            return [
                root / path
                for path in (assignment or {}).get("manifest", {}).get("allowed_paths", [])
            ]
        if worktree is not None:
            return [
                root / p.relative_to(self.repo)
                if p.is_absolute() and p.is_relative_to(self.repo)
                else p
                for p in doc_paths
            ]
        return doc_paths

    def _allowed_paths(
        self,
        doc_paths: list[Path],
        agent_dest: Path,
        role: str,
        substate: str,
        assignment: dict | None = None,
        root: Path | None = None,
    ) -> list[Path | str | None]:
        """Audit whitelist = code dirs (project.toml [layout]) + commentable docs + agent_dest.

        ``root`` is the Auditor's repo (the isolated worktree for writer
        dispatches, else the main tree) — every path MUST resolve under
        it or the audit mis-flags every legal write as over-reach (B1
        Prism blocker fix). agent_dest stays a main-repo deployment
        path on purpose: it is gitignored, so the worktree's git status
        never reports writes there.
        """
        base = root if root is not None else self.repo
        commentable = self._commentable_doc_paths(role, base)
        if role == "shield":
            allowed = [base / d for d in layout_paths(base, "shield")]
            if substate == "WRITE":
                # WRITE target docs (incl. acceptance.md, not a COMMENTABLE_DOCS
                # entry) are whitelisted; replies there are discussion-checked.
                allowed = [*doc_paths, *allowed]
            # SHIELD_FIX may name diagnosed test files outside the Shield
            # [layout] dirs (e.g. a unit-level RED contract test); the
            # dispatch grants exactly those via manifest.allowed_paths and
            # the audit must honor the grant (Fix-M pattern, shield side).
            manifest_paths = self._manifest_allowed_paths(assignment, base)
            return [*commentable, *allowed, agent_dest, *manifest_paths]
        if role == "devon":
            devon_dirs = [base / d for d in layout_paths(base, "devon")]
            manifest_paths = self._manifest_allowed_paths(assignment, base)
            return [*commentable, agent_dest, *devon_dirs, *manifest_paths]
        return [*doc_paths, agent_dest, base]

    @staticmethod
    def _manifest_allowed_paths(assignment: dict | None, root: Path | None = None) -> list[Path]:
        """Explicit write whitelist from the assignment manifest (Archer-authored
        per-task allowed_paths); audit must honor it in addition to the coarse
        [layout] dirs, else a compliant out-of-layout write (e.g. a CI workflow
        path) is mis-flagged as over-reach and force-rolled-back."""
        manifest = (assignment or {}).get("manifest") or {}
        base = root if root is not None else Path(".")
        return [base / p for p in (manifest.get("allowed_paths") or [])]

    def _target_paths(self, doc_path: Path | None, assignment: dict | None) -> list[Path]:
        """Doc set of this dispatch: the explicit target doc, else the
        assignment's ``docs`` set resolved against the version dir (a multi-doc
        M-DESIGN DRAFT legitimately writes all three design docs, flow.md §8).
        Single-doc stages are unaffected: they always carry ``doc_path``."""
        if doc_path is not None:
            return [doc_path]
        docs = (assignment or {}).get("docs")
        if not docs:
            return []
        vdir = paths.version_dir(paths.tracks_home(self.repo), self.version)
        return [vdir / str(name) for name in docs]

    def _commentable_doc_paths(self, role: str, root: Path | None = None) -> list[Path]:
        """Every COMMENTABLE_DOCS path for the role, including ones missing at
        dispatch or gone post-run (deleted/type-swapped docs must be audited)."""
        cdocs = COMMENTABLE_DOCS.get(role)
        if not cdocs:
            return []
        vdir = paths.version_dir(paths.tracks_home(root or self.repo), self.version)
        return [vdir / d for d in cdocs]
