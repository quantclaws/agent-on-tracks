"""OpenCode 2.x native NDJSON → intake activity timeline.

``tracks.server.intake_activity.collect_activity`` reads the live JSONL files
that ``OpencodeRunMixin._stream_stdout`` tees into
``<repo>/.tracks/runtime/opencode-logs/``. Each line is one 2.x event::

    {"type": ..., "timestamp": <ms int | ISO str>, "sessionID": ...,
     "part": {...}}

There is no 1.x ``opencode-logger`` ``message.part.updated`` wrapper.
"""

from __future__ import annotations

import json

from tracks.server import intake_activity

INTAKE = "IN-001"


def _mkline(kind, part, ts, session="ses_v2", **extra):
    payload = {"type": kind, "timestamp": ts, "sessionID": session, "part": part}
    payload.update(extra)
    return json.dumps(payload) + "\n"


def _write_log(tmp_path, *lines):
    log_dir = tmp_path / ".tracks" / "runtime" / "opencode-logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "stream-20261121T000000-4242.jsonl"
    path.write_text("".join(lines), encoding="utf-8")
    return path


def test_collect_activity_parses_v2_ndjson(tmp_path, monkeypatch):
    monkeypatch.delenv("TRACKS_HOME", raising=False)
    intake_path = f".tracks/projects/v1/intakes/{INTAKE}/intake.md"
    _write_log(
        tmp_path,
        _mkline("step_start", {"type": "step-start", "id": "p0"}, 1700000000000),
        _mkline(
            "tool_use",
            {
                "type": "tool",
                "partID": "prt_read",
                "tool": "read",
                "state": {"input": {"path": intake_path}, "output": "intake body"},
            },
            1700000001000,
        ),
        _mkline(
            "tool_use",
            {
                "type": "tool",
                "partID": "prt_shell",
                "tool": "shell",
                "state": {"input": {"command": "ls  -la"}, "output": "files"},
            },
            1700000002000,
        ),
        _mkline(
            "reasoning",
            {"type": "reasoning", "id": "p_reason", "text": "weighing the intake"},
            1700000002500,
        ),
        _mkline(
            "text",
            {"type": "text", "id": "p_text", "text": "evaluating intake"},
            1700000003000,
        ),
        _mkline("step_finish", {"type": "step-finish", "id": "p1"}, 1700000004000),
    )

    sessions, items = intake_activity.collect_activity(tmp_path, INTAKE)

    assert sessions == ["ses_v2"]
    assert [item["kind"] for item in items] == [
        "step",
        "tool",
        "tool",
        "thinking",
        "text",
        "step",
    ]
    assert [item["tool"] for item in items] == [
        None,
        "read",
        "shell",
        None,
        None,
        None,
    ]
    assert items[0]["label"] == "步骤开始"
    assert items[1]["label"] == f"读取 {intake_path}"
    assert items[1]["text"] == "intake body"
    assert items[2]["label"] == "执行 ls -la"
    assert items[3]["text"] == "weighing the intake"
    assert items[4]["text"] == "evaluating intake"
    assert items[5]["label"] == "步骤结束"
    # integer millisecond timestamps normalize to ISO-8601 and stay ordered.
    assert items[0]["ts"] == "2023-11-14T22:13:20Z"
    assert items[-1]["ts"] == "2023-11-14T22:13:24Z"


def test_collect_activity_accepts_iso_timestamps(tmp_path, monkeypatch):
    monkeypatch.delenv("TRACKS_HOME", raising=False)
    _write_log(
        tmp_path,
        _mkline(
            "text",
            {"type": "text", "id": "p1", "text": f"see intakes/{INTAKE}/intake.md"},
            "2026-11-21T10:00:00.000Z",
        ),
    )
    sessions, items = intake_activity.collect_activity(tmp_path, INTAKE)
    assert sessions == ["ses_v2"]
    assert items[0]["ts"] == "2026-11-21T10:00:00.000Z"
    assert items[0]["text"] == f"see intakes/{INTAKE}/intake.md"


def test_collect_activity_ignores_v1_logger_wrapper(tmp_path, monkeypatch):
    """Zero 1.x compatibility: V1 ``message.part.updated`` rows are not parts."""
    monkeypatch.delenv("TRACKS_HOME", raising=False)
    v1 = {
        "timestamp": "2026-11-09T10:00:00.000Z",
        "eventType": "message.part.updated",
        "payload": {
            "properties": {
                "sessionID": "ses_v1",
                "part": {"type": "text", "text": f"intakes/{INTAKE}/intake.md"},
            }
        },
    }
    _write_log(tmp_path, json.dumps(v1) + "\n")
    assert intake_activity.collect_activity(tmp_path, INTAKE) == ([], [])


def test_collect_activity_skips_unrelated_and_malformed_lines(tmp_path, monkeypatch):
    monkeypatch.delenv("TRACKS_HOME", raising=False)
    _write_log(
        tmp_path,
        "[opencode] plugin initialized\n",
        "not json\n",
        json.dumps({"type": "text", "part": "nope", "sessionID": "ses_v2"}) + "\n",
        json.dumps({"type": "text", "part": {"type": "text"}, "timestamp": 1}) + "\n",
        _mkline(
            "text", {"type": "text", "id": "p1", "text": f"intakes/{INTAKE}/x.md"}, 1700000000000
        ),
    )
    sessions, items = intake_activity.collect_activity(tmp_path, INTAKE)
    assert sessions == ["ses_v2"]
    assert len(items) == 1


def test_collect_activity_reads_appended_bytes(tmp_path, monkeypatch):
    """Polling cache: a second poll only consumes the appended tail."""
    monkeypatch.delenv("TRACKS_HOME", raising=False)
    path = _write_log(
        tmp_path,
        _mkline(
            "text", {"type": "text", "id": "p1", "text": f"intakes/{INTAKE}/a.md"}, 1700000000000
        ),
    )
    assert len(intake_activity.collect_activity(tmp_path, INTAKE)[1]) == 1
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            _mkline(
                "text",
                {"type": "text", "id": "p2", "text": "second"},
                1700000001000,
            )
        )
    assert len(intake_activity.collect_activity(tmp_path, INTAKE)[1]) == 2
