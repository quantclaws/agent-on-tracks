"""Unit tests for OpencodeBackend manifest extraction robustness.

Covers the two WRITE-attempt failure modes observed on 2026-08-14:

- Shield dropped the ``artifact_manifest`` closing brace, relocating
  ``include``/``suggested_commit_message`` to the top level of the emitted
  JSON; the extraction must still accept the recoverable shape.
- The reverse-scan loop overwrote the real validation error with generic
  prose-event errors ("final part.text must be a raw JSON object"),
  sending misleading feedback to the next dispatch.
"""

from __future__ import annotations

import json
import subprocess

from tracks.effects.opencode import OpencodeBackend

_VALID_ITEMS = [
    {"path": "tests/integration/test_x.py", "kind": "integration_test", "role": "test"},
]


def _proc(*texts: str) -> subprocess.CompletedProcess:
    lines = "".join(
        json.dumps({"type": "text", "part": {"text": t}}) + "\n" for t in texts
    )
    return subprocess.CompletedProcess(["opencode"], 0, stdout=lines, stderr="")


def test_top_level_include_accepted_when_wrapper_brace_dropped():
    payload = (
        "```json\n"
        + json.dumps(
            {
                "include": _VALID_ITEMS,
                "suggested_commit_message": "M-TEST: add tests",
            }
        )
        + "\n```"
    )
    manifest, commit, error = OpencodeBackend._extract_manifest(_proc(payload))
    assert error is None
    assert manifest == {"include": _VALID_ITEMS}
    assert commit == "M-TEST: add tests"


def test_manifest_shaped_error_not_masked_by_trailing_prose():
    payload = json.dumps(
        {
            "artifact_manifest": {
                "include": [{"path": "a.py", "kind": "t", "role": ""}]
            },
            "suggested_commit_message": "x",
        }
    )
    manifest, commit, error = OpencodeBackend._extract_manifest(
        _proc(payload, "already completed, nothing to do")
    )
    assert manifest is None
    assert commit is None
    assert error == "artifact_manifest.include[0].role must be a non-empty string"


def test_unrelated_json_after_manifest_does_not_stop_scan():
    good = json.dumps(
        {
            "artifact_manifest": {"include": _VALID_ITEMS},
            "suggested_commit_message": "M-TEST: add tests",
        }
    )
    manifest, commit, error = OpencodeBackend._extract_manifest(
        _proc(good, json.dumps({"note": "unrelated object"}))
    )
    assert error is None
    assert manifest == {"include": _VALID_ITEMS}
    assert commit == "M-TEST: add tests"
