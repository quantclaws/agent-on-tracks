"""NFR-0050 Agent I/O evidence contracts."""

from tracks.effects.opencode import redact
from tracks.executor.helpers import _agent_io_evidence


def test_redact_masks_credentials_and_preserves_non_sensitive_fields():
    text = '{"api_key":"secret-value","answer":"ordinary"} Authorization: Bearer token'
    redacted = redact(text)
    assert "secret-value" not in redacted
    assert "Bearer token" not in redacted
    assert "ordinary" in redacted


def test_audit_blob_failure_is_partial_without_dangling_references():
    class BrokenStore:
        @staticmethod
        def write_audit_blob(_payload):
            return None

    evidence = _agent_io_evidence(
        BrokenStore(),
        {"role": "lex", "substate": "LEX_REVIEW", "doc": "spec.md"},
        {"stdout": "{}", "stderr": "", "stdout_bytes": 2, "stderr_bytes": 0},
    )
    assert evidence["audit_completeness"] == "partial"
    assert evidence["input_ref"] is None
    assert evidence["output_ref"] is None
    assert "agent_output_blob_write_failed" in evidence["audit_gaps"]
