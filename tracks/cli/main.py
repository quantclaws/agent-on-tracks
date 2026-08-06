"""`trac` CLI (FR-01..FR-30 surface) — thin shell over store/kernel/executor.

Single-writer discipline (D-07, FR-27): every mutating subcommand holds
`runtime/lock` (O_CREAT|O_EXCL, holder PID inside). A held lock aborts with
the holder PID on stderr and writes no events.
"""
from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

from tracks import paths, templating
from tracks.baseline import baseline_summary, revision_digest
from tracks.checks.reach import check_reach_file
from tracks.checks.trace import check_trace_full_file
from tracks.deliverables import check_deliverables
from tracks.discuss.cli import run_discuss
from tracks.executor import Executor, git
from tracks.executor.validate import (
    check_design_trace_file,
    check_template,
    check_trace_file,
)
from tracks.kernel import Command, project
from tracks.report import generate_report
from tracks.store import Store, new_ulid


class LockHeld(Exception):
    def __init__(self, pid: str):
        super().__init__(pid)
        self.pid = pid


def _err(msg: str) -> int:
    print(msg, file=sys.stderr)
    return 1


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


RUNTIME_GITIGNORE = "tracks.db*\nblobs/\nlock\n"
PROJECTS_GITIGNORE = "*.lock\n*.tmp\n"


def cmd_init(repo: Path) -> int:
    home = paths.tracks_home(repo)
    for d in (paths.projects_dir(home), paths.runtime_dir(home),
              paths.project_dir(home), paths.wiki_dir(home)):
        d.mkdir(parents=True, exist_ok=True)
    gitignore = paths.runtime_dir(home) / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(RUNTIME_GITIGNORE, encoding="utf-8")
    # Runtime-owned transient files under projects/ (the discuss writer's flock
    # lock files ``<doc>.md.lock`` and tmp-before-rename ``<doc>.md.tmp``) are
    # intentionally left in place - deleting would break flock serialization -
    # so they must be gitignored in every stage or they pollute host git status.
    # Idempotent: write only if absent (mirrors the runtime gitignore above).
    projects_gitignore = paths.projects_dir(home) / ".gitignore"
    if not projects_gitignore.exists():
        projects_gitignore.write_text(PROJECTS_GITIGNORE, encoding="utf-8")
    for d in (paths.projects_dir(home), paths.wiki_dir(home)):
        keep = d / ".gitkeep"
        if not keep.exists():
            keep.write_text("", encoding="utf-8")
    # AC-01b/c: idempotent, no empty commit, leaves the worktree clean.
    git(repo, "add", str(home))
    if git(repo, "diff", "--cached", "--quiet", check=False).returncode != 0:
        git(repo, "commit", "-m", "trac init: scaffold .tracks")
    print(f"initialized {home}")
    return 0


def cmd_start(repo: Path, *args: str) -> int:
    version, confirm = _parse_start_args(args)
    raw = sys.stdin.read().strip()
    if not raw:
        return _err("empty stdin: pipe the raw requirement into `trac start <version>`")
    if git(repo, "status", "--porcelain", check=False).stdout.strip():
        return _err("working tree dirty (uncommitted changes); commit or stash first")
    home = paths.tracks_home(repo)
    store = Store(home)
    with writer_lock(home):
        # SM-01.2: an active run exists → record the requirement into the
        # backlog and exit WITHOUT creating a branch (normative: flow.md §3.1).
        active = store.active_run()
        if active is not None:
            bid = new_ulid()
            store.append(
                bid, version, "backlog.recorded",
                {"version": version, "decision": "queued", "reason": "active_run"},
            )
            print(
                f"active run {active}; requirement queued to backlog (run not started)"
            )
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
        git(repo, "commit", "-m", f"M-START: capture raw requirement for {version}")
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
        raise SystemExit(
            "usage: trac start <version> [--confirm|--cancel]"
        )
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


def _parse_action_actor(
    args: tuple[str, ...], usage: str
) -> tuple[str, str | None] | None:
    if not args or len(args) > 3 or (len(args) == 3 and args[1] != "--actor"):
        _err(usage)
        return None
    return args[0].replace("-", "_"), args[2] if len(args) == 3 else None


def _parse_run_args(args: tuple[str, ...]) -> tuple[str | None, int | None]:
    if len(args) % 2:
        raise ValueError(
            "usage: trac run [--assignment-overlay PATH] [--max-dispatches N]"
        )
    values = {}
    for flag, value in zip(args[::2], args[1::2], strict=True):
        if flag not in ("--assignment-overlay", "--max-dispatches"):
            raise ValueError(
                "usage: trac run [--assignment-overlay PATH] [--max-dispatches N]"
            )
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
    run_id = store.active_run()
    if run_id is None:
        return _err("no active run; `trac start <version>` first")
    with writer_lock(home):
        state = Executor(
            store,
            repo,
            run_id,
            assignment_overlay=assignment_overlay,
            max_dispatches=max_dispatches,
        ).run_loop()
    print(
        f"run {run_id}: stage={state.stage} substate={state.substate} "
        f"status={state.status} awaiting={state.awaiting or '-'}"
    )
    return 0


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
    # AC-27b: lock precedes the state gate — a held lock aborts with holder
    # PID before any state read, and no event is appended.
    with writer_lock(home):
        state = store.state(run_id)
        if state.awaiting != "triage":
            return _err(f"run not awaiting triage (awaiting={state.awaiting or 'nothing'})")
        store.append(
            run_id, state.version, "human.triage",
            {"decision": decision, "actor": actor or _human_actor(repo)},
        )
    print(f"triage recorded: {decision}")
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
    with writer_lock(home):  # AC-27b: lock before the state gate
        state = store.state(run_id)
        if state.awaiting != "review":
            return _err(f"run not awaiting review (awaiting={state.awaiting or 'nothing'})")
        if action == "no_comment":
            payload = {"action": "no_comment", "actor": actor or _human_actor(repo)}
        else:
            allowed = f".tracks/projects/{state.version}/"
            dirty = [
                line[3:].strip()
                for line in git(repo, "status", "--porcelain",
                                check=False).stdout.splitlines()
                if line.strip()
            ]
            outside = [f for f in dirty if not f.startswith(allowed)]
            if outside:
                # AC-16b: reject before any event is appended; state unchanged.
                return _err(f"revise touches files outside {allowed}: "
                            + ", ".join(sorted(outside)))
            diff_ref = None
            if dirty:
                git(repo, "add", "--", *dirty)
                git(repo, "commit", "-m",
                    f"HUMAN_REVIEW: human revise ({state.stage})")
                diff_ref = git(repo, "rev-parse", "HEAD").stdout.strip()
            payload = {"action": "comment", "diff_ref": diff_ref,
                       "actor": actor or _human_actor(repo)}
        store.append(run_id, state.version, "human.review", payload)
    print(f"review recorded: {action}")
    return 0


def _approval_gate(store: Store, run_id: str):
    """FR-0180: approve/return are only legal at a Human gate.

    Two gates accept ``approve``: M-REQ-APPROVAL (awaiting=approval) and
    M-TEST DIAGNOSE ac_gap/spec_gap rollback (awaiting=rollback, SM-01.13)."""
    state = store.state(run_id)
    if state.awaiting == "rollback" and state.stage == "M-TEST":
        return state, None  # SM-01.13: Human approves the rollback
    if state.stage != "M-REQ-APPROVAL" or state.awaiting != "approval":
        return None, (f"run not awaiting approval (stage={state.stage} "
                      f"awaiting={state.awaiting or 'nothing'})")
    return state, None


def cmd_approve(repo: Path, *args) -> int:
    args = list(args)
    if args and (args[0] != "--actor" or len(args) != 2):
        return _err("usage: trac approve [--actor NAME]")
    actor = args[1] if args else None
    home = paths.tracks_home(repo)
    store = Store(home)
    run_id = store.active_run()
    if run_id is None:
        return _err("no active run")
    with writer_lock(home):  # AC-27b: lock before the state gate
        state, err = _approval_gate(store, run_id)
        if err:
            return _err(err)
        # SM-01.13: M-TEST rollback approval needs no digest check
        if state.stage == "M-TEST" and state.awaiting == "rollback":
            actor = actor or git(repo, "config", "user.name",
                                 check=False).stdout.strip() or "human"
            store.append(run_id, state.version, "human.approval",
                         {"actor": actor, "digest": None,
                          "ts": datetime.now(timezone.utc).isoformat()})
            print(f"approved rollback to {state.return_target}")
            return 0
        vdir = paths.version_dir(home, state.version)
        digest = revision_digest(vdir)
        previews = [e for e in store.events(run_id)
                    if e.type == "preview.generated"]
        if not previews or previews[-1].payload["digest"] != digest:
            # C-02: the trio changed under the reviewed preview — reject THIS
            # approve (not the run) and regenerate the preview for re-review.
            store.append(run_id, state.version, "preview.generated",
                         {"digest": digest, "summary": baseline_summary(vdir)})
            return _err("baseline changed since preview: approve rejected, "
                        "preview regenerated — review and approve again")
        if actor is None:
            actor = git(repo, "config", "user.name",
                        check=False).stdout.strip() or "human"
        store.append(run_id, state.version, "human.approval",
                     {"actor": actor, "digest": digest,
                      "ts": datetime.now(timezone.utc).isoformat()})
    print(f"approved {digest}")
    return 0


_RETURN_STAGES = ("M-STORY", "M-SPEC", "M-ACC")


def cmd_return(repo: Path, *args) -> int:
    opts = {}
    args = list(args)
    while args:
        flag = args.pop(0)
        if flag not in ("--to", "--reason") or not args or flag in opts:
            return _err("usage: trac return --to <M-STORY|M-SPEC|M-ACC> --reason TEXT")
        opts[flag] = args.pop(0)
    if set(opts) != {"--to", "--reason"}:
        return _err("usage: trac return --to <M-STORY|M-SPEC|M-ACC> --reason TEXT")
    if opts["--to"] not in _RETURN_STAGES:
        # SM-05.7/C-03: closed set, explicitly validated — no event on reject.
        return _err(f"invalid --to {opts['--to']}: must be one of "
                    + "|".join(_RETURN_STAGES))
    home = paths.tracks_home(repo)
    store = Store(home)
    run_id = store.active_run()
    if run_id is None:
        return _err("no active run")
    with writer_lock(home):  # AC-27b: lock before the state gate
        state, err = _approval_gate(store, run_id)
        if err:
            return _err(err)
        store.append(run_id, state.version, "human.return",
                     {"reason": opts["--reason"], "to_stage": opts["--to"]})
    print(f"returned to {opts['--to']}")
    return 0


def cmd_status(repo: Path) -> int:
    home = paths.tracks_home(repo)
    store = Store(home)
    store.rebuild_projections()  # NFR-04: survives dropped projection tables
    # Skip backlog-only phantom rows (SM-01.2): a `backlog.recorded` event on a
    # run with no `stage.entered` is a queue placeholder, not a real run. The
    # `stage IS NULL` filter is robust to stale projection rows (it derives from
    # the absence of stage.entered events, not from status); `status != 'backlog'`
    # catches the post-rebuild case where the reducer has marked the phantom.
    row = store.conn.execute(
        "SELECT run_id FROM runs "
        "WHERE status != 'backlog' AND stage IS NOT NULL "
        "ORDER BY updated_ts DESC LIMIT 1"
    ).fetchone()
    if row is None:
        print("no runs yet")
        return 0
    s = store.state(row[0])
    if s.status == "completed":
        print(f"run {row[0]}: completed terminal={s.terminal_state} stage={s.stage}")
    else:
        print(
            f"run {row[0]}: stage={s.stage} substate={s.substate} "
            f"status={s.status} awaiting={s.awaiting or '-'}"
        )
    return 0


def cmd_replay(repo: Path, run_id: str) -> int:
    home = paths.tracks_home(repo)
    store = Store(home)
    events = list(store.events(run_id))
    if not events:
        return _err(f"unknown run: {run_id}")
    for ev in events:
        payload = json.dumps(ev.payload, ensure_ascii=False, sort_keys=True)
        print(f"{ev.seq}\t{ev.ts}\t{ev.type}\t{payload}")
    s = project(events)
    print(
        f"final: stage={s.stage} substate={s.substate} "
        f"status={s.status} awaiting={s.awaiting or '-'}"
    )
    return 0


_REPORT_USAGE = (
    "usage: trac report --run-id ID --output DIR [--format md|html]"
)


def _parse_report_args(args: tuple[str, ...]) -> tuple[str, str, str] | None:
    run_id = None
    output = None
    report_format = "md"
    index = 0
    while index < len(args):
        flag = args[index]
        if flag not in ("--run-id", "--output", "--format") or index + 1 >= len(args):
            return None
        value = args[index + 1]
        if flag == "--run-id":
            run_id = value
        elif flag == "--output":
            output = value
        elif flag == "--format":
            report_format = value
        index += 2
    if not run_id or not output or report_format not in ("md", "html"):
        return None
    return run_id, output, report_format


def cmd_report(repo: Path, *args: str) -> int:
    """Generate a read-only Markdown/HTML report for one Runtime run."""
    parsed = _parse_report_args(args)
    if parsed is None:
        return _err(_REPORT_USAGE)
    run_id, output, report_format = parsed
    try:
        report_md, index_html = generate_report(repo, run_id, output)
    except (RuntimeError, ValueError) as exc:
        return _err(str(exc))
    selected = report_md if report_format == "md" else index_html
    print(f"report: {selected}")
    print(f"html: {index_html}")
    return 0


def cmd_validate(repo: Path, *args) -> int:
    # FR-150 / AC-1501: `trac validate --file <path>` — standalone template
    # check (also reused by the outcome / exit-gate validation). No lock/state:
    # it is a pure read of the given file against its kind template.
    if len(args) != 2 or args[0] != "--file":
        return _err("usage: trac validate --file <path>")
    path = Path(args[1])
    issues = check_template(path)
    if path.name == "acceptance.md":  # FR-0170: trace auto-runs for acceptance
        issues += check_trace_file(path)
    if path.name == "test-plan.md":  # BS-06: design trace (AC -> test layer)
        issues += check_design_trace_file(path)
    if issues:
        for issue in issues:
            print(issue, file=sys.stderr)
        return 1
    print("valid")
    return 0


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
    versions = sorted(
        d.name for d in projects.iterdir() if d.is_dir() and d.name.startswith("v")
    )
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
    vdir = paths.version_dir(home, version)
    if not vdir.exists():
        return _err(f"version directory not found: {vdir}")
    tests_dir = repo / "tests"
    baseline = _load_baseline(home)
    report = check_trace_full_file(vdir, tests_dir, baseline)
    if use_json:
        print(json.dumps({
            "status": report.status,
            "hard_errors": list(report.hard_errors),
            "warnings": list(report.warnings),
        }, ensure_ascii=False))
    else:
        for e in report.hard_errors:
            print(e)
        for w in report.warnings:
            print(f"warning: {w}")
        if not report.hard_errors:
            print("trace ok")
    return 1 if report.status == "fail" else 0


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
        print(json.dumps({
            "status": report.status,
            "islands": list(report.islands),
            "entrypoints": list(report.entrypoints),
            "errors": list(report.errors),
            "warnings": list(report.warnings),
        }, ensure_ascii=False))
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


def cmd_check(repo: Path, *args) -> int:
    # FR-040/FR-130 / AC-1303: `trac check deliverables` - pre-commit/CI gate
    # FR-0080: `trac check trace [--json] [--version <ver>]`
    # FR-0090: `trac check reach [--json] [--entry <module>]...`
    args = list(args)
    if not args:
        return _err("usage: trac check <deliverables|trace|reach>")
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
    return _err("usage: trac check <deliverables|trace|reach>")


USAGE = (
    "usage: trac init|start <version>|run [--assignment-overlay PATH] [--max-dispatches N]"
    "|triage <decision> [--actor NAME]"
    "|review <action> [--actor NAME]|approve [--actor NAME]|return --to <stage> --reason TEXT"
    "|status|replay <run-id>|validate --file <path>"
    "|report --run-id <run-id> --output <dir> [--format md|html]"
    "|discuss <query|start|reply|edit|set-status> ...|check <deliverables|trace|reach>"
)

# command name -> (handler, positional-arg count or None=variadic); handler is (repo, *args) -> int
_COMMANDS = {
    "init": (cmd_init, 0),
    "start": (cmd_start, None),
    "run": (cmd_run, None),
    "triage": (cmd_triage, None),
    "review": (cmd_review, None),
    "approve": (cmd_approve, None),
    "return": (cmd_return, None),
    "status": (cmd_status, 0),
    "replay": (cmd_replay, 1),
    "report": (cmd_report, None),
    "validate": (cmd_validate, 2),
    "discuss": (cmd_discuss, None),
    "check": (cmd_check, None),
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
