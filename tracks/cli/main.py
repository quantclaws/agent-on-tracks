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
from tracks.checks.release_evidence import check_release_evidence_file
from tracks.checks.trace import check_trace_full_file
from tracks.deliverables import check_deliverables
from tracks.discuss.cli import run_discuss
from tracks.executor import Executor, git
from tracks.executor.executor import (
    _resolve_run_version,
    hotfix_entry_output,
    hotfix_entry_run,
    hotfix_feature_route,
    hotfix_human_anchor,
)
from tracks.executor.taskgraph import (
    parse_tasks_json,
    validate_ac_coverage,
    validate_dag,
    validate_issue_numbers,
    validate_scope,
)
from tracks.executor.test_tasks import _extract_if_registry, _known_ac_ids
from tracks.executor.validate import (
    check_design_trace_file,
    check_template,
    check_test_tasks_contract_file,
    check_trace_file,
)
from tracks.kernel import Command, project
from tracks.report import generate_report, progress_summary
from tracks.store import Store, new_ulid


class LockHeld(Exception):
    def __init__(self, pid: str):
        super().__init__(pid)
        self.pid = pid


def _err(msg: str) -> int:
    print(msg, file=sys.stderr)
    return 1


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


def _reject_dirty_staged(repo: Path, version: str, label: str) -> int:
    """v0.5 review-A: reject dirty files outside the allowlist and pre-staged
    content before any event is appended. Returns 0 if clean, non-zero on
    rejection (with stderr message)."""
    allowed_prefix = f".tracks/projects/{version}/"
    dirty = [
        line[3:].strip()
        for line in git(repo, "status", "--porcelain", check=False).stdout.splitlines()
        if line.strip()
    ]
    outside = [f for f in dirty if not f.startswith(allowed_prefix)]
    if outside:
        return _err(
            f"{label} touches files outside {allowed_prefix}: " + ", ".join(sorted(outside))
        )
    staged = [
        line[3:].strip()
        for line in git(repo, "diff", "--cached", "--name-only", check=False).stdout.splitlines()
        if line.strip()
    ]
    if staged:
        return _err("pre-staged content found; unstage first: " + ", ".join(sorted(staged)))
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
        latest = store.latest_run()
        if latest is not None:
            state = store.state(latest)
            if state.status == "completed":
                print(f"run {latest}: {_format_state(state)}")
                return 0
        return _err("no active run; `trac start <version>` first")
    with writer_lock(home):
        state = Executor(
            store,
            repo,
            run_id,
            assignment_overlay=assignment_overlay,
            max_dispatches=max_dispatches,
        ).run_loop()
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
        run_id = store.active_run()
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
    if git(repo, "status", "--porcelain", check=False).stdout.strip():
        return _err("working tree dirty (uncommitted changes); commit or stash first")
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
    early dirty-tree rejection."""
    rc = _reject_dirty_staged(repo, state.version, label)
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
        if clear_evidence:
            # Operator signals the underlying program/config/validator was
            # fixed and the old failure evidence is stale. Allowed at escalation
            # (same as ordinary retry) OR at a clean active state (the recovery
            # path: ordinary retry already happened, a dispatch was killed, and
            # stale evidence must not leak into the next dispatch). Other
            # awaiting gates (review/approval/triage/rollback) are NOT relaxed.
            if state.awaiting != "escalation" and not (
                state.status == "active" and state.awaiting is None
            ):
                return _err(
                    "retry --clear-evidence requires escalation or a clean "
                    f"active state (status={state.status} "
                    f"awaiting={state.awaiting or 'nothing'})"
                )
        elif state.awaiting != "escalation":
            # Ordinary retry: only at escalation, preserves evidence (FR-11).
            return _err(f"run not awaiting escalation (awaiting={state.awaiting or 'nothing'})")
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
    actor = actor or git(repo, "config", "user.name", check=False).stdout.strip() or "human"
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
        # FR-0248 (IF-HOTFIX-009): hotfix ac_gap/spec_gap Human decision ->
        # human.approval evidence -> backlog.recorded -> run.completed terminal.
        if _hotfix_gap_exit(state):
            return _approve_hotfix_gap(repo, store, run_id, state, actor)
        # SM-01.13: M-TEST / M-IMPL rollback approval needs no digest check
        if state.awaiting == "rollback" and state.stage in ("M-TEST", "M-IMPL"):
            actor = actor or git(repo, "config", "user.name", check=False).stdout.strip() or "human"
            store.append(
                run_id,
                state.version,
                "human.approval",
                {"actor": actor, "digest": None, "ts": datetime.now(timezone.utc).isoformat()},
            )
            print(f"approved rollback to {state.return_target}")
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
        if actor is None:
            actor = git(repo, "config", "user.name", check=False).stdout.strip() or "human"
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
    if state.awaiting == "escalation":
        allowed = _escalation_return_targets(state.stage)
        if allowed:
            return state, allowed, None
        return state, (), (f"escalation at stage {state.stage} has no return targets")
    return (
        state,
        (),
        (
            f"run not awaiting approval or escalation (stage={state.stage} "
            f"awaiting={state.awaiting or 'nothing'})"
        ),
    )


def cmd_return(repo: Path, *args) -> int:
    opts = {}
    args = list(args)
    usage = "usage: trac return --to <M-STORY|M-SPEC|M-ACC|M-DESIGN> --reason TEXT"
    while args:
        flag = args.pop(0)
        if flag not in ("--to", "--reason") or not args or flag in opts:
            return _err(usage)
        opts[flag] = args.pop(0)
    if set(opts) != {"--to", "--reason"}:
        return _err(usage)
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
            # SM-05.7a/C-03: closed per-gate set, explicitly validated — no
            # event on reject.
            return _err(f"invalid --to {opts['--to']}: must be one of " + "|".join(allowed))
        store.append(
            run_id,
            state.version,
            "human.return",
            {"reason": opts["--reason"], "to_stage": opts["--to"]},
        )
    print(f"returned to {opts['--to']}")
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
    print(_status_line(rows[0][0], store.state(rows[0][0])))
    # interfaces §2b (IF-HOTFIX-006): one `suspended:` line per remaining
    # non-completed run (projection-rebuildable; `trac replay` reads it back).
    for (run_id,) in rows[1:]:
        sub = store.state(run_id)
        if sub.status == "completed":
            continue
        print(
            f"suspended: run={run_id} stage={sub.stage} substate={sub.substate} "
            f"branch={_status_branch(sub)}"
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
    store = Store(home)
    try:
        if run_id is None or run_id == "latest":
            run_id = store.latest_run()
            if run_id is None:
                return _err("no runs available for report")
        events = list(store.events(run_id))
    finally:
        store.close()
    if not events:
        return _err(f"unknown run: {run_id}")
    try:
        report_md, index_html = generate_report(repo, run_id, output or home / "report")
    except (RuntimeError, ValueError) as exc:
        return _err(str(exc))
    selected = report_md if report_format == "md" else index_html
    print(f"report: {selected}")
    print(f"html: {index_html}")
    summary = progress_summary(events)
    if summary:
        print(summary)
    return 0


def cmd_validate(repo: Path, *args) -> int:
    # FR-150 / AC-1501: `trac validate --file <path>` — standalone template
    # check (also reused by the outcome / exit-gate validation). No lock/state:
    # it is a pure read of the given file against its kind template.
    if len(args) != 2 or args[0] != "--file":
        return _err("usage: trac validate --file <path>")
    path = Path(args[1])
    if path.name == "tasks.json":  # FR-0180: five structural checks (not template)
        issues = _validate_tasksjson(path)
    else:
        issues = check_template(path)
        if path.name == "acceptance.md":  # FR-0170: trace auto-runs for acceptance
            issues += check_trace_file(path)
        if path.name == "test-plan.md":  # BS-06: design trace (AC -> test layer)
            issues += check_design_trace_file(path)
            issues += check_test_tasks_contract_file(path)  # FR-0140
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
    vdir = paths.version_dir(home, version)
    if not vdir.exists():
        return _err(f"version directory not found: {vdir}")
    tests_dir = repo / "tests"
    baseline = _load_baseline(home)
    report = check_trace_full_file(vdir, tests_dir, baseline)
    if use_json:
        print(
            json.dumps(
                {
                    "status": report.status,
                    "hard_errors": list(report.hard_errors),
                    "warnings": list(report.warnings),
                },
                ensure_ascii=False,
            )
        )
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
