"""Observation, dirty-tree bookkeeping, and document-path helpers extracted
from the Executor (mixin ``ExecObserveMixin``)."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from tracks import paths
from tracks.effects import oob
from tracks.executor.file_identity import (
    TREE_STAMP_SKIP_PREFIXES as _TREE_STAMP_SKIP_PREFIXES,
)
from tracks.executor.file_identity import path_identity, porcelain_path
from tracks.executor.helpers import _hook_output, git
from tracks.executor.result_checkpoint import _COMMITTED_EVENT
from tracks.kernel.machine import State


@dataclass(frozen=True)
class _TasksMdGuard:
    """A tasks.md projection mismatch awaiting attribution."""

    path: Path
    rendered: str
    post_md: str | None


class ExecObserveMixin:
    """Observation/dirty-tree handlers; the host Executor provides store/repo/run_id
and the remaining surface via self."""

    def _observe_oob(self) -> None:
        """D-36 浅版（#43）：观察派发窗口外的操作者提交并入账。

        HEAD 相对上次观察点前移时，``since..HEAD`` 中携带 ``Tracks-OOB``
        trailer 的提交逐个发出 ``oob.accepted``（sha/reason/files）；未
        声明的提交仅 stderr 提示后随静默期语义吸收（观察点前移，不发
        事件）。观察点单调前移保证幂等。

        Prism review #43 B1：``pending`` 未关闭（WAL 窗口内——典型为
        异常路径的 finally 调用）时绝不发射事件（B40 挂死条件：非
        command.issued 事件清 pending → pending=None + doc_dispatched=
        True → run 挂死）。此时观察整体推迟（不前移指针），待 pending
        关闭后的下一次观察补账。
        """
        current = oob.head_sha(Path(self.repo))
        if current is None or current == self._oob_head:
            return
        if self.store.state(self.run_id).pending is not None:
            return  # WAL 窗口内：推迟到 pending 关闭后再观察
        commits = oob.commits_since(Path(self.repo), self._oob_head)
        for c in commits:
            if c["oob"]:
                self._emit(
                    "oob.accepted",
                    {
                        "sha": c["sha"],
                        "reason": c["reason"],
                        "files": sorted(oob.changed_paths(Path(self.repo), c["sha"])),
                    },
                )
            else:
                print(
                    f"  [oob] undeclared operator commit {c['sha'][:12]} "
                    f"'{c['subject']}' — declare with a 'Tracks-OOB: <reason>' "
                    f"trailer to have it accepted/audited",
                    file=sys.stderr,
                    flush=True,
                )
        # 历史改写（range 不可解析 → commits 为空）时同样前移观察点。
        self._oob_head = current

    def _emit_commit_failure(
        self, proc: subprocess.CompletedProcess, state: State, command_id: str
    ) -> None:
        """D-30/F-1: pre-commit hook rejected the commit -> emit the established
        failure-evidence event (verdict.failed -> s.last_failure via
        _on_verdict_failed) carrying the hook's combined output, so decide()
        re-dispatches the agent to fix the deliverable (FR-11)."""
        self._emit_commit_failure_evidence(
            state,
            command_id,
            "pre-commit hook rejected the commit",
            _hook_output(proc),
        )

    def _emit_commit_failure_evidence(
        self, state: State, command_id: str, reason: str, evidence: str
    ) -> None:
        self._emit(
            "verdict.failed",
            {
                "check": "commit",
                "reason": reason,
                "evidence": evidence,
                "attempt": state.current_attempt + 1,
            },
            command_id=command_id,
        )

    def _doc_path(self, doc: str) -> Path:
        return paths.version_dir(self.store.home, self.version) / doc

    def _doc_paths(self, docs: list[str]) -> dict:
        """Map doc names to their filesystem paths."""
        return {doc: self._doc_path(doc) for doc in docs}

    def _artifact_path(self, name: str) -> Path:
        """Resolve an artifact name to its filesystem path for checkpoint
        operations. Known doc names (in ``_COMMITTED_EVENT``) resolve to the
        version dir; other names (e.g. ``tests/``) are repo-relative."""
        if name in _COMMITTED_EVENT:
            return self._doc_path(name)
        return self.repo / name

    def _artifact_paths(self, names: list[str]) -> dict:
        """Map artifact names to filesystem paths (version-dir docs or
        repo-relative paths)."""
        return {name: self._artifact_path(name) for name in names}

    def _dirty_files(self) -> set[str]:
        """Return repo-relative paths of all dirty (modified, staged, or
        untracked) files, excluding ignored files. Used to capture the
        exact file set written by Shield during M-TEST WRITE."""
        proc = git(self.repo, "status", "--porcelain", "-uall", check=False)
        files: set[str] = set()
        for line in proc.stdout.splitlines():
            if not line.strip():
                continue
            path = line[3:]
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            files.add(path.strip().strip('"'))
        return files

    def _dirty_snapshot(self) -> dict[str, str]:
        """Content-identity snapshot of all dirty files: {path: identity}.

        identity is sha256(content) for regular files, ``symlink:{target}``
        for symlinks, ``missing`` for deleted entries, ``unreadable`` for
        non-regular/permission-denied files. No mtime — deterministic and
        JSON-serializable so it can be persisted in ``command.issued`` and
        compared after the Agent returns (or after crash recovery)."""
        proc = git(self.repo, "status", "--porcelain", "-uall", check=False)
        snapshot: dict[str, str] = {}
        for line in proc.stdout.splitlines():
            if not line.strip():
                continue
            path = line[3:]
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            path = path.strip().strip('"')
            snapshot[path] = self._path_identity(self.repo / path)
        return snapshot

    @staticmethod
    def _path_identity(path: Path) -> str:
        return path_identity(path)

    def _resolve_pre_dirty(self, state, substate, params):
        """Resolve the pre-dispatch dirty baseline for M-TEST Shield WRITE.

        Prefers the persisted content-identity snapshot (new commands); falls
        back to the legacy path-set for backward compat with old WALs; finally
        falls back to a fresh snapshot. Returns ``None`` outside M-TEST/WRITE
        (including v0.5 no_diff peer-review substates — no file attribution)."""
        if not (state.stage == "M-TEST" and substate == "WRITE"):
            return None
        if substate in ("NO_DIFF_EXPLAIN", "NO_DIFF_REVIEW"):
            return None
        if "pre_dirty_snapshot" in params:
            return params["pre_dirty_snapshot"]
        if "pre_dirty" in params:
            return set(params["pre_dirty"])
        return self._dirty_snapshot()

    def _handle_tasks_md_projection(self, cmd, state, task_id, params: dict) -> str:
        """OOB b89 OB-4 incremental attribution guard.

        Returns ``violation`` (fail-closed, already emitted ``verdict.failed`` +
        ``tasks_md.restored``), ``repaired`` (emitted ``tasks_md.system_repaired``),
        ``ok`` (no action), or ``skipped`` (tasks.json unparsable).

        The guard is evaluated on every dispatch that could touch
        ``.tracks/projects/<version>/tasks.{json,md}``.  It is a pure
        file-content check plus an incremental-diff attribution using the
        dispatched ``pre_dirty_snapshot``.
        """
        guard = self._tasks_md_guard()
        if isinstance(guard, str):
            return guard
        return self._apply_tasks_md_guard(cmd, state, task_id, params, guard)

    def _tasks_md_guard(self):
        """Load the rendered tasks.md and its on-disk content, or a skip/ok str."""
        # Resolve version dir (same as MImplRuntimeMixin._vdir)
        try:
            vdir = self._vdir()
        except Exception:
            return "skipped"
        tasks_json_path = vdir / "tasks.json"
        tasks_md_path = vdir / "tasks.md"
        # No graph yet → nothing to guard
        if not tasks_json_path.exists():
            return "skipped"
        # Read and parse tasks.json (guard skips when unparsable → taskgraph channel reports)
        try:
            raw_json = tasks_json_path.read_text(encoding="utf-8")
        except Exception:
            return "skipped"
        try:
            from tracks.executor.taskgraph import parse_tasks_json, render_tasks_md
        except Exception:
            return "skipped"
        tasks, err = parse_tasks_json(raw_json)
        if err is not None:
            return "skipped"
        try:
            rendered = render_tasks_md(tasks)
        except Exception:
            return "skipped"
        # Post tasks.md content (None when missing → treat as mismatch with rendered)
        try:
            post_md = (
                tasks_md_path.read_text(encoding="utf-8")
                if tasks_md_path.exists()
                else None
            )
        except Exception:
            post_md = None
        # Fast path: match → nothing to do (no restoration, no events)
        # Note: rendered is never None here; if post_md is None, it's a mismatch.
        if rendered == post_md:
            return "ok"
        return _TasksMdGuard(tasks_md_path, rendered, post_md)

    def _apply_tasks_md_guard(self, cmd, state, task_id, params: dict, guard) -> str:
        """Mismatch → decide violation vs system_repaired via attribution."""
        from tracks.executor.taskgraph import classify_tasks_md_guard

        pre = self._extract_pre_snapshot_for_guards(params)
        try:
            post_snapshot = self._dirty_snapshot()
        except Exception:
            post_snapshot = {}
        rel_md = self._tasks_md_relpath(guard.path)
        pre_contains = self._is_path_in_diff(rel_md, pre, post_snapshot)
        decision = classify_tasks_md_guard(pre_contains, guard.rendered, guard.post_md)
        if decision == "violation":
            # Fail-closed: this turn's diff introduced the mismatch
            self._emit(
                "verdict.failed",
                {
                    "check": "tasks_md_projection",
                    "reason": (
                        "tasks.md is a runtime projection of tasks.json; "
                        "modify tasks.json instead"
                    ),
                    "evidence": (
                        "tasks.md mismatch: expected render of tasks.json but "
                        "got different content; diff contains tasks.md "
                        "(attributed to this turn)"
                    ),
                    "attempt": state.current_attempt + 1,
                },
                command_id=cmd.command_id,
                task_id=task_id,
            )
            # Restore in the same command (audited, idempotent)
            self._restore_tasks_md(
                guard.path, guard.rendered, "tasks_md restore failed"
            )
            self._emit(
                "tasks_md.restored",
                {
                    "path": "tasks.md",
                    "reason": "tasks_md_projection violation — restored to render(tasks.json)",
                },
                command_id=cmd.command_id,
                task_id=task_id,
            )
            return "violation"
        if decision == "system_repaired":
            # Inherited dirty (crash, checkout residual): self-heal without violation
            self._restore_tasks_md(
                guard.path, guard.rendered, "tasks_md system repair failed"
            )
            self._emit(
                "tasks_md.system_repaired",
                {
                    "path": "tasks.md",
                    "reason": "tasks.md mismatched but not in this turn's diff — system repaired",
                },
                command_id=cmd.command_id,
                task_id=task_id,
            )
            return "repaired"
        return "ok"

    def _tasks_md_relpath(self, tasks_md_path: Path) -> str:
        """Repo-relative key for tasks.md (fallback .tracks/projects form)."""
        try:
            return str(tasks_md_path.relative_to(Path(self.repo)).as_posix())  # type: ignore[arg-type]
        except Exception:
            try:
                return str(
                    tasks_md_path.relative_to(Path(self.repo).resolve()).as_posix()
                )
            except Exception:
                return tasks_md_path.name

    @staticmethod
    def _restore_tasks_md(tasks_md_path: Path, rendered: str, message: str) -> None:
        """Best-effort restore; a failure prints but never crashes the guard."""
        try:
            tasks_md_path.parent.mkdir(parents=True, exist_ok=True)
            tasks_md_path.write_text(rendered, encoding="utf-8")
        except Exception as exc:
            print(f"{message}: {exc}", file=sys.stderr, flush=True)

    def _dirty_tree_stamp(self, root: Path | None = None) -> str:
        """Dirty-aware worktree content stamp over relevant source/test/config
        files (review pin: selection identity must move when dirty R2 content
        changes under an identical node set and HEAD).

        Clean relevant tree -> the HEAD commit identity; dirty -> a sha256
        over HEAD plus the changed paths' current content. Porcelain rename
        pairs (``R  old -> new``, FRB-E) are keyed by their DESTINATION and
        hash the destination file's live content, so mutating a renamed file
        moves the stamp. Runtime state (.tracks/, caches, venvs, build
        output) never enters the stamp: it must stay deterministic across
        WAL/replay of the same command."""
        repo = Path(root) if root is not None else Path(self.repo)
        head = git(repo, "rev-parse", "HEAD", check=False).stdout.strip()
        status = git(repo, "status", "--porcelain", "-uall", check=False).stdout
        dirty: dict[str, str] = {}
        for line in status.splitlines():
            path = porcelain_path(line)  # FRB-E: rename hashes the dest
            if path is None:
                continue
            if (
                not path
                or path.startswith(_TREE_STAMP_SKIP_PREFIXES)
                or "__pycache__/" in path
                or Path(path).name.startswith(".coverage")
            ):
                continue
            try:
                digest = hashlib.sha256(
                    (repo / path).read_bytes()
                ).hexdigest()
            except OSError:
                digest = "unreadable"
            dirty[path] = digest
        if not dirty:
            return head  # clean relevant tree -> plain HEAD identity
        canonical = json.dumps(
            {"head": head, "dirty": dirty},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _oob_accepted_test_files(self) -> set[str]:
        """Test files carried by operator OOB declarations (oob.accepted).

        2026-08-27 doctrinal gap (run 01M0S0FQ M-TEST park): operator
        emergency fixes ship with their regression tests, and those tests are
        necessarily green-on-arrival -- the fix is already on the tree. The
        must-be-red doctrine (a v0.7 acceptance instrument must fail until
        M-IMPL implements it) does not apply to them: they are operator
        verification of landed behavior, not version acceptance instruments.
        The OOB channel is the system's own declaration for exactly this
        operator scope; files it recorded are exempt from the unexpected_pass
        verdict (classified ``oob_verified`` instead)."""
        files: set[str] = set()
        for ev in self.store.events(self.run_id):
            if ev.type != "oob.accepted":
                continue
            for path in ev.payload.get("files") or []:
                if isinstance(path, str) and path.startswith("tests/"):
                    files.add(path)
        return files
