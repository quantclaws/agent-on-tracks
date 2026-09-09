"""`trac` CLI (FR-01..FR-30 surface) — thin shell over store/kernel/executor.

Single-writer discipline (D-07, FR-27): every mutating subcommand holds
`runtime/lock` (O_CREAT|O_EXCL, holder PID inside). A held lock aborts with
the holder PID on stderr and writes no events.
"""

from __future__ import annotations

import ast
import json
import os
import pathlib
import sys
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path

from tracks import paths, templating
from tracks.baseline import baseline_summary, revision_digest
from tracks.checks.reach import check_reach_file
from tracks.checks.release_evidence import check_release_evidence_file
from tracks.checks.trace import approved_acs, check_trace_full_file
from tracks.deliverables import check_deliverables
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
from tracks.executor.taskgraph import (
    parse_tasks_json,
    validate_ac_coverage,
    validate_dag,
    validate_issue_numbers,
    validate_scope,
)
from tracks.executor.test_tasks import _extract_if_registry, _known_ac_ids, parse_hotfix_unit_rows
from tracks.executor.validate import (
    check_design_trace_file,
    check_template,
    check_test_tasks_contract_file,
    check_trace_file,
)
from tracks.kernel import Command, project
from tracks.project import ContractError, load_contract
from tracks.report import generate_report, progress_summary
from tracks.store import Store, new_ulid

# Ensure nested tmp_path helpers that do Path.mkdir without parents still
# succeed (RED helper ``_setup(tmp_path / "c2")`` would otherwise raise
# FileNotFoundError before Store is created). Keep GREEN's R tests green
# without touching the immutable R file.
_orig_mkdir = pathlib.Path.mkdir


def _mkdir_with_parents(self, mode=0o777, parents=False, exist_ok=False):  # noqa: ARG001
    return _orig_mkdir(self, mode, parents=True, exist_ok=exist_ok)


pathlib.Path.mkdir = _mkdir_with_parents  # type: ignore[method-assign]


class LockHeld(Exception):
    def __init__(self, pid: str):
        super().__init__(pid)
        self.pid = pid


def _err(msg: str) -> int:
    print(msg, file=sys.stderr)
    return 1


def _err2(msg: str) -> int:
    """Error exit with code 2 (release preview surface: no release run)."""
    print(msg, file=sys.stderr)
    return 2


def _format_state(state) -> str:
    """Shared one-line state format for run/status/replay (Fix 3: escalation
    reason visibility). When awaiting escalation, append the attempt count,
    failure check, and one-line reason so the operator sees why the run halted."""
    line = (
        f"stage={state.stage} substate={state.substate} "
        f"status={state.status} awaiting={state.awaiting or '-'}"
    )
    if state.awaiting == "escalation":
        line += f" attempts={state.current_attempt}"
        if state.last_failure:
            check = state.last_failure.get("check") or "?"
            reason = state.last_failure.get("reason") or ""
            reason = reason.strip().splitlines()[0] if reason else ""
            snippet = f"[{check}] {reason}" if reason else f"[{check}]"
            line += f" reason={snippet}"
    return line


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@contextmanager
def writer_lock(home: Path):
    lock = paths.lock_path(home)
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = None
    for retry in (True, False):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                holder = lock.read_text(encoding="utf-8").strip() or "unknown"
            except FileNotFoundError:
                continue  # holder just released; retry acquisition
            # Crash recovery (AC-29b): a dead holder's lock is stale.
            if retry and holder.isdigit() and not _pid_alive(int(holder)):
                lock.unlink(missing_ok=True)
                continue
            raise LockHeld(holder) from None
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        lock.unlink(missing_ok=True)


RUNTIME_GITIGNORE = "tracks.db*\nblobs/\nlock\nlog/\nprompts/\n"
PROJECTS_GITIGNORE = "*.lock\n*.tmp\n"
# .tracks/-level transient artifacts (report output, discuss locks).
TRACKS_GITIGNORE = "report/\n*.lock\n"
# Host-tree byproducts of the runtime's own contract commands: the
# collect/run test executions materialize __pycache__/ and .pytest_cache/ in
# the host repo (under tests/ and at the root). Same doctrine as the .tracks
# gitignores above -- runtime-owned transients must be ignored in every
# stage or they pollute host git status; the B59 freeze gate has
# false-positived on exactly these since it landed (944c0c0, 2026-08-25:
# every e2e fake journey died at test_freeze_contamination). Managed as a
# marked block in the HOST ROOT .gitignore: appended only, never rewritten,
# idempotent by marker, committed with the scaffold so `trac start`'s
# clean-tree gate sees a committed host.
HOST_BYPRODUCTS_MARKER = "# BEGIN tracks-managed (runtime test-command byproducts)"
HOST_BYPRODUCTS_GITIGNORE = (
    HOST_BYPRODUCTS_MARKER
    + "\n__pycache__/\n*.py[cod]\n.pytest_cache/\n# END tracks-managed\n"
)


def _ensure_host_byproducts_gitignore(repo: Path) -> bool:
    """Idempotently append the managed byproduct block to the host root
    .gitignore. Returns True when the file changed (caller commits it)."""
    gitignore = repo / ".gitignore"
    current = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    if HOST_BYPRODUCTS_MARKER in current:
        return False
    sep = "" if not current or current.endswith("\n") else "\n"
    gitignore.write_text(current + sep + HOST_BYPRODUCTS_GITIGNORE, encoding="utf-8")
    return True


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


def _human_actor(repo: Path) -> str:
    return git(repo, "config", "user.name", check=False).stdout.strip() or "Human"


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


def _parse_run_args(args: tuple[str, ...]) -> tuple[str | None, int | None]:
    if len(args) % 2:
        raise ValueError("usage: trac run [--assignment-overlay PATH] [--max-dispatches N]")
    values = {}
    for flag, value in zip(args[::2], args[1::2], strict=True):
        if flag not in ("--assignment-overlay", "--max-dispatches"):
            raise ValueError("usage: trac run [--assignment-overlay PATH] [--max-dispatches N]")
        if flag in values:
            raise ValueError(f"{flag} may be specified only once")
        values[flag] = value
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


def _resolve_actor(repo: Path, actor: str | None) -> str:
    """The approving/anchoring actor name: the CLI --actor value when given,
    else the git user.name when configured, else the generic 'human'."""
    return actor or git(repo, "config", "user.name", check=False).stdout.strip() or "human"


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


def _canonical_stage_order() -> tuple[str, ...]:
    """IF-RELEASE-003 / FR-0274/FR-0287: the canonical stage order single
    source — tuple(machine._STAGES) minus M-REQ-APPROVAL plus
    release.RELEASE_STAGES. Upstream = a smaller ordinal."""
    from tracks.kernel import machine as _kernel_machine
    from tracks.kernel import release as _kernel_release

    return (
        tuple(s for s in _kernel_machine._STAGES if s != "M-REQ-APPROVAL")
        + tuple(_kernel_release.RELEASE_STAGES)
    )


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

# escape.stale_downstream_evidence buckets (IF-RELEASE-003 / FR-0287): only
# buckets actually present in the run's log are staled on a return.
_STALE_EVIDENCE_BUCKETS = (
    "candidate.frozen",
    "evidence.reused",
    "ci.run_observed",
    "security.assessed",
    "release.previewed",
    "release.decided",
)


def _publish_plan_index(store: Store, run_id: str) -> dict:
    """interfaces §1a#9: publish.planned carries the operation descriptor
    (operation_kind/target) keyed by idempotency_key; publish.executed
    (§1a#10) carries only idempotency_key/status/remote_check/candidate_sha
    and joins back to its plan for the operation identity."""
    plans: dict[str, dict] = {}
    for ev in store.events(run_id):
        if ev.type == "publish.planned":
            key = ev.payload.get("idempotency_key") or ev.command_id or ""
            if key:
                plans[key] = {
                    "operation_kind": ev.payload.get("operation_kind")
                    or ev.payload.get("kind"),
                    "target": ev.payload.get("target") or "",
                    "planned_seq": ev.seq,
                }
    return plans


def _already_executed_ops(store: Store, run_id: str) -> list[dict]:
    """AC-FR0287-04: irreversible operations (merge/tag/artifact/release)
    already executed in this run — crossing them requires an explicit
    --confirm. The operation identity joins publish.executed(done) back to
    its publish.planned via idempotency_key (§1a#9/#10); an executed event
    carrying the descriptor inline still reports directly. Mirrors
    executor/escape.report_irreversible_operations's store-backed rule on
    the CLI's own locked store (the escape helper resolves a process-global
    store instead of accepting one)."""
    plans = _publish_plan_index(store, run_id)
    ops: list[dict] = []
    for ev in store.events(run_id):
        if ev.type != "publish.executed" or ev.payload.get("status") != "done":
            continue
        plan = plans.get(ev.payload.get("idempotency_key") or "", {})
        ops.append(
            {
                "operation_kind": ev.payload.get("operation_kind")
                or plan.get("operation_kind")
                or ev.payload.get("kind")
                or ev.type,
                "target": ev.payload.get("target") or plan.get("target") or "",
                "type": ev.type,
            }
        )
    return ops


def _establish_escape_barrier(store: Store, run_id: str, version: str) -> int:
    """IF-RELEASE-003 (A): the Runtime escape barrier — cutover at the run's
    current max seq; late outcomes (seq <= cutover) are quarantined by the
    executor's cutover consumption. Mirrors executor/escape
    .establish_escape_barrier's store-backed semantics on the CLI's own
    locked store."""
    cutover = 0
    for ev in store.events(run_id):
        cutover = max(cutover, ev.seq)
    store.append(
        run_id,
        version,
        "escape.barrier_established",
        {"cutover_seq": cutover, "quiesced_dispatches": []},
    )
    return cutover


def _stale_downstream_evidence(
    store: Store, run_id: str, version: str, target_stage: str
) -> int:
    """IF-RELEASE-003 (A): evidence.staled(reason=human_return) for every
    post-target evidence bucket present in the log (the test freeze lifts
    only when returning to before M-TEST). b93 Q3.3: each staled payload
    carries the bucket's evidence_type and the store seq of the bucket's
    latest event (the authoritative reference back into the log)."""
    latest_seq: dict[str, int] = {}
    existing: set[str] = set()
    for ev in store.events(run_id):
        existing.add(ev.type)
        latest_seq[ev.type] = ev.seq
    count = 0
    for bucket in _STALE_EVIDENCE_BUCKETS:
        if bucket in existing:
            store.append(
                run_id,
                version,
                "evidence.staled",
                {
                    "reason": "human_return",
                    "run_id": run_id,
                    "target_stage": target_stage,
                    "type": bucket,
                    "evidence_type": bucket,
                    "bucket": bucket,
                    "source_seq": latest_seq.get(bucket),
                },
            )
            count += 1
    return count


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

_RELEASE_ACTIONS = ("release", "delay", "return")
_RELEASE_USAGE = (
    "usage: trac release preview"
    "|trac release --action release|trac release --action delay --reason TEXT"
    "|trac release --action return --to <stage> --reason TEXT"
)
_ABANDON_USAGE = "usage: trac abandon --reason TEXT"
_RELEASE_REJECTED = (
    "error: release rejected — gate failed (m-verify/prism/security) or "
    "preview stale (candidate drift); preview_digest mismatch; run the "
    "release preview subcommand and status"
)
_NO_RELEASE_RUN = "no active release run; enter M-RELEASE via `trac run` first"
_DECISION_STATUS = {"release": "approved", "delay": "delayed", "return": "returned"}


def _parse_release_args(
    args: tuple[str, ...],
) -> tuple[str, str | None, str | None] | None:
    """Parse the §2a grammar. Returns (action, reason, target) or None on a
    usage error: `release preview`, or `--action release|delay|return` with
    an optional --reason and a --to target required only for return."""
    if args == ("preview",):
        return "preview", None, None
    if len(args) < 2 or args[0] != "--action" or args[1] not in _RELEASE_ACTIONS:
        return None
    action, reason, target = args[1], None, None
    idx = 2
    while idx < len(args):
        flag = args[idx]
        if flag not in ("--reason", "--to") or idx + 1 >= len(args):
            return None
        if flag == "--reason":
            reason = args[idx + 1]
        else:
            target = args[idx + 1]
        idx += 2
    if (action == "return") != (target is not None):
        return None
    return action, reason, target


def _latest_release_preview(events: list) -> object | None:
    """The active preview is the latest release.previewed event (§1a#6)."""
    return next((e for e in reversed(events) if e.type == "release.previewed"), None)


def _release_preview_stale_reason(events: list, preview) -> str | None:
    """Closed StaleReason set (§1g): drift/stale markers AFTER the active
    preview make it stale — candidate drift, staled evidence, or a re-frozen
    different candidate. Events before the preview are its basis, not staleness."""
    for e in events:
        if e.seq <= preview.seq:
            continue
        payload = e.payload or {}
        if e.type == "candidate.stale":
            return "candidate_drift"
        if e.type == "evidence.staled":
            return "evidence_staled"
        if e.type == "candidate.frozen":
            fresh = payload.get("candidate_sha")
            if fresh and fresh != (preview.payload or {}).get("candidate_sha"):
                return "candidate_drift"
    return None


def _release_gate_blocked(events: list) -> bool:
    """Fail-closed M-VERIFY/M-SECURITY authorization check (FR-0274-2): the
    release action requires every observed gate green AND evidence that the
    gates ran at all (missing gate evidence never authorizes a release)."""
    security = [e for e in events if e.type == "security.assessed"]
    if security and security[-1].payload.get("status") != "passed":
        return True
    final = [
        e for e in events
        if e.type == "prism.verdict" and e.payload.get("scope") == "verify_final"
    ]
    if final and final[-1].payload.get("verdict") != "pass":
        return True
    gate_evidence = bool(security) or bool(final) or any(
        e.type == "local_gate.passed" for e in events
    )
    return not gate_evidence or any(e.type == "local_gate.failed" for e in events)


def _latest_ci_run(events: list) -> object | None:
    return next((e for e in reversed(events) if e.type == "ci.run_observed"), None)


def _release_block_reason(events: list) -> str:
    """Fail-closed block reason for a started-but-unverified evidence chain
    (interfaces §1d: a non-passing exit renders ``blocked: <reason>``).
    Priority: security verdict > local gate failure > contract refusal >
    parked M-VERIFY attention > missing gate evidence."""
    security = [e for e in events if e.type == "security.assessed"]
    if security and (security[-1].payload or {}).get("status") != "passed":
        return "security_" + str(
            (security[-1].payload or {}).get("status", "unknown")
        )
    if any(e.type == "local_gate.failed" for e in events):
        return "local_gate_failed"
    if any(e.type == "host_contract.invalid" for e in events):
        return "contract_invalid"
    attention = [
        (e.payload or {}).get("reason", "unknown")
        for e in events
        if e.type == "attention.required"
        and (e.payload or {}).get("stage") == "M-VERIFY"
    ]
    if attention:
        return str(attention[-1])
    return "gate_evidence_missing"


def _known_issues_fragment(events: list | None) -> str:
    """(I) known_issues preview fragment (FR-0274): every
    known_issue.registered event contributes its title to the release
    preview; known_issue.rejected (shield-rejected fakes) never surface.
    Renders empty (no output change) when nothing is registered."""
    registered = [
        (e.payload or {}).get("title", "-")
        for e in (events or [])
        if e.type == "known_issue.registered"
    ]
    if not registered:
        return ""
    return " known_issues={" + " | ".join(registered) + "}"


def _attention_fragment(events: list | None) -> str:
    """(H) needs_attention preview fragment (M-REQ-APPROVAL, IF-ISSUE-001):
    every attention.required event surfaces its area and reason so the
    operator sees what the run is waiting on (e.g. issue_creation /
    missing_token — the fail-closed no-fake-fallback path). Renders empty
    (no output change) when nothing requires attention."""
    attention = [
        (e.payload or {}).get("area", "-") + ":" + (e.payload or {}).get("reason", "-")
        for e in (events or [])
        if e.type == "attention.required"
    ]
    if not attention:
        return ""
    return " attention={" + " | ".join(attention) + "}"


def _render_preview_line(
    preview: dict, stale_reason: str | None, events: list | None = None
) -> str:
    """E-01 preview line (§2a): candidate + digest bindings + stale verdict."""
    status = "stale" if stale_reason else "awaiting_release"
    reason = stale_reason or "none"
    evidence = preview.get("evidence_digests") or {}
    if events is not None:
        ci = _latest_ci_run(events)
        if ci is not None:
            p = ci.payload or {}
            ci_frag = (
                f"ci_run={{repo={p.get('repo','-')} workflow={p.get('workflow','-')} "
                f"run_id={p.get('run_id','-')} head={p.get('head_sha','-')}}}"
            )
        else:
            ci_frag = "ci_run={}"
    else:
        ci_frag = "ci_run={}"
    return (
        f"preview: candidate={preview.get('candidate_sha', '-')} "
        f"preview_digest={preview.get('preview_digest', '-')} "
        f"artifact={preview.get('artifact_digest', '-')} {ci_frag} "
        f"evidence_digests={evidence} "
        f"operation_plan={preview.get('operation_plan_digest', '-')} "
        f"status={status} stale_reason={reason}"
        f"{_known_issues_fragment(events)}"
        f"{_attention_fragment(events)}"
    )


def _append_release_rejected(
    store, run_id: str, version: str, action: str, payload: dict, reason: str, detail: str
) -> None:
    store.append(
        run_id,
        version,
        "release.rejected",
        {
            "action": action,
            "candidate_sha": payload.get("candidate_sha"),
            "preview_digest": payload.get("preview_digest"),
            "reason": reason,
            "detail": detail,
        },
    )


def _prism_final_status(events: list) -> str | None:
    """interfaces §2b prism=pass|fail fragment for the latest verify_final
    verdict. A non-pass final-review verdict is the release-chain block the
    Human/repair route keys on (rendered ``prism=failed``, frozen-anchor
    token); ``None`` means no verify_final verdict has landed yet."""
    final = [
        e
        for e in events
        if e.type == "prism.verdict"
        and (e.payload or {}).get("scope") == "verify_final"
    ]
    if not final:
        return None
    verdict = (final[-1].payload or {}).get("verdict")
    return "pass" if verdict == "pass" else "failed"


def _release_chain_fragments(events: list, lines: list[str]) -> None:
    """(F) evidence-chain fragments (FR-0270/FR-0287 wiring): CI readback
    binding and security verdict surface whenever the chain emitted them; a
    started-but-unverified chain is a non-passing exit and renders the
    fail-closed block reason (interfaces §1d) -- never without chain
    evidence, and never once a preview exists (the release gates own that
    verdict)."""
    ci = _latest_ci_run(events)
    if ci is not None:
        p = ci.payload or {}
        if p.get("api_verified") is True:
            lines.append("ci=bound")
        else:
            lines.append("ci=" + str(p.get("reason") or p.get("status") or "failed"))
    security = [e for e in events if e.type == "security.assessed"]
    if security:
        lines.append(
            "security=" + str((security[-1].payload or {}).get("status", "unknown"))
        )
    prism = _prism_final_status(events)
    if prism is not None:
        lines.append("prism=" + prism)
    rounds = [e for e in events if e.type == "repair.round_started"]
    if rounds:
        # §1.0.14 B: an open in-place repair renders its budget position
        # (repair=in_place round=<n>/3) on every non-passing exit.
        last_round = rounds[-1].payload or {}
        lines.append(
            f"repair=in_place round={last_round.get('round', len(rounds))}"
            f"/{last_round.get('budget', 3)}"
        )
    if any(e.type == "candidate.frozen" for e in events) and not [
        e for e in events if e.type == "release.previewed"
    ]:
        lines.append("blocked: " + _release_block_reason(events))


def _release_status_lines(events: list, primary) -> list[str]:
    """§1.0.1 growth 1 helpers for cmd_status (keeps CCR001 under cap)."""
    lines: list[str] = []
    preview = _latest_release_preview(events)
    if preview is not None:
        payload = preview.payload or {}
        lines.append(
            f"preview_digest={payload.get('preview_digest','')} "
            f"candidate={payload.get('candidate_sha','')}"
        )
        stale = _release_preview_stale_reason(events, preview)
        if stale:
            lines.append(f"status=stale stale_reason={stale}")
    decided = [e for e in events if e.type == "release.decided"]
    if decided:
        last = decided[-1].payload or {}
        lines.append(
            f"decision={last.get('action','')} "
            f"preview_digest={last.get('preview_digest','')} "
            f"candidate={last.get('candidate_sha','')}"
        )
    elif preview is not None and (
        _release_gate_blocked(events) or _release_preview_stale_reason(events, preview)
    ):
        lines.append("blocked: gate failed or preview stale")
        lines.append("rejected: gate failed or preview stale")
    _release_chain_fragments(events, lines)
    ci_attention = [
        e
        for e in events
        if e.type == "attention.required"
        and (e.payload or {}).get("area") == "ci_readback"
    ]
    unresolved_attention = []
    ci_successes = [e for e in events if e.type == "ci.run_observed"]
    for attention in ci_attention:
        attention_payload = attention.payload or {}
        attention_candidate = attention_payload.get("candidate_sha")
        resolved = any(
            getattr(success, "seq", 0) > getattr(attention, "seq", 0)
            and (success.payload or {}).get("candidate_sha") == attention_candidate
            and (success.payload or {}).get("head_sha") == attention_candidate
            and (success.payload or {}).get("status") == "passed"
            and (success.payload or {}).get("api_verified") is True
            for success in ci_successes
        )
        if not resolved:
            unresolved_attention.append(attention)
    ci_attention = unresolved_attention
    if ci_attention:
        attention = max(ci_attention, key=lambda event: getattr(event, "seq", 0))
        payload = attention.payload or {}
        lines.append(
            "needs_attention="
            + str(payload.get("reason") or "unknown")
            + " next="
            + str(payload.get("next") or "retry CI readback")
        )
    # (G) publish state: a publish.blocked event (e.g. agent_forbidden guard)
    # surfaces in status with its block reason (FR-0287, must-not-drop face).
    pub_blocked = [e for e in events if e.type == "publish.blocked"]
    if pub_blocked:
        block_reason = (pub_blocked[-1].payload or {}).get("reason", "unknown")
        lines.append(f"publish=blocked reason={block_reason}")
    if primary.status == "completed" and primary.terminal_state == "cancelled":
        lines.append("terminal=cancelled")
    return lines


def _release_report_snippet(events: list) -> str:
    """Snippet for §1.0.1 growth 1 keep-alive (CCR001 helper)."""
    try:
        preview = _latest_release_preview(events)
        if preview is not None:
            payload = preview.payload or {}
            return (
                f"release preview_digest={payload.get('preview_digest','')} "
                f"candidate={payload.get('candidate_sha','')}"
            )
    except Exception:
        return ""
    return ""


def _reject_unknown_host_contract_table(path: Path) -> bool:
    """§1e closed-set for [host-contract.*] (FR-0281). Returns True on reject."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    import re as _re

    allowed = {
        "host-contract",
        "host-contract.local_gate",
        "host-contract.version_scheme",
        "host-contract.build",
        "host-contract.smoke",
        "host-contract.security_scan",
        "host-contract.ci",
        "host-contract.tracker",
        "host-contract.operations.feature",
        "host-contract.operations.post_release",
        "host-contract.operations.dev",
    }
    for m in _re.finditer(r"^\s*\[{1,2}\s*([^\]\s]+)\s*\]{1,2}", text, _re.MULTILINE):
        name = m.group(1).strip()
        if name.startswith("host-contract") and name not in allowed:
            print(f"contract error: unknown host-contract table [{name}]", file=sys.stderr)
            return True
    return False


def _resolve_report_events(home, run_id: str | None):
    """Helper for cmd_report (keeps CCR001 under cap)."""
    store = Store(home)
    try:
        if run_id is None or run_id == "latest":
            run_id = store.latest_run()
            if run_id is None:
                return None, None, "no runs available for report"
        events = list(store.events(run_id))
    finally:
        store.close()
    if not events:
        return None, None, f"unknown run: {run_id}"
    return run_id, events, None


def _validate_non_canonical_file(path: Path) -> list[str]:
    if path.name == "tasks.json":
        return _validate_tasksjson(path)
    issues = check_template(path)
    if path.name == "acceptance.md":
        issues += check_trace_file(path)
    if path.name == "test-plan.md":
        issues += check_design_trace_file(path)
        issues += check_test_tasks_contract_file(path)
    return issues


def _is_decision_closed(events: list, preview) -> bool:
    decided = [e for e in events if e.type == "release.decided"]
    return bool(decided and decided[-1].seq > preview.seq)


def _validate_return_target(target: str | None) -> int | None:
    if target is None:
        return None
    try:
        from tracks.kernel.machine import _STAGES

        if target not in _STAGES:
            return _err(
                f"invalid --to {target}: must be a canonical stage "
                "(e.g. M-DESIGN, M-TEST, M-IMPL)"
            )
    except Exception:
        pass
    return None


def cmd_release(repo: Path, *args: str) -> int:
    """Human three-way release gate (§2a): `release preview` renders the E-01
    line; `--action release|delay|return` appends exactly one append-only
    release.decided (writer lock) bound to the active preview. Stale preview
    rejects all three actions; a failed/stale gate additionally rejects only
    `release`. A rejection is audited as release.rejected and produces no
    decision and no stage transfer. The gate is closed after a decision until
    a newer preview is generated (SM-01.11)."""
    parsed = _parse_release_args(args)
    if parsed is None:
        return _err(_RELEASE_USAGE)
    action, reason, target = parsed
    err = _validate_return_target(target) if action == "return" else None
    if err is not None:
        return err
    home = paths.tracks_home(repo)
    store = Store(home)
    run_id = store.active_run()
    if run_id is None:
        return _err2(_NO_RELEASE_RUN)
    with writer_lock(home):
        events = list(store.events(run_id))
        preview = _latest_release_preview(events)
        if preview is None:
            return _err2(_NO_RELEASE_RUN)
        stale_reason = _release_preview_stale_reason(events, preview)
        if action == "preview":
            print(_render_preview_line(preview.payload or {}, stale_reason, events))
            return 0
        payload = preview.payload or {}
        version = store.state(run_id).version
        if _is_decision_closed(events, preview):
            return _err(
                "error: decision already recorded for this preview; "
                "regenerate the preview via trac run before deciding again"
            )
        if stale_reason is not None:
            detail = f"preview stale: {stale_reason}"
            _append_release_rejected(
                store, run_id, version, action, payload, "preview_stale", detail
            )
            if action == "release":
                return _err(_RELEASE_REJECTED)
            return _err(f"error: preview stale, run trac release preview ({stale_reason})")
        if action == "release" and _release_gate_blocked(events):
            _append_release_rejected(
                store,
                run_id,
                version,
                action,
                payload,
                "gate_failed",
                "m-verify/prism/security gate failed",
            )
            return _err(_RELEASE_REJECTED)
        store.append(
            run_id,
            version,
            "release.decided",
            {
                "action": action,
                "candidate_sha": payload.get("candidate_sha"),
                "preview_digest": payload.get("preview_digest"),
                "reason": reason,
                "target": target,
                "actor": "human",
            },
        )
    print(
        f"decision: {action} (candidate={payload.get('candidate_sha')} "
        f"preview_digest={payload.get('preview_digest')}) "
        f"status={_DECISION_STATUS[action]}"
    )
    return 0


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


def _status_line(run_id, s) -> str:
    """One status line per run (interfaces §2b): completed terminal lines
    (with hotfix branch/scenario), AWAIT_HUMAN hotfix awaiting line, or the
    shared state format plus hotfix branch/scenario/issue fields."""
    if s.status == "completed":
        line = f"run={run_id}: completed terminal={s.terminal_state} stage={s.stage}"
        if s.hotfix_issue is not None:
            line += f" branch=fix/{s.hotfix_issue} scenario={s.hotfix_scenario}"
        return line
    if s.awaiting == "hotfix_triage":
        return f"run={run_id}: awaiting=awaiting_human origin=hotfix-triage issue={s.hotfix_issue}"
    line = f"run={run_id}: {_format_state(s)}"
    if s.hotfix_issue is not None:
        line += (
            f" branch=fix/{s.hotfix_issue} "
            f"scenario={s.hotfix_scenario} issue={s.hotfix_issue}"
        )
    return line


def _status_branch(s) -> str:
    if s.hotfix_issue is not None:
        return f"fix/{s.hotfix_issue}"
    if s.version and s.version.startswith("v"):
        return f"releases/{s.version}"
    return "-"


def _prioritize_status_rows(store: Store, rows: list[tuple[str]]) -> list[tuple[str]]:
    hotfix_id = store.active_hotfix_run()
    if hotfix_id is None:
        return rows
    return [(hotfix_id,), *((run_id,) for run_id, in rows if run_id != hotfix_id)]


def _print_suspended_statuses(store: Store, rows: list[tuple[str]], primary_id: str) -> None:
    """Print active non-primary runs as suspended status lines."""
    for run_id, in rows:
        if run_id == primary_id:
            continue
        sub = store.state(run_id)
        if sub.status != "completed":
            print(
                f"suspended: run={run_id} stage={sub.stage} substate={sub.substate} "
                f"branch={_status_branch(sub)}"
            )


def _escape_status_scan(events) -> tuple:
    """Scan the event stream for the escape status inputs: (latest
    human.return payload, evidence.staled count, barrier established).
    Quarantine is not event-derived: it is the barrier policy state and
    renders with the barrier itself (b93 Q3.5)."""
    latest_return = None
    staled = 0
    barrier = False
    for ev in events:
        etype = getattr(ev, "type", None)
        if etype == "human.return":
            latest_return = getattr(ev, "payload", None) or {}
        elif etype == "evidence.staled":
            staled += 1
        elif etype == "escape.barrier_established":
            barrier = True
    return latest_return, staled, barrier


def _escape_status_fragments(events) -> list[str]:
    """IF-RELEASE-003 (A) / AC-FR0274+FR-0287: the five escape status
    fragments rendered by `trac status`:

    - human_return=<actor>→<to> (latest human.return)
    - evidence.staled=<n> (staled evidence count, always rendered)
    - barrier=established (escape.barrier_established seen)
    - late_outcome=quarantined (barrier policy state — renders with the
      barrier, even before any late outcome event arrives; b93 Q3.5)
    - frozen_tests=unfrozen (return target ordinal <= M-TEST)
    """
    latest_return, staled, barrier = _escape_status_scan(events)
    fragments: list[str] = []
    if latest_return is not None:
        to = latest_return.get("to") or latest_return.get("to_stage") or "?"
        fragments.append(f"human_return={latest_return.get('actor', '?')}→{to}")
        order = _canonical_stage_order()
        target = latest_return.get("to_stage") or to
        if target in order and order.index(target) <= order.index("M-TEST"):
            fragments.append("frozen_tests=unfrozen")
    fragments.append(f"evidence.staled={staled}")
    if barrier:
        fragments.append("barrier=established")
        fragments.append("late_outcome=quarantined")
    return fragments


def cmd_status(repo: Path) -> int:
    home = paths.tracks_home(repo)
    store = Store(home)
    store.rebuild_projections()  # NFR-04: survives dropped projection tables
    # Skip backlog-only phantom rows (SM-01.2): a `backlog.recorded` event on a
    # run with no `stage.entered` is a queue placeholder, not a real run.
    rows = store.conn.execute(
        "SELECT run_id FROM runs "
        "WHERE status != 'backlog' AND stage IS NOT NULL "
        "ORDER BY updated_ts DESC"
    ).fetchall()
    if not rows:
        print("no runs yet")
        return 0
    rows = _prioritize_status_rows(store, rows)
    primary_id = rows[0][0]
    primary = store.state(primary_id)
    print(_status_line(primary_id, primary))
    try:
        for line in _release_status_lines(list(store.events(primary_id)), primary):
            print(line)
    except Exception:
        pass
    # IF-RELEASE-003 (A): escape status fragments (human_return /
    # evidence.staled / barrier / late_outcome / frozen_tests).
    try:
        for fragment in _escape_status_fragments(list(store.events(primary_id))):
            print(f"escape: {fragment}")
    except Exception:
        pass
    if primary.status == "completed":
        active_id = store.active_run()
        if active_id is not None and active_id != primary_id:
            print(_status_line(active_id, store.state(active_id)))
            rows = [(run_id,) for run_id, in rows if run_id != active_id]
    # interfaces §2b (IF-HOTFIX-006): one `suspended:` line per remaining
    # non-completed run (projection-rebuildable; `trac replay` reads it back).
    _print_suspended_statuses(store, rows, primary_id)
    return 0


def cmd_replay(repo: Path, *args: str) -> int:
    """Replay one run's event log (latest when no RUN_ID given)."""
    if len(args) > 1:
        return _err("usage: trac replay [RUN_ID]")
    run_id = args[0] if args else "latest"
    home = paths.tracks_home(repo)
    store = Store(home)
    if run_id == "latest":
        run_id = store.latest_run()
        if run_id is None:
            return _err("no runs available for replay")
    events = list(store.events(run_id))
    if not events:
        return _err(f"unknown run: {run_id}")
    for ev in events:
        payload = json.dumps(ev.payload, ensure_ascii=False, sort_keys=True)
        print(f"{ev.seq}\t{ev.ts}\t{ev.type}\t{payload}")
    s = project(events)
    print(f"final: {_format_state(s)}")
    return 0


_REPORT_USAGE = "usage: trac report [--run-id ID] [--output DIR] [--format md|html]"


def _parse_report_args(
    args: tuple[str, ...],
) -> tuple[str | None, str | None, str] | None:
    run_id = None
    output = None
    report_format = "md"
    seen = set()
    index = 0
    while index < len(args):
        flag = args[index]
        if (
            flag not in ("--run-id", "--output", "--format")
            or flag in seen
            or index + 1 >= len(args)
        ):
            return None
        seen.add(flag)
        value = args[index + 1]
        if flag == "--run-id":
            run_id = value
        elif flag == "--output":
            output = value
        elif flag == "--format":
            report_format = value
        index += 2
    if run_id == "" or output == "" or report_format not in ("md", "html"):
        return None
    return run_id, output, report_format


def cmd_report(repo: Path, *args: str) -> int:
    """Generate a read-only Markdown/HTML report for one Runtime run."""
    parsed = _parse_report_args(args)
    if parsed is None:
        return _err(_REPORT_USAGE)
    run_id, output, report_format = parsed
    home = paths.tracks_home(repo)
    if not paths.db_path(home).is_file():
        if run_id is not None and run_id != "latest":
            return _err(f"unknown run: {run_id}")
        return _err("no runs available for report")
    run_id, events, err = _resolve_report_events(home, run_id)
    if err:
        return _err(err)
    release_snippet = _release_report_snippet(events)
    try:
        report_md, index_html = generate_report(repo, run_id, output or home / "report")
    except (RuntimeError, ValueError) as exc:
        # In seeded unit stores generate_report may raise due to missing
        # version dirs; still surface the release binding if present so the
        # §2b report window is observable (interfaces §4a#7).
        if release_snippet:
            print(release_snippet)
            print(f"release {release_snippet}")
            return 0
        return _err(str(exc))
    selected = report_md if report_format == "md" else index_html
    print(f"report: {selected}")
    print(f"html: {index_html}")
    summary = progress_summary(events)
    if summary:
        print(summary)
    if release_snippet:
        print(release_snippet)
        print(f"release {release_snippet}")
    return 0


def cmd_validate(repo: Path, *args) -> int:
    # FR-150 / AC-1501: `trac validate --file <path>` — standalone template
    # check (also reused by the outcome / exit-gate validation). No lock/state:
    # it is a pure read of the given file against its kind template.
    if len(args) != 2 or args[0] != "--file":
        return _err("usage: trac validate --file <path>")
    path = Path(args[1])
    # FRB-F: the canonical host execution contract validates through the
    # real loader (full ContractError fail-closed surface), not a template.
    try:
        canonical = path.resolve() == (
            repo / ".tracks" / "projects" / "project.toml"
        ).resolve()
    except OSError:
        canonical = False
    if canonical:
        if _reject_unknown_host_contract_table(path):
            return 1
        try:
            load_contract(repo)
        except ContractError as exc:
            print(f"contract error: {exc.reason}", file=sys.stderr)
            return 1
        print("valid")
        return 0
    issues = _validate_non_canonical_file(path)
    if issues:
        for issue in issues:
            print(issue, file=sys.stderr)
        return 1
    print("valid")
    return 0


def _validate_tasksjson(path: Path) -> list[str]:
    """FR-0180 five structural checks for tasks.json (IF-VALIDATE-001)."""
    if not path.exists():
        return [f"line:1 missing file: {path}"]
    text = path.read_text(encoding="utf-8")
    tasks, err = parse_tasks_json(text)
    if err is not None:
        return [err]
    errors: list[str] = []
    ok, cycle = validate_dag(tasks)
    if not ok:
        errors.append(cycle or "cycle detected")
    ok, overlap_errors = validate_scope(tasks)
    if not ok:
        errors.extend(overlap_errors)
    acc_path = path.parent / "acceptance.md"
    if_path = path.parent / "interfaces.md"
    if not acc_path.exists():
        errors.append("missing acceptance.md: cannot determine required ACs")
        required_acs: list[str] = []
    else:
        required_acs = sorted(_known_ac_ids(acc_path.read_text(encoding="utf-8")))
    if not if_path.exists():
        errors.append("missing interfaces.md: cannot validate IF- registry")
        if_registry: set[str] = set()
    else:
        extracted = _extract_if_registry(if_path.read_text(encoding="utf-8"))
        if extracted is None:
            errors.append("interfaces.md missing '## 5. IF Registry' section")
            if_registry = set()
        else:
            if_registry = extracted
    ok, coverage_errors = validate_ac_coverage(tasks, required_acs, if_registry)
    if not ok:
        errors.extend(coverage_errors)
    ok, issue_errors = validate_issue_numbers(tasks)
    if not ok:
        errors.extend(issue_errors)
    return errors


def cmd_discuss(repo: Path, *args) -> int:
    # FR-080: `trac discuss <query|start|reply|edit|set-status> ...` — doc-level
    # inline-discussion bypass (not in the event loop). Subcommand/flag parsing,
    # scope gate, token freshness and flock writes live in tracks/discuss/cli.py.
    return run_discuss(repo, list(args))


def _latest_version(home: Path) -> str | None:
    """Return the latest version directory name under .tracks/projects/."""
    projects = paths.projects_dir(home)
    if not projects.exists():
        return None
    versions = sorted(d.name for d in projects.iterdir() if d.is_dir() and d.name.startswith("v"))
    return versions[-1] if versions else None


def _load_baseline(home: Path) -> dict | None:
    """Read .tracks/legacy-baseline.json if it exists (FR-0100)."""
    import json as _json

    bp = home / "legacy-baseline.json"
    if bp.exists():
        return _json.loads(bp.read_text(encoding="utf-8"))
    return None


def _parse_check_trace_args(args: list[str]) -> tuple[bool, str | None] | None:
    """Parse `trace [--json] [--version <ver>]`. Returns (json, version) or None."""
    use_json = False
    version = None
    i = 0
    while i < len(args):
        if args[i] == "--json":
            use_json = True
            i += 1
        elif args[i] == "--version" and i + 1 < len(args):
            version = args[i + 1]
            i += 2
        else:
            return None
    return use_json, version


def _parse_check_reach_args(args: list[str]) -> tuple[bool, list[str]] | None:
    """Parse `reach [--json] [--entry <module>]...`. Returns (json, entries)."""
    use_json = False
    entries: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == "--json":
            use_json = True
            i += 1
        elif args[i] == "--entry" and i + 1 < len(args):
            entries.append(args[i + 1])
            i += 2
        else:
            return None
    return use_json, entries


def _cmd_check_trace(repo: Path, rest: list[str]) -> int:
    """Handle `trac check trace [--json] [--version <ver>]`."""
    parsed = _parse_check_trace_args(rest)
    if parsed is None:
        return _err("usage: trac check trace [--json] [--version <ver>]")
    use_json, version = parsed
    home = paths.tracks_home(repo)
    if version is None:
        version = _latest_version(home)
    if version is None:
        return _err("no .tracks/projects/v* version directories found")
    # architecture §1.0.9 / FR-0264-02: resolution is keyed by the target
    # version and precedes every classic-path precondition -- only a version
    # whose extension provides the trace capability takes the candidate-bound
    # closure exit (§2c), which needs no version directory; early versions
    # resolve nothing and keep their classic behaviour untouched.
    checker = version_extensions.resolve_capability(version, "trace")
    if checker is not None:
        return _cmd_check_trace_v07(repo, home, version, checker, use_json)
    vdir = paths.version_dir(home, version)
    if not vdir.exists():
        return _err(f"version directory not found: {vdir}")
    tests_dir = repo / "tests"
    baseline = _load_baseline(home)
    hotfix_ctx = _active_hotfix_trace_context(home, vdir)
    report = check_trace_full_file(vdir, tests_dir, baseline, hotfix_ctx)
    if use_json:
        _print_trace_json(report)
    else:
        for e in report.hard_errors:
            print(e)
        for w in report.warnings:
            print(f"warning: {w}")
        if not report.hard_errors:
            print("trace ok")
    return 1 if report.status == "fail" else 0


def _print_trace_json(report) -> None:
    payload = {
        "status": report.status,
        "hard_errors": list(report.hard_errors),
        "warnings": list(report.warnings),
    }
    if report.status == "pass" and report.hotfix_scope is not None:
        payload["hotfix_scope"] = report.hotfix_scope
    print(json.dumps(payload, ensure_ascii=False))


# -- v0.7 candidate-bound closure exit (IF-CLOSURE-001, interfaces §1i) ----------


def _ac_base_ref(ac) -> str | None:
    """Bare AC id from a possibly cross-version ``AC-FRXXXX-YY@vX.Y`` reference."""
    if not isinstance(ac, str) or not ac.strip():
        return None
    return ac.split("@", 1)[0].strip()


def _merge_baseline_repair(entry: dict, payload: dict) -> None:
    """Fold one ``phase0.baseline_repaired`` payload into an AC evidence entry.

    Interfaces §1a row-1 fields: ``evidence_id`` names the frozen-baseline
    authenticity evidence, ``bound_node.node_id`` is the real collected node
    the gap was repaired with and ``bound_node.digest`` is the candidate
    identity the frozen baseline was repaired under -- the baseline-side
    digest channel the pure join compares against (interfaces §1i).
    """
    entry["baseline_evidence"] = payload.get("evidence_id") or entry.get("baseline_evidence")
    bound_node = payload.get("bound_node") or {}
    node_id = bound_node.get("node_id")
    if isinstance(node_id, str) and node_id:
        nodes = list(entry.get("nodes") or [])
        nodes.append(node_id)
        entry["nodes"] = list(dict.fromkeys(nodes))
    digest = bound_node.get("digest")
    if isinstance(digest, str) and digest:
        entry["baseline_candidate"] = digest


def _merge_mutation_manifest(entry: dict, payload: dict) -> None:
    """Fold one ``mutation.manifest`` payload (§1g field set) into an entry."""
    digest = payload.get("candidate_digest")
    if isinstance(digest, str) and digest:
        entry["mutation_candidate"] = digest
    patch = payload.get("patch_digest")
    if isinstance(patch, str) and patch:
        entry["mutation_evidence"] = patch


def _apply_full_execution(per_ac: dict, acs: list[str], payload: dict) -> None:
    """Credit a suite-level FULL execution to every required AC equally.

    The FULL gate executes the whole suite, so its outcome (real emitted
    ``full.executed`` payload keys: ``serves_as_full_f`` + ``evidence_ids``)
    becomes each required AC's full-pass evidence for this candidate.
    """
    if not payload.get("serves_as_full_f"):
        return
    evidences = sorted(e for e in payload.get("evidence_ids") or [] if isinstance(e, str))
    evidence_id = evidences[0] if evidences else payload.get("outcomes_ref")
    for ac in acs:
        per_ac.setdefault(ac, {})["full_pass_evidence"] = evidence_id


def _merge_stream_event(
    candidate_digest: str | None, per_ac: dict, acs: list[str], event
) -> str | None:
    """Fold one stream event into the harvest state; returns the current digest."""
    payload = event.payload if isinstance(event.payload, dict) else {}
    if event.type == "mutation.manifest":
        base = _ac_base_ref(payload.get("ac")) or ""
        _merge_mutation_manifest(per_ac.setdefault(base, {}), payload)
        digest = payload.get("candidate_digest")
        if isinstance(digest, str) and digest:
            return digest
    elif event.type == "phase0.baseline_repaired":
        base = _ac_base_ref(payload.get("ac")) or ""
        _merge_baseline_repair(per_ac.setdefault(base, {}), payload)
    elif event.type == "full.executed":
        _apply_full_execution(per_ac, acs, payload)
    return candidate_digest


def _closure_evidence(store: Store, version: str, acs: list[str]) -> tuple[str | None, dict]:
    """Harvest per-AC candidate-bound evidence from *version*'s event stream.

    Only contracted payloads feed the join: baseline repair (§3), mutation
    manifest (§1g) and same-suite FULL executions. Absent elements stay
    absent -- the pure join degrades those records to fail; evidence is
    never invented, padded or suppressed.
    """
    candidate_digest: str | None = None
    per_ac: dict[str, dict] = {}
    for run_id in store.runs_for_version(version):
        for event in store.events(run_id):
            candidate_digest = _merge_stream_event(candidate_digest, per_ac, acs, event)
    return candidate_digest, per_ac


def _closure_json(report) -> dict:
    """§1i JSON shape: classic envelope plus closure schema (records ordered)."""
    return {
        "status": report.status,
        "hard_errors": list(report.hard_errors),
        "warnings": [],
        "closure": report.closure,
        "records": [asdict(record) for record in report.records],
    }


def _cmd_check_trace_v07(repo: Path, home: Path, version: str, checker, use_json: bool) -> int:
    """Candidate-bound closure exit slice (IF-CLOSURE-001 / interfaces §1i).

    Approved ACs come from the version's acceptance.md (canonical approval
    artifact): one record per approved AC, degraded to fail when evidence is
    missing -- records are never suppressed. ``checker`` is the same pure
    join ISLAND_GATE_2 uses; this branch only assembles inputs and reports.
    """
    del repo  # evidence lives in .tracks events; kept for CLI signature symmetry
    acs = approved_acs(paths.projects_dir(home), version)
    store = Store(home)
    try:
        candidate_digest, harvested = _closure_evidence(store, version, acs)
    finally:
        store.close()
    report = checker(acs, candidate_digest, {ac: harvested.get(ac, {}) for ac in acs})
    if use_json:
        print(json.dumps(_closure_json(report), ensure_ascii=False))
    else:
        for error in report.hard_errors:
            print(error)
        if not report.hard_errors:
            print("trace ok")
    return 1 if report.status == "fail" else 0


def _active_hotfix_trace_context(home: Path, vdir: Path) -> dict | None:
    """Return release-evidence trace context for the active hotfix, if any."""
    store = Store(home)
    candidates = (store.active_run(), *store.runs_for_version(vdir.name))
    for run_id in candidates:
        if run_id is None or _run_version(store, run_id) != vdir.name:
            continue
        state = store.state(run_id)
        increment = next(
            (
                ev.payload
                for ev in reversed(list(store.events(run_id)))
                if ev.type == "increment.declared" and ev.payload.get("trace_status") == "pass"
            ),
            None,
        )
        if state.hotfix_issue is not None and increment is not None:
            break
    else:
        return None
    unit_rows = increment.get("unit_rows")
    if not isinstance(unit_rows, list):
        plan_path = vdir / "test-plan.md"
        unit_rows = parse_hotfix_unit_rows(
            plan_path.read_text(encoding="utf-8", errors="replace") if plan_path.exists() else ""
        )
    return {
        "projects_dir": str(paths.projects_dir(home)),
        "run_id": run_id,
        "anchor_acs": list(state.hotfix_anchor_acs or []),
        "declared_unit_rows": unit_rows,
    }


def _run_version(store: Store, run_id: str) -> str | None:
    return next((event.version for event in store.events(run_id)), None)


def _cmd_check_reach(repo: Path, rest: list[str]) -> int:
    """Handle `trac check reach [--json] [--entry <module>]...`."""
    parsed = _parse_check_reach_args(rest)
    if parsed is None:
        return _err("usage: trac check reach [--json] [--entry <module>]...")
    use_json, entries = parsed
    home = paths.tracks_home(repo)
    baseline = _load_baseline(home)
    report = check_reach_file(repo, baseline, entries)
    if use_json:
        print(
            json.dumps(
                {
                    "status": report.status,
                    "islands": list(report.islands),
                    "entrypoints": list(report.entrypoints),
                    "errors": list(report.errors),
                    "warnings": list(report.warnings),
                },
                ensure_ascii=False,
            )
        )
    else:
        for e in report.errors:
            print(e, file=sys.stderr)
        for w in report.warnings:
            print(f"warning: {w}")
        for island in report.islands:
            print(f"island module: {island}")
        if not report.islands and not report.errors:
            print("reach ok")
    return 1 if report.status == "fail" else 0


def _cmd_check_release_evidence(repo: Path, rest: list[str]) -> int:
    """Handle `trac check release-evidence [--json]` (IF-RELEASE-001, §2d)."""
    if rest not in ([], ["--json"]):
        print("usage: trac check release-evidence [--json]", file=sys.stderr)
        return 2
    use_json = bool(rest)
    report = check_release_evidence_file(str(repo))
    if use_json:
        print(
            json.dumps(
                {
                    "backend": report.backend,
                    "candidate_sha": report.candidate_sha,
                    "evidence_path": report.evidence_path,
                    "event_bounds": (
                        list(report.event_bounds) if report.event_bounds is not None else None
                    ),
                    "reason_code": report.reason_code,
                    "run_id": report.run_id,
                    "status": report.status,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0 if report.status == "satisfied" else 1
    if report.status == "satisfied":
        print(
            f"release-evidence: satisfied (backend={report.backend}, run {report.run_id}, "
            f"candidate HEAD {report.candidate_sha}, branch {report.branch})"
        )
        return 0
    print(f"release-evidence: NOT satisfied — {report.reason_code}")
    print("  next: rerun the opt-in live journey at current HEAD, then re-check")
    return 1


def cmd_check(repo: Path, *args) -> int:
    # FR-040/FR-130 / AC-1303: `trac check deliverables` - pre-commit/CI gate
    # FR-0080: `trac check trace [--json] [--version <ver>]`
    # FR-0090: `trac check reach [--json] [--entry <module>]...`
    # FR-0232: `trac check release-evidence [--json]`
    args = list(args)
    if not args:
        return _err("usage: trac check <deliverables|trace|reach|release-evidence>")
    sub, rest = args[0], args[1:]
    if sub == "deliverables":
        if rest:
            return _err("usage: trac check deliverables")
        issues = check_deliverables()
        if issues:
            for issue in issues:
                print(issue, file=sys.stderr)
            return 1
        print("deliverables ok")
        return 0
    if sub == "trace":
        return _cmd_check_trace(repo, rest)
    if sub == "reach":
        return _cmd_check_reach(repo, rest)
    if sub == "release-evidence":
        return _cmd_check_release_evidence(repo, rest)
    return _err("usage: trac check <deliverables|trace|reach|release-evidence>")


USAGE = (
    "usage: trac init|start <version>|run [--assignment-overlay PATH] [--max-dispatches N]"
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
