"""Unit tests for quality gate layered execution (IF-IMPL-005).

classify_failure is tested for all eight categories; run_production_checks /
run_test_checks / run_gates are tested for path classification and layering.
T-03: run_gates performs real subprocess execution; the Python invocation must
go through the project `.venv` interpreter via shell=False, and every executed
command must be surfaced as an immutable observed execution (GateObservation).
"""

from __future__ import annotations

import dataclasses
import json
import os
import sys
from pathlib import Path

import pytest

from tracks.executor.quality_gate import (
    classify_failure,
    run_gates,
    run_production_checks,
    run_test_checks,
)


def test_classify_budget():
    assert classify_failure("budget", 1, "", "") == "budget"


def test_classify_scope():
    assert classify_failure("scope", 1, "", "") == "scope"


def test_classify_public_interface():
    assert classify_failure("public_interface_check", 1, "", "") == "public_interface"
    assert classify_failure("public-interface", 1, "", "") == "public_interface"


def test_classify_regression():
    assert classify_failure("historical_unit_tests", 1, "", "") == "regression"
    assert classify_failure("regression_suite", 1, "", "") == "regression"


def test_classify_collection():
    assert classify_failure("unit-tests", 1, "", "ImportError: No module") == "collection"
    assert classify_failure("unit-tests", 1, "", "SyntaxError: bad syntax") == "collection"
    assert classify_failure("unit-tests", 1, "ERROR collecting tests/x.py", "") == "collection"


def test_classify_infrastructure():
    assert classify_failure("ruff", 127, "", "command not found") == "infrastructure"
    assert classify_failure("ruff", 127, "", "No module named 'ruff'") == "infrastructure"


def test_classify_test_defect():
    out = "test_foo.py::test_bar\nE   assert False\n"
    assert classify_failure("unit-tests", 1, out, "") == "test"


def test_classify_implementation_default():
    assert (
        classify_failure(
            "ruff",
            1,
            "tracks/foo.py:1:1 F401 unused import",
            "",
        )
        == "implementation"
    )
    assert (
        classify_failure(
            "flake8-CCR001",
            1,
            "tracks/foo.py:10:1 CCR001 too complex",
            "",
        )
        == "implementation"
    )


def test_production_checks_empty_paths():
    result = run_production_checks("/tmp/repo", [])
    assert result.status == "pass"
    assert result.checks_run == ()
    assert result.layer == "production"


def test_test_checks_empty_paths():
    result = run_test_checks("/tmp/repo", [])
    assert result.status == "pass"
    assert result.checks_run == ()
    assert result.layer == "test"


def test_run_gates_classifies_paths():
    toml = {"integration": {"run": "echo ok", "cwd": "."}}
    result = run_gates("/tmp/repo", ["tracks/foo.py", "tests/test_foo.py"], "green_gate", toml)
    assert result.layer == "both"


def test_run_gates_production_only():
    toml = {"integration": {"run": "echo ok", "cwd": "."}}
    result = run_gates("/tmp/repo", ["tracks/foo.py"], "green_gate", toml)
    assert result.layer == "production"


def test_run_gates_test_only():
    toml = {"integration": {"run": "echo ok", "cwd": "."}}
    result = run_gates("/tmp/repo", ["tests/test_foo.py"], "green_gate", toml)
    assert result.layer == "test"


def test_run_gates_no_test_command_fails_closed():
    result = run_gates("/tmp/repo", [], "green_gate", {})
    assert result.status == "fail"
    assert any("no test command" in f for f in result.failures)


def test_run_gates_malformed_command_fails_closed():
    toml = {"integration": {"run": "", "cwd": "."}}
    result = run_gates("/tmp/repo", [], "green_gate", toml)
    assert result.status == "fail"


def test_run_gates_no_test_command_fails_closed_refactor():
    result = run_gates("/tmp/repo", [], "refactor_gate", {})
    assert result.status == "fail"
    assert any("no test command" in f for f in result.failures)


def test_run_gates_executes_real_command_through_project_venv(tmp_path):
    """T-03 contract 7: run_gates really executes the configured unit command
    as a subprocess (not 127 infrastructure), using the project `.venv`
    interpreter and shell=False. Each executed command must be exposed as an
    immutable observed execution (argv/cwd/exit_code/classification/output
    hashes) for the Runtime-authoritative gate."""
    repo = Path(tmp_path)
    venv_python = repo / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.symlink_to(sys.executable)
    probe = repo / "probe.py"
    probe.write_text(
        "import json, os, pathlib, sys\n"
        "pathlib.Path('observed.json').write_text(json.dumps(\n"
        "    {'exe': sys.executable, 'argv': sys.argv, 'cwd': os.getcwd(),\n"
        "     'exit': int(sys.argv[1])}))\n"
        "sys.exit(int(sys.argv[1]))\n",
        encoding="utf-8",
    )
    toml = {"integration": {"run": ".venv/bin/python probe.py 2", "cwd": "."}}
    result = run_gates(str(repo), [], "green_gate", toml)

    assert result.status == "fail"
    assert any("unit-tests" in failure for failure in result.failures)
    observed = json.loads((repo / "observed.json").read_text(encoding="utf-8"))
    assert observed["exe"] == os.path.abspath(str(venv_python)), (
        "Python invocation must use the project .venv interpreter"
    )
    assert Path(observed["exe"]).resolve() == Path(sys.executable).resolve()
    assert observed["cwd"] == os.path.realpath(str(repo))
    assert observed["exit"] == 2

    observations = getattr(result, "observations", None)
    assert observations, (
        "gate result must expose observed executions as immutable GateObservation objects"
    )
    obs = observations[0]
    assert not isinstance(obs, dict) and not isinstance(obs, tuple), (
        "observed execution must be a GateObservation object"
    )
    assert isinstance(obs.argv, tuple), "argv must be a tuple"
    assert obs.argv[0] in (
        ".venv/bin/python",
        str(venv_python),
        os.path.abspath(str(venv_python)),
        os.path.realpath(str(venv_python)),
    ), "argv tuple must begin with the project .venv interpreter"
    assert "probe.py" in obs.argv, "argv must preserve the configured command"
    assert obs.cwd == os.path.realpath(str(repo)), (
        "GateObservation cwd must be the Runtime process cwd"
    )
    assert obs.exit_code == 2, "GateObservation exit_code must match the probe"
    for attr in ("classification", "stdout_sha", "stderr_sha"):
        assert isinstance(getattr(obs, attr), str) and getattr(obs, attr), (
            f"GateObservation.{attr} must be a non-empty string"
        )
    with pytest.raises(dataclasses.FrozenInstanceError):
        obs.exit_code = 0
    with pytest.raises(dataclasses.FrozenInstanceError):
        obs.argv = ("mutation",)
    assert obs.exit_code == 2, "mutation must not corrupt the observation"
