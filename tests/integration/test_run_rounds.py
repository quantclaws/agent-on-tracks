"""Per-round read faces (IF-ROUNDS-001, IF-QUERY-001).

AC-FR0357-01: the stage round list and one round's detail expose the
dispatch identity, input, output and tool counts over the documented HTTP
schema; unknown ids 404 and a stage without a window answers an empty list.
AC-FR0357-02: a round whose session log rotated degrades to
``thinking_available: false`` / ``thinking: null`` while input and output
stay readable.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests._support.v012_install import git_init
from tests._support.v012_runs import prepare_home, seed_rounds
from tests._support.v09_web import (
    http_get,
    login_session,
    parse_base_url,
    start_serve,
    stop_serve,
    wait_for_healthz,
    wait_for_port_line,
)

pytestmark = pytest.mark.integration

_VERSION = "v1.0"
_STAGE = "M-TEST"


@pytest.fixture
def rounds_serve(tmp_path: Path):
    repo = git_init(tmp_path / "repo")
    home = tmp_path / "home"
    prepare_home(home, repo, project_id="proj-rounds", version=_VERSION)
    command_ids = seed_rounds(repo, "run-rounds-1", version=_VERSION, count=2, with_logs=True)
    missing_ids = seed_rounds(
        repo,
        "run-rounds-missing",
        version="v1.1",
        count=1,
        with_logs=False,
    )
    proc = start_serve(home, repo, port=0)
    try:
        base = parse_base_url(wait_for_port_line(proc))
        wait_for_healthz(base)
        cookie, _csrf = login_session(base)
        yield base, cookie, command_ids, missing_ids
    finally:
        stop_serve(proc)


def _get(base: str, cookie: str, path: str) -> tuple[int, dict]:
    status, body = http_get(base, path, cookies=cookie)
    return status, json.loads(body)


# AC-FR0357-01@v0.12 TRACKS-TRACE round list and detail schema
def test_rounds_list_and_detail(rounds_serve):
    """AC-FR0357-01: the stage list carries one item per dispatch with the
    documented identity fields, and the detail opens input/output/thinking
    with tool counts; unknown run/command 404 and a windowless stage is []."""
    base, cookie, command_ids, _missing = rounds_serve
    status, payload = _get(
        base, cookie, f"/api/runs/run-rounds-1/stages/{_STAGE}/rounds"
    )
    assert status == 200
    assert payload["run_id"] == "run-rounds-1"
    assert payload["stage"] == _STAGE
    rounds = payload["rounds"]
    assert [item["n"] for item in rounds] == [1, 2]
    assert [item["command_id"] for item in rounds] == command_ids
    first = rounds[0]
    assert first["role"] == "devon"
    assert first["substate"] == "RED"
    assert first["review_round"] == 1
    assert first["attempt"] == 1
    assert first["doc"] == "tests/integration/test_sample.py"
    assert first["objective"] == "round 1"
    assert first["start_ts"]
    assert first["end_ts"]
    assert first["status"] == "ok"
    assert first["output_summary"] == "round 1 summary"
    assert first["input_ref"] and first["output_ref"]
    assert first["has_thinking"] is True

    status, detail = _get(
        base, cookie, f"/api/runs/run-rounds-1/rounds/{command_ids[0]}"
    )
    assert status == 200
    assert detail["command_id"] == command_ids[0]
    assert detail["running"] is False
    assert detail["status"] == "ok"
    assert detail["params"]["substate"] == "RED"
    assert detail["params"]["assignment"] == {"kind": "RGR", "phase": "red"}
    assert "round 1 prompt" in detail["input"]["prompt"]
    assert detail["output"]["text"] == "round 1 output"
    assert detail["output"]["tool_calls"] == [{"tool": "read", "n": 1}]
    assert detail["thinking_available"] is True
    thinking_texts = [item["text"] for item in detail["thinking"]]
    assert (
        "thinking round 1 while reading .tracks/projects/v1.0/test-plan.md"
        in thinking_texts
    ), thinking_texts

    status, body = _get(base, cookie, "/api/runs/unknown-run/stages/M-TEST/rounds")
    assert status == 404 and body["error"]["reason"] == "not_found"
    status, body = _get(
        base, cookie, "/api/runs/run-rounds-1/stages/M-SECURITY/rounds"
    )
    assert status == 200 and body["rounds"] == []
    status, body = _get(base, cookie, "/api/runs/run-rounds-1/rounds/unknown-command")
    assert status == 404 and body["error"]["reason"] == "not_found"


# AC-FR0357-02@v0.12 TRACKS-TRACE missing thinking degrades readably
def test_missing_thinking_degrades(rounds_serve):
    """AC-FR0357-02: when the session log is gone the detail reports
    ``thinking_available: false`` with ``thinking: null`` and still returns
    the archived input and output (never fabricates reasoning)."""
    base, cookie, _command_ids, missing_ids = rounds_serve
    status, detail = _get(
        base, cookie, f"/api/runs/run-rounds-missing/rounds/{missing_ids[0]}"
    )
    assert status == 200
    assert detail["thinking_available"] is False
    assert detail["thinking"] is None
    assert "round 1 prompt" in detail["input"]["prompt"]
    assert detail["output"]["text"] == "round 1 output"
    assert detail["output"]["tool_calls"] == [{"tool": "read", "n": 1}]
