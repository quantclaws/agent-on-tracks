"""T-035 RED (parity-face hardening round): direct pins for the
check_envelope_parity contract corners (IF-ENVELOPE-002, FR-0279/NFR-0145).

The envelope module face is implemented and heavily pinned (test_envelope.py
parse taxonomy, writer authority schemas, schema digest, the staged/default/
mismatch parity trio); the corners below were previously reachable only
partially. They pin the dispatch-parity contract directly:

- the COMPLETE documented six-face map (prompt/agent/skill/fake_backend/
  real_backend/validator, all declaring the current version) is consistent
  with zero mismatches — the pass face the dispatch gate requires;
- a face carrying a garbled (non-token) value is reason=invalid, never a
  silent pass (fail-closed);
- dynamic per-artifact faces (agent:<Name>/skill:<name>) are enforced by
  explicit name (the docstring's internal dynamic mode);
- the PARITY_STAGED legacy opt-out still blocks an explicitly DECLARED
  mismatched version — staged permissiveness only covers undeclared faces.
"""

from __future__ import annotations

from tracks.kernel.envelope import (
    DEFAULT_PARITY_FACES,
    ENVELOPE_PROTOCOL,
    ENVELOPE_VERSION,
    PARITY_STAGED,
    check_envelope_parity,
)

_V2 = f"{ENVELOPE_PROTOCOL}:v{ENVELOPE_VERSION}"


def test_parity_complete_six_face_map_is_consistent():
    """The dispatch gate's pass face: all six documented identities declare
    the current envelope version -> consistent, zero mismatches."""
    verdict = check_envelope_parity(dict.fromkeys(DEFAULT_PARITY_FACES, _V2))
    assert verdict["consistent"] is True, (
        f"assertion failure: a complete six-face v2 map must be consistent, "
        f"got {verdict!r}"
    )
    assert verdict["mismatches"] == [], (
        f"assertion failure: no mismatch may be reported on the pass face, "
        f"got {verdict['mismatches']!r}"
    )


def test_parity_garbled_face_is_invalid_not_missing():
    """A face whose value is present but not a valid version token is
    reason=invalid (garbled frontmatter) — fail-closed, never skipped."""
    verdict = check_envelope_parity(
        {"prompt": "garbage-token"}, required_faces=["prompt"]
    )
    assert verdict["consistent"] is False, (
        f"assertion failure: a garbled face must block, got {verdict!r}"
    )
    assert verdict["mismatches"][0]["reason"] == "invalid", (
        f"assertion failure: the garbled face reason must be invalid, "
        f"got {verdict['mismatches']!r}"
    )


def test_parity_dynamic_artifact_faces_enforced_by_name():
    """Internal dynamic artifact maps carry distinct per-artifact face names
    (agent:<Name>/skill:<name>); an explicit face sequence enforces exactly
    those names (check_envelope_parity docstring contract)."""
    verdict = check_envelope_parity(
        {"agent:Devon": _V2, "skill:tracks-devon-rgr": _V2},
        required_faces=["agent:Devon", "skill:tracks-devon-rgr"],
    )
    assert verdict["consistent"] is True, (
        f"assertion failure: declared dynamic artifact faces at the current "
        f"version must be consistent, got {verdict!r}"
    )
    partial = check_envelope_parity(
        {"agent:Devon": _V2}, required_faces=["agent:Devon", "skill:tracks-devon-rgr"]
    )
    assert partial["consistent"] is False, (
        f"assertion failure: a missing dynamic artifact face must block, "
        f"got {partial!r}"
    )


def test_parity_staged_opt_out_still_blocks_declared_mismatch():
    """PARITY_STAGED is the EXPLICIT legacy opt-out: undeclared faces are
    skipped, but a face that DECLARES a different version still blocks
    (permissiveness is never the default; staged rollout semantics)."""
    verdict = check_envelope_parity(
        {"assignment": _V2, "backend": f"{ENVELOPE_PROTOCOL}:v1"},
        required_faces=PARITY_STAGED,
    )
    assert verdict["consistent"] is False, (
        f"assertion failure: a staged map with a declared version mismatch "
        f"must block, got {verdict!r}"
    )
    mismatch = verdict["mismatches"][0]
    assert mismatch["face"] == "backend" and mismatch["reason"] == "version", (
        f"assertion failure: the declared mismatch must name the face and the "
        f"version reason, got {verdict['mismatches']!r}"
    )
