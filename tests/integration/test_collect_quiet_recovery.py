"""D4 regression: a real double-quiet collect recovers node ids.

The live defect was produced by a host whose runner config already lowers
verbosity (``addopts``) while the contract's collect command passes its own
quiet flag: the effective level drops one step further, the collect prints
only per-file counts, and the runtime parsed zero nodes. This test runs the
real runner in a throwaway host and asserts the verbosity-pinned recovery.
"""

from __future__ import annotations

import sys

import pytest

from tracks.executor.helpers import parse_collected_nodes, run_collect_command

pytestmark = pytest.mark.integration


def test_run_collect_command_recovers_quiet_node_ids(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\naddopts = "-q"\n', encoding="utf-8"
    )
    (tmp_path / "test_probe.py").write_text(
        "def test_a():\n    pass\n\n\ndef test_b():\n    pass\n",
        encoding="utf-8",
    )
    argv = [sys.executable, "-m", "pytest", "--collect-only", "-q", "test_probe.py"]
    proc = run_collect_command(argv, tmp_path)
    assert proc.returncode == 0
    assert parse_collected_nodes(proc.stdout) == [
        "test_probe.py::test_a",
        "test_probe.py::test_b",
    ]
