"""T-002 RED: readiness four-probe contract (IF-PROJ-001 / FR-0290).

Unit pins for the readiness slice this task owns
(``tracks/supervisor/readiness.py``):

- the four closed probe kinds (contract, harness_model, credentials_ref,
  tools) each reporting {ok, reason} (interfaces 1a#5, AC-FR0290-01);
- an unready probe carries a concrete non-empty reason and the aggregated
  summary is not-ready, so create_run can refuse with that reason instead
  of creating a fake run (AC-FR0290-02);
- the summary matches the ``project.readiness_checked`` payload body shape
  (``{ok, checks: {kind: {ok, reason}}}``) and the blocking message names
  the failing kind with its concrete reason.

Assertions are contract-level only: closed kinds, ok/reason discipline,
aggregation shape, and reason propagation. No assertion depends on
implementation-chosen reason wordings beyond non-emptiness, and env
sensitive probes (harness/credentials/tools) are only asserted for shape
and discipline — never for their ambient outcome.

M-IMPL RED discipline: failures must classify as assertion_failure or
symbol_missing (no assembly errors, no pytest.fail guards). Nothing here
mocks the system under test; probes run against real filesystem paths.

AC: FR-0290 — TRACKS-TRACE IF-PROJ-001.
"""

from __future__ import annotations

from pathlib import Path

from tracks.supervisor.readiness import (
    PROBE_KINDS,
    ProbeResult,
    blocking_reason,
    run_readiness,
    summarize_readiness,
)

_CLOSED_KINDS = ("contract", "harness_model", "credentials_ref", "tools")
_CONTRACT_RELPATH = Path(".tracks") / "projects" / "project.toml"


def _probe(check: str, ok: bool, reason: str | None = None) -> ProbeResult:
    return ProbeResult(check=check, ok=ok, reason=reason)


def _all_ok() -> list:
    return [_probe(kind, True) for kind in _CLOSED_KINDS]


def _tools_broken() -> list:
    results = _all_ok()
    results[_CLOSED_KINDS.index("tools")] = _probe("tools", False, "wrench gone")
    return results


def _contract_repo(base: Path) -> Path:
    contract = base / "contract-repo"
    contract.mkdir()
    target = contract / _CONTRACT_RELPATH
    target.parent.mkdir(parents=True)
    target.write_text("[unit]\n[integration]\n", encoding="utf-8")
    return contract


# AC-FR0290-01@v0.9 TRACKS-TRACE IF-PROJ-001 closed probe kind set
def test_probe_kinds_closed_set():
    assert PROBE_KINDS == _CLOSED_KINDS


# AC-FR0290-01@v0.9 TRACKS-TRACE IF-PROJ-001 four probes, each ok+reason
def test_run_readiness_covers_closed_kinds(tmp_path: Path):
    results = run_readiness(tmp_path)
    assert isinstance(results, list)
    assert {item.check for item in results} == set(_CLOSED_KINDS)
    for probe in results:
        assert isinstance(probe.ok, bool)
        assert probe.reason is None or isinstance(probe.reason, str)


# AC-FR0290-01@v0.9 TRACKS-TRACE IF-PROJ-001 per-probe reason discipline
def test_run_readiness_reason_discipline(tmp_path: Path):
    for probe in run_readiness(tmp_path):
        if probe.ok:
            assert probe.reason is None
        else:
            assert isinstance(probe.reason, str)
            assert probe.reason.strip() != ""


# AC-FR0290-01@v0.9 TRACKS-TRACE IF-PROJ-001 contract probe follows the
# architecture-declared contract file (.tracks/projects/project.toml with
# the [unit]/[integration] execution sections, architecture 4.1)
def test_contract_probe_bound_to_architecture_contract_file(tmp_path: Path):
    empty_repo = tmp_path / "empty-repo"
    empty_repo.mkdir()
    without = {p.check: p for p in run_readiness(empty_repo)}
    assert without["contract"].ok is False
    assert (without["contract"].reason or "").strip() != ""

    with_contract = {p.check: p for p in run_readiness(_contract_repo(tmp_path))}
    assert with_contract["contract"].ok is True
    assert with_contract["contract"].reason is None


# AC-FR0290-01@v0.9 TRACKS-TRACE IF-PROJ-001 summary body shape (1a#5)
def test_summarize_readiness_all_ok_shape():
    summary = summarize_readiness(_all_ok())
    assert set(summary) >= {"ok", "checks"}
    assert summary["ok"] is True
    assert set(summary["checks"]) == set(_CLOSED_KINDS)
    for kind in _CLOSED_KINDS:
        pair = summary["checks"][kind]
        assert pair["ok"] is True
        assert pair["reason"] is None


# AC-FR0290-02@v0.9 TRACKS-TRACE IF-PROJ-001 failure propagates into summary
def test_summarize_readiness_propagates_concrete_failure():
    summary = summarize_readiness(_tools_broken())
    assert summary["ok"] is False
    tools = summary["checks"]["tools"]
    assert tools["ok"] is False
    assert tools["reason"] == "wrench gone"
    for kind in set(_CLOSED_KINDS) - {"tools"}:
        assert summary["checks"][kind]["ok"] is True


# AC-FR0290-02@v0.9 TRACKS-TRACE IF-PROJ-001 unready repo is summarized
# not-ready with concrete reasons (no fake readiness). Only the
# filesystem-anchored contract probe outcome is asserted — the env-bound
# probes (harness/credentials/tools) vary with the host and are pinned
# for shape elsewhere in this module.
def test_unready_repo_summary_is_not_ready(tmp_path: Path):
    empty_repo = tmp_path / "empty-repo"
    empty_repo.mkdir()
    summary = summarize_readiness(run_readiness(empty_repo))
    assert summary["ok"] is False
    contract = summary["checks"]["contract"]
    assert contract["ok"] is False
    assert (contract["reason"] or "").strip() != ""


# AC-FR0290-02@v0.9 TRACKS-TRACE IF-PROJ-001 blocking message names the
# failing kind and carries the concrete reason; ready summaries unblock
def test_blocking_reason_names_failing_kind_and_reason():
    message = blocking_reason(summarize_readiness(_tools_broken()))
    assert isinstance(message, str)
    assert "tools" in message
    assert "wrench gone" in message
    assert blocking_reason(summarize_readiness(_all_ok())) is None


# AC-FR0290-02@v0.9 TRACKS-TRACE IF-PROJ-001 probes are repeatable so a
# fixed environment can re-check into readiness (修复后重查转就绪)
def test_run_readiness_deterministic_on_same_repo(tmp_path: Path):
    repo = _contract_repo(tmp_path)
    first = [(p.check, p.ok, p.reason) for p in run_readiness(repo)]
    second = [(p.check, p.ok, p.reason) for p in run_readiness(repo)]
    assert first == second
