"""Trailing-brace repair for _first_json_object (OOB, PRISM DIAGNOSE)."""

from __future__ import annotations

import json
import pathlib

from tracks.effects.opencode import OpencodeBackend

BASE = (
    '{"classification":"test_defect","reason":"x",'
    '"evidence":{"defect_file":"a","linked":{"status":"ok"}}}'
)


def _missing(text: str, n: int) -> str:
    return text[:-n] if n else text


def test_missing_one_brace_shaped():
    txt = _missing(BASE, 1)
    got = OpencodeBackend._first_json_object(txt, ("classification",))
    assert got is not None and got["classification"] == "test_defect"


def test_missing_two_braces_shaped():
    txt = _missing(BASE, 2)
    got = OpencodeBackend._first_json_object(txt, ("classification",))
    assert got is not None and got["classification"] == "test_defect"


def test_missing_three_braces_shaped():
    txt = _missing(BASE, 3)
    got = OpencodeBackend._first_json_object(txt, ("classification",))
    assert got is not None and got["classification"] == "test_defect"


def test_missing_four_or_more_not_repaired():
    txt = _missing(BASE, 4)
    got = OpencodeBackend._first_json_object(txt, ("classification",))
    assert got is None
    # blind fallback also should not be the outer classification
    blind = OpencodeBackend._first_json_object(txt)
    assert blind is None or "classification" not in blind


def test_mid_corruption_not_repaired():
    txt = '{"classification":"test_defect","reason":"x" "evidence":{}}'
    got = OpencodeBackend._first_json_object(txt, ("classification",))
    assert got is None or "classification" not in got


def test_complete_with_fence_still_extracts():
    txt = BASE + "\n```"
    got = OpencodeBackend._first_json_object(txt, ("classification",))
    assert got is not None and got["classification"] == "test_defect"
    txt2 = _missing(BASE, 1) + "\n```"
    got2 = OpencodeBackend._first_json_object(txt2, ("classification",))
    assert got2 is not None and got2["classification"] == "test_defect"


def test_diagnose_via_repaired_stdout():
    text = _missing(BASE, 1)
    event = {"type": "text", "part": {"text": text}}
    proc = type("P", (), {"stdout": json.dumps(event), "stderr": ""})()
    backend = OpencodeBackend(pathlib.Path("."), "v0.8")
    diag = backend._diagnose_classification_from(proc)
    assert diag is not None and diag["classification"] == "test_defect"
