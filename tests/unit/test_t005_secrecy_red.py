"""T-005 RED: controlled-reference redaction on every outbound surface
(IF-SECRECY-001).

Devon-owned unit RED for the T-005 delivery slice: ``tracks/server/redaction.py``
implements ``SecretRedactor`` (interfaces §1g.3) — credential values collected
at serve start (referenced env names, kept in memory only) plus common
token/key shapes are replaced with controlled references (``${NAME}``) or
``***`` before any API response, SSE frame, HTML page or structured service-log
line is emitted.

The scaffold body still raises its IF-SECRECY-001 stub token; every failing
node deliberately guards that stub state into a real ``AssertionError`` so the
records classify as assertion_failure (no stub_token, no assembly errors).
The full all-surface sweep over a live serve (pages/API/SSE/logs) is the frozen
integration anchor (deferred to T-INT); these units pin the redactor's own
contract: named-value references, token-shape masking, recursion over
JSON-able payloads and log records, and no over-redaction of clean text.

AC: FR-0315 — TRACKS-TRACE IF-SECRECY-001.
"""

from __future__ import annotations

import json

from tracks.server.redaction import SecretRedactor

_PASSWORD_NAME = "TRAC_CANARY_DB_PASSWORD"
_PASSWORD_VALUE = "pw-synthetic-alpha-7f3a9c2e"
_TOKEN_NAME = "TRAC_CANARY_API_TOKEN"
_TOKEN_VALUE = "tok-synthetic-bravo-4d81f0aa"

_SECRETS = {_PASSWORD_NAME: _PASSWORD_VALUE, _TOKEN_NAME: _TOKEN_VALUE}


def _redactor(**extra: str) -> SecretRedactor:
    values = dict(_SECRETS)
    values.update(extra)
    return SecretRedactor(values)


def _calling(call, label: str):
    """Call a scaffold seam; a NotImplementedError stub becomes an assertion."""
    try:
        return call()
    except NotImplementedError as exc:
        raise AssertionError(f"{label} is still a stub: {exc}") from None


# AC-FR0315-01@v0.9 TRACKS-TRACE IF-SECRECY-001 named credential -> ${NAME}
def test_redact_text_replaces_named_credential_with_controlled_reference():
    text = f"connect failed: password={_PASSWORD_VALUE}; token={_TOKEN_VALUE}"
    out = _calling(lambda: _redactor().redact_text(text), "redact_text")
    assert _PASSWORD_VALUE not in out
    assert _TOKEN_VALUE not in out
    assert f"${{{_PASSWORD_NAME}}}" in out
    assert f"${{{_TOKEN_NAME}}}" in out


# AC-FR0315-01@v0.9 TRACKS-TRACE IF-SECRECY-001 unknown token shapes -> ***
def test_redact_text_masks_token_shape_not_in_known_values():
    raw_token = "ghp_synthetic00112233445566778899aabbcc"
    text = f"request failed: token={raw_token}"
    out = _calling(lambda: _redactor().redact_text(text), "redact_text")
    assert raw_token not in out
    assert "request failed" in out
    assert "***" in out or "${" in out


# AC-FR0315-01@v0.9 TRACKS-TRACE IF-SECRECY-001 clean text stays untouched
def test_redact_text_leaves_plain_text_unchanged():
    plain = "run R1 progressed to stage M-IMPL; 3 tasks done"
    assert _calling(lambda: _redactor().redact_text(plain), "redact_text") == plain


# AC-FR0315-01@v0.9 TRACKS-TRACE IF-SECRECY-001 payload recursion, shape kept
def test_redact_payload_redacts_nested_string_leaves():
    payload = {
        "run_id": "R1",
        "detail": {"command": f"export TOKEN={_TOKEN_VALUE}", "attempts": 2},
        "lines": ["ok", f"password={_PASSWORD_VALUE}", None, True],
    }
    clean = _calling(lambda: _redactor().redact_payload(payload), "redact_payload")
    assert isinstance(clean, dict)
    assert clean["run_id"] == "R1"
    assert clean["detail"]["attempts"] == 2
    assert clean["lines"][0] == "ok"
    assert clean["lines"][2] is None
    assert clean["lines"][3] is True
    blob = json.dumps(clean)
    assert _PASSWORD_VALUE not in blob
    assert _TOKEN_VALUE not in blob
    assert f"${{{_PASSWORD_NAME}}}" in blob


# AC-FR0315-01@v0.9 TRACKS-TRACE IF-SECRECY-001 log record fields scrubbed
def test_redact_log_record_scrubs_all_string_fields():
    record = {
        "ts": "2026-09-21T00:00:00Z",
        "level": "error",
        "msg": "backend auth failed",
        "run_id": "R1",
        "command_id": None,
        "detail": f"password={_PASSWORD_VALUE}",
        "context": {"token": _TOKEN_VALUE, "retries": 1},
    }
    clean = _calling(lambda: _redactor().redact_log_record(record), "redact_log_record")
    assert isinstance(clean, dict)
    assert clean["ts"] == record["ts"]
    assert clean["level"] == "error"
    assert clean["msg"] == "backend auth failed"
    assert clean["command_id"] is None
    assert clean["context"]["retries"] == 1
    blob = json.dumps(clean)
    assert _PASSWORD_VALUE not in blob
    assert _TOKEN_VALUE not in blob
    assert f"${{{_PASSWORD_NAME}}}" in blob


# AC-FR0315-01@v0.9 TRACKS-TRACE IF-SECRECY-001 every occurrence replaced
def test_redact_text_replaces_every_occurrence():
    text = f"{_TOKEN_VALUE} -> {_TOKEN_VALUE}"
    out = _calling(lambda: _redactor().redact_text(text), "redact_text")
    assert _TOKEN_VALUE not in out
    assert out.count(f"${{{_TOKEN_NAME}}}") == 2


# AC-FR0315-01@v0.9 TRACKS-TRACE IF-SECRECY-001 no known values -> passthrough
def test_redactor_without_known_values_is_transparent_on_clean_text():
    clean = _calling(
        lambda: SecretRedactor({}).redact_text("no credentials here"),
        "redact_text",
    )
    assert clean == "no credentials here"
