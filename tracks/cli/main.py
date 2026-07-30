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
from datetime import date
from pathlib import Path

from tracks import paths, templating
from tracks.executor import Executor, git
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


def cmd_start(repo: Path, version: str) -> int:
    raw = sys.stdin.read().strip()
    if not raw:
        return _err("empty stdin: pipe the raw requirement into `trac start <version>`")
    if git(repo, "status", "--porcelain", check=False).stdout.strip():
        return _err("working tree dirty (uncommitted changes); commit or stash first")
    home = paths.tracks_home(repo)
    store = Store(home)
    with writer_lock(home):
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


USAGE = (
    "usage: trac init|start <version>|run|triage <decision>"
    "|review <action>|status|replay <run-id>"
)

# command name -> (handler, positional-arg count); handler signature is (repo, *args) -> int
_COMMANDS = {
    "init": (cmd_init, 0),
    "start": (cmd_start, 1),
    "run": (cmd_run, 0),
    "triage": (cmd_triage, 1),
    "review": (cmd_review, 1),
    "status": (cmd_status, 0),
    "replay": (cmd_replay, 1),
}


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        return _err(USAGE)
    cmd, rest = args[0], args[1:]
    entry = _COMMANDS.get(cmd)
    if entry is None or len(rest) != entry[1]:
        return _err(USAGE)
    try:
        return entry[0](Path.cwd(), *rest)
    except LockHeld as e:
        return _err(f"runtime lock held by pid {e.pid}")
    except RuntimeError as e:
        return _err(str(e))


if __name__ == "__main__":
    raise SystemExit(main())
