"""T-002 RED: readiness not-ready blocking seam (IF-PROJ-001).

Third RED slice for the readiness task. The four probes (run_readiness)
and the aggregate seam (summarize_readiness) are delivered and green; what
is still missing is the not-ready blocking predicate the create_run
preflight needs (FR-0290 / AC-FR0290-02): given the readiness summary, say
whether run creation must be blocked — and when blocked, carry the concrete
failing-probe reasons so the caller can refuse with a reason instead of
creating a fake run. No such helper exists on
tracks.supervisor.readiness yet, so the blocking composition has no
unit-pinned pure seam.

M-IMPL RED discipline: failures must classify as assertion_failure (no
stub_token, no assembly errors). Each node first asserts the seam exists,
so a missing seam fails as an assertion — never as a collection or import
error. Nothing here mocks the system under test.

AC: FR-0290 — TRACKS-TRACE IF-PROJ-001.
"""

from __future__ import annotations

from tracks.supervisor import readiness as readiness_mod


def _seam():
    fn = getattr(readiness_mod, "blocking_reason", None)
    assert fn is not None, "readiness must expose blocking_reason seam"
    return fn


def _summary(*, tools_ok: bool = True) -> dict:
    checks = {
        "contract": {"ok": True, "reason": None},
        "harness_model": {"ok": True, "reason": None},
        "credentials_ref": {"ok": True, "reason": None},
        "tools": {"ok": tools_ok, "reason": None if tools_ok else "missing tools: git"},
    }
    return {"ok": all(pair["ok"] for pair in checks.values()), "checks": checks}


# AC-FR0290-01@v0.9 TRACKS-TRACE IF-PROJ-001 blocking seam exists
def test_blocking_reason_seam_exists():
    assert callable(_seam())


# AC-FR0290-01@v0.9 TRACKS-TRACE IF-PROJ-001 ready summary does not block
def test_blocking_reason_ready_returns_none():
    assert _seam()(_summary()) is None


# AC-FR0290-02@v0.9 TRACKS-TRACE IF-PROJ-001 not-ready blocks with reason
def test_blocking_reason_not_ready_reports_failing_probe():
    reason = _seam()(_summary(tools_ok=False))
    assert isinstance(reason, str)
    assert reason.strip() != ""
    assert "tools" in reason


# AC-FR0290-02@v0.9 TRACKS-TRACE IF-PROJ-001 blocking reason names the probe
def test_blocking_reason_names_every_failing_probe():
    summary = _summary(tools_ok=False)
    summary["checks"]["contract"] = {"ok": False, "reason": "project contract not found"}
    reason = _seam()(summary)
    assert "tools" in reason
    assert "contract" in reason
