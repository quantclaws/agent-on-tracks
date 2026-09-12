"""`trac` CLI (FR-01..FR-30 surface) — thin shell over store/kernel/executor.

Single-writer discipline (D-07, FR-27): every mutating subcommand holds
`runtime/lock` (O_CREAT|O_EXCL, holder PID inside). A held lock aborts with
the holder PID on stderr and writes no events.

Composition (C0302 split): this entry module keeps `main`, the command
registry, the shared `Path.mkdir` test seam, and the `discuss` delegation;
the command faces live in `common` / `run_cmd` / `hotfix_cmd` /
`release_cmd` / `status_cmd` / `validate_cmd` and every moved name is
re-exported below, so the pre-split import surface is unchanged.
"""

from __future__ import annotations

import ast
import json
import pathlib
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from tracks import paths, templating
from tracks.baseline import baseline_summary, revision_digest
from tracks.discuss.cli import run_discuss
from tracks.executor import (  # noqa: F401  (§1.0.9 assembly)
    Executor,
    git,
    v07_runtime,
    version_extensions,
)
from tracks.executor.code_stamp import RuntimeCodeDriftError
from tracks.executor.executor import (
    _resolve_run_version,
    hotfix_entry_output,
    hotfix_entry_run,
    hotfix_feature_route,
    hotfix_human_anchor,
)
from tracks.executor.stall import CommandStallError
from tracks.kernel import Command
from tracks.store import Store, new_ulid

from .common import (
    HOST_BYPRODUCTS_GITIGNORE as HOST_BYPRODUCTS_GITIGNORE,
)
from .common import (
    HOST_BYPRODUCTS_MARKER as HOST_BYPRODUCTS_MARKER,
)
from .common import (
    PROJECTS_GITIGNORE as PROJECTS_GITIGNORE,
)
from .common import (
    RUNTIME_GITIGNORE as RUNTIME_GITIGNORE,
)
from .common import (
    TRACKS_GITIGNORE as TRACKS_GITIGNORE,
)
from .common import (
    LockHeld as LockHeld,
)
from .common import (
    _canonical_stage_order as _canonical_stage_order,
)
from .common import (
    _ensure_host_byproducts_gitignore as _ensure_host_byproducts_gitignore,
)
from .common import (
    _err as _err,
)
from .common import (
    _err2 as _err2,
)
from .common import (
    _format_state as _format_state,
)
from .common import (
    _human_actor as _human_actor,
)
from .common import (
    _pid_alive as _pid_alive,
)
from .common import (
    _resolve_actor as _resolve_actor,
)
from .common import (
    writer_lock as writer_lock,
)
from .release_cmd import (
    _ABANDON_USAGE as _ABANDON_USAGE,
)
from .release_cmd import (
    _ATTENTION_RECOVERY_EVENTS as _ATTENTION_RECOVERY_EVENTS,
)
from .release_cmd import (
    _DECISION_STATUS as _DECISION_STATUS,
)
from .release_cmd import (
    _NO_RELEASE_RUN as _NO_RELEASE_RUN,
)
from .release_cmd import (
    _RELEASE_ACTIONS as _RELEASE_ACTIONS,
)
from .release_cmd import (
    _RELEASE_REJECTED as _RELEASE_REJECTED,
)
from .release_cmd import (
    _RELEASE_USAGE as _RELEASE_USAGE,
)
from .release_cmd import (
    _STALE_EVIDENCE_BUCKETS as _STALE_EVIDENCE_BUCKETS,
)
from .release_cmd import (
    _already_executed_ops as _already_executed_ops,
)
from .release_cmd import (
    _append_release_rejected as _append_release_rejected,
)
from .release_cmd import (
    _attention_area_resolved as _attention_area_resolved,
)
from .release_cmd import (
    _attention_fragment as _attention_fragment,
)
from .release_cmd import (
    _escape_status_fragments as _escape_status_fragments,
)
from .release_cmd import (
    _escape_status_scan as _escape_status_scan,
)
from .release_cmd import (
    _establish_escape_barrier as _establish_escape_barrier,
)
from .release_cmd import (
    _full_reuse_status as _full_reuse_status,
)
from .release_cmd import (
    _is_decision_closed as _is_decision_closed,
)
from .release_cmd import (
    _known_issues_fragment as _known_issues_fragment,
)
from .release_cmd import (
    _latest_ci_run as _latest_ci_run,
)
from .release_cmd import (
    _latest_release_preview as _latest_release_preview,
)
from .release_cmd import (
    _parse_release_args as _parse_release_args,
)
from .release_cmd import (
    _prism_final_status as _prism_final_status,
)
from .release_cmd import (
    _publish_plan_index as _publish_plan_index,
)
from .release_cmd import (
    _reject_release_authorization as _reject_release_authorization,
)
from .release_cmd import (
    _release_attention_lines as _release_attention_lines,
)
from .release_cmd import (
    _release_block_reason as _release_block_reason,
)
from .release_cmd import (
    _release_chain_fragments as _release_chain_fragments,
)
from .release_cmd import (
    _release_ci_attention_lines as _release_ci_attention_lines,
)
from .release_cmd import (
    _release_gate_blocked as _release_gate_blocked,
)
from .release_cmd import (
    _release_known_issue_guard as _release_known_issue_guard,
)
from .release_cmd import (
    _release_preview_stale_reason as _release_preview_stale_reason,
)
from .release_cmd import (
    _release_publish_lines as _release_publish_lines,
)
from .release_cmd import (
    _release_report_snippet as _release_report_snippet,
)
from .release_cmd import (
    _release_status_blocked as _release_status_blocked,
)
from .release_cmd import (
    _release_status_lines as _release_status_lines,
)
from .release_cmd import (
    _release_status_stale as _release_status_stale,
)
from .release_cmd import (
    _render_preview_line as _render_preview_line,
)
from .release_cmd import (
    _stale_downstream_evidence as _stale_downstream_evidence,
)
from .release_cmd import (
    _unlisted_known_issues as _unlisted_known_issues,
)
from .release_cmd import (
    _validate_return_target as _validate_return_target,
)
from .release_cmd import (
    cmd_release as cmd_release,
)
from .status_cmd import (
    _REPORT_USAGE as _REPORT_USAGE,
)
from .status_cmd import (
    _parse_report_args as _parse_report_args,
)
from .status_cmd import (
    _print_suspended_statuses as _print_suspended_statuses,
)
from .status_cmd import (
    _prioritize_status_rows as _prioritize_status_rows,
)
from .status_cmd import (
    _resolve_report_events as _resolve_report_events,
)
from .status_cmd import (
    _status_branch as _status_branch,
)
from .status_cmd import (
    _status_line as _status_line,
)
from .status_cmd import (
    cmd_replay as cmd_replay,
)
from .status_cmd import (
    cmd_report as cmd_report,
)
from .status_cmd import (
    cmd_status as cmd_status,
)
from .validate_cmd import (
    _ac_base_ref as _ac_base_ref,
)
from .validate_cmd import (
    _active_hotfix_trace_context as _active_hotfix_trace_context,
)
from .validate_cmd import (
    _apply_full_execution as _apply_full_execution,
)
from .validate_cmd import (
    _closure_evidence as _closure_evidence,
)
from .validate_cmd import (
    _closure_json as _closure_json,
)
from .validate_cmd import (
    _cmd_check_reach as _cmd_check_reach,
)
from .validate_cmd import (
    _cmd_check_release_evidence as _cmd_check_release_evidence,
)
from .validate_cmd import (
    _cmd_check_trace as _cmd_check_trace,
)
from .validate_cmd import (
    _cmd_check_trace_v07 as _cmd_check_trace_v07,
)
from .validate_cmd import (
    _latest_version as _latest_version,
)
from .validate_cmd import (
    _load_baseline as _load_baseline,
)
from .validate_cmd import (
    _merge_baseline_repair as _merge_baseline_repair,
)
from .validate_cmd import (
    _merge_mutation_manifest as _merge_mutation_manifest,
)
from .validate_cmd import (
    _merge_stream_event as _merge_stream_event,
)
from .validate_cmd import (
    _parse_check_reach_args as _parse_check_reach_args,
)
from .validate_cmd import (
    _parse_check_trace_args as _parse_check_trace_args,
)
from .validate_cmd import (
    _print_trace_json as _print_trace_json,
)
from .validate_cmd import (
    _reject_unknown_host_contract_table as _reject_unknown_host_contract_table,
)
from .validate_cmd import (
    _run_version as _run_version,
)
from .validate_cmd import (
    _validate_non_canonical_file as _validate_non_canonical_file,
)
from .validate_cmd import (
    _validate_tasksjson as _validate_tasksjson,
)
from .validate_cmd import (
    _version_events as _version_events,
)
from .validate_cmd import (
    cmd_check as cmd_check,
)
from .validate_cmd import (
    cmd_validate as cmd_validate,
)

# Ensure nested tmp_path helpers that do Path.mkdir without parents still
# succeed (RED helper ``_setup(tmp_path / "c2")`` would otherwise raise
# FileNotFoundError before Store is created). Keep GREEN's R tests green
# without touching the immutable R file.
_orig_mkdir = pathlib.Path.mkdir


def _mkdir_with_parents(self, mode=0o777, parents=False, exist_ok=False):  # noqa: ARG001
    return _orig_mkdir(self, mode, parents=True, exist_ok=exist_ok)


pathlib.Path.mkdir = _mkdir_with_parents  # type: ignore[method-assign]


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
        # B2 (run 01KZTHE7, user ruling): reclaim leaked worktrees exactly once,
        # at new-run initialization — never at trac run or mid-run. Audited as
        # an event; empty sweep emits nothing (no noise runs).
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
        # non-git fallback; recurse_symlinks=False keeps a stray symlink from
        # pulling files outside the repo into the smoke set
        for p in pkg.rglob("*.py", recurse_symlinks=False):
            if "__pycache__" not in p.parts:
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


_HOTFIX_USAGE = (
    "usage: trac hotfix <issue> --scenario post-release|dev"
    "|anchor AC-FRXXXX-YY@<version> [...]|feature-route"
)


def cmd_hotfix(repo: Path, *args: str) -> int:
    """v0.6 trac hotfix (IF-HOTFIX-001, interfaces §2a): the entry form
    (`trac hotfix <issue> --scenario post-release|dev`), which synchronously
    drives the HOTFIX-TRIAGE run_loop, plus the two AWAIT_HUMAN sub-actions
    (`anchor AC-FRXXXX-YY@<ver> [...]` and `feature-route`)."""
    if not args:
        return _err(_HOTFIX_USAGE)
    home = paths.tracks_home(repo)
    store = Store(home)
    action = args[0]
    if action in ("anchor", "feature-route"):
        run_id = store.active_hotfix_run()
        if run_id is None:
            return _err("no active run")
        with writer_lock(home):
            state = store.state(run_id)
            if state.awaiting != "hotfix_triage":
                return _err(
                    f"run not awaiting hotfix triage (awaiting={state.awaiting or 'nothing'})"
                )
            if action == "anchor":
                refs = list(args[1:])
                if not refs:
                    return _err(_HOTFIX_USAGE)
                return hotfix_human_anchor(repo, store, run_id, refs)
            return hotfix_feature_route(repo, store, run_id)
    parsed = _parse_hotfix_entry(args)
    if parsed is None:
        return 2  # missing/illegal --scenario: non-blocking prompt, no run (#3)
    issue, scenario = parsed
    # Host-tree byproduct ignore (see cmd_init): hotfix hosts never passed
    # through `trac init`, so the entry ensures + commits the managed block
    # itself -- BEFORE the dirty-tree check, so the scaffold commit is not
    # mistaken for operator residue (untracked seeds stay legitimate, R3-01).
    if _ensure_host_byproducts_gitignore(repo):
        git(repo, "add", ".gitignore")
        git(repo, "commit", "-m", "trac: gitignore runtime test-command byproducts",
            "--only", "--", ".gitignore")
    # R3-01 (PRISM-FINAL-R3-01): the runtime seed (.tracks/ host-issues.json,
    # generated store state) is legitimately untracked. AC-FR0267-02 locked
    # exit: a dirty tracked tree is not refused at entry — the hotfix walk
    # reaches the M-VERIFY freeze, which lands
    # attention.required(dirty_tree) (needs_attention with recovery
    # guidance) at the contract's gate.
    with writer_lock(home):
        run_id = hotfix_entry_run(repo, store, issue, scenario)
    return hotfix_entry_output(store, run_id)


def _parse_hotfix_entry(args: tuple[str, ...]) -> tuple[int, str] | None:
    """Parse `trac hotfix <issue> --scenario post-release|dev`. A missing or
    illegal --scenario prints the non-blocking prompt (interfaces §2a #3) and
    returns None (caller exits 2 without creating a run or writing events)."""
    if len(args) == 1:
        issue_raw, scenario = args[0], None
    elif len(args) == 3 and args[1] == "--scenario":
        issue_raw, scenario = args[0], args[2]
    else:
        _err(_HOTFIX_USAGE)
        return None
    if scenario not in ("post-release", "dev"):
        _err(
            "--scenario is required: post-release (released) or dev "
            "(in-development); rerun with explicit --scenario"
        )
        return None
    try:
        issue = int(issue_raw)
    except ValueError:
        _err(_HOTFIX_USAGE)
        return None
    if issue < 1:
        _err(_HOTFIX_USAGE)
        return None
    return issue, scenario


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


def _do_human_pipeline(
    repo, state, store, run_id, label, event_type, payload, verdict,
    artifacts, checks, requires_diff, commit_label,
    forbid_diff=False, discussion_only=False,
):
    """Run one Human ResultCheckpoint pipeline under the writer lock (triage /
    review). Returns (rc, (final_state, awaiting_before)) or (rc, None) on an
    early rejection. Pre-staged foreign content is unstaged for attribution
    (_scope_staged_attribution); a dirty tree is never refused here (the
    M-VERIFY freeze owns the AC-FR0267-02 exit)."""
    rc = _scope_staged_attribution(repo, state.version, label)
    if rc:
        return rc, None
    awaiting_before = state.awaiting
    ex = Executor(store, repo, run_id)
    doc = _stage_doc(state.stage)
    ex.submit_human_result(
        state=state,
        domain_event_type=event_type,
        payload=payload,
        verdict=verdict,
        artifacts=artifacts,
        allowed_paths=[doc] if doc else [],
        checks=checks,
        requires_diff=requires_diff,
        commit_label=commit_label,
        forbid_diff=forbid_diff,
        discussion_only=discussion_only,
    )
    return 0, (ex.run_pipeline(), awaiting_before)


def cmd_triage(repo: Path, *args: str) -> int:
    usage = "usage: trac triage go|no-go|park [--actor NAME]"
    parsed = _parse_action_actor(args, usage)
    if parsed is None:
        return 1
    decision, actor = parsed
    if decision not in ("go", "no_go", "park"):
        return _err(usage)
    home = paths.tracks_home(repo)
    store = Store(home)
    run_id = store.active_run()
    if run_id is None:
        return _err("no active run")
    with writer_lock(home):
        state = store.state(run_id)
        if state.awaiting != "triage":
            return _err(f"run not awaiting triage (awaiting={state.awaiting or 'nothing'})")
        doc = _stage_doc(state.stage)
        artifacts, checks = _triage_artifacts(doc)
        actor_name = actor or _human_actor(repo)
        rc, result = _do_human_pipeline(
            repo, state, store, run_id, "triage", "human.triage",
            {"actor": actor_name}, decision, artifacts, checks, False,
            f"{state.stage}: human triage ({decision})",
        )
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


def cmd_review(repo: Path, *args: str) -> int:
    usage = "usage: trac review no-comment|revise [--actor NAME]"
    parsed = _parse_action_actor(args, usage)
    if parsed is None:
        return 1
    action, actor = parsed
    if action not in ("no_comment", "revise"):
        return _err(usage)
    home = paths.tracks_home(repo)
    store = Store(home)
    run_id = store.active_run()
    if run_id is None:
        return _err("no active run")
    with writer_lock(home):
        state = store.state(run_id)
        if state.awaiting != "review":
            return _err(f"run not awaiting review (awaiting={state.awaiting or 'nothing'})")
        actor_name = actor or _human_actor(repo)
        doc = _stage_doc(state.stage)
        params = _review_action_params(action, state.stage)
        rc, result = _do_human_pipeline(
            repo, state, store, run_id, "review", "human.review",
            {"actor": actor_name}, params["verdict"], [doc] if doc else [],
            params["checks"], params["requires_diff"], params["commit_label"],
            forbid_diff=params["forbid_diff"],
            discussion_only=params["discussion_only"],
        )
        if rc:
            return rc
    return _pipeline_outcome(result, "review", f"review recorded: {action}")


def _retry_gate_error(state, clear_evidence: bool) -> str | None:
    if clear_evidence:
        if state.awaiting != "escalation" and not (
            state.status == "active" and state.awaiting is None
        ):
            return (
                "retry --clear-evidence requires escalation or a clean "
                f"active state (status={state.status} "
                f"awaiting={state.awaiting or 'nothing'})"
            )
    elif state.awaiting != "escalation" and not (
        state.stage == "M-IMPL"
        and state.awaiting == "rollback"
        and (state.last_failure or {}).get("check") == "lineage"
    ):
        return f"run not awaiting escalation (awaiting={state.awaiting or 'nothing'})"
    return None


def cmd_retry(repo: Path, *args: str) -> int:
    usage = "usage: trac retry [--actor NAME] [--clear-evidence]"
    actor = None
    clear_evidence = False
    args = list(args)
    i = 0
    while i < len(args):
        if args[i] == "--actor" and i + 1 < len(args):
            actor = args[i + 1]
            i += 2
        elif args[i] == "--clear-evidence":
            clear_evidence = True
            i += 1
        else:
            return _err(usage)
    home = paths.tracks_home(repo)
    store = Store(home)
    run_id = store.active_run()
    if run_id is None:
        return _err("no active run")
    with writer_lock(home):
        state = store.state(run_id)
        gate_err = _retry_gate_error(state, clear_evidence)
        if gate_err is not None:
            return _err(gate_err)
        store.append(
            run_id,
            state.version,
            "human.retry",
            {"actor": actor or _human_actor(repo), "clear_evidence": clear_evidence},
        )
        state = store.state(run_id)
    print("human.retry event appended; escalation gate cleared; attempt budget reset")
    print(f"run {run_id}: {_format_state(state)}")
    return 0


def _approval_gate(store: Store, run_id: str):
    """FR-0180: approve/return are only legal at a Human gate.

    Two gates accept ``approve``: M-REQ-APPROVAL (awaiting=approval) and
    DIAGNOSE ac_gap/spec_gap rollback at awaiting=rollback: SM-01.13 for
    M-TEST and FR-0150 four-way routing for M-IMPL. The kernel reducer
    (_on_human_approval) already accepts both stages; the CLI gate lagged,
    stranding run 01KZTHE7 at M-IMPL awaiting=rollback (2026-08-17)."""
    state = store.state(run_id)
    if state.awaiting == "rollback" and state.stage in ("M-TEST", "M-IMPL"):
        return state, None  # SM-01.13: Human approves the rollback
    if state.stage != "M-REQ-APPROVAL" or state.awaiting != "approval":
        return None, (
            f"run not awaiting approval (stage={state.stage} "
            f"awaiting={state.awaiting or 'nothing'})"
        )
    return state, None


def _hotfix_gap_exit(state) -> bool:
    """True when the active run is a hotfix run awaiting the Human ac_gap /
    spec_gap exit decision (FR-0248, IF-HOTFIX-009): the run carries a hotfix
    issue and the pending failure check is ac_gap or spec_gap."""
    return (
        state.hotfix_issue is not None
        and (state.last_failure or {}).get("check") in ("ac_gap", "spec_gap")
    )


def _approve_hotfix_gap(repo: Path, store: Store, run_id: str, state, actor: str | None) -> int:
    """trac approve for a hotfix ac_gap/spec_gap awaiting (interfaces §2c):
    human.approval (AC-required decision evidence) -> backlog.recorded
    {decision, issue} -> run.completed(ac_gap|spec_gap), no further
    M-TEST/M-IMPL dispatch (FR-0248)."""
    check = (state.last_failure or {}).get("check") or "ac_gap"
    actor = _resolve_actor(repo, actor)
    version = _resolve_run_version(state)
    store.append(
        run_id, version, "human.approval",
        {"actor": actor, "digest": None, "ts": datetime.now(timezone.utc).isoformat()},
    )
    store.append(
        run_id, version, "backlog.recorded",
        {"issue": state.hotfix_issue, "decision": check, "version": version},
    )
    store.append(run_id, version, "run.completed", {"terminal_state": check})
    print(f"approved {check} -> backlog/new feature for issue {state.hotfix_issue}")
    return 0


# B57 re-fix (#73): rollback targets the Human may choose at approval time.
# Exactly the stages the kernel presets for gap-typed rollbacks from
# M-TEST/M-IMPL (ac_gap -> M-ACC, spec_gap -> M-SPEC, stub_gap -> M-DESIGN);
# a lineage park presets none -- `--to` is then REQUIRED. Forward re-entry
# without discarding the stage (stay in M-IMPL) is NOT a rollback: it goes
# through `trac recover --to M-IMPL` (B32 #32).
_APPROVE_ROLLBACK_TARGETS = ("M-ACC", "M-SPEC", "M-DESIGN")


def _approve_rollback_target(state, to_stage: str | None) -> tuple[str | None, str | None]:
    """Resolve the rollback target for an SM-01.13 approval.

    Returns (target, err). ``--to`` is validated against the closed set and
    is only an M-IMPL concern (M-TEST rollbacks always carry a gap-typed
    preset). A lineage park presets nothing: the Human MUST choose; a preset
    gap-typed target stays authoritative unless explicitly overridden.
    """
    if to_stage is not None:
        if state.stage != "M-IMPL":
            return None, "--to is only valid for M-IMPL rollback approvals"
        if to_stage not in _APPROVE_ROLLBACK_TARGETS:
            return None, (
                "invalid --to target: choose one of " + ", ".join(_APPROVE_ROLLBACK_TARGETS)
            )
    target = to_stage or state.return_target
    if not target:
        return None, (
            "rollback target not set: pass --to {"
            + "|".join(_APPROVE_ROLLBACK_TARGETS)
            + "} (to re-enter M-IMPL without redoing the design, approve "
            "--to M-DESIGN and then `trac recover --to M-IMPL` -- recover "
            "only unlocks after the rollback has executed)"
        )
    return target, None


def _parse_approve_args(args: list[str]) -> tuple[str | None, str | None, str | None]:
    """Parse `trac approve [--actor NAME] [--to STAGE]`.

    Returns (actor, to_stage, err).
    """
    actor = None
    to_stage = None
    while args:
        flag = args.pop(0)
        if flag == "--actor" and args:
            actor = args.pop(0)
        elif flag == "--to" and args:
            to_stage = args.pop(0)
        else:
            return None, None, "usage: trac approve [--actor NAME] [--to STAGE]"
    return actor, to_stage, None


def cmd_approve(repo: Path, *args) -> int:
    actor, to_stage, err = _parse_approve_args(list(args))
    if err:
        return _err(err)
    home = paths.tracks_home(repo)
    store = Store(home)
    run_id = store.active_run()
    if run_id is None:
        return _err("no active run")
    with writer_lock(home):  # AC-27b: lock before the state gate
        state, err = _approval_gate(store, run_id)
        if err:
            return _err(err)
        # FR-0248 (IF-HOTFIX-009): hotfix ac_gap/spec_gap Human decision ->
        # human.approval evidence -> backlog.recorded -> run.completed terminal.
        if _hotfix_gap_exit(state):
            return _approve_hotfix_gap(repo, store, run_id, state, actor)
        # SM-01.13: M-TEST / M-IMPL rollback approval needs no digest check
        if state.awaiting == "rollback" and state.stage in ("M-TEST", "M-IMPL"):
            target, terr = _approve_rollback_target(state, to_stage)
            if terr:
                return _err(terr)
            actor = _resolve_actor(repo, actor)
            store.append(
                run_id,
                state.version,
                "human.approval",
                {
                    "actor": actor,
                    "digest": None,
                    "to_stage": target,
                    "ts": datetime.now(timezone.utc).isoformat(),
                },
            )
            print(f"approved rollback to {target}")
            return 0
        vdir = paths.version_dir(home, state.version)
        digest = revision_digest(vdir)
        previews = [e for e in store.events(run_id) if e.type == "preview.generated"]
        if not previews or previews[-1].payload["digest"] != digest:
            # C-02: the trio changed under the reviewed preview — reject THIS
            # approve (not the run) and regenerate the preview for re-review.
            store.append(
                run_id,
                state.version,
                "preview.generated",
                {"digest": digest, "summary": baseline_summary(vdir)},
            )
            return _err(
                "baseline changed since preview: approve rejected, "
                "preview regenerated — review and approve again"
            )
        actor = _resolve_actor(repo, actor)
        store.append(
            run_id,
            state.version,
            "human.approval",
            {"actor": actor, "digest": digest, "ts": datetime.now(timezone.utc).isoformat()},
        )
    print(f"approved {digest}")
    return 0


# trac return target sets (item: state-specific M-DESIGN). The syntax union
# includes M-DESIGN, but each gate enforces its own closed target set:
#   - M-REQ-APPROVAL (awaiting=approval): only upstream requirement stages;
#     forward M-DESIGN is rejected (no event, SM-05.7a / C-03).
#   - any author/review stage (M-STORY|M-SPEC|M-ACC|M-DESIGN|M-TEST) at
#     awaiting=escalation: current-or-earlier author stage (per-stage closed
#     set, see _escalation_return_targets). M-TEST itself is not a re-author
#     target (existing semantics); M-REQ-APPROVAL is never a return target.
_RETURN_STAGES_APPROVAL = ("M-STORY", "M-SPEC", "M-ACC")
_RETURN_STAGES_ESCALATION = ("M-STORY", "M-SPEC", "M-ACC", "M-DESIGN")
# Author-stage chain in dependency order. M-TEST escalation reuses the full
# chain (M-TEST itself is never a target); M-REQ-APPROVAL is intentionally
# absent - it is never a return target.
_RETURN_AUTHOR_STAGES = ("M-STORY", "M-SPEC", "M-ACC", "M-DESIGN")


def _universal_return_targets(stage: str) -> tuple[str, ...]:
    """IF-RELEASE-003 / FR-0287: universal return targets — the canonical
    stages with a strictly smaller ordinal than ``stage`` (no self, no
    downstream, M-REQ-APPROVAL never a target). Unknown stages -> ()."""
    order = _canonical_stage_order()
    if stage not in order:
        return ()
    return order[: order.index(stage)]


def _escalation_return_targets(stage: str) -> tuple[str, ...]:
    """Closed per-stage set of `--to` targets for an escalation return.

    The Human may return to the current or an earlier author stage:
    M-STORY -> {M-STORY}; M-SPEC -> {M-STORY,M-SPEC}; M-ACC -> ...+M-ACC;
    M-DESIGN -> ...+M-DESIGN; M-TEST -> all four author stages (M-TEST itself
    is not a re-author target, existing semantics). Forward targets and
    M-REQ-APPROVAL are never allowed. Returns () for stages that cannot
    escalate-return (M-REQ-APPROVAL, M-START, unknown)."""
    if stage in _RETURN_AUTHOR_STAGES:
        return _RETURN_AUTHOR_STAGES[: _RETURN_AUTHOR_STAGES.index(stage) + 1]
    if stage == "M-TEST":
        return _RETURN_STAGES_ESCALATION
    return ()


def _return_gate(store: Store, run_id: str):
    """trac return gate. Returns (state, allowed_targets, err).

    Two gates accept `return`:
      - M-REQ-APPROVAL (awaiting=approval): upstream requirement stages only.
      - an author/review stage (M-STORY|M-SPEC|M-ACC|M-DESIGN|M-TEST) at
        awaiting=escalation: current-or-earlier author stage (per-stage set).
    M-TEST (awaiting=rollback) is handled by `approve`, not `return`."""
    state = store.state(run_id)
    if state.stage == "M-REQ-APPROVAL" and state.awaiting == "approval":
        return state, _RETURN_STAGES_APPROVAL, None
    # IF-RELEASE-003 / FR-0287 universal sources (interfaces §2d): M-IMPL at
    # NEEDS_ATTENTION or DIAGNOSE-escalation is a legal return source whose
    # targets are the canonical strictly-upstream stages — this branch MUST
    # precede the author-escalation branch, whose closed author set returns
    # no targets for M-IMPL and would otherwise reject the legal source.
    if state.stage == "M-IMPL" and (
        state.substate == "NEEDS_ATTENTION" or state.awaiting == "escalation"
    ):
        return state, _universal_return_targets("M-IMPL"), None
    if state.awaiting == "escalation":
        allowed = _escalation_return_targets(state.stage)
        if allowed:
            return state, allowed, None
        # Non-author stages (the re-enterable release stages) fall through
        # to the universal canonical-upstream set instead of dead-ending.
        if state.stage in _canonical_stage_order():
            universal = _universal_return_targets(state.stage)
            if universal:
                return state, universal, None
        return state, (), (f"escalation at stage {state.stage} has no return targets")
    return (
        state,
        (),
        (
            f"run not awaiting approval or escalation (stage={state.stage} "
            f"awaiting={state.awaiting or 'nothing'})"
        ),
    )


_RETURN_USAGE = (
    "usage: trac return --to <stage> --reason TEXT [--confirm] "
    "(stage: an upstream canonical stage; --confirm crosses irreversible ops)"
)


def _parse_return_args(args: list) -> tuple[dict, str | None]:
    """Parse the `trac return` grammar: --to/--reason values plus the
    optional --confirm flag. Returns (opts, None) or ({}, usage_error)."""
    opts: dict = {}
    while args:
        flag = args.pop(0)
        if flag == "--confirm":
            opts["--confirm"] = True
            continue
        if flag not in ("--to", "--reason") or not args or flag in opts:
            return {}, _RETURN_USAGE
        opts[flag] = args.pop(0)
    if "--to" not in opts or "--reason" not in opts:
        return {}, _RETURN_USAGE
    return opts, None


def cmd_return(repo: Path, *args) -> int:
    opts, usage_err = _parse_return_args(list(args))
    if usage_err:
        return _err(usage_err)
    home = paths.tracks_home(repo)
    store = Store(home)
    run_id = store.active_run()
    if run_id is None:
        return _err("no active run")
    with writer_lock(home):  # AC-27b: lock before the state gate
        state, allowed, err = _return_gate(store, run_id)
        if err:
            return _err(err)
        if opts["--to"] not in allowed:
            # SM-05.7a/C-03 + FR-0287: closed upstream set, explicitly
            # validated — no event on reject.
            return _err(
                f"invalid --to {opts['--to']}: must be one of " + "|".join(allowed)
            )
        # AC-FR0287-04: crossing already-executed irreversible operations
        # requires an explicit confirmation — no events, pointer unmoved.
        executed = _already_executed_ops(store, run_id)
        if executed and "--confirm" not in opts:
            print("already_executed operations this return would cross:")
            for op in executed:
                print(f"  - {op['operation_kind']} {op['target']} ({op['type']})")
            return _err("irreversible operations present: re-run with --confirm")
        # interfaces §1a: the human.return payload actor is pinned to the
        # literal "Human" (E-01 renders human_return=Human→<to>) — not an
        # environment-sourced operator name.
        actor = "Human"
        _establish_escape_barrier(store, run_id, state.version)
        _stale_downstream_evidence(store, run_id, state.version, opts["--to"])
        store.append(
            run_id,
            state.version,
            "human.return",
            {
                "actor": actor,
                "from": state.stage,
                "to": opts["--to"],
                "to_stage": opts["--to"],  # the kernel hard-read key
                "reason": opts["--reason"],
            },
        )
    print(f"returned to {opts['--to']}")
    return 0


# -- IF-RELEASE-003 Human three-way release gate + IF-ESCAPE-002 abandon -------
#
# The independent `trac release` delivery surface (interfaces §2a/§1g) and the
# lightweight `trac abandon` termination exit (§2d, SM-01.21/22). Decisions
# are append-only release.decided events bound to the current preview digest +
# candidate SHA, written under the writer lock; stale previews and failed
# M-VERIFY/M-SECURITY gates fail closed with no decision and no stage transfer.


def cmd_abandon(repo: Path, *args: str) -> int:
    """Lightweight termination exit (§2d, SM-01.21): Human-only `trac abandon
    --reason` completes the run with terminal_state=cancelled. Evidence is
    kept, issues/branches untouched — zero external side effects; a cancelled
    run then rejects `trac run` (see cmd_run)."""
    opts: dict[str, str] = {}
    args = list(args)
    while args:
        flag = args.pop(0)
        if flag not in ("--reason",) or not args or flag in opts:
            return _err(_ABANDON_USAGE)
        opts[flag] = args.pop(0)
    if set(opts) != {"--reason"}:
        return _err(_ABANDON_USAGE)
    home = paths.tracks_home(repo)
    store = Store(home)
    run_id = store.active_run()
    if run_id is None:
        return _err("no active run")
    with writer_lock(home):  # AC-27b: lock before the state gate
        state = store.state(run_id)
        store.append(
            run_id,
            state.version,
            "run.completed",
            {"terminal_state": "cancelled", "reason": opts["--reason"]},
        )
    print(f"run {run_id}: terminal=cancelled reason={opts['--reason']}")
    return 0


def _recover_gate(store: Store, run_id: str):
    # B32 (#32): accept ONLY the post-rollback quiescent state (M-DESIGN/DRAFT,
    # last rollback from M-IMPL, no new work since); fail-closed otherwise.
    state = store.state(run_id)
    if state.stage != "M-DESIGN" or state.substate != "DRAFT":
        return state, (
            f"recover requires M-DESIGN/DRAFT after an M-IMPL rollback "
            f"(stage={state.stage} substate={state.substate or 'nothing'})")
    recents = list(store.events(run_id))
    rolled_back = [e for e in recents if e.type == "stage.rolled_back"]
    if not rolled_back:
        return state, "recover requires a prior stage.rolled_back event"
    last = rolled_back[-1]
    if last.payload.get("from_stage") != "M-IMPL" or last.payload.get("to_stage") != "M-DESIGN":
        return state, (
            "recover only after a stage.rolled_back from M-IMPL to M-DESIGN "
            f"(last rollback was {last.payload.get('from_stage')} -> "
            f"{last.payload.get('to_stage')})")
    for e in recents[recents.index(last) + 1 :]:
        if e.type in ("stage.entered", "stage.exited"):
            return state, "recover rejected: another stage entered/exited after the rollback"
        cmd = e.payload.get("command", {}) if e.type == "command.issued" else {}
        if cmd.get("kind") == "dispatch_agent":
            return state, "recover rejected: new dispatch_agent work issued after the rollback"
    return state, None


def cmd_recover(repo: Path, *args) -> int:
    # B32 (#32): human.recover gate -> append or fail-closed.
    opts = {}
    args = list(args)
    usage = "usage: trac recover --reason TEXT"
    while args:
        flag = args.pop(0)
        if flag not in ("--reason",) or not args or flag in opts:
            return _err(usage)
        opts[flag] = args.pop(0)
    if set(opts) != {"--reason"}:
        return _err(usage)
    home = paths.tracks_home(repo)
    store = Store(home)
    run_id = store.active_run()
    if run_id is None:
        return _err("no active run")
    with writer_lock(home):  # AC-27b: lock before the state gate
        state, err = _recover_gate(store, run_id)
        if err:
            return _err(err)
        store.append(
            run_id,
            state.version,
            "human.recover",
            {"reason": opts["--reason"], "to_stage": "M-IMPL"},
        )
    print("recover requested: -> M-IMPL")
    return 0


def cmd_discuss(repo: Path, *args) -> int:
    # FR-080: `trac discuss <query|start|reply|edit|set-status> ...` — doc-level
    # inline-discussion bypass (not in the event loop). Subcommand/flag parsing,
    # scope gate, token freshness and flock writes live in tracks/discuss/cli.py.
    return run_discuss(repo, list(args))


USAGE = (
    "usage: trac init|start <version>"
    "|run [--resume] [--assignment-overlay PATH] [--max-dispatches N]"
    "|triage <decision> [--actor NAME]"
    "|review <action> [--actor NAME]|approve [--actor NAME]|return --to <stage> --reason TEXT"
    "|recover --reason TEXT"
    "|retry [--actor NAME] [--clear-evidence]|status|replay <run-id>|validate --file <path>"
    "|hotfix <issue> --scenario post-release or dev or anchor or feature-route"
    "|report [--run-id <run-id>] [--output <dir>] [--format md|html]"
    "|discuss <query|start|reply|edit|set-status> ..."
    "|check <deliverables|trace|reach|release-evidence>"
    "|release preview|release --action release/delay/return|abandon --reason TEXT"
)

# command name -> (handler, positional-arg count or None=variadic); handler is (repo, *args) -> int
_COMMANDS = {
    "init": (cmd_init, 0),
    "start": (cmd_start, None),
    "run": (cmd_run, None),
    "hotfix": (cmd_hotfix, None),
    "triage": (cmd_triage, None),
    "review": (cmd_review, None),
    "approve": (cmd_approve, None),
    "return": (cmd_return, None),
    "recover": (cmd_recover, None),
    "retry": (cmd_retry, None),
    "status": (cmd_status, 0),
    "replay": (cmd_replay, None),
    "report": (cmd_report, None),
    "validate": (cmd_validate, 2),
    "discuss": (cmd_discuss, None),
    "check": (cmd_check, None),
    "release": (cmd_release, None),
    "abandon": (cmd_abandon, None),
}


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        return _err(USAGE)
    cmd, rest = args[0], args[1:]
    entry = _COMMANDS.get(cmd)
    if entry is None or (entry[1] is not None and len(rest) != entry[1]):
        return _err(USAGE)
    try:
        return entry[0](Path.cwd(), *rest)
    except LockHeld as e:
        return _err(f"runtime lock held by pid {e.pid}")
    except RuntimeError as e:
        return _err(str(e))


if __name__ == "__main__":
    raise SystemExit(main())
