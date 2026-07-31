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
from tracks.deliverables import check_deliverables
from tracks.discuss.cli import run_discuss
from tracks.executor import Executor, git
from tracks.executor.validate import check_template, check_trace_file
from tracks.kernel import Command, project
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


def cmd_init(repo: Path) -> int:
    home = paths.tracks_home(repo)
    for d in (paths.projects_dir(home), paths.runtime_dir(home), paths.wiki_dir(home)):
        d.mkdir(parents=True, exist_ok=True)
    gitignore = paths.runtime_dir(home) / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(RUNTIME_GITIGNORE, encoding="utf-8")
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


def cmd_run(repo: Path) -> int:
    home = paths.tracks_home(repo)
    store = Store(home)
    run_id = store.active_run()
    if run_id is None:
        return _err("no active run; `trac start <version>` first")
    with writer_lock(home):
        state = Executor(store, repo, run_id).run_loop()
    print(
        f"run {run_id}: stage={state.stage} substate={state.substate} "
        f"status={state.status} awaiting={state.awaiting or '-'}"
    )
    return 0


def cmd_triage(repo: Path, decision: str) -> int:
    decision = decision.replace("-", "_")
    if decision not in ("go", "no_go", "park"):
        return _err("usage: trac triage go|no-go|park")
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
        store.append(run_id, state.version, "human.triage", {"decision": decision})
    print(f"triage recorded: {decision}")
    return 0


def cmd_review(repo: Path, action: str) -> int:
    action = action.replace("-", "_")
    if action not in ("no_comment", "revise"):
        return _err("usage: trac review no-comment|revise")
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
            payload = {"action": "no_comment"}
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
            payload = {"action": "comment", "diff_ref": diff_ref}
        store.append(run_id, state.version, "human.review", payload)
    print(f"review recorded: {action}")
    return 0


def _approval_gate(store: Store, run_id: str):
    """FR-0180: approve/return are only legal at the AWAIT_HUMAN gate."""
    state = store.state(run_id)
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
    row = store.conn.execute(
        "SELECT run_id FROM runs ORDER BY updated_ts DESC LIMIT 1"
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


def cmd_check(repo: Path, *args) -> int:
    # FR-040/FR-130 / AC-1303: `trac check deliverables` — pre-commit/CI gate
    # (not Runtime): deliverable existence + frontmatter version. Non-zero on fail.
    if args != ("deliverables",):
        return _err("usage: trac check deliverables")
    issues = check_deliverables()
    if issues:
        for issue in issues:
            print(issue, file=sys.stderr)
        return 1
    print("deliverables ok")
    return 0


USAGE = (
    "usage: trac init|start <version>|run|triage <decision>"
    "|review <action>|approve [--actor NAME]|return --to <stage> --reason TEXT"
    "|status|replay <run-id>|validate --file <path>"
    "|discuss <query|start|reply|edit|set-status> ...|check deliverables"
)

# command name -> (handler, positional-arg count or None=variadic); handler is (repo, *args) -> int
_COMMANDS = {
    "init": (cmd_init, 0),
    "start": (cmd_start, None),
    "run": (cmd_run, 0),
    "triage": (cmd_triage, 1),
    "review": (cmd_review, 1),
    "approve": (cmd_approve, None),
    "return": (cmd_return, None),
    "status": (cmd_status, 0),
    "replay": (cmd_replay, 1),
    "validate": (cmd_validate, 2),
    "discuss": (cmd_discuss, None),
    "check": (cmd_check, 1),
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
