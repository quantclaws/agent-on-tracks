"""Generic version-capability composition seam (architecture §1.0.9).

One language-neutral, version-neutral lookup: extensions register their
capabilities per project version and consumers resolve a named capability for
a target version. The seam owns registration, resolution order and the
"unknown or missing callback blocks" rule -- it carries no Phase 0, mutation,
host-language or candidate-bound business conclusions.

Early versions simply select no extension for their target (FR-0264-02
wiring): resolving returns ``None`` instead of another version's handler, so
classic behaviour stays byte-identical while a v0.7 assembly registers
alongside.
"""

from __future__ import annotations

__all__ = ["CapabilityBlockedError", "register_extension", "resolve_capability"]

_EXTENSIONS: dict[str, object] = {}


class CapabilityBlockedError(RuntimeError):
    """A registered extension does not provide the requested capability."""


def register_extension(version: str, extension: object) -> None:
    """Bind *extension* as the composition root providing *version*'s callbacks.

    Re-registering the same *version* replaces its previous binding, which
    keeps repeated composition-root imports idempotent.
    """
    _EXTENSIONS[version] = extension


def resolve_capability(version: str, capability: str):
    """Return the callback bound to *capability* on *version*'s extension.

    Returns ``None`` when no extension is registered for *version*: early
    versions select no extension rather than inheriting some other version's
    handler. Raises :class:`CapabilityBlockedError` when an extension exists
    but lacks the requested callback -- including entirely unknown capability
    names -- so a missing piece blocks fail-closed instead of silently
    degrading (§1.0.9; §1h: no fallback to pseudo-success).
    """
    extension = _EXTENSIONS.get(version)
    if extension is None:
        return None
    try:
        return getattr(extension, capability)
    except AttributeError as exc:
        raise CapabilityBlockedError(
            f"extension for {version} blocks missing or unknown capability "
            f"{capability!r} (architecture §1.0.9)"
        ) from exc
