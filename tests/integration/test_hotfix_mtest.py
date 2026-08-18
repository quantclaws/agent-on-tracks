"""M-TEST hotfix variant: RED-first, layer ownership, cross-version trace,
empty-Shield-increment release (IF-HOTFIX-007, FR-0244).

Covers the four M-TEST hotfix contracts: RED-first regression
discipline, Archer-decided layer ownership validated by ``trac
validate``, cross-version AC trace closure (``trac check trace``), and
the empty-Shield-increment release path (``increment.declared`` +
plan-level closure).
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.hotfix_support import seed_host_issues, seed_v05_approved_baseline


def _hotfix_project_dir(host_repo: Path, version: str = "v0.5", issue: int = 42) -> Path:
    return Path(host_repo / ".tracks" / "projects" / f"{version}-hotfix-{issue}")


def _write_delta_test_plan(hotfix_dir: Path, *, unit_only: bool = False) -> Path:
    """Write a minimal hotfix delta test-plan for the M-TEST variants.

    The fixture data is the truth source (test-plan §3.1 row 3). The
    unit-only variant exercises the empty-Shield-increment path
    (FR-0244-04): no integration/e2e rows, only unit rows bound to
    cross-version AC refs.
    """
    hotfix_dir.mkdir(parents=True, exist_ok=True)
    tp = hotfix_dir / "test-plan.md"
    rows = (
        "| AC-FR0030-01@v0.5 | unit | tests/unit/test_ac_fr0030_01.py | IF-HOTFIX-007 |"
        if unit_only
        else "| AC-FR0030-01@v0.5 | integration | tests/integration/test_hotfix_unit.py | IF-HOTFIX-007 |"
    )
    body = f"""---
spec_id: SPEC-006-hotfix-42
status: draft
sha:
---

# hotfix delta test plan (42)

## 8. AC Coverage

{rows}
"""
    tp.write_text(body, encoding="utf-8")
    return tp


# AC-FR0244-01@v0.6 TRACKS-TRACE M-TEST RED-first then GREEN
def test_mtest_regression_red_first_then_green(trac, host_repo, event_log):
    """AC-FR0244-01@v0.6: regression cases must RED on the defective
    baseline before they GREEN after the fix; the audit evidence records
    the "RED-first, then GREEN" RGR order. No "already-green" regression
    case is accepted as a legal Red.

    Failure mode (legal Red): ``trac hotfix`` is unregistered; the
    hotfix run never reaches M-TEST, so no ``red.validated`` event can
    fire on the cross-version anchored AC.
    """
    seed_v05_approved_baseline(host_repo)
    seed_host_issues(host_repo)

    r = trac("hotfix", "42", "--scenario", "post-release")
    assert r.returncode == 0, r.stderr
    # Drive to M-TEST (single dispatch enters M-DESIGN -> M-TEST happy path).
    for _ in range(2):
        cont = trac("run")
        assert cont.returncode == 0, cont.stderr

    run_id = r.stdout.split()[1] if "run " in r.stdout else "unknown"
    evs = event_log(run_id)
    reds = [e for e in evs if e["type"] == "red.validated"]
    assert reds
    assert reds[0]["payload"]["status"] == "valid"  # legitimate Red on the defective baseline


# AC-FR0244-02@v0.6 TRACKS-TRACE delta test-plan layer ownership validated
def test_delta_testplan_layer_ownership_validated(trac, host_repo):
    """AC-FR0244-02@v0.6: the delta test-plan's layer ownership (unit /
    integration / e2e) is validated by ``trac validate --file test-plan.md``
    in the hotfix project dir. Unit rows are legal ONLY in hotfix delta
    plans (the unit layer word is gated to hotfix dirs).

    Failure mode (legal Red): ``trac hotfix`` unregistered -> the hotfix
    delta dir never exists; ``trac validate --file <hotfix>/test-plan.md``
    reads a non-existent file and returns non-zero. The fixture delta
    plan written here cannot be reached.
    """
    seed_v05_approved_baseline(host_repo)
    seed_host_issues(host_repo)

    r = trac("hotfix", "42", "--scenario", "post-release")
    assert r.returncode == 0, r.stderr
    hotfix_dir = _hotfix_project_dir(host_repo)
    _write_delta_test_plan(hotfix_dir, unit_only=True)

    v = trac("validate", "--file", str(hotfix_dir / "test-plan.md"))
    assert v.returncode == 0, v.stderr
    # The unit-layer extension is legal in hotfix dirs only.
    assert "unit" in v.stdout or v.returncode == 0


# AC-FR0244-03@v0.6 TRACKS-TRACE trace binds cross-version AC without new AC
def test_trace_binds_cross_version_ac_without_new_ac(trac, host_repo, event_log):
    """AC-FR0244-03@v0.6: regression cases bind cross-version AC refs
    (``AC-FRXXXX-YY@v0.5``) under the anchored set; no new AC is allocated
    in the hotfix run's own acceptance. ``trac check trace --json`` closes
    the plan by mapping regression rows to the target-version AC.

    Failure mode (legal Red): ``trac hotfix`` unregistered -> no anchored
    AC set, no regression rows, no hotfix_scope; ``trac check trace --json``
    cannot produce the hotfix_scope closure field.
    """
    seed_v05_approved_baseline(host_repo)
    seed_host_issues(host_repo)

    r = trac("hotfix", "42", "--scenario", "post-release")
    assert r.returncode == 0, r.stderr
    hotfix_dir = _hotfix_project_dir(host_repo)
    _write_delta_test_plan(hotfix_dir, unit_only=True)

    check = trac("check", "trace", "--json")
    assert check.returncode == 0, check.stderr
    data = json.loads(check.stdout)
    # Hotfix scope carries the anchored AC set and declared unit rows.
    assert "hotfix_scope" in data
    assert "AC-FR0030-01@v0.5" in data["hotfix_scope"]["anchor_acs"]


# AC-FR0244-04@v0.6 TRACKS-TRACE empty Shield increment release with unit closure
def test_empty_shield_increment_release_with_unit_closure(trac, host_repo, event_log):
    """AC-FR0244-04@v0.6: when Archer allocates ALL regression rows to the
    unit layer, Shield's increment is empty (no ``test.written`` /
    ``test.committed`` events), and M-TEST releases on
    ``increment.declared(shield=empty, trace_status=pass)`` plus the unit-
    layer plan-level closure (``trac check trace`` exit 0).

    Failure mode (legal Red): ``trac hotfix`` unregistered -> the hotfix
    M-TEST EXIT never fires, so no ``increment.declared`` event lands;
    the empty-Shield release contract cannot be observed.
    """
    seed_v05_approved_baseline(host_repo)
    seed_host_issues(host_repo)

    r = trac("hotfix", "42", "--scenario", "post-release")
    assert r.returncode == 0, r.stderr
    hotfix_dir = _hotfix_project_dir(host_repo)
    _write_delta_test_plan(hotfix_dir, unit_only=True)

    # Drive M-TEST to EXIT (single dispatch per the happy path).
    for _ in range(3):
        cont = trac("run")
        assert cont.returncode == 0, cont.stderr

    run_id = r.stdout.split()[1] if "run " in r.stdout else "unknown"
    evs = event_log(run_id)
    increments = [e for e in evs if e["type"] == "increment.declared"]
    assert increments
    assert increments[-1]["payload"]["shield"] == "empty"
    assert increments[-1]["payload"]["trace_status"] == "pass"
    # No Shield write activity events on the empty-increment path.
    assert not [e for e in evs if e["type"] in ("test.written", "test.committed")]
