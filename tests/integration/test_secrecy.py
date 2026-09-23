"""Secrecy and scope (IF-SECRECY-001)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tracks.server.redaction import SecretRedactor

pytestmark = pytest.mark.integration


def _redactor() -> SecretRedactor:
    return SecretRedactor({"TRAC_CANARY_V09_ALPHA": "sk-v09-canary-alpha-7f3a9c2e1b"})


# AC-FR0315-01@v0.9 TRACKS-TRACE no plaintext secrets any surface
# Operator OOB 2026-09-23: verified green-on-arrival in the island-2 terminal sweep; M-TEST re-entry after full implementation (run 01M2QTJB).
def test_no_plaintext_secrets_any_surface():
    """AC-FR0315-01: API SSE HTML logs carry no plaintext credential."""
    red = _redactor()
    raw = "token sk-v09-canary-alpha-7f3a9c2e1b in api body"
    clean = red.redact_text(raw)
    assert "sk-v09-canary-alpha-7f3a9c2e1b" not in clean
    # a credential survives only as the controlled reference (§1g.3)
    assert "${TRAC_CANARY_V09_ALPHA}" in clean, (
        f"the credential must appear as a controlled reference: {clean!r}"
    )
    payload = {"events": [{"data": raw}], "log": raw, "page": f"<p>{raw}</p>"}
    clean = red.redact_payload(payload)
    assert "sk-v09-canary-alpha-7f3a9c2e1b" not in repr(clean)
    assert repr(clean).count("${TRAC_CANARY_V09_ALPHA}") == 3, (
        "every surface must carry the controlled reference, never the secret"
    )
    record = {"msg": raw, "run_id": "run-1"}
    clean = red.redact_log_record(record)
    assert "sk-v09-canary-alpha-7f3a9c2e1b" not in repr(clean)
    assert "${TRAC_CANARY_V09_ALPHA}" in clean["msg"], (
        "the log record must carry the controlled reference"
    )


# AC-FR0315-02@v0.9 TRACKS-TRACE out of scope read denied
def test_out_of_scope_read_denied(tmp_path: Path):
    """AC-FR0315-02: out-of-scope file read denied and audited."""
    from tracks.server.guard import GuardRejection, check_path_scope

    permitted = [tmp_path / "repo"]
    (tmp_path / "repo").mkdir()
    try:
        check_path_scope(Path("/etc/passwd"), permitted)
    except GuardRejection as exc:
        assert exc.reason == "scope_violation"
    else:
        raise AssertionError("out of scope read must be denied")
