"""T-002 RED: readiness aggregate seam (IF-PROJ-001).

Second RED slice for the readiness task. The four probes themselves
(``run_readiness``) are delivered and green; what is still missing is the
aggregate seam the service/API layer needs to emit
``project.readiness_checked`` (interfaces §1a#5): the ``{ok, checks}``
payload body where ``checks`` maps each closed probe kind to its
``{ok, reason}`` pair. No such helper exists on
``tracks.supervisor.readiness`` yet, so the check_readiness composition
(architecture §1.2: four probes -> project.readiness_checked(ok,checks))
has no unit-pinned pure seam.

M-IMPL RED discipline: failures must classify as assertion_failure (no
stub_token, no assembly errors). Each node below first asserts the seam
exists, so a missing seam fails as an assertion — never as a collection
or import error. Nothing here mocks the system under test.

AC: FR-0290 — TRACKS-TRACE IF-PROJ-001.
"""

from __future__ import annotations

from tracks.supervisor import readiness as readiness_mod
from tracks.supervisor.readiness import PROBE_KINDS, ProbeResult


def _seam():
    fn = getattr(readiness_mod, "summarize_readiness", None)
    assert fn is not None, "readiness must expose summarize_readiness aggregate seam"
    return fn


def _ok_probes() -> list:
    return [ProbeResult(check=kind, ok=True, reason=None) for kind in PROBE_KINDS]


# AC-FR0290-01@v0.9 TRACKS-TRACE IF-PROJ-001 readiness aggregate seam exists
def test_summarize_readiness_seam_exists():
    assert callable(_seam())


# AC-FR0290-01@v0.9 TRACKS-TRACE IF-PROJ-001 all-ok probes summarize to ok
def test_summarize_all_ok_reports_ok_with_checks():
    summary = _seam()(_ok_probes())
    assert summary["ok"] is True
    assert set(summary["checks"]) == set(PROBE_KINDS)
    for kind in PROBE_KINDS:
        assert summary["checks"][kind] == {"ok": True, "reason": None}


# AC-FR0290-02@v0.9 TRACKS-TRACE IF-PROJ-001 failing probe blocks aggregate
def test_summarize_failing_probe_reports_not_ok_with_reason():
    probes = _ok_probes()
    probes[0] = ProbeResult(check="tools", ok=False, reason="missing tools: git")
    summary = _seam()(probes)
    assert summary["ok"] is False
    assert summary["checks"]["tools"] == {"ok": False, "reason": "missing tools: git"}
    assert summary["checks"]["contract"] == {"ok": True, "reason": None}


# AC-FR0290-01@v0.9 TRACKS-TRACE IF-PROJ-001 aggregate shape is closed
def test_summarize_shape_is_closed_ok_and_checks():
    summary = _seam()(_ok_probes())
    assert set(summary) == {"ok", "checks"}
    assert isinstance(summary["ok"], bool)
    for kind, pair in summary["checks"].items():
        assert kind in PROBE_KINDS
        assert set(pair) == {"ok", "reason"}
        assert isinstance(pair["ok"], bool)
