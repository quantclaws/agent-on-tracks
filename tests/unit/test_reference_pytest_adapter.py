"""IF-ADAPTER-002 reference pytest adapter (T-002 RED unit).

Pins the reference adapter's v0.6-equivalent behavior declared in
interfaces.md §1h *before* the GREEN implementation lands.  The reference
adapter is the first concrete ``Adapter``: it must reproduce collect /
``{nodes}``+``{result}`` expansion / JUnit-normalized exact-coverage result
with the SAME semantics as the pre-v0.7 pytest path (AC-FR0264-02), and fail
closed on malformed/inexact results (AC-FR0264-03).

Each assertion fails today because every reference-adapter function is a
``NotImplementedError("IF-ADAPTER-002")`` stub -- the M-IMPL RED on the
contract.
"""

from __future__ import annotations

import pytest

from tracks.adapters.base import (
    TEST_RESULT_PROTOCOL,
    TEST_RESULT_PROTOCOL_VERSION,
    AdapterResultError,
    TestNode,
    TestRunResult,
)
from tracks.adapters.reference_pytest import (
    ReferencePytestAdapter,
    collect_reference_nodes,
    normalize_reference_result,
    resolve_reference_command,
)

# AC-FR0264-02@v0.7 TRACKS-TRACE reference adapter identity is auditable

def test_reference_adapter_identity_is_auditable():
    adapter = ReferencePytestAdapter()
    assert adapter.adapter_id == "reference-pytest"
    assert adapter.protocol == TEST_RESULT_PROTOCOL
    assert adapter.protocol_version == TEST_RESULT_PROTOCOL_VERSION


# -- resolve/reference command: {nodes}/{result} expansion (v0.6 parity) -------

def test_resolve_reference_command_expands_nodes_and_result(tmp_path):
    nodes = ["z/node.py::test_b", "a/node.py::test_a"]
    result_path = tmp_path / "res.xml"
    argv = resolve_reference_command(
        ".venv/bin/python -m pytest {nodes} -q --junitxml={result}",
        nodes,
        result_path,
        tmp_path,
    )
    # Ordered (sorted) nodes joined with shlex quotes; {result} substituted.
    expanded = " ".join(str(a) for a in argv[1:-1])
    assert "a/node.py::test_a" in expanded
    assert "z/node.py::test_b" in expanded
    assert str(result_path) in " ".join(argv)
    assert argv[0].endswith("python")


# AC-FR0264-03/T-002: missing {nodes} or {result} placeholder is a defect
def test_resolve_reference_command_requires_result_placeholder(tmp_path):
    src = ".venv/bin/python -m pytest {nodes}"
    with pytest.raises(ValueError):
        resolve_reference_command(
            src, ["node/::test_x"], tmp_path / "res.xml", tmp_path
        )


# -- normalize: exact node coverage over selected (v0.6 norm) --------------

def _junit_xml(*case_tags) -> str:
    """Build a JUnit XML body with one testcase child tag per entry.

    Each case is (name, status, message) where ``status`` is a status label
    canonicalized to its JUnit-standard element: ``failed`` -> ``<failure>``,
    ``error`` -> ``<error>``, ``skipped`` -> ``<skipped>``; ``passed`` emits
    no child element. The v0.6 parser (via ``normalize_reference_result``)
    maps exactly <failure>/<error>/<skipped> to statuses, so emitting a
    non-standard tag like <failed> would silently yield ``passed``."""
    cases = []
    for name, status, message in case_tags:
        if status == "passed":
            cases.append(f'<testcase name="{name}"/>')
        else:
            # Canonicalize the semantic status to its JUnit-standard element.
            tag = {"failed": "failure"}.get(status, status)
            detail = f' message="{message}"' if message else ""
            cases.append(f'<testcase name="{name}"><{tag}{detail}/></testcase>')
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<testsuite name="pytest"><testsuite>'
        + "".join(cases)
        + "</testsuite></testsuite>"
    )


def test_normalize_reference_result_maps_exact_coverage(tmp_path):
    selected = ["t1", "t2"]
    path = tmp_path / "res.xml"
    path.write_text(
        _junit_xml(
            ("t1", "passed", None),
            # status label "failed" is canonicalized by the fixture to the
            # JUnit-standard <failure> element; the parser maps it to failed.
            ("t2", "failed", "assert 1 == 2"),
        ),
        encoding="utf-8",
    )
    results = normalize_reference_result(path, selected)
    assert [r.node_id for r in results] == selected
    by_status = {r.node_id: r.status for r in results}
    assert by_status["t1"] == "passed"
    assert by_status["t2"] == "failed"
    assert all(isinstance(r, TestRunResult) for r in results)


def test_normalize_reference_result_reports_skipped_and_error(tmp_path):
    selected = ["s", "e"]
    path = tmp_path / "res.xml"
    path.write_text(
        _junit_xml(
            ("s", "skipped", "not supported"),
            ("e", "error", "setup boom"),
        ),
        encoding="utf-8",
    )
    results = normalize_reference_result(path, selected)
    by_status = {r.node_id: r.status for r in results}
    assert by_status["s"] == "skipped"
    assert by_status["e"] == "error"


def test_normalize_reference_result_fails_closed_on_inexact_coverage(tmp_path):
    selected = ["selected"]
    path = tmp_path / "res.xml"
    path.write_text(
        _junit_xml(("other", "passed", None)), encoding="utf-8"
    )
    # AC-FR0264-03: inexact coverage (unselected or missing nodes) is a
    # malformed result the adapter must reject, not silently accept.
    with pytest.raises(AdapterResultError):
        normalize_reference_result(path, selected)


def test_normalize_reference_result_fails_closed_on_malformed(tmp_path):
    path = tmp_path / "res.xml"
    path.write_text("<not-valid-xml", encoding="utf-8")
    with pytest.raises(AdapterResultError):
        normalize_reference_result(path, selected=("x",))


def test_normalize_reference_result_fails_closed_on_missing_file(tmp_path):
    with pytest.raises(AdapterResultError):
        normalize_reference_result(tmp_path / "missing.xml", ("x",))


# -- collect: pytest --collect-only -q -> TestNode list (with layer+digest) --

def test_collect_reference_nodes_parses_collected_stdout(tmp_path, monkeypatch):
    # The adapter should re-use the v0.6 --collect-only parse (one node per
    # line with a :: separator) into TestNode(node_id, layer, source_digest).
    # Pinned without shelling out: a fake collect command result yields the
    # exact node set and per-node source digests (AC-FR0264-02).
    import subprocess

    collected = (
        "tests/unit/test_x.py::test_a\n"
        "tests/unit/test_y.py::test_b\n"
        "1 test collected\n"
    )
    fake = type(
        "Proc",
        (),
        {"returncode": 0, "stdout": collected, "stderr": ""},
    )()

    def fake_run(*args, **kwargs):
        assert args[0][0].endswith("python")  # argv0 resolved vs cwd
        return fake

    monkeypatch.setattr(subprocess, "run", fake_run)
    (tmp_path / "tests" / "unit").mkdir(parents=True)
    (tmp_path / "tests" / "unit" / "test_x.py").write_text(
        "def test_a():\n    pass\n", encoding="utf-8"
    )
    (tmp_path / "tests" / "unit" / "test_y.py").write_text(
        "def test_b():\n    pass\n", encoding="utf-8"
    )

    nodes = collect_reference_nodes(
        ".venv/bin/python -m pytest --collect-only -q tests/unit/",
        "unit",
        tmp_path,
    )
    assert len(nodes) == 2
    assert {n.layer for n in nodes} == {"unit"}
    assert {n.node_id for n in nodes} == {
        "tests/unit/test_x.py::test_a",
        "tests/unit/test_y.py::test_b",
    }
    assert all(len(n.source_digest) == 64 for n in nodes)
    assert all(isinstance(n, TestNode) for n in nodes)


# -- the concrete adapter instantiates and its methods delegate --------------

def test_adapter_methods_are_concrete(tmp_path):
    adapter = ReferencePytestAdapter()
    collect = adapter.collect
    run = adapter.run_selected
    norm = adapter.normalize_result
    # callable seams exist (would raise only because the impl is a stub)
    assert callable(collect) and callable(run) and callable(norm)
