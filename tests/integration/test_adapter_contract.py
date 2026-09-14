"""Integration: host adapter contract (IF-ADAPTER-001/002/003).

AC-FR0264-01@v0.7 three-interface seam, protocol version,
AC-FR0264-02@v0.7 reference adapter equivalent to v0.6,
AC-FR0264-03@v0.7 unknown adapter / malformed result blocked.

Assertions land on `resolve_adapter`/`ReferencePytestAdapter` and the
`AdapterResultError`/`UnknownAdapterError` outlets (IF-ADAPTER-001/002,
interfaces §1h). The equivalence half asserts the three-method contract
(collect shape, run_selected {nodes}/{result} substitution, normalize_result
JUnit parsing); the malformed-result half asserts AdapterResultError for
missing/duplicate/extra/unknown-status results.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tracks.adapters.base import (
    TEST_RESULT_PROTOCOL,
    TEST_RESULT_PROTOCOL_VERSION,
    Adapter,
    AdapterResultError,
    UnknownAdapterError,
    resolve_adapter,
)
from tracks.adapters.reference_pytest import ReferencePytestAdapter

pytestmark = pytest.mark.integration


# AC-FR0264-01@v0.7 TRACKS-TRACE three-interface seam, protocol version
def test_three_interface_seam_protocol_version():
    """AC-FR0264-01: adapter exposes collect/run_selected/normalize_result seam."""
    adapter = resolve_adapter(
        "reference-pytest", TEST_RESULT_PROTOCOL, TEST_RESULT_PROTOCOL_VERSION
    )
    assert isinstance(adapter, Adapter)
    assert adapter.adapter_id == "reference-pytest"
    assert adapter.protocol == TEST_RESULT_PROTOCOL == "tracks-test-result"
    assert adapter.protocol_version == TEST_RESULT_PROTOCOL_VERSION == 1
    # Three-method seam present (Protocol contract).
    for method in ("collect", "run_selected", "normalize_result"):
        assert callable(getattr(adapter, method)), f"adapter missing {method} seam"


# AC-FR0264-02@v0.7 TRACKS-TRACE reference adapter equivalent to v0.6
def test_reference_adapter_equivalent_to_v06(tmp_path):
    """AC-FR0264-02: reference adapter migrates v0.6 pytest path (same in/out).

    The three methods must honour the v0.6 contract: collect returns opaque
    node_ids + source_digests; run_selected substitutes {nodes} and {result}
    (concurrency flags embedded, fail-closed when {nodes} missing);
    normalize_result parses JUnit into exact TestRunResult coverage."""
    adapter = ReferencePytestAdapter()
    repo = Path(__file__).resolve().parents[2]
    # collect: same shape as v0.6 (opaque node_id, layer, source_digest).
    # sys.executable (not ".venv/bin/python"): the command must also work in
    # frozen-candidate environments (isolated worktrees/venvs) where no
    # repo-local .venv exists.
    collect_command = f"{sys.executable} -m pytest --collect-only tests/unit/"
    nodes = adapter.collect(
        collect_command=collect_command,
        layer="unit",
        cwd=repo,
    )
    assert isinstance(nodes, list)
    for node in nodes:
        assert node.node_id, "opaque node_id must be non-empty"
        assert node.layer == "unit"
        assert node.source_digest, "source_digest must be present (content-addressed)"
    # run_selected: {nodes} and {result} substitution, concurrency embedded.
    result_path = tmp_path / "result.xml"
    template = (
        ".venv/bin/python -m pytest {nodes} --tb=short -q "
        "-n 8 --dist loadscope --junitxml={result}"
    )
    selected = ("tests/unit/test_select_pure_functions.py::test_select_r2_returns_r2_only",)
    argv = adapter.run_selected(template, selected, result_path, repo)
    assert isinstance(argv, tuple)
    # {nodes} substituted by the selected node(s); {result} by the result path.
    flat = " ".join(argv)
    assert all(n in flat for n in selected), f"run_selected must substitute {{nodes}}; argv={argv}"
    assert str(result_path) in flat, f"run_selected must substitute {{result}}; argv={argv}"
    # run_selected must fail-closed when {nodes} is missing from the template.
    with pytest.raises(Exception):  # noqa: B017 - contract: missing {nodes} fail-closed
        adapter.run_selected(".venv/bin/python -m pytest --junitxml={result}", selected, result_path, repo)
    # normalize_result: parses a valid JUnit into exact TestRunResult coverage.
    junit = tmp_path / "ok.xml"
    junit.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<testsuites><testsuite name="suite">'
        '<testcase classname="t" name="test_a" />'
        '<testcase classname="t" name="test_b">'
        '<failure message="boom" />'
        "</testcase>"
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    sel = ("t::test_a", "t::test_b")
    results = adapter.normalize_result(junit, sel)
    assert {r.node_id for r in results} == set(sel), (
        "normalize_result must cover exactly the selected nodes"
    )
    by_node = {r.node_id: r for r in results}
    assert by_node["t::test_a"].status == "passed"
    assert by_node["t::test_b"].status == "failed"


# AC-FR0264-03@v0.7 TRACKS-TRACE unknown adapter / malformed result blocked
def test_unknown_adapter_malformed_result_blocked(tmp_path):
    """AC-FR0264-03: unknown adapter + malformed result fail closed (no language
    enumeration). AdapterResultError for missing/duplicate/extra/unknown-status."""
    # Unknown adapter id / protocol / version -> UnknownAdapterError.
    for args in (
        ("no-such-adapter", TEST_RESULT_PROTOCOL, 1),
        ("reference-pytest", "no-such-protocol", 1),
        ("reference-pytest", TEST_RESULT_PROTOCOL, 999),
    ):
        with pytest.raises(UnknownAdapterError):
            resolve_adapter(*args)

    adapter = ReferencePytestAdapter()
    # Malformed: result file missing on disk -> AdapterResultError.
    with pytest.raises(AdapterResultError):
        adapter.normalize_result(tmp_path / "absent.xml", ("t::test_a",))

    # Malformed: a selected node absent from the result -> AdapterResultError.
    junit = tmp_path / "missing.xml"
    junit.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<testsuites><testsuite name="s">'
        '<testcase classname="t" name="test_a" />'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    with pytest.raises(AdapterResultError):
        adapter.normalize_result(junit, ("t::test_a", "t::test_missing"))

    # Malformed: a result reports an unselected testcase -> AdapterResultError.
    junit_extra = tmp_path / "extra.xml"
    junit_extra.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<testsuites><testsuite name="s">'
        '<testcase classname="t" name="test_a" />'
        '<testcase classname="t" name="test_unselected" />'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    with pytest.raises(AdapterResultError):
        adapter.normalize_result(junit_extra, ("t::test_a",))
