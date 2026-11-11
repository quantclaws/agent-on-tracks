"""Shared constants and guards for the IF-HOSTENV-001 RED unit tests.

Expected values come from interfaces.md §1e (base allowlist, strip set and
the ``[host-contract].env_allowlist`` escape hatch), never from the
implementation under test. ``call_host_process_env`` converts the shipped
interface stub's ``NotImplementedError`` into a real ``AssertionError`` so
the RED gate observes ``assertion_failure`` instead of a stub token or an
assembly error.

Assertions are key-set based and every injected value is a synthetic
sentinel: failure output never renders a real operator environment value.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path

from tracks.executor.host_contract import load_host_contract
from tracks.executor.host_env import host_process_env

# interfaces §1e item 2: base allowlist + test-sizing knobs, verbatim.
BASE_ALLOWLIST = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "TMPDIR",
    "TEMP",
    "TMP",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "SYSTEMROOT",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "XDG_CACHE_HOME",
    "CI",
    "VIRTUAL_ENV",
    "PYTHONPATH",
    "PYTHONHASHSEED",
    "PYTHONDONTWRITEBYTECODE",
    "UV_CACHE_DIR",
    "PIP_CACHE_DIR",
    "CARGO_HOME",
    "RUSTUP_HOME",
    "RUSTFLAGS",
    "NODE_PATH",
    "NVM_DIR",
    "VOLTA_HOME",
    "PLAYWRIGHT_BROWSERS_PATH",
    "GOPATH",
    "GOROOT",
    "GOCACHE",
    "GOMODCACHE",
    "GOPROXY",
    "JAVA_HOME",
    "GRADLE_USER_HOME",
    "TRAC_FORENSICS_DIR",
    "TRAC_BATTERY_TIMEOUT",
)

# interfaces §1e item 3: one representative per strip rule.
STRIP_SAMPLES = (
    "GITHUB_TOKEN",
    "GITHUB_ACCESS_TOKEN",
    "GH_TOKEN",
    "GH_HOST",
    "TRAC_GITHUB_REPO",
    "TRAC_GITHUB_API_BASE",
    "TRAC_SERVE_TOKEN",
    "TRAC_AGENT_SESSION_REUSE",
    "TRAC_FAKE_GITHUB",
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "DEPLOY_TOKEN",
    "CUSTOM_SECRET",
    "VENDOR_API_KEY",
)

_CONTRACT_HEADER = """\
[host-contract]
version = 1
language = "python"
toolchain = "cpython>=3.10"
install = "pip install -e ."
"""

MINIMAL_CONTRACT = _CONTRACT_HEADER

ESCAPE_HATCH_CONTRACT = (
    _CONTRACT_HEADER + 'env_allowlist = ["HOST_TOOLCHAIN_BIN", "BOARD_HOME"]\n'
)


def call_host_process_env(
    extra: Mapping[str, str] | None = None,
    contract_allowlist: Iterable[str] = (),
) -> dict[str, str]:
    """Call the wrapper, turning the interface stub into a real assertion."""
    try:
        return host_process_env(extra=extra, contract_allowlist=contract_allowlist)
    except NotImplementedError:
        raise AssertionError(
            "IF-HOSTENV-001 仍是桩：host_process_env 必须返回单一 allowlist 子进程环境"
        ) from None


def load_contract_allowlist(path: Path) -> tuple[str, ...]:
    """Read the escape hatch, turning a missing schema field into an assertion."""
    try:
        contract = load_host_contract(path)
    except ValueError as exc:
        raise AssertionError(
            f"IF-HOSTENV-001 [host-contract].env_allowlist 逃逸阀未实现: {exc}"
        ) from None
    if not hasattr(contract, "env_allowlist"):
        raise AssertionError(
            "IF-HOSTENV-001 HostContract 缺少 env_allowlist：project.toml 逃逸阀未接线"
        )
    return tuple(contract.env_allowlist)


def synthetic_base(monkeypatch) -> dict[str, str]:
    """Pin every base-allowlist variable to a synthetic, non-secret value."""
    values = {name: f"synthetic::{name}" for name in BASE_ALLOWLIST}
    # Child interpreters validate this one, so it cannot carry the sentinel text.
    values["PYTHONHASHSEED"] = "0"
    temp_dir = tempfile.gettempdir()
    values.update({"TMPDIR": temp_dir, "TEMP": temp_dir, "TMP": temp_dir})
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return values


def environment_keys(path: Path) -> set[str]:
    """Return only the variable names captured by a real child-process probe."""
    return {
        line.partition("=")[0]
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    }


def assert_env_equals(actual: Mapping[str, str], expected: Mapping[str, str]) -> None:
    """Compare environments with key-name-only failure output.

    A raw ``assert actual == expected`` renders the full mapping, which can
    echo an unexpected operator credential into pytest output. This helper
    reports only the divergent key names.
    """
    actual_keys = set(actual)
    expected_keys = set(expected)
    assert actual_keys == expected_keys, {
        "missing": sorted(expected_keys - actual_keys),
        "unexpected": sorted(actual_keys - expected_keys),
    }
    mismatched = sorted(
        key for key in expected_keys if actual.get(key) != expected[key]
    )
    assert not mismatched, f"allowlist values changed for: {mismatched}"
