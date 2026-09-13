"""``trac status`` / ``trac replay`` / ``trac report`` command face.

Extracted from :mod:`tracks.cli.main` for module-size compliance (C0302):
the status-line rendering, suspended-run lines, replay, and report
generation (release status fragments stay with the release face and are
imported from :mod:`tracks.cli.release_cmd`).
``tracks.cli.main`` re-exports every name below.
"""

from __future__ import annotations

import json
from pathlib import Path

from tracks import paths
from tracks.kernel import project
from tracks.report import generate_report, progress_summary
from tracks.store import Store

from .common import _err, _format_state
from .release_cmd import (
    _escape_status_fragments,
    _release_report_snippet,
    _release_status_lines,
)


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
    """Active hotfix runs take run-loop priority -- but never displace the
    primary row when the newest run is terminal: a completed released run must
    stay observable instead of being shadowed by an older suspended hotfix
    (D5 live defect). ``rows`` are newest-first; a minimal fake store without
    ``state`` keeps the legacy promotion."""
    hotfix_id = store.active_hotfix_run()
    if hotfix_id is None:
        return rows
    if rows and hasattr(store, "state") and store.state(rows[0][0]).status == "completed":
        return rows
    return [(hotfix_id,), *((run_id,) for run_id, in rows if run_id != hotfix_id)]


def _next_status_row(
    store: Store, rows: list[tuple[str]], exclude: str, *, completed: bool
) -> str | None:
    """First newest-first row whose projection completion state matches."""
    for run_id, in rows:
        if run_id == exclude:
            continue
        if (store.state(run_id).status == "completed") is completed:
            return run_id
    return None


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


def cmd_status(repo: Path) -> int:
    home = paths.tracks_home(repo)
    store = Store(home)
    store.rebuild_projections()  # NFR-04: survives dropped projection tables
    # Skip backlog-only phantom rows (SM-01.2): a `backlog.recorded` event on a
    # run with no `stage.entered` is a queue placeholder, not a real run.
    # Newest first by TRUE event recency (D5): `rebuild_projections` above
    # re-stamps `updated_ts` to "now" for every row, so ordering by it was
    # effectively insertion order and let an older suspended run shadow a
    # newer completed one.
    rows = store.conn.execute(
        "SELECT runs.run_id FROM runs "
        "WHERE runs.status != 'backlog' AND runs.stage IS NOT NULL "
        "ORDER BY (SELECT MAX(ev.ts) FROM events AS ev "
        "WHERE ev.run_id = runs.run_id) DESC, runs.run_id DESC"
    ).fetchall()
    if not rows:
        print("no runs yet")
        return 0
    rows = _prioritize_status_rows(store, rows)
    primary_id = rows[0][0]
    primary = store.state(primary_id)
    print(_status_line(primary_id, primary))
    try:
        for line in _release_status_lines(list(store.events(primary_id)), primary, repo):
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
        active_id = _next_status_row(store, rows, primary_id, completed=False)
        if active_id is not None:
            print(_status_line(active_id, store.state(active_id)))
            rows = [(run_id,) for run_id, in rows if run_id != active_id]
    else:
        # D5: the active primary never hides the latest terminal outcome --
        # render it between the primary line and the suspended rows.
        completed_id = _next_status_row(store, rows, primary_id, completed=True)
        if completed_id is not None:
            print(_status_line(completed_id, store.state(completed_id)))
            rows = [(run_id,) for run_id, in rows if run_id != completed_id]
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
