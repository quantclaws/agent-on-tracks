"""Secret redaction for every outbound surface (IF-SECRECY-001).

Credential values are collected at serve start (referenced env names), held
in memory only, and redacted from API responses, SSE frames, HTML pages and
structured service logs; outputs show controlled references (``${NAME}``)
instead of values. Design reference: louke web/secret_redaction (not ported).

Contract token: IF-SECRECY-001.
"""

from __future__ import annotations

from typing import Any


class SecretRedactor:
    """Pattern + known-value redactor applied at every outbound boundary."""

    def __init__(self, secret_values: dict[str, str]) -> None:
        self._secret_values = dict(secret_values)

    def redact_text(self, text: str) -> str:
        """Return text with credential values replaced by ${NAME} / ***."""
        raise NotImplementedError("IF-SECRECY-001")

    def redact_payload(self, payload: Any) -> Any:
        """Recursively redact a JSON-able payload (dict/list/str leaves)."""
        raise NotImplementedError("IF-SECRECY-001")

    def redact_log_record(self, record: dict) -> dict:
        """Redact a structured service-log record before emission."""
        raise NotImplementedError("IF-SECRECY-001")
