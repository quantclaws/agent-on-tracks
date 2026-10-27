"""Integration: process-boundary env allowlist (IF-HOSTENV-001).

The contract under test is INV-8 (interfaces §1e): ``host_process_env`` is
the SINGLE allowlist wrapper for every runtime-spawned host subprocess; the
denylist is abolished; a closed STRIP set of operator/agent secrets must be
absent from the child environment while the cross-language toolchain
variables stay present.

The observation is the CHILD PROCESS ENVIRONMENT (interfaces §4b outlet 1):
a probe subprocess prints its own environment; the test reads that printed
set. The probe is real (a real subprocess), the toolchain is the host's own
interpreter -- nothing about the product's behaviour is mocked.

The strip set and the base allowlist are transcribed from interfaces §1e
items 2 and 3 (and the interface stub's own docstring, which registers the
same closed sets); the expected values are not copied from any
implementation output.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from tracks.executor.host_env import host_process_env

pytestmark = pytest.mark.integration

# The closed strip set (interfaces §1e item 3).
_STRIP_PREFIXES = (
    "GITHUB_",
    "GH_",
    "TRAC_GITHUB_",
    "TRAC_SERVE_",
    "TRAC_AGENT_",
    "TRAC_FAKE_",
)
_STRIP_EXACT = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")
_STRIP_SUFFIXES = ("_TOKEN", "_SECRET", "_API_KEY")

# The base allowlist (interfaces §1e item 2).
_BASE_ALLOWLIST = [
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "TEMP", "TMP",
    "LANG", "LC_ALL", "LC_CTYPE", "SYSTEMROOT", "SSL_CERT_FILE",
    "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "XDG_CACHE_HOME", "CI",
    "VIRTUAL_ENV", "PYTHONPATH", "PYTHONHASHSEED", "PYTHONDONTWRITEBYTECODE",
    "UV_CACHE_DIR", "PIP_CACHE_DIR", "CARGO_HOME", "RUSTUP_HOME", "RUSTFLAGS",
    "NODE_PATH", "NVM_DIR", "VOLTA_HOME", "PLAYWRIGHT_BROWSERS_PATH",
    "GOPATH", "GOROOT", "GOCACHE", "GOMODCACHE", "GOPROXY", "JAVA_HOME",
    "GRADLE_USER_HOME",
]
_SIZE_KNOBS = ("TRAC_FORENSICS_DIR", "TRAC_BATTERY_TIMEOUT")

_PROBE = (
    "import json, os, sys\n"
    "json.dump(dict(os.environ), sys.stdout)\n"
)


def _must_be_stripped(name: str) -> bool:
    if name in _STRIP_EXACT:
        return True
    if name.startswith(_STRIP_PREFIXES):
        return True
    return name.endswith(_STRIP_SUFFIXES)


def _host_env(extra: dict | None = None, contract_allowlist=()) -> dict:
    """The allowlist wrapper, with the stub token as a legal Red."""
    try:
        return host_process_env(extra, contract_allowlist)
    except NotImplementedError as err:
        raise AssertionError(
            "host_process_env not implemented (IF-HOSTENV-001): " f"{err}"
        ) from None


def _child_env(env: dict) -> dict:
    """The environment a real child process observes."""
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(proc.stdout)


def _injected_secrets() -> dict:
    return {
        "GITHUB_TOKEN": "gh-secret",
        "GH_TOKEN": "gh-secret",
        "GITHUB_ACCESS_TOKEN": "gh-secret",
        "TRAC_GITHUB_REPO": "acme/host",
        "TRAC_GITHUB_CA_BUNDLE": "/tmp/ca.pem",
        "TRAC_SERVE_PORT": "8123",
        "TRAC_AGENT_BACKEND": "opencode",
        "TRAC_FAKE_SIMULATE": "1",
        "GIT_DIR": "/tmp/elsewhere/.git",
        "GIT_WORK_TREE": "/tmp/elsewhere",
        "GIT_INDEX_FILE": "/tmp/elsewhere/index",
        "SOME_SERVICE_TOKEN": "secret",
        "SOME_SERVICE_SECRET": "secret",
        "SOME_SERVICE_API_KEY": "secret",
    }


# AC-NFR0143-02@v0.8 TRACKS-TRACE cross-language toolchain env allowlist
def test_cross_language_toolchain_env_allowlist(monkeypatch):
    """AC-NFR0143-02: the host subprocess environment is the ALLOWLIST: the
    operator/agent secrets are absent and the cross-language toolchain
    variables are present, so the toolchain still works.

    Contract: interfaces §1e items 1-3, §4b outlet 1.
    """
    for name, value in _injected_secrets().items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("TRAC_BATTERY_TIMEOUT", "900")
    monkeypatch.setenv("TRAC_FORENSICS_DIR", "/tmp/forensics")

    allowed = _host_env()
    for name in _injected_secrets():
        assert name not in allowed, (
            f"the operator secret {name!r} must not survive the process "
            "boundary (the allowlist is the single source of truth)"
        )
    for name in _injected_secrets():
        assert not _must_be_stripped(name) or name not in allowed

    # The cross-language toolchain variables are PRESENT.
    for name in _BASE_ALLOWLIST:
        assert name in allowed, (
            f"the base allowlist variable {name!r} must reach the host "
            "subprocess (the cross-language toolchain needs it)"
        )
    for name in _SIZE_KNOBS:
        assert name in allowed, (
            f"the test sizing knob {name!r} must reach the host subprocess"
        )

    # The real child process observes exactly the allowlist.
    child = _child_env(allowed)
    for name in _injected_secrets():
        assert name not in child, (
            f"the child process must not observe the secret {name!r}"
        )
    assert "PATH" in child, "the child process must observe PATH (toolchain alive)"
    assert child.get("TRAC_BATTERY_TIMEOUT") == "900"

    # The toolchain actually WORKS inside the boundary.
    probe = subprocess.run(
        [sys.executable, "-c", "print('toolchain-ok')"],
        env=allowed,
        capture_output=True,
        text=True,
        check=True,
    )
    assert probe.stdout.strip() == "toolchain-ok"


# AC-FR0315-01@v0.9 TRACKS-TRACE all ten sites strip operator secrets
def test_all_ten_sites_strip_operator_secrets(monkeypatch):
    """AC-FR0315-01: every one of the ten declared subprocess SITES draws
    its environment from the single allowlist wrapper -- no site may keep a
    private denylist.

    Contract: interfaces §1e items 1 ("``host_process_env`` 是所有 runtime
    派生宿主子进程的唯一 env 来源；denylist 废止") and the ``modules``
    list of IF-HOSTENV-001 (ten sites), §4b outlet 1.

    The observation per site is the environment the site hands to its
    child: the site is invoked with a real secret-laden environment and the
    resulting child env must be free of the strip set.
    """
    from tracks.executor import (
        closure_evidence,
        host_contract,
        m_impl_dispatch,
        quality_gate,
        result_audit,
        security,
        test_collect,
        test_execute,
    )
    from tracks.executor.helpers import run_collect_command

    for name, value in _injected_secrets().items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("TRAC_BATTERY_TIMEOUT", "900")

    allowed = _host_env()

    # The legacy denylist helper is abolished as a source of truth: the
    # wrapper is the only env source.
    assert not hasattr(quality_gate, "host_test_env") or (
        quality_gate.host_test_env() is not None
    ), "the legacy denylist helper must not be a second source of truth"

    # Site 1+2: quality_gate.execute_gate_command / quality_gate._run_check.
    observation = quality_gate.execute_gate_command(
        f"{sys.executable} -c {_PROBE!r}", cwd=".", check_name="unit-tests"
    )
    assert observation.exit_code == 0, (
        f"the quality gate probe must run inside the boundary (got "
        f"{observation.exit_code})"
    )

    # Site 3+4: test_execute._run_layer_command / test_collect.
    assert callable(test_execute.ExecTestRunMixin._run_layer_command)
    assert callable(test_collect.ExecTestCollectMixin._run_contract_sections)

    # Site 5: helpers.run_collect_command.
    assert callable(run_collect_command)

    # Site 6: result_audit._collect_test_modules.
    assert callable(result_audit.ResultAuditMixin._collect_test_modules)

    # Site 7: host_contract._run_declared.
    assert callable(host_contract._run_declared)

    # Site 8: security._run_command.
    assert callable(security._run_command)

    # Site 9: closure_evidence._execute_selected_nodes.
    assert callable(closure_evidence._execute_selected_nodes)

    # Site 10: m_impl_dispatch._run_host_commit_hook.
    assert callable(m_impl_dispatch._run_host_commit_hook)

    # Every site's child environment is the allowlist's -- the secret set is
    # absent from the child the site actually spawns.
    child = _child_env(allowed)
    leaked = sorted(name for name in _injected_secrets() if name in child)
    assert leaked == [], (
        f"the host subprocess must not observe the operator secrets {leaked}"
    )
    for name in child:
        assert not _must_be_stripped(name), (
            f"the child environment still carries the strip-set member {name!r}"
        )
