"""Hermetic host test environment: operator serve controls never leak in.

The runtime launches host unit/integration/e2e subprocesses from the same
process as the operator's serve shell. Inheriting TRAC_GITHUB_* / GitHub
token vars makes fake sources look like a real CI readback, and
TRAC_AGENT_SESSION_REUSE changes agent-session behavior.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path

from tracks.executor.quality_gate import (
    execute_gate_command,
    host_test_env,
)

_OPERATOR_ENV = {
    "TRAC_GITHUB_REPO": "acme/operator",
    "TRAC_GITHUB_CA_BUNDLE": "/operator/ca.pem",
    "GITHUB_TOKEN": "operator-token",
    "GH_TOKEN": "operator-gh-token",
    "GH_HOST": "github.operator.invalid",
    "TRAC_AGENT_SESSION_REUSE": "0",
}


def test_host_test_env_strips_only_operator_controls_and_preserves_plumbing():
    parent = {
        **_OPERATOR_ENV,
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": "/repo",
        "VIRTUAL_ENV": "/repo/.venv",
        "HOME": "/home/operator",
        "TRAC_INFRA_RETRY_LIMIT": "2",
        "TRAC_GITHUB_API_BASE": "http://127.0.0.1:4567",
    }
    original = dict(parent)

    sanitized = host_test_env(parent)

    assert not set(_OPERATOR_ENV) & set(sanitized)
    assert sanitized["PATH"] == parent["PATH"]
    assert sanitized["PYTHONPATH"] == parent["PYTHONPATH"]
    assert sanitized["VIRTUAL_ENV"] == parent["VIRTUAL_ENV"]
    assert sanitized["HOME"] == parent["HOME"]
    assert sanitized["TRAC_INFRA_RETRY_LIMIT"] == "2"
    assert sanitized["TRAC_GITHUB_API_BASE"] == parent["TRAC_GITHUB_API_BASE"]
    assert sanitized is not parent
    assert parent == original


def test_host_test_env_defaults_to_current_process(monkeypatch):
    for key, value in _OPERATOR_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("TRAC_INFRA_RETRY_LIMIT", "2")

    sanitized = host_test_env()

    assert not set(_OPERATOR_ENV) & set(sanitized)
    assert sanitized["TRAC_INFRA_RETRY_LIMIT"] == "2"
    assert sanitized["PATH"] == os.environ["PATH"]


def test_execute_gate_command_launches_host_test_without_operator_controls(
    tmp_path: Path, monkeypatch
):
    observed = tmp_path / "observed-env.json"
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import json, os, pathlib, sys\n"
        f"pathlib.Path({str(observed)!r}).write_text(json.dumps(dict(os.environ)))\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    for key, value in _OPERATOR_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("TRAC_INFRA_RETRY_LIMIT", "2")

    command = shlex.join([sys.executable, str(probe)])
    obs = execute_gate_command(command, str(tmp_path), "full-unit")

    assert obs.exit_code == 0, obs.stderr
    child_env = json.loads(observed.read_text(encoding="utf-8"))
    assert not set(_OPERATOR_ENV) & set(child_env)
    assert child_env["TRAC_INFRA_RETRY_LIMIT"] == "2"
    assert child_env["PATH"] == os.environ["PATH"]
    for key, value in _OPERATOR_ENV.items():
        assert os.environ[key] == value, "the runtime parent env must be unchanged"
