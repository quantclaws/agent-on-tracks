"""Deterministic version-capability helpers (shared, neutral).

The M-TEST -> M-IMPL stage transition and the Fake Shield/Devon machinery gate
capabilities on the flow version (IF-IMPL-001 / IF-SHIELD-001). This module is
the single neutral home for that predicate: it imports nothing from
``tracks.executor`` or ``tracks.effects``, so the effects boundary stays
acyclic (importing ``tracks.executor`` at module scope would run
``executor/__init__.py``, which imports ``tracks.effects`` back into this
package during import). ``tracks/executor/version.py`` re-exports these names
for backward compatibility.

Project versions follow the validated ``v<major>.<minor>`` convention (e.g.
``v0.1``, ``v0.4``, ``v0.5``, ``v0.10``). Lexicographic string comparison is
wrong here (``"v0.10" < "v0.5"``), so comparisons use numeric component
tuples. Malformed versions fail closed: they never unlock a v0.5+ capability
and keep the historical pre-v0.5 behavior.
"""

from __future__ import annotations

import re

_VERSION_RE = re.compile(r"^v(\d+)\.(\d+)$")

# v0.5 M-TEST -> M-IMPL: historical runs on versions strictly before this one
# complete at the M-TEST boundary instead of entering M-IMPL.
M_IMPL_FEATURE_VERSION = "v0.5"


def _version_tuple(version: str) -> tuple[int, int] | None:
    """Parse a project version into numeric ``(major, minor)``.

    Returns ``None`` for malformed/non-string input so callers fall back to
    the historical behavior deterministically.
    """
    if not isinstance(version, str):
        return None
    match = _VERSION_RE.match(version.strip())
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def version_at_least(version: str, minimum: str) -> bool:
    """True when ``version`` is at least ``minimum`` by numeric comparison.

    ``version_at_least("v0.5", "v0.5")`` and ``version_at_least("v0.10",
    "v0.5")`` are True; ``version_at_least("v0.4", "v0.5")`` is False. Any
    malformed operand returns False (fail closed).
    """
    got = _version_tuple(version)
    want = _version_tuple(minimum)
    if got is None or want is None:
        return False
    return got >= want


def supports_m_impl(version: str) -> bool:
    """True when ``version`` carries the M-IMPL capability (IF-IMPL-001).

    Runs on v0.5+ enter M-IMPL and get the behavioral Fake Shield/Devon
    machinery; pre-v0.5 and malformed versions fail closed to the historical
    M-TEST boundary behavior.
    """
    return version_at_least(version, M_IMPL_FEATURE_VERSION)
