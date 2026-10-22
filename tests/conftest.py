"""Merge subprocess coverage shards into the outer session data file."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SUBPROCESS_SHARD_GLOB = ".coverage.subprocess.*"


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    if getattr(session.config, "workerinput", None) is not None:
        return
    plugin = session.config.pluginmanager.getplugin("_cov")
    if plugin is None or getattr(plugin, "cov_controller", None) is None:
        return
    if not any(_REPO_ROOT.glob(_SUBPROCESS_SHARD_GLOB)):
        return
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "coverage",
            "combine",
            "--append",
            f"--data-file={_REPO_ROOT / '.coverage'}",
            f"--rcfile={_REPO_ROOT / 'pyproject.toml'}",
        ],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        reporter = session.config.pluginmanager.getplugin("terminalreporter")
        if reporter is not None:
            reporter.write_line(
                "coverage subprocess shard merge failed "
                f"({result.returncode}): {result.stderr.strip()}"
            )
