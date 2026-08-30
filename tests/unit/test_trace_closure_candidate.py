"""IF-CLOSURE-001 candidate-bound executable trace checker (FR-0265).

Pure-function core (`check_closure_candidate`) that connects the approved AC set
to the candidate-bound baseline evidence, mutation evidence and same-candidate
FULL pass, and reports per-AC records plus the closed set of hard errors.

Ground truth per interfaces.md §1i: the per-AC record carries ``ac / outlet /
nodes / baseline_evidence / mutation_evidence / candidate_digest /
full_pass_evidence / status`` and the top-level report carries ``status`` and
``closure="candidate-bound"``. The hard-error closed set is fixed to ``node_missing
| skip_xfail | identity_drift | control_failure | baseline_missing |
mutation_missing | full_pass_missing | foreign_candidate`` (AC-FR0265-01..03).

The same pure function backs ``trac check trace --version v0.7`` and the
ISLAND_GATE_2 exit gate (interfaces §1i: "同一函数供 CLI 与 ISLAND_GATE_2
调用").

RED note (T-013): the symbols are reached through the module object rather than
a collection-time top-level import, so until GREEN implements
``check_closure_candidate`` / ``CLOSURE_HARD_ERRORS`` every case fails as a
runtime ``AttributeError`` arising inside the test body -> a single legal
``symbol_missing`` classification (never an illegal collection/import error).
"""

import tracks.checks.trace as _trace

DIG = "sha256:" + "a" * 64
FOREIGN = "sha256:" + "b" * 64


def _evidence(
    ac="AC-FR0265-01",
    outlet="IF-CLOSURE-001",
    nodes=("opaque-node",),
    node_statuses=("passed",),
    control_statuses=("pass",),
    baseline_evidence="evidence-1",
    baseline_candidate=DIG,
    mutation_evidence="manifest-1",
    mutation_candidate=DIG,
    full_pass_evidence="evidence-full",
):
    return {
        "ac": ac,
        "outlet": outlet,
        "nodes": nodes,
        "node_statuses": node_statuses,
        "control_statuses": control_statuses,
        "baseline_evidence": baseline_evidence,
        "baseline_candidate": baseline_candidate,
        "mutation_evidence": mutation_evidence,
        "mutation_candidate": mutation_candidate,
        "full_pass_evidence": full_pass_evidence,
    }


def _check_closure(acs, candidate_digest, per_ac_evidence):
    """Delegate to ``check_closure_candidate`` (tracks.checks.trace).

    Until GREEN implements the pure checker the attribute access raises
    ``AttributeError`` inside this helper -> the calling test body fails on a
    legal ``symbol_missing`` (contract token IF-CLOSURE-001).
    """
    return _trace.check_closure_candidate(acs, candidate_digest, per_ac_evidence)


def _record(acs, candidate_digest, per_ac_evidence):
    return _check_closure(acs, candidate_digest, per_ac_evidence).records[0]


# AC-FR0265-01@v0.7 TRACKS-TRACE candidate-bound closure pass, closure=candidate-bound
def test_single_ac_candidate_bound_pass():
    """A fully evidenced AC (bound node, baseline, mutation, same-candidate
    FULL pass) closes candidate-bound with status=pass."""
    r = _check_closure(["AC-FR0265-01"], DIG, {"AC-FR0265-01": _evidence(ac="AC-FR0265-01")})
    assert r.status == "pass"
    assert r.closure == "candidate-bound"
    assert r.hard_errors == ()
    rec = r.records[0]
    assert rec.status == "pass"
    assert rec.candidate_digest == DIG


# AC-FR0265-1 TRACKS-TRACE record schema matches §1i fields
def test_record_carries_unofficial_fields():
    rec = _record(["AC-FR0265-01"], DIG, {"AC-FR0265-01": _evidence(ac="AC-FR0265-01")})
    assert rec.ac == "AC-FR0265-01"
    assert rec.outlet == "IF-CLOSURE-001"
    assert rec.nodes == ("opaque-node",)
    assert rec.baseline_evidence == "evidence-1"
    assert rec.mutation_evidence == "manifest-1"
    assert rec.full_pass_evidence == "evidence-full"


# AC-FR0265-1 TRACKS-TRACE all required ACs must pass for top-level pass
def test_all_required_acs_pass_yields_top_level_pass():
    acs = ["AC-FR0265-01", "AC-FR0265-02", "AC-FR0265-03"]
    evidences = {ac: _evidence(ac=ac) for ac in acs}
    r = _check_closure(acs, DIG, evidences)
    assert r.status == "pass"
    assert r.closure == "candidate-bound"
    assert all(rec.status == "pass" for rec in r.records)


# AC-FR0265-2 TRACKS-TRACE node missing breaks closure
def test_node_missing_hard_error():
    r = _check_closure(
        ["AC-FR0265-01"],
        DIG,
        {"AC-FR0265-01": _evidence(ac="AC-FR0265-01", nodes=(), node_statuses=())},
    )
    assert r.status == "fail"
    assert r.closure == "candidate-bound"
    assert "node_missing" in r.hard_errors


# AC-FR0265-2 TRACKS-TRACE skip/xfail is a hard error (not truly executed)
def test_skip_xfail_hard_error():
    for status in ("skipped", "xfail"):
        r = _check_closure(
            ["AC-FR0265-01"],
            DIG,
            {"AC-FR0265-01": _evidence(ac="AC-FR0265-01", node_statuses=(status,))},
        )
        assert r.status == "fail"
        assert "skip_xfail" in r.hard_errors


# AC-FR0265-2 TRACKS-TRACE identity drift blocks closure
def test_identity_drift_hard_error():
    # node statuses do not match selection identity (length inconsistency)
    r = _check_closure(
        ["AC-FR0265-01"],
        DIG,
        {"AC-FR0265-01": _evidence(ac="AC-FR0265-01", node_statuses=("passed", "failed"))},
    )
    assert r.status == "fail"
    assert "identity_drift" in r.hard_errors


# AC-FR0265-2 TRACKS-TRACE mutation control failure blocks closure
def test_control_failure_hard_error():
    r = _check_closure(
        ["AC-FR0265-01"],
        DIG,
        {
            "AC-FR0265-01": _evidence(
                ac="AC-FR0265-01",
                nodes=("target", "control"),
                node_statuses=("passed", "passed"),
                control_statuses=("pass", "fail"),
            )
        },
    )
    assert r.status == "fail"
    assert "control_failure" in r.hard_errors


# AC-FR0265-2 TRACKS-TRACE missing evidences are hard errors
def test_missing_evidences_hard_errors():
    for field, token in (
        ("baseline_evidence", "baseline_missing"),
        ("mutation_evidence", "mutation_missing"),
        ("full_pass_evidence", "full_pass_missing"),
    ):
        ev = _evidence(ac="AC-FR0265-01")
        ev[field] = None
        r = _check_closure(["AC-FR0265-01"], DIG, {"AC-FR0265-01": ev})
        assert r.status == "fail"
        assert token in r.hard_errors


# AC-FR0265-3 TRACKS-TRACE foreign candidate digest must fail closed
def test_foreign_candidate_hard_error():
    ev = _evidence(ac="AC-FR0265-01", mutation_candidate=FOREIGN)
    r = _check_closure(["AC-FR0265-01"], DIG, {"AC-FR0265-01": ev})
    assert r.status == "fail"
    assert "foreign_candidate" in r.hard_errors


# AC-FR0265-3 TRACKS-TRACE baseline foreign candidate also fails closed
def test_foreign_baseline_candidate_hard_error():
    ev = _evidence(ac="AC-FR0265-01", baseline_candidate=FOREIGN)
    r = _check_closure(["AC-FR0265-01"], DIG, {"AC-FR0265-01": ev})
    assert r.status == "fail"
    assert "foreign_candidate" in r.hard_errors


# §1i TRACKS-TRACE hard error set is a fixed closed set
def test_hard_error_closed_set():
    assert frozenset(
        {
            "node_missing",
            "skip_xfail",
            "identity_drift",
            "control_failure",
            "baseline_missing",
            "mutation_missing",
            "full_pass_missing",
            "foreign_candidate",
        }
    ) == _trace.CLOSURE_HARD_ERRORS


# AC-FR0265-2/3 TRACKS-TRACE fail keeps closure=candidate-bound
def test_fail_closure_always_candidate_bound():
    ev = _evidence(ac="AC-FR0265-01", nodes=(), node_statuses=())
    r = _check_closure(["AC-FR0265-01"], DIG, {"AC-FR0265-01": ev})
    assert r.closure == "candidate-bound"
    assert r.status == "fail"


# AC-FR0265-1 TRACKS-TRACE report is deterministic / stable
def test_closure_report_stable():
    evidences = {"AC-FR0265-01": _evidence(ac="AC-FR0265-01")}
    r1 = _check_closure(["AC-FR0265-01"], DIG, evidences)
    r2 = _check_closure(["AC-FR0265-01"], DIG, evidences)
    assert r1 == r2
    assert r1.records == r2.records
