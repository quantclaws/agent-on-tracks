"""Writer-worktree lifecycle extracted from the Executor (mixin
``ExecWorktreeMixin``)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from tracks.executor.helpers import git
from tracks.executor.worktree import (
    WorktreeHandle,
    _writer_worktree_path,
    cleanup_worktree,
    create_devon_worktree,
    create_test_authority_worktree,
    ensure_runtime_assets,
    runtime_asset_paths,
    seed_worktree_with_cycle_wip,
)


class ExecWorktreeMixin:
    """Devon/test-authority worktree open/replay/sync handlers."""

    def _act_in_worktree(
        self,
        cmd,
        role,
        substate,
        doc,
        doc_path,
        assignment,
        handle,
        wt_task_id,
    ):
        """Run backend.act() (inside the writer worktree when one is open),
        replay the worktree delta onto the main tree, and always close the
        worktree (audited via ``worktree.closed``). Returns
        ``(result, replay_error)``; replay_error None means clean/applied."""
        replay_error: str | None = None
        replayed = False
        try:
            result = self.backend.act(
                role,
                substate,
                doc,
                doc_path,
                assignment=assignment,
                worktree=Path(handle.path) if handle is not None else None,
            )
            if handle is not None:
                replay_error, replayed = self._replay_worktree_to_main(handle)
        finally:
            if handle is not None:
                cleanup_worktree(handle)
                self._emit(
                    "worktree.closed",
                    {
                        "kind": handle.kind,
                        "path": handle.path,
                        "task_id": wt_task_id,
                        "replayed": replayed,
                    },
                    command_id=cmd.command_id,
                    task_id=wt_task_id,
                )
        return result, replay_error

    def _open_writer_worktree(self, state, role: str, substate: str, cmd):
        """B1 (issue #2): open the per-dispatch worktree for writer agents.

        Devon RED/GREEN/REFACTOR (M-IMPL) get a devon_candidate worktree
        keyed by the task lease; Shield WRITE (M-TEST) gets a
        test_authority worktree. Both are created from the main HEAD —
        NOT a stale C_design — so re-dispatches after G/R commits see
        the committed work. Returns (handle, task_id) or (None, None)
        for every other dispatch.
        """
        kind: str | None = None
        task_id: str | None = None
        if (
            state.stage == "M-IMPL"
            and role == "devon"
            and substate in ("RED", "GREEN", "REFACTOR")
        ):
            kind, task_id = "devon", state.current_task_id or ""
        elif state.stage == "M-TEST" and role == "shield" and substate == "WRITE":
            kind = "test_authority"
        if kind is None or (kind == "devon" and not task_id):
            return None, None
        head = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        seed_note: dict | None = None
        if kind == "devon":
            self._clear_stale_worktree_path(
                _writer_worktree_path(str(self.repo), self.run_id, task_id, "devon"),
                "devon_candidate",
            )
            handle = create_devon_worktree(str(self.repo), head, self.run_id, task_id)
            seed_note = self._seed_devon_worktree_wip(state, handle.path)
        else:
            self._clear_stale_worktree_path(
                _writer_worktree_path(str(self.repo), self.run_id, None, "test_authority"),
                "test_authority",
            )
            handle = create_test_authority_worktree(str(self.repo), head, self.run_id)
        ensure_runtime_assets(str(self.repo), handle.path)
        self._emit(
            "worktree.opened",
            {
                "kind": handle.kind,
                "path": handle.path,
                "task_id": task_id,
                "base_sha": head,
                "attempt": state.current_attempt + 1,
                # Prism P4: the writer's actual input surface is HEAD + the
                # seeded cycle WIP -- the audit event records it so replay
                # can reconstruct what the writer saw.
                **({"devon_wip_seed": seed_note} if seed_note is not None else {}),
            },
            command_id=cmd.command_id,
            task_id=task_id,
        )
        return handle, task_id

    def _seed_devon_worktree_wip(self, state, worktree_path: str) -> dict:
        """OOB 2026-09-06: seed a fresh Devon worktree with the current
        cycle's accumulated WIP so a re-dispatched writer resumes from it
        instead of clean HEAD (worktree blindness, run
        01M19FJVES7G113RD8QXXY3PQZ: one 2h20m round produced zero net
        change). Prism P4: the seeded surface is recorded for the audit
        event -- replay reconstructs what the writer actually saw; a
        missing manifest (no scope derivable) degrades loudly, never
        silently blind."""
        manifest = state.current_manifest or {}
        scope = [p for p in (manifest.get("allowed_paths") or []) if isinstance(p, str)]
        seeded = seed_worktree_with_cycle_wip(str(self.repo), worktree_path, scope)
        note = {"seeded_wip_paths": seeded}
        if seeded:
            print(
                f"  [worktree] seeded {seeded} in-scope WIP path(s) into the "
                f"devon worktree (cycle accumulation)",
                file=sys.stderr,
                flush=True,
            )
        elif manifest.get("allowed_paths") is None:
            note["seeded_wip_paths"] = "degraded: no manifest scope"
        return note

    def _clear_stale_worktree_path(self, path: str, kind: str) -> None:
        """Reclaim this exact path if a crashed dispatch left it behind.

        Deliberately NOT the B2 sweep (user ruling: full sweeps run only at
        ``trac start``) — this reclaims only the one path this dispatch is
        about to occupy, so a crash between two runs cannot brick the
        loop's next writer dispatch.
        """
        if not os.path.exists(path):
            return
        cleanup_worktree(WorktreeHandle(path=path, base_sha="", kind=kind))

    def _replay_worktree_to_main(self, handle: WorktreeHandle) -> tuple[str | None, bool]:
        """Apply the worktree's working-tree delta onto the main tree.

        ``git add -A`` + ``git diff --cached --binary`` captures tracked
        edits plus new files; ``git apply`` (bytes mode — binary patches
        must never be decoded as text) writes them into the main tree
        WITHOUT staging, so downstream pipelines see exactly the state the
        agent would have left had it worked in the main tree.
        Returns (error, had_delta); error None means clean or applied ok.

        D-36 浅版（#43/B36 反碾压守卫）：主树在派发窗口内被操作者提交
        改动过的文件，mirror 回退不再覆盖——同名冲突 fail-closed 报错
        （主树侧内容保留，人工裁决后重试），杜绝 concurrent-collision
        replay 静默吞掉操作者已提交的工作。

        B60 (#76)：``ensure_runtime_assets`` 在 agent 运行**前**把
        ``runtime_asset_paths`` 链接进 worktree（声明的环境目录是符号链接），而
        canonical ``.gitignore`` 的该目录尾斜杠模式只匹配目录、
        不匹配符号链接——``git add -A`` 会把它 stage 进 replay diff，
        ``git apply`` 拒绝后 mirror 兜底 copy2 目录直接 Errno 21。故
        add 后对每个 runtime asset 显式 ``git reset -q``（只动 index，
        不碰 working tree）：replay diff 只携带 agent 工作增量，环境
        管线永不入镜。reset 对未 staged 的 asset 是无害 no-op 错误。
        """
        wt = handle.path
        subprocess.run(
            ["git", "-C", wt, "add", "-A"],
            capture_output=True,
            check=False,
        )
        for asset in runtime_asset_paths(str(self.repo)):
            subprocess.run(
                ["git", "-C", wt, "reset", "-q", "--", asset],
                capture_output=True,
                check=False,
            )
        diff = subprocess.run(
            ["git", "-C", wt, "diff", "--cached", "--binary"],
            capture_output=True,
            check=False,
        )
        if not diff.stdout.strip():
            # No file delta, but the agent may still have created empty
            # directories (git diffs cannot carry them; e.g. Shield's
            # tests/assets scaffold). Sync those so the replayed state
            # matches what working directly in the main tree would leave.
            return self._sync_worktree_dirs(handle), False
        applied = subprocess.run(
            ["git", "-C", str(self.repo), "apply", "--whitespace=nowarn", "-"],
            input=diff.stdout,
            capture_output=True,
            check=False,
        )
        if applied.returncode != 0:
            # Fast path refused (e.g. the main tree holds an uncommitted
            # file the diff creates). Mirror the changed files instead:
            # per-file copy/delete from the worktree's final state, which
            # is exactly the state the agent would have left had it
            # worked directly in the main tree (a direct agent edit also
            # overwrites a pre-existing dirty file) — EXCEPT paths the
            # operator committed on main during the dispatch window
            # (#43 anti-clobber guard: those conflict fail-closed).
            return self._mirror_worktree_changes(handle), True
        return self._sync_worktree_dirs(handle), True

    def _main_committed_since(self, base_sha: str) -> set[str]:
        """派发窗口内主树侧被提交改动过的路径（base_sha..HEAD，B36 守卫用）。

        Runtime 是单写者且在派发期间阻塞，窗口内主树 HEAD 前移只能是
        操作者提交（声明与否在守卫处不区分——碾压数据是更重的伤害）。
        base_sha 不可解析（历史改写）时返回空集（守卫退化为旧行为）。
        """
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(self.repo),
                "diff",
                "--name-only",
                "-z",
                f"{base_sha}..HEAD",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            return set()
        return {p for p in proc.stdout.split("\0") if p}

    def _sync_worktree_dirs(self, handle: WorktreeHandle) -> str | None:
        """mkdir -p every directory present in the worktree but missing in
        the main tree (agent-created scaffolding; empty dirs are invisible
        to git diffs). Never deletes anything; .git and symlinks are not
        traversed. Returns an error string or None."""
        wt = Path(handle.path)
        for dirpath, dirnames, _filenames in os.walk(wt, followlinks=False):
            dirnames[:] = [d for d in dirnames if d != ".git"]
            for name in dirnames:
                src = Path(dirpath) / name
                rel = src.relative_to(wt)
                dst = self.repo / rel
                if not dst.exists():
                    try:
                        dst.mkdir(parents=True, exist_ok=True)
                    except OSError as exc:
                        return f"mkdir {rel}: {exc}"
        return None

    def _mirror_worktree_changes(self, handle: WorktreeHandle) -> str | None:
        """Per-file sync of the worktree's staged changes onto the main
        tree (replay fallback). Returns an error string or None.

        D-36 浅版（#43）反碾压守卫：与派发窗口内主树侧提交重叠的路径
        fail-closed——不镜像、保留主树内容、报冲突清单（all-or-nothing：
        发现任一冲突即整个 mirror 不执行，避免留下半镜像的混合状态）。
        """
        names = subprocess.run(
            ["git", "-C", handle.path, "diff", "--cached", "--name-only", "-z"],
            capture_output=True,
            text=True,
            check=False,
        )
        changed = [n for n in names.stdout.split("\0") if n]
        guarded = self._main_committed_since(handle.base_sha)
        conflicts = sorted(set(changed) & guarded)
        if conflicts:
            return (
                "main tree moved during dispatch (operator commits) conflict "
                f"with agent worktree delta: {', '.join(conflicts)} — "
                "operator content preserved on main; resolve manually "
                "(re-dispatch will re-run the agent on the new HEAD)"
            )
        for name in changed:
            src = Path(handle.path) / name
            dst = self.repo / name
            try:
                if src.exists():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                else:
                    dst.unlink(missing_ok=True)
            except OSError as exc:
                return f"mirror {name}: {exc}"
        return None
