"""Process-boundary host env: one allowlist, no denylist truth source.

The runtime launches host unit/integration/e2e subprocesses from the same
process as the operator's serve shell. IF-HOSTENV-001 (interfaces §1e)
replaces the v0.12 denylist contract: :func:`host_process_env` is the single
allowlist source and ``host_test_env`` survives only as a compatibility face
over it. ``TRAC_GITHUB_*`` / GitHub token vars and unlisted operator
plumbing never leak into the child; the cross-language toolchain variables
(PATH/PYTHONPATH/VIRTUAL_ENV/HOME) still arrive.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path

from tests.unit.host_env_allowlist_support import (
    assert_env_equals,
    call_host_process_env,
    synthetic_base,
)
from tracks.executor.quality_gate import (
    execute_gate_command,
    host_test_env,
)

_OPERATOR_ENV = {
    "TRAC_GITHUB_REPO": "synthetic::operator",
    "TRAC_GITHUB_CA_BUNDLE": "/synthetic/ca.pem",
    "TRAC_GITHUB_API_BASE": "http://127.0.0.1:4567",
    "GITHUB_TOKEN": "synthetic::operator",
    "GH_TOKEN": "synthetic::operator",
    "GH_HOST": "github.operator.invalid",
    "TRAC_AGENT_SESSION_REUSE": "0",
}


# AC-FR0315-01@v0.9 TRACKS-TRACE IF-HOSTENV-001 compatibility face
def test_host_test_env_is_the_single_allowlist_face(monkeypatch):
    expected = synthetic_base(monkeypatch)
    for key, value in _OPERATOR_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("TRAC_INFRA_RETRY_LIMIT", "2")

    sanitized = host_test_env()

    assert_env_equals(sanitized, call_host_process_env())
    assert_env_equals(sanitized, expected)
    assert not set(_OPERATOR_ENV) & set(sanitized)
    assert "TRAC_INFRA_RETRY_LIMIT" not in set(sanitized)


# AC-NFR0143-02@v0.8 TRACKS-TRACE IF-HOSTENV-001 gate child allowlist
def test_execute_gate_command_child_sees_only_the_allowlist(
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
    expected = synthetic_base(monkeypatch)
    for key, value in _OPERATOR_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("TRAC_INFRA_RETRY_LIMIT", "2")
    monkeypatch.setenv("OPERATOR_ONLY_VAR", "synthetic::operator")

    command = shlex.join([sys.executable, str(probe)])
    obs = execute_gate_command(command, str(tmp_path), "full-unit")

    assert obs.exit_code == 0, obs.stderr
    child_env = json.loads(observed.read_text(encoding="utf-8"))
    compared_child = dict(child_env)
    compared_expected = dict(expected)
    if sys.platform == "darwin":
        # darwin libc injects this variable and rewrites LC_CTYPE: the key
        # set still has to equal the allowlist, so only these two platform
        # artefacts are excluded from the comparison.
        compared_child.pop("__CF_USER_TEXT_ENCODING", None)
        compared_child.pop("LC_CTYPE", None)
        compared_expected.pop("LC_CTYPE", None)
        assert "LC_CTYPE" in child_env
    assert_env_equals(compared_child, compared_expected)
    assert not set(_OPERATOR_ENV) & set(child_env)
    assert "TRAC_INFRA_RETRY_LIMIT" not in set(child_env)
    assert "OPERATOR_ONLY_VAR" not in set(child_env)
    for key, value in _OPERATOR_ENV.items():
        assert os.environ[key] == value, "the runtime parent env must be unchanged"
    assert os.environ["TRAC_INFRA_RETRY_LIMIT"] == "2"
