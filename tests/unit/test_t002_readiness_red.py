"""T-002 RED: readiness four-probe contract (IF-PROJ-001).

Devon-owned unit RED for the readiness slice the task declares:
``tracks/supervisor/readiness.py`` (four closed probes per FR-0290,
interfaces §1a#5 / §2b#7). The failing nodes pin the IF-PROJ-001 contracts
this task must deliver:

- four closed probes (contract, harness_model, credentials_ref, tools)
  each reporting {ok, reason} (AC-FR0290-01);
- an unready probe carries a concrete non-empty reason and the overall
  result is not-ready so create_run can block with that reason instead of
  creating a fake run (AC-FR0290-02);
- reason discipline (ok => reason None, not-ok => non-empty reason) and
  determinism on the same repo.

M-IMPL RED discipline: failures must classify as assertion_failure (no
stub_token, no assembly errors). The stub guard below converts a
NotImplementedError("IF-PROJ-001")桩 into an explicit behavioral
assertion failure — "the stub must be replaced by behavior" — which doubles
as the regression pin against falling back to the stub. Nothing here mocks
the system under test; probes run against real filesystem paths.

AC: FR-0290 — TRACKS-TRACE IF-PROJ-001.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tracks.supervisor import readiness as readiness_mod
from tracks.supervisor.readiness import PROBE_KINDS, ProbeResult, run_readiness


def _by_check(results: list) -> dict:
    return {item.check: item for item in results}


def _call(repo: Path) -> list:
    try:
        return run_readiness(repo)
    except NotImplementedError as exc:
        pytest.fail(f"IF-PROJ-001 still stub: {exc} — probes must report behavior")


# AC-FR0290-01@v0.9 TRACKS-TRACE IF-PROJ-001 closed probe kinds
def test_probe_kinds_closed_set():
    assert PROBE_KINDS == ("contract", "harness_model", "credentials_ref", "tools")


# AC-FR0290-01@v0.9 TRACKS-TRACE IF-PROJ-001 four probes with ok and reason
def test_run_readiness_returns_four_probes_with_ok_reason(tmp_path: Path):
    results = _call(tmp_path)
    assert isinstance(results, list)
    assert len(results) == 4
    assert set(_by_check(results)) == set(PROBE_KINDS)
    for probe in results:
        assert isinstance(probe, ProbeResult)
        assert probe.check in PROBE_KINDS
        assert isinstance(probe.ok, bool)
        assert probe.reason is None or isinstance(probe.reason, str)


# AC-FR0290-01@v0.9 TRACKS-TRACE IF-PROJ-001 reason discipline per probe
def test_run_readiness_reason_discipline(tmp_path: Path):
    results = _call(tmp_path)
    for probe in results:
        if probe.ok:
            assert probe.reason is None
        else:
            assert isinstance(probe.reason, str)
            assert probe.reason.strip() != ""


# AC-FR0290-02@v0.9 TRACKS-TRACE IF-PROJ-001 unready repo reports concrete reason
def test_run_readiness_empty_repo_reports_unready_with_reason(tmp_path: Path):
    empty_repo = tmp_path / "empty-repo"
    empty_repo.mkdir()
    results = _call(empty_repo)
    assert len(results) == 4
    by_check = _by_check(results)
    assert by_check["contract"].ok is False
    assert isinstance(by_check["contract"].reason, str)
    assert by_check["contract"].reason.strip() != ""
    assert not all(probe.ok for probe in results)


# AC-FR0290-02@v0.9 TRACKS-TRACE IF-PROJ-001 deterministic on same repo
def test_run_readiness_is_deterministic(tmp_path: Path):
    first = _call(tmp_path)
    second = _call(tmp_path)
    assert [(p.check, p.ok, p.reason) for p in first] == [
        (p.check, p.ok, p.reason) for p in second
    ]
    assert readiness_mod.PROBE_KINDS == PROBE_KINDS
