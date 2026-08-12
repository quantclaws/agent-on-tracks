"""Backward-compatible re-export of the neutral version-capability helpers.

The canonical implementation lives in ``tracks.capabilities`` (neutral: it
imports nothing from ``tracks.executor`` or ``tracks.effects``, keeping the
effects boundary acyclic). This module stays as a compatibility alias so
existing importers keep working unchanged.
"""
from __future__ import annotations

from tracks.capabilities import (
    _VERSION_RE,
    M_IMPL_FEATURE_VERSION,
    _version_tuple,
    supports_m_impl,
    version_at_least,
)

__all__ = [
    "M_IMPL_FEATURE_VERSION",
    "_VERSION_RE",
    "_version_tuple",
    "supports_m_impl",
    "version_at_least",
]
