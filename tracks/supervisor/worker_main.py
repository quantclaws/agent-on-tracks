"""Worker subprocess entry point (interfaces §1i, §1d).

Runs one claimed command in isolation: for ``drive_run`` it loops
``executor.drive.drive_once`` until a boundary (await_human /
await_external / terminal / failed) and returns the structured outcome to
the supervisor; for gate commands it executes the shared gate semantics.
The worker only executes commands already claimed under the run lease and
commits through the fenced generation CAS (§1i) — run-level serialization
stays arbitrated by the supervisor, never by a second in-worker writer; it
never talks to HTTP clients.

The service home is resolved from ``TRAC_SERVE_HOME`` (interfaces §2a); the
command row and the project repo it references come from the real service.db
so a restart-safe, fenced outcome is committed through the generation CAS
(§1h) — a stale worker quarantines instead of acting.

Contract token: IF-DRIVE-001.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from tracks import paths
from tracks.executor.drive import DriveConfig, DriveResult, drive_once
from tracks.supervisor import gate_exec
from tracks.supervisor.db import ServiceDB
from tracks.supervisor.waiting import WaitPolicy, classify_wait, enter_wait

_USAGE = "usage: python -m tracks.supervisor.worker_main --command-id <id>"
_MAX_DRIVE_STEPS = 1000
_DRIVE_KIND = "drive_run"


def main(argv: list[str] | None = None) -> int:
    """Entry: python -m tracks.supervisor.worker_main --command-id <id>."""
    command_id, usage_error = _parse_args(list(sys.argv[1:] if argv is None else argv))
    if usage_error is not None:
        print(usage_error, file=sys.stderr)
        return 2
    home = os.environ.get("TRAC_SERVE_HOME")
    if not home:
        print("worker: TRAC_SERVE_HOME is required to locate service.db", file=sys.stderr)
        return 1
    db = ServiceDB(Path(home))
    row = db.get_command(command_id)
    if row is None:
        print(f"worker: unknown command {command_id!r}", file=sys.stderr)
        return 1
    generation = int(row.get("claim_generation") or 0)
    result, failure = _execute(db, row)
    if not db.complete_command(command_id, generation, result, failure):
        # Fenced CAS rejected the outcome (a newer generation owns the run):
        # the store quarantines it with worker.late_result so a stale worker's
        # result can never drive anything.
        return 1
    return 1 if failure is not None else 0


def _parse_args(args: list[str]) -> tuple[str | None, str | None]:
    """--command-id <id> only; anything else is a usage error."""
    command_id: str | None = None
    index = 0
    while index < len(args):
        if args[index] == "--command-id" and index + 1 < len(args):
            command_id = args[index + 1]
            index += 2
            continue
        return None, _USAGE
    if not command_id:
        return None, _USAGE
    return command_id, None


def _params(row: dict) -> dict:
    raw = row.get("params_json")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _run_id_of(row: dict) -> str:
    return str(row.get("run_id") or _params(row).get("run_id") or "")


def _project_repo(db: Any, project_id: Any) -> Path | None:
    """Repo path of the registered project (interfaces §1c table 2)."""
    if not project_id:
        return None
    connect = getattr(db, "_connect", None)
    if not callable(connect):
        return None
    with contextlib.closing(connect()) as conn:
        row = conn.execute(
            "SELECT repo_path FROM projects WHERE project_id = ?", (project_id,)
        ).fetchone()
    if row is None:
        return None
    repo = Path(str(row[0]))
    return repo if repo.is_dir() else None


def _resolve_run_repo(db: Any, row: dict) -> tuple[str, Path | None]:
    """Resolve (run_id, repo) the same way for every claimable kind: the
    command row first, then the service plane's run→project discovery."""
    run_id = _run_id_of(row)
    project_id = row.get("project_id")
    if not project_id:
        finder = getattr(db, "find_run_project_id", None)
        project_id = finder(run_id) if callable(finder) else None
    return run_id, _project_repo(db, project_id)


def _execute_gate(db: Any, row: dict) -> tuple[dict | None, dict | None]:
    """v0.11 §1j.3 (IF-GATEUI-001): a claimed human gate command executes
    through the shared gate face (``gate_exec``), never the drive loop."""
    run_id, repo = _resolve_run_repo(db, row)
    if not run_id or repo is None:
        return None, _unrecoverable(
            f"cannot resolve run/project for command {row.get('command_id')!r}"
        )
    return gate_exec.execute_gate_command(
        repo,
        run_id,
        str(row.get("kind") or ""),
        _params(row),
        str(row.get("actor") or "human"),
    )


def _execute(db: Any, row: dict) -> tuple[dict | None, dict | None]:
    """Run the claimed command; returns ``(result, failure)`` — exactly one
    is non-None and ``failure`` carries the closed failure_class vocabulary."""
    kind = row.get("kind")
    if kind in gate_exec.GATE_KINDS:
        return _execute_gate(db, row)
    if kind != _DRIVE_KIND:
        return None, _unrecoverable(
            f"worker does not handle command kind {row.get('kind')!r} yet"
        )
    run_id, repo = _resolve_run_repo(db, row)
    # 2026-09-23 (island-2 sweep, e2e web journey): a supervisor-accepted
    # internal drive_run carries no project_id of its own -- resolve the
    # project THROUGH the run with the same discovery order the service
    # plane uses (commands row, then service events), instead of failing
    # unrecoverable on the first drive of every fresh run.
    if not run_id or repo is None:
        return None, _unrecoverable(
            f"cannot resolve run/project for command {row.get('command_id')!r}"
        )
    _bootstrap_run_plane(db, repo, run_id)
    config = DriveConfig()
    result = _drive_to_boundary(repo, run_id, config)
    if result.kind == "failed":
        failure = dict(result.failure or {})
        _persist_wait_if_recoverable(db, run_id, failure, config)
        return None, failure
    if result.kind == "await_external" and result.wait is not None:
        enter_wait(db, run_id, result.wait)
    return asdict(result), None


def _drive_to_boundary(repo: Path, run_id: str, config: DriveConfig) -> DriveResult:
    """Loop drive_once until a durable boundary (never a busy loop)."""
    result = drive_once(repo, run_id, config=config)
    steps = 1
    while result.kind == "continue" and steps < _MAX_DRIVE_STEPS:
        result = drive_once(repo, run_id, config=config)
        steps += 1
    if result.kind != "continue":
        return result
    return DriveResult(
        kind="failed",
        run_id=run_id,
        stage=result.stage,
        wait=None,
        failure=_unrecoverable(
            "drive step budget exhausted without reaching a durable boundary"
        ),
        detail=f"run {run_id}: drive step budget exhausted at {result.stage or '?'}",
    )


def _wait_policy(config: DriveConfig) -> WaitPolicy:
    """NFR-0152 backoff policy carried by the drive config (serve flags)."""
    return WaitPolicy(initial_s=config.wait_initial_s, cap_s=config.wait_cap_s)


def _persist_wait_if_recoverable(
    db: Any, run_id: str, failure: dict, config: DriveConfig
) -> None:
    """Turn a recoverable external failure into the durable wait registry
    (§1j) so the supervisor wakes exactly when the condition clears."""
    if failure.get("failure_class") != "recoverable_external":
        return
    spec = classify_wait(failure, policy=_wait_policy(config))
    if spec is not None:
        enter_wait(db, run_id, spec)


def _unrecoverable(reason: str) -> dict:
    return {"failure_class": "unrecoverable", "reason": reason}




def _create_run_params(db: Any, run_id: str) -> dict:
    """The service plane's create_run params for this run (version/story)."""
    import contextlib
    import json as _json

    connect = getattr(db, "_connect", None)
    if not callable(connect):
        return {}
    with contextlib.closing(connect()) as conn:
        row = conn.execute(
            "SELECT params_json FROM commands WHERE kind = 'create_run'"
            " AND run_id = ? ORDER BY rowid DESC LIMIT 1",
            (run_id,),
        ).fetchone()
    return _json.loads(row[0]) if row else {}


def _bootstrap_run_plane(db: Any, repo: Path, run_id: str) -> None:
    """2026-09-23 (island-2 sweep, e2e web journey): bridge the service-plane
    run creation into the run plane. The supervisor-accepted drive_run is the
    FIRST thing that touches the project repo, and drive_once fails "no
    run-plane events" without this bridge. Mirrors cmd_start's core (the
    run-plane authority for starting a run) minus the operator gates the
    service plane's create_run prechecks own (active-run, readiness, hotfix
    prechecks; the unmerged-release-branch confirm gate stays an operator
    concern): story.requested -> M-START -> release branch -> story.md
    capture -> M-STORY. Idempotent: a run the run plane already knows is
    left untouched.
    """

    from tracks.cli.common import writer_lock
    from tracks.kernel.events import Command
    from tracks.store import Store

    params = _create_run_params(db, run_id)
    if not params:
        # No service-plane create_run intent for this run_id: a drive for a
        # run nobody created is a genuine anomaly -- do NOT fabricate a run
        # plane; drive_once fails closed on it (T-008 pin preserved).
        return
    journey = str(params.get("journey") or "feature")
    if journey in ("hotfix_post", "hotfix_dev"):
        _bootstrap_hotfix_run_plane(repo, run_id, params, journey)
        return
    version, raw, title = _version_story(params)
    store = Store(paths.tracks_home(repo))
    try:
        with writer_lock(paths.tracks_home(repo)):
            if store.state(run_id).run_id is not None:
                return  # already bootstrapped (idempotent re-drive)
            store.append(run_id, version, "story.requested", {"raw_chars": len(raw)})
            store.append(run_id, version, "stage.entered", {"stage": "M-START"})
            from tracks.executor.executor import Executor  # noqa: PLC0415

            Executor(store, repo, run_id).issue(
                Command(
                    kind="create_branch",
                    params={"branch_name": f"releases/{version}", "base": "main"},
                )
            )
            _capture_story(repo, paths.tracks_home(repo), version, raw, title)
            store.append(run_id, version, "stage.exited", {"stage": "M-START"})
            store.append(run_id, version, "stage.entered", {"stage": "M-STORY"})
    finally:
        store.close()

def _bootstrap_hotfix_run_plane(repo: Path, run_id: str, params: dict, journey: str) -> None:
    """Bridge a service-plane hotfix create_run into the run plane.

    Mirrors ``hotfix_entry_run``'s establishment (the run-plane authority
    for the hotfix entry, IF-HOTFIX-001) minus its synchronous triage
    run_loop — the drive window below owns the loop: the same version the
    CLI derives from the deterministic precheck report
    (``{target}-hotfix-{issue}`` on pass, ``hotfix-{issue}`` otherwise),
    then hotfix.requested + stage.entered(M-HOTFIX-TRIAGE). Idempotent: a
    run the run plane already knows is left untouched.
    """
    from tracks.cli.common import writer_lock
    from tracks.executor.executor import precheck_hotfix_report  # noqa: PLC0415
    from tracks.kernel.hotfix import HOTFIX_TRIAGE_STAGE
    from tracks.store import Store

    scenario = "dev" if journey == "hotfix_dev" else "post-release"
    issue = params.get("issue")
    try:
        issue_number = int(issue) if not isinstance(issue, bool) else None
    except (TypeError, ValueError):
        issue_number = None
    store = Store(paths.tracks_home(repo))
    try:
        with writer_lock(paths.tracks_home(repo)):
            if store.state(run_id).run_id is not None:
                return  # already bootstrapped (idempotent re-drive)
            if issue_number is None or issue_number < 1:
                # Uncoercible issue: the acceptance preflight owns the
                # rejection, but belt-and-braces fails closed without
                # fabricating establishment events.
                return
            report, _issue = precheck_hotfix_report(repo, store, issue_number, scenario)
            version = (
                f"{report.target_version}-hotfix-{issue_number}"
                if report.status == "pass"
                else f"hotfix-{issue_number}"
            )
            store.append(
                run_id, version, "hotfix.requested",
                {"issue": issue_number, "scenario": scenario},
            )
            store.append(
                run_id, version, "stage.entered", {"stage": HOTFIX_TRIAGE_STAGE}
            )
    finally:
        store.close()


def _version_story(params: dict) -> tuple[str, str, str | None]:
    """Extract (version, raw_story, title) from create_run params.

    v0.11 §1g.2 (IF-MSTART-001): the develop path carries the
    Scribe-suggested ``title`` and the input-form classification; the
    capture render owns the form (raw skeleton / complete adoption) via
    the machine classifier, so ``story_form`` needs no branching here.
    """
    version = str(params.get("version") or "v0.1")
    raw = str(params.get("story") or "").strip() or "service-plane feature request"
    title = params.get("title") or None
    return version, raw, title


def _capture_story(
    repo: Path, home: Any, version: str, raw: str, title: str | None = None
) -> None:
    """Write the story.md capture and commit it (M-START step).

    v0.11 §1g.2: ``templating.render_story_capture`` renders per the input
    form — a raw seed enters the §1-verbatim skeleton, a complete story is
    adopted as-is — and the Scribe-suggested ``title`` prefills the
    frontmatter when present (absent → the placeholder stays, exactly the
    pre-v0.11 behavior for existing create_run callers)."""
    import datetime

    from tracks import templating
    from tracks.executor.helpers import git as _git

    vdir = paths.version_dir(home, version)
    vdir.mkdir(parents=True, exist_ok=True)
    story = vdir / "story.md"
    story.write_text(
        templating.render_story_capture(
            raw, datetime.date.today().isoformat(), title=title
        ),
        encoding="utf-8",
    )
    _git(repo, "add", str(story))
    _git(
        repo, "commit", "-m", f"M-START: capture raw requirement for {version}",
        "--only", "--", str(story),
    )


if __name__ == "__main__":  # pragma: no cover - process entry
    raise SystemExit(main())
