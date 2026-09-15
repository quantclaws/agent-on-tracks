"""Runtime entry command face: init/start/run and the Human review loop.

Extracted from :mod:`tracks.cli.main` for module-size compliance (C0302):
scaffold/start/run-loop entry points and the human triage/review
pipelines. ``tracks.cli.main`` re-exports every name below.
"""

from __future__ import annotations

import ast
import json
import os
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from tracks import paths, templating
from tracks.executor import Executor, git
from tracks.executor.code_stamp import RuntimeCodeDriftError
from tracks.executor.executor import _resolve_run_version
from tracks.executor.stall import CommandStallError
from tracks.kernel import Command
from tracks.store import Store, new_ulid

from .common import (
    PROJECTS_GITIGNORE,
    RUNTIME_GITIGNORE,
    TRACKS_GITIGNORE,
    _ensure_host_byproducts_gitignore,
    _err,
    _format_state,
    _human_actor,
    active_run_context,
    writer_lock,
)


def cmd_init(repo: Path) -> int:
    home = paths.tracks_home(repo)
    for d in (paths.projects_dir(home), paths.runtime_dir(home), paths.wiki_dir(home)):
        d.mkdir(parents=True, exist_ok=True)
    gitignore = paths.runtime_dir(home) / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(RUNTIME_GITIGNORE, encoding="utf-8")
    # .tracks/-level transient artifacts: trac report output and discuss
    # flock lock files.  Idempotent: write only if absent.
    tracks_gitignore = home / ".gitignore"
    if not tracks_gitignore.exists():
        tracks_gitignore.write_text(TRACKS_GITIGNORE, encoding="utf-8")
    # Runtime-owned transient files under projects/ (the discuss writer's flock
    # lock files ``<doc>.md.lock`` and tmp-before-rename ``<doc>.md.tmp``) are
    # intentionally left in place - deleting would break flock serialization -
    # so they must be gitignored in every stage or they pollute host git status.
    # Idempotent: write only if absent (mirrors the runtime gitignore above).
    projects_gitignore = paths.projects_dir(home) / ".gitignore"
    if not projects_gitignore.exists():
        projects_gitignore.write_text(PROJECTS_GITIGNORE, encoding="utf-8")
    # Runtime-owned byproducts in the HOST TREE (see HOST_BYPRODUCTS_GITIGNORE).
    host_gitignore_changed = _ensure_host_byproducts_gitignore(repo)
    for d in (paths.projects_dir(home), paths.wiki_dir(home)):
        keep = d / ".gitkeep"
        if not keep.exists():
            keep.write_text("", encoding="utf-8")
    # AC-01b/c: idempotent, no empty commit, leaves the worktree clean.
    # Path-scoped commit: the runtime never sweeps unrelated operator-staged
    # content into its scaffold commit (AC-FR0236-01 attribution).
    init_paths = [str(home)]
    if host_gitignore_changed:
        git(repo, "add", ".gitignore")
        init_paths.append(".gitignore")
    git(repo, "add", str(home))
    if git(
        repo, "diff", "--cached", "--quiet", "--", *init_paths, check=False
    ).returncode != 0:
        git(repo, "commit", "-m", "trac init: scaffold .tracks",
            "--only", "--", *init_paths)
    print(f"initialized {home}")
    return 0


def cmd_start(repo: Path, *args: str) -> int:
    version, confirm = _parse_start_args(args)
    raw = sys.stdin.read().strip()
    if not raw:
        return _err("empty stdin: pipe the raw requirement into `trac start <version>`")
    # AC-FR0267-02 locked exit: a dirty tree is NOT refused here — the run
    # starts and the M-VERIFY freeze lands attention.required(dirty_tree)
    # (needs_attention with recovery guidance) at the contract's gate.
    home = paths.tracks_home(repo)
    store = Store(home)
    with writer_lock(home):
        # SM-01.2: an active run exists → record the requirement into the
        # backlog and exit WITHOUT creating a branch (normative: flow.md §3.1).
        active = store.active_run()
        if active is not None:
            bid = new_ulid()
            store.append(
                bid,
                version,
                "backlog.recorded",
                {"version": version, "decision": "queued", "reason": "active_run"},
            )
            print(f"active run {active}; requirement queued to backlog (run not started)")
            return 0
        # SM-01.6: local unmerged release branches → AWAIT_CONFIRM gate.
        unmerged = _unmerged_branches(repo)
        if unmerged:
            if confirm is None:
                _err(
                    "unmerged release branches detected; run `trac start "
                    f"{version} --confirm` to proceed anyway, or --cancel to abort"
                )
                return 2
            if confirm == "cancel":
                print("start cancelled (unmerged release branches)")
                return 0
        run_id = new_ulid()
        branch = f"releases/{version}"
        store.append(run_id, version, "story.requested", {"raw_chars": len(raw)})
        _sweep_stale_worktrees(store, repo, run_id, version)
        store.append(run_id, version, "stage.entered", {"stage": "M-START"})
        # FR-04: create the release branch as a logged, reconcilable command.
        Executor(store, repo, run_id).issue(
            Command(kind="create_branch", params={"branch_name": branch, "base": "main"})
        )
        vdir = paths.version_dir(home, version)
        vdir.mkdir(parents=True, exist_ok=True)
        story = vdir / "story.md"
        story.write_text(
            templating.render_story_skeleton(raw, date.today().isoformat()),
            encoding="utf-8",
        )
        git(repo, "add", str(story))
        git(repo, "commit", "-m", f"M-START: capture raw requirement for {version}",
            "--only", "--", str(story))
        store.append(run_id, version, "stage.exited", {"stage": "M-START"})
        store.append(run_id, version, "stage.entered", {"stage": "M-STORY"})
    print(f"run {run_id} started on {branch}")
    return 0


def _sweep_stale_worktrees(store: Store, repo: Path, run_id: str, version: str) -> None:
    # B2 (run 01KZTHE7, user ruling): reclaim leaked worktrees exactly once,
    # at new-run initialization — never at trac run or mid-run. Audited as
    # an event; empty sweep emits nothing (no noise runs). Deferred import
    # avoids a circular dependency at module load.
    from tracks.executor.worktree import sweep_worktrees

    swept = sweep_worktrees(str(repo))
    if swept:
        store.append(
            run_id,
            version,
            "worktree.swept",
            {"removed": swept, "count": len(swept)},
        )
        print(f"start: swept {len(swept)} stale worktree(s)", flush=True)


def _parse_start_args(args: tuple[str, ...]) -> tuple[str, str | None]:
    """`start <version> [--confirm|--cancel]`. Returns (version, confirm)."""
    confirm = None
    parts = list(args)
    if "--confirm" in parts:
        confirm = "confirm"
        parts.remove("--confirm")
    elif "--cancel" in parts:
        confirm = "cancel"
        parts.remove("--cancel")
    if len(parts) != 1:
        raise SystemExit("usage: trac start <version> [--confirm|--cancel]")
    return parts[0], confirm


def _unmerged_branches(repo: Path) -> list[str]:
    """Local branches not fully merged into `main` (SM-01.6 CHECK_BRANCHES)."""
    merged = git(repo, "branch", "--merged", "main", check=False).stdout.splitlines()
    merged = {ln.strip().lstrip("*").strip() for ln in merged}
    allb = git(repo, "branch", "--list", "releases/*", check=False).stdout.splitlines()
    return [
        ln.strip().lstrip("*").strip()
        for ln in allb
        if ln.strip().lstrip("*").strip() not in merged
    ]


def _scope_staged_attribution(repo: Path, version: str, label: str) -> int:
    """v0.5 review-A attribution, AC-FR0267-02 realignment: pre-staged
    content OUTSIDE the artifact allowlist is unstaged (``git reset`` --
    content preserved in the working tree, never swept into the human
    checkpoint commit) instead of refusing the gate; a dirty tree is
    never a CLI-stage refusal — the walk proceeds and the M-VERIFY
    freeze lands attention.required(dirty_tree) (needs_attention with
    recovery guidance) at the contract's locked exit. Always returns 0."""
    del label  # retained in the signature for caller stability
    allowed_prefix = f".tracks/projects/{version}/"
    staged = [
        line.strip()
        for line in git(
            repo, "diff", "--cached", "--name-only", check=False
        ).stdout.splitlines()
        if line.strip()
    ]
    foreign = [f for f in staged if not f.startswith(allowed_prefix)]
    if foreign:
        # Unstage only; the working tree keeps the operator's content and
        # the scoped checkpoint commit never picks it up.
        git(repo, "reset", "--", *foreign, check=False)
    return 0


def _parse_action_actor(args: tuple[str, ...], usage: str) -> tuple[str, str | None] | None:
    if not args or len(args) > 3 or (len(args) == 3 and args[1] != "--actor"):
        _err(usage)
        return None
    return args[0].replace("-", "_"), args[2] if len(args) == 3 else None


_RUN_USAGE = (
    "usage: trac run [--resume] [--assignment-overlay PATH] [--max-dispatches N]"
)


def _parse_run_args(args: tuple[str, ...]) -> tuple[str | None, int | None]:
    """Parse `trac run` flags. ``--resume`` is the explicit resume alias
    (AC-FR0283-02 wording / interfaces §2b): a no-op token because an active
    run already reconciles/replays on the next drive (D-13)."""
    values = {}
    index = 0
    while index < len(args):
        flag = args[index]
        if flag == "--resume":
            index += 1
            continue
        if flag not in ("--assignment-overlay", "--max-dispatches"):
            raise ValueError(_RUN_USAGE)
        if flag in values:
            raise ValueError(f"{flag} may be specified only once")
        if index + 1 >= len(args):
            raise ValueError(_RUN_USAGE)
        values[flag] = args[index + 1]
        index += 2
    return values.get("--assignment-overlay"), _positive_dispatch_limit(
        values.get("--max-dispatches")
    )


def _positive_dispatch_limit(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("--max-dispatches must be a positive integer") from exc
    if value < 1:
        raise ValueError("--max-dispatches must be a positive integer")
    return value


def _read_assignment_overlay(path: str) -> dict:
    overlay_path = Path(path)
    try:
        raw = overlay_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ValueError(f"assignment overlay not found: {path}") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"cannot read assignment overlay {path}: {exc}") from exc
    try:
        overlay = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid assignment overlay JSON {path}: {exc.msg}") from exc
    if not isinstance(overlay, dict):
        raise ValueError("assignment overlay JSON must be an object")
    return overlay


def _ls_tracks_py(repo: Path) -> list[str]:
    """git 已跟踪 + 未跟踪 tracks/**/*.py（repo 相对路径）。

    git 不可用/非仓库返回空列表，由调用方用目录遍历兜底。"""
    try:
        proc = git(
            repo,
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "tracks/",
            check=False,
        )
    except OSError:
        return []
    rels: list[str] = []
    for rel in proc.stdout.splitlines():
        rel = rel.strip()
        if rel.endswith(".py") and "__pycache__" not in rel:
            rels.append(rel)
    return rels


def _tracks_python_files(repo: Path) -> list[Path]:
    """全部 tracks/**/*.py：跟踪 + 未跟踪（含在飞未提交——正是风险源）。"""
    pkg = Path(repo) / "tracks"
    if not pkg.is_dir():
        return []
    candidates: dict[str, Path] = {}
    for rel in _ls_tracks_py(repo):
        candidates[rel] = repo / rel
    if not candidates:
        # non-git fallback; followlinks=False keeps a stray symlink from
        # pulling files outside the repo into the smoke set (os.walk is the
        # pre-3.13-portable equivalent of rglob(..., recurse_symlinks=False))
        for root, dirs, files in os.walk(pkg, followlinks=False):
            if "__pycache__" in Path(root).parts:
                dirs[:] = []
                continue
            for name in files:
                if name.endswith(".py"):
                    p = Path(root) / name
                    candidates[str(p.relative_to(repo))] = p
    return sorted(p for p in candidates.values() if p.is_file())


def _startup_smoke_error(repo: Path) -> tuple[str, list[str]] | None:
    """#87 启动烟测：ast.parse 全部 tracks/**/*.py（含在飞未提交文件）。

    只做语法级解析——不 import、不执行、不写字节码，不受 import 副作用影响。
    覆盖 #87 现场：T-003 半成品增量（IndentationError）让 trac 本体
    ImportError 无法启动。返回 ``(summary, broken_files)`` 或 None（全绿）。"""
    broken: list[str] = []
    for py in _tracks_python_files(repo):
        try:
            ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except SyntaxError as exc:
            broken.append(
                f"{py.relative_to(repo)}: {exc.msg} (line {exc.lineno or 0})"
            )
        except (OSError, UnicodeDecodeError) as exc:
            # review (f): unreadable/binary/damaged files must fail CLOSED —
            # if syntax cannot be verified the loop must not start
            broken.append(f"{py.relative_to(repo)}: cannot read ({type(exc).__name__}): {exc}")
    if not broken:
        return None
    summary = (
        "trac run 拒绝启动：tracks/** 存在语法损坏文件（#87 启动烟测）\n"
        + "\n".join(f"  - {b}" for b in broken)
        + "\n修复指引：git checkout HEAD -- <files> 后重启，GREEN_GATE "
        "会以 impl_defect 重做。"
    )
    return summary, broken


def _terminal_run_outcome(store: Store) -> int | None:
    """cmd_run for the latest completed run with no active run: report a
    well-known terminal, or explicitly reject a cancelled one (SM-01.22)."""
    latest = store.latest_run()
    if latest is None:
        return None
    state = store.state(latest)
    if state.status != "completed":
        return None
    if state.terminal_state == "cancelled":
        # SM-01.22: a cancelled terminal run rejects `trac run`; redo
        # requires a fresh `trac start` run (FR-0287-5).
        return _err("run is cancelled, use trac start for new run")
    print(f"run {latest}: {_format_state(state)}")
    return 0


def cmd_run(repo: Path, *args: str) -> int:
    try:
        overlay_path, max_dispatches = _parse_run_args(args)
        assignment_overlay = (
            _read_assignment_overlay(overlay_path) if overlay_path is not None else None
        )
    except ValueError as exc:
        return _err(str(exc))
    home = paths.tracks_home(repo)
    store = Store(home)
    run_id = store.active_hotfix_run() or store.active_run()
    if run_id is None:
        terminal = _terminal_run_outcome(store)
        if terminal is not None:
            return terminal
        return _err("no active run; `trac start <version>` first")
    with writer_lock(home):
        smoke = _startup_smoke_error(repo)
        if smoke is not None:
            # #87：坏树立即停车，进 loop 前落 loop.aborted 审计事件
            # （screen 无重定向时 stdout 证据会丢，同 B85/#85 的 append 模式，
            # writer_lock 内执行）。version 沿用 executor 的解析方式。
            summary, broken_files = smoke
            version = _resolve_run_version(store.state(run_id))
            store.append(
                run_id,
                version,
                "loop.aborted",
                {"reason": "startup_smoke", "detail": summary, "files": broken_files},
            )
            print(summary, file=sys.stderr, flush=True)
            return 1
        try:
            state = Executor(
                store,
                repo,
                run_id,
                assignment_overlay=assignment_overlay,
                max_dispatches=max_dispatches,
            ).run_loop()
        except RuntimeCodeDriftError:
            # M7 (convergence plan 2026-09-05): drift at a dispatch
            # boundary is a HANDOVER, not an abort -- the in-flight
            # dispatch concluded, code.drift is on the audit stream, and
            # the run state stays active. Exit 0 so the restart watcher
            # treats this as a normal dead-loop takeover (executor has
            # already printed the handover banner).
            print(
                "run handover: tracks/** code drift (see message above)",
                file=sys.stderr,
            )
            return 0
        except CommandStallError:
            # B86/B88（#77）：executor 已落 loop.aborted 并打印处置指引
            # banner，这里只以非零退出码终止（同 B43 drift abort 处理链）。
            print("run aborted: command stall (see message above)", file=sys.stderr)
            return 1
    print(f"run {run_id}: {_format_state(state)}")
    return 0


def _stage_doc(stage: str) -> str | None:
    """Return the doc name for the current stage (deferred import to avoid
    circular dependency)."""
    from tracks.kernel.machine import _STAGES

    sd = _STAGES.get(stage)
    return sd.doc if sd else None


def _triage_artifacts(doc: str | None) -> tuple[list[str], list[str]]:
    """Returns (artifacts, checks). Triage always validates the stage doc
    with template check when a doc exists — no-diff is a legitimate no-change
    checkpoint. String basename matching against git paths is unreliable."""
    if doc:
        return [doc], ["template"]
    return [], []


def _review_action_params(action: str, stage: str) -> dict:
    """Returns pipeline params for a review action."""
    if action == "revise":
        return {
            "requires_diff": True,
            "forbid_diff": False,
            "discussion_only": False,
            "checks": ["template"],
            "commit_label": f"{stage}: human revise",
            "verdict": "comment",
        }
    return {
        "requires_diff": False,
        "forbid_diff": True,
        "discussion_only": False,
        "checks": [],
        "commit_label": f"{stage}: human no-comment",
        "verdict": "no_comment",
    }


@dataclass(frozen=True)
class _HumanCheckpoint:
    """One Human ResultCheckpoint submit (triage / review)."""

    label: str
    event_type: str
    payload: dict
    verdict: str
    artifacts: list[str]
    checks: list[str]
    requires_diff: bool
    commit_label: str
    forbid_diff: bool = False
    discussion_only: bool = False


def _do_human_pipeline(
    repo, state, store, run_id, checkpoint: _HumanCheckpoint
):
    """Run one Human ResultCheckpoint pipeline under the writer lock (triage /
    review). Returns (rc, (final_state, awaiting_before)) or (rc, None) on an
    early rejection. Pre-staged foreign content is unstaged for attribution
    (_scope_staged_attribution); a dirty tree is never refused here (the
    M-VERIFY freeze owns the AC-FR0267-02 exit)."""
    rc = _scope_staged_attribution(repo, state.version, checkpoint.label)
    if rc:
        return rc, None
    awaiting_before = state.awaiting
    ex = Executor(store, repo, run_id)
    doc = _stage_doc(state.stage)
    ex.submit_human_result(
        state=state,
        domain_event_type=checkpoint.event_type,
        payload=checkpoint.payload,
        verdict=checkpoint.verdict,
        artifacts=checkpoint.artifacts,
        allowed_paths=[doc] if doc else [],
        checks=checkpoint.checks,
        requires_diff=checkpoint.requires_diff,
        commit_label=checkpoint.commit_label,
        forbid_diff=checkpoint.forbid_diff,
        discussion_only=checkpoint.discussion_only,
    )
    return 0, (ex.run_pipeline(), awaiting_before)


def _triage_checkpoint(repo: Path, state, actor: str | None, decision: str) -> _HumanCheckpoint:
    """Assemble the triage checkpoint (actor name resolved at submit time)."""
    doc = _stage_doc(state.stage)
    artifacts, checks = _triage_artifacts(doc)
    return _HumanCheckpoint(
        label="triage",
        event_type="human.triage",
        payload={"actor": actor or _human_actor(repo)},
        verdict=decision,
        artifacts=artifacts,
        checks=checks,
        requires_diff=False,
        commit_label=f"{state.stage}: human triage ({decision})",
    )


def cmd_triage(repo: Path, *args: str) -> int:
    usage = "usage: trac triage go|no-go|park [--actor NAME]"
    parsed = _parse_action_actor(args, usage)
    if parsed is None:
        return 1
    decision, actor = parsed
    if decision not in ("go", "no_go", "park"):
        return _err(usage)
    with active_run_context(repo) as active:
        if active is None:
            return _err("no active run")
        home, store, run_id = active
        state = store.state(run_id)
        if state.awaiting != "triage":
            return _err(f"run not awaiting triage (awaiting={state.awaiting or 'nothing'})")
        checkpoint = _triage_checkpoint(repo, state, actor, decision)
        rc, result = _do_human_pipeline(repo, state, store, run_id, checkpoint)
        if rc:
            return rc
    return _pipeline_outcome(result, "triage", f"triage recorded: {decision}")


def _pipeline_outcome(result, label: str, success_line: str) -> int:
    """Share the triage/review ResultCheckpoint pipeline tail (cmd_triage /
    cmd_review): when the awaiting gate did NOT move the pipeline failed and
    its reason goes to stderr (exit 1); otherwise print the success line."""
    final_state, awaiting_before = result
    if final_state.awaiting == awaiting_before:
        reason = (final_state.last_failure or {}).get("reason", "unknown")
        return _err(f"{label} pipeline failed: {reason}")
    print(success_line)
    return 0


def _review_checkpoint(repo: Path, state, actor: str | None, action: str) -> _HumanCheckpoint:
    """Assemble the review checkpoint (action params resolved up front)."""
    actor_name = actor or _human_actor(repo)
    doc = _stage_doc(state.stage)
    params = _review_action_params(action, state.stage)
    return _HumanCheckpoint(
        label="review",
        event_type="human.review",
        payload={"actor": actor_name},
        verdict=params["verdict"],
        artifacts=[doc] if doc else [],
        checks=params["checks"],
        requires_diff=params["requires_diff"],
        commit_label=params["commit_label"],
        forbid_diff=params["forbid_diff"],
        discussion_only=params["discussion_only"],
    )


def cmd_review(repo: Path, *args: str) -> int:
    usage = "usage: trac review no-comment|revise [--actor NAME]"
    parsed = _parse_action_actor(args, usage)
    if parsed is None:
        return 1
    action, actor = parsed
    if action not in ("no_comment", "revise"):
        return _err(usage)
    with active_run_context(repo) as active:
        if active is None:
            return _err("no active run")
        home, store, run_id = active
        state = store.state(run_id)
        if state.awaiting != "review":
            return _err(f"run not awaiting review (awaiting={state.awaiting or 'nothing'})")
        checkpoint = _review_checkpoint(repo, state, actor, action)
        rc, result = _do_human_pipeline(repo, state, store, run_id, checkpoint)
        if rc:
            return rc
    return _pipeline_outcome(result, "review", f"review recorded: {action}")
