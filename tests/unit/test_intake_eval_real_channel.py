"""Live-only OOB: real-channel Scribe INTAKE_EVAL carries intake_report.

The fake channel (``FakeBackend._act_intake_eval``) always produced the
§1f.2 ``intake_report``; the real opencode channel's ``_success_result``
had no INTAKE_EVAL branch, so the intake worker's ``_coerce_report``
always rejected live outcomes ("outcome carried no conforming intake
report"). The final reply text already holds the report JSON — this
covers its extraction/attachment.
"""

from __future__ import annotations

import json
import subprocess

from tracks.effects.opencode import OpencodeBackend


def _proc(*texts: str) -> subprocess.CompletedProcess:
    lines = "".join(
        json.dumps({"type": "text", "part": {"text": t}}) + "\n" for t in texts
    )
    return subprocess.CompletedProcess(["opencode"], 0, stdout=lines, stderr="")


def _backend() -> OpencodeBackend:
    backend = object.__new__(OpencodeBackend)
    backend._capture_io = lambda proc, prompt, console_input: {"stub": True}
    return backend


def _report_payload() -> dict:
    return {
        "feasibility": {"verdict": "feasible", "blockers": []},
        "duplicates": {"found": False, "items": []},
        "conflicts": {"found": False, "pairs": []},
        "title": "live intake title",
        "input_form": "raw",
        "summary": "live summary",
    }


def test_intake_eval_real_channel_attaches_report():
    backend = _backend()
    payload = _report_payload()
    reply = "评估完成\n" + json.dumps(payload, ensure_ascii=False) + "\n"
    proc = _proc(reply)
    result = backend._success_result(
        "Scribe", "INTAKE_EVAL", [], None, proc, "p", None, False, False
    )
    assert result["status"] == "done"
    assert result["intake_report"] == payload


def test_intake_eval_real_channel_missing_json_leaves_result_untouched():
    backend = _backend()
    proc = _proc("plain prose without any report json")
    result = backend._success_result(
        "Scribe", "INTAKE_EVAL", [], None, proc, "p", None, False, False
    )
    assert result["status"] == "done"
    assert "intake_report" not in result


def test_intake_report_from_helper_paths():
    backend = _backend()
    payload = _report_payload()
    assert backend._intake_report_from(
        _proc(json.dumps(payload, ensure_ascii=False))
    ) == payload
    assert backend._intake_report_from(_proc("no json here")) is None
