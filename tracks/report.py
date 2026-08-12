"""Read-only workflow report generation (FR-0220/NFR-0050).

The report deliberately consumes the Runtime event store instead of test logs.
It writes ordinary files only; no event, document, or Git mutation is performed.
"""
from __future__ import annotations

import html
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from tracks import paths
from tracks.discuss.gate import check_ready
from tracks.discuss.model import iter_comments
from tracks.discuss.parser import parse_threads
from tracks.executor.executor import git
from tracks.kernel.events import EventEnvelope
from tracks.kernel.machine import project
from tracks.tasklog import rebuild_task_log

_GITHUB_REMOTE = re.compile(r"github\.com[/:](?P<repo>[^/]+/[^/]+?)(?:\.git)?$")
_RESULT_PREFIXES = (
    "outcome.", "verdict.", "stage.", "run.", "branch.", "story.",
    "spec.", "acceptance.", "preview.", "approval.", "issue.", "issues.",
)


@dataclass(frozen=True)
class Activity:
    """A command and its first associated result, or an open activity."""

    command: object
    result: object | None
    related: tuple = ()
    stage: str | None = None


def _repo_root(repo: Path) -> Path:
    result = git(repo, "rev-parse", "--show-toplevel")
    return Path(result.stdout.strip()).resolve()


def _output_dir(repo: Path, output: str | Path) -> Path:
    target = Path(output).expanduser()
    if not target.is_absolute():
        target = repo / target
    return target.resolve()


def _remote_url(repo: Path) -> str | None:
    remote = git(repo, "remote", check=False).stdout.splitlines()
    for name in remote:
        value = git(repo, "remote", "get-url", name, check=False).stdout.strip()
        if value:
            return value
    return None


def _commit_link(repo: Path, sha: str) -> str:
    remote = _remote_url(repo) or ""
    match = _GITHUB_REMOTE.search(remote.removesuffix("/"))
    if match:
        return f"https://github.com/{match.group('repo')}/commit/{sha}"
    return f"local: git show {sha}"


def _actor(event_type: str, payload: dict) -> str:
    if event_type.startswith("human."):
        return str(payload.get("actor") or "Human")
    if event_type.endswith(".verdict"):
        return event_type.split(".", maxsplit=1)[0]
    if event_type == "dispatch_agent":
        return str(payload.get("role") or "Runtime")
    if event_type == "outcome.received":
        return str(payload.get("role") or "Runtime")
    return "Runtime"


def _command_activities(events: list) -> list[Activity]:
    activities = []
    stage = None
    for index, event in enumerate(events):
        if event.type == "stage.entered":
            stage = event.payload.get("stage")
        if event.type != "command.issued":
            continue
        related = tuple(
            candidate
            for candidate in events[index + 1 :]
            if candidate.command_id == event.command_id
            and candidate.type != "command.issued"
            and candidate.type.startswith(_RESULT_PREFIXES)
        )
        kind = event.payload.get("command", {}).get("kind")
        if kind == "validate_document" and related and related[0].type == "verdict.passed":
            continue
        activities.append(Activity(event, related[0] if related else None, related, stage))
    return activities


def _short_json(value, limit: int = 6000) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if len(raw) <= limit:
        return raw
    return raw[:limit] + f"... [summary truncated; {len(raw)} chars total]"


def _load_blob(home: Path, ref: str):
    path = paths.blobs_dir(home) / ref
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"audit_gap": f"missing blob {ref}"}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"blob_ref": ref, "text": raw}


def _expand_refs(home: Path, value):
    if isinstance(value, dict):
        if set(value) == {"$ref"}:
            return _load_blob(home, str(value["$ref"]))
        return {key: _expand_refs(home, item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_refs(home, item) for item in value]
    return value


def _event_json(event) -> str:
    return _short_json(event.payload)


def _agent_io_details(home: Path, io: dict) -> dict:
    details = dict(io)
    if io.get("input_ref"):
        details["assignment"] = _load_blob(home, str(io["input_ref"]))
    if io.get("output_ref"):
        details["agent_output"] = _load_blob(home, str(io["output_ref"]))
    return details


def _activity_header(activity: Activity) -> list[str]:
    event = activity.command
    command = event.payload.get("command", {})
    params = command.get("params", {})
    kind = command.get("kind", "unknown")
    result = activity.result
    status = result.payload.get("status", result.type) if result else "interrupted"
    actor = _actor(result.type, result.payload) if result else _actor(kind, params)
    attempt = result.payload.get("attempt") if result else params.get("attempt")
    return [
        f"- `{event.ts}` **{kind}** stage=`{activity.stage or '-'}` actor=`{actor}` "
        f"status=`{status}` attempt=`{attempt if attempt is not None else '-'}` "
        f"review_round=`{params.get('review_round', '-')}`",
        f"  - assignment: `{_short_json(params)}`",
    ]


def _assignment_lines(assignment) -> list[str]:
    if assignment is None:
        return []
    lines = ["  - assignment expanded:", "```json", _short_json(assignment), "```"]
    if isinstance(assignment, dict) and assignment.get("audit_gap"):
        lines.append(f"  - audit gap: `{assignment['audit_gap']}`")
    return lines


def _agent_output_lines(output) -> list[str]:
    if output is None:
        return []
    stdout = output.get("stdout", "") if isinstance(output, dict) else output
    stderr = output.get("stderr", "") if isinstance(output, dict) else ""
    lines = []
    if isinstance(output, dict) and output.get("audit_gap"):
        lines.append(f"  - audit gap: `{output['audit_gap']}`")
    lines.extend(["  - agent stdout summary:", "```text",
                  _text_summary(stdout), "```",
                  "  - agent stderr summary:", "```text",
                  _text_summary(stderr), "```"])
    return lines


def _agent_io_lines(home: Path, io: dict) -> list[str]:
    details = _agent_io_details(home, io)
    lines = [
        f"  - agent input ref=`{io.get('input_ref') or '-'}` "
        f"output ref=`{io.get('output_ref') or '-'}` "
        f"completeness=`{io.get('audit_completeness', '-')}`"
    ]
    lines.extend(_assignment_lines(details.get("assignment")))
    lines.extend(_agent_output_lines(details.get("agent_output")))
    return lines


def _discussion_evidence_lines(evidence) -> list[str]:
    lines = ["  - discussion evidence: `" + _short_json(evidence) + "`"]
    threads = evidence.get("threads", []) if isinstance(evidence, dict) else ()
    for thread in threads:
        lines.append(
            f"  - discussion thread=`{thread.get('thread_id', '-')}` "
            f"initiator=`{thread.get('initiator', '-')}` "
            f"status=`{thread.get('status', '-')}` "
            f"reply_count=`{thread.get('reply_count', 0)}`"
        )
    return lines


def _activity_result_lines(activity: Activity, home: Path) -> list[str]:
    result = activity.result
    if result is None:
        return ["  - result: `interrupted` (no closing event)"]
    lines = [f"  - result: `{result.type}` at `{result.ts}`",
             f"  - payload: `{_event_json(result)}`"]
    io = result.payload.get("agent_io")
    if io:
        lines.extend(_agent_io_lines(home, io))
    evidence = result.payload.get("discussion_evidence")
    if evidence is not None:
        lines.extend(_discussion_evidence_lines(evidence))
    return lines


def _activity_commit_lines(activity: Activity, repo: Path) -> list[str]:
    lines = []
    for related in activity.related:
        for sha in _commit_shas(related):
            lines.append(f"  - commit: `{sha}` ({_commit_link(repo, sha)})")
    result = activity.result
    if result and result.payload.get("diff_ref") and not any(
        _commit_shas(related) for related in activity.related
    ):
        lines.append(
            "  - commit fallback: target diff captured (no commit event yet): `" +
            _text_summary(result.payload["diff_ref"], 2000) + "`"
        )
    return lines


def _activity_lines(activity: Activity, repo: Path, home: Path) -> list[str]:
    lines = _activity_header(activity)
    lines.extend(_activity_result_lines(activity, home))
    lines.extend(_activity_commit_lines(activity, repo))
    return lines


def _text_summary(value: str, limit: int = 4000) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text or "(empty)"
    return text[:limit] + f"\n[summary truncated; {len(text)} chars total]"


def _commit_shas(event) -> list[str]:
    if event is None:
        return []
    return [
        str(value)
        for key, value in event.payload.items()
        if key == "commit_sha" and value
    ]


def _discussion_lines(repo: Path, version: str | None) -> list[str]:
    lines = ["", "## Discussions", ""]
    if not version:
        return lines + ["- audit gap: run version is unavailable", ""]
    vdir = paths.version_dir(repo / ".tracks", version)
    found = False
    for doc in ("story.md", "spec.md", "acceptance.md"):
        path = vdir / doc
        if not path.is_file():
            continue
        found = True
        text = path.read_text(encoding="utf-8")
        threads = parse_threads(text)
        ready, blockers = check_ready(text)
        lines.append(
            f"- document=`{doc}` discussion_ready=`{ready}` "
            f"blockers=`{','.join(blockers) or '-'}` threads=`{len(threads)}`"
        )
        for thread in threads:
            lines.append(
                f"  - thread=`{thread.thread_id}` initiator=`{thread.initiator}` "
                f"status=`{thread.status}` last_speaker=`{thread.last_speaker}` "
                f"reply_count=`{thread.reply_count}` root=`{_text_summary(thread.root.body, 800)}`"
            )
            for reply in list(iter_comments(thread.root))[1:]:
                lines.append(
                    f"    - reply speaker=`{reply.speaker}` depth=`{reply.depth}` "
                    f"body=`{_text_summary(reply.body, 1200)}`"
                )
    if not found:
        lines.append("- audit gap: no story/spec/acceptance document found")
    return lines


def _event_audit_gaps(event) -> list[str]:
    payload = event.payload
    gaps = []
    if payload.get("audit_gap"):
        gaps.append(f"event {event.seq}: {payload['audit_gap']}")
    io = payload.get("agent_io")
    if io and io.get("audit_gaps"):
        gaps.extend(f"event {event.seq}: {gap}" for gap in io["audit_gaps"])
    return gaps


def _failed_dispatch_attempt(event, events: list):
    return next(
        (
            issued.payload.get("command", {}).get("params", {}).get("attempt")
            for issued in events
            if issued.type == "command.issued"
            and issued.command_id == event.command_id
        ),
        None,
    )


def _audit_event_lines(event, events: list) -> list[str]:
    payload = event.payload
    lines = []
    if event.type == "run.interrupted":
        lines.append(
            f"- interrupted activity: event=`{event.seq}` "
            f"reason=`{payload.get('reason', '-')}` "
            f"stage=`{payload.get('stage', '-')}` substate=`{payload.get('substate', '-')}`"
        )
    if event.type == "outcome.received" and payload.get("status") == "failed":
        attempt = _failed_dispatch_attempt(event, events)
        lines.append(
            f"- failed dispatch: role=`{payload.get('role', '-')}` "
            f"failure=`{payload.get('failure_class', '-')}` attempt=`{attempt or '-'}` "
            f"report=`{_text_summary(payload.get('self_report', ''), 1200)}`"
        )
    return lines


def _activity_audit_gaps(activity: Activity) -> list[str]:
    if activity.result is None:
        kind = activity.command.payload.get("command", {}).get("kind", "-")
        return [f"command {kind}: interrupted"]
    return []


def _audit_lines(events: list, activities: list[Activity]) -> list[str]:
    lines = ["", "## Audit", ""]
    gaps = []
    for event in events:
        gaps.extend(_event_audit_gaps(event))
        lines.extend(_audit_event_lines(event, events))
    for activity in activities:
        gaps.extend(_activity_audit_gaps(activity))
    if gaps:
        lines.append("- audit gaps:")
        lines.extend(f"  - {gap}" for gap in gaps)
    else:
        lines.append("- audit gaps: none observed")
    return lines


def _markdown(repo: Path, run_id: str, state, events: list) -> str:
    lines = [
        f"# Workflow report: `{run_id}`",
        "",
        f"- status: `{state.status}`",
        f"- stage: `{state.stage or '-'}`",
        f"- substate: `{state.substate or '-'}`",
        f"- awaiting: `{state.awaiting or '-'}`",
        "",
        "## Timeline",
        "",
    ]
    activities = _command_activities(events)
    for activity in activities:
        lines.extend(_activity_lines(activity, repo, repo / ".tracks"))
    lines.extend(_discussion_lines(repo, state.version))
    lines.extend(_audit_lines(events, activities))
    lines.extend(["", "## Events", ""])
    hidden_validates = {
        event.command_id
        for event in events
        if event.type == "command.issued"
        and event.payload.get("command", {}).get("kind") == "validate_document"
        and any(
            result.command_id == event.command_id and result.type == "verdict.passed"
            for result in events
        )
    }
    for event in events:
        if event.command_id in hidden_validates and event.type in (
            "command.issued", "verdict.passed"
        ):
            continue
        actor = _actor(event.type, event.payload)
        lines.append(
            f"- `{event.seq}` `{event.ts}` `{event.type}` actor=`{actor}` "
            f"payload=`{_event_json(event)}`"
        )
    return "\n".join(lines) + "\n"


def _read_events(home: Path, run_id: str) -> list[EventEnvelope]:
    """Read an existing database without opening it in writable mode."""
    database = paths.db_path(home)
    if not database.is_file():
        raise ValueError(f"runtime database not found: {database}")
    connection = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT run_id, seq, ts, version, type, schema_version, "
            "command_id, task_id, payload FROM events WHERE run_id = ? ORDER BY seq",
            (run_id,),
        ).fetchall()
    finally:
        connection.close()
    events = []
    for row in rows:
        payload = _expand_refs(home, json.loads(row[8]))
        events.append(
            EventEnvelope(
                seq=row[1], ts=row[2], run_id=row[0], version=row[3], type=row[4],
                schema_version=row[5], command_id=row[6], task_id=row[7], payload=payload,
            )
        )
    return events


def progress_summary(events: list[EventEnvelope]) -> str | None:
    """Return concise, deterministic task progress without agent output."""
    summaries = [
        summary
        for summary in (_task_progress_summary(events), _red_checkpoint_summary(events))
        if summary is not None
    ]
    return f"progress: {'; '.join(summaries)}" if summaries else None


def _task_progress_summary(events: list[EventEnvelope]) -> str | None:
    taskgraph_events = [event for event in events if event.type == "taskgraph.committed"]
    task_events = [event for event in events if event.type.startswith("task.")]
    if not taskgraph_events and not task_events:
        return None
    declared_ids = set().union(*(
        _payload_task_ids(event.payload.get("task_ids")) for event in taskgraph_events
    ))
    task_ids = declared_ids | _event_task_ids(task_events)
    started_ids = _event_task_ids(task_events, "task.started")
    completed_ids = _event_task_ids(task_events, "task.completed")
    total = max(
        len(task_ids),
        max((_declared_task_count(event) for event in taskgraph_events), default=0),
    )
    summary = f"tasks: {len(completed_ids)}/{total} completed"
    if started_ids:
        summary += f", {len(started_ids)} started"
    if task_ids:
        summary += f" ({', '.join(sorted(task_ids))})"
    return summary


def _red_checkpoint_summary(events: list[EventEnvelope]) -> str | None:
    checkpoints = [event for event in events if event.type == "red.checkpointed"]
    if not checkpoints:
        return None
    task_ids = _event_task_ids(checkpoints)
    summary = f"Tracks-R: {len(checkpoints)} checkpointed"
    if task_ids:
        summary += f" ({', '.join(sorted(task_ids))})"
    return summary


def _payload_task_ids(value) -> set[str]:
    if not isinstance(value, (list, tuple)):
        return set()
    return {item for item in value if isinstance(item, str) and item}


def _event_task_ids(events: list[EventEnvelope], event_type: str | None = None) -> set[str]:
    return {
        task_id
        for event in events
        if event_type is None or event.type == event_type
        for task_id in (_event_task_id(event),)
        if task_id is not None
    }


def _declared_task_count(event: EventEnvelope) -> int:
    value = event.payload.get("task_count")
    if isinstance(value, int) and not isinstance(value, bool):
        return max(value, 0)
    return 0


def _event_task_id(event: EventEnvelope) -> str | None:
    value = event.payload.get("task_id") or event.task_id
    return value if isinstance(value, str) and value else None


def _html_page(run_id: str) -> str:
    title = html.escape(f"Workflow report: {run_id}", quote=True)
    marked_path = Path(__file__).parent / "assets" / "marked.min.js"
    marked_source = marked_path.read_text(encoding="utf-8")
    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '  <meta charset="utf-8">\n'
        '  <meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"  <title>{title}</title>\n"
        "  <style>\n"
        "    body {\n"
        "      max-width: 72rem;\n"
        "      margin: 0 auto;\n"
        "      padding: 2rem 1rem;\n"
        "      font-family: sans-serif;\n"
        "      line-height: 1.5;\n"
        "      color: #222;\n"
        "    }\n"
        "    pre { overflow-x: auto; }\n"
        "    code { font-family: monospace; }\n"
        "  </style>\n"
        "</head>\n"
        "<body>\n"
        '  <article id="content"></article>\n'
        "  <script>\n"
        f"{marked_source}\n"
        "  </script>\n"
        "  <script>\n"
        '    fetch("report.md")\n'
        "      .then(response => {\n"
        "        if (!response.ok) {\n"
        "          throw new Error(`HTTP ${response.status}`);\n"
        "        }\n"
        "        return response.text();\n"
        "      })\n"
        "      .then(md => {\n"
        '        document.getElementById("content").innerHTML = marked.parse(md);\n'
        "      })\n"
        "      .catch(error => {\n"
        '        document.getElementById("content").textContent =\n'
        '          `Unable to load report.md: ${error.message}`;\n'
        "      });\n"
        "  </script>\n"
        "</body>\n"
        "</html>\n"
    )


def generate_report(repo: Path, run_id: str, output: str | Path) -> tuple[Path, Path]:
    """Generate report files from the current Git host and return both paths."""
    root = _repo_root(repo)
    home = root / ".tracks"
    events = _read_events(home, run_id)
    if not events:
        raise ValueError(f"unknown run: {run_id}")
    rebuild_task_log(home, events)
    target = _output_dir(repo, output)
    target.mkdir(parents=True, exist_ok=True)
    markdown = _markdown(root, run_id, project(events), events)
    report_md = target / "report.md"
    index_html = target / "index.html"
    report_md.write_text(markdown, encoding="utf-8")
    index_html.write_text(_html_page(run_id), encoding="utf-8")
    return report_md, index_html
