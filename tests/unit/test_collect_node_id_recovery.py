"""D4 regression: pytest>=9 quiet collect output must not read as empty.

``pytest --collect-only -q`` on a host whose pytest config already lowers
verbosity (e.g. ``addopts = "-q"``) prints one ``path: count`` line per file
and no ``::`` node ids. The collector must re-invoke the declared command
with the fine-grained collect-verbosity pin and parse the recovered nodes
instead of treating the layer as empty (live defect: collect_defect).
"""

from __future__ import annotations

import subprocess

from tracks.executor.helpers import (
    collect_node_ids_argv,
    collect_node_ids_command,
    is_per_file_collect_summary,
    parse_collected_nodes,
    run_collect_command,
)

_QUIET_LISTING = (
    "tests/unit/test_a.py: 2\n"
    "tests/unit/test_b.py: 1\n"
    "=============================== warnings summary ===============================\n"
    "tests/unit/test_a.py:3: PytestCollectionWarning: demo\n"
    "3390 tests collected in 1.23s\n"
)
_NODE_LISTING = (
    "tests/unit/test_a.py::test_a\n"
    "tests/unit/test_a.py::test_b\n"
    "2 tests collected in 0.01s\n"
)
_EXPECTED_NODES = ["tests/unit/test_a.py::test_a", "tests/unit/test_a.py::test_b"]


def _proc(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(
        args=["pytest"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_is_per_file_collect_summary_identifies_quiet_listing():
    assert is_per_file_collect_summary(_QUIET_LISTING) is True
    assert is_per_file_collect_summary(_NODE_LISTING) is False
    assert is_per_file_collect_summary("") is False
    assert is_per_file_collect_summary("no tests collected in 0.01s") is False


def test_collect_node_ids_helpers_append_the_verbosity_pin():
    assert collect_node_ids_argv(["pytest", "--collect-only", "-q"]) == [
        "pytest",
        "--collect-only",
        "-q",
        "-o",
        "verbosity_test_cases=-1",
    ]
    assert (
        collect_node_ids_command("pytest --collect-only -q")
        == "pytest --collect-only -q -o verbosity_test_cases=-1"
    )


def test_run_collect_command_retries_quiet_listing_with_pin(monkeypatch):
    calls = []

    def fake_run(argv, cwd=None, capture_output=True, text=True):
        calls.append(list(argv))
        if len(calls) == 1:
            return _proc(stdout=_QUIET_LISTING)
        return _proc(stdout=_NODE_LISTING)

    monkeypatch.setattr(subprocess, "run", fake_run)
    proc = run_collect_command(["pytest", "--collect-only", "-q", "tests/unit/"], "/repo")
    assert parse_collected_nodes(proc.stdout) == _EXPECTED_NODES
    assert len(calls) == 2
    assert calls[1] == [
        "pytest",
        "--collect-only",
        "-q",
        "tests/unit/",
        "-o",
        "verbosity_test_cases=-1",
    ]


def test_run_collect_command_keeps_node_listing_without_retry(monkeypatch):
    calls = []

    def fake_run(argv, cwd=None, capture_output=True, text=True):
        calls.append(list(argv))
        return _proc(stdout=_NODE_LISTING)

    monkeypatch.setattr(subprocess, "run", fake_run)
    proc = run_collect_command(["pytest", "--collect-only", "-q", "tests/unit/"], "/repo")
    assert parse_collected_nodes(proc.stdout) == _EXPECTED_NODES
    assert len(calls) == 1


def test_run_collect_command_keeps_original_when_retry_does_not_recover(monkeypatch):
    calls = []

    def fake_run(argv, cwd=None, capture_output=True, text=True):
        calls.append(list(argv))
        if len(calls) == 1:
            return _proc(stdout=_QUIET_LISTING)
        return _proc(returncode=4, stderr="usage error")

    monkeypatch.setattr(subprocess, "run", fake_run)
    proc = run_collect_command(["pytest", "--collect-only", "-q", "tests/unit/"], "/repo")
    assert proc.returncode == 0
    assert parse_collected_nodes(proc.stdout) == []
    assert len(calls) == 2
