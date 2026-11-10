"""A hotfix closes its approved anchors without losing version identity."""

from types import SimpleNamespace

import pytest

from tracks.checks.trace import check_closure_candidate, closure_acs
from tracks.cli.validate_cmd import _closure_evidence, _trace_v07_report
from tracks.executor.closure_evidence import ExecClosureEvidenceMixin
from tracks.store import Store

VERSION = "v0.12-hotfix-262"
AC = "AC-FR0001-01"
REFS = [f"{AC}@v0.8", f"{AC}@v0.9"]


def _scope(tmp_path):
    home = tmp_path / ".tracks"
    for version in ("v0.8", "v0.9"):
        target = home / "projects" / version / "acceptance.md"
        target.parent.mkdir(parents=True)
        target.write_text(f"# Acceptance\n\n### {AC}\n\n- visible outcome\n")
    return home


def _seed_run(home, *, increment=True):
    store = Store(home)
    for kind, payload in [
        ("hotfix.requested", {"issue": 262, "scenario": "post-release"}),
        ("stage.entered", {"stage": "M-VERIFY"}),
        ("anchor.validated", {"acs": REFS}),
        ("increment.declared", {"trace_status": "pass", "unit_rows": []}),
        ("candidate.frozen", {"candidate_sha": "candidate"}),
    ]:
        if kind == "increment.declared" and not increment:
            continue
        store.append("RUN", VERSION, kind, payload)
    return store


# AC-FR0265-01@v0.7 TRACKS-TRACE delta closure retains both approved source versions
def test_hotfix_scope_uses_approved_anchors_without_duplicate_acceptance(tmp_path):
    home = _scope(tmp_path)
    assert closure_acs(home / "projects", VERSION, REFS) == REFS
    assert not (home / "projects" / VERSION / "acceptance.md").exists()


# AC-FR0265-02@v0.7 TRACKS-TRACE missing scope and unresolved anchors fail closed
@pytest.mark.parametrize("refs", [None, [], [AC], [f"{AC}@v0.99"], ["AC-FR9999-01@v0.8"]])
def test_hotfix_scope_rejects_missing_or_unapproved_refs(tmp_path, refs):
    home = _scope(tmp_path)
    with pytest.raises(ValueError, match="hotfix_closure_"):
        closure_acs(home / "projects", VERSION, refs)


# AC-FR0265-02@v0.7 TRACKS-TRACE CLI reports one missing-evidence record per anchor
def test_cli_hotfix_report_does_not_vacuously_pass_zero_acs(tmp_path):
    home = _scope(tmp_path)
    store = _seed_run(home)
    store.close()
    report, _ = _trace_v07_report(home, VERSION, check_closure_candidate)
    assert report.status == "fail"
    assert [row.ac for row in report.records] == REFS
    assert "node_missing" in report.hard_errors


# AC-FR0265-01@v0.7 TRACKS-TRACE public CLI inherits the base capability on legacy deltas
@pytest.mark.parametrize("increment", [True, False])
def test_public_cli_uses_candidate_closure_for_hotfix(tmp_path, capsys, increment):
    from tracks.cli.main import cmd_check

    store = _seed_run(_scope(tmp_path), increment=increment)
    store.close()
    assert cmd_check(tmp_path, "trace", "--version", VERSION, "--json") == 1
    import json

    report = json.loads(capsys.readouterr().out)
    assert report["closure"] == "candidate-bound"
    assert [row["ac"] for row in report["records"]] == REFS
    assert "node_missing" in report["hard_errors"]


# AC-FR0265-02@v0.7 TRACKS-TRACE an unestablished delta has an explicit scope error
def test_cli_hotfix_without_approved_context_is_not_success(tmp_path):
    home = _scope(tmp_path)
    report, _ = _trace_v07_report(home, VERSION, check_closure_candidate)
    assert report.status == "fail"
    assert any("hotfix_closure_scope_missing" in error for error in report.hard_errors)


# AC-FR0265-01@v0.7 TRACKS-TRACE evidence from one source version cannot cover another
def test_harvest_keeps_qualified_anchor_identities_separate(tmp_path):
    store = _seed_run(_scope(tmp_path))
    for kind, payload in [
        ("phase0.baseline_repaired", {
            "ac": REFS[0], "evidence_id": "baseline",
            "bound_node": {"node_id": "tests/unit/test_one.py::test_one", "digest": "candidate"},
        }),
        ("mutation.manifest", {
            "ac": REFS[0], "status": "declared", "candidate_digest": "candidate",
            "patch_digest": "patch",
        }),
        ("mutation.manifest", {
            "ac": f"{AC}@v0.7", "status": "declared", "candidate_digest": "candidate",
            "patch_digest": "wrong-version",
        }),
    ]:
        store.append("RUN", VERSION, kind, payload)
    candidate, harvested = _closure_evidence(store, VERSION, REFS)
    assert candidate == "candidate"
    assert harvested[REFS[0]]["mutation_evidence"] == "patch"
    assert REFS[1] not in harvested
    store.append("RUN", VERSION, "mutation.manifest", {
        "ac": REFS[0], "status": "blocked", "candidate_digest": "candidate", "patch_digest": "bad",
    })
    _, harvested = _closure_evidence(store, VERSION, REFS)
    assert "mutation_evidence" not in harvested[REFS[0]]
    store.close()


# AC-FR0265-02@v0.7 TRACKS-TRACE interrupted and blocked experiments remain retryable
def test_existing_closure_requires_successful_manifest_and_baseline():
    events = []
    host = ExecClosureEvidenceMixin()
    host.run_id = "RUN"
    host.store = SimpleNamespace(events=lambda _: list(events))

    def emit(kind, **payload):
        events.append(SimpleNamespace(type=kind, payload={"candidate_digest": "candidate", **payload}))

    emit("mutation.manifest", ac=REFS[0], status="declared")
    assert host._closure_existing("candidate", REFS) == set()
    emit("phase0.baseline_repaired", ac=REFS[0], status="repaired")
    assert host._closure_existing("candidate", REFS) == {REFS[0]}
    emit("mutation.manifest", ac=REFS[0], status="blocked")
    emit("mutation.manifest", ac=f"{AC}@v0.7", status="declared")
    assert host._closure_existing("candidate", REFS) == set()
