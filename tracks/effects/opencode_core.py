"""Shared leaf vocabulary for the OpencodeBackend composition split (C0302).

``redact``, ``OpencodeError`` and the agent-name map are consumed by the
opencode mixin modules; :mod:`tracks.effects.opencode` re-exports every name,
so the pre-split import surface is unchanged.
"""

from __future__ import annotations

import os
import re

AGENT_NAME = {
    "scribe": "Scribe",
    "sage": "Sage",
    "lex": "Lex",
    "archer": "Archer",
    "prism": "Prism",
    "shield": "Shield",
    "devon": "Devon",
}

_SECRET_VALUE = re.compile(
    r"(?i)((?:authorization|api[_ -]?key|access[_ -]?token|secret|password)"
    r"\s*[\"']?\s*[:=]\s*(?:bearer\s+)?[\"']?)([^\s,;\"']+)"
)
_ENV_SECRET_VALUE = re.compile(
    r"(?i)([A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*)"
    r"([\"']?\s*=\s*[\"']?)([^\s,;\"']+)"
)

def redact(text: str) -> str:
    """Redact credential-shaped values before evidence leaves the subprocess."""
    text = _SECRET_VALUE.sub(r"\1[REDACTED]", text or "")
    text = _ENV_SECRET_VALUE.sub(r"\1\2[REDACTED]", text)
    # Providers sometimes print a raw secret without its environment-variable
    # name. Only replace long values from explicitly secret-shaped variables.
    for name, value in os.environ.items():
        if (
            value
            and len(value) >= 8
            and re.search(r"(?i)(?:API_KEY|TOKEN|SECRET|PASSWORD|AUTH)", name)
        ):
            text = text.replace(value, "[REDACTED]")
    return text


class OpencodeError(Exception):
    """A classified backend failure (failure_class per IF-003 §1a)."""

    def __init__(
        self,
        failure_class: str,
        message: str,
        exit_code: int | None = None,
        stderr: str = "",
        stdout: str = "",
    ):
        super().__init__(message)
        self.failure_class = failure_class
        self.exit_code = exit_code
        self.stderr = stderr
        self.stdout = stdout

