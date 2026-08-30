"""OOB exemption for the M-TEST must-be-red doctrine (2026-08-27 gap).

Operator emergency fixes ship with regression tests that are necessarily
green-on-arrival; files carried by oob.accepted declarations classify as
oob_verified (legal) instead of unexpected_pass. Regression for run
01M0S0FQ M-TEST park, where 3 Shield rewrite rounds burned the budget on
16 operator-green tests the machinery had no channel to accept.
"""

from __future__ import annotations

from types import SimpleNamespace

from tracks.executor.executor import Executor


def _case(status: str, detail: str = "") -> SimpleNamespace:
    return SimpleNamespace(status=status, detail=detail)


def test_oob_declared_file_green_node_classifies_oob_verified():
    """A passing node in an OOB-declared file is legal green-on-arrival."""
    nodes = [
        "tests/unit/test_cli_approval.py::test_a",
        "tests/unit/test_cli_approval.py::test_b",
    ]
    mapping = {n: _case("passed") for n in nodes}
    outcomes: list[dict] = []
    findings: list[dict] = []
    legit = Executor._record_layer_outcomes(
        nodes, mapping, outcomes, findings, {"tests/unit/test_cli_approval.py"}
    )
    assert legit, "oob_verified must count as legal for the red check"
    assert {o["classification"] for o in outcomes} == {"oob_verified"}
    assert {f["classification"] for f in findings} == {"oob_verified"}


def test_undeclared_green_node_still_unexpected_pass():
    """Without a declaration the doctrine is unchanged: green = defect."""
    nodes = ["tests/unit/test_plain.py::test_a"]
    mapping = {nodes[0]: _case("passed")}
    outcomes, findings = [], []
    legit = Executor._record_layer_outcomes(nodes, mapping, outcomes, findings, set())
    assert not legit
    assert outcomes[0]["classification"] == "unexpected_pass"


def test_oob_declaration_does_not_launder_legit_red_or_other_files():
    """The exemption is file-scoped and only softens unexpected_pass: a red
    node in a declared file keeps its own classification, and a green node
    in an UNdeclared file stays unexpected_pass even when another file is
    declared."""
    declared = "tests/unit/test_a.py"
    other = "tests/integration/test_b.py"
    nodes = [
        f"{declared}::test_red",
        f"{other}::test_green",
    ]
    mapping = {
        nodes[0]: _case("failed", "AssertionError: boom"),
        nodes[1]: _case("passed"),
    }
    outcomes, findings = [], []
    legit = Executor._record_layer_outcomes(nodes, mapping, outcomes, findings, {declared})
    assert not legit, "the undeclared green node must still fail the layer"
    by_node = {o["node"]: o["classification"] for o in outcomes}
    assert by_node[nodes[0]] == "assertion_failure"
    assert by_node[nodes[1]] == "unexpected_pass"
