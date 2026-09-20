"""Unit tests: ``trac validate-reply --file PATH --kind KIND``.

The agent-side self-verification command for #174 file delivery: zero exit
plus ``ok: <kind> v<version>`` on stdout when the file parses and validates
against the kind schema; exit 1 with the concrete reason on stderr for JSON
errors, missing two-layer headers, kind mismatch, and payload schema
violations (reusing EnvelopeFormatError kind+detail wording).
"""

from __future__ import annotations

import json
from pathlib import Path

from tracks.cli.main import _COMMANDS, main
from tracks.cli.validate_reply_cmd import _parse_validate_reply_args, cmd_validate_reply
from tracks.kernel.envelope import ENVELOPE_VERSION


def _write(path: Path, body: object) -> str:
    path.write_text(json.dumps(body) if not isinstance(body, str) else body, encoding="utf-8")
    return str(path)


def _valid(kind: str = "prism:final", payload: dict | None = None) -> dict:
    return {
        "envelope": {"kind": kind, "version": ENVELOPE_VERSION},
        "payload": payload if payload is not None else {"verdict": "pass"},
    }


def test_success_prints_ok_and_exits_zero(tmp_path, capsys):
    path = _write(tmp_path / "r.json", _valid())
    assert cmd_validate_reply(tmp_path, "--file", path, "--kind", "prism:final") == 0
    out = capsys.readouterr()
    assert out.out == f"ok: prism:final v{ENVELOPE_VERSION}\n"
    assert out.err == ""


def test_arg_order_is_free(tmp_path, capsys):
    path = _write(tmp_path / "r.json", _valid(kind="devon:green", payload={
        "phase": "green",
        "changed_paths": [],
        "commands": [{"cmd": "pytest", "result": "pass", "output_summary": "ok"}],
        "manifest_compliance": True,
        "pre_identity": "pre",
        "post_identity": "post",
        "r_identity": "r",
        "no_change_reason": "in baseline",
        "implemented_if_ids": ["IF-1"],
    }))
    assert cmd_validate_reply(tmp_path, "--kind", "devon:green", "--file", path) == 0
    assert capsys.readouterr().out.startswith("ok: devon:green v")


def test_malformed_json_exits_one(tmp_path, capsys):
    path = _write(tmp_path / "r.json", "{ not json")
    assert cmd_validate_reply(tmp_path, "--file", path, "--kind", "prism:final") == 1
    err = capsys.readouterr().err
    assert "malformed_json" in err


def test_missing_file_exits_one(tmp_path, capsys):
    assert cmd_validate_reply(tmp_path, "--file", str(tmp_path / "absent.json"),
                              "--kind", "prism:final") == 1
    assert "cannot read" in capsys.readouterr().err


def test_missing_two_layer_header_exits_one(tmp_path, capsys):
    path = _write(tmp_path / "r.json", {"verdict": "pass"})
    assert cmd_validate_reply(tmp_path, "--file", path, "--kind", "prism:final") == 1
    assert "missing_kind" in capsys.readouterr().err


def test_kind_mismatch_exits_one(tmp_path, capsys):
    path = _write(tmp_path / "r.json", _valid(kind="prism:review"))
    assert cmd_validate_reply(tmp_path, "--file", path, "--kind", "prism:final") == 1
    err = capsys.readouterr().err
    assert "schema_violation" in err
    assert "prism:review" in err and "prism:final" in err


def test_payload_schema_violation_exits_one(tmp_path, capsys):
    path = _write(tmp_path / "r.json", _valid(kind="prism:review", payload={"verdict": "revise"}))
    assert cmd_validate_reply(tmp_path, "--file", path, "--kind", "prism:review") == 1
    assert "schema_violation" in capsys.readouterr().err


def test_usage_error_on_bad_args(tmp_path, capsys):
    assert cmd_validate_reply(tmp_path, "--file", "only-one") == 1
    assert "usage: trac validate-reply" in capsys.readouterr().err
    assert cmd_validate_reply(tmp_path) == 1
    assert _parse_validate_reply_args(["--bogus"]) is None
    assert _parse_validate_reply_args(["--file", "a"]) is None


def test_registered_in_command_table_and_main_dispatch(tmp_path, capsys, monkeypatch):
    assert "validate-reply" in _COMMANDS
    path = _write(tmp_path / "r.json", _valid())
    monkeypatch.chdir(tmp_path)
    assert main(["validate-reply", "--file", path, "--kind", "prism:final"]) == 0
    assert capsys.readouterr().out == f"ok: prism:final v{ENVELOPE_VERSION}\n"
