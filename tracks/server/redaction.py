"""Secret redaction for every outbound surface (IF-SECRECY-001).

Credential values are collected at serve start (referenced env names), held in
memory only, and redacted from API responses, SSE frames, HTML pages and
structured service logs; outputs show controlled references (``${NAME}``)
instead of values. Common credential-shaped substrings that are *not*
registered values (``token=...``, ``Bearer ...``) are masked as ``***`` so an
unregistered secret cannot ride an outbound line. Design reference: louke
web/secret_redaction (not ported).

Contract token: IF-SECRECY-001.
"""

from __future__ import annotations

import re
from typing import Any

# Credential assignment shapes (interfaces §1g.3): keyword, separator, value.
_ASSIGNMENT_SHAPE = re.compile(
    r"(?i)\b(?P<keyword>password|passwd|token|secret|api[_-]?key|access[_-]?key|private[_-]?key)"
    r"(?P<separator>\s*[=:]\s*)(?P<value>\S+)"
)

# ``Authorization: Bearer <credential>`` and bare bearer credentials.
_BEARER_SHAPE = re.compile(r"(?i)\b(bearer\s+)([A-Za-z0-9._~+/=-]{8,})")

# Controlled references produced by the known-value pass: their ``${NAME}``
# body must never be masked again by the pattern pass.
_REFERENCE_PREFIX = "${"


class SecretRedactor:
    """Pattern + known-value redactor applied at every outbound boundary."""

    def __init__(self, secret_values: dict[str, str]) -> None:
        # Longest value first: a shorter registered value must not split a
        # longer one before the longer replacement lands.
        self._secret_values = sorted(
            (
                (name, value)
                for name, value in dict(secret_values).items()
                if isinstance(value, str) and value
            ),
            key=lambda item: len(item[1]),
            reverse=True,
        )

    def redact_text(self, text: str) -> str:
        """Return text with credential values replaced by ${NAME} / ***."""
        text = self._replace_known_values(text)
        text = _ASSIGNMENT_SHAPE.sub(self._mask_assignment, text)
        return _BEARER_SHAPE.sub(r"\1***", text)

    def _replace_known_values(self, text: str) -> str:
        """Replace every registered credential value with its ``${NAME}``."""
        for name, value in self._secret_values:
            text = text.replace(value, f"${{{name}}}")
        return text

    @staticmethod
    def _mask_assignment(match: re.Match[str]) -> str:
        """Mask one matched credential value unless it is a ${NAME} reference."""
        if match.group("value").startswith(_REFERENCE_PREFIX):
            return match.group(0)
        return f"{match.group('keyword')}{match.group('separator')}***"

    def redact_payload(self, payload: Any) -> Any:
        """Recursively redact a JSON-able payload (dict/list/str leaves)."""
        if isinstance(payload, str):
            return self.redact_text(payload)
        if isinstance(payload, dict):
            return {key: self.redact_payload(value) for key, value in payload.items()}
        if isinstance(payload, (list, tuple)):
            return [self.redact_payload(item) for item in payload]
        return payload

    def redact_log_record(self, record: dict) -> dict:
        """Redact a structured service-log record before emission."""
        redacted = self.redact_payload(record)
        if isinstance(redacted, dict):
            return redacted
        return dict(record)
