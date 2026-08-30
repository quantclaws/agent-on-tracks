"""IF-CLOSURE-001 RED: generic version-capability composition seam.

Architecture §1.0.9: ``cli.main``/``Executor``/``kernel.machine``/
``m_impl_runtime`` own one language-neutral, version-neutral capability seam
that resolves the available extension for a target version and invokes its
capabilities (``before_mtest``, event reducer / status-report renderer, trace
checker, ``island_gate_2`` callback). The seam owns registration, call order
and "unknown/missing callback blocks" -- it MUST NOT bake in Phase 0,
mutation, host-language or candidate-bound conclusions. Early versions
(v0.4/v0.6) simply select no v0.7 extension, keeping their classic behaviour
(FR-0264-02 wiring).

RED note (T-013): the seam module does not exist until GREEN delivers
``tracks.executor.version_extensions``. Every symbol is reached through a
function-body ``importlib`` call whose absence is normalized into an
``AssertionError`` carrying the contract token (IF-CLOSURE-001 /
architecture §1.0.9) -- a legal, uniform assertion_failure red that never
surfaces as an illegal collection/import or bare ``ModuleNotFoundError``.
"""

from __future__ import annotations

import importlib

_SEAM_TOKEN = (
    "IF-CLOSURE-001/architecture §1.0.9 version capability seam not delivered: "
    "tracks.executor.version_extensions"
)


def _seam():
    """Import the seam lazily; absence fails on the §1.0.9 contract token."""
    try:
        return importlib.import_module("tracks.executor.version_extensions")
    except ModuleNotFoundError as exc:
        raise AssertionError(f"{_SEAM_TOKEN} ({exc})") from exc


class _Ext:
    """Minimal stand-in extension: keyword attrs become its capabilities."""

    def __init__(self, **capabilities):
        self._capabilities = capabilities

    def __getattr__(self, name):
        try:
            return self._capabilities[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


# Arch §1.0.9: registering an extension for a version lets consumers resolve
# that extension's callback for a named capability.
def test_registered_capability_resolves_and_invokes():
    seam = _seam()
    probe = object()
    seam.register_extension("v0.7-unit-resolves", _Ext(island_gate_2=lambda *a, **k: probe))
    callback = seam.resolve_capability("v0.7-unit-resolves", "island_gate_2")
    assert callable(callback)
    assert callback("any", args=True) is probe


# Arch §1.0.9 / FR-0264-02: an unregistered (early) version selects no
# extension -- resolving yields nothing rather than another version's handler.
def test_unregistered_version_resolves_no_extension():
    seam = _seam()
    seam.register_extension("v0.7-unit-unreg", _Ext(trace=lambda: "v07-only"))
    assert seam.resolve_capability("v0.6-unit-unreg", "trace") is None


# Arch §1.0.9: "未知/缺 callback 即阻断" -- a registered extension lacking the
# requested capability blocks instead of silently degrading.
def test_missing_callback_on_extension_blocks():
    import pytest

    seam = _seam()
    seam.register_extension("v0.7-unit-missing-cb", _Ext(before_mtest=lambda: "present"))
    with pytest.raises(seam.CapabilityBlockedError):
        seam.resolve_capability("v0.7-unit-missing-cb", "island_gate_2")


# Arch §1.0.9: an entirely unknown capability name is likewise blocked.
def test_unknown_capability_name_blocks():
    import pytest

    seam = _seam()
    seam.register_extension("v0.7-unit-unknown-cap", _Ext(trace=lambda: None))
    with pytest.raises(seam.CapabilityBlockedError):
        seam.resolve_capability("v0.7-unit-unknown-cap", "no_such_capability")


# Arch §1.0.9: the seam is generic -- registrations never leak across versions.
def test_registrations_are_version_scoped():
    seam = _seam()
    seam.register_extension("v0.7-unit-scoped-a", _Ext(trace=lambda: "a"))
    seam.register_extension("v0.8-unit-scoped", _Ext(trace=lambda: "b"))
    assert seam.resolve_capability("v0.8-unit-scoped", "trace")() == "b"
    assert seam.resolve_capability("v0.7-unit-scoped-a", "trace")() == "a"
